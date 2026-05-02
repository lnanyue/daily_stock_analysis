# -*- coding: utf-8 -*-
"""
StockAnalysisPipeline 核心逻辑单元测试
"""

import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import date

from src.core.pipeline import StockAnalysisPipeline
from src.analyzer import AnalysisResult
from src.enums import ReportType

class TestStockAnalysisPipeline(unittest.IsolatedAsyncioTestCase):
    
    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.max_workers = 2
        self.mock_config.stock_list = ["600519", "000001"]
        self.mock_config.save_context_snapshot = True
        self.mock_config.news_search_enabled = True
        self.mock_config.enable_realtime_quote = True
        self.mock_config.agent_auto_route_analysis = True
        self.mock_config.agent_mode = False
        
        # 使用 patch 避免初始化时执行真实的 I/O
        self.get_db_patch = patch('src.core.pipeline.get_db')
        self.search_service_patch = patch('src.core.pipeline.SearchService')
        self.data_fetcher_patch = patch('src.core.pipeline.DataFetcherManager')
        self.analyzer_patch = patch('src.core.pipeline.GeminiAnalyzer')
        self.notifier_patch = patch('src.core.pipeline.NotificationService')
        self.social_patch = patch('src.core.pipeline.SocialSentimentService')
        self.cls_patch = patch('data_provider.cls_fetcher.ClsTelegramFetcher')
        self.registry_patch = patch('src.plugins.PluginRegistry')
        self.context_patch = patch('src.plugins.PluginContext')
        
        self.mock_db = self.get_db_patch.start().return_value
        self.mock_db.get_data_range_async = AsyncMock(return_value=[])
        self.mock_db.get_data_range = MagicMock(return_value=[])
        self.mock_db.save_analysis_history_async = AsyncMock()
        self.mock_db.has_today_data = MagicMock(return_value=False)
        
        self.mock_search = self.search_service_patch.start().return_value
        self.mock_search.is_available = True
        self.mock_search.search_comprehensive_intel_async = AsyncMock(return_value={})
        self.mock_search.news_window_days = 3
        
        self.mock_fetcher = self.data_fetcher_patch.start().return_value
        self.mock_fetcher.get_stock_name = AsyncMock(return_value="测试股票")
        self.mock_fetcher.get_realtime_quote = AsyncMock(return_value=None)
        self.mock_fetcher.get_chip_distribution = AsyncMock(return_value=None)
        self.mock_fetcher.get_fundamental_context = AsyncMock(return_value={})
        self.mock_fetcher.get_daily_data = AsyncMock(return_value=(None, "test"))
        
        self.mock_analyzer = self.analyzer_patch.start().return_value
        self.mock_analyzer.analyze_async = AsyncMock(return_value=None)
        self.mock_analyzer.is_available.return_value = True
        
        self.mock_notifier = self.notifier_patch.start().return_value
        self.mock_notifier.is_available.return_value = True
        
        # 补充：确保 patch 了 PluginRegistry
        self.registry_patch.start()
        self.context_patch.start()
        self.social_patch.start()
        self.mock_cls = self.cls_patch.start().return_value
        self.mock_cls.get_stock_news = AsyncMock(return_value=[])

    def tearDown(self):
        patch.stopall()

    async def test_pipeline_process_single_stock(self):
        """测试单股处理流程"""
        # 设置模拟对象
        pl = StockAnalysisPipeline(config=self.mock_config)
        pl.fetcher_manager.get_stock_name = AsyncMock(return_value="测试股票")
        pl.fetcher_manager.get_realtime_quote = AsyncMock(return_value=MagicMock(price=100.0, change_pct=1.5, name="测试股票"))
        
        # 模拟内部方法
        pl.fetch_and_save_stock_data = AsyncMock(return_value=(True, None))
        pl.analyze_stock = AsyncMock(return_value=AnalysisResult(
            code="600519", name="测试股票", sentiment_score=80,
            trend_prediction="看多", operation_advice="买入", confidence_level="高", success=True
        ))
        
        result = await pl.process_single_stock("600519")
        
        self.assertIsNotNone(result)
        self.assertEqual(result.code, "600519")
        pl.analyze_stock.assert_called_once()

    async def test_pipeline_run_batch(self):
        """测试批量运行流程及并发限制"""
        pl = StockAnalysisPipeline(config=self.mock_config)
        stock_codes = ["600519", "000001"]
        
        pl.process_single_stock = AsyncMock(side_effect=[
            AnalysisResult(code="600519", name="茅台", sentiment_score=80, trend_prediction="多", operation_advice="买", confidence_level="高"),
            AnalysisResult(code="000001", name="平安", sentiment_score=70, trend_prediction="平", operation_advice="持", confidence_level="中"),
        ])
        
        # 减少随机延迟
        with patch('random.uniform', return_value=0.1), \
             patch('asyncio.sleep', AsyncMock()):
            
            results = await pl.run(stock_codes=stock_codes, send_notification=False)
            
            self.assertEqual(len(results), 2)
            self.assertEqual(pl.process_single_stock.call_count, 2)

    async def test_pipeline_error_handling(self):
        """测试分析异常处理"""
        pl = StockAnalysisPipeline(config=self.mock_config)
        pl.fetcher_manager.get_realtime_quote = AsyncMock(side_effect=Exception("API 故障"))
        
        result = await pl.analyze_stock("600519", ReportType.SIMPLE, "test_query")
        
        self.assertIsNone(result)

    async def test_rate_limiting_logic(self):
        """验证限流和任务间歇逻辑是否被触发"""
        pl = StockAnalysisPipeline(config=self.mock_config)
        stock_codes = ["600519", "000001", "000002"]
        pl.process_single_stock = AsyncMock(return_value=MagicMock(spec=AnalysisResult))
        
        with patch('asyncio.sleep', AsyncMock()) as mock_sleep, \
             patch('random.uniform', return_value=1.5):
            
            await pl.run(stock_codes=stock_codes, send_notification=False)
            
            # index 1, 2 触发休眠，共 2 次
            self.assertEqual(mock_sleep.call_count, 2)
            mock_sleep.assert_called_with(1.5)

    def test_should_auto_route_to_agent(self):
        """测试自动路由到 Agent 的逻辑"""
        pl = StockAnalysisPipeline(config=self.mock_config)
        
        # 1. 未启用自动路由
        self.mock_config.agent_auto_route_analysis = False
        should, reasons = pl._should_auto_route_to_agent(
            code="600519", report_type=ReportType.SIMPLE, enhanced_context={}, 
            final_news="", fundamental_context=None, trend_result=None,
            a_stock_intelligence="", money_flow_intelligence="", guru_insight=""
        )
        self.assertFalse(should)

        # 2. 启用自动路由，但缺少核心数据 (major reason)
        self.mock_config.agent_auto_route_analysis = True
        with patch.object(pl, '_is_agent_runtime_available', return_value=True):
            should, reasons = pl._should_auto_route_to_agent(
                code="600519", report_type=ReportType.SIMPLE, enhanced_context={'today': {}}, 
                final_news="", fundamental_context=None, trend_result=None,
                a_stock_intelligence="", money_flow_intelligence="", guru_insight=""
            )
            # 如果依然失败，打印 reasons 辅助调试 (虽然此处看不到 stdout，但代码更清晰)
            self.assertTrue(should, f"Expected routing due to core_data_gap, but got {reasons}")
            self.assertIn("core_data_gap", reasons)

        # 3. 密集新闻流 (major reason)
        with patch.object(pl, '_is_agent_runtime_available', return_value=True):
            should, reasons = pl._should_auto_route_to_agent(
                code="600519", report_type=ReportType.SIMPLE, 
                enhanced_context={'today': {'close': 100}}, 
                final_news="- 1\n- 2\n- 3\n- 4\n- 5\n- 6", # 6 bullets
                fundamental_context={'coverage': {'financials': 'ok'}}, 
                trend_result=MagicMock(),
                a_stock_intelligence="", money_flow_intelligence="", guru_insight=""
            )
            self.assertTrue(should)
            self.assertIn("dense_news_flow:6", reasons)

    async def test_fetch_and_save_stock_data_breakpoint(self):
        """测试断点续传逻辑"""
        pl = StockAnalysisPipeline(config=self.mock_config)
        pl.db.has_today_data.return_value = True
        
        with patch.object(pl, '_resolve_resume_target_date', return_value=date(2026, 5, 2)):
            # 数据已存在，且不强制刷新
            success, error = await pl.fetch_and_save_stock_data("600519", force_refresh=False)
            self.assertTrue(success)
            pl.fetcher_manager.get_daily_data.assert_not_called()
            
            # 强制刷新
            pl.fetcher_manager.get_daily_data = AsyncMock(return_value=(MagicMock(empty=False), "test_source"))
            success, error = await pl.fetch_and_save_stock_data("600519", force_refresh=True)
            self.assertTrue(success)
            pl.fetcher_manager.get_daily_data.assert_called_once()

    def test_enhance_context_realtime_override(self):
        """测试实时行情覆盖逻辑 (Issue #234)"""
        pl = StockAnalysisPipeline(config=self.mock_config)
        
        context = {
            'today': {'open': 100, 'close': 105, 'high': 110, 'low': 95},
            'yesterday': {'close': 100}
        }
        realtime_quote = MagicMock(
            price=108.0, open_price=102.0, high=110.0, low=101.0, 
            volume=5000, amount=540000, change_pct=8.0
        )
        trend_result = MagicMock(ma5=104.0, ma10=102.0, ma20=100.0)
        
        enhanced = pl._enhance_context(
            context, realtime_quote, None, trend_result, "茅台"
        )
        
        # 验证今日价格被实时价格覆盖
        self.assertEqual(enhanced['today']['close'], 108.0)
        self.assertEqual(enhanced['today']['open'], 102.0)
        # 验证 MA 状态
        self.assertEqual(enhanced['ma_status'], "多头排列 📈")
        # 验证涨跌幅
        self.assertEqual(enhanced['price_change_ratio'], 8.0)

if __name__ == '__main__':
    unittest.main()
