# -*- coding: utf-8 -*-
"""Tests for src.core.risk_screener — 排雷筛选硬编码规则。"""

import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from src.core.risk_screener import (
    RiskLevel,
    RiskFlag,
    RiskScreenResult,
    RiskScreener,
)


class MockQuote:
    """Plain class to avoid MagicMock attribute fallback issues."""
    def __init__(self, pe_ratio=None):
        self.pe_ratio = pe_ratio
        self.name = "test"


class RiskScreenerTest(unittest.IsolatedAsyncioTestCase):
    """RiskScreener 主类测试——每个规则独立测试。"""

    def setUp(self):
        mock_config = MagicMock()
        mock_config.risk_screen_debt_threshold = 80.0
        mock_config.risk_screen_pe_max = 100.0
        mock_config.risk_screen_pe_negative_warn = True
        self.screener = RiskScreener(config=mock_config)

    # ── ST 检查 ──────────────────────────────────────────────────

    def test_st_check_by_name_returns_red(self):
        """名称含 ST 应返回 RED。"""
        flag = self.screener._check_st_status("000001", "*ST平安", st_list=[])
        self.assertEqual(flag.level, RiskLevel.RED)
        self.assertIn("ST", flag.rule_name)

    def test_st_check_by_list_code_match_returns_red(self):
        """在 ST 名单中但名称无 ST 标记也应返回 RED。"""
        st_list = [{"code": "000001", "name": "平安银行"}]
        flag = self.screener._check_st_status("000001", "平安银行", st_list=st_list)
        self.assertEqual(flag.level, RiskLevel.RED)

    def test_st_check_by_list_with_dataframe_column_names(self):
        """兼容 AKShare 返回的列名（代码、名称）。"""
        st_list = [{"代码": "000001", "名称": "*ST平安"}]
        flag = self.screener._check_st_status("000001", "平安银行", st_list=st_list)
        self.assertEqual(flag.level, RiskLevel.RED)

    def test_st_check_clean_returns_green(self):
        """非 ST 股票应返回 GREEN。"""
        flag = self.screener._check_st_status("600519", "贵州茅台", st_list=[])
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_st_check_none_st_list_returns_green(self):
        """ST 名单为 None 时，名称无 ST 标记应返回 GREEN。"""
        flag = self.screener._check_st_status("600519", "贵州茅台", st_list=None)
        self.assertEqual(flag.level, RiskLevel.GREEN)

    # ── 财务健康 ──────────────────────────────────────────────────

    def test_financial_health_all_normal_returns_green(self):
        ctx = {
            "growth": {"data": {"revenue_yoy": 15.0}},
            "earnings": {"data": {"roe": 12.5, "net_profit_yoy": 10.0}},
        }
        flag = self.screener._check_financial_health(ctx)
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_financial_health_negative_roe_returns_yellow(self):
        ctx = {
            "growth": {"data": {"revenue_yoy": 5.0}},
            "earnings": {"data": {"roe": -3.2, "net_profit_yoy": 2.0}},
        }
        flag = self.screener._check_financial_health(ctx)
        self.assertEqual(flag.level, RiskLevel.YELLOW)
        self.assertIn("ROE", flag.evidence)

    def test_financial_health_declining_revenue_returns_yellow(self):
        ctx = {
            "growth": {"data": {"revenue_yoy": -5.0}},
            "earnings": {"data": {"roe": 8.0, "net_profit_yoy": 2.0}},
        }
        flag = self.screener._check_financial_health(ctx)
        self.assertEqual(flag.level, RiskLevel.YELLOW)
        self.assertIn("营收", flag.evidence)

    def test_financial_health_declining_profit_returns_yellow(self):
        ctx = {
            "growth": {"data": {"revenue_yoy": 5.0}},
            "earnings": {"data": {"roe": 8.0, "net_profit_yoy": -10.0}},
        }
        flag = self.screener._check_financial_health(ctx)
        self.assertEqual(flag.level, RiskLevel.YELLOW)
        self.assertIn("净利润", flag.evidence)

    def test_financial_health_none_context_returns_green(self):
        flag = self.screener._check_financial_health(None)
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_financial_health_missing_keys_returns_green(self):
        flag = self.screener._check_financial_health({"growth": {}, "earnings": {}})
        self.assertEqual(flag.level, RiskLevel.GREEN)

    # ── 债务风险 ──────────────────────────────────────────────────

    def test_debt_ratio_under_threshold_returns_green(self):
        flag = self.screener._check_debt_risk({"debt_ratio": 45.0})
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_debt_ratio_over_threshold_returns_yellow(self):
        flag = self.screener._check_debt_risk({"debt_ratio": 85.0})
        self.assertEqual(flag.level, RiskLevel.YELLOW)

    def test_debt_ratio_none_returns_green(self):
        flag = self.screener._check_debt_risk({"debt_ratio": None})
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_debt_ratio_empty_metrics_returns_green(self):
        flag = self.screener._check_debt_risk({})
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_debt_ratio_none_value_metrics_returns_green(self):
        flag = self.screener._check_debt_risk(None)
        self.assertEqual(flag.level, RiskLevel.GREEN)

    # ── 估值风险 ──────────────────────────────────────────────────

    def test_pe_normal_returns_green(self):
        quote = MockQuote(pe_ratio=25.0)
        flag = self.screener._check_valuation_risk(quote)
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_pe_negative_returns_yellow(self):
        quote = MockQuote(pe_ratio=-5.0)
        flag = self.screener._check_valuation_risk(quote)
        self.assertEqual(flag.level, RiskLevel.YELLOW)

    def test_pe_too_high_returns_yellow(self):
        quote = MockQuote(pe_ratio=150.0)
        flag = self.screener._check_valuation_risk(quote)
        self.assertEqual(flag.level, RiskLevel.YELLOW)

    def test_pe_none_returns_green(self):
        quote = MockQuote(pe_ratio=None)
        flag = self.screener._check_valuation_risk(quote)
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_pe_none_quote_returns_green(self):
        flag = self.screener._check_valuation_risk(None)
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_pe_quote_as_dict(self):
        """行情数据以 dict 形式传入也应正常工作。"""
        flag = self.screener._check_valuation_risk({"pe_ratio": 25.0})
        self.assertEqual(flag.level, RiskLevel.GREEN)

    def test_pe_quote_as_dict_negative(self):
        flag = self.screener._check_valuation_risk({"pe_ratio": -5.0})
        self.assertEqual(flag.level, RiskLevel.YELLOW)

    # ── 监管风险 ──────────────────────────────────────────────────

    @patch.object(RiskScreener, "_has_search_capability", return_value=False)
    def test_regulatory_search_disabled_returns_green(self, mock_cap):
        """搜索未配置时应返回 GREEN（非跳过）。"""
        result = self.screener._check_regulatory_risk("600000", "浦发银行")
        import asyncio
        flag = asyncio.run(result)
        self.assertEqual(flag.level, RiskLevel.GREEN)

    @patch.object(RiskScreener, "_has_search_capability", return_value=True)
    async def test_regulatory_no_hits_returns_green(self, mock_cap):
        """搜索无命中应返回 GREEN。"""
        mock_search = MagicMock()
        mock_search.search_comprehensive_intel_async = AsyncMock(return_value={})
        self.screener.search_service = mock_search

        flag = await self.screener._check_regulatory_risk("600000", "浦发银行")
        self.assertEqual(flag.level, RiskLevel.GREEN)

    # ── 整体结果计算 ──────────────────────────────────────────────

    def test_red_overrides_yellow(self):
        """RED 标记应覆盖 YELLOW，使整体等级为 RED。"""
        flags = [
            RiskFlag(rule_name="债务", level=RiskLevel.YELLOW, evidence="负债高"),
            RiskFlag(rule_name="ST", level=RiskLevel.RED, evidence="ST"),
        ]
        result = RiskScreenResult(
            code="600000", name="测试", flags=flags,
            overall_level=RiskLevel.RED,
        )
        self.assertTrue(result.is_red)
        self.assertEqual(len(result.red_flags), 1)
        self.assertEqual(len(result.yellow_flags), 1)

    def test_all_green_returns_green(self):
        result = RiskScreenResult(
            code="600519", name="茅台", flags=[], overall_level=RiskLevel.GREEN,
        )
        self.assertFalse(result.is_red)
        self.assertFalse(result.is_yellow)
        self.assertEqual(result.overall_level, RiskLevel.GREEN)

    def test_yellow_only_returns_yellow(self):
        result = RiskScreenResult(
            code="600000", name="测试", flags=[
                RiskFlag(rule_name="债务", level=RiskLevel.YELLOW, evidence="负债高"),
            ],
            overall_level=RiskLevel.YELLOW,
        )
        self.assertFalse(result.is_red)
        self.assertTrue(result.is_yellow)

    # ── 整体 screen() 入口 ───────────────────────────────────────

    async def test_screen_clean_stock_returns_green(self):
        """贵州茅台应全部检查通过。"""
        result = await self.screener.screen(
            code="600519",
            stock_name="贵州茅台",
            fundamental_context={
                "growth": {"data": {"revenue_yoy": 15.0}},
                "earnings": {"data": {"roe": 25.0, "net_profit_yoy": 18.0}},
            },
            realtime_quote=MockQuote(pe_ratio=30.0),
            value_metrics={"debt_ratio": 35.0},
            st_list=[],
        )
        self.assertEqual(result.overall_level, RiskLevel.GREEN)

    async def test_screen_st_stock_returns_red(self):
        """ST 股票应返回 RED。"""
        result = await self.screener.screen(
            code="000001",
            stock_name="*ST平安",
            st_list=[{"code": "000001", "name": "*ST平安"}],
        )
        self.assertEqual(result.overall_level, RiskLevel.RED)
        self.assertEqual(len(result.red_flags), 1)
        self.assertIn("ST", result.red_flags[0].rule_name)

    async def test_screen_multiple_issues_shows_all_flags(self):
        """同时存在财务问题和估值问题应显示多个标记。"""
        result = await self.screener.screen(
            code="000001",
            stock_name="测试",
            fundamental_context={
                "growth": {"data": {"revenue_yoy": -5.0}},
                "earnings": {"data": {"roe": -3.0, "net_profit_yoy": -10.0}},
            },
            realtime_quote=MockQuote(pe_ratio=-10.0),
            value_metrics={"debt_ratio": 85.0},
            st_list=[],
        )
        self.assertEqual(result.overall_level, RiskLevel.YELLOW)
        # 至少应有财务健康 + 债务风险 + 估值风险 三个 YELLOW 标记
        self.assertGreaterEqual(len(result.flags), 3)


if __name__ == "__main__":
    unittest.main()
