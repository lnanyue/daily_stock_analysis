# -*- coding: utf-8 -*-
"""组合综述：在个股分析完成后生成跨股票的组合级洞察。"""

import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_PORTFOLIO_SYSTEM_PROMPT = (
    "You are a professional portfolio analyst specializing in multi-asset allocation "
    "for A-share, HK, and US equity portfolios. "
    "Respond in the same language as the input stock names."
)

_OUTPUT_FORMAT_INSTRUCTION = """
## Output format
Return a single JSON object (no markdown fences):
{
  "portfolio_risk_score": 6,
  "total_stocks": 5,
  "positions": [
    {"code": "600519", "suggested_weight": 0.25, "signal": "buy", "note": "..."}
  ],
  "sector_warnings": ["Consumer sector > 40%"],
  "correlation_warnings": ["600519 & 000858 may be correlated"],
  "cross_market_notes": ["US tariff risk may impact export-heavy positions"],
  "rebalance_suggestions": ["Trim position X, add defensive sector exposure"],
  "summary": "Portfolio is moderately concentrated ..."
}

Note: If sector information is missing for some stocks, infer the sector from the stock name/code.
"""


def _build_prompt(results: list) -> str:
    """Assemble the user prompt from per-stock analysis results."""
    parts = [f"Analyze the following portfolio of {len(results)} stocks:\n"]
    for r in results:
        sector = r.sector_position or "N/A"
        parts.append(
            f"- **{r.code} ({r.name})**: signal={r.operation_advice}, "
            f"score={r.sentiment_score}, confidence={r.confidence_level}, "
            f"sector={sector}"
        )
    parts.append(_OUTPUT_FORMAT_INSTRUCTION)
    return "\n".join(parts)


def _parse_json(raw: str) -> Optional[dict]:
    """Extract JSON from LLM response."""
    try:
        from src.utils.data_processing import extract_json_from_text
        data = extract_json_from_text(raw) or {}
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _render(data: dict) -> str:
    """Render parsed portfolio assessment as markdown."""

    def _get(key: str, default=""):
        """dict.get 的 None-aware 版本。"""
        val = data.get(key)
        return default if val is None else val

    score = _get("portfolio_risk_score", "N/A")
    summary = _get("summary", "")
    sector_warnings = _get("sector_warnings", []) or []
    correlation_warnings = _get("correlation_warnings", []) or []
    cross_market = _get("cross_market_notes", []) or []
    rebalance = _get("rebalance_suggestions", []) or []
    positions = _get("positions", []) or []

    lines = []
    if summary:
        lines.append(f"{summary}\n")
    lines.append(f"- **Portfolio Risk Score**: {score}/10\n")

    if sector_warnings:
        lines.append("\n**Sector Concentration**\n")
        for w in sector_warnings:
            lines.append(f"- {w}")

    if correlation_warnings:
        lines.append("\n**Correlation Warnings**\n")
        for w in correlation_warnings:
            lines.append(f"- {w}")

    if cross_market:
        lines.append("\n**Cross-Market Notes**\n")
        for n in cross_market:
            lines.append(f"- {n}")

    if rebalance:
        lines.append("\n**Rebalance Suggestions**\n")
        for s in rebalance:
            lines.append(f"- {s}")

    if positions:
        lines.append("\n| Code | Weight | Signal | Note |")
        lines.append("|------|--------|--------|------|")
        for p in positions:
            code = p.get("code", "")
            weight = p.get("suggested_weight", "")
            signal = p.get("signal", "")
            note = p.get("note", "")
            weight_str = f"{weight:.0%}" if isinstance(weight, (int, float)) else str(weight)
            lines.append(f"| {code} | {weight_str} | {signal} | {note} |")

    return "\n".join(lines)


async def run_portfolio_aggregation(
    analyzer: Any,
    results: list,
) -> Optional[str]:
    """在个股分析完成后生成跨股票的组合级洞察。

    使用 PortfolioAgent 的 prompt 结构，但通过 pipeline 的 analyzer 直接调用 LLM，
    避免构造完整 agent 基础设施的开销。

    返回不带顶层标题的 markdown 内容，调用方自行决定 header 级别。分析不可用时返回 None。
    """
    if not analyzer or not hasattr(analyzer, "generate_text_async") or not results:
        return None
    if len(results) < 2:
        logger.info("组合综述：跳过（需要 >= 2 只股票，当前 %d 只）", len(results))
        return None

    prompt = _build_prompt(results)

    raw = await analyzer.generate_text_async(
        prompt,
        max_tokens=4096,
        temperature=0.7,
        system_prompt=_PORTFOLIO_SYSTEM_PROMPT,
    )
    if not raw:
        logger.warning("组合综述：LLM 返回空响应")
        return None

    data = _parse_json(raw)
    if not data:
        logger.warning("组合综述：无法解析 LLM 响应为 JSON")
        return None

    return _render(data)
