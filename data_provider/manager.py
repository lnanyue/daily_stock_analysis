# -*- coding: utf-8 -*-
"""
数据源管理器 - 负责多数据源调度、故障切换与缓存
"""

import logging
import time
import asyncio
from threading import BoundedSemaphore, RLock
from typing import Optional, List, Tuple, Dict, Any
import pandas as pd

from .utils import (
    normalize_stock_code,
    summarize_exception,
    STANDARD_COLUMNS,
)
from .realtime_types import (
    UnifiedRealtimeQuote,
    ChipDistribution,
    get_realtime_circuit_breaker,
    get_chip_circuit_breaker,
)
from .fundamental_adapter import AkshareFundamentalAdapter
from .base import BaseFetcher
from .exceptions import DataFetchError

logger = logging.getLogger(__name__)


def canonical_stock_code(code: str) -> str:
    """返回标准大写的股票代码"""
    return (code or "").strip().upper()


class DataFetcherManager:
    """
    数据源策略管理器
    """
    
    _instance: Optional['DataFetcherManager'] = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, fetchers: Optional[List[BaseFetcher]] = None, config: Optional[Any] = None):
        if getattr(self, '_initialized', False):
            return
            
        self._config = config
        self._fetchers: List[BaseFetcher] = []
        
        if fetchers:
            self._fetchers = sorted(fetchers, key=lambda f: f.priority)
        else:
            self._init_default_fetchers()
            
        self._fundamental_adapter = AkshareFundamentalAdapter()
        self._tickflow_lock = RLock()
        self._fundamental_cache: Dict[str, Dict[str, Any]] = {}
        self._fundamental_cache_lock = RLock()
        self._fundamental_timeout_slots = BoundedSemaphore(8)
        self._initialized = True

    def _init_default_fetchers(self) -> None:
        """初始化默认数据源"""
        from .efinance_fetcher import EfinanceFetcher
        from .akshare_fetcher import AkshareFetcher
        from .tushare_fetcher import TushareFetcher
        from .pytdx_fetcher import PytdxFetcher
        from .baostock_fetcher import BaostockFetcher
        from .yfinance_fetcher import YfinanceFetcher

        self._fetchers = [
            EfinanceFetcher(),
            AkshareFetcher(),
            TushareFetcher(),
            PytdxFetcher(),
            BaostockFetcher(),
            YfinanceFetcher(),
        ]
        self._fetchers.sort(key=lambda f: f.priority)

    @classmethod
    def get_instance(cls) -> 'DataFetcherManager':
        if cls._instance is None:
            cls._instance = DataFetcherManager()
        return cls._instance

    def get_daily_data(
        self, 
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> Tuple[pd.DataFrame, str]:
        """获取日线数据（带故障切换）"""
        from .us_index_mapping import is_us_index_code, is_us_stock_code
        code = normalize_stock_code(stock_code)
        
        errors = []
        for fetcher in self._fetchers:
            if (is_us_index_code(code) or is_us_stock_code(code)) and fetcher.name != "YfinanceFetcher":
                continue
            try:
                df = fetcher.get_daily_data(code, start_date, end_date, days)
                if df is not None and not df.empty:
                    return df, fetcher.name
            except Exception as e:
                errors.append(f"{fetcher.name}: {str(e)}")
        
        raise DataFetchError(f"所有数据源均获取失败: {'; '.join(errors)}")

    async def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """异步获取实时行情（带熔断与缓存）"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_realtime_quote"):
                    quote = await fetcher.get_realtime_quote(stock_code)
                    if quote: return quote
            except Exception: continue
        return None

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """获取股票名称"""
        for fetcher in self._fetchers:
            try:
                name = fetcher.get_stock_name(stock_code) if hasattr(fetcher, "get_stock_name") else None
                if name: return name
            except Exception: continue
        return None

    # --- 补全被截断的公开 API ---

    def get_main_indices(self, region: str = "cn") -> Dict[str, Any]:
        """获取主要指数行情"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_main_indices"):
                    return fetcher.get_main_indices(region=region)
            except Exception: continue
        return {}

    def get_market_stats(self) -> Dict[str, Any]:
        """获取大盘统计数据（涨跌分布等）"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_market_stats"):
                    return fetcher.get_market_stats()
            except Exception: continue
        return {}

    def get_sector_rankings(self) -> List[Dict[str, Any]]:
        """获取板块排名"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_sector_rankings"):
                    return fetcher.get_sector_rankings()
            except Exception: continue
        return []

    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """获取筹码分布数据"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_chip_distribution"):
                    return fetcher.get_chip_distribution(stock_code)
            except Exception: continue
        return None

    def get_fundamental_context(self, stock_code: str) -> Dict[str, Any]:
        """获取基本面上下文"""
        return self._fundamental_adapter.get_fundamental_context(stock_code)

    def get_belong_boards(self, stock_code: str) -> List[str]:
        """获取所属板块"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_belong_boards"):
                    return fetcher.get_belong_boards(stock_code)
            except Exception: continue
        return []

    def prefetch_stock_names(self) -> None:
        """预取所有股票名称（加速映射）"""
        from src.data.stock_mapping import STOCK_NAME_MAP
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_all_stock_names"):
                    names = fetcher.get_all_stock_names()
                    if names:
                        STOCK_NAME_MAP.update(names)
                        logger.info(f"已从 {fetcher.name} 预取 {len(names)} 条股票名称")
                        break
            except Exception: continue

    def close(self) -> None:
        """关闭所有数据源连接"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "close"):
                    fetcher.close()
            except Exception: continue
