# -*- coding: utf-8 -*-
"""
TraderAgent — dedicated trading decision specialist (TradingAgents style).

Responsibilities:
- Consumes opinions from Technical + Intel + Risk agents
- Produces a final trading decision with position sizing
- Generates actionable BUY/SELL/HOLD signals with precise entry/exit levels
- Provides holding period and rationale
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json
from src.report_language import normalize_report_language

logger = logging.getLogger(__name__)


class TraderAgent:
    """Trading decision agent that synthesizes all prior analysis."""

    agent_name = "trader"

    def __init__(self, analyzer: Any, config: Any):
        self.analyzer = analyzer
        self.config = config

    async def run(self, ctx: AgentContext) -> Optional[AgentOpinion]:
        """Run trader agent and return structured opinion."""
        report_language = normalize_report_language(
            ctx.meta.get("report_language", "zh")
        )

        system_prompt = self._build_system_prompt(report_language)
        user_message = self._build_user_message(ctx, report_language)

        try:
            response_text, model_used, _ = await self.analyzer._call_litellm_async(
                user_message,
                {"max_tokens": 2048, "temperature": 0.3},
                system_prompt=system_prompt,
            )
        except Exception as e:
            logger.error("[TraderAgent] LLM call failed: %s", e)
            return None

        return self._post_process(response_text, report_language)

    def _build_system_prompt(self, report_language: str) -> str:
        prompt = """\
You are a **Trader Agent** that makes final trading decisions.

You will receive:
1. Technical analysis opinion (trend, indicators, patterns)
2. Intelligence analysis (news, sentiment, catalysts)
3. Risk assessment flags (severity, categories, descriptions)
4. Portfolio context (current positions, available cash, risk tolerance)

## Your Output: Trading Decision Dashboard

Produce a JSON object with:

```json
{
  "signal": "buy|sell|hold",
  "conviction": "high|medium|low",
  "position_sizing": {
    "recommended_pct": 30,
    "max_position_pct": 40,
    "reasoning": "Strong fundamentals + technical breakout"
  },
  "entry_plan": {
    "ideal_entry": 188.50,
    "secondary_entry": 185.00,
    "current_price": 190.20,
    "entry_strategy": "Scale in: 50% at ideal, 50% at secondary"
  },
  "exit_plan": {
    "stop_loss": 180.00,
    "take_profit_1": 210.00,
    "take_profit_2": 230.00,
    "trailing_stop_pct": 8.0
  },
  "holding_period": {
    "expected_days": 15,
    "time_horizon": "short_term",
    "rationale": "Awaiting Q3 earnings catalyst"
  },
  "risk_assessment": {
    "max_loss_pct": 5.0,
    "risk_reward_ratio": 3.2,
    "portfolio_risk_impact": "medium"
  },
  "rationale": "Strong technical breakout with positive news catalyst...",
  "key_triggers": ["Earnings beat", "Sector rotation continues"]
}
```

## Rules
- **Risk override**: If high-severity risk flags exist, cap signal at "hold" or "sell"
- **Position sizing**: New position ≤ 40%, additional position ≤ 25%
- **Stop loss**: Must be provided for all BUY/SELL signals
- **Holding period**: Provide expected duration (days) and time horizon
"""
        if report_language == "en":
            return prompt + "\n\n## Output Language\n- All JSON values in English.\n"
        return prompt + "\n\n## 输出语言\n- 所有 JSON 值必须使用中文。\n"

    def _build_user_message(self, ctx: AgentContext, report_language: str) -> str:
        parts = [
            f"# Trading Decision Request for {ctx.stock_code}",
            f"Stock: {ctx.stock_code} ({ctx.stock_name})" if ctx.stock_name else f"Stock: {ctx.stock_code}",
            "",
        ]

        # Feed prior opinions
        if ctx.opinions:
            parts.append("## Prior Agent Opinions")
            for op in ctx.opinions:
                parts.append(f"\n### {op.agent_name}")
                parts.append(f"Signal: {op.signal} | Confidence: {op.confidence:.2f}")
                parts.append(f"Reasoning: {op.reasoning}")
                if op.raw_data:
                    extra_keys = {k: v for k, v in op.raw_data.items()
                                  if k not in ("signal", "confidence", "reasoning")}
                    if extra_keys:
                        parts.append(f"Extra: {json.dumps(extra_keys, ensure_ascii=False, default=str)}")
                parts.append("")

        # Feed risk flags
        if ctx.risk_flags:
            parts.append("## Risk Flags")
            for rf in ctx.risk_flags:
                parts.append(f"- [{rf.get('severity', 'medium')}] {rf.get('category', '')}: {rf.get('description', '')}")
            parts.append("")

        # Portfolio context
        portfolio_ctx = ctx.meta.get("portfolio_context", {})
        if portfolio_ctx:
            parts.append("## Portfolio Context")
            parts.append(json.dumps(portfolio_ctx, ensure_ascii=False, default=str))
            parts.append("")

        parts.append("Synthesize into Trading Decision Dashboard JSON.")
        return "\n".join(parts)

    def _post_process(self, raw_text: str, report_language: str) -> Optional[AgentOpinion]:
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[TraderAgent] failed to parse decision JSON")
            return None

        # Store trader decision in context if available
        # (context is passed separately)

        # Map conviction to confidence
        conviction_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
        confidence = conviction_map.get(str(parsed.get("conviction", "medium")).lower(), 0.6)

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=parsed.get("signal", "hold"),
            confidence=confidence,
            reasoning=parsed.get("rationale", ""),
            raw_data=parsed,
        )
