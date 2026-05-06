# -*- coding: utf-8 -*-
"""Tests for src.core.pipeline_helpers — extracted pipeline helper functions."""

from unittest import TestCase
from unittest.mock import Mock, MagicMock

from src.core.pipeline_helpers import (
    compute_ma_status,
    estimate_intel_bullet_count,
    extract_chip_payload,
    extract_quote_payload,
    extract_risk_keywords,
    extract_trend_payload,
    override_sniper_points,
    safe_to_dict,
)


class OverrideSniperPointsTest(TestCase):
    """override_sniper_points: clamp LLM prices against real support/resistance."""

    def _make_result(self, dashboard=None, metadata=None):
        from collections import defaultdict

        class FakeResult:
            def __init__(self):
                self.dashboard = dashboard or {}
                self.analysis_metadata = metadata or defaultdict(int)

        return FakeResult()

    def _trend(self, support=None, resistance=None, ma5=None, ma10=None):
        t = MagicMock()
        t.support_levels = support or []
        t.resistance_levels = resistance or []
        t.ma5 = ma5
        t.ma10 = ma10
        return t

    def test_returns_zero_when_price_none(self):
        result = self._make_result()
        self.assertEqual(override_sniper_points(result, self._trend(), None), 0)

    def test_returns_zero_when_price_zero(self):
        result = self._make_result()
        self.assertEqual(override_sniper_points(result, self._trend(), 0), 0)

    def test_returns_zero_when_trend_none(self):
        result = self._make_result()
        self.assertEqual(override_sniper_points(result, None, 100.0), 0)

    def test_returns_zero_when_no_dashboard(self):
        result = Mock()
        result.dashboard = None
        self.assertEqual(override_sniper_points(result, self._trend(), 100.0), 0)

    def test_returns_zero_when_dashboard_not_dict(self):
        result = Mock()
        result.analysis_metadata = {}
        result.dashboard = "nope"
        self.assertEqual(override_sniper_points(result, self._trend(), 100.0), 0)

    def test_returns_zero_when_no_sniper_points(self):
        result = self._make_result(dashboard={"battle_plan": {}})
        self.assertEqual(override_sniper_points(result, self._trend(support=[95]), 100.0), 0)

    def test_overrides_stop_loss_above_support(self):
        result = self._make_result(dashboard={"battle_plan": {"sniper_points": {"stop_loss": 99}}},
                                    metadata={"sniper_overrides": 0})
        trend = self._trend(support=[95])
        count = override_sniper_points(result, trend, 100.0)
        self.assertGreater(count, 0)
        sp = result.dashboard["battle_plan"]["sniper_points"]
        self.assertLessEqual(sp["stop_loss"], 95)

    def test_overrides_stop_loss_too_low(self):
        result = self._make_result(dashboard={"battle_plan": {"sniper_points": {"stop_loss": 70}}},
                                    metadata={"sniper_overrides": 0})
        trend = self._trend(support=[95])
        count = override_sniper_points(result, trend, 100.0)
        self.assertGreater(count, 0)

    def test_passes_valid_stop_loss(self):
        result = self._make_result(dashboard={"battle_plan": {"sniper_points": {"stop_loss": 92.5}}},
                                    metadata={"sniper_overrides": 0})
        trend = self._trend(support=[95])
        self.assertEqual(override_sniper_points(result, trend, 100.0), 0)

    def test_overrides_ideal_buy_too_high(self):
        result = self._make_result(dashboard={"battle_plan": {"sniper_points": {"ideal_buy": 120}}},
                                    metadata={"sniper_overrides": 0})
        count = override_sniper_points(result, self._trend(support=[90]), 100.0)
        self.assertGreater(count, 0)
        self.assertLessEqual(result.dashboard["battle_plan"]["sniper_points"]["ideal_buy"], 105)

    def test_overrides_ideal_buy_too_low(self):
        result = self._make_result(dashboard={"battle_plan": {"sniper_points": {"ideal_buy": 60}}},
                                    metadata={"sniper_overrides": 0})
        count = override_sniper_points(result, self._trend(support=[90]), 100.0)
        self.assertGreater(count, 0)
        self.assertGreaterEqual(result.dashboard["battle_plan"]["sniper_points"]["ideal_buy"], 85)

    def test_overrides_take_profit_above_resistance(self):
        result = self._make_result(dashboard={"battle_plan": {"sniper_points": {"take_profit": 200}}},
                                    metadata={"sniper_overrides": 0})
        trend = self._trend(support=[90], resistance=[120])
        count = override_sniper_points(result, trend, 100.0)
        self.assertGreater(count, 0)
        self.assertLessEqual(result.dashboard["battle_plan"]["sniper_points"]["take_profit"], 144)

    def test_overrides_take_profit_below_min_rr(self):
        result = self._make_result(dashboard={"battle_plan": {"sniper_points": {
            "stop_loss": 95, "take_profit": 103}}},
            metadata={"sniper_overrides": 0})
        count = override_sniper_points(result, self._trend(support=[90]), 100.0)
        self.assertGreater(count, 0)

    def test_overrides_all_three(self):
        result = self._make_result(dashboard={"battle_plan": {"sniper_points": {
            "stop_loss": 99, "ideal_buy": 120, "take_profit": 200}}},
            metadata={"sniper_overrides": 0})
        trend = self._trend(support=[95], resistance=[130])
        count = override_sniper_points(result, trend, 100.0)
        self.assertGreaterEqual(count, 2)


class ExtractQuotePayloadTest(TestCase):
    """extract_quote_payload: safely extract quote fields from object or dict."""

    def test_none_returns_none(self):
        self.assertIsNone(extract_quote_payload(None))

    def test_dict_extraction(self):
        quote = {"name": "茅台", "price": 180.5, "change_pct": 1.2}
        payload = extract_quote_payload(quote)
        self.assertEqual(payload["name"], "茅台")
        self.assertEqual(payload["price"], 180.5)
        self.assertEqual(payload["change_pct"], 1.2)

    def test_attr_extraction(self):
        class Quote:
            name = "茅台"
            price = 180.5
            change_pct = 1.2
        payload = extract_quote_payload(Quote())
        self.assertEqual(payload["name"], "茅台")
        self.assertEqual(payload["price"], 180.5)

    def test_filters_empty_values(self):
        quote = {"name": "茅台", "price": None}
        payload = extract_quote_payload(quote)
        self.assertNotIn("price", payload)
        self.assertIn("name", payload)

    def test_returns_none_when_all_empty(self):
        self.assertIsNone(extract_quote_payload({}))

    def test_open_fallback(self):
        class Quote:
            open = 179.0
        payload = extract_quote_payload(Quote())
        self.assertEqual(payload["open"], 179.0)


class ExtractChipPayloadTest(TestCase):
    """extract_chip_payload: safely extract chip distribution data."""

    def test_none_returns_none(self):
        self.assertIsNone(extract_chip_payload(None))

    def test_dict_passthrough(self):
        data = {"profit_ratio": 50.0, "avg_cost": 100.0}
        payload = extract_chip_payload(data)
        self.assertEqual(payload["profit_ratio"], 50.0)
        self.assertEqual(payload["avg_cost"], 100.0)

    def test_object_extraction(self):
        class Chip:
            profit_ratio = 45.0
            avg_cost = 110.0
            concentration_90 = 12.0
        payload = extract_chip_payload(Chip())
        self.assertEqual(payload["profit_ratio"], 45.0)
        self.assertEqual(payload["avg_cost"], 110.0)

    def test_filters_none(self):
        chip = {"profit_ratio": None, "avg_cost": 100.0}
        payload = extract_chip_payload(chip)
        self.assertNotIn("profit_ratio", payload)
        self.assertIn("avg_cost", payload)

    def test_neither_dict_nor_object(self):
        self.assertIsNone(extract_chip_payload("string"))


class ExtractTrendPayloadTest(TestCase):
    """extract_trend_payload: safely extract trend analysis data."""

    def test_none_returns_none(self):
        self.assertIsNone(extract_trend_payload(None))

    def test_to_dict_method(self):
        obj = Mock()
        obj.to_dict.return_value = {"ma5": 100, "ma10": 98}
        payload = extract_trend_payload(obj)
        self.assertEqual(payload["ma5"], 100)

    def test_dict_input(self):
        payload = extract_trend_payload({"ma5": 100})
        self.assertEqual(payload["ma5"], 100)

    def test_object_fallback(self):
        class Trend:
            def __init__(self):
                self.ma5 = 100
        payload = extract_trend_payload(Trend())
        self.assertEqual(payload["ma5"], 100)

    def test_unconvertible(self):
        self.assertIsNone(extract_trend_payload(42))


class ComputeMaStatusTest(TestCase):
    """compute_ma_status: MA alignment classification."""

    def test_bullish(self):
        self.assertIn("多头", compute_ma_status(102, 101, 100, 103))

    def test_bearish(self):
        self.assertIn("空头", compute_ma_status(98, 99, 100, 97))

    def test_short_term_bullish(self):
        self.assertIn("短期", compute_ma_status(102, 101, 105, 103))

    def test_short_term_bearish(self):
        self.assertIn("短期", compute_ma_status(99, 100, 98, 98.5))

    def test_consolidation(self):
        self.assertIn("震荡", compute_ma_status(101, 100, 102, 101))

    def test_insufficient_data(self):
        self.assertEqual(compute_ma_status(0, 100, 100, 100), "均线不足")

    def test_edge_case_all_zero(self):
        result = compute_ma_status(0, 0, 0, 0)
        self.assertEqual(result, "均线不足")


class SafeToDictTest(TestCase):
    """safe_to_dict: safe conversion to dict."""

    def test_none_returns_none(self):
        self.assertIsNone(safe_to_dict(None))

    def test_to_dict_method(self):
        obj = Mock()
        obj.to_dict.return_value = {"a": 1}
        self.assertEqual(safe_to_dict(obj), {"a": 1})

    def test_dict_returns(self):
        self.assertEqual(safe_to_dict({"a": 1}), {"a": 1})

    def test_object_fallback(self):
        class Obj:
            pass
        obj = Obj()
        obj.a = 1
        self.assertEqual(safe_to_dict(obj), {"a": 1})

    def test_unconvertible(self):
        self.assertIsNone(safe_to_dict("string"))


class ExtractRiskKeywordsTest(TestCase):
    """extract_risk_keywords: pattern matching for risk terms."""

    def test_no_matches(self):
        self.assertEqual(extract_risk_keywords("一切正常"), [])

    def test_detects_reduction(self):
        self.assertIn("减持", extract_risk_keywords("大股东计划减持"))

    def test_detects_penalty(self):
        self.assertIn("处罚", extract_risk_keywords("公司收到处罚通知"))
        self.assertIn("处罚", extract_risk_keywords("被罚款"))

    def test_detects_investigation(self):
        self.assertIn("调查", extract_risk_keywords("被立案调查"))

    def test_detects_loss(self):
        self.assertIn("预亏", extract_risk_keywords("业绩预亏"))

    def test_detects_multiple(self):
        hits = extract_risk_keywords("公司收到处罚通知书，业绩预亏，大股东减持")
        self.assertIn("减持", hits)
        self.assertIn("处罚", hits)
        self.assertIn("预亏", hits)

    def test_empty_text(self):
        self.assertEqual(extract_risk_keywords(""), [])


class EstimateIntelBulletCountTest(TestCase):
    """estimate_intel_bullet_count: count markdown bullets."""

    def test_no_bullets(self):
        self.assertEqual(estimate_intel_bullet_count("hello"), 0)

    def test_empty_text(self):
        self.assertEqual(estimate_intel_bullet_count(""), 0)

    def test_single_bullet(self):
        text = "- item one"
        self.assertEqual(estimate_intel_bullet_count(text), 1)

    def test_multiple_bullets(self):
        text = "- first\n- second\n- third"
        self.assertEqual(estimate_intel_bullet_count(text), 3)

    def test_not_bullet_if_not_at_line_start(self):
        text = "text - not bullet"
        self.assertEqual(estimate_intel_bullet_count(text), 0)
