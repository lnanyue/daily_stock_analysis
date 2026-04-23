# -*- coding: utf-8 -*-
"""Regression tests for stock-analysis prompt builder."""

import unittest

from src.analyzer.prompt_builder import format_analysis_prompt


class TestPromptBuilder(unittest.TestCase):
    def test_format_analysis_prompt_returns_non_empty_string(self):
        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-04-23",
            "trend_analysis": {
                "trend_status": "盘整",
                "ma_alignment": "neutral",
                "trend_strength": 52,
                "bias_ma5": 1.2,
                "bias_ma10": 0.8,
                "volume_status": "量能正常",
                "volume_trend": "平稳",
                "buy_signal": "观望",
                "signal_score": 52,
                "signal_reasons": ["均线仍有支撑"],
                "risk_factors": ["缺少实时新闻"],
            },
        }

        prompt = format_analysis_prompt(
            context,
            "贵州茅台",
            "",
            report_language="zh",
            news_window_days_config=3,
        )

        self.assertIsInstance(prompt, str)
        self.assertTrue(prompt.strip())
        self.assertIn("贵州茅台", prompt)
        self.assertIn("决策仪表盘分析请求", prompt)

    def test_format_analysis_prompt_includes_fundamental_context_section(self):
        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-04-23",
            "fundamental_context": {
                "earnings": {
                    "data": {
                        "financial_report": {
                            "report_date": "2025-12-31",
                            "revenue": 1000.0,
                            "net_profit_parent": 300.0,
                            "operating_cash_flow": 500.0,
                            "roe": 18.2,
                        },
                        "dividend": {
                            "ttm_cash_dividend_per_share": 30.876,
                            "ttm_dividend_yield_pct": 2.15,
                            "ttm_event_count": 2,
                        },
                    }
                }
            },
        }

        prompt = format_analysis_prompt(
            context,
            "贵州茅台",
            "",
            report_language="zh",
            news_window_days_config=3,
        )

        self.assertIn("财报与分红", prompt)
        self.assertIn("2025-12-31", prompt)
        self.assertIn("30.876", prompt)


if __name__ == "__main__":
    unittest.main()
