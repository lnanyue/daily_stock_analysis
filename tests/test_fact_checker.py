# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock
from src.agent.fact_checker import FactChecker
from src.analyzer import AnalysisResult

class TestFactChecker(unittest.TestCase):
    def setUp(self):
        self.context = {
            "realtime": {"price": 100.0, "change_pct": 5.0},
            "ma_status": "多头排列 📈"
        }
        self.checker = FactChecker(self.context)

    def test_verify_pass(self):
        """测试正常数据通过核查"""
        result = MagicMock(spec=AnalysisResult)
        result.current_price = 100.2 # 0.2% 误差，允许
        result.change_pct = 5.05     # 0.05% 误差，允许
        result.trend_prediction = "多头向上"
        
        passed, issues = self.checker.verify(result)
        self.assertTrue(passed)
        self.assertEqual(len(issues), 0)

    def test_verify_price_hallucination(self):
        """测试捕获价格幻觉"""
        result = MagicMock(spec=AnalysisResult)
        result.current_price = 110.0 # 10% 误差
        result.change_pct = 5.0
        result.trend_prediction = "多头"
        
        passed, issues = self.checker.verify(result)
        self.assertFalse(passed)
        self.assertTrue(any("价格幻觉" in issue for issue in issues))

    def test_verify_change_pct_hallucination(self):
        """测试捕获涨跌幅幻觉"""
        result = MagicMock(spec=AnalysisResult)
        result.current_price = 100.0
        result.change_pct = -2.0 # 真实是 5.0
        result.trend_prediction = "多头"
        
        passed, issues = self.checker.verify(result)
        self.assertFalse(passed)
        self.assertTrue(any("涨跌幅幻觉" in issue for issue in issues))

    def test_verify_ma_status_hallucination(self):
        """测试捕获均线状态矛盾"""
        result = MagicMock(spec=AnalysisResult)
        result.current_price = 100.0
        result.change_pct = 5.0
        result.trend_prediction = "空头排列" # 真实是 多头
        
        passed, issues = self.checker.verify(result)
        self.assertFalse(passed)
        self.assertTrue(any("技术面幻觉" in issue for issue in issues))

    def test_correction_prompt_generation(self):
        """测试纠错提示词生成"""
        issues = ["价格幻觉", "涨跌幅幻觉"]
        prompt = self.checker.build_correction_prompt(issues, report_language="zh")
        self.assertIn("事实核查未通过", prompt)
        self.assertIn("重新评估", prompt)
        self.assertIn("价格幻觉", prompt)

if __name__ == "__main__":
    unittest.main()
