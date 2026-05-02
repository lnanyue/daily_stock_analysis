# -*- coding: utf-8 -*-
"""
实时行情数据类型定义
"""

import logging
import math
import time
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class RealtimeSource(str, Enum):
    """实时行情数据源"""
    EFINANCE = "efinance"           # 东方财富（efinance库）
    OPENBB = "openbb"               # OpenBB 平台
    AKSHARE_EM = "akshare_em"       # 东方财富（akshare库）
    AKSHARE_SINA = "akshare_sina"   # 新浪财经
    AKSHARE_QQ = "akshare_qq"       # 腾讯财经
    TUSHARE = "tushare"             # Tushare Pro
    TENCENT = "tencent"             # 腾讯直连
    SINA = "sina"                   # 新浪直连
    STOOQ = "stooq"                 # Stooq 美股兜底
    FUTU = "futu"                   # 富途牛牛
    FALLBACK = "fallback"           # 降级兜底

class UnifiedRealtimeQuote(BaseModel):
    """
    统一实时行情数据结构
    
    设计原则：
    - 自动验证数据类型（Pydantic V2）
    - 统一各数据源的字段命名
    - 支持 .to_dict() 向后兼容
    """
    code: str
    name: str = ""
    source: RealtimeSource = RealtimeSource.FALLBACK

    # === 核心价格数据 ===
    price: Optional[float] = None           # 最新价
    change_pct: Optional[float] = None      # 涨跌幅(%)
    change_amount: Optional[float] = None   # 涨跌额

    # === 量价指标 ===
    volume: Optional[float] = None            # 成交量（手/股）
    amount: Optional[float] = None          # 成交额（元）
    volume_ratio: Optional[float] = None    # 量比
    turnover_rate: Optional[float] = None   # 换手率(%)
    amplitude: Optional[float] = None       # 振幅(%)

    # === 价格区间 ===
    open_price: Optional[float] = None      # 开盘价
    high: Optional[float] = None            # 最高价
    low: Optional[float] = None             # 最低价
    pre_close: Optional[float] = None       # 昨收价

    # === 估值指标 ===
    pe_ratio: Optional[float] = None        # 市盈率(动态)
    pb_ratio: Optional[float] = None        # 市净率
    total_mv: Optional[float] = None        # 总市值(元)
    circ_mv: Optional[float] = None         # 流通市值(元)

    # === 其他指标 ===
    change_60d: Optional[float] = None      # 60日涨跌幅(%)
    high_52w: Optional[float] = None        # 52周最高
    low_52w: Optional[float] = None         # 52周最低

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（向后兼容）"""
        return self.model_dump(exclude_none=True)

    def has_basic_data(self) -> bool:
        """检查是否有基本的价格数据"""
        return self.price is not None and self.price > 0

    def has_volume_data(self) -> bool:
        """检查是否有量价数据"""
        return self.volume_ratio is not None or self.turnover_rate is not None

class ChipDistribution(BaseModel):
    """
    筹码分布模型
    """
    code: str
    profit_ratio: float = 0.0      # 获利比例
    avg_cost: float = 0.0          # 平均成本
    concentration: float = 0.0     # 集中度
    concentration_90: float = 0.0  # 90% 筹码集中度
    concentration_70: float = 0.0  # 70% 筹码集中度
    date: str = ""                 # 统计日期
    pattern: Optional[str] = None  # 自动识别的形态
    pattern_description: Optional[str] = None


class CircuitBreaker:
    """
    熔断器 - 管理数据源的熔断/冷却状态

    策略：
    - 连续失败 N 次后进入熔断状态
    - 熔断期间跳过该数据源
    - 冷却时间后自动恢复半开状态
    - 半开状态下单次成功则完全恢复，失败则继续熔断

    状态机：
    CLOSED（正常） --失败N次--> OPEN（熔断）--冷却时间到--> HALF_OPEN（半开）
    HALF_OPEN --成功--> CLOSED
    HALF_OPEN --失败--> OPEN
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        half_open_max_calls: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self._states: Dict[str, Dict[str, Any]] = {}

    def _get_state(self, source: str) -> Dict[str, Any]:
        if source not in self._states:
            self._states[source] = {
                "state": self.CLOSED,
                "failures": 0,
                "last_failure_time": 0.0,
                "half_open_calls": 0,
            }
        return self._states[source]

    def is_available(self, source: str) -> bool:
        state = self._get_state(source)
        current_time = time.time()

        if state["state"] == self.CLOSED:
            return True

        if state["state"] == self.OPEN:
            time_since_failure = current_time - state["last_failure_time"]
            if time_since_failure >= self.cooldown_seconds:
                state["state"] = self.HALF_OPEN
                state["half_open_calls"] = 0
                logger.info("[熔断器] %s 冷却完成，进入半开状态", source)
                return True
            remaining = self.cooldown_seconds - time_since_failure
            logger.debug("[熔断器] %s 处于熔断状态，剩余冷却时间: %.0fs", source, remaining)
            return False

        if state["state"] == self.HALF_OPEN:
            if state["half_open_calls"] < self.half_open_max_calls:
                return True
            return False

        return True

    def record_success(self, source: str) -> None:
        state = self._get_state(source)
        if state["state"] == self.HALF_OPEN:
            logger.info("[熔断器] %s 半开状态请求成功，恢复正常", source)
        state["state"] = self.CLOSED
        state["failures"] = 0
        state["half_open_calls"] = 0

    def record_failure(self, source: str, error: Optional[str] = None) -> None:
        state = self._get_state(source)
        current_time = time.time()
        state["failures"] += 1
        state["last_failure_time"] = current_time

        if state["state"] == self.HALF_OPEN:
            state["state"] = self.OPEN
            state["half_open_calls"] = 0
            logger.warning(
                "[熔断器] %s 半开状态请求失败，继续熔断 %ss", source, self.cooldown_seconds
            )
        elif state["failures"] >= self.failure_threshold:
            state["state"] = self.OPEN
            logger.warning(
                "[熔断器] %s 连续失败 %s 次，进入熔断状态 (冷却 %ss)",
                source,
                state["failures"],
                self.cooldown_seconds,
            )
            if error:
                logger.warning("[熔断器] 最后错误: %s", error)

    def get_status(self) -> Dict[str, str]:
        return {source: info["state"] for source, info in self._states.items()}

    def reset(self, source: Optional[str] = None) -> None:
        if source:
            if source in self._states:
                del self._states[source]
        else:
            self._states.clear()


_realtime_circuit_breaker = CircuitBreaker(
    failure_threshold=3,
    cooldown_seconds=300.0,
    half_open_max_calls=1,
)

_chip_circuit_breaker = CircuitBreaker(
    failure_threshold=2,
    cooldown_seconds=600.0,
    half_open_max_calls=1,
)


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """
    安全转换为浮点数
    """
    try:
        if val is None:
            return default
        if isinstance(val, str):
            val = val.strip()
            if val in ("", "-", "--"):
                return default
        try:
            if math.isnan(float(val)):
                return default
        except (ValueError, TypeError):
            pass
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """安全转换为整数"""
    f_val = safe_float(val, default=None)
    if f_val is not None:
        return int(f_val)
    return default


def get_realtime_circuit_breaker() -> CircuitBreaker:
    """获取实时行情熔断器"""
    return _realtime_circuit_breaker


def get_chip_circuit_breaker() -> CircuitBreaker:
    """获取筹码接口熔断器"""
    return _chip_circuit_breaker
