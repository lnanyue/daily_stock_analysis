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
        
        # 使用 patch 避免初始化时执行真实的 I/O
        self.get_db_patch = patch('src.core.pipeline.get_db')
        self.search_service_patch = patch('src.core.pipeline.SearchService')
        self.data_fetcher_patch = patch('src.core.pipeline.DataFetcherManager')
        self.analyzer_patch = patch('src.core.pipeline.GeminiAnalyzer')
        self.notifier_patch = patch('src.core.pipeline.NotificationService')
        self.social_patch = patch('src.core.pipeline.SocialSentimentService')
        self.registry_patch = patch('src.plugins.PluginRegistry')
        self.context_patch = patch('src.plugins.PluginContext')
        
        self.mock_db = self.get_db_patch.start()
        self.mock_search = self.search_service_patch.start()
        self.mock_fetcher = self.data_fetcher_patch.start()
        self.mock_analyzer = self.analyzer_patch.start()
        self.mock_notifier = self.notifier_patch.start()
        
        # 补充：确保 patch 了 PluginRegistry
        self.registry_patch.start()
        self.context_patch.start()
        self.social_patch.start()

    def tearDown(self):
        patch.stopall()

    async def test_pipeline_process_single_stock(self):
        """测试单股处理流程"""
        # 设置模拟对象
        pl = StockAnalysisPipeline(config=self.mock_config)
        pl.fetcher_manager.get_stock_name.return_value = "测试股票"
        pl.fetcher_manager.get_realtime_quote = AsyncMock(return_value=MagicMock(price=100.0, change_pct=1.5, name="测试股票"))
        
        # 模拟内部方法
        pl.fetch_and_save_stock_data = AsyncMock(return_value=(True, None))
        pl.analyze_stock = AsyncMock(return_value=AnalysisResult(
            code="600519", name="测试股票", sentiment_score=80,
            trend_prediction="看多", operation_advice="买入", confidence_level="高"
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

if __name__ == '__main__':
    unittest.main()
