# -*- coding: utf-8 -*-
"""
Signal normalisation layer — converts raw computation outputs into
standardised {signal, score, confidence} triples for the LLM prompt.

Each ``NormalizedSignal`` represents one analytical dimension so the LLM
receives pre-digested judgements rather than raw numbers and does not
need to re-derive them itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Standardised signal type
# ---------------------------------------------------------------------------


@dataclass
class NormalizedSignal:
    """One dimension of standardised analysis output.

    ``dimension`` name, ``signal`` polarity, ``score`` in 0-100 and
    ``confidence`` in 0.0-1.0 together let the LLM quickly grasp the
    balance of evidence for this aspect of the stock.
    """

    dimension: str = ""  # e.g. "trend", "volume", "momentum", "chip", "sentiment"
    signal: str = "neutral"  # "bullish" | "neutral" | "bearish"
    score: float = 50.0  # 0-100
    confidence: float = 0.5  # 0.0-1.0
    key_facts: List[str] = field(default_factory=list)

    def to_prompt_line(self) -> str:
        icon = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(self.signal, "➡️")
        conf_stars = "★" * max(1, round(self.confidence * 3))
        facts = " | ".join(self.key_facts[:2])
        return f"| {icon} {self.dimension} | {self.signal} | {self.score:.0f}/100 | {conf_stars} | {facts} |"


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _get(obj: Any, attr: str, default: Any = None) -> Any:
    """Extract attribute from object or dict, with a fallback default."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _enum_value(obj: Any, attr: str, default: str = "") -> str:
    """Extract an Enum attribute's .value (or plain str)."""
    val = _get(obj, attr)
    if val is None:
        return default
    if hasattr(val, "value"):
        return val.value
    return str(val)


def _normalize_trend(trend_result: Any) -> NormalizedSignal:
    """Trend direction & strength."""
    if trend_result is None:
        return NormalizedSignal(dimension="trend")

    signal_score = _get(trend_result, "signal_score", 50)
    buy_signal_str = _enum_value(trend_result, "buy_signal", "")
    trend_strength = _get(trend_result, "trend_strength", 50)
    trend_status = _enum_value(trend_result, "trend_status", "")

    # Map buy_signal to signal polarity
    bullish_keywords = ["强烈买入", "买入"]
    bearish_keywords = ["强烈卖出", "卖出"]
    if buy_signal_str in bullish_keywords:
        signal = "bullish"
        score = max(signal_score, 60)
    elif buy_signal_str in bearish_keywords:
        signal = "bearish"
        score = min(signal_score, 40)
    else:
        signal = "neutral"
        score = signal_score

    confidence = min(0.9, max(0.3, trend_strength / 100.0 * 0.8 + 0.2))
    facts = [f"趋势:{trend_status}", f"评分:{signal_score}"]
    return NormalizedSignal(
        dimension="trend",
        signal=signal,
        score=float(score),
        confidence=confidence,
        key_facts=facts,
    )


def _normalize_volume(trend_result: Any) -> NormalizedSignal:
    """Volume health."""
    if trend_result is None:
        return NormalizedSignal(dimension="volume")

    vol_status = _enum_value(trend_result, "volume_status", "量能正常")
    vol_ratio = _get(trend_result, "volume_ratio_5d", 1.0)

    # VolumeStatus values from StockTrendAnalyzer
    vol = vol_status.lower().replace(" ", "")
    if vol in ("缩量回调", "量能正常"):
        signal = "bullish"
        score = 65
    elif vol in ("放量上涨",):
        signal = "neutral"
        score = 50
    elif vol in ("缩量上涨",):
        signal = "neutral"
        score = 45
    elif vol in ("放量下跌", "放量杀跌"):
        signal = "bearish"
        score = 25
    else:
        signal = "neutral"
        score = 50

    confidence = 0.5
    facts = [f"量能:{vol_status}", f"量比5日:{vol_ratio:.2f}"]
    return NormalizedSignal(
        dimension="volume",
        signal=signal,
        score=float(score),
        confidence=confidence,
        key_facts=facts,
    )


def _normalize_momentum(trend_result: Any) -> NormalizedSignal:
    """MACD + RSI combined momentum."""
    if trend_result is None:
        return NormalizedSignal(dimension="momentum")

    macd_status = _enum_value(trend_result, "macd_status", "")
    rsi_status = _enum_value(trend_result, "rsi_status", "")
    rsi_6 = _get(trend_result, "rsi_6", 50)

    # Score based on RSI
    if rsi_6 >= 70:
        signal = "bearish"  # overbought — potential pullback
        score = 30
    elif rsi_6 <= 30:
        signal = "bullish"  # oversold — potential bounce
        score = 70
    elif rsi_6 >= 55:
        signal = "bullish"
        score = 60
    elif rsi_6 <= 45:
        signal = "bearish"
        score = 40
    else:
        signal = "neutral"
        score = 50

    # Adjust for MACD confirmation
    macd = macd_status.lower().replace(" ", "")
    if "金叉" in macd:
        score = min(95, score + 15)
        signal = "bullish"
    elif "死叉" in macd:
        score = max(5, score - 15)
        signal = "bearish"

    confidence = 0.55 if rsi_status else 0.4
    facts = [f"RSI(6):{rsi_6:.0f}", f"MACD:{macd_status}"]
    return NormalizedSignal(
        dimension="momentum",
        signal=signal,
        score=float(score),
        confidence=confidence,
        key_facts=facts,
    )


def _normalize_chip(chip_data: Any) -> NormalizedSignal:
    """Chip /筹码 distribution health."""
    if chip_data is None:
        return NormalizedSignal(dimension="chip")

    profit = _get(chip_data, "profit_ratio", None)
    concentration = _get(chip_data, "concentration_90", None)

    if profit is None:
        return NormalizedSignal(dimension="chip")

    profit_pct = float(profit) * 100 if isinstance(profit, float) and profit < 1 else float(profit)

    if profit_pct >= 80:
        signal = "bearish"  # everyone profitable — distribution risk
        score = 30
    elif profit_pct >= 60:
        signal = "neutral"
        score = 50
    elif profit_pct >= 30:
        signal = "bullish"
        score = 65
    else:
        signal = "bearish"  # most holders underwater
        score = 35

    # Adjust for concentration
    if concentration is not None:
        conc_pct = float(concentration) * 100 if isinstance(concentration, float) and concentration < 1 else float(concentration)
        if conc_pct < 15:
            confidence = 0.65  # concentrated = more reliable
        elif conc_pct > 30:
            confidence = 0.4  # dispersed = less reliable
        else:
            confidence = 0.5
    else:
        confidence = 0.45

    avg_cost = _get(chip_data, "avg_cost")
    cost_hint = f"成本:{avg_cost}" if avg_cost else ""
    facts = [f"获利:{profit_pct:.0f}%", f"集中度:{concentration or 'N/A'}"]
    if cost_hint:
        facts.append(cost_hint)
    return NormalizedSignal(
        dimension="chip",
        signal=signal,
        score=float(score),
        confidence=confidence,
        key_facts=facts,
    )


def _normalize_sentiment(
    sentiment_score: Optional[float],
    news_context: str,
) -> NormalizedSignal:
    """News / sentiment polarity.

    Uses simple keyword heuristics since we don't have a dedicated
    sentiment model.  Confidence is deliberately low because these are
    rough estimates.
    """
    if not news_context:
        if sentiment_score is not None:
            score = float(sentiment_score)
            signal = "bullish" if score >= 60 else "bearish" if score <= 40 else "neutral"
            return NormalizedSignal(
                dimension="sentiment",
                signal=signal,
                score=score,
                confidence=0.4,
                key_facts=["基于历史评分，无新舆情"],
            )
        return NormalizedSignal(dimension="sentiment")

    risk_keywords = ["减持", "处罚", "罚款", "立案", "预亏", "亏损", "诉讼", "违规",
                     "风险提示", "解禁", "净流出"]
    positive_keywords = ["业绩预增", "合同", "中标", "回购", "增持", "突破",
                         "新产品", "政策利好", "放量突破", "涨停"]

    risk_count = sum(1 for kw in risk_keywords if kw in news_context)
    pos_count = sum(1 for kw in positive_keywords if kw in news_context)

    net = pos_count - risk_count
    if net >= 2:
        signal = "bullish"
        base = 65
    elif net <= -2:
        signal = "bearish"
        base = 35
    elif net >= 1:
        signal = "bullish"
        base = 55
    elif net <= -1:
        signal = "bearish"
        base = 45
    else:
        signal = "neutral"
        base = 50

    # Blend with historical sentiment score if available
    if sentiment_score is not None:
        score = (base + float(sentiment_score)) / 2
    else:
        score = float(base)

    confidence = min(0.5, 0.2 + (abs(net) * 0.08))
    facts = [f"利好信号:{pos_count}", f"风险信号:{risk_count}"]
    return NormalizedSignal(
        dimension="sentiment",
        signal=signal,
        score=score,
        confidence=confidence,
        key_facts=facts,
    )


def _normalize_valuation(realtime_quote: Any) -> NormalizedSignal:
    """Valuation and market activity from real-time quote.

    Uses PE, PB, and turnover rate to create a blended valuation signal.
    Confidence is intentionally capped at 0.5 because valuation
    interpretation is highly industry-dependent.
    """
    if realtime_quote is None:
        return NormalizedSignal(dimension="valuation")

    pe = _get(realtime_quote, "pe_ratio")
    pb = _get(realtime_quote, "pb_ratio")
    turnover = _get(realtime_quote, "turnover_rate")

    scores: List[float] = []
    facts: List[str] = []

    # --- PE ratio ---
    if pe is not None:
        pe_f = float(pe)
        if pe_f <= 0:
            pe_score = 50  # negative earnings — can't judge
            facts.append(f"PE:亏损")
        elif pe_f < 15:
            pe_score = 65
            facts.append(f"PE:{pe_f:.1f}")
        elif pe_f < 30:
            pe_score = 55
            facts.append(f"PE:{pe_f:.1f}")
        elif pe_f < 50:
            pe_score = 45
            facts.append(f"PE:{pe_f:.1f}")
        else:
            pe_score = 35
            facts.append(f"PE:{pe_f:.1f}")
        scores.append(pe_score)

    # --- PB ratio ---
    if pb is not None:
        pb_f = float(pb)
        if pb_f < 1:
            pb_score = 55  # below book — could be value or distressed
            facts.append(f"PB:{pb_f:.2f}")
        elif pb_f < 3:
            pb_score = 55
            facts.append(f"PB:{pb_f:.2f}")
        elif pb_f < 10:
            pb_score = 45
            facts.append(f"PB:{pb_f:.2f}")
        else:
            pb_score = 35
            facts.append(f"PB:{pb_f:.2f}")
        scores.append(pb_score)

    # --- Turnover rate ---
    if turnover is not None:
        t_f = float(turnover)
        if t_f < 1:
            t_score = 45  # dormant
            facts.append(f"换手:{t_f:.1f}%")
        elif t_f < 5:
            t_score = 55  # normal activity
            facts.append(f"换手:{t_f:.1f}%")
        elif t_f < 10:
            t_score = 50  # active — could go either way
            facts.append(f"换手:{t_f:.1f}%")
        else:
            t_score = 35  # speculative — distribution risk
            facts.append(f"换手:{t_f:.1f}%")
        scores.append(t_score)

    if not scores:
        return NormalizedSignal(dimension="valuation")

    score = sum(scores) / len(scores)
    signal = "bullish" if score >= 60 else "bearish" if score <= 40 else "neutral"
    confidence = min(0.5, 0.25 + (len(scores) * 0.08))  # more data = slightly more confident

    return NormalizedSignal(
        dimension="valuation",
        signal=signal,
        score=round(score, 1),
        confidence=round(confidence, 2),
        key_facts=facts[:3],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _normalize_divergence(trend_result: Any) -> NormalizedSignal:
    """MACD / RSI divergence — hidden trend reversal warning.

    Bullish divergence (price lower low, indicator higher low) signals
    weakening selling pressure.  Bearish divergence (price higher high,
    indicator lower high) signals weakening buying pressure.
    """
    if trend_result is None:
        return NormalizedSignal(dimension="divergence")

    macd_div = _get(trend_result, "macd_divergence", "")
    rsi_div = _get(trend_result, "rsi_divergence", "")

    facts: List[str] = []
    bearish_count = 0
    bullish_count = 0

    if macd_div == "bearish":
        bearish_count += 1
        facts.append("MACD顶背离")
    elif macd_div == "bullish":
        bullish_count += 1
        facts.append("MACD底背离")

    if rsi_div == "bearish":
        bearish_count += 1
        facts.append("RSI顶背离")
    elif rsi_div == "bullish":
        bullish_count += 1
        facts.append("RSI底背离")

    if not facts:
        return NormalizedSignal(dimension="divergence")

    # Score: the more indicators confirm, the stronger the signal
    if bearish_count > 0 and bullish_count > 0:
        # Mixed — both types detected, treat as neutral warning
        return NormalizedSignal(
            dimension="divergence",
            signal="neutral",
            score=45.0,
            confidence=0.4,
            key_facts=facts + ["多指标背离冲突"],
        )

    if bearish_count >= 1:
        score_weight = 35 - (bearish_count - 1) * 10  # 35(1), 25(2)
        return NormalizedSignal(
            dimension="divergence",
            signal="bearish",
            score=float(max(score_weight, 10)),
            confidence=min(0.7, 0.4 + bearish_count * 0.15),
            key_facts=facts,
        )

    # bullish
    score_weight = 65 + (bullish_count - 1) * 10  # 65(1), 75(2)
    return NormalizedSignal(
        dimension="divergence",
        signal="bullish",
        score=float(min(score_weight, 90)),
        confidence=min(0.7, 0.4 + bullish_count * 0.15),
        key_facts=facts,
    )


def detect_conflicts(signals: List[NormalizedSignal]) -> List[str]:
    """Identify serious contradictions between different analytical dimensions.

    Returns a list of human-readable warning strings to be highlighted
    in the LLM prompt.
    """
    sig_map = {s.dimension: s for s in signals}
    warnings = []

    # 1. Price Trend vs Divergence (Trend Exhaustion)
    trend = sig_map.get("trend")
    div = sig_map.get("divergence")
    if trend and div and trend.signal == "bullish" and div.signal == "bearish":
        warnings.append("价格趋势向上但存在顶背离，警惕趋势衰竭及高位诱多风险。")
    if trend and div and trend.signal == "bearish" and div.signal == "bullish":
        warnings.append("价格趋势向下但存在底背离，关注下行压力减弱及反弹契机。")

    # 2. Technical vs Volume (Volume-Price Divergence)
    vol = sig_map.get("volume")
    if trend and vol and trend.signal == "bullish" and vol.signal == "bearish":
        warnings.append("股价上涨但成交量缩减（量价背离），显示上涨动力不足。")
    if trend and vol and trend.signal == "bearish" and vol.signal == "bearish":
        # Both bearish trend + bearish volume (heavy down) = acceleration
        warnings.append("价格下跌伴随放量，显示恐慌盘抛售，下行压力极大。")

    # 3. Technical vs Sentiment (News Override)
    sent = sig_map.get("sentiment")
    if trend and sent and trend.signal == "bullish" and sent.signal == "bearish":
        warnings.append("技术面走强但舆情情报显着偏负面，需防范突发利空对趋势的破坏。")

    # 4. Technical vs Valuation (Bubble/Deep Value)
    val = sig_map.get("valuation")
    if trend and val and trend.signal == "bullish" and val.signal == "bearish" and val.score < 30:
        warnings.append("股价虽强但估值已进入极高风险区（泡沫化），追高风险极大。")

    return warnings


def _normalize_fundamental_growth(fundamental_context: Any) -> NormalizedSignal:
    """Fundamental growth health from revenue / profit / ROE data.

    Reads the ``growth`` and ``earnings`` blocks from the fundamental
    context produced by ``FundamentalDataAdapter``.
    """
    if not fundamental_context or not isinstance(fundamental_context, dict):
        return NormalizedSignal(dimension="fundamental_growth")

    growth = fundamental_context.get("growth") or {}
    earnings = fundamental_context.get("earnings") or {}
    fin_report = earnings.get("financial_report") or {}

    revenue_yoy = growth.get("revenue_yoy")
    profit_yoy = growth.get("net_profit_yoy")
    roe = growth.get("roe") or fin_report.get("roe")

    scores: List[float] = []
    facts: List[str] = []

    # --- Revenue YoY ---
    if revenue_yoy is not None:
        ry = float(revenue_yoy)
        if ry > 20:
            scores.append(70)
            facts.append(f"营收+{ry:.0f}%")
        elif ry > 5:
            scores.append(60)
            facts.append(f"营收+{ry:.0f}%")
        elif ry > 0:
            scores.append(50)
            facts.append(f"营收+{ry:.0f}%")
        elif ry > -10:
            scores.append(35)
            facts.append(f"营收{ry:.0f}%")
        else:
            scores.append(20)
            facts.append(f"营收{ry:.0f}%")

    # --- Net profit YoY ---
    if profit_yoy is not None:
        np_y = float(profit_yoy)
        if np_y > 30:
            scores.append(75)
            facts.append(f"利润+{np_y:.0f}%")
        elif np_y > 10:
            scores.append(60)
            facts.append(f"利润+{np_y:.0f}%")
        elif np_y > 0:
            scores.append(50)
            facts.append(f"利润+{np_y:.0f}%")
        elif np_y > -15:
            scores.append(35)
            facts.append(f"利润{np_y:.0f}%")
        else:
            scores.append(20)
            facts.append(f"利润{np_y:.0f}%")

    # --- ROE ---
    if roe is not None:
        r = float(roe)
        if r > 15:
            scores.append(65)
            facts.append(f"ROE:{r:.1f}%")
        elif r > 8:
            scores.append(55)
            facts.append(f"ROE:{r:.1f}%")
        else:
            scores.append(40)
            facts.append(f"ROE:{r:.1f}%")

    if not scores:
        return NormalizedSignal(dimension="fundamental_growth")

    score = sum(scores) / len(scores)
    signal = "bullish" if score >= 60 else "bearish" if score <= 40 else "neutral"
    confidence = min(0.6, 0.2 + len(scores) * 0.12)

    return NormalizedSignal(
        dimension="fundamental_growth",
        signal=signal,
        score=round(score, 1),
        confidence=round(confidence, 2),
        key_facts=facts[:3],
    )


def normalize_all_signals(
    *,
    trend_result: Any = None,
    chip_data: Any = None,
    sentiment_score: Optional[float] = None,
    news_context: str = "",
    realtime_quote: Any = None,
    fundamental_context: Any = None,
) -> List[NormalizedSignal]:
    """Normalise all available computation outputs into a standard signal list.

    Each ``NormalizedSignal`` covers one analytical dimension.  Dimensions
    whose input is ``None`` are still returned with default "neutral" values
    so the caller (or prompt builder) can decide how to display gaps.
    """
    signals = [
        _normalize_trend(trend_result),
        _normalize_volume(trend_result),
        _normalize_momentum(trend_result),
        _normalize_chip(chip_data),
        _normalize_sentiment(sentiment_score, news_context),
        _normalize_valuation(realtime_quote),
        _normalize_divergence(trend_result),
        _normalize_fundamental_growth(fundamental_context),
    ]
    return signals
