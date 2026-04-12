# -*- coding: utf-8 -*-
"""
Fetcher 共享工具层 - 代码规范化、市场判定与异常处理
"""

import logging
import re
from typing import Any, Optional, Tuple, List

logger = logging.getLogger(__name__)

# === 标准化列名定义 ===
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']

ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")


def unwrap_exception(exc: Exception) -> Exception:
    """获取链式异常的最深层原因"""
    current = exc
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        next_exc = current.__cause__ or current.__context__
        if next_exc is None:
            break
        current = next_exc
    return current


def summarize_exception(exc: Exception) -> Tuple[str, str]:
    """构建用于日志的稳定异常摘要"""
    root = unwrap_exception(exc)
    error_type = type(root).__name__
    message = str(exc).strip() or str(root).strip() or error_type
    return error_type, " ".join(message.split())


def normalize_stock_code(stock_code: str) -> str:
    """标准化股票代码，移除市场前缀/后缀"""
    code = stock_code.strip()
    upper = code.upper()

    if upper.startswith('HK') and not upper.startswith('HK.'):
        candidate = upper[2:]
        if candidate.isdigit() and 1 <= len(candidate) <= 5:
            return f"HK{candidate.zfill(5)}"

    if upper.startswith(('SH', 'SZ')) and not upper.startswith('SH.') and not upper.startswith('SZ.'):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) in (5, 6):
            return candidate

    if upper.startswith('BJ') and not upper.startswith('BJ.'):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) == 6:
            return candidate

    if '.' in code:
        base, suffix = code.rsplit('.', 1)
        if suffix.upper() == 'HK' and base.isdigit() and 1 <= len(base) <= 5:
            return f"HK{base.zfill(5)}"
        if suffix.upper() in ('SH', 'SZ', 'SS', 'BJ') and base.isdigit():
            return base

    return code


def _is_us_market(code: str) -> bool:
    """判断是否为美股/美股指数代码"""
    from .us_index_mapping import is_us_stock_code, is_us_index_code
    normalized = (code or "").strip().upper()
    return is_us_index_code(normalized) or is_us_stock_code(normalized)


def _is_hk_market(code: str) -> bool:
    """判定是否为港股代码"""
    normalized = (code or "").strip().upper()
    if normalized.endswith(".HK"):
        base = normalized[:-3]
        return base.isdigit() and 1 <= len(base) <= 5
    if normalized.startswith("HK"):
        digits = normalized[2:]
        return digits.isdigit() and 1 <= len(digits) <= 5
    if normalized.isdigit() and len(normalized) == 5:
        return True
    return False


def _is_etf_code(code: str) -> bool:
    """判定 A 股 ETF 基金代码"""
    normalized = normalize_stock_code(code)
    return (
        normalized.isdigit()
        and len(normalized) == 6
        and normalized.startswith(ETF_PREFIXES)
    )


def _market_tag(code: str) -> str:
    """返回市场标签: cn/us/hk"""
    if _is_us_market(code): return "us"
    if _is_hk_market(code): return "hk"
    return "cn"


def is_bse_code(code: str) -> bool:
    """判断是否为北交所代码"""
    c = (code or "").strip().split(".")[0]
    if len(c) != 6 or not c.isdigit(): return False
    if c.startswith("900"): return False
    return c.startswith(("92", "43", "81", "82", "83", "87", "88"))


def is_st_stock(name: str) -> bool:
    """根据名称判断是否为 ST 股"""
    return 'ST' in (name or "").upper()


def is_kc_cy_stock(code: str) -> bool:
    """判断是否为科创板或创业板代码"""
    normalized = normalize_stock_code(code)
    # 科创板 688, 创业板 300
    return normalized.startswith(('688', '300'))
