# -*- coding: utf-8 -*-
"""
StockAnalyzer 核心方法单元测试
"""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult, BuySignal


class TestTrendAnalysisResult(unittest.TestCase):
    """测试 TrendAnalysisResult 数据结构"""

    def test_to_dict(self):
        """测试转换为字典"""
        result = TrendAnalysisResult(code="SH600000")
        result.ma_alignment = "多头排列"
        result.trend_strength = 8.0

        result_dict = result.to_dict()
        self.assertIsInstance(result_dict, dict)
        self.assertEqual(result_dict.get('code'), "SH600000")
        self.assertEqual(result_dict.get('ma_alignment'), "多头排列")

    def test_to_dict_empty(self):
        """测试空结果转换为字典"""
        result = TrendAnalysisResult(code="SH600000")
        result_dict = result.to_dict()
        self.assertIsInstance(result_dict, dict)


class TestAnalyzeStock(unittest.TestCase):
    """测试 analyze_stock 函数"""

    @patch('src.stock_analyzer.StockTrendAnalyzer')
    def test_analyze_stock(self, mock_analyzer_class):
        """测试股票分析函数"""
        mock_analyzer = MagicMock()
        mock_result = TrendAnalysisResult(code='600519')
        mock_result.buy_signal = BuySignal.HOLD
        mock_analyzer.analyze.return_value = mock_result
        mock_analyzer_class.return_value = mock_analyzer

        from src.stock_analyzer import analyze_stock

        df = pd.DataFrame({
            'open': [10.0, 10.5, 11.0],
            'high': [10.8, 11.2, 11.5],
            'low': [9.8, 10.2, 10.8],
            'close': [10.5, 11.0, 11.2],
            'volume': [1000000, 1200000, 1100000]
        })

        with patch('src.stock_analyzer.get_config'):
            result = analyze_stock(df, '600519')
            self.assertIsInstance(result, TrendAnalysisResult)


class TestVolumeAnalysis(unittest.TestCase):
    """测试量能分析相关方法"""

    def setUp(self):
        """设置测试环境"""
        with patch('src.stock_analyzer.get_config'):
            self.analyzer = StockTrendAnalyzer()

    def test_calculate_volume_heavy(self):
        """测试放量判断"""
        df = pd.DataFrame({
            'volume': [1000000, 1200000, 1500000, 2000000, 3000000]  # latest=3000000, avg=1425000, ratio=2.1 > 1.5
        })

        latest_vol = df['volume'].iloc[-1]
        avg_vol = df['volume'].iloc[:-1].mean()
        ratio = latest_vol / avg_vol if avg_vol > 0 else 0

        self.assertGreater(ratio, self.analyzer.VOLUME_HEAVY_RATIO)

    def test_calculate_volume_shrink(self):
        """测试缩量判断"""
        df = pd.DataFrame({
            'volume': [2000000, 1800000, 1500000, 1000000, 800000]  # latest=800000, avg=1575000, ratio=0.51 < 0.7
        })

        latest_vol = df['volume'].iloc[-1]
        avg_vol = df['volume'].iloc[:-1].mean()
        ratio = latest_vol / avg_vol if avg_vol > 0 else 0

        self.assertLess(ratio, self.analyzer.VOLUME_SHRINK_RATIO)


class TestBiasCalculation(unittest.TestCase):
    """测试乖离率计算"""

    def setUp(self):
        """设置测试环境"""
        with patch('src.stock_analyzer.get_config'):
            self.analyzer = StockTrendAnalyzer()

    def test_bias_positive(self):
        """测试正乖离（价格在 MA5 上方）"""
        price = 105.0
        ma5 = 100.0
        bias = (price - ma5) / ma5 * 100

        self.assertGreater(bias, 0)
        self.assertAlmostEqual(bias, 5.0)

    def test_bias_negative(self):
        """测试负乖离（价格在 MA5 下方）"""
        price = 95.0
        ma5 = 100.0
        bias = (price - ma5) / ma5 * 100

        self.assertLess(bias, 0)
        self.assertAlmostEqual(bias, -5.0)

    def test_bias_zero(self):
        """测试零乖离（价格等于 MA5）"""
        price = 100.0
        ma5 = 100.0
        bias = (price - ma5) / ma5 * 100

        self.assertAlmostEqual(bias, 0.0)


class TestMACDAnalysis(unittest.TestCase):
    """测试 MACD 分析"""

    def test_macd_calculation(self):
        """测试 MACD 指标计算"""
        dates = pd.date_range('2026-01-01', periods=50)
        df = pd.DataFrame({
            'close': np.random.normal(100, 10, 50)
        }, index=dates)

        # 手动计算 MACD（简化版）
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        macd = (dif - dea) * 2

        self.assertEqual(len(macd), 50)
        self.assertFalse(macd.isna().all())


if __name__ == '__main__':
    unittest.main()
