# -*- coding: utf-8 -*-
"""
RiskAgent — dedicated risk screening specialist.

Responsible for:
- Scanning for insider sell-downs, earnings warnings, regulatory actions
- Checking valuation anomalies (PE/PB extremes)
- Evaluating lock-up expiration risks
- Producing risk flags that can override or downgrade signals from other agents

Risk flags use a two-level severity system:
- **soft**: downgrades the signal and adds a visible warning
- **hard**: vetoes buy signals entirely when risk override is enabled
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)


class RiskAgent(BaseAgent):
    agent_name = "risk"
    max_steps = 4
    tool_names = [
        "search_stock_news",
        "get_realtime_quote",
        "get_stock_info",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return """\
You are a **Risk Screening Agent**. Identify all risk factors and output a JSON assessment.

## Mandatory Risk Checks
1. Insider / Major Shareholder — sell-downs (减持), pledges
2. Earnings — pre-loss, downward revisions (业绩预亏)
3. Regulatory — penalties, investigations (监管处罚, 立案调查)
4. Industry Policy — headwinds, sector crackdowns
5. Lock-up Expirations — large unlocks within 30 days (解禁)
6. Valuation Extremes — PE > 100 or negative, PB > 10
7. Technical Warning Signs — death crosses, broken supports

## Severity
- "high": existential or material (fraud, massive insider selling)
- "medium": significant (earnings miss, lock-up, sector headwind)
- "low": minor or informational

## Evidence Policy
- If structured Intel data is already provided, treat it as the primary evidence set.
- Only call `search_stock_news` when one or more mandatory risk checks still lacks
  dated, source-backed evidence, or when you need to confirm a very recent breaking risk.
- Reuse dates and sources from upstream Intel items instead of paraphrasing them away.

## Output Format
Return **only** a JSON object:
{
  "risk_level": "high|medium|low|none",
  "risk_score": 0-100,
  "flags": [
    {
      "category": "insider|earnings|regulatory|industry|lockup|valuation|technical",
      "severity": "high|medium|low",
      "description": "Clear description of the risk",
      "source": "Where this information came from"
    }
  ],
  "reasoning": "2-3 sentence overall risk assessment"
}
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        parts = [f"Screen stock **{ctx.stock_code}**"]
        if ctx.stock_name:
            parts[0] += f" ({ctx.stock_name})"
        parts.append("for ALL risk factors listed in your instructions.")
        parts.append(
            "Use existing Intel evidence first. Only search for latest news when a mandatory"
            " risk category is still missing dated, source-backed support."
        )

        # Feed any existing intel data so the risk agent doesn't redo searches
        if ctx.get_data("intel_opinion"):
            parts.append(f"\n[Existing intel data]\n{json.dumps(ctx.get_data('intel_opinion'), ensure_ascii=False, default=str)}")

        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[RiskAgent] failed to parse risk JSON")
            return None

        # Propagate structured risk flags to context
        for flag in parsed.get("flags", []):
            if isinstance(flag, dict):
                ctx.add_risk_flag(
                    category=flag.get("category", "unknown"),
                    description=flag.get("description", ""),
                    severity=flag.get("severity", "medium"),
                )

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=_risk_to_signal(parsed.get("risk_level", "none")),
            confidence=float(parsed.get("risk_score", 50)) / 100.0,
            reasoning=parsed.get("reasoning", ""),
            raw_data=parsed,
        )


def _risk_to_signal(risk_level: str) -> str:
    """Map risk level to a trading signal (inverted)."""
    mapping = {
        "none": "buy",
        "low": "hold",
        "medium": "sell",
        "high": "strong_sell",
    }
    return mapping.get(risk_level, "hold")
