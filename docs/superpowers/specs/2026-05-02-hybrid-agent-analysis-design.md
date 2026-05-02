# Hybrid Agent Analysis: Structured Data Pipeline + Single LLM Call

**Date:** 2026-05-02
**Status:** Approved for implementation

## Problem

The current Agent-based analysis pipeline has four interconnected problems:

1. **High latency & cost** — ReAct loop (think → act → observe) requires 3-5 LLM calls per stock, yielding 20-40s analysis time vs ~5s for the standard pipeline
2. **Weak integrity** — LLM generates the decision dashboard JSON directly; can skip fields or hallucinate values despite json_repair
3. **Global market inconsistency** — Agent tools (chip distribution, A-share deep review) only work for A-shares; HK/US stocks degenerate to a news-search agent
4. **No long-term memory** — Each analysis is stateless; no cross-session recall of prior judgments

## Solution: Approach A — Hybrid

Replace the ReAct agent loop with a two-phase architecture:

**Phase 1 — Code collects all data (no change):** Reuse the existing `pipeline.analyze_stock` data collection logic which already handles A/HK/US markets deterministically.

**Phase 2 — Single LLM call for analysis:** Inject all collected structured data into one prompt and let the LLM produce analysis text + a decision dashboard JSON in a single response.

## Design

### Data flow

```
pipeline.analyze_stock(code)
  │
  ├─ [code] get_realtime_quote        ← 1-2s parallel
  ├─ [code] get_chip_distribution
  ├─ [code] get_daily_data (45d)
  ├─ [code] search news (multi-dimension)
  └─ [code] get_analysis_context (DB)
  │
  └─ single LLM call:
       prompt = format_analysis_prompt(
           realtime_quote, chip, df, news, context,
           output_format="analysis_result + dashboard",
           market_role=get_market_role(code),   ← auto-select A/HK/US
       )
       response = llm.generate(prompt)
  │
  ├─ [code] parse response → AnalysisResult
  ├─ [code] override deterministic fields:
  │   current_price = realtime_quote.price
  │   change_pct    = realtime_quote.change_pct
  │   market_snapshot = build_market_snapshot(realtime_quote, chip, df)
  └─ [code] schema validation + placeholder fill
  │
  └─ return AnalysisResult (dashboard field populated)
```

### Files changed

| File | Change |
|---|---|
| `src/core/pipeline.py` | Modify `_run_agent_pipeline()`: replace Orchestrator call with shared data collection + single LLM call |
| `src/analyzer/prompt_builder.py` | Extend `format_analysis_prompt()` to include dashboard JSON output schema |
| `src/schemas/analysis_result.py` | Define explicit JSON schema for `dashboard` field |
| `src/agent/orchestrator.py` | Deprecate; leave in place for reference |

### Files unchanged

All `data_provider/`, notification/report modules, `src/market_analyzer.py`, other `src/agent/` modules.

### Key design decisions

1. **Data collection stays in code** — guarantees correctness, speed, and multi-market support
2. **Single LLM call** — eliminates the ReAct loop overhead entirely
3. **Deterministic field override** — price/change/market-snapshot set by code after LLM returns, not by the LLM
4. **Existing integrity checks apply** — `check_content_integrity` + `apply_placeholder_fill` handle missing dashboard fields
5. **Memory via prompt injection** — recent analysis results and win-rate stats injected as text, no extra call
6. **Backward compatible** — `AnalysisResult` schema unchanged; agent-orchestrator path can coexist via config flag

### Performance targets

| Metric | Current (Agent) | Target (Hybrid) |
|---|---|---|
| Time per stock | 20-40s | 5-7s |
| LLM calls per stock | 3-5 | 1 |
| Data integrity | LLM-dependent | Code-guaranteed |
| Multi-market (US/HK) | Degraded | Native (same as standard) |

## Implementation order

1. Add dashboard JSON schema to `analysis_result.py`
2. Extend `format_analysis_prompt()` with dashboard output format
3. Modify `_run_agent_pipeline()` in `pipeline.py` to use hybrid path
4. Update config flag and deprecation notice
5. Update tests

## Risk and rollback

- **Risk:** LLM single-call analysis may be less nuanced than multi-agent debate
  - **Mitigation:** Prompt design can encode multiple perspectives (technical, fundamental, sentiment) in a single response
- **Rollback:** Agent mode config flag (`config.agent_mode`) falls back to standard pipeline; orchestrator code remains intact
