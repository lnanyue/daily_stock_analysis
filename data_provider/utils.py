# -*- coding: utf-8 -*-
"""
===================================
Fetcher 共享工具层
===================================

集中管理各数据源间的重复代码：
- 错误分类
- 失败日志构建
- User-Agent 池
- 实时行情缓存
"""

import logging
import time
import random
from typing import Tuple, Optional, Any, Dict

logger = logging.getLogger(__name__)

# === 错误分类关键词 ===

HTTP_ERROR_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "remote_disconnect": (
        "remotedisconnected",
        "remote end closed connection without response",
        "connection aborted",
        "connection broken",
        "protocolerror",
        "chunkedencodingerror",
    ),
    "timeout": (
        "timeout",
        "timed out",
        "readtimeout",
        "connecttimeout",
    ),
    "rate_limit_or_anti_bot": (
        "banned",
        "blocked",
        "频率",
        "rate limit",
        "too many requests",
        "429",
        "限制",
        "forbidden",
        "403",
    ),
}


def classify_http_error(exc: Exception) -> Tuple[str, str]:
    """
    Classify HTTP request failures into stable categories.
    Supports both httpx and requests exception types.

    Returns:
        (category, detail) tuple. Categories: timeout, remote_disconnect,
        rate_limit_or_anti_bot, unknown_request_error.
    """
    detail = str(exc).strip() or type(exc).__name__
    lowered = detail.lower()

    if any(keyword in lowered for keyword in HTTP_ERROR_KEYWORDS["remote_disconnect"]):
        return "remote_disconnect", detail
    if any(keyword in lowered for keyword in HTTP_ERROR_KEYWORDS["timeout"]):
        return "timeout", detail
    if any(keyword in lowered for keyword in HTTP_ERROR_KEYWORDS["rate_limit_or_anti_bot"]):
        return "rate_limit_or_anti_bot", detail
    return "unknown_request_error", detail


# === 失败消息构建 ===

def build_realtime_failure_message(
    source_name: str,
    endpoint: str,
    stock_code: str,
    symbol: str,
    category: str,
    detail: str,
    elapsed: float,
    error_type: str,
) -> str:
    """统一格式化实时行情失败日志。"""
    return (
        f"{source_name} 实时行情接口失败: endpoint={endpoint}, stock_code={stock_code}, "
        f"symbol={symbol}, category={category}, error_type={error_type}, "
        f"elapsed={elapsed:.2f}s, detail={detail}"
    )


def build_history_failure_message(
    endpoint: str,
    stock_code: str,
    instrument_type: str,
    beg_date: str,
    end_date: str,
    exc: Exception,
    elapsed: float,
) -> str:
    """统一格式化历史数据失败日志。"""
    _, detail = classify_http_error(exc)
    return (
        "历史K线接口失败: "
        f"endpoint={endpoint}, stock_code={stock_code}, "
        f"market_type={instrument_type}, range={beg_date}~{end_date}, "
        f"category=history_failed, error_type={type(exc).__name__}, elapsed={elapsed:.2f}s, detail={detail}"
    )


# === User-Agent 池 ===

DEFAULT_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


# === 实时行情缓存 ===

def _make_cache_dict(data: Any, ttl: int) -> Dict[str, Any]:
    """内部辅助 — 返回 dict 格式的缓存结构（兼容旧代码访问）。"""
    return {'data': data, 'timestamp': 0.0, 'ttl': ttl}


class RealtimeCache:
    """
    实时行情数据缓存（线程安全）。

    通过类封装替代原先的模块级 dict，提供 ttl 可配置的缓存。
    同时保持 dict-like 访问兼容（cache['data'] 等）以最小化调用方改动。
    """

    def __init__(self, ttl: int = 600):
        self.data: Any = None
        self.timestamp: float = 0.0
        self.ttl: int = ttl

    @property
    def age(self) -> float:
        """缓存年龄（秒）"""
        if self.timestamp == 0.0:
            return float('inf')
        return time.time() - self.timestamp

    @property
    def is_fresh(self) -> bool:
        """缓存是否未过期"""
        if self.data is None:
            return False
        return self.age < self.ttl

    def get(self) -> Optional[Any]:
        """获取缓存数据，过期则返回 None"""
        if self.is_fresh:
            return self.data
        return None

    def set(self, data: Any) -> None:
        """存入数据并更新时间戳"""
        self.data = data
        self.timestamp = time.time()

    def __getitem__(self, key: str) -> Any:
        """兼容 dict 风格的 cache['data'] 访问"""
        if key == 'data':
            return self.data
        if key == 'timestamp':
            return self.timestamp
        if key == 'ttl':
            return self.ttl
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        """兼容 dict 风格的 cache['data'] = x 赋值"""
        if key == 'data':
            self.data = value
        elif key == 'timestamp':
            self.timestamp = value
        elif key == 'ttl':
            self.ttl = value
        else:
            raise KeyError(key)


def pick_random_user_agent(user_agents: Optional[list] = None) -> str:
    """从 UA 池中随机选一个。"""
    pool = user_agents if user_agents is not None else DEFAULT_USER_AGENTS
    return random.choice(pool)
