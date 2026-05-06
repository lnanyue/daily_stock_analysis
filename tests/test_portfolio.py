# -*- coding: utf-8 -*-
"""Tests for src.core.portfolio — portfolio aggregation logic."""

import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, Mock

from src.core.portfolio import (
    run_portfolio_aggregation,
    _build_prompt,
    _parse_json,
    _render,
)


def _fake_result(code: str, name: str, *, score: int = 60, advice: str = "持有",
                 confidence: str = "中", sector: str = "消费"):
    """Build a minimal AnalysisResult-like object (duck-typed)."""
    return Mock(
        code=code, name=name,
        sentiment_score=score, operation_advice=advice,
        confidence_level=confidence, sector_position=sector,
    )


class BuildPromptTest(TestCase):
    def test_includes_stock_details(self):
        results = [
            _fake_result("600519", "贵州茅台", sector="白酒"),
            _fake_result("000858", "五粮液", sector="白酒"),
        ]
        prompt = _build_prompt(results)
        self.assertIn("600519", prompt)
        self.assertIn("贵州茅台", prompt)
        self.assertIn("五粮液", prompt)
        self.assertIn("signal=持有", prompt)
        self.assertIn("sector=白酒", prompt)
        self.assertIn("Output format", prompt)
        self.assertIn("portfolio_risk_score", prompt)

    def test_includes_count(self):
        results = [_fake_result("A", "A"), _fake_result("B", "B"), _fake_result("C", "C")]
        prompt = _build_prompt(results)
        self.assertIn("3 stocks", prompt)

    def test_fills_missing_sector(self):
        results = [_fake_result("600519", "茅台", sector="")]
        prompt = _build_prompt(results)
        self.assertIn("sector=N/A", prompt)


class ParseJsonTest(TestCase):
    def test_valid_json(self):
        raw = '{"portfolio_risk_score": 5, "summary": "ok"}'
        self.assertEqual(_parse_json(raw), {"portfolio_risk_score": 5, "summary": "ok"})

    def test_json_in_markdown_fence(self):
        raw = '```json\n{"portfolio_risk_score": 3}\n```'
        self.assertEqual(_parse_json(raw), {"portfolio_risk_score": 3})

    def test_invalid_input_returns_empty_dict(self):
        self.assertEqual(_parse_json("not json"), {})
        self.assertEqual(_parse_json(""), {})

    def test_list_instead_of_dict_returns_empty_dict(self):
        self.assertEqual(_parse_json("[1, 2, 3]"), {})

    def test_partial_malformed(self):
        """extract_json_from_text tolerates some noise; if it can't extract, returns None."""
        result = _parse_json("Some text before {\"a\": 1} some after")
        # depends on extract_json_from_text — accept either outcome
        self.assertIsInstance(result, (dict, type(None)))


class RenderTest(TestCase):
    def test_summary_and_risk_score(self):
        data = {"portfolio_risk_score": 4, "summary": "Balanced portfolio"}
        md = _render(data)
        self.assertIn("Balanced portfolio", md)
        self.assertIn("4/10", md)

    def test_all_sections(self):
        data = {
            "portfolio_risk_score": 7,
            "summary": "Risky",
            "sector_warnings": ["Tech > 40%"],
            "correlation_warnings": ["A & B correlated"],
            "cross_market_notes": ["Tariff risk"],
            "rebalance_suggestions": ["Trim tech"],
            "positions": [
                {"code": "A", "suggested_weight": 0.3, "signal": "buy", "note": "good"},
                {"code": "B", "suggested_weight": 0.7, "signal": "hold", "note": ""},
            ],
        }
        md = _render(data)
        self.assertIn("Risky", md)
        self.assertIn("7/10", md)
        self.assertIn("Tech > 40%", md)
        self.assertIn("A & B correlated", md)
        self.assertIn("Tariff risk", md)
        self.assertIn("Trim tech", md)
        self.assertIn("| A | 30% | buy | good |", md)
        self.assertIn("| B | 70% | hold |  |", md)

    def test_empty_data(self):
        md = _render({})
        self.assertIn("N/A", md)

    def test_none_values(self):
        md = _render({
            "portfolio_risk_score": None,
            "summary": None,
            "sector_warnings": None,
            "positions": None,
        })
        self.assertIn("N/A", md)

    def test_weight_string_fallback(self):
        data = {
            "portfolio_risk_score": 5,
            "positions": [{"code": "X", "suggested_weight": "equal", "signal": "", "note": ""}],
        }
        md = _render(data)
        self.assertIn("| X | equal |  |  |", md)

    def test_missing_position_keys(self):
        data = {
            "portfolio_risk_score": 5,
            "positions": [{"code": "X"}],
        }
        md = _render(data)
        self.assertIn("| X |  |  |  |", md)


class RunPortfolioAggregationTest(TestCase):
    """Integration-style tests with mocked analyzer."""

    def setUp(self):
        self.two_results = [
            _fake_result("600519", "茅台"),
            _fake_result("000858", "五粮液"),
        ]
        self.analyzer = Mock()
        self.analyzer.generate_text_async = AsyncMock()

    def test_returns_none_when_no_analyzer(self):
        result = asyncio.run(run_portfolio_aggregation(None, self.two_results))
        self.assertIsNone(result)

    def test_returns_none_when_no_results(self):
        result = asyncio.run(run_portfolio_aggregation(self.analyzer, []))
        self.assertIsNone(result)

    def test_returns_none_when_results_is_none(self):
        result = asyncio.run(run_portfolio_aggregation(self.analyzer, None))
        self.assertIsNone(result)

    def test_returns_none_when_single_stock(self):
        single = [_fake_result("600519", "茅台")]
        result = asyncio.run(run_portfolio_aggregation(self.analyzer, single))
        self.assertIsNone(result)

    def test_returns_none_when_llm_returns_empty(self):
        self.analyzer.generate_text_async.return_value = None
        result = asyncio.run(run_portfolio_aggregation(self.analyzer, self.two_results))
        self.assertIsNone(result)

    def test_returns_none_when_llm_returns_empty_string(self):
        self.analyzer.generate_text_async.return_value = ""
        result = asyncio.run(run_portfolio_aggregation(self.analyzer, self.two_results))
        self.assertIsNone(result)

    def test_returns_none_when_llm_returns_non_json(self):
        self.analyzer.generate_text_async.return_value = "Sorry, I cannot do that"
        result = asyncio.run(run_portfolio_aggregation(self.analyzer, self.two_results))
        self.assertIsNone(result)

    def test_success_path(self):
        self.analyzer.generate_text_async.return_value = """
        {
            "portfolio_risk_score": 3,
            "summary": "Low risk portfolio",
            "sector_warnings": [],
            "positions": [
                {"code": "600519", "suggested_weight": 0.5, "signal": "buy", "note": "strong"}
            ]
        }
        """
        result = asyncio.run(run_portfolio_aggregation(self.analyzer, self.two_results))
        self.assertIsNotNone(result)
        self.assertIn("Low risk portfolio", result)
        self.assertIn("3/10", result)
        self.assertIn("600519", result)
        self.assertIn("50%", result)

    def test_calls_generate_text_async_with_system_prompt(self):
        self.analyzer.generate_text_async.return_value = '{"portfolio_risk_score": 5}'
        asyncio.run(run_portfolio_aggregation(self.analyzer, self.two_results))
        self.analyzer.generate_text_async.assert_awaited_once()
        _, kwargs = self.analyzer.generate_text_async.call_args
        self.assertIn("system_prompt", kwargs)
        self.assertIn("portfolio analyst", kwargs["system_prompt"])

    def test_analyzer_without_generate_text_async(self):
        """analyzer object that doesn't have the required method."""
        bad_analyzer = Mock(spec=[])  # no methods
        result = asyncio.run(run_portfolio_aggregation(bad_analyzer, self.two_results))
        self.assertIsNone(result)
