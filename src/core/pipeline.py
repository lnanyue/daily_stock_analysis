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
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple, Callable

import pandas as pd

from src.config import get_config, Config
from src.storage import get_db
from data_provider import DataFetcherManager
from data_provider.base import normalize_stock_code
from data_provider.realtime_types import ChipDistribution
from src.analyzer import (
    GeminiAnalyzer,
    AnalysisResult,
    fill_chip_structure_if_needed,
    fill_price_position_if_needed,
)
from src.data.stock_mapping import STOCK_NAME_MAP
from src.notification import NotificationService, NotificationChannel
from src.report_language import (
    get_unknown_text,
    localize_confidence_level,
)
from src.search_service import SearchService
from src.services.social_sentiment_service import SocialSentimentService
from src.enums import ReportType
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from src.core.pipeline_agent import run_agent_analysis
from src.core.pipeline_context import enhance_analysis_context
from src.core.pipeline_notifications import (
    send_single_stock_notification,
    sync_maybe_await,
)
from src.core.trading_calendar import (
    get_effective_trading_date,
    get_market_for_stock,
    get_market_now,
    is_market_open,
)
from data_provider.us_index_mapping import is_us_stock_code
from bot.models import BotMessage


logger = logging.getLogger(__name__)

# 防御性 guard：当实例绕过 __init__（如测试中 __new__）构造时，
# double-check 初始化 _single_stock_notify_lock 仍然线程安全。
_SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD = threading.Lock()


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
        self.search_service = SearchService(
            tavily_keys=self.config.tavily_api_keys,
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

    @staticmethod
    def _estimate_intel_bullet_count(text: str) -> int:
        return len(re.findall(r"(?m)^\s*-\s+", text or ""))

    @staticmethod
    def _extract_risk_keywords(text: str) -> List[str]:
        patterns = [
            ("减持", r"减持"),
            ("处罚", r"处罚|罚款|罚单"),
            ("调查", r"调查|立案"),
            ("预亏", r"预亏|亏损|下修"),
            ("解禁", r"解禁"),
            ("诉讼", r"诉讼"),
            ("违规", r"违规"),
            ("流出", r"净流出|持续流出"),
            ("风险", r"风险提示|重大风险"),
        ]
        hits: List[str] = []
        haystack = text or ""
        for label, pattern in patterns:
            if re.search(pattern, haystack, flags=re.IGNORECASE) and label not in hits:
                hits.append(label)
        return hits

    async def fetch_and_save_stock_data(
        self,
        code: str,
        force_refresh: bool = False,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        获取并保存单只股票数据
        """
        stock_name = code
        try:
            stock_name = await self._maybe_await(self.fetcher_manager.get_stock_name(code))
        except Exception as exc:
            return False, str(exc)

        target_date = self._resolve_resume_target_date(code, current_time=current_time)

        try:
            # 断点续传检查
            if not force_refresh and self.db.has_today_data(code, target_date):
                logger.info(f"{stock_name}({code}) {target_date} 数据已存在，跳过获取（断点续传）")
                return True, None
            
            res = await self.fetcher_manager.get_daily_data(code, days=45)
            df, source_name = res
                
            if df is None or df.empty: 
                return False, "获取数据为空"
                
            await self.db.save_daily_data_async(df, code, source_name)
            return True, None
        except Exception as e:
            logger.error(f"[{code}] 数据抓取失败: {e}")
            return False, str(e)

    async def analyze_stock(self, code: str, report_type: ReportType, query_id: str) -> Optional[AnalysisResult]:
        """
        分析单只股票（增强版：含量比、换手率、筹码分析、多维度情报）

        流程：
        1. 获取实时行情（量比、换手率）- 通过 DataFetcherManager 自动故障切换
        2. 获取筹码分布 - 通过 DataFetcherManager 带熔断保护
        3. 进行趋势分析（基于交易理念）
        4. 多维度情报搜索（最新消息+风险排查+业绩预期）
        5. 从数据库获取分析上下文
        6. 调用 AI 进行综合分析

        Args:
            query_id: 查询链路关联 id
            code: 股票代码
            report_type: 报告类型

        Returns:
            AnalysisResult 或 None（如果分析失败）
        """
        stock_name = code
        try:
            self._emit_progress(18, f"{code}：正在获取行情与筹码数据")
            # 获取股票名称（先走轻量名称路径，后续若 realtime_quote 有 name 再覆盖）
            stock_name = await self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            # Step 1: 获取实时行情（量比、换手率等）- 使用统一入口，自动故障切换
            realtime_quote = None
            try:
                if self.config.enable_realtime_quote:
                    realtime_quote = await self.fetcher_manager.get_realtime_quote(code, log_final_failure=False)
                    if realtime_quote:
                        # 使用实时行情返回的真实股票名称
                        if realtime_quote.name:
                            stock_name = realtime_quote.name
                        # 兼容不同数据源的字段（有些数据源可能没有 volume_ratio）
                        volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
                        turnover_rate = getattr(realtime_quote, 'turnover_rate', None)
                        logger.info(f"{stock_name}({code}) 实时行情: 价格={realtime_quote.price}, "
                                  f"量比={volume_ratio}, 换手率={turnover_rate}% "
                                  f"(来源: {realtime_quote.source.value if hasattr(realtime_quote, 'source') else 'unknown'})")
                    else:
                        logger.warning(f"{stock_name}({code}) 所有实时行情数据源均不可用，已降级为历史收盘价继续分析")
                else:
                    logger.info(f"{stock_name}({code}) 实时行情已禁用，使用历史收盘价继续分析")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 实时行情链路异常，已降级为历史收盘价继续分析: {e}")

            # 如果还是没有名称，使用代码作为名称
            if not stock_name:
                stock_name = f'股票{code}'

            # Step 2: 获取筹码分布 - 使用统一入口，带熔断保护
            chip_data = None
            try:
                chip_data = await self.fetcher_manager.get_chip_distribution(code)
                if chip_data:
                    logger.info(f"{stock_name}({code}) 筹码分布: 获利比例={chip_data.profit_ratio:.1%}, "
                              f"90%集中度={chip_data.concentration_90:.2%}")
                else:
                    logger.debug(f"{stock_name}({code}) 筹码分布获取失败或已禁用")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 获取筹码分布失败: {e}")

            # If agent mode is explicitly enabled, or specific agent skills are configured, use the Agent analysis pipeline.
            # NOTE: use config.agent_mode (explicit opt-in) instead of
            # config.is_agent_available() so that users who only configured an
            # API Key for the traditional analysis path are not silently
            # switched to Agent mode (which is slower and more expensive).
            use_agent = getattr(self.config, 'agent_mode', False)
            if not use_agent:
                # Auto-enable agent mode when specific skills are configured (e.g., scheduled task with strategy)
                configured_skills = getattr(self.config, 'agent_skills', [])
                if configured_skills and configured_skills != ['all']:
                    use_agent = True
                    logger.info(f"{stock_name}({code}) Auto-enabled agent mode due to configured skills: {configured_skills}")

            self._emit_progress(32, f"{stock_name}：正在聚合基本面与趋势数据")

            # Step 2.5: 基本面与对标能力聚合
            fundamental_context = {}
            peer_comparison = None
            try:
                ctx = await self.fetcher_manager.get_fundamental_context(code)
                if ctx:
                    fundamental_context = ctx
                # 获取行业对标数据 (P2)
                peer_comparison = await self.fetcher_manager.get_peer_comparison_context(code)
            except Exception as e:
                logger.warning("%s(%s) 获取基本面/对标数据失败: %s", stock_name, code, e)

            if realtime_quote and getattr(realtime_quote, 'name', None):
                stock_name = realtime_quote.name

            # 2. A股特色情报
            a_stock_intelligence = ""
            money_flow_intelligence = ""
            guru_insight = ""
            ak_fetcher = None
            if not is_us_stock_code(code):
                ak_fetcher = next((f for f in self.fetcher_manager.fetchers if f.name == "AkshareFetcher"), None)
            
            if ak_fetcher:
                tasks = [
                    ak_fetcher.get_value_metrics_async(code),
                    ak_fetcher.get_lhb_data_async(code),
                    ak_fetcher.get_research_reports_async(code),
                    ak_fetcher.get_money_flow_async(code),
                    ak_fetcher.get_limit_up_pool_async()
                ]
                intel_results = await asyncio.gather(*tasks, return_exceptions=True)
                if not isinstance(intel_results[0], Exception) and intel_results[0]:
                    fundamental_context['quality_metrics'] = intel_results[0]
                if not isinstance(intel_results[1], Exception) and intel_results[1]:
                    a_stock_intelligence += "\n### 龙虎榜动向\n" + "\n".join([f"- {i['date']}: {i['reason']} (净买: {i['net_amount']:.2f}万)" for i in intel_results[1][:3]])
                if not isinstance(intel_results[2], Exception) and intel_results[2] and intel_results[2].get('reports'):
                    a_stock_intelligence += "\n### 研报观点\n" + "\n".join([f"- [{r['org']}] {r['title']}" for r in intel_results[2]['reports'][:2]])
                if not isinstance(intel_results[3], Exception) and intel_results[3] and intel_results[3].get('main_inflow'):
                    money_flow_intelligence += f"\n### 资金面\n- 主力净流入: {intel_results[3]['main_inflow']:.2f}万\n"
                if not isinstance(intel_results[4], Exception) and intel_results[4]:
                    money_flow_intelligence += "### 题材热度\n" + "\n".join([f"- {t['name']} ({t['count']}涨停)" for t in intel_results[4][:2]])

                from src.agent.guru_analyzer import GuruAnalyzer
                guru = GuruAnalyzer(self.analyzer)
                guru_insight = await guru.analyze({
                    'stock_name': stock_name, 'code': code, 
                    'fundamental': fundamental_context, 'money_flow': money_flow_intelligence
                }, a_stock_intelligence)

            # 3. 趋势与历史数据
            end_date = date.today()
            hist = await self.db.get_data_range_async(code, end_date - timedelta(days=90), end_date)
            trend_result = None
            visual_description = ""
            today_k = {}
            yesterday_k = {}
            
            if hist:
                df = pd.DataFrame([bar.to_dict() for bar in hist])
                if self.config.enable_realtime_quote and realtime_quote:
                    df = self._augment_historical_with_realtime(df, realtime_quote, code)
                
                trend_result = await asyncio.to_thread(self.trend_analyzer.analyze, df, code)
                visual_description = f"\n### 视觉形态描述\n- 趋势: {trend_result.trend_status.value}\n"
                
                # 提取今日和昨日 K 线数据
                sorted_df = df.sort_values('date', ascending=False)
                if len(sorted_df) > 0:
                    today_k = sorted_df.iloc[0].to_dict()
                    # 转换时间戳为字符串
                    if isinstance(today_k.get('date'), (datetime, date)):
                        today_k['date'] = today_k['date'].isoformat()
                if len(sorted_df) > 1:
                    yesterday_k = sorted_df.iloc[1].to_dict()
                    if isinstance(yesterday_k.get('date'), (datetime, date)):
                        yesterday_k['date'] = yesterday_k['date'].isoformat()

            # 4. 舆情
            news_context = ""
            if self.search_service.is_available:
                from data_provider.cls_fetcher import ClsTelegramFetcher
                cls_fetcher = ClsTelegramFetcher()
                search_tasks = [
                    self.search_service.search_comprehensive_intel_async(code, stock_name, 5),
                    cls_fetcher.get_stock_news(stock_name, code)
                ]
                intel_raw = await asyncio.gather(*search_tasks, return_exceptions=True)
                if not isinstance(intel_raw[0], Exception) and intel_raw[0]:
                    news_context = self.search_service.format_intel_report(intel_raw[0], stock_name)
                if len(intel_raw) > 1 and not isinstance(intel_raw[1], Exception) and intel_raw[1]:
                    news_context += "\n\n### ⚡ 财联社电报\n" + "\n".join([f"- {n['content'][:100]}" for n in intel_raw[1][:5]])

            # 5. 组装上下文
            base_context = {
                'code': code, 
                'stock_name': stock_name, 
                'date': end_date.isoformat(),
                'today': today_k,
                'yesterday': yesterday_k
            }
            
            enhanced_context = self._enhance_context(
                base_context, realtime_quote, chip_data, trend_result, stock_name,
                fundamental_context, None, peer_comparison
            )

            
            final_news = (news_context or "")
            if a_stock_intelligence: final_news += "\n\n" + a_stock_intelligence
            if money_flow_intelligence: final_news += "\n\n" + money_flow_intelligence
            if guru_insight: final_news += "\n\n### 🎓 大师灵魂审视\n" + guru_insight
            if visual_description: final_news += "\n\n" + visual_description

            route_reasons: List[str] = []
            should_use_agent = self._coerce_bool_setting(
                getattr(self.config, "agent_mode", False),
                default=False,
            )
            if should_use_agent:
                route_reasons = ["config:AGENT_MODE=true"]
            else:
                should_use_agent, route_reasons = self._should_auto_route_to_agent(
                    code=code,
                    report_type=report_type,
                    enhanced_context=enhanced_context,
                    final_news=final_news,
                    fundamental_context=fundamental_context,
                    trend_result=trend_result,
                    a_stock_intelligence=a_stock_intelligence,
                    money_flow_intelligence=money_flow_intelligence,
                    guru_insight=guru_insight,
                )

            if should_use_agent:
                logger.info("[%s] 切换到 Agent 分析: %s", code, ", ".join(route_reasons))
                self._emit_progress(58, f"{stock_name}：正在切换 Agent 分析链路")
                return await self._maybe_await(self._analyze_with_agent(
                    code,
                    report_type,
                    query_id,
                    stock_name,
                    realtime_quote,
                    chip_data,
                    fundamental_context,
                    trend_result,
                    today_k=today_k,
                    yesterday_k=yesterday_k,
                    peer_comparison=peer_comparison,
                    news_context=final_news,
                    route_reasons=route_reasons,
                ))

            # 执行 AI 分析
            analysis_mode = getattr(self.config, 'analysis_mode', 'simple').lower()
            if analysis_mode == 'debate':
                from src.agent.debate_analyzer import DebateAnalyzer
                debate = DebateAnalyzer(self.config, self.analyzer)
                result = await debate.analyze(enhanced_context, final_news)
            else:
                result = await self.analyzer.analyze_async(enhanced_context, final_news)

            if result:
                self._emit_progress(94, f"{stock_name}：正在校验并整理分析结果")
                result.query_id = query_id
                result.historical_performance = enhanced_context.get('historical_performance')
                result.peer_comparison = peer_comparison
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                fill_chip_structure_if_needed(result, chip_data)

                # 调用 TraderAgent 补充交易决策
                if getattr(self.config, 'trader_agent_enabled', True):
                    self._emit_progress(95, f"{stock_name}：正在生成交易决策（Trader Agent）")
                    await self._run_trader_agent(
                        code=code,
                        stock_name=stock_name,
                        enhanced_context=enhanced_context,
                        query_id=query_id,
                        report_type=report_type,
                        trend_result=trend_result,
                        news_context=final_news,
                        route_reasons=route_reasons,
                        result=result,
                        realtime_quote=realtime_quote,
                    )

                await self.db.save_analysis_history_async(result, query_id, report_type.value, final_news, {}, self.save_context_snapshot)

            return result
    
        except Exception as e:
            logger.error(f"[{code}] AI 分析失败: {e}", exc_info=True)
            return None

    async def run(self, stock_codes=None, dry_run=False, send_notification=True, merge_notification=False) -> List[AnalysisResult]:
        if stock_codes is None: stock_codes = self.config.stock_list
        if not stock_codes: return []

        if not dry_run and hasattr(self.fetcher_manager, "prefetch_stock_names"):
            try:
                await asyncio.to_thread(
                    self.fetcher_manager.prefetch_stock_names,
                    list(stock_codes),
                    use_bulk=False,
                )
            except Exception as exc:
                logger.warning("股票名称预取失败，继续主流程: %s", exc)
        
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
            compute_ma_status=self._compute_ma_status,
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

        bullet_count = self._estimate_intel_bullet_count(final_news)
        if bullet_count >= 6 or len(final_news or "") >= 1600:
            major_reasons.append(f"dense_news_flow:{bullet_count}")

        risk_hits = self._extract_risk_keywords(final_news)
        if risk_hits:
            major_reasons.append(f"risk_sensitive_intel:{','.join(risk_hits[:2])}")

        a_share_layers = sum(
            1 for section in (a_stock_intelligence, money_flow_intelligence, guru_insight)
            if isinstance(section, str) and section.strip()
        )
        if a_share_layers >= 2:
            minor_reasons.append("multi_layer_a_share_intel")

        report_type_value = getattr(report_type, "value", str(report_type))
        if report_type_value != getattr(ReportType.SIMPLE, "value", "simple") and (major_reasons or minor_reasons):
            minor_reasons.append(f"report_type:{report_type_value}")

        reasons = major_reasons + minor_reasons
        should_route = bool(major_reasons) or len(minor_reasons) >= 2
        return should_route, reasons

    async def _ensure_agent_history(self, code: str, min_days: int = 240) -> None:
        """Ensure at least *min_days* of K-line history is in DB for agent tools."""
        from src.services.history_loader import get_frozen_target_date

        target = get_frozen_target_date()
        if target is None:
            target = self._resolve_resume_target_date(code)
        start = target - timedelta(days=int(min_days * 1.8))
        bars = self.db.get_data_range(code, start, target)
        if bars and len(bars) >= min(min_days, 200):
            logger.debug("[%s] Agent history: %d bars in DB, sufficient", code, len(bars))
            return
        try:
            df, source = await self.fetcher_manager.get_daily_data(code, days=min_days)
            if df is not None and not df.empty:
                await self.db.save_daily_data_async(df, code, source)
                logger.info("[%s] Prefetched %d rows of history for agent (source: %s)", code, len(df), source)
        except Exception as e:
            logger.warning("[%s] Agent history prefetch failed: %s", code, e)

    @staticmethod
    def _extract_quote_payload(realtime_quote: Any) -> Optional[Dict[str, Any]]:
        if realtime_quote is None:
            return None

        def _get_value(key: str, fallback: Optional[str] = None) -> Any:
            if isinstance(realtime_quote, dict):
                if key in realtime_quote:
                    return realtime_quote.get(key)
                return realtime_quote.get(fallback) if fallback else None
            value = getattr(realtime_quote, key, None)
            if value is not None:
                return value
            return getattr(realtime_quote, fallback, None) if fallback else None

        payload = {
            "name": _get_value("name"),
            "price": _get_value("price"),
            "change_pct": _get_value("change_pct"),
            "volume": _get_value("volume"),
            "amount": _get_value("amount"),
            "open": _get_value("open_price", "open"),
            "high": _get_value("high"),
            "low": _get_value("low"),
            "turnover_rate": _get_value("turnover_rate"),
            "volume_ratio": _get_value("volume_ratio"),
            "pe_ratio": _get_value("pe_ratio"),
            "pb_ratio": _get_value("pb_ratio"),
            "total_mv": _get_value("total_mv"),
            "circ_mv": _get_value("circ_mv"),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        return payload or None

    @staticmethod
    def _apply_trend_fallback(
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        if trend_result is None:
            result.sentiment_score = 50
            result.operation_advice = "Watch" if report_language == "en" else "观望"
            return

        score = getattr(trend_result, "signal_score", None)
        try:
            numeric_score = int(score)
        except (TypeError, ValueError):
            numeric_score = 50
        result.sentiment_score = numeric_score if numeric_score > 0 else 50

        trend_status = getattr(trend_result, "trend_status", None)
        trend_label = getattr(trend_status, "value", None) or str(trend_status or "").strip()
        if trend_label:
            result.trend_prediction = trend_label

        buy_signal = getattr(trend_result, "buy_signal", None)
        signal_label = getattr(buy_signal, "value", None) or str(buy_signal or "").strip()
        if signal_label:
            result.operation_advice = signal_label
        else:
            result.operation_advice = "Watch" if report_language == "en" else "观望"

        from src.agent.protocols import normalize_decision_signal

        signal_name = getattr(buy_signal, "name", "").lower()
        signal_to_decision = {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }
        result.decision_type = signal_to_decision.get(signal_name, result.decision_type or "hold")
        result.decision_type = normalize_decision_signal(result.decision_type)
        result.data_sources = f"{result.data_sources},trend:fallback" if result.data_sources else "trend:fallback"

    @staticmethod
    def _extract_chip_payload(chip_data: Any) -> Optional[Dict[str, Any]]:
        if chip_data is None:
            return None
        if isinstance(chip_data, dict):
            payload = dict(chip_data)
        elif hasattr(chip_data, "__dict__"):
            payload = {
                "profit_ratio": getattr(chip_data, "profit_ratio", None),
                "avg_cost": getattr(chip_data, "avg_cost", None),
                "concentration_90": getattr(chip_data, "concentration_90", None),
                "concentration_70": getattr(chip_data, "concentration_70", None),
                "date": getattr(chip_data, "date", None),
            }
        else:
            return None
        payload = {key: value for key, value in payload.items() if value is not None}
        return payload or None

    @staticmethod
    def _compute_ma_status(ma5: float, ma10: float, ma20: float, price: float) -> str:
        """
        Compute MA alignment status from price and MA values.
        Logic mirrors storage._analyze_ma_status (Issue #234).
        """
        price = price or 0
        ma5 = ma5 or 0
        ma10 = ma10 or 0
        ma20 = ma20 or 0
        if not all([ma5, ma10, ma20]):
            return "均线不足"
        if ma5 > ma10 > ma20:
            return "多头排列 📈" if price >= ma5 else "多头承压"
        elif ma5 < ma10 < ma20:
            return "空头排列 📉" if price <= ma5 else "空头反抽"
        elif price > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif price < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"

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

    @staticmethod
    def _extract_trend_payload(trend_result: Any) -> Optional[Dict[str, Any]]:
        if trend_result is None:
            return None
        if hasattr(trend_result, "to_dict"):
            payload = trend_result.to_dict()
        elif isinstance(trend_result, dict):
            payload = dict(trend_result)
        elif hasattr(trend_result, "__dict__"):
            payload = dict(trend_result.__dict__)
        else:
            return None
        return payload or None

    @staticmethod
    def _resolve_resume_target_date(
        code: str, current_time: Optional[datetime] = None
    ) -> date:
        """
        Resolve the trading date used by checkpoint/resume checks.
        """
        market = get_market_for_stock(normalize_stock_code(code))
        return get_effective_trading_date(market, current_time=current_time)

    @staticmethod
    def _safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
        """
        安全转换为字典
        """
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            payload = value.to_dict()
        elif isinstance(value, dict):
            payload = dict(value)
        elif hasattr(value, "__dict__"):
            payload = dict(value.__dict__)
        else:
            return None
        return payload or None

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
        1. 获取数据
        2. 保存数据
        3. AI 分析
        4. 单股推送（可选，#55）

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
        frozen_td = self._resolve_resume_target_date(code, current_time=current_time)
        token = set_frozen_target_date(frozen_td)
        try:
            self._emit_progress(12, f"{code}：正在准备分析任务")
            # Step 1: 获取并保存数据
            success, error = await self.fetch_and_save_stock_data(
                code, current_time=current_time
            )
            
            if not success:
                logger.warning(f"[{code}] 数据获取失败: {error}")
                # 即使获取失败，也尝试用已有数据分析
            else:
                self._emit_progress(16, f"{code}：行情数据准备完成")
            
            # Step 2: AI 分析
            if skip_analysis:
                logger.info(f"[{code}] 跳过 AI 分析（dry-run 模式）")
                return None
            
            effective_query_id = analysis_query_id or self.query_id or uuid.uuid4().hex
            result = await self.analyze_stock(code, report_type, query_id=effective_query_id)
            
            if result and result.success:
                logger.info(
                    f"[{code}] 分析完成: {result.operation_advice}, "
                    f"评分 {result.sentiment_score}"
                )
                
                # 单股推送模式（#55）：每分析完一只股票立即推送
                if single_stock_notify:
                    await self._send_single_stock_notification_async(
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

    @staticmethod
    def _sync_maybe_await(value: Any) -> Any:
        return sync_maybe_await(value)

    def _send_notifications(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
    ) -> bool:
        """Synchronous notification helper kept for legacy callers/tests."""
        if not results or not self.notifier.is_available():
            return False

        channels = self.notifier.get_available_channels()
        sent = False

        def _channel_enabled(target: NotificationChannel) -> bool:
            return any(channel == target or getattr(channel, "value", None) == target.value for channel in channels)

        def _send_email_report(subset: List[AnalysisResult], receivers: Optional[List[str]] = None) -> None:
            nonlocal sent
            content = self.notifier.generate_dashboard_report(subset)
            image_bytes = None
            if "email" in getattr(self.notifier, "_markdown_to_image_channels", set()):
                from src.md2img import markdown_to_image

                image_bytes = markdown_to_image(
                    content,
                    max_chars=getattr(self.notifier, "_markdown_to_image_max_chars", 15000),
                )
            if self.notifier._should_use_image_for_channel(NotificationChannel.EMAIL, image_bytes):
                sent = bool(self._sync_maybe_await(
                    self.notifier._send_email_with_inline_image(
                        content,
                        image_bytes,
                        receivers=receivers,
                    )
                )) or sent
            else:
                sent = bool(self._sync_maybe_await(
                    self.notifier.send_to_email(content, receivers=receivers)
                )) or sent

        if _channel_enabled(NotificationChannel.WECHAT):
            wechat_content = (
                self.notifier.generate_wechat_dashboard(results)
                if hasattr(self.notifier, "generate_wechat_dashboard")
                else self.notifier.generate_dashboard_report(results)
            )
            image_bytes = None
            if "wechat" in getattr(self.notifier, "_markdown_to_image_channels", set()):
                from src.md2img import markdown_to_image

                image_bytes = markdown_to_image(
                    wechat_content,
                    max_chars=getattr(self.notifier, "_markdown_to_image_max_chars", 15000),
                )
            if self.notifier._should_use_image_for_channel(NotificationChannel.WECHAT, image_bytes):
                sent = bool(self._sync_maybe_await(self.notifier._send_wechat_image(image_bytes))) or sent
            else:
                if "wechat" in getattr(self.notifier, "_markdown_to_image_channels", set()):
                    engine = getattr(get_config(), "md2img_engine", "unknown")
                    logger.warning("企业微信 Markdown 转图片失败，已回退文本推送，engine=%s", engine)
                sent = bool(self._sync_maybe_await(self.notifier.send_to_wechat(wechat_content))) or sent

        if _channel_enabled(NotificationChannel.EMAIL):
            groups = list(getattr(self.config, "stock_email_groups", []) or [])
            grouped_codes = set()
            for codes, receivers in groups:
                codes_set = set(codes or [])
                subset = [result for result in results if getattr(result, "code", None) in codes_set]
                if not subset:
                    continue
                grouped_codes.update(getattr(result, "code", None) for result in subset)
                _send_email_report(subset, receivers=list(receivers or []))

            remaining = [result for result in results if getattr(result, "code", None) not in grouped_codes]
            if remaining:
                _send_email_report(remaining, receivers=None)

        return sent

    def _send_single_stock_notification(
        self,
        result: AnalysisResult,
        report_type: ReportType = ReportType.SIMPLE,
        fallback_code: Optional[str] = None,
    ) -> None:
        """发送单股通知，供直接单股入口和批量串行推送共用。"""
        try:
            self._sync_maybe_await(
                self._send_single_stock_notification_async(
                    result,
                    report_type=report_type,
                    fallback_code=fallback_code,
                )
            )
        except Exception as e:
            stock_code = getattr(result, "code", None) or fallback_code or "unknown"
            logger.error(f"[{stock_code}] 单股推送异常: {e}")

    async def _run_trader_agent(
        self,
        code: str,
        stock_name: str,
        enhanced_context: Dict[str, Any],
        query_id: str,
        report_type: Any,
        trend_result: Any = None,
        news_context: str = "",
        route_reasons: Optional[List[str]] = None,
        result: Optional[Any] = None,
        realtime_quote: Optional[Any] = None,
    ) -> None:
        """Run TraderAgent to produce final trading decision and fill result."""
        try:
            from src.agent.agents.trader_agent import TraderAgent

            agent = TraderAgent(analyzer=self.analyzer, config=self.config)

            # Build context for trader
            from src.agent.protocols import AgentContext

            # Extract real-time price from multiple sources
            current_price = None
            yesterday_close = None
            today_data = enhanced_context.get("today", {})
            if isinstance(today_data, dict):
                current_price = today_data.get("close")
                yesterday_close = enhanced_context.get("yesterday", {}).get("close") if isinstance(enhanced_context.get("yesterday"), dict) else None

            # Override with realtime_quote if available (more accurate)
            if realtime_quote is not None:
                rt_price = getattr(realtime_quote, "price", None)
                if rt_price is not None:
                    current_price = rt_price

            trader_meta = {
                "report_language": getattr(self.config, "report_language", "zh"),
            }
            if current_price is not None:
                trader_meta["current_price"] = float(current_price)
            if yesterday_close is not None:
                trader_meta["yesterday_close"] = float(yesterday_close)

            trader_ctx = AgentContext(
                stock_code=code,
                stock_name=stock_name or "",
                query=f"Trading decision for {code}",
                meta=trader_meta,
            )

            # Build prior opinions from result if available
            if result is not None:
                from src.agent.protocols import AgentOpinion
                # Add technical opinion if available
                if result.technical_analysis:
                    trader_ctx.add_opinion(AgentOpinion(
                        agent_name="technical",
                        signal=result.decision_type or "hold",
                        confidence=result.sentiment_score / 100.0 if result.sentiment_score else 0.5,
                        reasoning=result.technical_analysis[:200] if result.technical_analysis else "",
                    ))
                # Add fundamental opinion if available
                if result.fundamental_analysis:
                    trader_ctx.add_opinion(AgentOpinion(
                        agent_name="fundamental",
                        signal=result.decision_type or "hold",
                        confidence=0.6,
                        reasoning=result.fundamental_analysis[:200] if result.fundamental_analysis else "",
                    ))
                # Add news/intel opinion if available
                if result.news_summary:
                    trader_ctx.add_opinion(AgentOpinion(
                        agent_name="intel",
                        signal="hold",
                        confidence=0.5,
                        reasoning=result.news_summary[:200] if result.news_summary else "",
                    ))

            opinion = await agent.run(trader_ctx)
            if opinion is None:
                logger.warning(f"[{code}] TraderAgent returned None")
                return

            # Fill result with trader's decision
            if result is not None:
                result.trader_decision = opinion.raw_data
                if opinion.raw_data:
                    result.position_sizing_pct = opinion.raw_data.get("position_sizing", {}).get("recommended_pct")
                    result.holding_period_days = opinion.raw_data.get("holding_period", {}).get("expected_days")
                    result.risk_reward_ratio = opinion.raw_data.get("risk_assessment", {}).get("risk_reward_ratio")

        except Exception as e:
            logger.error(f"[{code}] TraderAgent failed: {e}", exc_info=True)


    async def _send_single_stock_notification_async(
        self,
        result: AnalysisResult,
        report_type: ReportType = ReportType.SIMPLE,
        fallback_code: Optional[str] = None,
    ) -> bool:
        stock_code = getattr(result, "code", None) or fallback_code or "unknown"
        notify_lock = getattr(self, "_single_stock_notify_lock", None)
        if notify_lock is None:
            with _SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD:
                notify_lock = getattr(self, "_single_stock_notify_lock", None)
                if notify_lock is None:
                    notify_lock = threading.Lock()
                    setattr(self, "_single_stock_notify_lock", notify_lock)
        try:
            return await send_single_stock_notification(
                notifier=self.notifier,
                result=result,
                report_type=report_type,
                fallback_code=stock_code,
                notify_lock=notify_lock,
            )
        except Exception as e:
            logger.error(f"[{stock_code}] 单股推送异常: {e}")
            return False

    async def _analyze_with_agent(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        stock_name: Optional[str] = None,
        realtime_quote: Any = None,
        chip_data: Any = None,
        fundamental_context: Optional[Dict[str, Any]] = None,
        trend_result: Any = None,
        *,
        today_k: Optional[Dict[str, Any]] = None,
        yesterday_k: Optional[Dict[str, Any]] = None,
        peer_comparison: Optional[Dict[str, Any]] = None,
        news_context: str = "",
        route_reasons: Optional[List[str]] = None,
    ) -> Optional[AnalysisResult]:
        return await run_agent_analysis(
            code=code,
            report_type=report_type,
            query_id=query_id,
            config=self.config,
            analyzer=self.analyzer,
            db=self.db,
            search_service=self.search_service,
            emit_progress=self._emit_progress,
            enhance_context=self._enhance_context,
            stock_name=stock_name,
            realtime_quote=realtime_quote,
            chip_data=chip_data,
            fundamental_context=fundamental_context,
            trend_result=trend_result,
            today_k=today_k,
            yesterday_k=yesterday_k,
            peer_comparison=peer_comparison,
            news_context=news_context,
            route_reasons=route_reasons,
        )
