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
import uuid
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd

from src.config import get_config, Config
from src.storage import get_db
from data_provider import DataFetcherManager
from data_provider.base import normalize_stock_code
from data_provider.realtime_types import ChipDistribution
from src.analyzer import GeminiAnalyzer, AnalysisResult, fill_chip_structure_if_needed, fill_price_position_if_needed
from src.data.stock_mapping import STOCK_NAME_MAP
from src.notification import NotificationService, NotificationChannel
from src.report_language import (
    get_unknown_text,
    localize_confidence_level,
    normalize_report_language,
)
from src.search_service import SearchService
from src.services.social_sentiment_service import SocialSentimentService
from src.enums import ReportType
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from src.core.trading_calendar import get_market_for_stock, is_market_open
from data_provider.us_index_mapping import is_us_stock_code
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
        
        self.db = get_db()
        self.search_service = SearchService(
            bocha_keys=self.config.bocha_api_keys,
            tavily_keys=self.config.tavily_api_keys,
            exa_keys=self.config.exa_api_keys,
            brave_keys=self.config.brave_api_keys,
            serpapi_keys=self.config.serpapi_keys,
            minimax_keys=self.config.minimax_api_keys,
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
        if inspect.isawaitable(value):
            return await value
        return value

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

    async def fetch_and_save_stock_data(self, code: str, force_refresh: bool = False) -> Tuple[bool, Optional[str]]:
        try:
            await self.fetcher_manager.get_stock_name(code)
        except Exception as e:
            return False, str(e)
        try:
            if not force_refresh and self.db.has_today_data(code):
                return True, None
            
            res = await self.fetcher_manager.get_daily_data(code, days=45)
            df, source_name = res
                
            if df is None or df.empty: 
                return False, "获取数据为空"
                
            self.db.save_daily_data(df, code, source_name)
            return True, None
        except Exception as e:
            logger.error(f"[{code}] 数据抓取失败: {e}")
            return False, str(e)

    async def analyze_stock(self, code: str, report_type: ReportType, query_id: str) -> Optional[AnalysisResult]:
        return await self._analyze_stock_async(code, report_type, query_id)

    async def _analyze_stock_async(self, code: str, report_type: ReportType, query_id: str) -> Optional[AnalysisResult]:
        """分析单只股票异步主流程"""
        try:
            stock_name = await self.fetcher_manager.get_stock_name(code)
            
            # 1. 并发请求基础数据
            realtime_task = self.fetcher_manager.get_realtime_quote(code)
            chip_task = self.fetcher_manager.get_chip_distribution(code)
            fundamental_task = self.fetcher_manager.get_fundamental_context(code)
            region = "us" if is_us_stock_code(code) else "cn"
            market_overview_task = self.fetcher_manager.get_market_overview(region=region)
            
            realtime_quote, chip_data, fundamental_context, market_overview = await asyncio.gather(
                realtime_task, chip_task, fundamental_task, market_overview_task,
                return_exceptions=True
            )
            
            if isinstance(realtime_quote, Exception): realtime_quote = None
            if isinstance(chip_data, Exception): chip_data = None
            if isinstance(fundamental_context, Exception): fundamental_context = {}
            if isinstance(market_overview, Exception): market_overview = {}
            if isinstance(fundamental_context, dict):
                fundamental_context = await asyncio.to_thread(
                    self._attach_belong_boards_to_fundamental_context,
                    code,
                    fundamental_context,
                )

            if realtime_quote and getattr(realtime_quote, 'name', None):
                stock_name = realtime_quote.name

            # 2. A股特色情报
            a_stock_intelligence = ""
            money_flow_intelligence = ""
            guru_insight = ""
            ak_fetcher = None
            if not is_us_stock_code(code) and hasattr(self.fetcher_manager, "_fetchers"):
                ak_fetcher = next((f for f in self.fetcher_manager._fetchers if f.name == "AkshareFetcher"), None)
            
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
                fundamental_context, market_overview
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
                return await self._maybe_await(self._analyze_with_agent(
                    code,
                    report_type,
                    query_id,
                    stock_name,
                    realtime_quote,
                    chip_data,
                    fundamental_context,
                    trend_result,
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
                result.query_id = query_id
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                fill_chip_structure_if_needed(result, chip_data)
                await self.db.save_analysis_history_async(result, query_id, report_type.value, final_news, {}, self.save_context_snapshot)

            return result

        except Exception as e:
            logger.error(f"[{code}] AI 分析失败: {e}", exc_info=True)
            return None

    async def process_single_stock(self, code, skip_analysis=False, single_stock_notify=False, report_type=ReportType.SIMPLE):
        logger.info(f"[{code}] 正在处理...")
        await self.fetch_and_save_stock_data(code)
        if skip_analysis: return None
        result = await self.analyze_stock(code, report_type, uuid.uuid4().hex)
        if result and single_stock_notify and self.notifier.is_available():
            report_content = self.notifier.generate_single_stock_report(result)
            await self.notifier.send(report_content, email_stock_codes=[code])
        return result

    async def run(self, stock_codes=None, dry_run=False, send_notification=True, merge_notification=False):
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

    def _enhance_context(self, context, realtime_quote, chip_data, trend_result, stock_name, fundamental_context=None, market_overview=None):
        def _as_float(value: Any) -> Optional[float]:
            try:
                number = float(value)
                return None if pd.isna(number) else number
            except Exception:
                logger.debug("_as_float failed for value=%r", value)
                return None

        def _get_quote_value(obj: Any, key: str) -> Any:
            if obj is None: return None
            return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

        enhanced = context.copy()
        enhanced['stock_name'] = stock_name
        
        if fundamental_context: enhanced['fundamental'] = fundamental_context
        if market_overview: enhanced['market_overview'] = market_overview

        trend_payload = {}
        if trend_result:
            if hasattr(trend_result, "to_dict"): trend_payload = trend_result.to_dict()
            elif hasattr(trend_result, "__dict__"): trend_payload = trend_result.__dict__
        if trend_payload: enhanced['trend_analysis'] = trend_payload

        if realtime_quote:
            enhanced['realtime'] = {
                'price': _get_quote_value(realtime_quote, 'price'),
                'change_pct': _get_quote_value(realtime_quote, 'change_pct'),
                'volume': _get_quote_value(realtime_quote, 'volume'),
                'amount': _get_quote_value(realtime_quote, 'amount'),
                'open': _get_quote_value(realtime_quote, 'open_price'),
                'high': _get_quote_value(realtime_quote, 'high'),
                'low': _get_quote_value(realtime_quote, 'low'),
                'turnover_rate': _get_quote_value(realtime_quote, 'turnover_rate'),
                'pe_ratio': _get_quote_value(realtime_quote, 'pe_ratio'),
                'total_mv': _get_quote_value(realtime_quote, 'total_mv'),
            }
        if chip_data:
            enhanced['chip_structure'] = {'profit_ratio': chip_data.profit_ratio, 'avg_cost': chip_data.avg_cost}

        today = dict(enhanced.get('today') or {})
        yesterday = dict(enhanced.get('yesterday') or {})
        
        # 注入均线
        ma5 = _as_float(trend_payload.get('ma5'))
        if ma5:
            today['ma5'] = round(ma5, 2)
            today['ma10'] = round(_as_float(trend_payload.get('ma10')) or 0, 2)
            today['ma20'] = round(_as_float(trend_payload.get('ma20')) or 0, 2)

        # 实时价覆盖历史收盘价
        rt_price = _as_float(_get_quote_value(realtime_quote, 'price'))
        if rt_price:
            today['close'] = round(rt_price, 2)
            today['pct_chg'] = round(_as_float(_get_quote_value(realtime_quote, 'change_pct')) or 0, 2)

        enhanced['today'] = today
        enhanced['ma_status'] = self._compute_ma_status(today.get('ma5'), today.get('ma10'), today.get('ma20'), today.get('close'))
        
        if yesterday and today:
            prev_close = _as_float(yesterday.get('close'))
            if prev_close and today.get('close'): 
                enhanced['price_change_ratio'] = round((today['close'] - prev_close) / prev_close * 100, 2)

        return enhanced

    @staticmethod
    def _compute_ma_status(ma5, ma10, ma20, price=None) -> str:
        if not all([ma5, ma10, ma20]): return "均线不足"
        if ma5 > ma10 > ma20: return "多头排列" if price is None or price >= ma5 else "多头承压"
        if ma5 < ma10 < ma20: return "空头排列" if price is None or price <= ma5 else "空头反抽"
        return "震荡整理"

    def _augment_historical_with_realtime(self, df: pd.DataFrame, realtime_quote: Any, code: str) -> pd.DataFrame:
        if df is None or df.empty or realtime_quote is None: return df
        price = float(getattr(realtime_quote, 'price', 0) or 0)
        if price <= 0: return df
        df = df.copy()
        new_row = {'code': code, 'date': date.today(), 'close': price, 'open': getattr(realtime_quote, 'open_price', price), 'high': getattr(realtime_quote, 'high', price), 'low': getattr(realtime_quote, 'low', price)}
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        return df

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
            "pe_ratio": _get_value("pe_ratio"),
            "pb_ratio": _get_value("pb_ratio"),
            "total_mv": _get_value("total_mv"),
            "circ_mv": _get_value("circ_mv"),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        return payload or None

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

        try:
            belong_boards = self._call_fetcher_manager_sync(
                "get_belong_boards_sync",
                "get_belong_boards",
                stock_code,
            ) or []
        except Exception:
            belong_boards = []

        context["belong_boards"] = [
            dict(item) if isinstance(item, dict) else item
            for item in belong_boards
        ]
        return context

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
        news_context: str = "",
        route_reasons: Optional[List[str]] = None,
    ) -> Optional[AnalysisResult]:
        from src.agent.factory import build_agent_executor
        route_suffix = f" ({', '.join(route_reasons)})" if route_reasons else ""
        logger.info(f"[{code}] 正在启动智能 Agent 深度分析{route_suffix}...")
        
        executor = build_agent_executor(self.config, skills=getattr(self.config, "agent_skills", None))
        prompt_name = stock_name or code
        agent_context: Dict[str, Any] = {
            "stock_code": code,
            "stock_name": prompt_name,
            "report_type": getattr(report_type, "value", str(report_type)),
            "report_language": normalize_report_language(getattr(self.config, "report_language", "zh")),
        }
        quote_payload = self._extract_quote_payload(realtime_quote)
        if quote_payload:
            agent_context["realtime_quote"] = quote_payload
        chip_payload = self._extract_chip_payload(chip_data)
        if chip_payload:
            agent_context["chip_distribution"] = chip_payload
        trend_payload = self._extract_trend_payload(trend_result)
        if trend_payload:
            agent_context["trend_result"] = trend_payload
        if news_context:
            agent_context["news_context"] = news_context

        agent_result = executor.run(
            f"深度分析股票 {code} ({prompt_name})，请结合最新技术面、筹码面、新闻情报和基本面给出决策仪表盘。",
            context=agent_context,
        )
        
        result = self._agent_result_to_analysis_result(agent_result, code, prompt_name, report_type, query_id)
        logger.info(f"[{code}] Agent 分析完成，评分: {result.sentiment_score}")

        if result and getattr(self.search_service, "is_available", False):
            try:
                news_response = self.search_service.search_stock_news(
                    stock_code=code,
                    stock_name=result.name,
                    max_results=5,
                )
                news_items = getattr(news_response, "results", None) or []
                if news_items:
                    try:
                        self.db.save_news_intel(
                            news_items=news_items,
                            code=code,
                            name=result.name,
                            query_id=query_id,
                        )
                    except TypeError:
                        self.db.save_news_intel(news_items)
            except Exception:
                logger.debug("[%s] Agent 新闻持久化跳过", code, exc_info=True)

        return result

    def _agent_result_to_analysis_result(
        self,
        agent_result: Any,
        code: str,
        stock_name: str,
        report_type: ReportType,
        query_id: str,
    ) -> AnalysisResult:
        dashboard_payload = agent_result.dashboard or {}
        if not dashboard_payload and getattr(agent_result, "content", ""):
            try:
                parsed = json.loads(agent_result.content)
                if isinstance(parsed, dict):
                    dashboard_payload = parsed
            except Exception:
                dashboard_payload = {}

        provider_tag = f"agent:{getattr(agent_result, 'provider', '')}".rstrip(":")
        dashboard_name = str(dashboard_payload.get("stock_name") or "").strip() if isinstance(dashboard_payload, dict) else ""
        resolved_name = dashboard_name if self._is_placeholder_stock_name(stock_name, code) and dashboard_name else stock_name

        if not getattr(agent_result, "success", False):
            error_message = getattr(agent_result, "error", None) or getattr(agent_result, "content", "") or "Agent 分析失败"
            return AnalysisResult(
                code=code,
                name=resolved_name or code,
                sentiment_score=50,
                trend_prediction="震荡",
                operation_advice="观望",
                decision_type="hold",
                confidence_level="中",
                analysis_summary=error_message,
                data_sources=provider_tag,
                success=False,
                error_message=error_message,
                query_id=query_id,
                model_used=getattr(agent_result, "model", None) or getattr(agent_result, "provider", None),
            )

        return AnalysisResult(
            code=code,
            name=resolved_name or code,
            sentiment_score=self._extract_agent_score(dashboard_payload, getattr(agent_result, "content", "")),
            trend_prediction=dashboard_payload.get("trend_prediction") or "震荡",
            operation_advice=dashboard_payload.get("operation_advice") or "观望",
            decision_type=dashboard_payload.get("decision_type") or "hold",
            confidence_level=dashboard_payload.get("confidence_level") or "中",
            dashboard=dashboard_payload.get("dashboard") or dashboard_payload,
            analysis_summary=dashboard_payload.get("analysis_summary") or getattr(agent_result, "content", ""),
            data_sources=provider_tag,
            success=True,
            query_id=query_id,
            model_used=getattr(agent_result, "model", None) or getattr(agent_result, "provider", None),
        )

    @staticmethod
    def _safe_int(value: Any, default: int = 50) -> int:
        try:
            if value is None: return default
            if isinstance(value, (int, float)): return int(value)
            match = re.search(r"-?\d+", str(value))
            return int(match.group(0)) if match else default
        except Exception:
            logger.debug("_safe_int failed for value=%r", value)
            return default

    @staticmethod
    def _is_placeholder_stock_name(name: Optional[str], code: str) -> bool:
        text = (name or "").strip()
        if not text:
            return True
        lowered = text.lower()
        return text == code or text.startswith("股票") or lowered in {"unknown", "未知", "n/a"}

    @staticmethod
    def _extract_agent_score(dashboard_payload: Dict[str, Any], raw_text: str = "") -> int:
        try:
            from src.analyzer.core import GeminiAnalyzer as AnalyzerCore

            return AnalyzerCore._extract_sentiment_score(
                dashboard_payload if isinstance(dashboard_payload, dict) else {},
                dashboard_payload if isinstance(dashboard_payload, dict) else {},
                raw_text=raw_text,
            )
        except Exception:
            return StockAnalysisPipeline._safe_int(
                dashboard_payload.get("sentiment_score") if isinstance(dashboard_payload, dict) else None,
                50,
            )
