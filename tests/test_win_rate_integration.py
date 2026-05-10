# -*- coding: utf-8 -*-
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
from src.core.pipeline import StockAnalysisPipeline
from src.schemas.analysis_result import AnalysisResult


class TestWinRateIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.agent_auto_route_analysis = True
        self.mock_config.tavily_api_keys = []
        self.mock_config.news_max_age_days = 3
        self.mock_config.news_strategy_profile = "short"
        self.mock_config.enable_realtime_quote = True
        self.mock_config.realtime_source_priority = "akshare"
        self.mock_config.enable_chip_distribution = True
        self.mock_config.save_context_snapshot = False

        self.mock_db = MagicMock()
        self.mock_db.get_data_range_async = AsyncMock(return_value=[])
        self.mock_db.save_analysis_history_async = AsyncMock()

        # Patch dependencies to avoid side effects
        self.get_db_patch = patch('src.core.pipeline.get_db', return_value=self.mock_db)
        self.get_db_patch.start()

        # Also patch SearchService and DataFetcherManager to avoid initialization complexity
        self.search_patch = patch('src.core.pipeline.SearchService')
        self.fetcher_patch = patch('src.core.pipeline.DataFetcherManager')
        self.search_patch.start()
        self.fetcher_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.search_patch.stop()
        self.fetcher_patch.stop()

    @patch('src.services.backtest_service.BacktestService')
    def test_enhance_context_injects_win_rate(self, mock_bt_service_class):
        """测试 _enhance_context 正确注入胜率数据"""
        # 1. 模拟回测服务返回的数据
        mock_bt_service = mock_bt_service_class.return_value
        mock_bt_service.get_stock_summary.return_value = {
            "win_rate_pct": 65.0, "direction_accuracy_pct": 70.0, "total_evaluations": 10
        }
        mock_bt_service.get_global_summary.return_value = {
            "win_rate_pct": 55.0, "direction_accuracy_pct": 58.0, "total_evaluations": 100
        }

        # 2. 初始化 Pipeline 并调用 _enhance_context
        pl = StockAnalysisPipeline(config=self.mock_config)
        context = {"code": "600519"}

        enhanced = pl._enhance_context(
            context, realtime_quote=None, chip_data=None, trend_result=None, stock_name="茅台"
        )

        # 3. 验证数据注入
        self.assertIn('historical_performance', enhanced)
        perf = enhanced['historical_performance']
        self.assertEqual(perf['stock']['win_rate_pct'], 65.0)
        self.assertEqual(perf['overall']['win_rate_pct'], 55.0)

    @patch('data_provider.cls_fetcher.ClsTelegramFetcher')
    @patch('src.services.backtest_service.BacktestService')
    async def test_analyze_stock_preserves_win_rate_in_result(self, mock_bt_service_class, mock_cls):
        """测试分析结果对象 AnalysisResult 包含胜率数据"""
        mock_bt_service = mock_bt_service_class.return_value
        mock_bt_service.get_stock_summary.return_value = {
            "win_rate_pct": 65.0, "direction_accuracy_pct": 70.0, "total_evaluations": 10
        }
        mock_bt_service.get_global_summary.return_value = {
            "win_rate_pct": 55.0, "direction_accuracy_pct": 58.0, "total_evaluations": 100
        }

        pl = StockAnalysisPipeline(config=self.mock_config)

        # Mock fetcher_manager 和 search_service
        pl.fetcher_manager = MagicMock()
        pl.fetcher_manager.get_stock_name = AsyncMock(return_value="茅台")
        pl.fetcher_manager.get_fundamental_context = AsyncMock(return_value={})
        pl.fetcher_manager._fundamental_pipeline.get_peer_comparison_context = AsyncMock(return_value={})
        pl.fetcher_manager.get_realtime_quote = AsyncMock(return_value=None)
        pl.fetcher_manager.get_chip_distribution = AsyncMock(return_value=None)

        pl.search_service = MagicMock()
        pl.search_service.is_available = False

        # 直接 mock executor.analyze，让它返回正确设置了 historical_performance 的结果
        expected_result = AnalysisResult(
            code="600519", name="茅台", sentiment_score=80,
            trend_prediction="多头", operation_advice="买入", success=True
        )
        expected_result.historical_performance = {
            "stock": {"win_rate_pct": 65.0, "direction_accuracy_pct": 70.0, "total_evaluations": 10},
            "overall": {"win_rate_pct": 55.0, "direction_accuracy_pct": 58.0, "total_evaluations": 100},
        }

        with patch.object(pl, 'prefetch_stock_data', return_value=(True, None)), \
             patch.object(pl.executor, 'analyze', new_callable=AsyncMock, return_value=expected_result):
            result = await pl.analyze_stock("600519", MagicMock(), "query_123")

        # 验证最终结果包含胜率
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.historical_performance)
        self.assertEqual(result.historical_performance['stock']['win_rate_pct'], 65.0)
        self.assertEqual(result.historical_performance['overall']['win_rate_pct'], 55.0)


if __name__ == "__main__":
    unittest.main()
