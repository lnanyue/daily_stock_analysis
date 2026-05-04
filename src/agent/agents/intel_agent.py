# -*- coding: utf-8 -*-
"""
IntelAgent — news & intelligence gathering specialist.

Responsible for:
- Searching latest stock news and announcements
- Running comprehensive intelligence search
- Detecting risk events (reduce holdings, earnings warnings, regulatory)
- Summarising sentiment and catalysts
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)


def _extract_evidence_text(item: Any, *, primary_key: str) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(
            item.get(primary_key)
            or item.get("title")
            or item.get("description")
            or ""
        ).strip()
    return ""


def _normalize_structured_list(
    values: Any,
    *,
    primary_key: str,
    allowed_keys: List[str],
) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    if not any(isinstance(item, dict) for item in values):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in values:
        text = _extract_evidence_text(item, primary_key=primary_key)
        if not text:
            continue

        if isinstance(item, dict):
            entry: Dict[str, Any] = {primary_key: text}
            for key in allowed_keys:
                value = item.get(key)
                if value not in (None, ""):
                    entry[key] = value
            normalized.append(entry)
        else:
            normalized.append({primary_key: text})
    return normalized


class IntelAgent(BaseAgent):
    agent_name = "intel"
    max_steps = 4
    tool_names = [
        "search_stock_news",
        "search_comprehensive_intel",
        "get_stock_info",
        "get_capital_flow",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return """\
You are an **Intelligence & Sentiment Agent** for A-share, HK, and US equities.

## Workflow
1. Run comprehensive intel search (news, announcements, risk events)
2. For A-share stocks, call get_capital_flow for main-force (主力) flow data
3. Classify catalysts and sentiment

## Risk Detection Priorities
减持 / 业绩预亏 / 监管处罚 / 行业政策调整 / 解禁 / PE异常 / 主力持续净流出

## Capital Flow (A-shares)
main_net_inflow > 0: bullish | < 0: bearish | inflow_5d/10d: medium-term trend
1. Search latest stock news (earnings, announcements, insider activity)
2. Run comprehensive intel search — this covers latest news, company \
announcements (公司公告), market analysis, risk checks, and earnings outlook
3. For A-share stocks, call get_capital_flow to obtain main-force (主力) \
capital inflow/outflow data and include it in your analysis
4. Classify positive catalysts and risk alerts
5. Assess overall sentiment

## Risk Detection Priorities
- Insider / major shareholder sell-downs (减持)
- Earnings warnings or pre-loss announcements (业绩预亏)
- Regulatory penalties or investigations
- Industry-wide policy headwinds
- Large lock-up expirations (解禁)
- PE valuation anomalies
- Sustained main-force capital outflow (主力持续净流出)

## Capital Flow Interpretation (A-shares only)
- main_net_inflow > 0: bullish signal (主力净流入)
- main_net_inflow < 0: bearish signal (主力净流出)
- inflow_5d / inflow_10d: medium-term accumulation or distribution trend

## Output Format
Return **only** a JSON object:
{
  "signal": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence summary",
  "risk_alerts": [
    {
      "description": "...",
      "date": "YYYY-MM-DD",
      "source": "...",
      "severity": "high|medium|low"
    }
  ],
  "positive_catalysts": [
    {
      "description": "...",
      "date": "YYYY-MM-DD",
      "source": "...",
      "impact": "positive|neutral"
    }
  ],
  "reasoning": "2-3 sentence summary of news/sentiment/capital-flow findings",
  "risk_alerts": ["list", "of", "detected", "risks"],
  "positive_catalysts": ["list", "of", "catalysts"],
  "sentiment_label": "very_positive|positive|neutral|negative|very_negative",
  "capital_flow_signal": "inflow|outflow|neutral|not_available",
  "key_news": [
    {
      "title": "...",
      "impact": "positive|negative|neutral",
      "date": "YYYY-MM-DD",
      "source": "...",
      "url": "https://..."
    }
  ]
}

Every item in `risk_alerts`, `positive_catalysts`, and `key_news` must carry a
specific date and source when available. Do not mix stale, undated, or
unverifiable claims into those lists.
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        parts = [f"Gather intelligence and assess sentiment for stock **{ctx.stock_code}**"]
        if ctx.stock_name:
            parts[0] += f" ({ctx.stock_name})"
        parts.append(
            "Prefer fresh, source-backed evidence. Every risk or catalyst item should"
            " be attributable to a dated news item, announcement, filing, or capital-flow observation."
        )
        parts.append("Follow your standard workflow and output the JSON opinion.")
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[IntelAgent] failed to parse opinion JSON")
            return None

        parsed["risk_alerts"] = _normalize_structured_list(
            parsed.get("risk_alerts"),
            primary_key="description",
            allowed_keys=["date", "source", "severity", "impact", "url"],
        ) or parsed.get("risk_alerts", [])
        parsed["positive_catalysts"] = _normalize_structured_list(
            parsed.get("positive_catalysts"),
            primary_key="description",
            allowed_keys=["date", "source", "impact", "url"],
        ) or parsed.get("positive_catalysts", [])
        parsed["key_news"] = _normalize_structured_list(
            parsed.get("key_news"),
            primary_key="title",
            allowed_keys=["date", "source", "impact", "url"],
        ) or parsed.get("key_news", [])

        # Cache parsed intel so downstream agents (especially RiskAgent) can
        # reuse it instead of re-searching the same evidence.
        ctx.set_data("intel_opinion", parsed)

        # Propagate risk alerts to context
        for alert in parsed.get("risk_alerts", []):
            description = _extract_evidence_text(alert, primary_key="description")
            if description:
                ctx.add_risk_flag(category="intel", description=description)

        signal = parsed.get("signal", "hold")
        # Map signal string to direction integer
        direction_map = {
            "strong_buy": 1, "buy": 1,
            "hold": 0,
            "sell": -1, "strong_sell": -1
        }
        direction = direction_map.get(str(signal).lower(), 0)

        # Standardise score: map sentiment_label to 0-100
        sentiment_map = {
            "very_positive": 90.0,
            "positive": 70.0,
            "neutral": 50.0,
            "negative": 30.0,
            "very_negative": 10.0
        }
        score = sentiment_map.get(str(parsed.get("sentiment_label", "neutral")).lower(), 50.0)

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=signal,
            score=score,
            direction=direction,
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            raw_data=parsed,
        )
