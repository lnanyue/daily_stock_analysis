"""
Integration tests for StockDataCollector + AnalysisExecutor split.
Verifies the interface contract between collector and executor,
and that pipeline.py is pure orchestration.
"""
import unittest
import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from src.core.pipeline_data_collector import (
    StockDataCollector,
    StockDataCollectionResult,
)
from src.core.pipeline_executor import AnalysisExecutor
from src.analyzer import AnalysisResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_quote(price=100.0, **kwargs):
    """Build a realtime quote using a plain object (no MagicMock — avoids attribute fallback)."""
    default_price = price

    class MockQuote:
        price = default_price
        name = kwargs.get("name", "测试股票")
        change_pct = kwargs.get("change_pct", 1.5)
        volume = kwargs.get("volume", 100000)
        volume_ratio = kwargs.get("volume_ratio", 1.2)
        turnover_rate = kwargs.get("turnover_rate", 0.5)
        open_price = kwargs.get("open_price", 99.0)
        high = kwargs.get("high", 101.0)
        low = kwargs.get("low", 98.0)
        amount = kwargs.get("amount", 10000000)
        pre_close = kwargs.get("pre_close", 98.5)
        total_mv = kwargs.get("total_mv", 500000000000)
        circ_mv = kwargs.get("circ_mv", 400000000000)
        pe_ratio = kwargs.get("pe_ratio", 25.0)
        pb_ratio = kwargs.get("pb_ratio", 5.0)
        change_60d = kwargs.get("change_60d", 5.0)

    q = MockQuote()
    q.source = MagicMock()
    q.source.value = "mock_source"
    return q


def _make_mock_chip():
    """Chip data as a plain object (no MagicMock)."""
    class MockChip:
        profit_ratio = 0.45
        concentration_90 = 0.15
        concentration_70 = 0.08
        avg_cost = 120.0
        chip_status = "集中"

    return MockChip()


def _make_mock_trend(signal_score=65):
    """Trend result as a plain object with to_dict() support (no MagicMock)."""

    class MockTrend:
        def __init__(self):
            self.signal_score = signal_score
            self.trend_strength = 60
            self.ma5 = 105.0
            self.ma10 = 102.0
            self.ma20 = 100.0
            self.support = 95.0
            self.resistance = 110.0
            self.volume_ratio_5d = 1.0
            self.rsi_6 = 50
            self.macd_divergence = ""
            self.rsi_divergence = ""

        @property
        def trend_status(self):
            s = MagicMock()
            s.value = "震荡向上"
            return s

        @property
        def buy_signal(self):
            s = MagicMock()
            s.value = "买入"
            s.name = "buy"
            return s

        @property
        def volume_status(self):
            s = MagicMock()
            s.value = "量能正常"
            return s

        @property
        def macd_status(self):
            s = MagicMock()
            s.value = "金叉"
            return s

        @property
        def rsi_status(self):
            s = MagicMock()
            s.value = "中性"
            return s

        def to_dict(self):
            return {
                "signal_score": self.signal_score,
                "trend_strength": self.trend_strength,
                "ma5": self.ma5,
                "ma10": self.ma10,
                "ma20": self.ma20,
                "support": self.support,
                "resistance": self.resistance,
                "volume_ratio_5d": self.volume_ratio_5d,
                "rsi_6": self.rsi_6,
                "macd_divergence": self.macd_divergence,
                "rsi_divergence": self.rsi_divergence,
            }

    return MockTrend()


def _make_dummy_analyzer():
    """A complete dummy GeminiAnalyzer that returns a valid AnalysisResult."""
    a = MagicMock()
    a._make_error_result = MagicMock(
        side_effect=lambda code, name, msg: AnalysisResult(
            code=code, name=name, sentiment_score=50,
            trend_prediction="错误", operation_advice="出错",
            analysis_summary=f"分析失败: {msg}", success=False, error_message=msg,
        )
    )
    a._call_litellm_async = AsyncMock(return_value=(
        _SAMPLE_PARSEABLE_RESPONSE, "test/model", {}
    ))
    a._parse_response = MagicMock(
        return_value=AnalysisResult(
            code="600519", name="测试股票", sentiment_score=70,
            trend_prediction="看多", operation_advice="买入",
            analysis_summary="测试分析结果", success=True,
            current_price=100.0,
            technical_analysis="均线多头排列，MACD金叉",
            fundamental_analysis="业绩稳健增长",
            news_summary="暂无重大消息",
            decision_type="buy",
            support_price=95.0,
            resistance_price=110.0,
            stop_loss_price=93.0,
            target_price=115.0,
        )
    )
    return a


_SAMPLE_PARSEABLE_RESPONSE = """
## 技术面
均线多头排列，MACD金叉，成交量放大。
## 基本面
业绩稳健增长，估值合理。
## 消息面
暂无重大消息。
## 综合评分
70
## 操作建议
买入
"""


# ===========================================================================
# StockDataCollector integration tests
# ===========================================================================

class TestStockDataCollectorIntegration(unittest.IsolatedAsyncioTestCase):
    """Test StockDataCollector as a standalone unit with mocked fetchers."""

    def _make_collector(self, fetcher_overrides=None):
        config = MagicMock()
        config.enable_realtime_quote = True
        config.enable_chip_distribution = True
        config.analysis_mode = "simple"
        config.realtime_source_priority = []
        config.news_max_age_days = 7
        config.news_strategy_profile = "short"
        config.save_context_snapshot = False

        fetcher = MagicMock()
        fetcher.name = "AkshareFetcher"
        fetcher.get_stock_name = AsyncMock(return_value="测试股票")
        fetcher.get_realtime_quote = AsyncMock(return_value=_make_mock_quote())
        fetcher.get_chip_distribution = AsyncMock(return_value=_make_mock_chip())
        fetcher.get_fundamental_context = AsyncMock(return_value={
            "market": "cn",
            "coverage": {"financials": "ok", "boards": "ok"},
            "belong_boards": [{"name": "白酒"}],
        })
        fetcher.get_peer_comparison_context = AsyncMock(return_value={
            "peers": [{"name": "五粮液", "pe": 25.0}]
        })
        fetcher.get_value_metrics_async = AsyncMock(return_value={})
        fetcher.get_lhb_data_async = AsyncMock(return_value=[])
        fetcher.get_research_reports_async = AsyncMock(return_value={})
        fetcher.get_money_flow_async = AsyncMock(return_value={})
        fetcher.get_limit_up_pool_async = AsyncMock(return_value=[])

        if fetcher_overrides:
            for k, v in fetcher_overrides.items():
                setattr(fetcher, k, v)

        fm = MagicMock()
        fm.fetchers = [fetcher]
        fm.get_stock_name = fetcher.get_stock_name
        fm.get_realtime_quote = fetcher.get_realtime_quote
        fm.get_chip_distribution = fetcher.get_chip_distribution
        fm.get_fundamental_context = fetcher.get_fundamental_context
        fm.get_peer_comparison_context = fetcher.get_peer_comparison_context
        fm.get_daily_data = AsyncMock(return_value=(MagicMock(), "mock"))

        search = MagicMock()
        search.is_available = False

        analyzer = MagicMock()

        trend_analyzer = MagicMock()
        trend_analyzer.analyze = MagicMock(return_value=_make_mock_trend())

        return StockDataCollector(
            config=config,
            fetcher_manager=fm,
            search_service=search,
            analyzer=analyzer,
            trend_analyzer=trend_analyzer,
        )

    async def test_collect_returns_collection_result(self):
        """Collector returns a StockDataCollectionResult."""
        collector = self._make_collector()
        result = await collector.collect("600519")
        self.assertIsInstance(result, StockDataCollectionResult)

    async def test_collect_populates_stock_name(self):
        """Stock name is resolved during collection."""
        collector = self._make_collector()
        result = await collector.collect("600519")
        self.assertEqual(result.stock_name, "测试股票")

    async def test_collect_populates_realtime_quote(self):
        """Realtime quote is populated when available."""
        collector = self._make_collector()
        result = await collector.collect("600519")
        self.assertIsNotNone(result.realtime_quote)
        self.assertEqual(result.realtime_quote.price, 100.0)

    async def test_collect_populates_chip_data(self):
        """Chip distribution data is populated."""
        collector = self._make_collector()
        result = await collector.collect("600519")
        self.assertIsNotNone(result.chip_data)
        self.assertEqual(result.chip_data.profit_ratio, 0.45)

    async def test_collect_populates_fundamental_context(self):
        """Fundamental context is populated."""
        collector = self._make_collector()
        result = await collector.collect("600519")
        self.assertIn("market", result.fundamental_context)

    async def test_collect_populates_trend_result(self):
        """Trend analysis is populated from historical data."""
        import pandas as pd
        collector = self._make_collector()

        dates = [date.today() - timedelta(days=i) for i in range(5)]
        hist_df = pd.DataFrame({
            "date": dates,
            "open": 99.0, "close": [100.0 - i for i in range(5)],
            "high": 101.0, "low": 98.0,
            "volume": 100000, "amount": 10000000, "pct_chg": 0.5,
        })
        collector.fetcher_manager.get_daily_data = AsyncMock(return_value=(hist_df, "mock"))

        result = await collector.collect("600519")
        self.assertIsNotNone(result.trend_result)
        self.assertEqual(result.trend_result.signal_score, 65)

    async def test_collect_sets_analysis_mode(self):
        """Analysis mode is read from config."""
        collector = self._make_collector()
        result = await collector.collect("600519")
        self.assertEqual(result.analysis_mode, "simple")

    async def test_collect_sets_analysis_date(self):
        """Analysis date defaults to today."""
        collector = self._make_collector()
        result = await collector.collect("600519")
        self.assertEqual(result.analysis_date, date.today())

    async def test_collect_today_k_populated(self):
        """today_k dict is populated from trend result."""
        import pandas as pd
        collector = self._make_collector()

        hist_df = pd.DataFrame([{
            "date": date.today(),
            "open": 99.0, "close": 100.0, "high": 101.0, "low": 98.0,
            "volume": 100000,
        }])
        collector.fetcher_manager.get_daily_data = AsyncMock(return_value=(hist_df, "mock"))

        result = await collector.collect("600519")
        self.assertIn("close", result.today_k)
        self.assertEqual(result.today_k["close"], 100.0)

    async def test_collect_final_news_assembled(self):
        """final_news includes visual_description from trend."""
        import pandas as pd
        collector = self._make_collector()

        hist_df = pd.DataFrame([{
            "date": date.today(),
            "open": 99.0, "close": 100.0, "high": 101.0, "low": 98.0,
            "volume": 100000,
        }])
        collector.fetcher_manager.get_daily_data = AsyncMock(return_value=(hist_df, "mock"))

        result = await collector.collect("600519")
        self.assertIn("趋势", result.final_news)

    async def test_collect_fallback_on_failure(self):
        """Collector returns partial result on failure (not None)."""
        collector = self._make_collector()
        # Simulate total failure
        collector.fetcher_manager.get_stock_name = AsyncMock(
            side_effect=Exception("API down")
        )
        collector.fetcher_manager.get_realtime_quote = AsyncMock(
            side_effect=Exception("API down")
        )
        result = await collector.collect("600519")
        # Should still return a StockDataCollectionResult
        self.assertIsInstance(result, StockDataCollectionResult)

    async def test_collect_preserves_code_as_name_on_failure(self):
        """When name resolution fails, stock_name falls back to the code."""
        collector = self._make_collector()
        collector.fetcher_manager.get_stock_name = AsyncMock(
            side_effect=Exception("API down")
        )
        collector.fetcher_manager.get_realtime_quote = AsyncMock(return_value=None)
        result = await collector.collect("600519")
        # stock_name starts as the code, and stays as the code when resolution fails
        self.assertEqual(result.stock_name, "600519")


# ===========================================================================
# AnalysisExecutor integration tests
# ===========================================================================

class TestAnalysisExecutorIntegration(unittest.IsolatedAsyncioTestCase):
    """Test AnalysisExecutor as a standalone unit with mocked dependencies."""

    def setUp(self):
        self.config = MagicMock()
        self.config.report_language = "zh"
        self.config.llm_temperature = 0.7
        self.config.validation_retry_enabled = True
        self.config.trader_agent_enabled = False  # avoid TraderAgent complexity
        self.config.save_context_snapshot = False
        self.config.market_review_enabled = False
        self.config.analysis_mode = "simple"

        self.db = MagicMock()
        self.db.get_analysis_history = MagicMock(return_value=[])
        self.db.save_analysis_history_async = AsyncMock()
        self.db.save_prediction_eval = MagicMock()

        self.analyzer = _make_dummy_analyzer()

        self.search = MagicMock()
        self.search.is_available = False

        self.fetcher = MagicMock()
        self.fetcher.get_main_indices = AsyncMock(return_value=None)
        self.fetcher.get_sector_rankings = AsyncMock(return_value=None)

        self.executor = AnalysisExecutor(
            config=self.config,
            db=self.db,
            analyzer=self.analyzer,
            search_service=self.search,
            fetcher_manager=self.fetcher,
        )

    def _make_collected(self, **overrides):
        """Build a standard StockDataCollectionResult."""
        kwargs = dict(
            stock_name="测试股票",
            realtime_quote=_make_mock_quote(),
            chip_data=_make_mock_chip(),
            fundamental_context={"market": "cn", "coverage": {"financials": "ok"}},
            peer_comparison={"peers": []},
            trend_result=_make_mock_trend(),
            today_k={"close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0, "volume": 100000},
            yesterday_k={"close": 98.5},
            final_news="测试新闻资讯",
            analysis_mode="simple",
            analysis_date=date.today(),
        )
        kwargs.update(overrides)
        return StockDataCollectionResult(**kwargs)

    async def test_analyze_accepts_collection_result(self):
        """Executor.analyze() accepts StockDataCollectionResult and returns AnalysisResult."""
        collected = self._make_collected()
        result = await self.executor.analyze("600519", MagicMock(value="simple"), "q-test", collected)
        self.assertIsInstance(result, AnalysisResult)
        self.assertTrue(result.success)

    async def test_analyze_returns_error_on_llm_failure(self):
        """When LLM call fails, executor returns an error result (not None)."""
        self.analyzer._call_litellm_async = AsyncMock(
            side_effect=Exception("LLM unavailable")
        )
        collected = self._make_collected()
        result = await self.executor.analyze("600519", MagicMock(value="simple"), "q-test", collected)
        self.assertIsInstance(result, AnalysisResult)
        self.assertFalse(result.success)
        self.assertIn("LLM", result.error_message or "")

    async def test_analyze_sets_query_id(self):
        """The query_id is propagated to the result."""
        collected = self._make_collected()
        result = await self.executor.analyze("600519", MagicMock(value="simple"), "q-special", collected)
        self.assertEqual(result.query_id, "q-special")

    async def test_analyze_saves_history(self):
        """Analysis history is persisted after successful analysis."""
        collected = self._make_collected()
        await self.executor.analyze("600519", MagicMock(value="simple"), "q-test", collected)
        self.db.save_analysis_history_async.assert_called_once()

    async def test_analyze_persists_prediction_eval(self):
        """Prediction evaluation record is created."""
        collected = self._make_collected()
        await self.executor.analyze("600519", MagicMock(value="simple"), "q-test", collected)
        self.db.save_prediction_eval.assert_called_once()

    async def test_analyze_overrides_current_price(self):
        """Current price is overridden from realtime quote."""
        collected = self._make_collected()
        result = await self.executor.analyze("600519", MagicMock(value="simple"), "q-test", collected)
        self.assertEqual(result.current_price, 100.0)

    async def test_debate_analysis_mode(self):
        """Debate mode triggers DebateAnalyzer path."""
        debate_mock = MagicMock()
        debate_mock.analyze = AsyncMock(return_value=AnalysisResult(
            code="600519", name="测试股票", sentiment_score=65,
            trend_prediction="震荡", operation_advice="持有",
            analysis_summary="辩论分析结果", success=True,
        ))
        # DebateAnalyzer is imported inside executor.analyze(), so patch the module
        with patch("src.agent.debate_analyzer.DebateAnalyzer", return_value=debate_mock):
            collected = self._make_collected(analysis_mode="debate")
            from src.enums import ReportType
            result = await self.executor.analyze("600519", ReportType.SIMPLE, "q-debate", collected)
            self.assertIsInstance(result, AnalysisResult)
            self.assertTrue(result.success)

    async def test_analyze_with_missing_quote(self):
        """Analyze works with missing realtime_quote (fallback to today_k)."""
        collected = self._make_collected(realtime_quote=None)
        result = await self.executor.analyze("600519", MagicMock(value="simple"), "q-test", collected)
        self.assertIsInstance(result, AnalysisResult)

    async def test_analyze_content_integrity_check(self):
        """Content integrity check runs and fills missing fields."""
        config = MagicMock()
        config.report_language = "zh"
        config.llm_temperature = 0.7
        config.validation_retry_enabled = False
        config.trader_agent_enabled = False
        config.save_context_snapshot = False
        config.market_review_enabled = False
        config.analysis_mode = "simple"

        stub_analyzer = MagicMock()
        stub_analyzer._make_error_result = self.analyzer._make_error_result
        # Return parseable but minimal response
        stub_analyzer._call_litellm_async = AsyncMock(return_value=(
            "## 技术面\n测试\n## 综合评分\n50\n## 操作建议\n观望\n", "test/model", {}
        ))
        stub_analyzer._parse_response = MagicMock(
            return_value=AnalysisResult(
                code="600519", name="测试股票", sentiment_score=50,
                trend_prediction="震荡", operation_advice="观望",
                analysis_summary="简单分析", success=True,
                decision_type="hold",
            )
        )

        executor = AnalysisExecutor(
            config=config,
            db=self.db,
            analyzer=stub_analyzer,
            search_service=self.search,
            fetcher_manager=self.fetcher,
        )
        collected = self._make_collected()
        result = await executor.analyze("600519", MagicMock(value="simple"), "q-test", collected)
        # Even without the stub having price, should still return a result
        self.assertIsInstance(result, AnalysisResult)


# ===========================================================================
# Pipeline orchestration purity tests
# ===========================================================================

class TestPipelineOrchestrationPurity(unittest.IsolatedAsyncioTestCase):
    """Verify pipeline.analyze_stock is pure orchestration (delegation only)."""

    def setUp(self):
        self.config = MagicMock()
        self.config.max_workers = 2
        self.config.stock_list = ["600519"]
        self.config.save_context_snapshot = False
        self.config.news_max_age_days = 7
        self.config.enable_realtime_quote = True
        self.config.enable_chip_distribution = True
        self.config.realtime_source_priority = []
        self.config.news_strategy_profile = "short"
        self.config.tavily_api_keys = []
        self.config.agent_mode = False
        self.config.agent_auto_route_analysis = False
        self.config.market_review_enabled = False

    @patch("src.core.pipeline.get_db")
    @patch("src.core.pipeline.SearchService")
    @patch("src.core.pipeline.DataFetcherManager")
    @patch("src.core.pipeline.GeminiAnalyzer")
    @patch("src.core.pipeline.NotificationService")
    @patch("src.core.pipeline.SocialSentimentService")
    @patch("data_provider.cls_fetcher.ClsTelegramFetcher")
    @patch("src.plugins.PluginRegistry")
    @patch("src.plugins.PluginContext")
    async def test_analyze_stock_delegates_to_collector_and_executor(
        self, *_mocks
    ):
        """analyze_stock() calls data_collector.collect() then executor.analyze()."""
        from src.core.pipeline import StockAnalysisPipeline
        from src.enums import ReportType

        pipeline = StockAnalysisPipeline(config=self.config)

        # Mock collector and executor at the instance level
        pipeline.data_collector.collect = AsyncMock(
            return_value=StockDataCollectionResult(stock_name="测试股票")
        )
        pipeline.executor.analyze = AsyncMock(
            return_value=AnalysisResult(
                code="600519", name="测试股票", sentiment_score=70,
                trend_prediction="看多", operation_advice="买入",
                analysis_summary="测试", success=True,
            )
        )

        result = await pipeline.analyze_stock("600519", ReportType.SIMPLE, "q-test")

        pipeline.data_collector.collect.assert_awaited_once_with("600519")
        pipeline.executor.analyze.assert_awaited_once()
        args, _ = pipeline.executor.analyze.call_args
        self.assertEqual(args[0], "600519")  # code
        self.assertEqual(args[1], ReportType.SIMPLE)  # report_type
        self.assertEqual(args[2], "q-test")  # query_id
        self.assertIsInstance(args[3], StockDataCollectionResult)  # collected

        self.assertIsInstance(result, AnalysisResult)

    @patch("src.core.pipeline.get_db")
    @patch("src.core.pipeline.SearchService")
    @patch("src.core.pipeline.DataFetcherManager")
    @patch("src.core.pipeline.GeminiAnalyzer")
    @patch("src.core.pipeline.NotificationService")
    @patch("src.core.pipeline.SocialSentimentService")
    @patch("data_provider.cls_fetcher.ClsTelegramFetcher")
    @patch("src.plugins.PluginRegistry")
    @patch("src.plugins.PluginContext")
    async def test_analyze_stock_handles_collector_exception(
        self, *_mocks
    ):
        """When collector fails, analyze_stock returns None (doesn't crash)."""
        from src.core.pipeline import StockAnalysisPipeline
        from src.enums import ReportType

        pipeline = StockAnalysisPipeline(config=self.config)
        pipeline.data_collector.collect = AsyncMock(
            side_effect=Exception("Collection failed")
        )

        result = await pipeline.analyze_stock("600519", ReportType.SIMPLE, "q-test")
        self.assertIsNone(result)

    @patch("src.core.pipeline.get_db")
    @patch("src.core.pipeline.SearchService")
    @patch("src.core.pipeline.DataFetcherManager")
    @patch("src.core.pipeline.GeminiAnalyzer")
    @patch("src.core.pipeline.NotificationService")
    @patch("src.core.pipeline.SocialSentimentService")
    @patch("data_provider.cls_fetcher.ClsTelegramFetcher")
    @patch("src.plugins.PluginRegistry")
    @patch("src.plugins.PluginContext")
    async def test_analyze_stock_handles_executor_exception(
        self, *_mocks
    ):
        """When executor fails, analyze_stock returns None (doesn't crash)."""
        from src.core.pipeline import StockAnalysisPipeline
        from src.enums import ReportType

        pipeline = StockAnalysisPipeline(config=self.config)
        pipeline.data_collector.collect = AsyncMock(
            return_value=StockDataCollectionResult(stock_name="测试股票")
        )
        pipeline.executor.analyze = AsyncMock(
            side_effect=Exception("Analysis failed")
        )

        result = await pipeline.analyze_stock("600519", ReportType.SIMPLE, "q-test")
        self.assertIsNone(result)

    @patch("src.core.pipeline.get_db")
    @patch("src.core.pipeline.SearchService")
    @patch("src.core.pipeline.DataFetcherManager")
    @patch("src.core.pipeline.GeminiAnalyzer")
    @patch("src.core.pipeline.NotificationService")
    @patch("src.core.pipeline.SocialSentimentService")
    @patch("data_provider.cls_fetcher.ClsTelegramFetcher")
    @patch("src.plugins.PluginRegistry")
    @patch("src.plugins.PluginContext")
    async def test_process_single_stock_delegates_to_analyze_stock(
        self, *_mocks
    ):
        """process_single_stock() calls analyze_stock() (which delegates)."""
        from src.core.pipeline import StockAnalysisPipeline
        from src.enums import ReportType

        pipeline = StockAnalysisPipeline(config=self.config)
        pipeline.fetch_and_save_stock_data = AsyncMock(return_value=(True, None))
        pipeline.analyze_stock = AsyncMock(
            return_value=AnalysisResult(
                code="600519", name="测试股票", sentiment_score=70,
                trend_prediction="看多", operation_advice="买入",
                analysis_summary="测试", success=True,
            )
        )

        result = await pipeline.process_single_stock("600519")
        pipeline.analyze_stock.assert_awaited_once()
        self.assertIsNotNone(result)
