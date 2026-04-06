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
import anyio
import logging
import time
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
    
    职责：
    1. 管理整个分析流程
    2. 协调数据获取、存储、搜索、分析、通知等模块
    3. 实现并发控制和异常处理
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None,
        source_message: Optional[BotMessage] = None,
        query_id: Optional[str] = None,
        query_source: Optional[str] = None,
        save_context_snapshot: Optional[bool] = None,
        # ★ NEW: factory-based DI (optional, defaults to concrete instantiation)
        analyzer_factory: Optional[Any] = None,  # Callable[[Config], BaseAnalyzer]
        notifier_factory: Optional[Any] = None,  # Callable[[BotMessage], NotificationService]
    ):
        """
        初始化调度器
        
        Args:
            config: 配置对象（可选，默认使用全局配置）
            max_workers: 最大并发线程数（可选，默认从配置读取）
        """
        self.config = config or get_config()
        self.max_workers = max_workers or self.config.max_workers
        self.source_message = source_message
        self.query_id = query_id
        self.query_source = self._resolve_query_source(query_source)
        self.save_context_snapshot = (
            self.config.save_context_snapshot if save_context_snapshot is None else save_context_snapshot
        )
        
        # 初始化各模块
        self.db = get_db()

        # 初始化搜索服务（插件系统需要引用）
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

        # 加载插件系统（数据源 + 分析策略）
        from src.plugins import PluginRegistry, PluginContext

        self.plugins = PluginRegistry()
        plugin_ctx = PluginContext(
            config=self.config,
            db=self.db,
            search_service=self.search_service,
            fetcher_manager=None,
        )
        self.plugins.load(plugin_ctx)

        # 插件 fetchers 优先，内置 fetchers 作为 fallback
        plugin_fetchers = self.plugins.get_enabled_fetchers()
        if plugin_fetchers:
            self.fetcher_manager = DataFetcherManager(fetchers=plugin_fetchers, config=self.config)
        else:
            self.fetcher_manager = DataFetcherManager(config=self.config)

        plugin_ctx.fetcher_manager = self.fetcher_manager
        # 不再单独创建 akshare_fetcher，统一使用 fetcher_manager 获取增强数据
        self.trend_analyzer = StockTrendAnalyzer()  # 技术分析器
        
        # ★ Use factories or fall back to defaults
        if analyzer_factory is not None:
            self.analyzer = analyzer_factory(self.config)
        else:
            self.analyzer = GeminiAnalyzer(config=self.config)

        if notifier_factory is not None:
            self.notifier = notifier_factory(source_message=source_message)
        else:
            self.notifier = NotificationService(source_message=source_message)
        
        logger.info(f"调度器初始化完成，最大并发数: {self.max_workers}")
        logger.info("已启用技术分析引擎（均线/趋势/量价指标）")
        # 打印实时行情/筹码配置状态
        if self.config.enable_realtime_quote:
            logger.info(f"实时行情已启用 (优先级: {self.config.realtime_source_priority})")
        else:
            logger.info("实时行情已禁用，将使用历史收盘价")
        if self.config.enable_chip_distribution:
            logger.info("筹码分布分析已启用")
        else:
            logger.info("筹码分布分析已禁用")
        if self.search_service.is_available:
            logger.info("搜索服务已启用")
        else:
            logger.warning("搜索服务未启用（未配置搜索能力）")

        # 初始化社交舆情服务（仅美股）
        self.social_sentiment_service = SocialSentimentService(
            api_key=self.config.social_sentiment_api_key,
            api_url=self.config.social_sentiment_api_url,
        )
        if self.social_sentiment_service.is_available:
            logger.info("Social sentiment service enabled (Reddit/X/Polymarket, US stocks only)")

    async def fetch_and_save_stock_data(
        self, 
        code: str,
        force_refresh: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        获取并保存单只股票数据 - 异步版
        """
        stock_name = code
        try:
            # 首先获取股票名称 (sync)
            stock_name = self.fetcher_manager.get_stock_name(code)

            today = date.today()
            
            # 断点续传检查
            if not force_refresh and self.db.has_today_data(code, today):
                logger.info(f"{stock_name}({code}) 今日数据已存在，跳过获取（断点续传）")
                return True, None

            # 从数据源获取数据
            logger.info(f"{stock_name}({code}) 开始从数据源获取数据...")
            # ★ wrap sync fetcher call
            df, source_name = await anyio.to_thread.run_sync(self.fetcher_manager.get_daily_data, code, 30)

            if df is None or df.empty:
                return False, "获取数据为空"

            # 保存到数据库
            # ★ wrap sync DB call
            saved_count = await anyio.to_thread.run_sync(self.db.save_daily_data, df, code, source_name)
            logger.info(f"{stock_name}({code}) 数据保存成功（来源: {source_name}，新增 {saved_count} 条）")

            return True, None

        except Exception as e:
            error_msg = f"获取/保存数据失败: {str(e)}"
            logger.error(f"{stock_name}({code}) {error_msg}")
            return False, error_msg
    
    async def analyze_stock(self, code: str, report_type: ReportType, query_id: str) -> Optional[AnalysisResult]:
        """
        分析单只股票（增强版：含量比、换手率、筹码分析、多维度情报）
        """
        try:
            # 获取股票名称 (sync)
            stock_name = self.fetcher_manager.get_stock_name(code)

            # Step 1: 获取实时行情
            realtime_quote = None
            try:
                # ★ Async call
                realtime_quote = await self.fetcher_manager.get_realtime_quote(code)
                if realtime_quote:
                    if realtime_quote.name:
                        stock_name = realtime_quote.name
                    volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
                    turnover_rate = getattr(realtime_quote, 'turnover_rate', None)
                    logger.info(f"{stock_name}({code}) 实时行情: 价格={realtime_quote.price}, "
                              f"量比={volume_ratio}, 换手率={turnover_rate}% "
                              f"(来源: {realtime_quote.source.value if hasattr(realtime_quote, 'source') else 'unknown'})")
                else:
                    logger.info(f"{stock_name}({code}) 实时行情获取失败或已禁用，将使用历史数据进行分析")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 获取实时行情失败: {e}")

            if not stock_name:
                stock_name = f'股票{code}'

            # Step 2: 获取筹码分布
            chip_data = None
            try:
                # ★ wrap sync call
                chip_data = await anyio.to_thread.run_sync(self.fetcher_manager.get_chip_distribution, code)
                if chip_data:
                    logger.info(f"{stock_name}({code}) 筹码分布: 获利比例={chip_data.profit_ratio:.1%}, "
                              f"90%集中度={chip_data.concentration_90:.2%}")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 获取筹码分布失败: {e}")

            # Step 2.5: 基本面能力聚合（必须在 A 股深度情报增强之前，因为电报过滤依赖板块信息）
            fundamental_context = None
            try:
                # ★ wrap sync call
                fundamental_context = await anyio.to_thread.run_sync(
                    self.fetcher_manager.get_fundamental_context,
                    code,
                    getattr(self.config, 'fundamental_stage_timeout_seconds', 1.5)
                )
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 基本面聚合失败: {e}")
                fundamental_context = self.fetcher_manager.build_failed_fundamental_context(code, str(e))

            fundamental_context = self._attach_belong_boards_to_fundamental_context(code, fundamental_context)

            # === A股深度情报增强 (LH榜/研报/电报) ===
            a_stock_intelligence = ""
            if not is_us_stock_code(code) and hasattr(self.fetcher_manager, "_fetchers"):
                # 寻找 AkshareFetcher
                ak_fetcher = next((f for f in self.fetcher_manager._fetchers if f.name == "AkshareFetcher"), None)
                if ak_fetcher:
                    try:
                        # 1. 龙虎榜
                        lhb_list = await anyio.to_thread.run_sync(ak_fetcher.get_lhb_data, code)
                        if lhb_list:
                            a_stock_intelligence += "\n### 龙虎榜动向 (近30日)\n"
                            for item in lhb_list[:3]:
                                a_stock_intelligence += f"- {item['date']}: {item['reason']} (净买额: {item['net_amount']:.2f}万)\n"
                        
                        # 2. 研报预期
                        report_data = await anyio.to_thread.run_sync(ak_fetcher.get_research_reports, code)
                        if report_data and report_data.get('reports'):
                            a_stock_intelligence += "\n### 机构研报观点\n"
                            reports = report_data['reports']
                            for r in reports[:2]:
                                a_stock_intelligence += f"- [{r['org']}] {r['title']} (评级: {r['rating']})\n"
                            f = report_data.get('forecast')
                            if f and f.get('pe'):
                                a_stock_intelligence += f"- 业绩预测({f['year']}): 预测PE={f['pe']}, 预测EPS={f['eps']}\n"

                        # 3. 财联社电报 (根据股票名称和板块关键词过滤)
                        keywords = [stock_name]
                        if fundamental_context and fundamental_context.get('belong_boards'):
                            keywords.extend(fundamental_context['belong_boards'][:2])
                        
                        telegraphs = await anyio.to_thread.run_sync(ak_fetcher.get_latest_telegraph, keywords)
                        if telegraphs:
                            a_stock_intelligence += "\n### 财联社实时快讯\n"
                            for t in telegraphs[:3]:
                                a_stock_intelligence += f"- [{t['time']}] {t['title']}: {t['content'][:150]}...\n"
                        
                        if a_stock_intelligence:
                            logger.info(f"{stock_name}({code}) 成功获取 A股深度情报增强")
                    except Exception as e:
                        logger.debug(f"获取 A股深度情报失败: {e}")

            # === A股资金面与题材增强 (主力/北向/涨停池) ===
            money_flow_intelligence = ""
            if not is_us_stock_code(code) and ak_fetcher:
                try:
                    # 1. 主力资金流向
                    flow = await anyio.to_thread.run_sync(ak_fetcher.get_money_flow, code)
                    if flow and flow.get('main_inflow'):
                        money_flow_intelligence += f"\n### 主力资金流向\n- 今日主力净流入: {flow['main_inflow']:.2f}万 (占比 {flow['main_pct']:.2f}%)\n"
                        money_flow_intelligence += f"- 超大单净流入: {flow['huge_inflow']:.2f}万, 大单净流入: {flow['large_inflow']:.2f}万\n"
                    
                    # 2. 北向资金
                    nb = await anyio.to_thread.run_sync(ak_fetcher.get_northbound_data, code)
                    if nb and nb.get('hold_ratio'):
                        status = "增持" if nb['is_buying'] else "减持"
                        money_flow_intelligence += f"### 北向资金 (外资)\n- 当前持股比例: {nb['hold_ratio']:.2f}%\n- 近期动向: 处于{status}状态\n"

                    # 3. 市场热点题材 (涨停池)
                    themes = await anyio.to_thread.run_sync(ak_fetcher.get_limit_up_pool)
                    if themes:
                        money_flow_intelligence += "### 当日最强题材梯队\n"
                        for t in themes[:3]:
                            money_flow_intelligence += f"- {t['name']} ({t['count']}家涨停): 龙头={', '.join(t['leaders'])}\n"
                    
                    if money_flow_intelligence:
                        logger.info(f"{stock_name}({code}) 成功获取 A股资金题材增强")
                except Exception as e:
                    logger.debug(f"获取 A股资金面数据失败: {e}")

            # === 视觉形态文字化 (K线特征描述) ===
            visual_description = ""
            if trend_result:
                visual_description = f"\n### 视觉K线形态描述\n- 当前趋势: {trend_result.trend_status.value}\n"
                if trend_result.ma_alignment == "bullish":
                    visual_description += "- 形态特征: 均线呈现典型【多头排列】，价格处于上升通道。\n"
                elif trend_result.ma_alignment == "bearish":
                    visual_description += "- 形态特征: 均线呈现典型【空头排列】，空方占据主导。\n"
                
                if trend_result.signal_score > 70:
                    visual_description += "- 动能观察: 量价配合良好，具备向上突破的视觉张力。\n"
                elif trend_result.signal_score < 40:
                    visual_description += "- 动能观察: 价格跌破关键支撑，视觉上呈现破位下行态势。\n"

            use_agent = getattr(self.config, 'agent_mode', False)
            if not use_agent:
                configured_skills = getattr(self.config, 'agent_skills', [])
                if configured_skills and configured_skills != ['all']:
                    use_agent = True
                    logger.info(f"{stock_name}({code}) Auto-enabled agent mode due to configured skills: {configured_skills}")

            # Step 3: 趋势分析
            trend_result: Optional[TrendAnalysisResult] = None
            try:
                end_date = date.today()
                start_date = end_date - timedelta(days=89)
                # ★ wrap sync DB call
                historical_bars = await anyio.to_thread.run_sync(self.db.get_data_range, code, start_date, end_date)
                if historical_bars:
                    df = pd.DataFrame([bar.to_dict() for bar in historical_bars])
                    if self.config.enable_realtime_quote and realtime_quote:
                        df = self._augment_historical_with_realtime(df, realtime_quote, code)
                    # ★ wrap sync analysis
                    trend_result = await anyio.to_thread.run_sync(self.trend_analyzer.analyze, df, code)
                    logger.info(f"{stock_name}({code}) 趋势分析: {trend_result.trend_status.value}, "
                              f"买入信号={trend_result.buy_signal.value}, 评分={trend_result.signal_score}")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 趋势分析失败: {e}")

            if use_agent:
                logger.info(f"{stock_name}({code}) 启用 Agent 模式进行分析")
                # ★ async call
                return await self._analyze_with_agent(
                    code, report_type, query_id, stock_name, realtime_quote, chip_data, fundamental_context, trend_result
                )

            # Step 4: 多维度情报搜索
            news_context = None
            if self.search_service.is_available:
                logger.info(f"{stock_name}({code}) 开始多维度情报搜索...")
                # ★ wrap sync call
                intel_results = await anyio.to_thread.run_sync(
                    self.search_service.search_comprehensive_intel, code, stock_name, 5
                )
                if intel_results:
                    news_context = self.search_service.format_intel_report(intel_results, stock_name)
                    # 保存新闻情报
                    try:
                        query_context = self._build_query_context(query_id=query_id)
                        for dim_name, response in intel_results.items():
                            if response and response.success and response.results:
                                # ★ wrap sync DB call
                                await anyio.to_thread.run_sync(
                                    self.db.save_news_intel,
                                    code, stock_name, dim_name, response.query, response, query_context
                                )
                    except Exception as e:
                        logger.warning(f"{stock_name}({code}) 保存新闻情报失败: {e}")
            else:
                logger.info(f"{stock_name}({code}) 搜索服务不可用，跳过情报搜索")

            # Step 4.5: Social sentiment
            if self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    # ★ wrap sync call
                    social_context = await anyio.to_thread.run_sync(self.social_sentiment_service.get_social_context, code)
                    if social_context:
                        news_context = (news_context + "\n\n" + social_context) if news_context else social_context
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) Social sentiment fetch failed: {e}")

            # Step 4.6: 执行分析策略插件
            plugin_strategy_results = []
            enabled_strategies = self.plugins.get_enabled_strategies()
            if enabled_strategies:
                try:
                    from src.plugins import AnalysisContext as PluginAnalysisContext
                    bar_start = (date.today() - timedelta(days=89)).isoformat()
                    # ★ wrap sync DB call
                    hist = await anyio.to_thread.run_sync(self.db.get_data_range, code, bar_start, date.today().isoformat())
                    if hist:
                        strategy_df = pd.DataFrame([bar.to_dict() for bar in hist])
                        analysis_ctx = PluginAnalysisContext(
                            stock_code=code, price_data=strategy_df,
                            indicators=trend_result or {}, search_results=news_context,
                        )
                        # ★ wrap sync call
                        plugin_strategy_results = await anyio.to_thread.run_sync(self.plugins.execute_strategies, analysis_ctx)
                        plugin_text = "".join([f"\n## {r.title}\n{r.summary}\n" for r in plugin_strategy_results])
                        if plugin_text:
                            news_context = (news_context or "") + "\n\n--- 附加分析 ---" + plugin_text
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) 策略插件执行失败: {e}")

            # Step 5: 获取分析上下文
            # ★ wrap sync DB call
            context = await anyio.to_thread.run_sync(self.db.get_analysis_context, code) or {
                'code': code, 'stock_name': stock_name, 'date': date.today().isoformat(),
                'data_missing': True, 'today': {}, 'yesterday': {}
            }
            
            # Step 6: 增强上下文
            enhanced_context = self._enhance_context(context, realtime_quote, chip_data, trend_result, stock_name, fundamental_context)
            
            # Step 7: AI 分析
            if a_stock_intelligence:
                news_context = (news_context or "") + "\n\n" + a_stock_intelligence
            
            if money_flow_intelligence:
                news_context = (news_context or "") + "\n\n" + money_flow_intelligence
            
            if visual_description:
                news_context = (news_context or "") + "\n\n" + visual_description
            
            # 检查是否启用红蓝对垒模式
            analysis_mode = getattr(self.config, 'analysis_mode', 'simple').lower()
            if analysis_mode == 'debate' and not use_agent:
                from src.agent.debate_analyzer import DebateAnalyzer
                logger.info(f"[{code}] 正在使用红蓝对垒模式进行深度分析...")
                debate_analyzer = DebateAnalyzer(self.config, self.analyzer)
                result = await debate_analyzer.analyze(enhanced_context, news_context)
            else:
                # 默认单模型分析
                # ★ wrap sync LLM call
                result = await anyio.to_thread.run_sync(self.analyzer.analyze, enhanced_context, news_context)

            if result:
                result.query_id = query_id
                realtime_data = enhanced_context.get('realtime', {})
                result.current_price = realtime_data.get('price')
                result.change_pct = realtime_data.get('change_pct')
                if chip_data: fill_chip_structure_if_needed(result, chip_data)
                fill_price_position_if_needed(result, trend_result, realtime_quote)

                # Step 8: 保存历史
                try:
                    snapshot = self._build_context_snapshot(enhanced_context, news_context, realtime_quote, chip_data)
                    # ★ wrap sync DB call
                    await anyio.to_thread.run_sync(
                        self.db.save_analysis_history,
                        result, query_id, report_type.value, news_context, snapshot, self.save_context_snapshot
                    )
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) 保存分析历史失败: {e}")

            return result

        except Exception as e:
            logger.error(f"{stock_name}({code}) 分析失败: {e}")
            logger.exception(f"{stock_name}({code}) 详细错误信息:")
            return None

    async def _analyze_with_agent(
        self, code: str, report_type: ReportType, query_id: str, stock_name: str,
        realtime_quote: Any, chip_data: Optional[ChipDistribution],
        fundamental_context: Optional[Dict[str, Any]] = None,
        trend_result: Optional[TrendAnalysisResult] = None,
    ) -> Optional[AnalysisResult]:
        """Agent 模式异步版"""
        try:
            from src.agent.factory import build_agent_executor
            report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
            executor = build_agent_executor(self.config, getattr(self.config, 'agent_skills', None))

            initial_context = {
                "stock_code": code, "stock_name": stock_name, "report_type": report_type.value,
                "report_language": report_language, "fundamental_context": fundamental_context,
            }
            if realtime_quote: initial_context["realtime_quote"] = self._safe_to_dict(realtime_quote)
            if chip_data: initial_context["chip_distribution"] = self._safe_to_dict(chip_data)
            if trend_result: initial_context["trend_result"] = self._safe_to_dict(trend_result)

            if self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    # ★ wrap sync call
                    sc = await anyio.to_thread.run_sync(self.social_sentiment_service.get_social_context, code)
                    if sc: initial_context["news_context"] = (initial_context.get("news_context", "") + "\n\n" + sc).strip()
                except Exception as e:
                    logger.warning(f"[{code}] Agent social sentiment failed: {e}")

            message = f"Analyze stock {code} ({stock_name})" if report_language == "en" else f"请分析股票 {code} ({stock_name})"
            # ★ wrap sync executor.run
            agent_result = await anyio.to_thread.run_sync(executor.run, message, initial_context)
            result = self._agent_result_to_analysis_result(agent_result, code, stock_name, report_type, query_id)
            
            if result:
                result.query_id = query_id
                if chip_data: fill_chip_structure_if_needed(result, chip_data)
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                
                # 保存历史 (Agent 模式)
                try:
                    # ★ wrap sync DB call
                    await anyio.to_thread.run_sync(
                        self.db.save_analysis_history,
                        result, query_id, report_type.value, None, initial_context, self.save_context_snapshot
                    )
                except Exception as e:
                    logger.warning(f"[{code}] 保存 Agent 分析历史失败: {e}")

            return result
        except Exception as e:
            logger.error(f"[{code}] Agent 分析失败: {e}")
            return None

    def _agent_result_to_analysis_result(self, agent_result, code, stock_name, report_type, query_id) -> AnalysisResult:
        """Helper to convert agent output to standardized AnalysisResult"""
        report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
        result = AnalysisResult(
            code=code, name=stock_name, sentiment_score=50,
            trend_prediction="Unknown" if report_language == "en" else "未知",
            operation_advice="Watch" if report_language == "en" else "观望",
            confidence_level=localize_confidence_level("medium", report_language),
            report_language=report_language, success=agent_result.success,
            error_message=agent_result.error or None,
            data_sources=f"agent:{agent_result.provider}",
            model_used=agent_result.model or None,
        )
        if agent_result.success and agent_result.dashboard:
            dash = agent_result.dashboard
            result.sentiment_score = self._safe_int(dash.get("sentiment_score"), 50)
            result.trend_prediction = dash.get("trend_prediction", result.trend_prediction)
            # Simplistic extraction of advice string
            adv = dash.get("operation_advice")
            if isinstance(adv, dict): 
                from src.agent.protocols import normalize_decision_signal
                ds = str(dash.get("decision_type", "hold")).lower()
                result.operation_advice = "Buy" if "buy" in ds else ("Sell" if "sell" in ds else "Hold")
            else:
                result.operation_advice = str(adv or result.operation_advice)
            result.dashboard = dash.get("dashboard") or dash
        return result

    async def process_single_stock(
        self, code: str, skip_analysis: bool = False,
        single_stock_notify: bool = False, report_type: ReportType = ReportType.SIMPLE,
        analysis_query_id: Optional[str] = None,
    ) -> Optional[AnalysisResult]:
        """单只股票处理链路异步版"""
        logger.info(f"========== 开始处理 {code} ==========")
        try:
            # ★ async call
            success, error = await self.fetch_and_save_stock_data(code)
            if not success: logger.warning(f"[{code}] 数据获取失败: {error}")
            if skip_analysis: return None
            
            effective_query_id = analysis_query_id or self.query_id or uuid.uuid4().hex
            # ★ async call
            result = await self.analyze_stock(code, report_type, query_id=effective_query_id)
            
            if result and single_stock_notify and self.notifier.is_available():
                try:
                    if report_type == ReportType.FULL: report_content = self.notifier.generate_dashboard_report([result])
                    elif report_type == ReportType.BRIEF: report_content = self.notifier.generate_brief_report([result])
                    else: report_content = self.notifier.generate_single_stock_report(result)
                    # ★ async notification
                    if await self.notifier.send(report_content, email_stock_codes=[code]):
                        logger.info(f"[{code}] 单股推送成功")
                except Exception as e:
                    logger.error(f"[{code}] 单股推送失败: {e}")
            return result
        except Exception as e:
            logger.exception(f"[{code}] 处理异常: {e}")
            return None

    async def run(
        self, stock_codes: Optional[List[str]] = None, dry_run: bool = False,
        send_notification: bool = True, merge_notification: bool = False
    ) -> List[AnalysisResult]:
        """异步运行流水线"""
        start_time = time.time()
        if stock_codes is None:
            self.config.refresh_stock_list()
            stock_codes = self.config.stock_list
        if not stock_codes: return []

        logger.info(f"===== 开始分析 {len(stock_codes)} 只股票 =====")
        
        # 预取 (sync)
        if len(stock_codes) >= 5:
            await anyio.to_thread.run_sync(self.fetcher_manager.prefetch_realtime_quotes, stock_codes)
        if not dry_run:
            await anyio.to_thread.run_sync(self.fetcher_manager.prefetch_stock_names, stock_codes, False)

        single_stock_notify = getattr(self.config, 'single_stock_notify', False)
        report_type_str = getattr(self.config, 'report_type', 'simple').lower()
        report_type = ReportType.BRIEF if report_type_str == 'brief' else (ReportType.FULL if report_type_str == 'full' else ReportType.SIMPLE)
        analysis_delay = getattr(self.config, 'analysis_delay', 0)

        # 并发控制
        semaphore = asyncio.Semaphore(self.max_workers)
        async def _bounded_process(code):
            async with semaphore:
                res = await self.process_single_stock(code, dry_run, single_stock_notify and send_notification, report_type)
                if analysis_delay > 0: await asyncio.sleep(analysis_delay)
                return res

        results_raw = await asyncio.gather(*[_bounded_process(c) for c in stock_codes], return_exceptions=True)
        results = [r for r in results_raw if isinstance(r, AnalysisResult)]
        
        # 保存汇总报告 (sync)
        if results and not dry_run:
            report_text = self._generate_aggregate_report(results, report_type)
            await anyio.to_thread.run_sync(self.notifier.save_report_to_file, report_text)

        # 发送汇总通知
        if results and send_notification and not dry_run and not single_stock_notify and not merge_notification:
            if self.notifier.is_available():
                report_text = self._generate_aggregate_report(results, report_type)
                # ★ async send
                await self.notifier.send(report_text, email_stock_codes=stock_codes)

        logger.info(f"===== 分析完成 (耗时: {time.time()-start_time:.1f}s) =====")
        return results

    def run_sync(self, *args, **kwargs) -> List[AnalysisResult]:
        """同步运行包装器"""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                return asyncio.run_coroutine_threadsafe(self.run(*args, **kwargs), loop).result()
        except RuntimeError: pass
        return asyncio.run(self.run(*args, **kwargs))

    def _generate_aggregate_report(self, results: List[AnalysisResult], report_type: ReportType) -> str:
        if report_type == ReportType.BRIEF: return self.notifier.generate_brief_report(results)
        return self.notifier.generate_dashboard_report(results)

    def _attach_belong_boards_to_fundamental_context(self, code: str, fundamental_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Attach board info (sync helper)"""
        enriched = dict(fundamental_context) if isinstance(fundamental_context, dict) else self.fetcher_manager.build_failed_fundamental_context(code, "invalid")
        if "belong_boards" in enriched: return enriched
        try:
            boards = self.fetcher_manager.get_belong_boards(code)
            enriched["belong_boards"] = boards if isinstance(boards, list) else []
        except Exception: enriched["belong_boards"] = []
        return enriched

    def _augment_historical_with_realtime(self, df: pd.DataFrame, realtime_quote: Any, code: str) -> pd.DataFrame:
        """Intraday augmentation (sync helper)"""
        if df is None or df.empty or realtime_quote is None: return df
        price = getattr(realtime_quote, 'price', 0)
        if price <= 0: return df
        df = df.copy()
        # Simplistic append/update logic
        last_date = pd.to_datetime(df['date'].max()).date()
        if last_date >= date.today():
            df.loc[df.index[-1], 'close'] = price
        else:
            new_row = {'code': code, 'date': date.today(), 'close': price, 'open': price, 'high': price, 'low': price, 'volume': 0}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        return df

    def _enhance_context(
        self,
        context: Dict[str, Any],
        realtime_quote,
        chip_data: Optional[ChipDistribution],
        trend_result: Optional[TrendAnalysisResult],
        stock_name: str = "",
        fundamental_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        增强分析上下文
        """
        enhanced = context.copy()
        
        # 添加股票名称
        if stock_name:
            enhanced['stock_name'] = stock_name
        
        # 基本面注入
        if fundamental_context:
            enhanced['fundamental'] = fundamental_context

        # 添加实时行情
        if realtime_quote:
            volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
            enhanced['realtime'] = {
                'name': getattr(realtime_quote, 'name', ''),
                'price': getattr(realtime_quote, 'price', None),
                'change_pct': getattr(realtime_quote, 'change_pct', None),
                'volume_ratio': volume_ratio,
                'volume_ratio_desc': self._describe_volume_ratio(volume_ratio) if volume_ratio is not None else '无数据',
                'turnover_rate': getattr(realtime_quote, 'turnover_rate', None),
                'pe_ratio': getattr(realtime_quote, 'pe_ratio', None),
                'pb_ratio': getattr(realtime_quote, 'pb_ratio', None),
                'total_mv': getattr(realtime_quote, 'total_mv', None),
                'circ_mv': getattr(realtime_quote, 'circ_mv', None),
                'source': getattr(realtime_quote, 'source', None),
            }
            enhanced['realtime'] = {k: v for k, v in enhanced['realtime'].items() if v is not None}
        
        # 添加筹码分布
        if chip_data:
            current_price = getattr(realtime_quote, 'price', 0) if realtime_quote else 0
            enhanced['chip'] = {
                'profit_ratio': chip_data.profit_ratio,
                'avg_cost': chip_data.avg_cost,
                'concentration_90': chip_data.concentration_90,
                'concentration_70': chip_data.concentration_70,
                'chip_status': chip_data.get_chip_status(current_price or 0),
            }
        
        # 添加趋势分析结果
        if trend_result:
            enhanced['trend_analysis'] = {
                'trend_status': trend_result.trend_status.value,
                'ma_alignment': trend_result.ma_alignment,
                'trend_strength': trend_result.trend_strength,
                'buy_signal': trend_result.buy_signal.value,
                'signal_score': trend_result.signal_score,
                'signal_reasons': trend_result.signal_reasons,
                'risk_factors': trend_result.risk_factors,
            }
        
        # 注入检索参数
        if self.search_service:
            enhanced['news_window_days'] = self.search_service.news_window_days

        return enhanced

    def _describe_volume_ratio(self, volume_ratio: float) -> str:
        if volume_ratio < 0.5: return "极度萎缩"
        if volume_ratio < 0.8: return "明显萎缩"
        if volume_ratio < 1.2: return "正常"
        if volume_ratio < 2.0: return "温和放量"
        if volume_ratio < 3.0: return "明显放量"
        return "巨量"

    def _build_context_snapshot(self, enhanced_context, news_content, realtime_quote, chip_data) -> Dict[str, Any]:
        return {
            "enhanced_context": enhanced_context, "news_content": news_content,
            "realtime_quote_raw": self._safe_to_dict(realtime_quote),
            "chip_distribution_raw": self._safe_to_dict(chip_data),
        }

    @staticmethod
    def _safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
        if value is None: return None
        if hasattr(value, "to_dict"): return value.to_dict()
        return None

    def _resolve_query_source(self, query_source: Optional[str]) -> str:
        if query_source: return query_source
        return "bot" if self.source_message else ("web" if self.query_id else "system")

    def _build_query_context(self, query_id: Optional[str] = None) -> Dict[str, str]:
        ctx = {"query_id": query_id or self.query_id or "", "query_source": self.query_source or ""}
        if self.source_message:
            m = self.source_message
            ctx.update({"requester_platform": m.platform, "requester_user_id": m.user_id, "requester_query": m.content})
        return ctx

    @staticmethod
    def _compute_ma_status(close, ma5, ma10, ma20) -> str:
        if close > ma5 > ma10 > ma20 > 0: return "多头排列 📈"
        return "震荡整理 ↔️"

    @staticmethod
    def _is_placeholder_stock_name(name, code) -> bool:
        return not name or name == code or "股票" in name

    @staticmethod
    def _safe_int(value, default=50) -> int:
        try: return int(float(value))
        except: return default
