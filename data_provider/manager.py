# -*- coding: utf-8 -*-
"""
数据抓取管理器 - 负责多数据源调度、故障切换及统一接口。
"""

import asyncio
import logging
import time
import inspect
from threading import RLock, BoundedSemaphore, Thread
from typing import Any, Dict, List, Optional, Tuple, Iterable

import pandas as pd

from .base import BaseFetcher, DataFetchError
from .exceptions import InsufficientQuotaError
from .fundamental_pipeline import FundamentalPipeline
from .realtime_types import UnifiedRealtimeQuote, ChipDistribution
from .utils import (
    normalize_stock_code,
    _market_tag,
    _is_hk_market,
    _is_etf_code,
    summarize_exception,
)
from .us_index_mapping import is_us_index_code, is_us_stock_code
from .normalizers import normalize_belong_boards

logger = logging.getLogger(__name__)


class DataFetcherManager:
    """
    统一数据抓取管理器。
    """

    _instance = None
    _lock = RLock()

    def __init__(
        self,
        fetchers: Optional[List[BaseFetcher]] = None,
        config=None,
    ):
        if config is None:
            try:
                from src.config import get_config
                config = get_config()
            except Exception:
                config = None

        self._config = config
        self._fetchers = fetchers or self._create_default_fetchers(config=config)
        self._fetchers.sort(key=lambda x: getattr(x, "priority", 99))
        
        self._stock_name_cache: Dict[str, str] = {}
        self._stock_name_cache_lock = RLock()
        
        self._tickflow_fetcher = None
        self._tickflow_api_key = None
        self._tickflow_lock = RLock()

        # 属性补全，防止 legacy 方法报错
        self._stock_name_timeout_seconds = 3.0
        
        # 业务流水线逻辑拆分
        self._fundamental_pipeline = FundamentalPipeline(manager=self)

    @classmethod
    def get_instance(cls, fetchers: List[BaseFetcher] = None, config=None):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = cls(fetchers, config)
        return cls._instance

    @staticmethod
    def _create_default_fetchers(config=None) -> List[BaseFetcher]:
        """按需创建默认数据源实现。"""
        fetchers: List[BaseFetcher] = []
        try:
            from .efinance_fetcher import EfinanceFetcher
            from .akshare_fetcher import AkshareFetcher
            from .tushare_fetcher import TushareFetcher
            from .baostock_fetcher import BaostockFetcher
            from .yfinance_fetcher import YfinanceFetcher
            
            fetchers.extend([
                EfinanceFetcher(),
                AkshareFetcher(),
                TushareFetcher(config=config),
                BaostockFetcher(),
                YfinanceFetcher()
            ])
        except Exception as e:
            logger.error(f"创建默认数据源失败: {e}")
        return fetchers

    def _ensure_runtime_state(self):
        """确保运行时属性存在（用于单例恢复后的健壮性）。"""
        if not hasattr(self, "_fetchers"): self._fetchers = []
        if not hasattr(self, "_stock_name_cache"): self._stock_name_cache = {}
        if not hasattr(self, "_stock_name_cache_lock"): self._stock_name_cache_lock = RLock()
        if not hasattr(self, "_stock_name_timeout_seconds"): self._stock_name_timeout_seconds = 3.0

    async def get_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30,
    ) -> Tuple[Optional[pd.DataFrame], str]:
        stock_code = normalize_stock_code(stock_code)
        is_us = is_us_index_code(stock_code) or is_us_stock_code(stock_code)
        fetchers = list(self._fetchers)
        
        if is_us:
            source_order = ["YfinanceFetcher", "LongbridgeFetcher"]
            for src_name in source_order:
                fetcher = next((f for f in fetchers if f.name == src_name), None)
                if not fetcher: continue
                try:
                    df = await fetcher.get_daily_data_async(stock_code, start_date, end_date, days)
                    if df is not None and not df.empty:
                        return df, fetcher.name
                except Exception: continue

        for fetcher in fetchers:
            try:
                df = await fetcher.get_daily_data_async(stock_code, start_date, end_date, days)
                if df is not None and not df.empty:
                    return df, fetcher.name
            except Exception as e:
                continue
        return None, "None"

    async def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        stock_code = normalize_stock_code(stock_code)
        if _is_hk_market(stock_code):
            ak = next((f for f in self._fetchers if f.name == "AkshareFetcher"), None)
            if ak and hasattr(ak, "get_realtime_quote"):
                return await self._maybe_await(ak.get_realtime_quote(stock_code, source="hk"))

        for fetcher in self._fetchers:
            if hasattr(fetcher, "get_realtime_quote"):
                try:
                    quote = await self._maybe_await(fetcher.get_realtime_quote(stock_code))
                    if quote: return quote
                except Exception: continue
        return None

    async def get_fundamental_context(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        return await self._fundamental_pipeline.get_fundamental_context(stock_code, budget_seconds)

    def get_fundamental_context_sync(self, *args, **kwargs):
        try:
            return asyncio.run(self.get_fundamental_context(*args, **kwargs))
        except RuntimeError:
            return asyncio.get_event_loop().run_until_complete(self.get_fundamental_context(*args, **kwargs))

    def build_failed_fundamental_context(self, stock_code: str, reason: str) -> Dict[str, Any]:
        return self._fundamental_pipeline._build_failed_context(stock_code, reason)

    def get_capital_flow_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        return self._fundamental_pipeline.get_capital_flow_context(stock_code, budget_seconds)

    def get_dragon_tiger_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        return self._fundamental_pipeline.get_dragon_tiger_context(stock_code, budget_seconds)

    def get_board_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        return self._fundamental_pipeline.get_board_context(stock_code, budget_seconds)

    async def get_belong_boards(self, stock_code: str) -> List[Dict[str, Any]]:
        self._ensure_runtime_state()
        for fetcher in self._fetchers:
            method = None
            if hasattr(fetcher, "get_belong_boards"):
                method = fetcher.get_belong_boards
            elif hasattr(fetcher, "get_belong_board"):
                method = fetcher.get_belong_board
            if method is None: continue
            try:
                res = await self._maybe_await(method(stock_code))
                boards = normalize_belong_boards(res)
                if boards: return boards
            except Exception: continue
        return []

    def get_belong_boards_sync(self, stock_code: str) -> List[Dict[str, Any]]:
        try:
            return asyncio.run(self.get_belong_boards(stock_code))
        except RuntimeError:
            return asyncio.get_event_loop().run_until_complete(self.get_belong_boards(stock_code))

    def _get_sector_rankings_with_meta(self, n: int = 5):
        return self._fundamental_pipeline._get_sector_rankings_with_meta(n)

    async def get_sector_rankings(self, n: int = 5) -> Optional[Tuple[List[dict], List[dict]]]:
        """获取板块涨跌榜。"""
        top, bottom, _, _ = self._get_sector_rankings_with_meta(n)
        return top, bottom

    def get_sector_rankings_sync(self, n: int = 5):
        top, bottom, _, _ = self._get_sector_rankings_with_meta(n)
        return top, bottom

    def _get_fundamental_cache_key(self, stock_code: str, budget_seconds: Optional[float] = None) -> str:
        normalized = normalize_stock_code(stock_code)
        bucket = "default" if budget_seconds is None else f"{max(0.0, float(budget_seconds)):.1f}"
        return f"{normalized}|budget={bucket}"

    def _run_with_timeout(self, func, timeout, label, slots_attr=None):
        return self._fundamental_pipeline._run_with_timeout(func, timeout, label)

    @staticmethod
    def _infer_block_status(payload, fallback):
        if isinstance(payload, dict) and any(v not in (None, "", [], {}) for v in payload.values()):
            return "ok"
        if payload:
            return "ok"
        return fallback

    async def get_stock_name(self, stock_code: str, allow_realtime: bool = True) -> Optional[str]:
        self._ensure_runtime_state()
        stock_code = normalize_stock_code(stock_code)
        with self._stock_name_cache_lock:
            if stock_code in self._stock_name_cache: return self._stock_name_cache[stock_code]
        
        from src.data.stock_mapping import STOCK_NAME_MAP, is_meaningful_stock_name
        if stock_code in STOCK_NAME_MAP:
            name = STOCK_NAME_MAP[stock_code]
            with self._stock_name_cache_lock: self._stock_name_cache[stock_code] = name
            return name

        for fetcher in self._fetchers:
            if hasattr(fetcher, "get_stock_name"):
                try:
                    name = await self._call_stock_name(
                        fetcher,
                        stock_code,
                        timeout=max(0.001, float(self._stock_name_timeout_seconds)),
                    )
                    if is_meaningful_stock_name(name, stock_code):
                        with self._stock_name_cache_lock: self._stock_name_cache[stock_code] = name
                        return name
                except Exception: continue
        return stock_code

    async def _call_stock_name(self, fetcher, stock_code: str, timeout: float):
        method = fetcher.get_stock_name
        if inspect.iscoroutinefunction(method):
            return await asyncio.wait_for(method(stock_code), timeout=timeout)

        outcome: Dict[str, Any] = {}
        def _target():
            try: outcome["value"] = method(stock_code)
            except Exception as exc: outcome["error"] = exc

        thread = Thread(target=_target, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout
        while thread.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(0.001)
        if thread.is_alive(): return None
        if "error" in outcome: raise outcome["error"]
        return outcome.get("value")

    def get_stock_name_sync(self, stock_code: str, allow_realtime: bool = True) -> Optional[str]:
        try:
            return asyncio.run(self.get_stock_name(stock_code, allow_realtime))
        except RuntimeError:
            return asyncio.get_event_loop().run_until_complete(self.get_stock_name(stock_code, allow_realtime))

    def prefetch_stock_names(self, stock_codes: Iterable[str], use_bulk: bool = True):
        for code in stock_codes: self.get_stock_name_sync(normalize_stock_code(code), allow_realtime=False)

    async def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        for fetcher in self._fetchers:
            if hasattr(fetcher, "get_chip_distribution"):
                try: return await self._maybe_await(fetcher.get_chip_distribution(stock_code))
                except Exception: continue
        return None

    def get_daily_data_sync(self, *args, **kwargs):
        try: return asyncio.run(self.get_daily_data(*args, **kwargs))
        except RuntimeError: return asyncio.get_event_loop().run_until_complete(self.get_daily_data(*args, **kwargs))

    def get_realtime_quote_sync(self, stock_code: str):
        try: return asyncio.run(self.get_realtime_quote(stock_code))
        except RuntimeError: return asyncio.get_event_loop().run_until_complete(self.get_realtime_quote(stock_code))

    def _maybe_await(self, value):
        if asyncio.iscoroutine(value) or hasattr(value, "__await__"): return value
        async def _wrap(): return value
        return _wrap()

    @property
    def fetchers(self) -> List[BaseFetcher]:
        return self._fetchers

    @staticmethod
    def _normalize_market_stats(stats: Dict[str, Any], source: str) -> Dict[str, Any]:
        normalized = {
            "up": stats.get("up", stats.get("up_count", 0)),
            "down": stats.get("down", stats.get("down_count", 0)),
            "flat": stats.get("flat", stats.get("flat_count", 0)),
            "limit_up": stats.get("limit_up", stats.get("limit_up_count", 0)),
            "limit_down": stats.get("limit_down", stats.get("limit_down_count", 0)),
            "volume_total": stats.get(
                "volume_total",
                stats.get("total_amount", stats.get("amount_total", 0)),
            ),
            "source": source,
        }
        normalized["up_count"] = normalized["up"]
        normalized["down_count"] = normalized["down"]
        normalized["flat_count"] = normalized["flat"]
        normalized["limit_up_count"] = normalized["limit_up"]
        normalized["limit_down_count"] = normalized["limit_down"]
        normalized["total_amount"] = normalized["volume_total"]
        return normalized

    def _get_tickflow_fetcher(self):
        self._ensure_runtime_state()
        if self._tickflow_fetcher is not None:
            return self._tickflow_fetcher
        config = getattr(self, "_config", None)
        if config is None:
            try:
                from src.config import get_config
                config = get_config()
            except Exception:
                config = None
        api_key = (getattr(config, "tickflow_api_key", None) or "").strip()
        if not api_key:
            return None
        with self._tickflow_lock:
            if self._tickflow_fetcher is None or self._tickflow_api_key != api_key:
                from .tickflow_fetcher import TickFlowFetcher
                self._tickflow_fetcher = TickFlowFetcher(api_key=api_key)
                self._tickflow_api_key = api_key
        return self._tickflow_fetcher

    async def get_main_indices(self, region: str = "cn"):
        self._ensure_runtime_state()
        if region == "cn":
            try:
                tickflow_fetcher = self._get_tickflow_fetcher()
                if tickflow_fetcher is not None:
                    data = await self._maybe_await(tickflow_fetcher.get_main_indices(region=region))
                    if data:
                        return data
            except Exception as exc:
                logger.warning("[TickFlowFetcher] 获取指数失败，切换后续数据源: %s", exc)

        for fetcher in self._fetchers:
            if not hasattr(fetcher, "get_main_indices"):
                continue
            try:
                data = await self._maybe_await(fetcher.get_main_indices(region=region))
                if data:
                    return data
            except Exception:
                continue
        return []

    def get_main_indices_sync(self, region: str = "cn"):
        try:
            return asyncio.run(self.get_main_indices(region=region))
        except RuntimeError:
            return asyncio.get_event_loop().run_until_complete(self.get_main_indices(region=region))

    async def get_market_stats(self):
        self._ensure_runtime_state()
        try:
            tickflow_fetcher = self._get_tickflow_fetcher()
            if tickflow_fetcher is not None:
                stats = await self._maybe_await(tickflow_fetcher.get_market_stats())
                if stats:
                    return self._normalize_market_stats(stats, "TickFlowFetcher")
        except Exception as exc:
            logger.warning("[TickFlowFetcher] 获取市场统计失败，切换后续数据源: %s", exc)

        for fetcher in self._fetchers:
            if not hasattr(fetcher, "get_market_stats"):
                continue
            try:
                stats = await self._maybe_await(fetcher.get_market_stats())
                if stats:
                    return self._normalize_market_stats(stats, fetcher.name)
            except Exception:
                continue
        return {}

    def get_market_stats_sync(self):
        try:
            return asyncio.run(self.get_market_stats())
        except RuntimeError:
            return asyncio.get_event_loop().run_until_complete(self.get_market_stats())

    def close(self):
        for f in getattr(self, "_fetchers", []):
            if hasattr(f, "close"):
                try: f.close()
                except Exception: pass
        tickflow_fetcher = getattr(self, "_tickflow_fetcher", None)
        if tickflow_fetcher is not None and hasattr(tickflow_fetcher, "close"):
            try:
                tickflow_fetcher.close()
            except Exception:
                pass
        self._tickflow_fetcher = None
        self._tickflow_api_key = None
