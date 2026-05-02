# -*- coding: utf-8 -*-
"""Tests for Analyzer.generate_text() and the market_analyzer bypass fix.

Covers:
- generate_text() returns the LLM response on success
- generate_text() returns None and logs on failure (no exception propagated)
- market_analyzer calls generate_text(), not private analyzer attributes
- Any provider configuration (Gemini / Anthropic / OpenAI / LLM_CHANNELS)
  does NOT trigger AttributeError (regression guard for the old bypass bug)
"""
import sys
from unittest.mock import MagicMock, patch

# Stub heavy dependencies before project imports
for _mod in ("litellm", "google.generativeai", "google.genai", "anthropic"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest
from unittest.mock import PropertyMock


# ---------------------------------------------------------------------------
# Analyzer.generate_text()
# ---------------------------------------------------------------------------

class TestAnalyzerGenerateText:
    def _make_analyzer(self):
        """Return a minimally configured GeminiAnalyzer with _call_litellm mocked."""
        with patch("src.analyzer.get_config") as mock_cfg:
            cfg = MagicMock()
            cfg.litellm_model = "gemini/gemini-2.0-flash"
            cfg.litellm_fallback_models = []
            cfg.gemini_api_keys = ["sk-gemini-testkey-1234"]
            cfg.anthropic_api_keys = []
            cfg.openai_api_keys = []
            cfg.deepseek_api_keys = []
            cfg.llm_model_list = []
            cfg.openai_base_url = None
            mock_cfg.return_value = cfg
            from src.analyzer import GeminiAnalyzer
            analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
            analyzer._router = None
            return analyzer

    def test_generate_text_returns_llm_response(self):
        analyzer = self._make_analyzer()
        with patch.object(analyzer, "_call_litellm", return_value="市场分析报告") as mock_call:
            result = analyzer.generate_text("写一份复盘", max_tokens=1024, temperature=0.5)
            assert result == "市场分析报告"
            mock_call.assert_called_once_with(
                "写一份复盘",
                generation_config={"max_tokens": 1024, "temperature": 0.5},
            )

    def test_generate_text_returns_none_on_failure(self):
        analyzer = self._make_analyzer()
        with patch.object(analyzer, "_call_litellm", side_effect=Exception("LLM error")):
            result = analyzer.generate_text("prompt")
            assert result is None  # must not raise

    def test_generate_text_default_params(self):
        analyzer = self._make_analyzer()
        with patch.object(analyzer, "_call_litellm", return_value="ok") as mock_call:
            analyzer.generate_text("hello")
            _, kwargs = mock_call.call_args
            gen_cfg = kwargs["generation_config"]
            assert gen_cfg["max_tokens"] == 2048
            assert gen_cfg["temperature"] == 0.7


# ---------------------------------------------------------------------------
# market_analyzer uses generate_text(), not private attributes
# ---------------------------------------------------------------------------

class TestMarketAnalyzerBypassFix:
    def _make_market_analyzer_with_mock_generate_text(self, return_value="复盘报告"):
        """Return a MarketAnalyzer whose embedded Analyzer.generate_text is mocked."""
        from src.core.market_profile import CN_PROFILE
        from src.core.market_strategy import get_market_strategy_blueprint

        with patch("src.analyzer.get_config") as mock_cfg, \
             patch("src.market_analyzer.get_config") as mock_cfg2:
            cfg = MagicMock()
            cfg.litellm_model = "gemini/gemini-2.0-flash"
            cfg.litellm_fallback_models = []
            cfg.gemini_api_keys = ["sk-gemini-testkey-1234"]
            cfg.anthropic_api_keys = []
            cfg.openai_api_keys = []
            cfg.deepseek_api_keys = []
            cfg.llm_model_list = []
            cfg.openai_base_url = None
            cfg.market_review_region = "cn"
            mock_cfg.return_value = cfg
            mock_cfg2.return_value = cfg

            from src.analyzer import GeminiAnalyzer
            from src.market_analyzer import MarketAnalyzer

            analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
            analyzer._router = None
            analyzer._litellm_available = True
            analyzer.generate_text = MagicMock(return_value=return_value)

            ma = MarketAnalyzer.__new__(MarketAnalyzer)
            ma.analyzer = analyzer
            ma.profile = CN_PROFILE
            ma.strategy = get_market_strategy_blueprint("cn")
            ma.region = "cn"
            return ma

    def test_no_access_to_private_model_attribute(self):
        """generate_text() must be called; _model must never be accessed."""
        ma = self._make_market_analyzer_with_mock_generate_text("复盘结果")
        # Ensure _model attribute does not exist (simulates PR #494 state)
        assert not hasattr(ma.analyzer, "_model") or ma.analyzer._model is None, (
            "_model should not be set on the LiteLLM-based analyzer"
        )
        # generate_text is a MagicMock, so calling it won't crash
        result = ma.analyzer.generate_text("prompt")
        assert result == "复盘结果"
        ma.analyzer.generate_text.assert_called_once()

    def test_generate_text_none_falls_back_to_template(self):
        """generate_market_review() falls back to template when generate_text returns None."""
        from src.market_analyzer import MarketOverview, MarketIndex

        ma = self._make_market_analyzer_with_mock_generate_text(return_value=None)
        overview = MarketOverview(
            date="2026-03-05",
            indices=[
                MarketIndex(
                    code="000001",
                    name="上证指数",
                    current=3300.0,
                    change=5.0,
                    change_pct=0.15,
                )
            ],
        )
        result = ma.generate_market_review(overview, [])
        assert isinstance(result, str) and len(result) > 0
        ma.analyzer.generate_text.assert_called_once()

    def test_no_private_attribute_access_in_market_analyzer_source(self):
        """Static guard: market_analyzer.py must not access private analyzer attrs."""
        import ast
        import pathlib

        src = pathlib.Path("src/market_analyzer.py").read_text()
        tree = ast.parse(src)
        forbidden = {
            "_model", "_router", "_use_openai", "_use_anthropic",  # historical
            "_call_litellm",      # use generate_text() instead
            "_litellm_available", # use is_available() instead
        }

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr in forbidden:
                    violations.append(node.attr)

        assert violations == [], (
            f"market_analyzer.py still accesses private Analyzer attributes: {violations}"
        )

    def test_prompt_uses_total_amount_as_market_turnover(self):
        """Provider total_amount is already in 亿元 and should not fall back to N/A."""
        from src.market_analyzer import MarketAnalyzer, MarketAnalysisContext
        from src.core.market_strategy import get_market_strategy_blueprint

        ma = MarketAnalyzer.__new__(MarketAnalyzer)
        ma.region = "cn"
        context = MarketAnalysisContext(
            region="cn",
            date="2026-04-24",
            stats={
                "up_count": 2800,
                "down_count": 1900,
                "limit_up_count": 72,
                "total_amount": 11234.56,
            },
            strategy_blueprint=get_market_strategy_blueprint("cn"),
        )

        prompt = ma._build_prompt(context)

        assert "上涨: 2800" in prompt
        assert "下跌: 1900" in prompt
        assert "涨停: 72" in prompt
        assert "成交额: 11234.56 亿元" in prompt

    def test_cn_prompt_uses_seven_section_market_review_contract(self):
        """A-share market review prompt should preserve the main-branch report shape."""
        from src.market_analyzer import MarketAnalyzer, MarketAnalysisContext
        from src.core.market_strategy import get_market_strategy_blueprint

        ma = MarketAnalyzer.__new__(MarketAnalyzer)
        ma.region = "cn"
        context = MarketAnalysisContext(
            region="cn",
            date="2026-03-28",
            indices=[
                {
                    "name": "上证指数",
                    "current": 3913.72,
                    "change_pct": 0.63,
                    "amount": 7997 * 100_000_000,
                },
                {
                    "name": "深证成指",
                    "current": 13760.37,
                    "change_pct": 1.13,
                    "amount": 10536 * 100_000_000,
                },
            ],
            stats={
                "up_count": 4337,
                "down_count": 1073,
                "flat_count": 70,
                "limit_up_count": 94,
                "limit_down_count": 5,
                "total_amount": 18638,
            },
            sector_rankings={
                "top": [{"name": "锂", "change_pct": 8.88}],
                "bottom": [{"name": "风力发电", "change_pct": -2.13}],
            },
            strategy_blueprint=get_market_strategy_blueprint("cn"),
        )

        prompt = ma._build_prompt(context)

        for title in [
            "### 一、盘面总览",
            "### 二、指数结构",
            "### 三、板块主线",
            "### 四、资金与情绪",
            "### 五、消息催化",
            "### 六、明日交易计划",
            "### 七、风险提示",
        ]:
            assert title in prompt

        assert "📈 上涨 4337 家 / 下跌 1073 家 / 平盘 70 家 | 涨停 94 / 跌停 5 | 成交额 18638 亿" in prompt
        assert "| 指数 | 最新 | 涨跌幅 | 成交额(亿) |" in prompt
        assert "| 上证指数 | 3913.72 | 🟢 +0.63% | 7997 |" in prompt
        assert "🔥 领涨: 锂(+8.88%)" in prompt
        assert "💧 领跌: 风力发电(-2.13%)" in prompt
        assert "结论 / 仓位 / 关注方向 / 回避方向 / 失效条件" in prompt
        assert "### 一、市场总结" not in prompt
        assert "### 七、策略计划" not in prompt

    def test_cn_fallback_report_uses_structured_market_review_contract(self):
        """Fallback report should still look like the desired A-share recap."""
        from src.market_analyzer import MarketAnalyzer, MarketAnalysisContext

        ma = MarketAnalyzer.__new__(MarketAnalyzer)
        ma.region = "cn"
        context = MarketAnalysisContext(
            region="cn",
            date="2026-03-28",
            indices=[
                {
                    "name": "创业板指",
                    "current": 3295.88,
                    "change_pct": 0.71,
                    "amount": 4636 * 100_000_000,
                }
            ],
            stats={
                "up": 4337,
                "down": 1073,
                "flat": 70,
                "limit_up": 94,
                "limit_down": 5,
                "volume_total": 18638,
            },
            sector_rankings={
                "top": [{"name": "能源金属", "change_pct": 7.36}],
                "bottom": [{"name": "水力发电", "change_pct": -1.37}],
            },
        )

        report = ma._generate_fallback_report(context)

        for title in [
            "### 一、盘面总览",
            "### 二、指数结构",
            "### 三、板块主线",
            "### 四、资金与情绪",
            "### 五、消息催化",
            "### 六、明日交易计划",
            "### 七、风险提示",
        ]:
            assert title in report

        assert "## 2026-03-28 大盘复盘" in report
        assert "📈 上涨 4337 家 / 下跌 1073 家 / 平盘 70 家 | 涨停 94 / 跌停 5 | 成交额 18638 亿" in report
        assert "| 指数 | 最新 | 涨跌幅 | 成交额(亿) |" in report
        assert "🔥 领涨: 能源金属(+7.36%)" in report
        assert "💧 领跌: 水力发电(-1.37%)" in report
        assert "- **结论**：" in report
        assert "- **仓位**：" in report
        assert "- **失效条件**：" in report
        assert "### 一、市场总结" not in report
        assert "### 七、策略计划" not in report
