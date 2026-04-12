# -*- coding: utf-8 -*-
"""
AI 分析辅助函数与数据填充逻辑
"""

import logging
import math
from typing import Optional, Dict, Any, List

from src.data.stock_mapping import STOCK_NAME_MAP
from src.report_language import localize_chip_health
from src.schemas.analysis_result import AnalysisResult

logger = logging.getLogger(__name__)

# ---------- chip_structure fallback (Issue #589) ----------

_CHIP_KEYS: tuple = ("profit_ratio", "avg_cost", "concentration", "chip_health")


def _is_value_placeholder(v: Any) -> bool:
    """True if value is empty or placeholder (N/A, 数据缺失, etc.)."""
    if v is None:
        return True
    if isinstance(v, (int, float)) and v == 0:
        return True
    s = str(v).strip().lower()
    return s in ("", "n/a", "na", "数据缺失", "未知", "data unavailable", "unknown", "tbd")


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Safely convert to float; return default on failure. Private helper for chip fill."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        try:
            return default if math.isnan(float(v)) else float(v)
        except (ValueError, TypeError):
            return default
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _derive_chip_health(profit_ratio: float, concentration_90: float, language: str = "zh") -> str:
    """Derive chip_health from profit_ratio and concentration_90."""
    if profit_ratio >= 0.9:
        return localize_chip_health("警惕", language)  # 获利盘极高
    if concentration_90 >= 0.25:
        return localize_chip_health("警惕", language)  # 筹码分散
    if concentration_90 < 0.15 and 0.3 <= profit_ratio < 0.9:
        return localize_chip_health("健康", language)  # 集中且获利比例适中
    return localize_chip_health("一般", language)


def _build_chip_structure_from_data(chip_data: Any, language: str = "zh") -> Dict[str, Any]:
    """Build chip_structure dict from ChipDistribution or dict."""
    if hasattr(chip_data, "profit_ratio"):
        pr = _safe_float(chip_data.profit_ratio)
        ac = chip_data.avg_cost
        c90 = _safe_float(chip_data.concentration_90)
    else:
        d = chip_data if isinstance(chip_data, dict) else {}
        pr = _safe_float(d.get("profit_ratio"))
        ac = d.get("avg_cost")
        c90 = _safe_float(d.get("concentration_90"))
    chip_health = _derive_chip_health(pr, c90, language=language)
    return {
        "profit_ratio": f"{pr:.1%}",
        "avg_cost": ac if (ac is not None and _safe_float(ac) != 0.0) else "N/A",
        "concentration": f"{c90:.2%}",
        "chip_health": chip_health,
    }


def _identify_chip_pattern(
    profit_ratio: float, 
    concentration_90: float, 
    current_price: float, 
    avg_cost: float
) -> Tuple[str, str]:
    """
    识别筹码形态 (Pattern Recognition)
    
    Returns:
        Tuple[pattern_name, description]
    """
    if concentration_90 < 0.10:
        if current_price > avg_cost * 1.1:
            return "高位单峰密集", "主力获利丰厚，警惕派发风险"
        elif current_price < avg_cost * 0.9:
            return "低位超跌密集", "股价处于筹码密集区下方，存在反弹动力"
        else:
            return "低位单峰密集", "主力高度控盘，底部支撑极强，爆发潜力大"
    
    if concentration_90 > 0.25:
        return "筹码高度分散", "多空分歧大，上涨抛压重，短期难有大行情"
    
    if profit_ratio > 0.95:
        return "全员获利", "几乎无套牢盘，上攻无压力，但需防范获利盘踩踏"
    elif profit_ratio < 0.05:
        return "深度套牢", "多头信心涣散，抛压枯竭，等待绝望中的反弹"
        
    return "筹码结构平稳", "分布相对均衡，跟随趋势为主"


def fill_chip_structure_if_needed(result: AnalysisResult, chip_data: Any) -> None:
    """当存在筹码数据时，填充占位字段并增加深度形态识别 (Issue #589)"""
    if not result or not chip_data:
        return
    try:
        if not result.dashboard:
            result.dashboard = {}
        dash = result.dashboard
        dp = dash.get("data_perspective") or {}
        dash["data_perspective"] = dp
        cs = dp.get("chip_structure") or {}
        
        filled = _build_chip_structure_from_data(
            chip_data,
            language=getattr(result, "report_language", "zh"),
        )
        
        # 提取核心指标用于形态识别
        if hasattr(chip_data, "profit_ratio"):
            pr = _safe_float(chip_data.profit_ratio)
            c90 = _safe_float(chip_data.concentration_90)
            ac = _safe_float(chip_data.avg_cost)
        else:
            d = chip_data if isinstance(chip_data, dict) else {}
            pr = _safe_float(d.get("profit_ratio"))
            c90 = _safe_float(d.get("concentration_90"))
            ac = _safe_float(d.get("avg_cost"))
            
        curr_price = getattr(result, "current_price", 0.0) or 0.0
        
        # 深度形态识别
        pattern, pattern_desc = _identify_chip_pattern(pr, c90, curr_price, ac)
        filled["pattern"] = pattern
        filled["pattern_description"] = pattern_desc
        
        # 合并 LLM 结果与量化识别结果
        merged = dict(cs)
        for k in _CHIP_KEYS:
            if _is_value_placeholder(merged.get(k)):
                merged[k] = filled[k]
        
        # 始终注入识别到的形态（量化识别通常比 LLM 更准）
        merged["pattern"] = pattern
        merged["pattern_desc"] = pattern_desc
        
        dp["chip_structure"] = merged
        logger.info(f"[筹码深度识别] {result.code} 识别为: {pattern}")
    except Exception as e:
        logger.warning("[chip_structure] 深度分析失败: %s", e)


_PRICE_POS_KEYS = ("ma5", "ma10", "ma20", "bias_ma5", "bias_status", "current_price", "support_level", "resistance_level")


def fill_price_position_if_needed(
    result: AnalysisResult,
    trend_result: Any = None,
    realtime_quote: Any = None,
) -> None:
    """Fill missing price_position fields from trend_result / realtime data (in-place)."""
    if not result:
        return
    try:
        if not result.dashboard:
            result.dashboard = {}
        dash = result.dashboard
        dp = dash.get("data_perspective") or {}
        dash["data_perspective"] = dp
        pp = dp.get("price_position") or {}

        computed: Dict[str, Any] = {}
        if trend_result:
            tr = trend_result if isinstance(trend_result, dict) else (
                trend_result.__dict__ if hasattr(trend_result, "__dict__") else {}
            )
            computed["ma5"] = tr.get("ma5")
            computed["ma10"] = tr.get("ma10")
            computed["ma20"] = tr.get("ma20")
            computed["bias_ma5"] = tr.get("bias_ma5")
            computed["current_price"] = tr.get("current_price")
            support_levels = tr.get("support_levels") or []
            resistance_levels = tr.get("resistance_levels") or []
            if support_levels:
                computed["support_level"] = support_levels[0]
            if resistance_levels:
                computed["resistance_level"] = resistance_levels[0]
        if realtime_quote:
            rq = realtime_quote if isinstance(realtime_quote, dict) else (
                realtime_quote.to_dict() if hasattr(realtime_quote, "to_dict") else {}
            )
            if _is_value_placeholder(computed.get("current_price")):
                computed["current_price"] = rq.get("price")

        filled = False
        for k in _PRICE_POS_KEYS:
            if _is_value_placeholder(pp.get(k)) and not _is_value_placeholder(computed.get(k)):
                pp[k] = computed[k]
                filled = True
        if filled:
            dp["price_position"] = pp
            logger.info("[price_position] Filled placeholder fields from computed data")
    except Exception as e:
        logger.warning("[price_position] Fill failed, skipping: %s", e)


def _format_percent(value: Optional[float]) -> str:
    """格式化百分比显示"""
    if value is None:
        return 'N/A'
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return 'N/A'


def _format_price(value: Optional[float]) -> str:
    """格式化价格显示"""
    if value is None:
        return 'N/A'
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return 'N/A'


def _format_volume(volume: Optional[float]) -> str:
    """格式化成交量显示"""
    if volume is None:
        return 'N/A'
    if volume >= 1e8:
        return f"{volume / 1e8:.2f} 亿股"
    elif volume >= 1e4:
        return f"{volume / 1e4:.2f} 万股"
    else:
        return f"{volume:.0f} 股"


def _format_amount(amount: Optional[float]) -> str:
    """格式化成交额显示"""
    if amount is None:
        return 'N/A'
    if amount >= 1e8:
        return f"{amount / 1e8:.2f} 亿元"
    elif amount >= 1e4:
        return f"{amount / 1e4:.2f} 万元"
    else:
        return f"{amount:.0f} 元"


def build_market_snapshot(context: Dict[str, Any]) -> Dict[str, Any]:
    """构建当日行情快照（展示用）"""
    today = context.get('today', {}) or {}
    realtime = context.get('realtime', {}) or {}
    yesterday = context.get('yesterday', {}) or {}

    prev_close = yesterday.get('close')
    close = today.get('close')
    high = today.get('high')
    low = today.get('low')

    amplitude = None
    change_amount = None
    if prev_close not in (None, 0) and high is not None and low is not None:
        try:
            amplitude = (float(high) - float(low)) / float(prev_close) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            amplitude = None
    if prev_close is not None and close is not None:
        try:
            change_amount = float(close) - float(prev_close)
        except (TypeError, ValueError):
            change_amount = None

    snapshot = {
        "date": context.get('date', '未知'),
        "close": _format_price(close),
        "open": _format_price(today.get('open')),
        "high": _format_price(high),
        "low": _format_price(low),
        "prev_close": _format_price(prev_close),
        "pct_chg": _format_percent(today.get('pct_chg')),
        "change_amount": _format_price(change_amount),
        "amplitude": _format_percent(amplitude),
        "volume": _format_volume(today.get('volume')),
        "amount": _format_amount(today.get('amount')),
    }

    if realtime:
        snapshot.update({
            "price": _format_price(realtime.get('price')),
            "volume_ratio": realtime.get('volume_ratio', 'N/A'),
            "turnover_rate": _format_percent(realtime.get('turnover_rate')),
            "source": getattr(realtime.get('source'), 'value', realtime.get('source', 'N/A')),
        })

    return snapshot


def get_stock_name_multi_source(
    stock_code: str,
    context: Optional[Dict] = None,
    data_manager = None
) -> str:
    """
    多来源获取股票中文名称
    """
    # 1. 从上下文获取（实时行情数据）
    if context:
        # 优先从 stock_name 字段获取
        if context.get('stock_name'):
            name = context['stock_name']
            if name and not name.startswith('股票'):
                return name

        # 其次从 realtime 数据获取
        if 'realtime' in context and context['realtime'].get('name'):
            return context['realtime']['name']

    # 2. 从静态映射表获取
    if stock_code in STOCK_NAME_MAP:
        return STOCK_NAME_MAP[stock_code]

    # 3. 从数据源获取
    if data_manager is None:
        try:
            from data_provider.base import DataFetcherManager
            data_manager = DataFetcherManager()
        except Exception as e:
            logger.debug(f"无法初始化 DataFetcherManager: {e}")

    if data_manager:
        try:
            name = data_manager.get_stock_name(stock_code)
            if name:
                # 更新缓存
                STOCK_NAME_MAP[stock_code] = name
                return name
        except Exception as e:
            logger.debug(f"从数据源获取股票名称失败: {e}")

    # 4. 返回默认名称
    return f'股票{stock_code}'
