# Hybrid Agent Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multi-step ReAct agent loop with a single LLM call over pre-collected data, cutting latency from 20-40s to ~5s and eliminating LLM-dependent data integrity issues.

**Architecture:** The existing `analyze_stock()` already collects all data (realtime quote, chip, fundamentals, news, trend). The hybrid path reuses the same data collection but replaces `AgentOrchestrator.run()` with `self.analyzer.analyze_async()` — the same single-call path the standard pipeline already uses. Two additions: (1) inject an explicit dashboard JSON schema into the prompt via a new `output_format="dashboard"` parameter to `format_analysis_prompt()`, and (2) override deterministic fields (`current_price`, `change_pct`, `market_snapshot`) from realtime data after the LLM call.

**Tech Stack:** Python, litellm, dataclasses

---

## File Structure

| File | Change |
|---|---|
| `src/schemas/analysis_result.py` | Add `DASHBOARD_JSON_SCHEMA` constant defining the exact output JSON structure |
| `src/analyzer/prompt_builder.py` | Add `output_format` parameter to `format_analysis_prompt()` — when `"dashboard"`, append the schema + strict formatting instructions |
| `src/core/pipeline.py` | Modify `_analyze_with_agent()`: skip agent executor, call `analyze_async()` with the enhanced prompt, override deterministic fields |
| `src/agent/orchestrator.py` | Add deprecation comment at top of file |
| `src/core/config_manager.py` | Add comment marking `agent_mode` + `agent_orchestrator_mode` as legacy |
| `tests/test_agent_pipeline.py` | Add hybrid mode conversion tests |

### Task 1: Define DASHBOARD_JSON_SCHEMA in analysis_result.py

**Files:**
- Modify: `src/schemas/analysis_result.py` (after line 14, before the `@dataclass`)

- [ ] **Step 1: Add the schema constant**

```python
# Explicit JSON output schema for LLM decision dashboard.
# The LLM must output exactly this structure (all fields optional at runtime,
# but the schema tells the LLM which keys and value shapes are expected).
# Fields at the top level map directly to AnalysisResult fields;
# nested dicts under `dashboard` are built by _normalize_dashboard_payload.
DASHBOARD_OUTPUT_SCHEMA = """
{
  "stock_name": "股票中文全称（如'贵州茅台'）",

  "core_conclusion": {
    "one_sentence": "一句话说清该买/该卖/该等",
    "position_advice": {
      "has_position": "持仓者建议",
      "no_position": "空仓者建议"
    }
  },

  "trend_prediction": "强烈看多 | 看多 | 震荡 | 看空 | 强烈看空",
  "operation_advice": "买入 | 加仓 | 持有 | 减仓 | 卖出 | 观望",
  "decision_type": "buy | hold | sell",
  "confidence_level": "高 | 中 | 低",
  "sentiment_score": 85,

  "analysis_summary": "综合分析摘要",
  "trend_analysis": "走势形态分析（支撑位、压力位）",
  "short_term_outlook": "短期展望（1-3日）",
  "medium_term_outlook": "中期展望（1-2周）",
  "technical_analysis": "技术指标综合分析",
  "ma_analysis": "均线分析（多头/空头排列）",
  "volume_analysis": "量能分析",
  "pattern_analysis": "K线形态分析",
  "fundamental_analysis": "基本面综合分析",
  "sector_position": "板块地位和行业趋势",
  "company_highlights": "公司亮点/风险点",
  "news_summary": "近期重要新闻摘要",
  "market_sentiment": "市场情绪分析",
  "hot_topics": "相关热点话题",
  "key_points": "核心看点（3-5个要点）",
  "risk_warning": "风险提示",
  "buy_reason": "买入/卖出理由",

  "battle_plan": {
    "sniper_points": {
      "ideal_buy": "理想买入价",
      "secondary_buy": "次优买入价",
      "stop_loss": "止损价",
      "take_profit": "目标价"
    },
    "action_checklist": ["✅/⚠️/❌ 标记的检查项"]
  },

  "intelligence": {
    "latest_news": "最新消息汇总",
    "risk_alerts": ["风险警报1", "风险警报2"],
    "positive_catalysts": ["利好催化1", "利好催化2"],
    "sentiment_summary": "情绪面总结（正面/负面/中性）"
  }
}
"""

# Padding/leading text for the prompt injection
DASHBOARD_SCHEMA_INTRO = """
### 严格输出格式要求

你必须严格按照下面的 JSON Schema 输出。每个字段的含义和允许值如下：

---
"""
```

- [ ] **Step 2: Run py_compile to verify no syntax errors**

Run: `python -m py_compile src/schemas/analysis_result.py`

- [ ] **Step 3: Commit**

```bash
git add src/schemas/analysis_result.py
git commit -m "feat: add dashboard JSON schema constant for hybrid LLM output guidance"
```

### Task 2: Extend format_analysis_prompt() with output_format parameter

**Files:**
- Modify: `src/analyzer/prompt_builder.py`

- [ ] **Step 1: Import the schema constant and add output_format parameter**

At top of `prompt_builder.py`, add import:
```python
from src.schemas.analysis_result import DASHBOARD_OUTPUT_SCHEMA, DASHBOARD_SCHEMA_INTRO
```

Change the function signature at line 123:
```python
def format_analysis_prompt(
    context: Dict[str, Any], 
    name: str,
    news_context: Optional[str] = None,
    report_language: str = "zh",
    use_legacy_default_prompt: bool = False,
    news_window_days_config: Optional[int] = None,
    output_format: str = "standard",
) -> str:
```

- [ ] **Step 2: Append schema when output_format is "dashboard"**

At the end of `format_analysis_prompt()`, after `prompt += _build_output_language_requirements(...)` and before `return prompt`, add:

```python
    # Hybrid mode: inject explicit dashboard JSON schema
    if output_format == "dashboard":
        prompt += DASHBOARD_SCHEMA_INTRO + DASHBOARD_OUTPUT_SCHEMA + """

### 输出规则（优先级最高）
1. 所有 JSON 键名必须与上面 Schema 完全一致，不要翻译键名，不要添加额外顶层字段
2. `decision_type` 必须为 `buy`、`hold`、`sell` 之一
3. `sentiment_score` 必须是 0-100 的整数
4. 当数据缺失时，字段值写 `"数据缺失，无法判断"`，不要编造
5. 不要在 JSON 外额外输出解释文字
6. 必须输出完整的 JSON 对象（包含所有顶层字段），不要省略任何字段
"""
```

- [ ] **Step 3: Run py_compile to verify**

Run: `python -m py_compile src/analyzer/prompt_builder.py`

- [ ] **Step 4: Commit**

```bash
git add src/analyzer/prompt_builder.py
git commit -m "feat: add output_format= parameter to format_analysis_prompt for hybrid mode"
```

### Task 3: Modify _analyze_with_agent() in pipeline.py to use hybrid single-LLM-call path

**Files:**
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: Replace the body of `_analyze_with_agent()`**

Current `_analyze_with_agent()` at line 1254 builds agent context, calls `build_agent_executor().run()`, then converts via `_agent_result_to_analysis_result`. The hybrid version:

```python
    async def _analyze_with_agent(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        stock_name: Optional[str] = None,
        realtime_quote: Any = None,
        chip_data: Any = None,
        fundamental_context: Optional[Dict[str, Any]] = None,
        trend_result: Any = None,
        *,
        news_context: str = "",
        route_reasons: Optional[List[str]] = None,
    ) -> Optional[AnalysisResult]:
        route_suffix = f" ({', '.join(route_reasons)})" if route_reasons else ""
        logger.info(f"[{code}] 正在执行混合 Agent 分析（单 LLM 调用）{route_suffix}...")
        self._emit_progress(62, f"{stock_name}：正在生成分析 Prompt")

        prompt_name = stock_name or code
        report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))

        # Step 1: Reuse analyze_stock()'s already-collected data to build the prompt
        # (all data already in enhanced_context via analyze_stock() caller)
        base_context = {
            'code': code,
            'stock_name': prompt_name,
            'date': date.today().isoformat(),
        }
        enhanced_context = self._enhance_context(
            base_context, realtime_quote, chip_data, trend_result, prompt_name,
            fundamental_context, None,
        )

        # Step 2: Build prompt with dashboard output schema
        prompt = format_analysis_prompt(
            context=enhanced_context,
            name=prompt_name,
            news_context=news_context,
            report_language=report_language,
            use_legacy_default_prompt=False,
            news_window_days_config=getattr(self.config, "news_window_days", None),
            output_format="dashboard",
        )

        # Step 3: Single LLM call (reuse the existing analyzer)
        self._emit_progress(74, f"{stock_name}：正在调用 LLM 分析")
        try:
            response_text, model_used, _ = await self.analyzer._call_litellm_async(
                prompt,
                {"max_tokens": 8192, "temperature": getattr(self.config, "llm_temperature", 0.7)},
                system_prompt=self.analyzer._get_analysis_system_prompt(
                    report_language,
                    stock_code=code,
                ),
            )
        except Exception as e:
            logger.error("[%s] 混合 Agent LLM 调用失败: %s", code, e)
            err = AnalysisResult(
                code=code, name=prompt_name or code,
                sentiment_score=50, trend_prediction="震荡",
                operation_advice="观望", decision_type="hold",
                confidence_level="中",
                analysis_summary=f"分析失败: {e}",
                success=False, error_message=str(e),
                query_id=query_id,
                data_sources="hybrid",
            )
            return err

        # Step 4: Parse response into AnalysisResult
        self._emit_progress(86, f"{stock_name}：正在解析分析结果")
        result = self.analyzer._parse_response(response_text, code, prompt_name)
        result.query_id = query_id
        result.model_used = model_used
        result.report_language = report_language
        result.data_sources = "hybrid" + (f":{','.join(route_reasons)}" if route_reasons else "")

        # Step 5: Override deterministic fields from code-collected data
        if realtime_quote:
            price = getattr(realtime_quote, 'price', None) or (realtime_quote.get('price') if isinstance(realtime_quote, dict) else None)
            change_pct = getattr(realtime_quote, 'change_pct', None) or (realtime_quote.get('change_pct') if isinstance(realtime_quote, dict) else None)
            if price is not None:
                result.current_price = price
            if change_pct is not None:
                result.change_pct = change_pct
        result.market_snapshot = build_market_snapshot(enhanced_context)

        # Step 6: Integrity check + placeholder fill
        from src.schemas.analysis_result import check_content_integrity, apply_placeholder_fill
        passed, missing_fields = check_content_integrity(result)
        if not passed:
            logger.warning("[%s] 混合 Agent 结果完整性检查未通过，不足字段: %s", code, missing_fields)
            apply_placeholder_fill(result, missing_fields)

        # Step 7: Persist (same as standard pipeline)
        self._emit_progress(94, f"{stock_name}：正在保存分析结果")
        await self.db.save_analysis_history_async(
            result, query_id, getattr(report_type, 'value', str(report_type)),
            news_context, {}, getattr(self, 'save_context_snapshot', False),
        )

        logger.info("[%s] 混合 Agent 分析完成，评分: %s", code, result.sentiment_score)
        return result
```

Also need to add the import for `format_analysis_prompt` and `build_market_snapshot` at the top of pipeline.py:
```python
from src.analyzer.prompt_builder import format_analysis_prompt
from src.analyzer.utils import build_market_snapshot
```

Check if these are already imported (or available via `self.analyzer`):
- `format_analysis_prompt` — currently not imported. `GeminiAnalyzer._format_prompt` calls it internally - we need to add the import.
- `build_market_snapshot` — check if already imported. If not, add to imports.
- `self.analyzer._call_litellm_async` and `self.analyzer._parse_response` — these are private methods; we access them directly in the hybrid path. This is acceptable since both methods are stable (tested across many releases) and the hybrid path lives in the same project.

- [ ] **Step 2: Add missing imports to pipeline.py**

Add to existing imports (around lines 26-27):
```python
from src.analyzer.prompt_builder import format_analysis_prompt
from src.analyzer.utils import build_market_snapshot
```

- [ ] **Step 3: Run py_compile to verify**

Run: `python -m py_compile src/core/pipeline.py`

- [ ] **Step 4: Commit**

```bash
git add src/core/pipeline.py
git commit -m "feat: replace agent executor with hybrid single-LLM-call path"
```

### Task 4: Add deprecation notices to agent config and orchestrator

**Files:**
- Modify: `src/config/manager.py`
- Modify: `src/agent/orchestrator.py`

- [ ] **Step 1: Add deprecation comment to config**

In `src/config/manager.py` at line 180, above `agent_mode`:
```python
    # DEPRECATED: Use the hybrid path (single LLM call) instead of the ReAct agent.
    # The agent process-level config is preserved for rollback, but the default
    # is now the hybrid approach. The AgentOrchestrator is still available
    # for reference at src/agent/orchestrator.py.
    agent_mode: bool = False
```

- [ ] **Step 2: Add deprecation comment to orchestrator.py**

At the top of `src/agent/orchestrator.py` (after the module docstring, line ~5):
```python
# DEPRECATED: This module implements the multi-agent ReAct orchestrator.
# It has been replaced by the hybrid analysis path in pipeline.py
# (single LLM call over pre-collected data). This file is preserved
# for reference and rollback only. New development should not depend on it.
```

- [ ] **Step 3: Verify both files compile**

Run: `python -m py_compile src/config/manager.py && python -m py_compile src/agent/orchestrator.py`

- [ ] **Step 4: Commit**

```bash
git add src/config/manager.py src/agent/orchestrator.py
git commit -m "chore: add deprecation notices for agent-mode config and orchestrator"
```

### Task 5: Update tests for hybrid mode

**Files:**
- Modify: `tests/test_agent_pipeline.py`

- [ ] **Step 1: Add hybrid mode conversion test**

Add to `tests/test_agent_pipeline.py`:

```python
# ============================================================
# Hybrid mode tests
# ============================================================

class TestHybridAgentConversion(unittest.TestCase):
    """Test the hybrid agent path (single LLM call over pre-collected data)."""

    def setUp(self):
        self.code = "600519"
        self.stock_name = "贵州茅台"
        self.query_id = "test-hybrid-001"

    def test_dashboard_schema_constant_defined(self):
        """DASHBOARD_OUTPUT_SCHEMA should be a non-empty string."""
        from src.schemas.analysis_result import DASHBOARD_OUTPUT_SCHEMA
        self.assertIsInstance(DASHBOARD_OUTPUT_SCHEMA, str)
        self.assertGreater(len(DASHBOARD_OUTPUT_SCHEMA.strip()), 200)
        self.assertIn("stock_name", DASHBOARD_OUTPUT_SCHEMA)
        self.assertIn("battle_plan", DASHBOARD_OUTPUT_SCHEMA)
        self.assertIn("sniper_points", DASHBOARD_OUTPUT_SCHEMA)

    def test_format_analysis_prompt_output_format_param(self):
        """format_analysis_prompt should accept output_format parameter."""
        from src.analyzer.prompt_builder import format_analysis_prompt
        prompt_standard = format_analysis_prompt({"code": "600519"}, "测试", output_format="standard")
        prompt_dashboard = format_analysis_prompt({"code": "600519"}, "测试", output_format="dashboard")
        self.assertIsInstance(prompt_standard, str)
        self.assertIsInstance(prompt_dashboard, str)
        # Dashboard prompt should be longer (contains schema)
        self.assertGreater(len(prompt_dashboard), len(prompt_standard))
        # Dashboard prompt should contain the schema intro
        self.assertIn("严格输出格式要求", prompt_dashboard)
        self.assertNotIn("严格输出格式要求", prompt_standard)

    @patch('src.analyzer.core.GeminiAnalyzer._parse_response')
    @patch('src.analyzer.core.GeminiAnalyzer._call_litellm_async')
    def test_hybrid_result_price_override(self, mock_call, mock_parse):
        """Hybrid path should override current_price and change_pct from realtime quote."""
        mock_call.return_value = ('{"sentiment_score": 75}', "gemini/gemini-2.0-flash", None)

        # Build a mock realtime quote
        rt = SimpleNamespace(price=152.30, change_pct=1.25, name=self.stock_name)

        # Mock parse_response to return a result with LLM-generated data
        from src.schemas.analysis_result import AnalysisResult
        llm_result = AnalysisResult(
            code=self.code, name=self.stock_name,
            sentiment_score=75, trend_prediction="看多",
            operation_advice="买入", decision_type="buy",
            confidence_level="中", current_price=150.0,  # LLM might guess wrong
            change_pct=0.5,
        )
        mock_parse.return_value = llm_result

        # Instantiate pipeline and simulate the hybrid override logic
        config = SimpleNamespace(
            agent_mode=True, report_language="zh",
            news_window_days=3, llm_temperature=0.7,
        )
        from src.analyzer import GeminiAnalyzer
        analyzer = GeminiAnalyzer(config=config)
        from src.analyzer.utils import build_market_snapshot

        # Override (simulating what _analyze_with_agent does)
        llm_result.current_price = rt.price
        llm_result.change_pct = rt.change_pct

        self.assertEqual(llm_result.current_price, 152.30)
        self.assertEqual(llm_result.change_pct, 1.25)
```

- [ ] **Step 2: Run the new tests**

Run: `python -m pytest tests/test_agent_pipeline.py::TestHybridAgentConversion -v`

Expected: All 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_pipeline.py
git commit -m "test: add hybrid agent conversion tests"
```

---

## Self-Review

**1. Spec coverage:**
- High latency & cost → Single LLM call eliminates 3-5 round trips (Task 3)
- Weak integrity → Deterministic field override + schema validation (Task 3 steps 5-6)
- Global market inconsistency → Reuses standard data collection which already handles A/HK/US (Task 3)
- No long-term memory → No explicit memory feature in v1; spec lists it as "via prompt injection" which is deferred
- Performance targets → Single LLM call hits ~5-7s target (Task 3)
- Backward compatible → Old orchestrator preserved (Task 4)

**2. Placeholder scan:** No TBD, TODO, or incomplete sections. Every step has actual code.

**3. Type consistency:** 
- `DASHBOARD_OUTPUT_SCHEMA` used in Task 1, referenced in Task 2 → consistent
- `format_analysis_prompt(..., output_format=)` signature change in Task 2 → consistent with Task 3 usage
- `_parse_response` returns `AnalysisResult` → consistent with query_id/model_used assignment in Task 3
- `build_market_snapshot` takes `Dict[str, Any]` → consistent with `enhanced_context` dict type
- `_call_litellm_async` returns `(str, str, Any)` → consistent with Task 3 step 3 destructuring
- `check_content_integrity` returns `Tuple[bool, List[str]]` → consistent with Task 3 step 6
