# -*- coding: utf-8 -*-
"""
Dashboard normalization and price level collection logic.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from src.agent.protocols import AgentContext, AgentOpinion, normalize_decision_signal
from src.agent.utils.text_utils import (
    first_non_empty_text,
    truncate_text,
    extract_evidence_text,
    extract_latest_news_title,
)
from src.agent.quantitative.signal_logic import (
    signal_to_operation,
    signal_to_signal_type,
    default_position_advice,
    default_position_size,
    confidence_label,
    estimate_sentiment_score,
)

logger = logging.getLogger(__name__)


def coerce_level_value(value: Any) -> Any:
    """Standardise a price level into a float or clean string."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace(",", "").replace("，", "").strip()
    if not text or text.upper() == "N/A" or text in {"-", "—"}:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return text


def pick_first_level(*values: Any) -> Any:
    """Return the first non-None price level after coercion."""
    for value in values:
        normalized = coerce_level_value(value)
        if normalized is not None:
            return normalized
    return None


def level_values_equal(left: Any, right: Any) -> bool:
    """Return whether two price levels are numerically equivalent."""
    left_normalized = coerce_level_value(left)
    right_normalized = coerce_level_value(right)
    return (
        left_normalized is not None
        and right_normalized is not None
        and left_normalized == right_normalized
    )


def latest_opinion(ctx: AgentContext, names: Set[str]) -> Optional[AgentOpinion]:
    """Return the most recent opinion from one of the listed agents."""
    for opinion in reversed(ctx.opinions):
        if opinion.agent_name in names:
            return opinion
    return None


def collect_key_levels(
    ctx: AgentContext,
    payload: Dict[str, Any],
    dashboard_block: Dict[str, Any],
) -> Dict[str, Any]:
    """Collect key price levels from dashboard payloads and agent opinions."""
    levels: Dict[str, Any] = {}

    def absorb(source: Any) -> None:
        if not isinstance(source, dict):
            return
        for key, value in source.items():
            normalized = coerce_level_value(value)
            if normalized is not None and key not in levels:
                levels[key] = normalized

    absorb(payload.get("key_levels"))
    absorb(dashboard_block.get("key_levels"))
    for opinion in reversed(ctx.opinions):
        absorb(getattr(opinion, "key_levels", {}))
        raw = opinion.raw_data if isinstance(opinion.raw_data, dict) else {}
        absorb(raw.get("key_levels"))
    return levels


def build_data_perspective(
    ctx: AgentContext,
    key_levels: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a lightweight data_perspective block from cached market data."""
    realtime = ctx.get_data("realtime_quote")
    chip = ctx.get_data("chip_distribution")
    trend = ctx.get_data("trend_result")
    technical = latest_opinion(ctx, {"technical"})
    tech_raw = technical.raw_data if technical and isinstance(technical.raw_data, dict) else {}
    trend_dict = trend if isinstance(trend, dict) else {}

    data_perspective: Dict[str, Any] = {}
    ma_alignment = tech_raw.get("ma_alignment")
    trend_score = tech_raw.get("trend_score")
    if ma_alignment or trend_score is not None:
        data_perspective["trend_status"] = {
            "ma_alignment": ma_alignment or "N/A",
            "trend_score": trend_score if trend_score is not None else "N/A",
            "is_bullish": str(ma_alignment).lower() == "bullish",
        }

    def _bias_label(bias):
        if not isinstance(bias, (int, float)):
            return ""
        if bias > 5:
            return "超买"
        elif bias > 2:
            return "偏高"
        elif bias < -5:
            return "超卖"
        elif bias < -2:
            return "偏低"
        return "中性"

    def _r(val, n=2):
        return round(val, n) if isinstance(val, (int, float)) else val

    def _pick(primary_dict, primary_key, fallback_dict, fallback_key, default="N/A"):
        v = primary_dict.get(primary_key)
        if v is not None:
            return v
        v2 = fallback_dict.get(fallback_key, default)
        return v2 if v2 is not None else default

    if isinstance(realtime, dict) or trend_dict:
        data_perspective["price_position"] = {
            "current_price": _r(_pick(trend_dict, "current_price", realtime or {}, "price")),
            "ma5": _r(_pick(trend_dict, "ma5", tech_raw, "ma5")),
            "ma10": _r(_pick(trend_dict, "ma10", tech_raw, "ma10")),
            "ma20": _r(_pick(trend_dict, "ma20", tech_raw, "ma20")),
            "bias_ma5": _r(_pick(trend_dict, "bias_ma5", tech_raw, "bias_ma5")),
            "bias_status": _bias_label(trend_dict.get("bias_ma5")) or tech_raw.get("bias_status", "N/A"),
            "support_level": key_levels.get("support") or key_levels.get("immediate_support") or "N/A",
            "resistance_level": key_levels.get("resistance") or key_levels.get("current_resistance") or "N/A",
        }
        data_perspective["volume_analysis"] = {
            "volume_ratio": (realtime or {}).get("volume_ratio", "N/A"),
            "turnover_rate": (realtime or {}).get("turnover_rate", "N/A"),
            "volume_status": trend_dict.get("volume_status") or tech_raw.get("volume_status", "N/A"),
            "volume_meaning": tech_raw.get("reasoning", "") if tech_raw else "",
        }

    if isinstance(chip, dict):
        concentration = chip.get("concentration_90")
        if concentration is None:
            concentration = chip.get("concentration")
        data_perspective["chip_structure"] = {
            "profit_ratio": chip.get("profit_ratio", "N/A"),
            "avg_cost": chip.get("avg_cost", "N/A"),
            "concentration": concentration if concentration is not None else "N/A",
            "chip_health": chip.get("chip_health", "一般"),
        }

    return data_perspective


def collect_risk_alerts(
    ctx: AgentContext,
    intelligence: Dict[str, Any],
) -> List[str]:
    """Gather all risk alerts from intelligence payload, agents, and context flags."""
    alerts: List[str] = []

    def absorb(values: Any) -> None:
        if not isinstance(values, list):
            return
        for item in values:
            text = extract_evidence_text(item)
            if text and text not in alerts:
                alerts.append(text)

    absorb(intelligence.get("risk_alerts"))
    intel = latest_opinion(ctx, {"intel"})
    intel_raw = intel.raw_data if intel and isinstance(intel.raw_data, dict) else {}
    absorb(intel_raw.get("risk_alerts"))
    risk = latest_opinion(ctx, {"risk"})
    risk_raw = risk.raw_data if risk and isinstance(risk.raw_data, dict) else {}
    absorb(risk_raw.get("flags"))
    for flag in ctx.risk_flags:
        description = str(flag.get("description", "")).strip()
        if description and description not in alerts:
            alerts.append(description)
    return alerts[:8]


def collect_positive_catalysts(
    ctx: AgentContext,
    intelligence: Dict[str, Any],
) -> List[str]:
    """Gather positive news catalysts from intelligence payload and agent."""
    catalysts: List[str] = []

    def absorb(values: Any) -> None:
        if not isinstance(values, list):
            return
        for item in values:
            text = extract_evidence_text(item)
            if text and text not in catalysts:
                catalysts.append(text)

    absorb(intelligence.get("positive_catalysts"))
    intel = latest_opinion(ctx, {"intel"})
    intel_raw = intel.raw_data if intel and isinstance(intel.raw_data, dict) else {}
    absorb(intel_raw.get("positive_catalysts"))
    return catalysts[:8]


def mark_partial_dashboard(
    dashboard: Dict[str, Any],
    *,
    note: str,
) -> Dict[str, Any]:
    """Tag a dashboard as degraded/partial with a specific note."""
    tagged = dict(dashboard)
    summary = first_non_empty_text(tagged.get("analysis_summary"))
    prefix = "[降级结果] "
    if summary and not summary.startswith(prefix):
        tagged["analysis_summary"] = prefix + summary
    elif not summary:
        tagged["analysis_summary"] = prefix + note

    warning = first_non_empty_text(tagged.get("risk_warning"))
    tagged["risk_warning"] = f"{note} {warning}".strip() if warning else note

    nested = tagged.get("dashboard")
    if isinstance(nested, dict):
        nested = dict(nested)
        core = nested.get("core_conclusion")
        if isinstance(core, dict):
            core = dict(core)
            one_sentence = first_non_empty_text(core.get("one_sentence"), tagged.get("analysis_summary"))
            if one_sentence and not str(one_sentence).startswith(prefix):
                core["one_sentence"] = prefix + str(one_sentence)
            nested["core_conclusion"] = core
        tagged["dashboard"] = nested
    return tagged


def normalize_dashboard_payload(
    ctx: AgentContext,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Perform deep normalization on a raw dashboard JSON payload.

    Ensures all expected keys exist and are populated with logical defaults
    derived from agent opinions if the LLM left them blank.
    """
    decision_type = normalize_decision_signal(payload.get("decision_type", "hold"))
    confidence = float(payload.get("confidence_level") or 0.6) if not isinstance(payload.get("confidence_level"), str) else 0.6
    
    # Try to extract confidence from string labels or upstream opinions
    if isinstance(payload.get("confidence_level"), str):
        label = payload["confidence_level"].strip()
        confidence = 0.85 if "高" in label else 0.55 if "中" in label else 0.3
    
    # Fallback to decision agent's own confidence if payload is empty
    base_opinion = latest_opinion(ctx, {"decision", "skill_consensus", "strategy_consensus"})
    if base_opinion and confidence == 0.6:
        confidence = base_opinion.confidence

    sentiment_score = payload.get("sentiment_score")
    if sentiment_score is None:
        sentiment_score = estimate_sentiment_score(decision_type, confidence)

    analysis_summary = first_non_empty_text(
        payload.get("analysis_summary"),
        getattr(base_opinion, "reasoning", ""),
        "分析进行中..."
    )
    trend_prediction = first_non_empty_text(
        payload.get("trend_prediction"),
        "走势待定"
    )

    # Resolve operation advice
    op_advice_raw = payload.get("operation_advice")
    if isinstance(op_advice_raw, dict):
        operation_advice = op_advice_raw.get("no_position") or op_advice_raw.get("has_position") or signal_to_operation(decision_type)
    else:
        operation_advice = first_non_empty_text(op_advice_raw, signal_to_operation(decision_type))

    # Inner dashboard block
    dashboard_block = payload.get("dashboard")
    if not isinstance(dashboard_block, dict):
        dashboard_block = {}

    core = dashboard_block.get("core_conclusion")
    if not isinstance(core, dict):
        core = {}
    
    intelligence = dashboard_block.get("intelligence")
    if not isinstance(intelligence, dict):
        intelligence = {}

    battle = dashboard_block.get("battle_plan")
    if not isinstance(battle, dict):
        battle = {}

    # Key Levels
    key_levels = collect_key_levels(ctx, payload, dashboard_block)
    
    # Position Advice
    position_advice = core.get("position_advice")
    if not isinstance(position_advice, dict) or not position_advice:
        position_advice = default_position_advice(decision_type)

    # Sniper Points
    sniper = battle.get("sniper_points")
    if not isinstance(sniper, dict):
        sniper = {}
    
    sniper.setdefault(
        "current_price",
        pick_first_level(
            payload.get("current_price"),
            ctx.get_data("realtime_quote", {}).get("price"),
            ctx.get_data("trend_result", {}).get("current_price"),
        ) or "N/A",
    )
    sniper.setdefault(
        "stop_loss",
        key_levels.get("stop_loss")
        or key_levels.get("strong_support_stop_loss")
        or "待补充",
    )
    sniper.setdefault(
        "take_profit",
        key_levels.get("take_profit")
        or key_levels.get("next_breakout_target")
        or key_levels.get("current_resistance")
        or key_levels.get("resistance")
        or "N/A",
    )

    risk_alerts = collect_risk_alerts(ctx, intelligence)
    positive_catalysts = collect_positive_catalysts(ctx, intelligence)
    latest_news = extract_latest_news_title(intelligence)

    if not intelligence.get("risk_alerts"):
        intelligence["risk_alerts"] = risk_alerts
    if positive_catalysts and not intelligence.get("positive_catalysts"):
        intelligence["positive_catalysts"] = positive_catalysts
    if latest_news and not intelligence.get("latest_news"):
        intelligence["latest_news"] = latest_news

    if not core.get("one_sentence"):
        core["one_sentence"] = truncate_text(analysis_summary, 60)
    if not core.get("time_sensitivity"):
        core["time_sensitivity"] = "本周内"
    if not core.get("signal_type"):
        core["signal_type"] = signal_to_signal_type(decision_type)
    core["position_advice"] = position_advice

    battle["sniper_points"] = sniper
    if "action_checklist" not in battle:
        battle["action_checklist"] = []
    
    position_strategy = battle.get("position_strategy")
    if not isinstance(position_strategy, dict) or not position_strategy:
        battle["position_strategy"] = {
            "suggested_position": default_position_size(decision_type),
            "entry_plan": position_advice["no_position"],
            "risk_control": f"止损参考 {sniper.get('stop_loss', '待补充')}",
        }

    data_perspective = dashboard_block.get("data_perspective")
    if not isinstance(data_perspective, dict):
        data_perspective = {}
    if not data_perspective:
        built_dp = build_data_perspective(ctx, key_levels)
        if built_dp:
            data_perspective = built_dp
    if data_perspective:
        dashboard_block["data_perspective"] = data_perspective

    dashboard_block["core_conclusion"] = core
    dashboard_block["intelligence"] = intelligence
    dashboard_block["battle_plan"] = battle

    key_points = payload.get("key_points")
    if not isinstance(key_points, list) or not key_points:
        key_points = [
            truncate_text(op.reasoning, 120)
            for op in ctx.opinions
            if isinstance(op.reasoning, str) and op.reasoning.strip()
        ][:5]

    risk_warning = first_non_empty_text(
        payload.get("risk_warning"),
        "；".join(risk_alerts[:3]),
        getattr(latest_opinion(ctx, {"risk"}), "reasoning", ""),
    )
    if not risk_warning:
        risk_warning = "暂无额外风险提示"

    payload["stock_name"] = first_non_empty_text(payload.get("stock_name"), ctx.stock_name, ctx.stock_code)
    payload["sentiment_score"] = sentiment_score
    payload["trend_prediction"] = trend_prediction
    payload["operation_advice"] = operation_advice
    payload["decision_type"] = decision_type
    payload["confidence_level"] = confidence_label(confidence)
    payload["analysis_summary"] = analysis_summary
    payload["key_points"] = key_points
    payload["risk_warning"] = risk_warning
    payload["dashboard"] = dashboard_block
    return payload
