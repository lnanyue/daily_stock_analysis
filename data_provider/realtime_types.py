# -*- coding: utf-8 -*-
"""
实时行情数据类型定义
"""

import logging
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
