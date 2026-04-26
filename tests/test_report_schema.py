# -*- coding: utf-8 -*-
"""
===================================
Report Engine - Schema parsing and fallback tests
===================================

Tests for AnalysisReportSchema validation and analyzer fallback behavior.
"""

import asyncio
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Mock litellm before importing analyzer (optional runtime dep)
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.schemas.report_schema import AnalysisReportSchema
from src.analyzer import GeminiAnalyzer, AnalysisResult


class TestAnalysisReportSchema(unittest.TestCase):
    """Schema parsing tests."""

    def test_valid_dashboard_parses(self) -> None:
        """Valid LLM-like JSON parses successfully."""
        data = {
            "stock_name": "贵州茅台",
            "sentiment_score": 75,
            "trend_prediction": "看多",
            "operation_advice": "持有",
            "decision_type": "hold",
            "confidence_level": "中",
            "dashboard": {
                "core_conclusion": {"one_sentence": "持有观望"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110元"}},
            },
            "analysis_summary": "基本面稳健",
        }
        schema = AnalysisReportSchema.model_validate(data)
        self.assertEqual(schema.stock_name, "贵州茅台")
        self.assertEqual(schema.sentiment_score, 75)
        self.assertIsNotNone(schema.dashboard)

    def test_schema_allows_optional_fields_missing(self) -> None:
        """Schema accepts minimal valid structure."""
        data = {
            "stock_name": "测试",
            "sentiment_score": 50,
            "trend_prediction": "震荡",
            "operation_advice": "观望",
        }
        schema = AnalysisReportSchema.model_validate(data)
        self.assertIsNone(schema.dashboard)
        self.assertIsNone(schema.analysis_summary)

    def test_schema_allows_numeric_strings(self) -> None:
        """Schema accepts string values for numeric fields (LLM may return N/A)."""
        data = {
            "stock_name": "测试",
            "sentiment_score": 60,
            "trend_prediction": "看多",
            "operation_advice": "买入",
            "dashboard": {
                "data_perspective": {
                    "price_position": {
                        "current_price": "N/A",
                        "bias_ma5": "2.5",
                    }
                }
            },
        }
        schema = AnalysisReportSchema.model_validate(data)
        self.assertIsNotNone(schema.dashboard)
        pp = schema.dashboard and schema.dashboard.data_perspective and schema.dashboard.data_perspective.price_position
        self.assertIsNotNone(pp)
        if pp:
            self.assertEqual(pp.current_price, "N/A")
            self.assertEqual(pp.bias_ma5, "2.5")

    def test_schema_fails_on_invalid_sentiment_score(self) -> None:
        """Schema validation fails when sentiment_score out of range."""
        data = {
            "stock_name": "测试",
            "sentiment_score": 150,  # out of 0-100
            "trend_prediction": "看多",
            "operation_advice": "买入",
        }
        with self.assertRaises(Exception):
            AnalysisReportSchema.model_validate(data)


class TestAnalyzerSchemaFallback(unittest.TestCase):
    """Analyzer fallback when schema validation fails."""

    def test_parse_response_continues_when_schema_fails(self) -> None:
        """When schema validation fails, analyzer continues with raw dict."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "贵州茅台",
            "sentiment_score": 150,  # invalid for schema
            "trend_prediction": "看多",
            "operation_advice": "持有",
            "analysis_summary": "测试摘要",
        })
        result = analyzer._parse_response(response, "600519", "贵州茅台")
        self.assertIsInstance(result, AnalysisResult)
        self.assertEqual(result.code, "600519")
        self.assertEqual(result.sentiment_score, 150)  # from raw dict
        self.assertTrue(result.success)

    def test_parse_response_valid_json_succeeds(self) -> None:
        """Valid JSON produces correct AnalysisResult."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "贵州茅台",
            "sentiment_score": 72,
            "trend_prediction": "看多",
            "operation_advice": "持有",
            "decision_type": "hold",
            "confidence_level": "高",
            "analysis_summary": "技术面向好",
        })
        result = analyzer._parse_response(response, "600519", "股票600519")
        self.assertIsInstance(result, AnalysisResult)
        self.assertEqual(result.name, "贵州茅台")
        self.assertEqual(result.sentiment_score, 72)
        self.assertEqual(result.analysis_summary, "技术面向好")

    def test_parse_response_normalizes_nested_decision_dashboard(self) -> None:
        """Nested decision_dashboard payloads should still populate dashboard fields."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "decision_dashboard": {
                "stock_name": "宁德时代（300750）",
                "decision_type": "buy",
                "system_score": 72,
                "core_conclusion": "趋势偏强，但需要等回踩确认。",
                "position_advice": {
                    "empty_position": "等待回踩 MA10 再分批介入",
                    "holding_position": "继续持有，跌破防守位减仓",
                },
                "sniper_levels": {
                    "buy_price": "438-440元",
                    "stop_loss_price": "432元",
                    "target_price": "460元",
                },
                "checklist": [
                    {
                        "question": "结构是否成立",
                        "result": "⚠️",
                        "detail": "均线仍需确认",
                    }
                ],
                "risk_alerts": ["短线量能不足"],
                "positive_catalysts": ["储能订单改善"],
                "latest_news": ["2026-04-24 机构继续上调全年盈利预期"],
                "comment": "结构改善，但仍需等待量能确认。",
            }
        })

        result = analyzer._parse_response(response, "300750", "宁德时代")

        self.assertEqual(result.name, "宁德时代（300750）")
        self.assertEqual(result.decision_type, "buy")
        self.assertEqual(result.operation_advice, "买入")
        self.assertEqual(result.trend_prediction, "看多")
        self.assertEqual(result.sentiment_score, 72)
        self.assertEqual(result.analysis_summary, "结构改善，但仍需等待量能确认。")
        self.assertEqual(
            result.dashboard["core_conclusion"]["position_advice"]["no_position"],
            "等待回踩 MA10 再分批介入",
        )
        self.assertEqual(
            result.dashboard["battle_plan"]["sniper_points"]["stop_loss"],
            "432元",
        )
        self.assertIn(
            "⚠️ 结构是否成立 均线仍需确认",
            result.dashboard["battle_plan"]["action_checklist"],
        )
        self.assertIn(
            "短线量能不足",
            result.dashboard["intelligence"]["risk_alerts"],
        )

    def test_generate_text_async_accepts_legacy_positional_arguments(self) -> None:
        """Legacy callers may still pass max_tokens and temperature positionally."""
        analyzer = GeminiAnalyzer()
        with patch.object(
            analyzer,
            "_call_litellm_async",
            new=AsyncMock(return_value=("ok", "deepseek/deepseek-chat", {})),
        ) as mocked:
            content = asyncio.run(analyzer.generate_text_async("prompt", 2048, 0.8))

        self.assertEqual(content, "ok")
        mocked.assert_awaited_once_with("prompt", {"max_tokens": 2048, "temperature": 0.8})

    def test_parse_response_normalizes_top_level_sniper_points_and_text_alerts(self) -> None:
        """Top-level sniper_points and string alerts should survive parser normalization."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "贵州茅台（600519）",
            "decision_type": "hold",
            "core_conclusion": "等待确认突破。",
            "sniper_points": {
                "buy_price": "1422",
                "stop_loss_price": "1400",
                "target_price": "1480",
            },
            "checklist": {
                "量能配合": "⚠️ 仍需继续放量确认",
            },
            "risk_alerts": "近3日无可用新闻信息，风险识别受限。",
            "positive_catalysts": "暂无新增催化。",
            "technical_summary": "结构仍在修复阶段。",
        })

        result = analyzer._parse_response(response, "600519", "贵州茅台")

        self.assertEqual(result.dashboard["battle_plan"]["sniper_points"]["ideal_buy"], "1422")
        self.assertEqual(result.dashboard["battle_plan"]["sniper_points"]["stop_loss"], "1400")
        self.assertEqual(result.dashboard["battle_plan"]["sniper_points"]["take_profit"], "1480")
        self.assertIn(
            "量能配合 ⚠️ 仍需继续放量确认",
            result.dashboard["battle_plan"]["action_checklist"],
        )
        self.assertEqual(
            result.dashboard["intelligence"]["risk_alerts"],
            ["近3日无可用新闻信息，风险识别受限。"],
        )
        self.assertEqual(
            result.dashboard["intelligence"]["positive_catalysts"],
            ["暂无新增催化。"],
        )

    def test_parse_response_recovers_score_from_summary_text(self) -> None:
        """Model summaries like '系统评分77/100' should not collapse to the neutral default."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "阿里巴巴（BABA）",
            "decision_type": "buy",
            "operation_advice": "买入",
            "trend_prediction": "看多",
            "analysis_summary": "多头排列成型，系统评分77/100，可顺势做多。",
        }, ensure_ascii=False)

        result = analyzer._parse_response(response, "BABA", "阿里巴巴")

        self.assertEqual(result.sentiment_score, 77)

    def test_parse_response_recovers_score_from_summary_dict(self) -> None:
        """Nested summary dicts emitted by some models should be inspected for scores."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "宁德时代（300750）",
            "decision_type": "hold",
            "analysis_summary": {
                "summary": "技术面多头，但 RSI 超买。",
                "system_score": "72/100",
            },
        }, ensure_ascii=False)

        result = analyzer._parse_response(response, "300750", "宁德时代")

        self.assertEqual(result.sentiment_score, 72)

    def test_analyze_async_uses_structured_analysis_system_prompt(self) -> None:
        """Regular analysis should ask the model for the dashboard JSON schema."""
        config = SimpleNamespace(
            report_language="zh",
            llm_temperature=0.2,
            news_max_age_days=7,
            news_strategy_profile="short",
        )
        response = json.dumps({
            "stock_name": "贵州茅台（600519）",
            "sentiment_score": 66,
            "trend_prediction": "震荡",
            "operation_advice": "持有",
            "decision_type": "hold",
            "analysis_summary": "结构震荡，等待确认。",
        }, ensure_ascii=False)

        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None), \
             patch("src.analyzer.core.persist_llm_usage"):
            analyzer = GeminiAnalyzer(config=config)
            with patch.object(
                analyzer,
                "_call_litellm_async",
                new=AsyncMock(return_value=(response, "deepseek/deepseek-chat", {})),
            ) as mocked:
                result = asyncio.run(analyzer.analyze_async({
                    "code": "600519",
                    "stock_name": "贵州茅台",
                    "date": "2026-04-26",
                    "today": {},
                }))

        self.assertEqual(result.sentiment_score, 66)
        call_kwargs = mocked.await_args.kwargs
        self.assertIn("system_prompt", call_kwargs)
        self.assertIn("sentiment_score", call_kwargs["system_prompt"])

    def test_parse_text_response_honors_injected_runtime_report_language(self) -> None:
        """Fallback text parsing should use the analyzer's injected config, not the global singleton."""
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(config=SimpleNamespace(report_language="en"))

        result = analyzer._parse_text_response("bullish buy setup", "AAPL", "Apple")

        self.assertEqual(result.report_language, "en")
        self.assertEqual(result.trend_prediction, "Bullish")
        self.assertEqual(result.operation_advice, "Buy")
        self.assertEqual(result.confidence_level, "Low")
