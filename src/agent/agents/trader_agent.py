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
5. Current market price (from context meta)

## Your Output: Trading Decision Dashboard

Produce a JSON object with:

```json
{
  "signal": "buy|sell|hold",
  "conviction": "high|medium|low",
  "position_sizing": {
    "recommended_pct": <CALCULATE_BASED_ON_SIGNAL_AND_RISK>,
    "max_position_pct": <CALCULATE_MAX>,
    "reasoning": "<EXPLAIN_SIZING_DECISION>"
  },
  "entry_plan": {
    "ideal_entry": <CALCULATE_FROM_TECHNICAL>,
    "secondary_entry": <CALCULATE_SUPPORT_LEVEL>,
    "current_price": <MUST_USE_ACTUAL_PRICE_FROM_META>,
    "entry_strategy": "<DESCRIBE_ENTRY_STRATEGY>"
  },
  "exit_plan": {
    "stop_loss": <CALCULATE_FROM_ATR_OR_SUPPORT>,
    "take_profit_1": <CALCULATE_TARGET_1>,
    "take_profit_2": <CALCULATE_TARGET_2>,
    "trailing_stop_pct": <CALCULATE_TRAILING>
  },
  "holding_period": {
    "expected_days": <CALCULATE_BASED_ON_CATALYSTS_AND_TREND>,
    "time_horizon": "<short_term|medium_term|long_term>",
    "rationale": "<EXPLAIN_HOLDING_PERIOD>"
  },
  "risk_assessment": {
    "max_loss_pct": <CALCULATE_MAX_LOSS>,
    "risk_reward_ratio": <CALCULATE_RR_RATIO>,
    "portfolio_risk_impact": "<low|medium|high>"
  },
  "rationale": "<EXPLAIN_DECISION_BASED_ON_ALL_INPUTS>",
  "key_triggers": ["<CATALYST_1>", "<CATALYST_2>"]
}
```

## Critical Rules
- **MUST use actual market data**: All price fields (current_price, ideal_entry, stop_loss, etc.) MUST be calculated from the real-time quote provided in context meta, NOT from examples
- **NO copy-paste from examples**: The placeholder values like <CALCULATE_...> are NOT real values - replace them with actual calculations
- **Current price**: ALWAYS use the actual current_price from context meta, never guess or use example values like 190.20
- **Holding period**: Calculate expected_days based on actual technical patterns and catalysts, NOT example values like 15

## Trading Rules
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

        # Inject real-time price from meta
        current_price = ctx.meta.get("current_price")
        yesterday_close = ctx.meta.get("yesterday_close")
        if current_price is not None:
            parts.append(f"## Real-Time Market Data")
            parts.append(f"- **Current Price**: ¥{current_price:.2f}")
            if yesterday_close is not None:
                change = current_price - yesterday_close
                change_pct = (change / yesterday_close * 100) if yesterday_close else 0
                parts.append(f"- **Yesterday Close**: ¥{yesterday_close:.2f}")
                parts.append(f"- **Change**: ¥{change:+.2f} ({change_pct:+.2f}%)")
            parts.append("")
            parts.append("**IMPORTANT**: You MUST use the current price above (¥{:.2f}) for all price calculations. Do NOT use example values like 190.20.".format(current_price))
            parts.append("")

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
