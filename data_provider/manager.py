# -*- coding: utf-8 -*-
"""
数据源管理器 - 负责多数据源调度、故障切换与缓存 (加固版)
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
        self._tickflow_fetcher = None
        self._tickflow_api_key: Optional[str] = None
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

    @property
    def available_fetchers(self) -> List[str]:
        return [getattr(fetcher, "name", type(fetcher).__name__) for fetcher in getattr(self, "_fetchers", [])]

    @classmethod
    def reset_instance(cls) -> None:
        current = cls._instance
        cls._instance = None
        if current is not None:
            try:
                current.close()
            except Exception:
                pass

    def _get_tickflow_fetcher(self):
        """Lazily create a TickFlow fetcher for market-review-only calls."""
        from src.config import get_config

        config = get_config()
        api_key = (getattr(config, "tickflow_api_key", None) or "").strip()

        if not hasattr(self, "_tickflow_lock") or self._tickflow_lock is None:
            self._tickflow_lock = RLock()

        with self._tickflow_lock:
            current_fetcher = getattr(self, "_tickflow_fetcher", None)
            current_key = getattr(self, "_tickflow_api_key", None)

            if not api_key:
                if current_fetcher is not None and hasattr(current_fetcher, "close"):
                    try:
                        current_fetcher.close()
                    except Exception as exc:
                        logger.debug("[TickFlowFetcher] 关闭旧实例失败: %s", exc)
                self._tickflow_fetcher = None
                self._tickflow_api_key = None
                return None

            if current_fetcher is not None and current_key == api_key:
                return current_fetcher

            if current_fetcher is not None and hasattr(current_fetcher, "close"):
                try:
                    current_fetcher.close()
                except Exception as exc:
                    logger.debug("[TickFlowFetcher] 切换实例时关闭失败: %s", exc)

            try:
                from .tickflow_fetcher import TickFlowFetcher

                fetcher = TickFlowFetcher(api_key=api_key)
                self._tickflow_fetcher = fetcher
                self._tickflow_api_key = api_key
                return fetcher
            except Exception as exc:
                logger.warning("[TickFlowFetcher] 初始化失败: %s", exc)
                self._tickflow_fetcher = None
                self._tickflow_api_key = None
                return None

    async def get_daily_data(
        self, 
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> Tuple[pd.DataFrame, str]:
        """异步获取日线数据（带故障切换）"""
        from .us_index_mapping import is_us_index_code, is_us_stock_code
        code = normalize_stock_code(stock_code)
        
        errors = []
        for fetcher in self._fetchers:
            if (is_us_index_code(code) or is_us_stock_code(code)) and fetcher.name != "YfinanceFetcher":
                continue
            try:
                if hasattr(fetcher, "get_daily_data_async"):
                    df = await fetcher.get_daily_data_async(code, start_date, end_date, days)
                else:
                    df = await asyncio.to_thread(fetcher.get_daily_data, code, start_date, end_date, days)
                
                if df is not None and not df.empty:
                    return df, fetcher.name
            except Exception as e:
                errors.append(f"{fetcher.name}: {str(e)}")
        
        raise DataFetchError(f"所有数据源均获取失败: {'; '.join(errors)}")

    async def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """异步获取实时行情"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_realtime_quote"):
                    quote = await fetcher.get_realtime_quote(stock_code)
                    if quote: return quote
            except Exception: continue
        return None

    async def get_stock_name(self, stock_code: str) -> Optional[str]:
        """异步获取股票名称"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_stock_name"):
                    name = await asyncio.to_thread(fetcher.get_stock_name, stock_code)
                    if name: return name
            except Exception: continue
        return None

    # --- 修复故障切换逻辑：确保空数据时继续尝试其他源 ---

    def get_main_indices(self, region: str = "cn") -> List[Dict[str, Any]]:
        """获取主要指数行情 (Failover enabled)."""
        if region == "cn":
            tickflow_fetcher = self._get_tickflow_fetcher()
            if tickflow_fetcher is not None:
                try:
                    data = tickflow_fetcher.get_main_indices(region=region)
                    if data:
                        return data
                except Exception as e:
                    logger.warning("[TickFlowFetcher] get_main_indices 失败: %s", e)
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_main_indices"):
                    data = fetcher.get_main_indices(region=region)
                    if data: return data # 只有非空才返回
            except Exception as e:
                logger.warning(f"[{fetcher.name}] get_main_indices 失败: {e}")
                continue
        return []

    def get_market_stats(self) -> Dict[str, Any]:
        """获取市场统计数据 (Failover enabled)."""
        tickflow_api_key = ""
        config = getattr(self, "_config", None)
        if config is not None:
            tickflow_api_key = (getattr(config, "tickflow_api_key", None) or "").strip()
        else:
            try:
                from src.config import get_config

                tickflow_api_key = (getattr(get_config(), "tickflow_api_key", None) or "").strip()
            except Exception:
                tickflow_api_key = ""

        if tickflow_api_key:
            tickflow_fetcher = self._get_tickflow_fetcher()
            if tickflow_fetcher is not None:
                try:
                    data = tickflow_fetcher.get_market_stats()
                    if data:
                        return data
                except Exception as e:
                    logger.warning("[TickFlowFetcher] get_market_stats 失败: %s", e)

        for fetcher in getattr(self, "_fetchers", []):
            try:
                if hasattr(fetcher, "get_market_stats"):
                    data = fetcher.get_market_stats()
                    if data: return data
            except Exception as e:
                logger.warning(f"[{fetcher.name}] get_market_stats 失败: {e}")
                continue
        return {}

    def get_sector_rankings(self, n: int = 5):
        """获取板块排名 (Failover enabled)."""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_sector_rankings"):
                    data = fetcher.get_sector_rankings(n=n)
                    if data: return data
            except Exception as e:
                logger.warning(f"[{fetcher.name}] get_sector_rankings 失败: {e}")
                continue
        return [] if n else ([], [])

    async def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """异步获取筹码分布数据 (Failover enabled)"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_chip_distribution"):
                    data = await asyncio.to_thread(fetcher.get_chip_distribution, stock_code)
                    if data: return data
            except Exception: continue
        return None

    async def get_fundamental_context(self, stock_code: str) -> Dict[str, Any]:
        """异步获取基本面上下文"""
        return await asyncio.to_thread(self._fundamental_adapter.get_fundamental_context, stock_code)

    async def get_belong_boards(self, stock_code: str) -> List[str]:
        """异步获取所属板块"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_belong_boards"):
                    return await asyncio.to_thread(fetcher.get_belong_boards, stock_code)
            except Exception: continue
        return []

    def prefetch_stock_names(self) -> None:
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
        if not hasattr(self, "_tickflow_lock") or self._tickflow_lock is None:
            self._tickflow_lock = RLock()

        with self._tickflow_lock:
            current_fetcher = getattr(self, "_tickflow_fetcher", None)
            self._tickflow_fetcher = None
            self._tickflow_api_key = None

        if current_fetcher is not None and hasattr(current_fetcher, "close"):
            try:
                current_fetcher.close()
            except Exception as exc:
                logger.debug("[TickFlowFetcher] 关闭管理器资源失败: %s", exc)

        for fetcher in getattr(self, "_fetchers", []):
            try:
                if hasattr(fetcher, "close"): fetcher.close()
            except Exception: continue

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
