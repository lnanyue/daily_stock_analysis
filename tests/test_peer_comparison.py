# -*- coding: utf-8 -*-
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import pandas as pd
from data_provider.fundamental_pipeline import FundamentalPipeline

class TestPeerComparison(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_manager = MagicMock()
        self.pipeline = FundamentalPipeline(self.mock_manager)

    @patch("akshare.stock_zh_a_spot_em")
    async def test_get_peer_comparison_context_success(self, mock_spot):
        """测试成功获取行业对标数据"""
        # 1. 模拟 Tushare 股票列表
        mock_tushare = MagicMock()
        mock_tushare.name = "TushareFetcher"
        mock_tushare.get_stock_list.return_value = pd.DataFrame([
            {"code": "600519", "name": "贵州茅台", "industry": "白酒"},
            {"code": "000858", "name": "五粮液", "industry": "白酒"},
            {"code": "000568", "name": "泸州老窖", "industry": "白酒"},
            {"code": "600809", "name": "山西汾酒", "industry": "白酒"},
        ])
        
        # 模拟 AkshareFetcher 以便进入 Step 2
        mock_ak = MagicMock()
        mock_ak.name = "AkshareFetcher"
        
        self.mock_manager.fetchers = [mock_tushare, mock_ak]

        # 2. 模拟 Akshare 实时行情
        mock_spot.return_value = pd.DataFrame([
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1700.0, "涨跌幅": 1.0, "总市值": 20000e8, "动态市盈率": 30.0, "市净率": 8.0},
            {"代码": "000858", "名称": "五粮液", "最新价": 150.0, "涨跌幅": 0.5, "总市值": 6000e8, "动态市盈率": 20.0, "市净率": 5.0},
            {"代码": "000568", "名称": "泸州老窖", "最新价": 180.0, "涨跌幅": 2.0, "总市值": 3000e8, "动态市盈率": 18.0, "市净率": 4.0},
            {"代码": "600809", "名称": "山西汾酒", "最新价": 200.0, "涨跌幅": -1.0, "总市值": 2500e8, "动态市盈率": 25.0, "市净率": 6.0},
        ])

        # 3. 模拟深度财务指标抓取
        self.pipeline.adapter.get_fundamental_bundle = MagicMock(return_value={
            "status": "ok",
            "growth": {"roe": 25.0, "revenue_yoy": 15.0, "net_profit_yoy": 18.0, "gross_margin": 90.0},
            "source_chain": ["test"]
        })

        # 执行测试
        result = await self.pipeline.get_peer_comparison_context("600519")

        if result["status"] == "failed":
            print(f"\nDEBUG: Peer comparison failed with: {result.get('errors')}")

        self.assertEqual(result["status"], "ok")
        data = result["data"]
        self.assertEqual(data["industry"], "白酒")
        self.assertEqual(len(data["comparison"]), 4) # 目标 1 + 对标 3
        
        # 验证排序（茅台市值最大，排第一）
        self.assertEqual(data["comparison"][0]["code"], "600519")
        self.assertTrue(data["comparison"][0]["is_target"])
        self.assertEqual(data["comparison"][1]["code"], "000858") # 五粮液市值第二

    async def test_get_peer_comparison_industry_not_found(self):
        """测试行业信息缺失时的降级"""
        self.mock_manager.fetchers = []
        result = await self.pipeline.get_peer_comparison_context("999999")
        self.assertEqual(result["status"], "not_supported")
        self.assertIn("Industry information not found", result["errors"][0])

if __name__ == "__main__":
    unittest.main()
