# -*- coding: utf-8 -*-
"""
AkShare 工具函数集合
拆分自 akshare_fetcher.py，减少单文件大小。
"""

import logging
from typing import List, Optional, Tuple

from .utils import is_bse_code, is_st_stock, is_kc_cy_stock, normalize_stock_code
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource, safe_float, safe_int
from .us_index_mapping import is_us_index_code, is_us_stock_code

logger = logging.getLogger(__name__)

SINA_REALTIME_ENDPOINT = "hq.sinajs.cn/list"
TENCENT_REALTIME_ENDPOINT = "qt.gtimg.cn/q"


def _is_etf_code(stock_code: str) -> bool:
    """
    判断代码是否为 ETF 基金

    ETF 代码规则：
    - 上交所 ETF: 51xxxx, 52xxxx, 56xxxx, 58xxxx
    - 深交所 ETF: 15xxxx, 16xxxx, 18xxxx

    Args:
        stock_code: 股票/基金代码

    Returns:
        True 表示是 ETF 代码，False 表示是普通股票代码
    """
    etf_prefixes = ('51', '52', '56', '58', '15', '16', '18')
    code = stock_code.strip().split('.')[0]
    return code.startswith(etf_prefixes) and len(code) == 6


def _is_hk_code(stock_code: str) -> bool:
    """
    判断代码是否为港股

    港股代码规则：
    - 5位数字代码，如 '00700' (腾讯控股)
    - 部分港股代码可能带有前缀，如 'hk00700', 'hk1810'

    Args:
        stock_code: 股票代码

    Returns:
        True 表示是港股代码，False 表示不是港股代码
    """
    # 去除可能的 'hk' 前缀并检查是否为纯数字
    code = stock_code.strip().lower()
    if code.endswith('.hk'):
        numeric_part = code[:-3]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    if code.startswith('hk'):
        numeric_part = code[2:]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    # 无前缀时，5位纯数字才视为港股（避免误判 A 股代码）
    return code.isdigit() and len(code) == 5


def is_hk_stock_code(stock_code: str) -> bool:
    """
    Public API: determine if a stock code is a Hong Kong stock.

    Delegates to _is_hk_code for internal compatibility.

    Args:
        stock_code: Stock code (e.g. '00700', 'hk00700')

    Returns:
        True if HK stock, False otherwise
    """
    return _is_hk_code(stock_code)


def _is_us_code(stock_code: str) -> bool:
    """
    判断代码是否为美股股票（不包括美股指数）。

    委托给 us_index_mapping 模块的 is_us_stock_code()。

    Args:
        stock_code: 股票代码

    Returns:
        True 表示是美股代码，False 表示不是美股代码
    """
    return is_us_stock_code(stock_code)


def _to_sina_tx_symbol(stock_code: str) -> str:
    """Convert 6-digit A-share code to sh/sz/bj prefixed symbol for Sina/Tencent APIs."""
    base = (stock_code.strip().split(".")[0] if "." in stock_code else stock_code).strip()
    if is_bse_code(base):
        return f"bj{base}"
    # Shanghai: 60xxxx, 5xxxx (ETF), 90xxxx (B-shares)
    if base.startswith(("6", "5", "90")):
        return f"sh{base}"
    return f"sz{base}"


def _parse_sina_quote(fields: list, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
    """解析新浪财经实时行情数据"""
    # 字段顺序：0:名称 1:今开 2:昨收 3:最新价 4:最高 5:最低
    # 6:买一价 7:卖一价 8:成交量(股) 9:成交额(元)
    if len(fields) < 32:
        return None
    price = safe_float(fields[3])
    pre_close = safe_float(fields[2])
    change_pct = None
    change_amount = None
    if price and pre_close and pre_close > 0:
        change_amount = price - pre_close
        change_pct = (change_amount / pre_close) * 100
    return UnifiedRealtimeQuote(
        code=stock_code,
        name=fields[0],
        source=RealtimeSource.AKSHARE_SINA,
        price=price,
        change_pct=change_pct,
        change_amount=change_amount,
        volume=safe_int(fields[8]),
        amount=safe_float(fields[9]),
        open_price=safe_float(fields[1]),
        high=safe_float(fields[4]),
        low=safe_float(fields[5]),
        pre_close=pre_close,
    )


def _parse_tencent_quote(fields: list, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
    """解析腾讯财经实时行情数据"""
    # 字段顺序：1:名称 2:代码 3:最新价 4:昨收 5:今开 6:成交量(手)
    # 31:涨跌额 32:涨跌幅 33:最高 34:最低 38:换手率 43:振幅
    # 44:流通市值(亿) 45:总市值(亿) 46:市净率 49:量比
    if len(fields) < 45:
        return None
    return UnifiedRealtimeQuote(
        code=stock_code,
        name=fields[1] if len(fields) > 1 else "",
        source=RealtimeSource.TENCENT,
        price=safe_float(fields[3]),
        change_pct=safe_float(fields[32]),
        change_amount=safe_float(fields[31]) if len(fields) > 31 else None,
        volume=safe_int(fields[6]) * 100 if fields[6] else None,
        open_price=safe_float(fields[5]),
        high=safe_float(fields[33]) if len(fields) > 33 else None,
        low=safe_float(fields[34]) if len(fields) > 34 else None,
        pre_close=safe_float(fields[4]),
        turnover_rate=safe_float(fields[38]) if len(fields) > 38 else None,
        amplitude=safe_float(fields[43]) if len(fields) > 43 else None,
        volume_ratio=safe_float(fields[49]) if len(fields) > 49 else None,
        pe_ratio=safe_float(fields[39]) if len(fields) > 39 else None,
        pb_ratio=safe_float(fields[46]) if len(fields) > 46 else None,
        circ_mv=safe_float(fields[44]) * 100000000 if len(fields) > 44 and fields[44] else None,
        total_mv=safe_float(fields[45]) * 100000000 if len(fields) > 45 and fields[45] else None,
    )
