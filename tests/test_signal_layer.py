# -*- coding: utf-8 -*-
"""Tests for :mod:`src.agent.signal_layer`."""

from __future__ import annotations

import pytest
from src.agent.signal_layer import (
    NormalizedSignal,
    normalize_all_signals,
)
from src.schemas.analysis_result import (
    validate_numerical_fields,
    AnalysisResult,
)


class TestNormalizedSignal:
    def test_default_neutral(self):
        sig = NormalizedSignal()
        assert sig.signal == "neutral"
        assert sig.score == 50.0
        assert sig.confidence == 0.5

    def test_to_prompt_line(self):
        sig = NormalizedSignal(
            dimension="trend",
            signal="bullish",
            score=75.0,
            confidence=0.7,
            key_facts=["趋势:多头排列", "评分:75"],
        )
        line = sig.to_prompt_line()
        assert "📈" in line
        assert "trend" in line
        assert "bullish" in line
        assert "75" in line


class TestNormalizeTrend:
    @staticmethod
    def _make_trend_result(
        signal_score: int = 50,
        buy_signal: str = "观望",
        trend_status: str = "盘整",
        trend_strength: float = 50.0,
    ):
        """Build a minimal TrendAnalysisResult-like object."""
        from types import SimpleNamespace
        return SimpleNamespace(
            signal_score=signal_score,
            buy_signal=SimpleNamespace(value=buy_signal),
            trend_status=SimpleNamespace(value=trend_status),
            trend_strength=trend_strength,
        )

    def test_none_input(self):
        signals = normalize_all_signals(trend_result=None)
        trend = [s for s in signals if s.dimension == "trend"][0]
        assert trend.signal == "neutral"
        assert trend.score == 50.0

    def test_strong_buy_signal(self):
        tr = self._make_trend_result(signal_score=80, buy_signal="强烈买入", trend_strength=85.0)
        signals = normalize_all_signals(trend_result=tr)
        trend = [s for s in signals if s.dimension == "trend"][0]
        assert trend.signal == "bullish"
        assert trend.score >= 70
        assert trend.confidence > 0.5

    def test_sell_signal(self):
        tr = self._make_trend_result(signal_score=30, buy_signal="强烈卖出", trend_strength=70.0)
        signals = normalize_all_signals(trend_result=tr)
        trend = [s for s in signals if s.dimension == "trend"][0]
        assert trend.signal == "bearish"
        assert trend.score <= 40

    def test_hold_signal(self):
        tr = self._make_trend_result(signal_score=55, buy_signal="持有", trend_strength=40.0)
        signals = normalize_all_signals(trend_result=tr)
        trend = [s for s in signals if s.dimension == "trend"][0]
        assert trend.signal == "neutral"
        assert 40 <= trend.score <= 70


class TestNormalizeVolume:
    @staticmethod
    def _make_volume_result(volume_status: str, volume_ratio_5d: float = 1.0):
        from types import SimpleNamespace
        return SimpleNamespace(
            signal_score=50,
            buy_signal=SimpleNamespace(value="观望"),
            trend_status=SimpleNamespace(value="盘整"),
            trend_strength=50.0,
            volume_status=SimpleNamespace(value=volume_status),
            volume_ratio_5d=volume_ratio_5d,
        )

    def test_shrink_volume_down_is_bullish(self):
        tr = self._make_volume_result("缩量回调")
        signals = normalize_all_signals(trend_result=tr)
        vol = [s for s in signals if s.dimension == "volume"][0]
        assert vol.signal == "bullish"
        assert vol.score >= 60

    def test_heavy_volume_down_is_bearish(self):
        tr = self._make_volume_result("放量下跌")
        signals = normalize_all_signals(trend_result=tr)
        vol = [s for s in signals if s.dimension == "volume"][0]
        assert vol.signal == "bearish"
        assert vol.score <= 35


class TestNormalizeMomentum:
    @staticmethod
    def _make_momentum_result(rsi_6: float = 50, macd_status: str = "多头"):
        from types import SimpleNamespace
        return SimpleNamespace(
            signal_score=50,
            buy_signal=SimpleNamespace(value="观望"),
            trend_status=SimpleNamespace(value="盘整"),
            trend_strength=50.0,
            rsi_6=rsi_6,
            rsi_status=SimpleNamespace(value="中性"),
            macd_status=SimpleNamespace(value=macd_status),
            macd_signal="",
        )

    def test_overbought(self):
        tr = self._make_momentum_result(rsi_6=80, macd_status="多头")
        signals = normalize_all_signals(trend_result=tr)
        mom = [s for s in signals if s.dimension == "momentum"][0]
        assert mom.signal == "bearish"

    def test_oversold(self):
        tr = self._make_momentum_result(rsi_6=25, macd_status="空头")
        signals = normalize_all_signals(trend_result=tr)
        mom = [s for s in signals if s.dimension == "momentum"][0]
        assert mom.signal == "bullish"

    def test_golden_cross_boosts(self):
        tr = self._make_momentum_result(rsi_6=55, macd_status="零轴上金叉")
        signals = normalize_all_signals(trend_result=tr)
        mom = [s for s in signals if s.dimension == "momentum"][0]
        assert mom.signal == "bullish"
        assert mom.score >= 60


class TestNormalizeChip:
    def test_none_input(self):
        signals = normalize_all_signals(chip_data=None)
        chip = [s for s in signals if s.dimension == "chip"][0]
        assert chip.signal == "neutral"

    def test_healthy_profit_ratio(self):
        chip = {"profit_ratio": 0.50, "concentration_90": 0.12}
        signals = normalize_all_signals(chip_data=chip)
        chip_sig = [s for s in signals if s.dimension == "chip"][0]
        assert chip_sig.signal == "bullish"

    def test_high_profit_danger(self):
        chip = {"profit_ratio": 0.85, "concentration_90": 0.25}
        signals = normalize_all_signals(chip_data=chip)
        chip_sig = [s for s in signals if s.dimension == "chip"][0]
        assert chip_sig.signal == "bearish"


class TestNormalizeSentiment:
    def test_no_news_no_score(self):
        signals = normalize_all_signals(news_context="")
        sent = [s for s in signals if s.dimension == "sentiment"][0]
        assert sent.signal == "neutral"

    def test_positive_news(self):
        ctx = "公司业绩预增50% 且中标大额合同 同时获得政策利好"
        signals = normalize_all_signals(news_context=ctx)
        sent = [s for s in signals if s.dimension == "sentiment"][0]
        assert sent.signal == "bullish"

    def test_negative_news(self):
        ctx = "公司遭证监会立案调查 持股5%以上股东减持 诉讼败诉赔偿"
        signals = normalize_all_signals(news_context=ctx)
        sent = [s for s in signals if s.dimension == "sentiment"][0]
        assert sent.signal == "bearish"

    def test_mixed_news(self):
        ctx = "业绩预增但股东减持"
        signals = normalize_all_signals(news_context=ctx)
        sent = [s for s in signals if s.dimension == "sentiment"][0]
        # net = 1 - 1 = 0 → neutral
        assert sent.signal == "neutral"


class TestAllSignalsCount:
    def test_all_dimensions_present(self):
        signals = normalize_all_signals()
        dimensions = {s.dimension for s in signals}
        expected = {"trend", "volume", "momentum", "chip", "sentiment", "valuation", "divergence", "fundamental_growth"}
        assert dimensions == expected


class TestNormalizeValuation:
    @staticmethod
    def _make_quote(pe=None, pb=None, turnover=None):
        from types import SimpleNamespace
        return SimpleNamespace(pe_ratio=pe, pb_ratio=pb, turnover_rate=turnover)

    def test_none_input(self):
        signals = normalize_all_signals(realtime_quote=None)
        val = [s for s in signals if s.dimension == "valuation"][0]
        assert val.signal == "neutral"
        assert val.score == 50.0

    def test_no_realtime_quote_returns_neutral(self):
        """When realtime_quote is omitted, valuation stays default-neutral."""
        signals = normalize_all_signals()
        val = [s for s in signals if s.dimension == "valuation"][0]
        assert val.signal == "neutral"
        assert val.score == 50.0

    def test_low_pe_is_bullish_leaning(self):
        signals = normalize_all_signals(realtime_quote=self._make_quote(pe=10))
        val = [s for s in signals if s.dimension == "valuation"][0]
        assert val.score > 55

    def test_high_pe_is_bearish_leaning(self):
        signals = normalize_all_signals(realtime_quote=self._make_quote(pe=80))
        val = [s for s in signals if s.dimension == "valuation"][0]
        assert val.score < 45

    def test_speculative_turnover_is_bearish(self):
        signals = normalize_all_signals(realtime_quote=self._make_quote(pe=20, turnover=15))
        val = [s for s in signals if s.dimension == "valuation"][0]
        assert val.score <= 46  # blended from PE:55 + turnover:35 → avg 45

    def test_healthy_valuation(self):
        signals = normalize_all_signals(
            realtime_quote=self._make_quote(pe=18, pb=2.5, turnover=3),
        )
        val = [s for s in signals if s.dimension == "valuation"][0]
        assert val.score > 50
        assert val.signal in ("bullish", "neutral")


class TestNormalizeDivergence:
    def test_none_trend_result_returns_neutral(self):
        from src.agent.signal_layer import _normalize_divergence
        sig = _normalize_divergence(None)
        assert sig.dimension == "divergence"
        assert sig.signal == "neutral"
        assert sig.score == 50.0

    def test_no_divergence_returns_neutral(self):
        from types import SimpleNamespace
        from src.agent.signal_layer import _normalize_divergence

        tr = SimpleNamespace(macd_divergence="", rsi_divergence="")
        sig = _normalize_divergence(tr)
        assert sig.signal == "neutral"
        assert sig.score == 50.0

    def test_macd_bearish_divergence(self):
        from types import SimpleNamespace
        from src.agent.signal_layer import _normalize_divergence

        tr = SimpleNamespace(macd_divergence="bearish", rsi_divergence="")
        sig = _normalize_divergence(tr)
        assert sig.signal == "bearish"
        assert sig.score <= 40
        assert any("MACD" in f for f in sig.key_facts)

    def test_rsi_bullish_divergence(self):
        from types import SimpleNamespace
        from src.agent.signal_layer import _normalize_divergence

        tr = SimpleNamespace(macd_divergence="", rsi_divergence="bullish")
        sig = _normalize_divergence(tr)
        assert sig.signal == "bullish"
        assert sig.score >= 60
        assert any("RSI" in f for f in sig.key_facts)

    def test_dual_bearish_divergence_boosts_confidence(self):
        from types import SimpleNamespace
        from src.agent.signal_layer import _normalize_divergence

        tr = SimpleNamespace(macd_divergence="bearish", rsi_divergence="bearish")
        sig = _normalize_divergence(tr)
        assert sig.signal == "bearish"
        assert sig.score <= 35  # more bearish than single
        assert sig.confidence >= 0.55  # higher confidence with confirmation

    def test_divergence_in_signal_table(self):
        """Divergence dimension appears in normalize_all_signals output."""
        from types import SimpleNamespace

        tr = SimpleNamespace(macd_divergence="bearish", rsi_divergence="")
        signals = normalize_all_signals(trend_result=tr)
        div = [s for s in signals if s.dimension == "divergence"][0]
        assert div.signal == "bearish"


class TestNormalizeFundamentalGrowth:
    def test_none_input_returns_neutral(self):
        from src.agent.signal_layer import _normalize_fundamental_growth
        sig = _normalize_fundamental_growth(None)
        assert sig.dimension == "fundamental_growth"
        assert sig.signal == "neutral"

    def test_empty_dict_returns_neutral(self):
        from src.agent.signal_layer import _normalize_fundamental_growth
        sig = _normalize_fundamental_growth({})
        assert sig.signal == "neutral"

    def test_strong_growth_is_bullish(self):
        ctx = {"growth": {"revenue_yoy": 25.0, "net_profit_yoy": 35.0, "roe": 18.0}}
        from src.agent.signal_layer import _normalize_fundamental_growth
        sig = _normalize_fundamental_growth(ctx)
        assert sig.signal == "bullish"
        assert sig.score >= 60
        assert sig.confidence >= 0.5

    def test_negative_growth_is_bearish(self):
        ctx = {"growth": {"revenue_yoy": -5.0, "net_profit_yoy": -10.0, "roe": 5.0}}
        from src.agent.signal_layer import _normalize_fundamental_growth
        sig = _normalize_fundamental_growth(ctx)
        assert sig.signal == "bearish"
        assert sig.score <= 45

    def test_mixed_signals_produce_neutral(self):
        """One strong, one weak, one average → neutral."""
        ctx = {"growth": {"revenue_yoy": 25.0, "net_profit_yoy": -5.0, "roe": 10.0}}
        from src.agent.signal_layer import _normalize_fundamental_growth
        sig = _normalize_fundamental_growth(ctx)
        assert sig.signal == "neutral"

    def test_roe_from_financial_report(self):
        """ROE can come from earnings.financial_report.roe."""
        ctx = {
            "growth": {"revenue_yoy": 10.0, "net_profit_yoy": 15.0},
            "earnings": {"financial_report": {"roe": 20.0}},
        }
        from src.agent.signal_layer import _normalize_fundamental_growth
        sig = _normalize_fundamental_growth(ctx)
        assert sig.signal == "bullish"
        assert sig.score >= 55

    def test_fundamental_growth_in_signal_table(self):
        """fundamental_growth dimension appears in normalize_all_signals output."""
        ctx = {"growth": {"revenue_yoy": 30.0, "net_profit_yoy": 40.0, "roe": 22.0}}
        signals = normalize_all_signals(fundamental_context=ctx)
        fg = [s for s in signals if s.dimension == "fundamental_growth"][0]
        assert fg.signal == "bullish"
        assert fg.score >= 60

    def test_no_growth_data_returns_neutral(self):
        signals = normalize_all_signals(fundamental_context={"growth": {}})
        fg = [s for s in signals if s.dimension == "fundamental_growth"][0]
        assert fg.signal == "neutral"


class TestValidateNumericalFields:
    def test_empty_result_no_crash(self):
        r = AnalysisResult(code="000001", name="平安银行")
        warnings = validate_numerical_fields(r, current_price=10.0)
        assert warnings == []

    def test_stop_loss_below_buy(self):
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 10.0,
                    "stop_loss": 9.5,
                    "take_profit": 12.0,
                }
            }
        }
        warnings = validate_numerical_fields(r, current_price=10.0)
        assert len(warnings) == 0  # all valid

    def test_stop_loss_above_buy(self):
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 10.0,
                    "stop_loss": 10.5,
                    "take_profit": 12.0,
                }
            }
        }
        warnings = validate_numerical_fields(r, current_price=10.0)
        assert any("止损价" in w for w in warnings)

    def test_take_profit_below_buy(self):
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 10.0,
                    "stop_loss": 9.5,
                    "take_profit": 9.0,
                }
            }
        }
        warnings = validate_numerical_fields(r, current_price=10.0)
        assert any("目标价" in w for w in warnings)

    def test_price_deviation_too_large(self):
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 5.0,
                    "stop_loss": 4.5,
                    "take_profit": 6.0,
                }
            }
        }
        warnings = validate_numerical_fields(r, current_price=10.0)
        assert any("偏离现价" in w for w in warnings)

    def test_no_price_skips_validation(self):
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 100.0,
                    "stop_loss": 99.0,
                }
            }
        }
        warnings = validate_numerical_fields(r, current_price=None)
        assert warnings == []

    def test_parse_price_from_string(self):
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "10.50元",
                    "stop_loss": "9.80元",
                    "take_profit": "12.30元",
                }
            }
        }
        warnings = validate_numerical_fields(r, current_price=10.0)
        assert len(warnings) == 0  # strings parsed correctly


class TestOverrideSniperPoints:
    """Tests for override_sniper_points in pipeline.py."""

    @staticmethod
    def _make_trend_result(
        support_levels=None,
        resistance_levels=None,
        ma5=10.0,
        ma10=9.5,
    ):
        from types import SimpleNamespace
        return SimpleNamespace(
            support_levels=support_levels or [],
            resistance_levels=resistance_levels or [],
            ma5=ma5,
            ma10=ma10,
        )

    def test_no_dashboard_no_crash(self):
        from src.core.pipeline_helpers import override_sniper_points
        r = AnalysisResult(code="000001", name="测试")
        tr = self._make_trend_result()
        count = override_sniper_points(r, tr, current_price=10.0)
        assert count == 0

    def test_stop_loss_above_support_overridden(self):
        from src.core.pipeline_helpers import override_sniper_points
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 10.0,
                    "stop_loss": 11.0,  # above support → should be clamped
                    "take_profit": 12.0,
                }
            }
        }
        tr = self._make_trend_result(support_levels=[9.5, 9.0])
        count = override_sniper_points(r, tr, current_price=10.0)
        assert count >= 1
        from src.schemas.analysis_result import parse_price
        new_sl = parse_price(r.dashboard["battle_plan"]["sniper_points"]["stop_loss"])
        assert new_sl is not None
        assert new_sl < 10.0  # should be below current price

    def test_stop_loss_too_low_overridden(self):
        from src.core.pipeline_helpers import override_sniper_points
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 10.0,
                    "stop_loss": 5.0,  # way too low → clamped
                    "take_profit": 12.0,
                }
            }
        }
        tr = self._make_trend_result(support_levels=[9.5])
        count = override_sniper_points(r, tr, current_price=10.0)
        assert count >= 1
        from src.schemas.analysis_result import parse_price
        new_sl = parse_price(r.dashboard["battle_plan"]["sniper_points"]["stop_loss"])
        assert new_sl is not None
        assert new_sl > 5.0  # should have been raised

    def test_ideal_buy_too_high_overridden(self):
        from src.core.pipeline_helpers import override_sniper_points
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 15.0,  # 50% above market → clamped
                    "stop_loss": 9.5,
                    "take_profit": 12.0,
                }
            }
        }
        tr = self._make_trend_result()
        count = override_sniper_points(r, tr, current_price=10.0)
        assert count >= 1
        from src.schemas.analysis_result import parse_price
        new_buy = parse_price(r.dashboard["battle_plan"]["sniper_points"]["ideal_buy"])
        assert new_buy is not None
        assert new_buy <= 10.5  # max_buy = current * 1.05

    def test_ideal_buy_too_low_overridden(self):
        from src.core.pipeline_helpers import override_sniper_points
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 5.0,  # 50% below market → clamped
                    "stop_loss": 4.5,
                    "take_profit": 12.0,
                }
            }
        }
        tr = self._make_trend_result()
        count = override_sniper_points(r, tr, current_price=10.0)
        assert count >= 1
        from src.schemas.analysis_result import parse_price
        new_buy = parse_price(r.dashboard["battle_plan"]["sniper_points"]["ideal_buy"])
        assert new_buy is not None
        assert new_buy >= 8.5  # min_buy = current * 0.85

    def test_take_profit_too_high_overridden(self):
        from src.core.pipeline_helpers import override_sniper_points
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 10.0,
                    "stop_loss": 9.5,
                    "take_profit": 50.0,  # absurd → clamped
                }
            }
        }
        tr = self._make_trend_result(resistance_levels=[12.0])
        count = override_sniper_points(r, tr, current_price=10.0)
        assert count >= 1
        from src.schemas.analysis_result import parse_price
        new_tp = parse_price(r.dashboard["battle_plan"]["sniper_points"]["take_profit"])
        assert new_tp is not None
        assert new_tp < 50.0

    def test_no_trend_result_skips_override(self):
        from src.core.pipeline_helpers import override_sniper_points
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 100.0,
                    "stop_loss": 99.0,
                }
            }
        }
        count = override_sniper_points(r, None, current_price=10.0)
        assert count == 0  # nothing to override with

    def test_valid_values_not_overridden(self):
        from src.core.pipeline_helpers import override_sniper_points
        r = AnalysisResult(code="000001", name="测试")
        r.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": 10.0,
                    "stop_loss": 9.3,
                    "take_profit": 12.0,
                }
            }
        }
        tr = self._make_trend_result(
            support_levels=[9.0, 9.5],  # nearest_support=9.5 > stop_loss 9.3 → no override
            resistance_levels=[12.5],
            ma5=9.8,
            ma10=9.5,
        )
        count = override_sniper_points(r, tr, current_price=10.0)
        assert count == 0  # all values already reasonable


class TestNormalizedSignalsInPrompt:
    def test_prompt_renders_signal_table(self):
        """Integration: format_analysis_prompt with normalized_signals."""
        from src.analyzer.prompt_builder import format_analysis_prompt

        signals = normalize_all_signals()
        signal_dicts = [s.__dict__ for s in signals]
        context = {"code": "600519", "stock_name": "贵州茅台", "date": "2026-05-04"}
        prompt = format_analysis_prompt(
            context,
            "贵州茅台",
            output_format="dashboard",
            normalized_signals=signal_dicts,
        )
        # Should contain the signal table header
        assert "量化信号摘要" in prompt
        assert "trend" in prompt or "趋势" in prompt or "trend" in prompt
        assert "系统预计算" in prompt

    def test_signal_table_skipped_when_none(self):
        """Backward compat: prompt without signals is unchanged."""
        from src.analyzer.prompt_builder import format_analysis_prompt

        context = {"code": "600519", "stock_name": "贵州茅台", "date": "2026-05-04"}
        prompt_with = format_analysis_prompt(
            context, "贵州茅台", output_format="dashboard", normalized_signals=None
        )
        prompt_without = format_analysis_prompt(
            context, "贵州茅台", output_format="dashboard"
        )
        assert prompt_with == prompt_without
        assert "量化信号摘要" not in prompt_with

    def test_data_freshness_marker(self):
        """Freshness markers appear in prompt when context has data_freshness."""
        from src.analyzer.prompt_builder import format_analysis_prompt

        context = {
            "code": "600519", "stock_name": "贵州茅台", "date": "2026-05-04",
            "data_freshness": "05-04 14:30",
        }
        prompt = format_analysis_prompt(context, "贵州茅台", output_format="dashboard")
        assert "05-04 14:30" in prompt
