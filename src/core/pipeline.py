# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 核心分析流水线
===================================

职责：
1. 管理整个分析流程
2. 协调数据获取、存储、搜索、分析、通知等模块
3. 实现并发控制和异常处理
4. 提供股票分析的核心功能
"""

import asyncio
import logging
import random
import uuid

import uuid
from collections import defaultdict
from datetime import date, timedelta
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
        self.fetcher_manager = DataFetcherManager(fetchers=plugin_fetchers, config=self.config)
        plugin_ctx.fetcher_manager = self.fetcher_manager
        
        self.trend_analyzer = StockTrendAnalyzer()
        self.analyzer = analyzer_factory(self.config) if analyzer_factory else GeminiAnalyzer(config=self.config)
        self.notifier = notifier_factory(source_message=source_message) if notifier_factory else NotificationService(source_message=source_message)
        
        self.social_sentiment_service = SocialSentimentService(
            api_key=self.config.social_sentiment_api_key,
            api_url=self.config.social_sentiment_api_url,
        )

    async def fetch_and_save_stock_data(self, code: str, force_refresh: bool = False) -> Tuple[bool, Optional[str]]:
        stock_name = self.fetcher_manager.get_stock_name(code)
        try:
            if not force_refresh and self.db.has_today_data(code, date.today()):
                return True, None
            df, source_name = await asyncio.to_thread(self.fetcher_manager.get_daily_data, code, 30)
            if df is None or df.empty: return False, "获取数据为空"
            await asyncio.to_thread(self.db.save_daily_data, df, code, source_name)
            return True, None
        except Exception as e:
            return False, str(e)
    
    async def analyze_stock(self, code: str, report_type: ReportType, query_id: str) -> Optional[AnalysisResult]:
        """分析单只股票异步主流程"""
        try:
            stock_name = self.fetcher_manager.get_stock_name(code)

            # 1. 实时行情
            realtime_quote = await self.fetcher_manager.get_realtime_quote(code)
            if realtime_quote and realtime_quote.name:
                stock_name = realtime_quote.name

            # 2. 筹码分布
            chip_data = await asyncio.to_thread(self.fetcher_manager.get_chip_distribution, code)

            # 3. 基本面上下文
            fundamental_context = await asyncio.to_thread(self.fetcher_manager.get_fundamental_context, code)
            fundamental_context = self._attach_belong_boards_to_fundamental_context(code, fundamental_context)

            # 4. A股深度情报与资金面增强 (重点注入点)
            a_stock_intelligence = ""
            money_flow_intelligence = ""
            guru_insight = ""
            ak_fetcher = None
            if not is_us_stock_code(code) and hasattr(self.fetcher_manager, "_fetchers"):
                ak_fetcher = next((f for f in self.fetcher_manager._fetchers if f.name == "AkshareFetcher"), None)
            
            if ak_fetcher:
                # 4.1 财务质量 (巴菲特/芒格)
                quality_metrics = await asyncio.to_thread(ak_fetcher.get_value_metrics, code)
                if fundamental_context and quality_metrics:
                    fundamental_context['quality_metrics'] = quality_metrics
                
                # 4.2 龙虎榜/研报/电报
                lhb = await asyncio.to_thread(ak_fetcher.get_lhb_data, code)
                if lhb:
                    a_stock_intelligence += "\n### 龙虎榜动向 (近30日)\n" + "\n".join([f"- {i['date']}: {i['reason']} (净买额: {i['net_amount']:.2f}万)" for i in lhb[:3]])
                
                reports = await asyncio.to_thread(ak_fetcher.get_research_reports, code)
                if reports and reports.get('reports'):
                    a_stock_intelligence += "\n### 机构研报观点\n" + "\n".join([f"- [{r['org']}] {r['title']} (评级: {r['rating']})" for r in reports['reports'][:2]])
                
                telegraphs = await asyncio.to_thread(ak_fetcher.get_latest_telegraph, [stock_name])
                if telegraphs:
                    a_stock_intelligence += "\n### 财联社实时快讯\n" + "\n".join([f"- [{t['time']}] {t['title']}" for t in telegraphs[:3]])

                # 4.3 资金流向
                flow = await asyncio.to_thread(ak_fetcher.get_money_flow, code)
                if flow and flow.get('main_inflow'):
                    money_flow_intelligence += f"\n### 资金面动向\n- 主力净流入: {flow['main_inflow']:.2f}万 ({flow['main_pct']:.2f}%)\n"
                
                # 4.4 题材梯队 (龙头识别依据)
                zt_pool = await asyncio.to_thread(ak_fetcher.get_limit_up_pool)
                if zt_pool:
                    money_flow_intelligence += "### 题材热度\n" + "\n".join([f"- {t['name']} ({t['count']}家涨停): 龙头={', '.join(t['leaders'])}" for t in zt_pool[:2]])

                # 4.5 大师深度审视
                from src.agent.guru_analyzer import GuruAnalyzer
                guru = GuruAnalyzer(self.analyzer)
                guru_insight = await guru.analyze({
                    'stock_name': stock_name, 'code': code, 
                    'fundamental': fundamental_context, 'money_flow': money_flow_intelligence
                }, a_stock_intelligence)

            # 5. 趋势分析与视觉形态
            end_date = date.today()
            hist = await asyncio.to_thread(self.db.get_data_range, code, end_date - timedelta(days=89), end_date)
            trend_result = None
            visual_description = ""
            if hist:
                df = pd.DataFrame([bar.to_dict() for bar in hist])
                if self.config.enable_realtime_quote and realtime_quote:
                    df = self._augment_historical_with_realtime(df, realtime_quote, code)
                trend_result = await asyncio.to_thread(self.trend_analyzer.analyze, df, code)
                
                # 视觉文字化
                visual_description = f"\n### 视觉形态描述\n- 趋势: {trend_result.trend_status.value}\n"
                if trend_result.ma_alignment == "bullish": visual_description += "- 形态: 均线典型【多头排列】，具备向上爆发力。\n"
                elif trend_result.ma_alignment == "bearish": visual_description += "- 形态: 均线【空头排列】，破位压力明显。\n"

            # 5. 搜索深度情报 (Async native + Cls Telegram)
            news_context = ""
            if self.search_service.is_available:
                # 并发抓取：通用搜索 + 财联社电报
                from data_provider.cls_fetcher import ClsTelegramFetcher
                cls_fetcher = ClsTelegramFetcher()
                
                search_tasks = [
                    self.search_service.search_comprehensive_intel_async(code, stock_name, 5),
                    cls_fetcher.get_stock_news(stock_name, code)
                ]
                
                intel_results = await asyncio.gather(*search_tasks, return_exceptions=True)
                
                # 处理通用搜索结果
                intel = intel_results[0] if not isinstance(intel_results[0], Exception) else {}
                if intel:
                    news_context = self.search_service.format_intel_report(intel, stock_name)
                
                # 处理财联社电报（注入灵魂）
                cls_news = intel_results[1] if len(intel_results) > 1 and not isinstance(intel_results[1], Exception) else []
                if cls_news:
                    cls_text = "\n\n### ⚡ 财联社实时快讯\n" + "\n".join([
                        f"- [{n['date']}] {n['content']}" for n in cls_news[:5]
                    ])
                    news_context += cls_text
                    logger.info(f"[{code}] 成功注入 {len(cls_news)} 条财联社电报")


            # 7. 组装最终上下文并调用 AI
            enhanced_context = self._enhance_context(
                {'code': code, 'stock_name': stock_name, 'date': date.today().isoformat()}, 
                realtime_quote, chip_data, trend_result, stock_name, fundamental_context
            )
            
            # 整合所有维度的情报
            final_news = (news_context or "")
            if a_stock_intelligence: final_news += "\n\n" + a_stock_intelligence
            if money_flow_intelligence: final_news += "\n\n" + money_flow_intelligence
            if guru_insight: final_news += "\n\n### 🎓 大师灵魂审视 (Buffett & Munger)\n" + guru_insight
            if visual_description: final_news += "\n\n" + visual_description

            # 执行辩论模式或单模型分析
            analysis_mode = getattr(self.config, 'analysis_mode', 'simple').lower()
            if analysis_mode == 'debate':
                from src.agent.debate_analyzer import DebateAnalyzer
                debate_analyzer = DebateAnalyzer(self.config, self.analyzer)
                result = await debate_analyzer.analyze(enhanced_context, final_news)
            else:
                result = await self.analyzer.analyze_async(enhanced_context, final_news)

            if result:
                result.query_id = query_id
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                fill_chip_structure_if_needed(result, chip_data)
                # 保存历史
                await asyncio.to_thread(self.db.save_analysis_history, result, query_id, report_type.value, final_news, {}, self.save_context_snapshot)

            return result

        except Exception as e:
            logger.error("%s 分析失败: %s", code, e)
            logger.exception(e)
            return None

    # --- 以下为辅助方法，保持原有逻辑 ---

    def _enhance_context(self, context, realtime_quote, chip_data, trend_result, stock_name, fundamental_context):
        enhanced = context.copy()
        enhanced['stock_name'] = stock_name
        if fundamental_context: enhanced['fundamental'] = fundamental_context
        if realtime_quote:
            enhanced['realtime'] = {
                'price': realtime_quote.price, 'change_pct': realtime_quote.change_pct,
                'volume_ratio': getattr(realtime_quote, 'volume_ratio', None),
                'turnover_rate': getattr(realtime_quote, 'turnover_rate', None),
            }
        if chip_data:
            enhanced['chip'] = {'profit_ratio': chip_data.profit_ratio, 'avg_cost': chip_data.avg_cost}
        if trend_result:
            enhanced['trend_analysis'] = {
                'trend_status': trend_result.trend_status.value, 'signal_score': trend_result.signal_score
            }
        return enhanced

    def _attach_belong_boards_to_fundamental_context(self, code: str, fundamental_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        enriched = dict(fundamental_context) if isinstance(fundamental_context, dict) else {}
        try:
            boards = self.fetcher_manager.get_belong_boards(code)
            enriched["belong_boards"] = boards if isinstance(boards, list) else []
        except: pass
        return enriched

    def _augment_historical_with_realtime(self, df: pd.DataFrame, realtime_quote: Any, code: str) -> pd.DataFrame:
        if df is None or df.empty or realtime_quote is None: return df
        price = getattr(realtime_quote, 'price', 0)
        if price <= 0: return df
        df = df.copy()
        last_date = pd.to_datetime(df['date'].max()).date()
        if last_date >= date.today():
            df.loc[df.index[-1], 'close'] = price
        else:
            new_row = {'code': code, 'date': date.today(), 'close': price, 'open': price, 'high': price, 'low': price, 'volume': 0}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        return df

    async def process_single_stock(self, code, skip_analysis=False, single_stock_notify=False, report_type=ReportType.SIMPLE):
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
        
        # 强制并发限制：单个 IP 建议最大并发 2，避免触发封锁
        concurrency_limit = max(1, min(self.max_workers, 2))
        semaphore = asyncio.Semaphore(concurrency_limit)
        
        async def _bounded_process(code, index):
            async with semaphore:
                # 反封锁 3: 任务间随机休眠 (Jitter)
                # 在任务开始前增加随机等待，确保请求在时间轴上离散化
                if index > 0:
                    delay = random.uniform(1.0, 3.0)
                    logger.debug(f"[{code}] 频率控制：等待 {delay:.1f}s 后开始...")
                    await asyncio.sleep(delay)
                
                return await self.process_single_stock(
                    code, dry_run, 
                    getattr(self.config, 'single_stock_notify', False) and send_notification
                )
        
        logger.info(f"开始批量分析，并发限制: {concurrency_limit}，预计最小耗时: {len(stock_codes)*1.5:.1f}s")
        
        # 使用列表推导式配合索引来触发不同的等待时长
        tasks = [_bounded_process(c, i) for i, c in enumerate(stock_codes)]
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)
        
        results = [r for r in results_raw if isinstance(r, AnalysisResult)]
        
        if results and send_notification and not dry_run and not getattr(self.config, 'single_stock_notify', False) and not merge_notification:
            report_text = self.notifier.generate_dashboard_report(results)
            await self.notifier.send(report_text, email_stock_codes=stock_codes)
        return results

    def _resolve_query_source(self, query_source: Optional[str]) -> str:
        return query_source or ("bot" if self.source_message else "system")

    def _build_query_context(self, query_id: Optional[str] = None) -> Dict[str, str]:
        return {"query_id": query_id or "", "query_source": self.query_source or ""}

    @staticmethod
    def _safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
        return value.to_dict() if value and hasattr(value, "to_dict") else None
