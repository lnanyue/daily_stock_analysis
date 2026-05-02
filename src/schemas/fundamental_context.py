# -*- coding: utf-8 -*-
"""
基本面分析上下文模型定义
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class SourceChainEntry(BaseModel):
    """来源追踪记录"""
    provider: str
    result: str = "ok"
    duration_ms: int = 0
    error: Optional[str] = None

class BaseBlock(BaseModel):
    """基础数据块结构"""
    status: str = "not_supported"
    data: Dict[str, Any] = Field(default_factory=dict)
    source_chain: List[SourceChainEntry] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

class ValuationBlock(BaseBlock):
    """估值区块"""
    # 特定字段可以在 data 之外定义，或者保持在 data 中以增加灵活性

class GrowthBlock(BaseBlock):
    """成长性区块"""

class EarningsBlock(BaseBlock):
    """盈利能力区块"""

class InstitutionBlock(BaseBlock):
    """机构持仓区块"""

class CapitalFlowBlock(BaseBlock):
    """资金流向区块"""

class DragonTigerBlock(BaseBlock):
    """龙虎榜区块"""

class PeerComparisonEntry(BaseModel):
    """行业对标条目"""
    code: str
    name: str
    price: Optional[float] = None
    change_pct: Optional[float] = None
    pe_ttm: Any = "N/A"
    pb: Any = "N/A"
    market_cap: Any = "N/A"
    roe: Any = "N/A"
    revenue_yoy: Any = "N/A"
    net_profit_yoy: Any = "N/A"
    gross_margin: Any = "N/A"
    is_target: bool = False

class PeerComparisonData(BaseModel):
    """行业对标数据内容"""
    industry: str
    comparison: List[PeerComparisonEntry] = Field(default_factory=list)
    peer_count: int = 0

class PeerComparisonBlock(BaseBlock):
    """行业横向对比区块"""
    data: PeerComparisonData = Field(default_factory=lambda: PeerComparisonData(industry="Unknown"))

class FundamentalContext(BaseModel):
    """
    完整的基本面聚合上下文模型
    """
    market: str = "cn"
    status: str = "partial"
    elapsed_ms: int = 0
    
    valuation: ValuationBlock = Field(default_factory=ValuationBlock)
    growth: GrowthBlock = Field(default_factory=GrowthBlock)
    earnings: EarningsBlock = Field(default_factory=EarningsBlock)
    institution: InstitutionBlock = Field(default_factory=InstitutionBlock)
    capital_flow: CapitalFlowBlock = Field(default_factory=CapitalFlowBlock)
    dragon_tiger: DragonTigerBlock = Field(default_factory=DragonTigerBlock)
    boards: BaseBlock = Field(default_factory=BaseBlock)
    peer_comparison: PeerComparisonBlock = Field(default_factory=PeerComparisonBlock)
    
    coverage: Dict[str, str] = Field(default_factory=dict)
    source_chain: List[SourceChainEntry] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
