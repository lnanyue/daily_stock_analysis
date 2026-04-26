# -*- coding: utf-8 -*-
import asyncio
import logging
import time
from typing import List, Dict, Any, Optional, Tuple, Union
from threading import RLock
import pandas as pd

from .base import (
    BaseFetcher,
    DataFetchError,
    normalize_stock_code
)
from .exceptions import InsufficientQuotaError
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote, ChipDistribution

logger = logging.getLogger(__name__)


class DataFetcherManager:
    """
    统一数据抓取管理器，负责协调不同的 DataFetcher
    """
    _instance = None
    _lock = RLock()

    def __init__(self, fetchers: List[BaseFetcher] = None, config=None):
        self._fetchers = sorted(fetchers or [], key=lambda x: getattr(x, 'priority', 1))
        self._config = config
        self._stock_name_cache = {}
        self.date_list = None
        self._date_list_end = None

    @classmethod
    def get_instance(cls, fetchers: List[BaseFetcher] = None, config=None):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = cls(fetchers, config)
        return cls._instance

    @property
    def fetchers(self):
        return self._fetchers

    async def _maybe_await(self, value):
        if asyncio.iscoroutine(value) or hasattr(value, "__await__"):
            return await value
        return value

    async def get_stock_name(self, stock_code: str) -> Optional[str]:
        """异步获取股票名称"""
        from src.data.stock_mapping import STOCK_NAME_MAP, is_meaningful_stock_name
        
        normalized_code = normalize_stock_code(stock_code)
        if normalized_code in self._stock_name_cache:
            return self._stock_name_cache[normalized_code]
        
        if normalized_code in STOCK_NAME_MAP:
            name = STOCK_NAME_MAP[normalized_code]
            self._stock_name_cache[normalized_code] = name
            return name

        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_stock_name"):
                    name = await asyncio.wait_for(
                        asyncio.to_thread(fetcher.get_stock_name, normalized_code),
                        timeout=5.0
                    )
                    if is_meaningful_stock_name(name, normalized_code):
                        self._stock_name_cache[normalized_code] = name
                        return name
            except InsufficientQuotaError as e:
                logger.warning(f"[{fetcher.name}] 积分配额不足，尝试下一个数据源: {e}")
                continue
            except Exception:
                continue
        return None

    async def get_daily_data(self, stock_code: str, days: int = 30) -> Tuple[Optional[pd.DataFrame], str]:
        """异步获取历史日线数据"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_daily_data"):
                    df = await asyncio.wait_for(
                        asyncio.to_thread(fetcher.get_daily_data, stock_code, days=days),
                        timeout=15.0
                    )
                    if df is not None and not df.empty:
                        return df, fetcher.name
            except InsufficientQuotaError as e:
                logger.warning(f"[{fetcher.name}] 积分配额不足，尝试下一个数据源: {e}")
                continue
            except Exception as e:
                logger.warning(f"[{fetcher.name}] 获取日线数据失败 {stock_code}: {e}")
                continue
        return None, "None"

    async def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """异步获取实时行情"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_realtime_quote"):
                    # 优先尝试异步执行
                    res = fetcher.get_realtime_quote(stock_code)
                    quote = await self._maybe_await(res)
                    if quote: return quote
            except InsufficientQuotaError as e:
                logger.warning(f"[{fetcher.name}] 积分配额不足，尝试下一个数据源: {e}")
                continue
            except Exception:
                continue
        return None

    async def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """异步获取筹码分布"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_chip_distribution"):
                    res = fetcher.get_chip_distribution(stock_code)
                    data = await self._maybe_await(res)
                    if data: return data
            except InsufficientQuotaError as e:
                logger.warning(f"[{fetcher.name}] 积分配额不足，尝试下一个数据源: {e}")
                continue
            except Exception:
                continue
        return None

    async def get_fundamental_context(self, stock_code: str) -> Dict[str, Any]:
        """异步获取基本面上下文"""
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_fundamental_context"):
                    res = fetcher.get_fundamental_context(stock_code)
                    data = await self._maybe_await(res)
                    if data: return data
            except InsufficientQuotaError as e:
                logger.warning(f"[{fetcher.name}] 积分配额不足，尝试下一个数据源: {e}")
                continue
            except Exception:
                continue
        return {}

    async def get_market_overview(self, region: str = "cn") -> Dict[str, Any]:
        """异步获取大盘概览"""
        tasks = []
        # 1. 指数任务
        async def fetch_indices():
            for f in self._fetchers:
                try:
                    if hasattr(f, "get_main_indices"):
                        res = f.get_main_indices(region=region)
                        data = await self._maybe_await(res)
                        if data: return data
                except InsufficientQuotaError as e:
                    logger.warning(f"[{f.name}] 积分配额不足，尝试下一个数据源: {e}")
                    continue
                except: continue
            return []
        tasks.append(fetch_indices())

        if region == "cn":
            # 2. 统计任务
            async def fetch_stats():
                for f in self._fetchers:
                    try:
                        if hasattr(f, "get_market_stats"):
                            res = f.get_market_stats()
                            data = await self._maybe_await(res)
                            if data: return data
                    except InsufficientQuotaError as e:
                        logger.warning(f"[{f.name}] 积分配额不足，尝试下一个数据源: {e}")
                        continue
                    except: continue
                return {}
            tasks.append(fetch_stats())

            # 3. 板块任务
            async def fetch_sectors():
                for f in self._fetchers:
                    try:
                        if hasattr(f, "get_sector_rankings"):
                            res = f.get_sector_rankings()
                            data = await self._maybe_await(res)
                            if data: return data
                    except InsufficientQuotaError as e:
                        logger.warning(f"[{f.name}] 积分配额不足，尝试下一个数据源: {e}")
                        continue
                    except: continue
                return {}
            tasks.append(fetch_sectors())

        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20.0)
            overview = {
                "indices": results[0],
                "stats": results[1] if len(results) > 1 else {},
                "sector_rankings": {}
            }
            if len(results) > 2:
                sector_data = results[2]
                if isinstance(sector_data, tuple):
                    overview["sector_rankings"] = {"top": sector_data[0], "bottom": sector_data[1]}
                else:
                    overview["sector_rankings"] = {"top": sector_data[:5], "bottom": sector_data[-5:]}
            return overview
        except:
            return {"indices": [], "stats": {}, "sector_rankings": {}}

    async def get_belong_boards(self, stock_code: str) -> List[str]:
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_belong_boards"):
                    res = fetcher.get_belong_boards(stock_code)
                    return await self._maybe_await(res)
            except InsufficientQuotaError as e:
                logger.warning(f"[{fetcher.name}] 积分配额不足，尝试下一个数据源: {e}")
                continue
            except: continue
        return []
