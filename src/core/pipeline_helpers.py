# -*- coding: utf-8 -*-
"""Extracted helper functions for StockAnalysisPipeline.

These were originally @staticmethod methods or module-level functions in
pipeline.py.  Extracted to reduce pipeline.py size and improve testability.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from data_provider.base import normalize_stock_code
from src.core.trading_calendar import (
    get_effective_trading_date,
    get_market_for_stock,
)
from src.schemas.analysis_result import AnalysisResult

logger = logging.getLogger(__name__)


def override_sniper_points(
    result: "AnalysisResult",
    trend_result: Any,
    current_price: Optional[float],
) -> int:
    """Override LLM-generated sniper-point prices with support/resistance data.

    LLMs often hallucinate stop-loss, buy, and take-profit levels.  This
    function clamps them against real support/resistance levels from the
    trend analyser so they stay grounded in actual market structure.

    Returns the number of fields that were overridden.
    """
    if current_price is None or current_price <= 0:
        return 0
    if trend_result is None:
        return 0

    # Extract support / resistance levels from trend_result
    support_levels: List[float] = []
    resistance_levels: List[float] = []
    if hasattr(trend_result, "support_levels") and trend_result.support_levels:
        support_levels = sorted(trend_result.support_levels)
    if hasattr(trend_result, "resistance_levels") and trend_result.resistance_levels:
        resistance_levels = sorted(trend_result.resistance_levels)

    # Also use MA5 / MA10 as fallback support/resistance anchors
    ma5 = getattr(trend_result, "ma5", None) or 0
    ma10 = getattr(trend_result, "ma10", None) or 0

    # Find the nearest support BELOW current price
    nearest_support = None
    for s in reversed(support_levels):
        if s < current_price:
            nearest_support = s
            break
    if nearest_support is None and ma5 and ma5 < current_price:
        nearest_support = ma5
    if nearest_support is None and ma10 and ma10 < current_price:
        nearest_support = ma10

    # Find the nearest resistance ABOVE current price
    nearest_resistance = None
    for r in resistance_levels:
        if r > current_price:
            nearest_resistance = r
            break
    if nearest_resistance is None and ma5 and ma5 > current_price:
        nearest_resistance = ma5
    if nearest_resistance is None and ma10 and ma10 > current_price:
        nearest_resistance = ma10

    # Only proceed if we have a dashboard with sniper_points
    if not isinstance(result.dashboard, dict):
        return 0
    battle = result.dashboard.get("battle_plan") or {}
    sniper = battle.get("sniper_points")
    if not isinstance(sniper, dict):
        return 0

    from src.schemas.analysis_result import parse_price

    overrides = 0

    # --- Override stop_loss -------------------------------------------------
    llm_sl = parse_price(sniper.get("stop_loss"))
    if llm_sl is not None and nearest_support is not None:
        # Stop loss should be 2-3% below nearest support, never above it
        target_sl = round(nearest_support * 0.97, 2)
        if llm_sl > nearest_support or llm_sl < nearest_support * 0.85:
            sniper["stop_loss"] = target_sl
            logger.info(
                "Overrode stop_loss from %s to %.2f (nearest_support=%.2f)",
                llm_sl, target_sl, nearest_support,
            )
            overrides += 1

    # --- Override ideal_buy --------------------------------------------------
    llm_buy = parse_price(sniper.get("ideal_buy"))
    if llm_buy is not None:
        # Don't chase: ideal buy should not be > 5% above current price
        max_buy = round(current_price * 1.05, 2)
        if llm_buy > max_buy:
            sniper["ideal_buy"] = max_buy
            logger.info(
                "Overrode ideal_buy from %s to %.2f (max_buy=%.2f)",
                llm_buy, max_buy, current_price,
            )
            overrides += 1
        # Don't expect unrealistic dips: not below 15% of current price
        min_buy = round(current_price * 0.85, 2)
        if llm_buy < min_buy:
            sniper["ideal_buy"] = min_buy
            logger.info(
                "Overrode ideal_buy from %s to %.2f (min_buy=%.2f)",
                llm_buy, min_buy, current_price,
            )
            overrides += 1

    # --- Override take_profit ------------------------------------------------
    llm_tp = parse_price(sniper.get("take_profit"))
    if llm_tp is not None:
        if nearest_resistance is not None:
            # Take profit should be within 20% of nearest resistance
            max_tp = round(nearest_resistance * 1.20, 2)
            min_tp = round(nearest_resistance * 0.95, 2)
            if llm_tp > max_tp:
                sniper["take_profit"] = max_tp
                logger.info(
                    "Overrode take_profit from %s to %.2f (nearest_resistance=%.2f)",
                    llm_tp, max_tp, nearest_resistance,
                )
                overrides += 1
            elif llm_tp < min_tp:
                sniper["take_profit"] = min_tp
                logger.info(
                    "Overrode take_profit from %s to %.2f (nearest_resistance=%.2f)",
                    llm_tp, min_tp, nearest_resistance,
                )
                overrides += 1
        else:
            # No resistance data — use R:R sanity: take_profit should be above current_price
            # and at least 1.5x stop-loss distance
            llm_sl_for_tp = parse_price(sniper.get("stop_loss")) or current_price * 0.95
            min_tp = round(current_price + (current_price - llm_sl_for_tp) * 1.5, 2)
            if llm_tp < min_tp:
                sniper["take_profit"] = min_tp
                logger.info(
                    "Overrode take_profit from %s to %.2f (min_RR=%.2f)",
                    llm_tp, min_tp, min_tp,
                )
                overrides += 1

    if overrides:
        result.dashboard["battle_plan"]["sniper_points"] = sniper
        result.analysis_metadata["sniper_overrides"] = overrides

    return overrides


def extract_quote_payload(realtime_quote: Any) -> Optional[Dict[str, Any]]:
    """Safely extract quote data from realtime_quote (object or dict)."""
    if realtime_quote is None:
        return None

    def _get_value(key: str, fallback: Optional[str] = None) -> Any:
        if isinstance(realtime_quote, dict):
            if key in realtime_quote:
                return realtime_quote.get(key)
            return realtime_quote.get(fallback) if fallback else None
        value = getattr(realtime_quote, key, None)
        if value is not None:
            return value
        return getattr(realtime_quote, fallback, None) if fallback else None

    payload = {
        "name": _get_value("name"),
        "price": _get_value("price"),
        "change_pct": _get_value("change_pct"),
        "volume": _get_value("volume"),
        "amount": _get_value("amount"),
        "open": _get_value("open_price", "open"),
        "high": _get_value("high"),
        "low": _get_value("low"),
        "turnover_rate": _get_value("turnover_rate"),
        "volume_ratio": _get_value("volume_ratio"),
        "pe_ratio": _get_value("pe_ratio"),
        "pb_ratio": _get_value("pb_ratio"),
        "total_mv": _get_value("total_mv"),
        "circ_mv": _get_value("circ_mv"),
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    return payload or None


def extract_chip_payload(chip_data: Any) -> Optional[Dict[str, Any]]:
    """Safely extract chip distribution data."""
    if chip_data is None:
        return None
    if isinstance(chip_data, dict):
        payload = dict(chip_data)
    elif hasattr(chip_data, "__dict__"):
        payload = {
            "profit_ratio": getattr(chip_data, "profit_ratio", None),
            "avg_cost": getattr(chip_data, "avg_cost", None),
            "concentration_90": getattr(chip_data, "concentration_90", None),
            "concentration_70": getattr(chip_data, "concentration_70", None),
            "date": getattr(chip_data, "date", None),
        }
    else:
        return None
    payload = {key: value for key, value in payload.items() if value is not None}
    return payload or None


def extract_trend_payload(trend_result: Any) -> Optional[Dict[str, Any]]:
    """Safely extract trend analysis data."""
    if trend_result is None:
        return None
    if hasattr(trend_result, "to_dict"):
        payload = trend_result.to_dict()
    elif isinstance(trend_result, dict):
        payload = dict(trend_result)
    elif hasattr(trend_result, "__dict__"):
        payload = dict(trend_result.__dict__)
    else:
        return None
    return payload or None


def compute_ma_status(ma5: float, ma10: float, ma20: float, price: float) -> str:
    """Compute MA alignment status from price and MA values."""
    price = price or 0
    ma5 = ma5 or 0
    ma10 = ma10 or 0
    ma20 = ma20 or 0
    if not all([ma5, ma10, ma20]):
        return "均线不足"
    if ma5 > ma10 > ma20:
        return "多头排列 📈" if price >= ma5 else "多头承压"
    elif ma5 < ma10 < ma20:
        return "空头排列 📉" if price <= ma5 else "空头反抽"
    elif price > ma5 and ma5 > ma10:
        return "短期向好 🔼"
    elif price < ma5 and ma5 < ma10:
        return "短期走弱 🔽"
    else:
        return "震荡整理 ↔️"


def safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
    """Safely convert any object to a dict."""
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
    elif isinstance(value, dict):
        payload = dict(value)
    elif hasattr(value, "__dict__"):
        payload = dict(value.__dict__)
    else:
        return None
    return payload or None


def resolve_resume_target_date(
    code: str, current_time: Optional[datetime] = None
) -> date:
    """Resolve the trading date used by checkpoint/resume checks."""
    market = get_market_for_stock(normalize_stock_code(code))
    return get_effective_trading_date(market, current_time=current_time)


def extract_risk_keywords(text: str) -> List[str]:
    """Extract risk-related keywords from text."""
    patterns = [
        ("减持", r"减持"),
        ("处罚", r"处罚|罚款|罚单"),
        ("调查", r"调查|立案"),
        ("预亏", r"预亏|亏损|下修"),
        ("解禁", r"解禁"),
        ("诉讼", r"诉讼"),
        ("违规", r"违规"),
        ("流出", r"净流出|持续流出"),
        ("风险", r"风险提示|重大风险"),
    ]
    hits: List[str] = []
    haystack = text or ""
    for label, pattern in patterns:
        if re.search(pattern, haystack, flags=re.IGNORECASE) and label not in hits:
            hits.append(label)
    return hits


def estimate_intel_bullet_count(text: str) -> int:
    """Count markdown bullet points in text."""
    return len(re.findall(r"(?m)^\s*-\s+", text or ""))
