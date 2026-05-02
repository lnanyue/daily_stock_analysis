# -*- coding: utf-8 -*-
"""
DecisionAgent — final synthesis and decision-making specialist.

Responsible for:
- Aggregating opinions from technical + intel + risk + skill agents
- Producing the final Decision Dashboard JSON
- Generating actionable buy/hold/sell recommendations with price levels
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion, normalize_decision_signal
from src.report_language import normalize_report_language

logger = logging.getLogger(__name__)


class DecisionAgent(BaseAgent):
    """Synthesise prior agent opinions into the final dashboard."""

    agent_name = "decision"
    max_steps = 3  # pure synthesis, should not need many tool calls
    tool_names: Optional[List[str]] = []  # no tool access — works from context only

    @staticmethod
    def _is_chat_mode(ctx: AgentContext) -> bool:
        return ctx.meta.get("response_mode") == "chat"

    def system_prompt(self, ctx: AgentContext) -> str:
        report_language = normalize_report_language(ctx.meta.get("report_language", "zh"))
        if self._is_chat_mode(ctx):
            prompt = """\
You are a **Decision Synthesis Agent** replying directly to the user's latest
stock-analysis question.

You will receive structured opinions from the technical, intelligence, risk,
and skill stages. Synthesize them into a concise, natural-language answer.

Requirements:
- Answer the user's actual question directly
- Use Markdown when helpful
- Keep the response practical and specific
- Highlight the main signal, key reasoning, and major risks
- Do NOT output JSON or code fences unless the user explicitly asks for them
"""
            if report_language == "en":
                return prompt + "\nAlways answer in English.\n"
            return prompt + "\n默认使用中文回答。\n"

        skills = ""
        if self.skill_instructions:
            skills = f"\n## Active Trading Skills\n\n{self.skill_instructions}\n"

        prompt = f"""\
You are a **Decision Synthesis Agent** that produces the final investment \
Decision Dashboard.

You will receive:
1. Structured opinions from a Technical Agent and an Intel Agent
2. Any risk flags raised by a Risk Agent
3. Skill evaluation results (if applicable)

Your task: synthesise all inputs into a single, actionable Decision Dashboard.
{skills}
## Principles
- One-sentence core conclusion first (<=30 chars)
- Split advice: no-position vs has-position
- Use precise price levels only when upstream evidence clearly provides them
- If support/resistance/entry levels are missing or conflicting, output `N/A`
  for that field and explain which data is missing instead of inventing a price
- Risk alert: high-severity risk caps signal at "hold"

## Scoring (0-100)
>=60 buy | 40-59 hold | <40 sell

## Output Format
Valid JSON with keys: stock_name, sentiment_score, trend_prediction,
operation_advice, decision_type (buy|hold|sell only),
confidence_level, dashboard, analysis_summary, key_points, risk_warning
"""
        if report_language == "en":
            return prompt + """

## Output Language
- Keep every JSON key unchanged.
- Write all human-readable JSON values in English.
"""
        return prompt + """

## 输出语言
- 所有 JSON 键名保持不变。
- 所有面向用户的人类可读文本值必须使用中文。
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        if self._is_chat_mode(ctx):
            parts = [
                "# User Question",
                ctx.query,
                "",
                f"Stock: {ctx.stock_code} ({ctx.stock_name})" if ctx.stock_name else f"Stock: {ctx.stock_code}",
                "",
            ]
        else:
            parts = [
                f"# Synthesis Request for {ctx.stock_code}",
                f"Stock: {ctx.stock_code} ({ctx.stock_name})" if ctx.stock_name else f"Stock: {ctx.stock_code}",
                "",
            ]

        # Feed prior opinions
        if ctx.opinions:
            parts.append("## Agent Opinions")
            for op in ctx.opinions:
                parts.append(f"\n### {op.agent_name}")
                parts.append(f"Signal: {op.signal} | Confidence: {op.confidence:.2f}")
                parts.append(f"Reasoning: {op.reasoning}")
                if op.key_levels:
                    parts.append(f"Key levels: {json.dumps(op.key_levels)}")
                if op.raw_data:
                    extra_keys = {k: v for k, v in op.raw_data.items()
                                  if k not in ("signal", "confidence", "reasoning", "key_levels")}
                    if extra_keys:
                        parts.append(f"Extra data: {json.dumps(extra_keys, ensure_ascii=False, default=str)}")
                parts.append("")

        # Feed risk flags
        if ctx.risk_flags:
            parts.append("## Risk Flags")
            for rf in ctx.risk_flags:
                parts.append(f"- [{rf.get('severity', 'medium')}] {rf.get('category', '')}: {rf.get('description', '')}")
            parts.append("")

        # Skill meta
        requested_skills = ctx.meta.get("skills_requested") or ctx.meta.get("strategies_requested")
        if requested_skills:
            parts.append(f"## Skills: {', '.join(requested_skills)}")
            parts.append("")

        if self._is_chat_mode(ctx):
            parts.append(
                "Answer the user in natural language using the evidence above. "
                "Do not output JSON unless the user explicitly requests structured data."
            )
        else:
            parts.append("Synthesise the above into the Decision Dashboard JSON.")
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        """Store the parsed dashboard in ctx.meta; also return an opinion."""
        if self._is_chat_mode(ctx):
            text = (raw_text or "").strip()
            if not text:
                return None

            ctx.set_data("final_response_text", text)
            prior = next((op for op in reversed(ctx.opinions) if op.agent_name != self.agent_name), None)
            return AgentOpinion(
                agent_name=self.agent_name,
                signal=prior.signal if prior is not None else "hold",
                confidence=prior.confidence if prior is not None else 0.5,
                reasoning=text,
                raw_data={"response_mode": "chat"},
            )

        from src.agent.runner import parse_dashboard_json

        dashboard = parse_dashboard_json(raw_text)
        if dashboard:
            dashboard["decision_type"] = normalize_decision_signal(
                dashboard.get("decision_type", "hold")
            )
            ctx.set_data("final_dashboard", dashboard)
            try:
                _raw_score = dashboard.get("sentiment_score", 50) or 50
                _score = float(_raw_score)
            except (TypeError, ValueError):
                _score = 50.0
            return AgentOpinion(
                agent_name=self.agent_name,
                signal=dashboard.get("decision_type", "hold"),
                confidence=min(1.0, _score / 100.0),
                reasoning=dashboard.get("analysis_summary", ""),
                raw_data=dashboard,
            )
        else:
            # Even if JSON parsing fails, store the raw text for downstream use
            ctx.set_data("final_dashboard_raw", raw_text)
            logger.warning("[DecisionAgent] failed to parse dashboard JSON")
            return None
