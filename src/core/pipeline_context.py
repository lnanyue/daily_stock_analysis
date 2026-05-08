# -*- coding: utf-8 -*-
"""Context assembly helpers for ``StockAnalysisPipeline``."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

import pandas as pd

from data_provider.base import normalize_stock_code
from src.core.trading_calendar import get_market_for_stock, get_market_now
from src.search_service import SearchService

logger = logging.getLogger(__name__)


def _as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return None if pd.isna(number) else number
    except Exception:
        logger.debug("_as_float failed for value=%r", value)
        return None


def _get_quote_value(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _trend_payload(trend_result: Any) -> Dict[str, Any]:
    if not trend_result:
        return {}
    if hasattr(trend_result, "to_dict"):
        return trend_result.to_dict()
    if isinstance(trend_result, dict):
        return dict(trend_result)
    if hasattr(trend_result, "__dict__"):
        return dict(trend_result.__dict__)
    return {}


def enhance_analysis_context(
    *,
    context: Dict[str, Any],
    realtime_quote: Any,
    chip_data: Any,
    trend_result: Any,
    stock_name: str,
    search_service: Any,
    fetcher_manager: Any,
    db: Any,
    compute_ma_status: Callable[[float, float, float, float], str],
    fundamental_context: Optional[Dict[str, Any]] = None,
    market_overview: Optional[Dict[str, Any]] = None,
    peer_comparison: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    enhanced = context.copy()
    enhanced["stock_name"] = stock_name
    enhanced["news_window_days"] = getattr(search_service, "news_window_days", None)

    if fundamental_context:
        enhanced["fundamental_context"] = fundamental_context
    if market_overview:
        enhanced["market_overview"] = market_overview
    if peer_comparison:
        enhanced["peer_comparison"] = peer_comparison

    trend_payload = _trend_payload(trend_result)
    if trend_payload:
        enhanced["trend_analysis"] = trend_payload

    if realtime_quote:
        rt_data = {
            "price": _get_quote_value(realtime_quote, "price"),
            "change_pct": _get_quote_value(realtime_quote, "change_pct"),
            "volume": _get_quote_value(realtime_quote, "volume"),
            "amount": _get_quote_value(realtime_quote, "amount"),
            "open": _get_quote_value(realtime_quote, "open_price"),
            "high": _get_quote_value(realtime_quote, "high"),
            "low": _get_quote_value(realtime_quote, "low"),
            "turnover_rate": _get_quote_value(realtime_quote, "turnover_rate"),
            "volume_ratio": _get_quote_value(realtime_quote, "volume_ratio"),
            "pe_ratio": _get_quote_value(realtime_quote, "pe_ratio"),
            "pb_ratio": _get_quote_value(realtime_quote, "pb_ratio"),
            "total_mv": _get_quote_value(realtime_quote, "total_mv"),
            "circ_mv": _get_quote_value(realtime_quote, "circ_mv"),
            "change_60d": _get_quote_value(realtime_quote, "change_60d"),
            "source": _get_quote_value(realtime_quote, "source"),
        }
        
        # 补全：如果实时行情缺失 PE/PB，从基本面数据中提取
        if fundamental_context and isinstance(fundamental_context, dict):
            val_block = fundamental_context.get("valuation", {})
            val_data = val_block.get("data", {}) if isinstance(val_block, dict) else {}
            if rt_data["pe_ratio"] is None:
                rt_data["pe_ratio"] = val_data.get("pe_ratio")
            if rt_data["pb_ratio"] is None:
                rt_data["pb_ratio"] = val_data.get("pb_ratio")
            if rt_data["total_mv"] is None:
                rt_data["total_mv"] = val_data.get("total_mv")
            if rt_data["circ_mv"] is None:
                rt_data["circ_mv"] = val_data.get("circ_mv")
        
        enhanced["realtime"] = rt_data

    if chip_data:
        enhanced["chip"] = {
            "profit_ratio": getattr(chip_data, "profit_ratio", 0),
            "avg_cost": getattr(chip_data, "avg_cost", None),
            "concentration_90": getattr(chip_data, "concentration_90", 0),
            "concentration_70": getattr(chip_data, "concentration_70", 0),
            "chip_status": getattr(chip_data, "chip_status", ""),
        }

    today = dict(enhanced.get("today") or {})
    yesterday = dict(enhanced.get("yesterday") or {})

    ma5 = _as_float(trend_payload.get("ma5"))
    if ma5:
        today["ma5"] = round(ma5, 2)
        today["ma10"] = round(_as_float(trend_payload.get("ma10")) or 0, 2)
        today["ma20"] = round(_as_float(trend_payload.get("ma20")) or 0, 2)

    enhanced["today"] = today
    enhanced["ma_status"] = compute_ma_status(
        today.get("ma5"),
        today.get("ma10"),
        today.get("ma20"),
        today.get("close"),
    )

    if yesterday and today:
        prev_close = _as_float(yesterday.get("close"))
        if prev_close and today.get("close"):
            enhanced["price_change_ratio"] = round((today["close"] - prev_close) / prev_close * 100, 2)

    trend_ma5 = getattr(trend_result, "ma5", 0) if trend_result else 0
    if realtime_quote and trend_result and trend_ma5 > 0:
        price = getattr(realtime_quote, "price", None)
        if price is not None and price > 0:
            yesterday_close = None
            if enhanced.get("yesterday") and isinstance(enhanced["yesterday"], dict):
                yesterday_close = enhanced["yesterday"].get("close")
            orig_today = enhanced.get("today") or {}
            open_p = (
                getattr(realtime_quote, "open_price", None)
                or getattr(realtime_quote, "pre_close", None)
                or yesterday_close
                or orig_today.get("open")
                or price
            )
            high_p = getattr(realtime_quote, "high", None) or price
            low_p = getattr(realtime_quote, "low", None) or price
            vol = getattr(realtime_quote, "volume", None)
            amt = getattr(realtime_quote, "amount", None)
            pct = getattr(realtime_quote, "change_pct", None)
            realtime_today = {
                "close": price,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "ma5": trend_result.ma5,
                "ma10": trend_result.ma10,
                "ma20": trend_result.ma20,
            }
            if vol is not None:
                realtime_today["volume"] = vol
            if amt is not None:
                realtime_today["amount"] = amt
            if pct is not None:
                realtime_today["pct_chg"] = pct
            for key, value in orig_today.items():
                if key not in realtime_today and value is not None:
                    realtime_today[key] = value
            enhanced["today"] = realtime_today
            enhanced["ma_status"] = compute_ma_status(
                trend_result.ma5,
                trend_result.ma10,
                trend_result.ma20,
                price,
            )
            enhanced["date"] = get_market_now(
                get_market_for_stock(normalize_stock_code(enhanced.get("code", "")))
            ).date().isoformat()
            if yesterday_close is not None:
                try:
                    yc = float(yesterday_close)
                    if yc > 0:
                        enhanced["price_change_ratio"] = round((price - yc) / yc * 100, 2)
                except (TypeError, ValueError):
                    pass
            if vol is not None and enhanced.get("yesterday"):
                yest_vol = (
                    enhanced["yesterday"].get("volume")
                    if isinstance(enhanced["yesterday"], dict)
                    else None
                )
                if yest_vol is not None:
                    try:
                        yv = float(yest_vol)
                        if yv > 0:
                            enhanced["volume_change_ratio"] = round(float(vol) / yv, 2)
                    except (TypeError, ValueError):
                        pass

    enhanced["is_index_etf"] = SearchService.is_index_or_etf(
        context.get("code", ""),
        enhanced.get("stock_name", stock_name),
    )
    enhanced["fundamental_context"] = (
        fundamental_context
        if isinstance(fundamental_context, dict)
        else fetcher_manager.build_failed_fundamental_context(
            context.get("code", ""),
            "invalid fundamental context",
        )
    )

    code_str = context.get("code", "")
    try:
        from src.services.backtest_service import BacktestService

        bt_service = BacktestService(db)
        enhanced["historical_performance"] = {
            "stock": bt_service.get_stock_summary(code_str),
            "overall": bt_service.get_global_summary(),
        }
    except Exception as exc:
        logger.debug("[%s] 获取历史胜率失败: %s", code_str, exc)

    return enhanced
