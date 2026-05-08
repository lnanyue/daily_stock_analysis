"""
Stock data collector — extracted from StockAnalysisPipeline.analyze_stock.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Callable

import pandas as pd

from src.config import Config
from data_provider import DataFetcherManager
from data_provider.us_index_mapping import is_us_stock_code
from src.search_service import SearchService
from src.stock_analyzer import StockTrendAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class StockDataCollectionResult:
    """Structured output from StockDataCollector.collect()."""
    stock_name: str = ""
    realtime_quote: Any = None
    chip_data: Any = None
    fundamental_context: Dict[str, Any] = field(default_factory=dict)
    peer_comparison: Any = None
    a_stock_intelligence: str = ""
    money_flow_intelligence: str = ""
    guru_insight: str = ""
    trend_result: Any = None
    today_k: Dict[str, Any] = field(default_factory=dict)
    yesterday_k: Dict[str, Any] = field(default_factory=dict)
    news_context: str = ""
    visual_description: str = ""
    final_news: str = ""
    analysis_mode: str = "simple"
    analysis_date: date = field(default_factory=date.today)


class StockDataCollector:
    """
    Collects all pre-analysis data for a single stock.

    Responsibilities:
    - Stock name resolution
    - Real-time quote fetching
    - Chip distribution fetching
    - Fundamental context + peer comparison
    - A-share intel (LHB, research reports, money flow, limit-up pool)
    - Trend analysis + K-line data
    - News / intel search
    - Final news assembly
    """

    def __init__(
        self,
        config: Config,
        fetcher_manager: DataFetcherManager,
        db: Any,
        search_service: SearchService,
        analyzer: Any,
        trend_analyzer: StockTrendAnalyzer,
        augment_historical_with_realtime: Optional[Callable] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ):
        self.config = config
        self.fetcher_manager = fetcher_manager
        self.db = db
        self.search_service = search_service
        self.analyzer = analyzer
        self.trend_analyzer = trend_analyzer
        self._augment_fn = augment_historical_with_realtime
        self._progress_callback = progress_callback

    # ------------------------------------------------------------------
    # Progress delegation
    # ------------------------------------------------------------------
    def _emit_progress(self, progress: int, message: str) -> None:
        if self._progress_callback is not None:
            try:
                self._progress_callback(progress, message)
            except Exception:
                pass

    @staticmethod
    def _enrich_quote_from_history(quote: Any, df: pd.DataFrame) -> None:
        """Fallback: fill missing volume_ratio/turnover_rate from historical data.

        Called after trend collection when the realtime quote source
        (e.g. Sina) does not provide these fields.
        """
        if quote is None or df is None or df.empty:
            return
        if "volume" not in df.columns:
            return

        today_vol = getattr(quote, "volume", None)
        if today_vol and isinstance(today_vol, (int, float)) and today_vol > 0:
            # volume_ratio = 今日量 / 近5日同口径均量
            if getattr(quote, "volume_ratio", None) is None:
                sorted_df = df.sort_values("date", ascending=False)
                hist_vols = pd.to_numeric(sorted_df["volume"], errors="coerce").dropna()
                if len(hist_vols) >= 2:
                    avg_5d = hist_vols.iloc[1:min(6, len(hist_vols))].mean()
                    if avg_5d > 0:
                        quote.volume_ratio = round(float(today_vol) / float(avg_5d), 2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def collect(self, code: str) -> StockDataCollectionResult:
        """Collect all data for a stock and return a structured result."""
        result = StockDataCollectionResult()
        result.stock_name = code

        try:
            self._emit_progress(18, f"{code}：正在获取行情与筹码数据")
            await self._resolve_stock_name(code, result)
            await self._collect_realtime_quote(code, result)
            await self._collect_chip_data(code, result)
            self._emit_progress(32, f"{result.stock_name}：正在聚合基本面与趋势数据")
            await self._collect_fundamental_context(code, result)
            await self._collect_a_stock_intel(code, result)
            await self._collect_trend_and_kline(code, result)
            await self._collect_news(code, result)
            self._assemble_final_news(result)
            result.analysis_mode = getattr(self.config, "analysis_mode", "simple").lower()
        except Exception as exc:
            logger.error("[%s] Data collection failed: %s", code, exc, exc_info=True)
            # Return partial result — caller decides whether to proceed

        return result

    # ------------------------------------------------------------------
    # Step 1: Stock name
    # ------------------------------------------------------------------
    async def _resolve_stock_name(self, code: str, result: StockDataCollectionResult) -> None:
        try:
            name = await self.fetcher_manager.get_stock_name(code, allow_realtime=False)
            if name:
                result.stock_name = name
        except Exception as exc:
            logger.warning("[%s] Stock name resolution failed: %s", code, exc)

    # ------------------------------------------------------------------
    # Step 2: Real-time quote
    # ------------------------------------------------------------------
    async def _collect_realtime_quote(self, code: str, result: StockDataCollectionResult) -> None:
        try:
            if self.config.enable_realtime_quote:
                quote = await self.fetcher_manager.get_realtime_quote(code, log_final_failure=False)
                if quote:
                    result.realtime_quote = quote
                    if quote.name:
                        result.stock_name = quote.name
                    volume_ratio = getattr(quote, 'volume_ratio', None)
                    turnover_rate = getattr(quote, 'turnover_rate', None)
                    logger.info(
                        f"{result.stock_name}({code}) 实时行情: 价格={quote.price}, "
                        f"量比={volume_ratio}, 换手率={turnover_rate}% "
                        f"(来源: {quote.source.value if hasattr(quote, 'source') else 'unknown'})"
                    )
                else:
                    logger.warning(f"{result.stock_name}({code}) 所有实时行情数据源均不可用，已降级为历史收盘价继续分析")
            else:
                logger.info(f"{result.stock_name}({code}) 实时行情已禁用，使用历史收盘价继续分析")
        except Exception as e:
            logger.warning(f"{result.stock_name}({code}) 实时行情链路异常，已降级为历史收盘价继续分析: {e}")

        if not result.stock_name:
            result.stock_name = f'股票{code}'

    # ------------------------------------------------------------------
    # Step 3: Chip distribution
    # ------------------------------------------------------------------
    async def _collect_chip_data(self, code: str, result: StockDataCollectionResult) -> None:
        try:
            chip = await self.fetcher_manager.get_chip_distribution(code)
            if chip:
                result.chip_data = chip
                logger.info(
                    f"{result.stock_name}({code}) 筹码分布: 获利比例={chip.profit_ratio:.1%}, "
                    f"90%集中度={chip.concentration_90:.2%}"
                )
            else:
                logger.debug(f"{result.stock_name}({code}) 筹码分布获取失败或已禁用")
        except Exception as e:
            logger.warning(f"{result.stock_name}({code}) 获取筹码分布失败: {e}")

    # ------------------------------------------------------------------
    # Step 4: Fundamental context + peer comparison
    # ------------------------------------------------------------------
    async def _collect_fundamental_context(self, code: str, result: StockDataCollectionResult) -> None:
        try:
            ctx = await self.fetcher_manager.get_fundamental_context(code)
            if ctx:
                result.fundamental_context = ctx
            result.peer_comparison = await self.fetcher_manager.get_peer_comparison_context(code)
        except Exception as e:
            logger.warning("%s(%s) 获取基本面/对标数据失败: %s", result.stock_name, code, e)

        if result.realtime_quote and getattr(result.realtime_quote, 'name', None):
            result.stock_name = result.realtime_quote.name

    # ------------------------------------------------------------------
    # Step 5: A-share intel (LHB, research, money flow, limit-up)
    # ------------------------------------------------------------------
    async def _collect_a_stock_intel(self, code: str, result: StockDataCollectionResult) -> None:
        if is_us_stock_code(code):
            return
        ak_fetcher = next(
            (f for f in self.fetcher_manager.fetchers if f.name == "AkshareFetcher"),
            None,
        )
        if not ak_fetcher:
            return

        tasks = [
            ak_fetcher.get_value_metrics_async(code),
            ak_fetcher.get_lhb_data_async(code),
            ak_fetcher.get_research_reports_async(code),
            ak_fetcher.get_money_flow_async(code),
            ak_fetcher.get_limit_up_pool_async(),
        ]
        intel_results = await asyncio.gather(*tasks, return_exceptions=True)

        if not isinstance(intel_results[0], Exception) and intel_results[0]:
            result.fundamental_context['quality_metrics'] = intel_results[0]
        if not isinstance(intel_results[1], Exception) and intel_results[1]:
            result.a_stock_intelligence += "\n### 龙虎榜动向\n" + "\n".join(
                f"- {i['date']}: {i['reason']} (净买: {i['net_amount']:.2f}万)"
                for i in intel_results[1][:3]
            )
        if not isinstance(intel_results[2], Exception) and intel_results[2] and intel_results[2].get('reports'):
            result.a_stock_intelligence += "\n### 研报观点\n" + "\n".join(
                f"- [{r['org']}] {r['title']}"
                for r in intel_results[2]['reports'][:2]
            )
        if not isinstance(intel_results[3], Exception) and intel_results[3] and intel_results[3].get('main_inflow'):
            result.money_flow_intelligence += f"\n### 资金面\n- 主力净流入: {intel_results[3]['main_inflow']:.2f}万\n"
        if not isinstance(intel_results[4], Exception) and intel_results[4]:
            result.money_flow_intelligence += "### 题材热度\n" + "\n".join(
                f"- {t['name']} ({t['count']}涨停)" for t in intel_results[4][:2]
            )

        from src.agent.guru_analyzer import GuruAnalyzer
        guru = GuruAnalyzer(self.analyzer)
        result.guru_insight = await guru.analyze({
            'stock_name': result.stock_name, 'code': code,
            'fundamental': result.fundamental_context,
            'money_flow': result.money_flow_intelligence,
        }, result.a_stock_intelligence)

    # ------------------------------------------------------------------
    # Step 6: Trend + K-line
    # ------------------------------------------------------------------
    async def _collect_trend_and_kline(self, code: str, result: StockDataCollectionResult) -> None:
        end_date = result.analysis_date
        hist = await self.db.get_data_range_async(code, end_date - timedelta(days=90), end_date)
        if not hist:
            return

        df = pd.DataFrame([bar.to_dict() for bar in hist])
        if self.config.enable_realtime_quote and result.realtime_quote and self._augment_fn is not None:
            df = self._augment_fn(df, result.realtime_quote, code)

        trend_result = await asyncio.to_thread(self.trend_analyzer.analyze, df, code)
        result.trend_result = trend_result
        result.visual_description = f"\n### 视觉形态描述\n- 趋势: {trend_result.trend_status.value}\n"

        # 补全数据源缺失的字段（如 Sina 不提供量比/换手率）
        if result.realtime_quote is not None:
            self._enrich_quote_from_history(result.realtime_quote, df)

        sorted_df = df.sort_values('date', ascending=False)
        if len(sorted_df) > 0:
            result.today_k = sorted_df.iloc[0].to_dict()
            if isinstance(result.today_k.get('date'), (datetime, date)):
                result.today_k['date'] = result.today_k['date'].isoformat()
        if len(sorted_df) > 1:
            result.yesterday_k = sorted_df.iloc[1].to_dict()
            if isinstance(result.yesterday_k.get('date'), (datetime, date)):
                result.yesterday_k['date'] = result.yesterday_k['date'].isoformat()

    # ------------------------------------------------------------------
    # Step 7: News / intel
    # ------------------------------------------------------------------
    async def _collect_news(self, code: str, result: StockDataCollectionResult) -> None:
        if not self.search_service.is_available:
            return
        from data_provider.cls_fetcher import ClsTelegramFetcher
        cls_fetcher = ClsTelegramFetcher()

        # Schedule each task independently so gather construction errors
        # (e.g. non-awaitable returned by a mock) don't leak coroutines.
        import inspect as _inspect
        tasks = []
        try:
            coro = self.search_service.search_comprehensive_intel_async(code, result.stock_name, 5)
            if _inspect.isawaitable(coro):
                tasks.append(asyncio.create_task(coro))
        except Exception as exc:
            logger.warning("[%s] search_comprehensive_intel_async failed: %s", code, exc)

        try:
            coro = cls_fetcher.get_stock_news(result.stock_name, code)
            if _inspect.isawaitable(coro):
                tasks.append(asyncio.create_task(coro))
        except Exception as exc:
            logger.warning("[%s] get_stock_news failed: %s", code, exc)

        if not tasks:
            return

        intel_raw = await asyncio.gather(*tasks, return_exceptions=True)
        if len(intel_raw) > 0 and not isinstance(intel_raw[0], Exception) and intel_raw[0]:
            result.news_context = self.search_service.format_intel_report(intel_raw[0], result.stock_name)
        if len(intel_raw) > 1 and not isinstance(intel_raw[1], Exception) and intel_raw[1]:
            result.news_context += "\n\n### ⚡ 财联社电报\n" + "\n".join(
                f"- {n['content'][:100]}" for n in intel_raw[1][:5]
            )

    # ------------------------------------------------------------------
    # Assembly: combine intel sections into final_news
    # ------------------------------------------------------------------
    def _assemble_final_news(self, result: StockDataCollectionResult) -> None:
        parts = [result.news_context or ""]
        if result.a_stock_intelligence:
            parts.append("\n\n" + result.a_stock_intelligence)
        if result.money_flow_intelligence:
            parts.append("\n\n" + result.money_flow_intelligence)
        if result.guru_insight:
            parts.append("\n\n### 🎓 大师灵魂审视\n" + result.guru_insight)
        if result.visual_description:
            parts.append("\n\n" + result.visual_description)
        result.final_news = "".join(parts)
