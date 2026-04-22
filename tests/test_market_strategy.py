# -*- coding: utf-8 -*-
"""Tests for market strategy blueprints."""

import unittest

from src.core.market_strategy import get_market_strategy_blueprint
from src.market_analyzer import MarketAnalyzer, MarketOverview


class TestMarketStrategyBlueprint(unittest.TestCase):
    """Validate CN/US strategy blueprint basics."""

    def test_cn_blueprint_contains_action_framework(self):
        blueprint = get_market_strategy_blueprint("cn")
        block = blueprint.to_prompt_block()

        self.assertIn("A股市场三段式复盘策略", block)
        self.assertIn("Action Framework", block)
        self.assertIn("进攻", block)

    def test_us_blueprint_contains_regime_strategy(self):
        blueprint = get_market_strategy_blueprint("us")
        block = blueprint.to_prompt_block()

        self.assertIn("US Market Regime Strategy", block)
        self.assertIn("Risk-on", block)
        self.assertIn("Macro & Flows", block)


class TestMarketAnalyzerStrategyPrompt(unittest.TestCase):
    """Validate strategy section is injected into prompt/report."""

    def test_cn_prompt_contains_strategy_plan_section(self):
        analyzer = MarketAnalyzer(region="cn")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

        self.assertIn("策略计划", prompt)
        self.assertIn("A股市场三段式复盘策略", prompt)
        self.assertIn("你是一位专业的A股市场分析师", prompt)
        self.assertIn("### 一、市场总览", prompt)
        self.assertIn("不要臆测全球市场或跨市场联动", prompt)
        self.assertNotIn("### 一、全球视野", prompt)
        self.assertNotIn("A 股与美股主要指数", prompt)

    def test_us_prompt_contains_strategy_plan_section(self):
        analyzer = MarketAnalyzer(region="us")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

        self.assertIn("Strategy Plan", prompt)
        self.assertIn("US Market Regime Strategy", prompt)
        self.assertIn("你是一位专业的美股市场分析师", prompt)
        self.assertIn("### 二、指数与风格点评", prompt)
        self.assertIn("不要臆测 A 股表现或中美联动", prompt)
        self.assertNotIn("### 一、全球视野", prompt)

    def test_global_prompt_keeps_cross_market_template(self):
        analyzer = MarketAnalyzer(region="global")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

        self.assertIn("你是一位专业的全球市场分析师", prompt)
        self.assertIn("### 一、全球视野", prompt)
        self.assertIn("A 股与美股主要指数", prompt)
        self.assertIn("不要把“无新闻”直接等同于“无法评估全球市场”", prompt)


if __name__ == "__main__":
    unittest.main()
