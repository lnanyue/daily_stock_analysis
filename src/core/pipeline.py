# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 核心分析流水线
===================================
"""

import asyncio
import inspect
import logging
import random
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from src.config import Config, get_config
from src.storage import get_db
from data_provider import DataFetcherManager
from data_provider.base import normalize_stock_code
from src.analyzer import AnalysisResult, GeminiAnalyzer
from src.notification import NotificationService
from src.search_service import SearchService
from src.services.social_sentiment_service import SocialSentimentService
from src.enums import ReportType
from src.stock_analyzer import StockTrendAnalyzer
from src.core.pipeline_context import enhance_analysis_context
from src.core.pipeline_helpers import compute_ma_status, extract_risk_keywords, estimate_intel_bullet_count, resolve_resume_target_date
from src.core.pipeline_notifications import (
    send_single_stock_notification,
    send_single_stock_notification_async_wrapper,
    sync_maybe_await,
)
from src.core.trading_calendar import (
    advance_trading_days,
    get_effective_trading_date,
    get_market_for_stock,
    get_market_now,
    is_market_open,
)
from src.core.pipeline_data_collector import StockDataCollector, StockDataCollectionResult
from src.core.pipeline_executor import AnalysisExecutor
from src.core.stock_cache import StockCache
from bot.models import BotMessage


logger = logging.getLogger(__name__)

class StockAnalysisPipeline:
    """
    股票分析主流程调度器
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None,
        source_message: Optional[BotMessage] = None,
        query_id: Optional[str] = None,
        query_source: Optional[str] = None,
        save_context_snapshot: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        analyzer_factory: Optional[Any] = None,
        notifier_factory: Optional[Any] = None,
    ):
        self.config = config or get_config()
        self.max_workers = max_workers or self.config.max_workers
        self.source_message = source_message
        self.query_id = query_id
        self.query_source = self._resolve_query_source(query_source)
        self.save_context_snapshot = (
            self.config.save_context_snapshot if save_context_snapshot is None else save_context_snapshot
        )
        self.progress_callback = progress_callback
        
        self.db = get_db()
        self.cache = StockCache()
        self.search_service = SearchService(
            tavily_keys=self.config.tavily_api_keys,
            finnhub_api_key=getattr(self.config, "finnhub_api_key", None),
            news_max_age_days=self.config.news_max_age_days,
            news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
        )

        from src.plugins import PluginRegistry, PluginContext
        self.plugins = PluginRegistry()
        plugin_ctx = PluginContext(
            config=self.config, db=self.db, search_service=self.search_service, fetcher_manager=None,
        )
        self.plugins.load(plugin_ctx)

        plugin_fetchers = self.plugins.get_enabled_fetchers()
        self.fetcher_manager = DataFetcherManager(
            fetchers=plugin_fetchers,
            config=self.config,
            include_default_fetchers=True,
        )
        plugin_ctx.fetcher_manager = self.fetcher_manager
        
        self.trend_analyzer = StockTrendAnalyzer()
        self.analyzer = analyzer_factory(self.config) if analyzer_factory else GeminiAnalyzer(config=self.config)
        self.notifier = notifier_factory(source_message=source_message) if notifier_factory else NotificationService(source_message=source_message)

        self.data_collector = StockDataCollector(
            config=self.config,
            fetcher_manager=self.fetcher_manager,
            search_service=self.search_service,
            analyzer=self.analyzer,
            trend_analyzer=self.trend_analyzer,
            augment_historical_with_realtime=self._augment_historical_with_realtime,
            progress_callback=self.progress_callback,
        )
        self.executor = AnalysisExecutor(
            config=self.config,
            db=self.db,
            analyzer=self.analyzer,
            search_service=self.search_service,
            fetcher_manager=self.fetcher_manager,
            progress_callback=self.progress_callback,
        )

        self._cached_market_overview: Optional[Dict[str, Any]] = None
        self.social_sentiment_service = SocialSentimentService(
            api_key=self.config.social_sentiment_api_key,
            api_url=self.config.social_sentiment_api_url,
        )

    async def _maybe_await(self, value):
        from data_provider.utils import maybe_await
        return await maybe_await(value)

    @staticmethod
    def _coerce_bool_setting(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
        return default

    def _emit_progress(self, progress: int, message: str) -> None:
        """Best-effort bridge from pipeline stages to task SSE progress."""
        callback = getattr(self, "progress_callback", None)
        if callback is None:
            return
        try:
            callback(progress, message)
        except Exception as exc:
            query_id = getattr(self, "query_id", None)
            logger.warning(
                "[pipeline] progress callback failed: %s (progress=%s, message=%r, query_id=%s)",
                exc,
                progress,
                message,
                query_id,
                extra={
                    "progress": progress,
                    "progress_message": message,
                    "query_id": query_id,
                },
            )

    def _is_agent_runtime_available(self) -> bool:
        checker = getattr(self.config, "is_agent_available", None)
        if callable(checker):
            try:
                available = checker()
            except Exception:
                available = None
            if isinstance(available, bool):
                return available

        for field_name in ("agent_litellm_model", "litellm_model"):
            value = getattr(self.config, field_name, None)
            if isinstance(value, str) and value.strip():
                return True

        return self._coerce_bool_setting(getattr(self.config, "agent_mode", False), default=False)

    async def prefetch_stock_data(
        self,
        code: str,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        预取并缓存单只股票的日线数据。

        1. 检查 parquet 缓存今天是否已 fetch → 是则跳过网络
        2. 网络拉取 45 天数据
        3. 成功 → 写入 parquet 缓存
        4. 失败 → fallback 读缓存（不限新旧）
        """
        stock_name = code
        try:
            stock_name = await self._maybe_await(self.fetcher_manager.get_stock_name(code))
        except Exception as exc:
            return False, str(exc)

        # 1. Check cache freshness
        if self.cache.is_fresh(code):
            logger.info("[%s] 缓存有效，跳过网络请求", code)
            return True, None

        # 2. Network fetch
        try:
            res = await self.fetcher_manager.get_daily_data(code, days=45)
            df, source_name = res
            if df is None or df.empty:
                # 3a. Network returned empty — fallback to cache
                cached, _ = self.cache.read(code)
                if cached is not None and not cached.empty:
                    logger.warning("[%s] 网络获取为空，使用缓存数据", code)
                    return True, None
                return False, "获取数据为空"
            # 3b. Write to cache
            self.cache.write(code, df)
            return True, source_name
        except Exception as e:
            logger.error("[%s] 数据抓取失败: %s", code, e)
            # 4. Fallback to cache
            cached, _ = self.cache.read(code)
            if cached is not None and not cached.empty:
                logger.warning("[%s] 网络异常，使用缓存数据: %s", code, e)
                return True, None
            return False, str(e)

    async def analyze_stock(self, code: str, report_type: ReportType, query_id: str) -> Optional[AnalysisResult]:
        """分析单只股票 — 委托 StockDataCollector + AnalysisExecutor 执行。"""
        try:
            collected = await self.data_collector.collect(code)
            return await self.executor.analyze(
                code, report_type, query_id, collected,
                analysis_mode=collected.analysis_mode,
            )
        except Exception as e:
            logger.error(f"[{code}] AI 分析失败: {e}", exc_info=True)
            return None

    async def run(self, stock_codes=None, dry_run=False, send_notification=True, merge_notification=False) -> List[AnalysisResult]:
        if stock_codes is None: stock_codes = self.config.stock_list
        if not stock_codes: return []

        concurrency_limit = max(1, min(self.max_workers, 2))
        semaphore = asyncio.Semaphore(concurrency_limit)
        
        async def _bounded_process(code, index):
            async with semaphore:
                if index > 0:
                    await asyncio.sleep(random.uniform(1.0, 3.0))
                return await self.process_single_stock(
                    code, dry_run, 
                    getattr(self.config, 'single_stock_notify', False) and send_notification
                )
        
        logger.info(f"开始批量分析，任务总数: {len(stock_codes)}")
        tasks = [_bounded_process(c, i) for i, c in enumerate(stock_codes)]
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)
        results = [r for r in results_raw if isinstance(r, AnalysisResult)]
        
        if results and send_notification and not dry_run and not getattr(self.config, 'single_stock_notify', False) and not merge_notification:
            report_text = self.notifier.generate_dashboard_report(results)
            await self.notifier.send(report_text, email_stock_codes=stock_codes)
        return results

    def _enhance_context(self, context, realtime_quote, chip_data, trend_result, stock_name, fundamental_context=None, market_overview=None, peer_comparison=None) -> Dict[str, Any]:
        return enhance_analysis_context(
            context=context,
            realtime_quote=realtime_quote,
            chip_data=chip_data,
            trend_result=trend_result,
            stock_name=stock_name,
            search_service=self.search_service,
            fetcher_manager=self.fetcher_manager,
            db=self.db,
            compute_ma_status=compute_ma_status,
            fundamental_context=fundamental_context,
            market_overview=market_overview,
            peer_comparison=peer_comparison,
        )

    def _should_auto_route_to_agent(
        self,
        *,
        code: str,
        report_type: ReportType,
        enhanced_context: Dict[str, Any],
        final_news: str,
        fundamental_context: Optional[Dict[str, Any]],
        trend_result: Any,
        a_stock_intelligence: str,
        money_flow_intelligence: str,
        guru_insight: str,
    ) -> Tuple[bool, List[str]]:
        if not self._coerce_bool_setting(
            getattr(self.config, "agent_auto_route_analysis", False),
            default=False,
        ):
            return False, []
        logger.warning(
            "[%s] agent_auto_route_analysis 已弃用，配置不生效，自动分流路径未连接",
            code,
        )

        if not self._is_agent_runtime_available():
            logger.info("[%s] 自动 Agent 分流已启用，但当前 Agent 运行时不可用，继续使用经典分析链路", code)
            return False, []

        major_reasons: List[str] = []
        minor_reasons: List[str] = []

        today = dict(enhanced_context.get("today") or {})
        if trend_result is None or not today or today.get("close") in (None, "", 0):
            major_reasons.append("core_data_gap")

        coverage = (fundamental_context or {}).get("coverage") or {}
        failing_blocks = sorted(
            key
            for key, status in coverage.items()
            if str(status).strip().lower() in {"failed", "partial"}
        )
        if failing_blocks:
            minor_reasons.append(f"fundamental_coverage:{','.join(failing_blocks[:2])}")

        bullet_count = estimate_intel_bullet_count(final_news)
        if bullet_count >= 6 or len(final_news or "") >= 1600:
            major_reasons.append(f"dense_news_flow:{bullet_count}")

        risk_hits = extract_risk_keywords(final_news)
        if risk_hits:
            major_reasons.append(f"risk_sensitive_intel:{','.join(risk_hits[:2])}")

        a_share_layers = sum(
            1 for section in (a_stock_intelligence, money_flow_intelligence, guru_insight)
            if isinstance(section, str) and section.strip()
        )
        if a_share_layers >= 2:
            minor_reasons.append("multi_layer_a_share_intel")

        report_type_value = getattr(report_type, "value", str(report_type))
        if report_type_value != "simple" and (major_reasons or minor_reasons):
            minor_reasons.append(f"report_type:{report_type_value}")

        reasons = major_reasons + minor_reasons
        should_route = bool(major_reasons) or len(minor_reasons) >= 2
        return should_route, reasons


    def _augment_historical_with_realtime(
        self, df: pd.DataFrame, realtime_quote: Any, code: str
    ) -> pd.DataFrame:
        """
        Augment historical OHLCV with today's realtime quote for intraday MA calculation.
        Issue #234: Use realtime price instead of yesterday's close for technical indicators.
        """
        if df is None or df.empty or 'close' not in df.columns:
            return df
        if realtime_quote is None:
            return df
        price = getattr(realtime_quote, 'price', None)
        if price is None or not (isinstance(price, (int, float)) and price > 0):
            return df

        # Optional: skip augmentation on non-trading days (fail-open)
        enable_realtime_tech = getattr(
            self.config, 'enable_realtime_technical_indicators', True
        )
        if not enable_realtime_tech:
            return df
        market = get_market_for_stock(code)
        market_today = get_market_now(market).date()
        if market and not is_market_open(market, market_today):
            return df

        last_val = df['date'].max()
        last_date = (
            last_val.date() if hasattr(last_val, 'date') else
            (last_val if isinstance(last_val, date) else pd.Timestamp(last_val).date())
        )
        yesterday_close = float(df.iloc[-1]['close']) if len(df) > 0 else price
        open_p = getattr(realtime_quote, 'open_price', None) or getattr(
            realtime_quote, 'pre_close', None
        ) or yesterday_close
        high_p = getattr(realtime_quote, 'high', None) or price
        low_p = getattr(realtime_quote, 'low', None) or price
        vol = getattr(realtime_quote, 'volume', None) or 0
        amt = getattr(realtime_quote, 'amount', None)
        pct = getattr(realtime_quote, 'change_pct', None)

        if last_date >= market_today:
            # Update last row with realtime close (copy to avoid mutating caller's df)
            df = df.copy()
            idx = df.index[-1]
            df.loc[idx, 'close'] = price
            if open_p is not None:
                df.loc[idx, 'open'] = open_p
            if high_p is not None:
                df.loc[idx, 'high'] = high_p
            if low_p is not None:
                df.loc[idx, 'low'] = low_p
            if vol:
                df.loc[idx, 'volume'] = vol
            if amt is not None:
                df.loc[idx, 'amount'] = amt
            if pct is not None:
                df.loc[idx, 'pct_chg'] = pct
        else:
            # Append virtual today row
            new_row = {
                'code': code,
                'date': market_today,
                'open': open_p,
                'high': high_p,
                'low': low_p,
                'close': price,
                'volume': vol,
                'amount': amt if amt is not None else 0,
                'pct_chg': pct if pct is not None else 0,
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        return df

    def _resolve_query_source(self, query_source: Optional[str]) -> str:
        return query_source or ("bot" if self.source_message else "system")

    def _call_fetcher_manager_sync(self, sync_name: str, legacy_name: str, *args, **kwargs):
        manager = getattr(self, "fetcher_manager", None)
        if manager is None:
            return None

        method = None
        if hasattr(type(manager), sync_name) or sync_name in getattr(manager, "__dict__", {}):
            method = getattr(manager, sync_name, None)
        if callable(method):
            return method(*args, **kwargs)

        legacy_method = getattr(manager, legacy_name, None)
        if not callable(legacy_method):
            return None

        result = legacy_method(*args, **kwargs)
        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result

    def _attach_belong_boards_to_fundamental_context(
        self,
        stock_code: str,
        fundamental_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        context = dict(fundamental_context or {})

        existing_boards = context.get("belong_boards")
        if isinstance(existing_boards, list):
            context["belong_boards"] = [
                dict(item) if isinstance(item, dict) else item
                for item in existing_boards
            ]
            return context

        market = context.get("market")
        if not market:
            normalized_code = normalize_stock_code(stock_code)
            if normalized_code.isdigit() and len(normalized_code) == 6:
                market = "cn"
            else:
                market = get_market_for_stock(stock_code)
        board_block = context.get("boards") or {}
        coverage = context.get("coverage") or {}
        board_status = str(
            board_block.get("status") or coverage.get("boards") or ""
        ).strip().lower()

        if market != "cn" or board_status == "not_supported":
            context["belong_boards"] = []
            return context

        boards = self._call_fetcher_manager_sync(
            "get_belong_boards_sync",
            "get_belong_boards",
            stock_code,
        )
        if isinstance(boards, list):
            context["belong_boards"] = [
                dict(item) if isinstance(item, dict) else item
                for item in boards
            ]
        else:
            context["belong_boards"] = []
        return context
    
    async def process_single_stock(
        self,
        code: str,
        skip_analysis: bool = False,
        single_stock_notify: bool = False,
        report_type: ReportType = ReportType.SIMPLE,
        analysis_query_id: Optional[str] = None,
        current_time: Optional[datetime] = None,
    ) -> Optional[AnalysisResult]:
        """
        处理单只股票的完整流程

        包括：
        1. 获取并缓存数据
        2. AI 分析
        3. 单股推送（可选，#55）

        此方法会被线程池调用，需要处理好异常

        Args:
            analysis_query_id: 查询链路关联 id
            code: 股票代码
            skip_analysis: 是否跳过 AI 分析
            single_stock_notify: 是否启用单股推送模式（每分析完一只立即推送）
            report_type: 报告类型枚举（从配置读取，Issue #119）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断

        Returns:
            AnalysisResult 或 None
        """
        logger.info(f"========== 开始处理 {code} ==========")

        from src.services.history_loader import set_frozen_target_date, reset_frozen_target_date
        frozen_td = resolve_resume_target_date(code, current_time=current_time)
        token = set_frozen_target_date(frozen_td)
        try:
            self._emit_progress(12, f"{code}：正在准备分析任务")
            # Step 1: 获取并缓存数据
            success, error = await self.prefetch_stock_data(
                code, current_time=current_time
            )

            if not success:
                logger.warning(f"[{code}] 数据获取失败: {error}")
                # 即使获取失败，也尝试用已有数据分析
            else:
                self._emit_progress(16, f"{code}：行情数据准备完成")

            # dry-run: 数据已拉，跳过 AI 分析
            if skip_analysis:
                logger.info("[%s] dry-run 模式：数据已缓存，跳过 AI 分析", code)
                return None

            # Step 2: AI 分析
            effective_query_id = analysis_query_id or self.query_id or uuid.uuid4().hex
            result = await self.analyze_stock(code, report_type, query_id=effective_query_id)
            
            if result and result.success:
                logger.info(
                    f"[{code}] 分析完成: {result.operation_advice}, "
                    f"评分 {result.sentiment_score}"
                )
                
                # 单股推送模式（#55）：每分析完一只股票立即推送
                if single_stock_notify:
                    await send_single_stock_notification_async_wrapper(
                        self.notifier,
                        result,
                        report_type=report_type,
                        fallback_code=code,
                    )
            elif result:
                logger.warning(
                    f"[{code}] 分析未成功: {result.error_message or '未知错误'}"
                )
            
            return result
            
        except Exception as e:
            # 捕获所有异常，确保单股失败不影响整体
            logger.exception(f"[{code}] 处理过程发生未知异常: {e}")
            return None
        finally:
            reset_frozen_target_date(token)



