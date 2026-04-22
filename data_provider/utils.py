# -*- coding: utf-8 -*-
"""
Fetcher 共享工具层 - 代码规范化、市场判定与异常处理
"""

import logging
import re
import random
from typing import Any, Optional, Tuple, List

logger = logging.getLogger(__name__)

# === 标准化列名定义 ===
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']

ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")

# === 现代浏览器 User-Agent 池 (Issue #612) ===
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
]

import time
from threading import RLock

class RealtimeCache:
    """实时行情缓存（单例）"""
    _instance = None
    _lock = RLock()
    
    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._cache = {}
        return cls._instance

    @property
    def data(self) -> Optional[Any]:
        """向后兼容属性"""
        return None

    def set(self, key: str, value: Any, ttl: int = 600):
        with self._lock:
            self._cache[key] = {
                'data': value,
                'expire_at': time.time() + ttl
            }

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if not entry: return None
            if time.time() > entry['expire_at']:
                del self._cache[key]
                return None
            return entry['data']

def cache_realtime_quote(code: str, quote: Any, ttl: int = 600):
    """便捷存入缓存"""
    RealtimeCache().set(code, quote, ttl)

def get_cached_realtime_quote(code: str) -> Optional[Any]:
    """便捷读取缓存"""
    return RealtimeCache().get(code)


def pick_random_user_agent() -> str:
    """随机选择一个 User-Agent"""
    return random.choice(DEFAULT_USER_AGENTS)

def classify_http_error(exc: Exception) -> Tuple[str, str]:
    """将 HTTP 请求异常分类为稳定的日志类别"""
    err_str = str(exc).lower()
    if "timeout" in err_str: return "网络超时", "连接目标服务器超时"
    if "connection" in err_str or "reachable" in err_str: return "连接失败", "无法建立网络连接"
    if "429" in err_str or "rate limit" in err_str: return "速率限制", "触发目标 API 频次限制"
    if "403" in err_str or "forbidden" in err_str: return "访问被拒", "IP 被封禁或权限不足"
    return type(exc).__name__, str(exc)

def build_history_failure_message(stock_code: str, beg_date: str, end_date: str, exc: Exception, elapsed: float = 0.0, is_etf: bool = False) -> str:
    """构建标准化的历史数据获取失败日志消息"""
    cat, reason = classify_http_error(exc)
    label = "ETF" if is_etf else "股票"
    return f"[{cat}] {label} {stock_code} 历史 K 线获取失败 | 范围: {beg_date} ~ {end_date} | 耗时: {elapsed:.2f}s | 原因: {reason}"

def build_realtime_failure_message(stock_code: str, exc: Exception, elapsed: float = 0.0) -> str:
    """构建标准化的实时行情获取失败日志消息"""
    cat, reason = classify_http_error(exc)
    return f"[{cat}] 实时行情 {stock_code} 获取失败 | 耗时: {elapsed:.2f}s | 原因: {reason}"

def unwrap_exception(exc: Exception) -> Exception:
    """获取链式异常的最深层原因"""
    current = exc
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        next_exc = current.__cause__ or current.__context__
        if next_exc is None: break
        current = next_exc
    return current

def summarize_exception(exc: Exception) -> Tuple[str, str]:
    """构建用于日志的稳定异常摘要"""
    root = unwrap_exception(exc)
    error_type = type(root).__name__
    message = str(exc).strip() or str(root).strip() or error_type
    return error_type, " ".join(message.split())

def canonical_stock_code(code: str) -> str:
    """返回标准大写的股票代码"""
    return (code or "").strip().upper()


def normalize_stock_code(stock_code: str) -> str:
    """标准化股票代码，移除市场前缀/后缀"""
    code = (stock_code or "").strip().upper()
    if code.startswith('HK') and not code.startswith('HK.'):
        c = code[2:]
        if c.isdigit() and 1 <= len(c) <= 5: return f"HK{c.zfill(5)}"
    if code.startswith(('SH', 'SZ')) and not code.startswith(('SH.', 'SZ.')):
        c = code[2:]
        if c.isdigit() and len(c) in (5, 6): return c
    if code.startswith('BJ') and not code.startswith('BJ.'):
        c = code[2:]
        if c.isdigit() and len(c) == 6: return c
    if '.' in code:
        base, suffix = code.rsplit('.', 1)
        if suffix == 'HK' and base.isdigit() and 1 <= len(base) <= 5: return f"HK{base.zfill(5)}"
        if suffix in ('SH', 'SZ', 'SS', 'BJ') and base.isdigit(): return base
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
    if normalized.isdigit() and len(normalized) == 5: return True
    return False

def _is_etf_code(code: str) -> bool:
    """判定 A 股 ETF 基金代码"""
    normalized = normalize_stock_code(code)
    return normalized.isdigit() and len(normalized) == 6 and normalized.startswith(ETF_PREFIXES)

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
    return normalized.startswith(('688', '300'))
