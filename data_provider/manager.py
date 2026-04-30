# -*- coding: utf-8 -*-
import asyncio
import logging
import time
from threading import BoundedSemaphore, RLock, Thread
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

from .base import BaseFetcher, DataFetchError, normalize_stock_code
from .exceptions import InsufficientQuotaError
from .fundamental_adapter import AkshareFundamentalAdapter
from .realtime_types import ChipDistribution, UnifiedRealtimeQuote
from .utils import _is_etf_code, _market_tag

logger = logging.getLogger(__name__)


class DataFetcherManager:
    """
    统一数据抓取管理器，负责协调不同的 DataFetcher。

    Async 方法服务主流程；显式的 `*_sync` 包装器服务 agent tools、
    同步测试和其他非异步调用点。
    """

    _instance = None
    _lock = RLock()
    _DEFAULT_FUNDAMENTAL_TIMEOUT_WORKERS = 2
    _DEFAULT_STOCK_NAME_TIMEOUT_WORKERS = 4
    _DEFAULT_STOCK_NAME_TIMEOUT_SECONDS = 3.0

    def __init__(
        self,
        fetchers: Optional[List[BaseFetcher]] = None,
        config=None,
        include_default_fetchers: bool = False,
    ):
        if config is None:
            try:
                from src.config import get_config

                config = get_config()
            except Exception:
                config = None

        if fetchers is None:
            base_fetchers = self._create_default_fetchers(config=config)
        else:
            base_fetchers = list(fetchers)
            if include_default_fetchers:
                existing_names = {
                    getattr(fetcher, "name", fetcher.__class__.__name__)
                    for fetcher in base_fetchers
                }
                base_fetchers.extend(
                    self._create_default_fetchers(config=config, skip_names=existing_names)
                )

        self._fetchers = sorted(base_fetchers, key=lambda x: getattr(x, "priority", 1))
        self._config = config
        self._stock_name_cache: Dict[str, str] = {}
        self._tickflow_fetcher = None
        self._tickflow_api_key = getattr(config, "tickflow_api_key", None) if config is not None else None
        self._tickflow_lock = RLock()
        self._fundamental_adapter = AkshareFundamentalAdapter()
        self._fundamental_timeout_slots = BoundedSemaphore(self._DEFAULT_FUNDAMENTAL_TIMEOUT_WORKERS)
        self._stock_name_timeout_slots = BoundedSemaphore(self._DEFAULT_STOCK_NAME_TIMEOUT_WORKERS)
        self._stock_name_timeout_seconds = self._DEFAULT_STOCK_NAME_TIMEOUT_SECONDS
        self.date_list = None
        self._date_list_end = None

    @classmethod
    def get_instance(cls, fetchers: List[BaseFetcher] = None, config=None):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = cls(fetchers, config)
        return cls._instance

    @classmethod
    def _create_default_fetchers(
        cls,
        config=None,
        skip_names: Optional[Iterable[str]] = None,
    ) -> List[BaseFetcher]:
        """Create built-in fetchers; optional dependencies stay best-effort."""
        skip: Set[str] = set(skip_names or [])
        fetchers: List[BaseFetcher] = []

        def add(name: str, factory) -> None:
            if name in skip:
                return
            try:
                fetchers.append(factory())
            except Exception as exc:
                logger.warning("初始化内置数据源 %s 失败: %s", name, exc)

        add(
            "EfinanceFetcher",
            lambda: __import__(
                "data_provider.efinance_fetcher",
                fromlist=["EfinanceFetcher"],
            ).EfinanceFetcher(),
        )
        add(
            "AkshareFetcher",
            lambda: __import__(
                "data_provider.akshare_fetcher",
                fromlist=["AkshareFetcher"],
            ).AkshareFetcher(),
        )

        if getattr(config, "tushare_token", None):
            add(
                "TushareFetcher",
                lambda: __import__(
                    "data_provider.tushare_fetcher",
                    fromlist=["TushareFetcher"],
                ).TushareFetcher(config=config),
            )

        add(
            "BaostockFetcher",
            lambda: __import__(
                "data_provider.baostock_fetcher",
                fromlist=["BaostockFetcher"],
            ).BaostockFetcher(),
        )
        if getattr(config, "openbb_fetcher_enabled", False):
            add(
                "OpenBBFetcher",
                lambda: __import__(
                    "data_provider.openbb_fetcher",
                    fromlist=["OpenBBFetcher"],
                ).OpenBBFetcher(config=config),
            )
        add(
            "FutuFetcher",
            lambda: __import__(
                "data_provider.futu_fetcher",
                fromlist=["FutuFetcher"],
            ).FutuFetcher(config=config),
        )
        add(
            "YfinanceFetcher",
            lambda: __import__(
                "data_provider.yfinance_fetcher",
                fromlist=["YfinanceFetcher"],
            ).YfinanceFetcher(),
        )

        tickflow_key = getattr(config, "tickflow_api_key", None)
        if tickflow_key:
            add(
                "TickFlowFetcher",
                lambda: __import__(
                    "data_provider.tickflow_fetcher",
                    fromlist=["TickFlowFetcher"],
                ).TickFlowFetcher(api_key=tickflow_key),
            )

        return fetchers

    @property
    def fetchers(self):
        return self._fetchers

    def _ensure_runtime_state(self) -> None:
        if not hasattr(self, "_fetchers"):
            self._fetchers = []
        if not hasattr(self, "_config"):
            self._config = None
        if not hasattr(self, "_stock_name_cache"):
            self._stock_name_cache = {}
        if not hasattr(self, "_tickflow_fetcher"):
            self._tickflow_fetcher = None
        if not hasattr(self, "_tickflow_api_key"):
            self._tickflow_api_key = None
        if not hasattr(self, "_tickflow_lock"):
            self._tickflow_lock = RLock()
        if not hasattr(self, "_fundamental_adapter"):
            self._fundamental_adapter = AkshareFundamentalAdapter()
        if not hasattr(self, "_fundamental_timeout_slots"):
            self._fundamental_timeout_slots = BoundedSemaphore(self._DEFAULT_FUNDAMENTAL_TIMEOUT_WORKERS)
        if not hasattr(self, "_stock_name_timeout_slots"):
            self._stock_name_timeout_slots = BoundedSemaphore(self._DEFAULT_STOCK_NAME_TIMEOUT_WORKERS)
        if not hasattr(self, "_stock_name_timeout_seconds"):
            self._stock_name_timeout_seconds = self._DEFAULT_STOCK_NAME_TIMEOUT_SECONDS

    def _get_runtime_config(self):
        self._ensure_runtime_state()
        if self._config is not None:
            return self._config
        try:
            from src.config import get_config

            self._config = get_config()
        except Exception:
            self._config = None
        return self._config

    async def _maybe_await(self, value):
        if asyncio.iscoroutine(value) or hasattr(value, "__await__"):
            return await value
        return value

    def _run_awaitable_sync(self, awaitable):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        outcome: Dict[str, Any] = {}

        def _runner() -> None:
            try:
                outcome["value"] = asyncio.run(awaitable)
            except Exception as exc:
                outcome["error"] = exc

        thread = Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def _resolve_sync_result(self, value):
        if asyncio.iscoroutine(value) or hasattr(value, "__await__"):
            return self._run_awaitable_sync(value)
        return value

    @staticmethod
    def _is_quota_error_message(message: str) -> bool:
        text = str(message or "").lower()
        quota_keywords = ("quota", "配额", "权限", "积分", "频率超限", "rate limit")
        return any(keyword in text for keyword in quota_keywords)

    def _get_tickflow_fetcher(self):
        self._ensure_runtime_state()
        for fetcher in self._fetchers:
            if getattr(fetcher, "name", "") == "TickFlowFetcher":
                return fetcher

        config = self._get_runtime_config()
        api_key = getattr(config, "tickflow_api_key", None) if config is not None else None
        self._tickflow_api_key = api_key
        if not api_key:
            return None

        if self._tickflow_fetcher is not None:
            return self._tickflow_fetcher

        with self._tickflow_lock:
            if self._tickflow_fetcher is None:
                try:
                    from .tickflow_fetcher import TickFlowFetcher

                    self._tickflow_fetcher = TickFlowFetcher(api_key=api_key)
                except Exception as exc:
                    logger.warning("初始化 TickFlowFetcher 失败: %s", exc)
                    self._tickflow_fetcher = None
            return self._tickflow_fetcher

    async def get_stock_name(self, stock_code: str, allow_realtime: bool = True) -> Optional[str]:
        """异步获取股票名称。"""
        del allow_realtime  # 保留参数名以兼容旧调用约定。
        self._ensure_runtime_state()
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
                    name, error, _ = await asyncio.to_thread(
                        self._run_with_timeout,
                        lambda current_fetcher=fetcher: self._resolve_sync_result(
                            current_fetcher.get_stock_name(normalized_code)
                        ),
                        float(self._stock_name_timeout_seconds),
                        f"stock_name[{getattr(fetcher, 'name', fetcher.__class__.__name__)}]",
                        "_stock_name_timeout_slots",
                    )
                    if error is not None:
                        if self._is_quota_error_message(error):
                            logger.warning("[%s] 股票名称接口受限，尝试下一个数据源: %s", fetcher.name, error)
                        elif "timeout" in error.lower():
                            logger.warning("[%s] 获取股票名称超时，尝试下一个数据源: %s", fetcher.name, error)
                        else:
                            logger.debug("[%s] 获取股票名称失败，尝试下一个数据源: %s", fetcher.name, error)
                        continue
                    if is_meaningful_stock_name(name, normalized_code):
                        self._stock_name_cache[normalized_code] = name
                        return name
            except InsufficientQuotaError as exc:
                logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
                continue
            except Exception:
                continue
        return None

    def get_stock_name_sync(self, stock_code: str, allow_realtime: bool = True) -> Optional[str]:
        return self._run_awaitable_sync(self.get_stock_name(stock_code, allow_realtime=allow_realtime))

    def prefetch_stock_names(self, stock_codes: Iterable[str], use_bulk: bool = False) -> None:
        del use_bulk
        self._ensure_runtime_state()
        for stock_code in stock_codes or []:
            normalized = normalize_stock_code(stock_code)
            if not normalized:
                continue
            try:
                self.get_stock_name_sync(normalized, allow_realtime=False)
            except Exception:
                continue

    async def get_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30,
    ) -> Tuple[Optional[pd.DataFrame], str]:
        """异步获取历史日线数据。"""
        self._ensure_runtime_state()
        total = len(self._fetchers)
        previous_name = None

        for index, fetcher in enumerate(self._fetchers, start=1):
            if not hasattr(fetcher, "get_daily_data"):
                continue
            try:
                logger.info("[数据源尝试 %s/%s] [%s] 获取 %s...", index, total, fetcher.name, stock_code)
                df = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetcher.get_daily_data,
                        stock_code,
                        start_date=start_date,
                        end_date=end_date,
                        days=days,
                    ),
                    timeout=15.0,
                )
                if df is not None and not df.empty:
                    if previous_name:
                        logger.info("[数据源切换] %s: [%s] -> [%s]", stock_code, previous_name, fetcher.name)
                    logger.info(
                        "[数据源完成] %s 使用 [%s] 获取成功: rows=%s",
                        stock_code,
                        fetcher.name,
                        len(df),
                    )
                    return df, fetcher.name
            except InsufficientQuotaError as exc:
                logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
            except Exception as exc:
                logger.warning("[数据源失败 %s/%s] [%s] %s: %s", index, total, fetcher.name, stock_code, exc)
            previous_name = fetcher.name
        return None, "None"

    def get_daily_data_sync(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30,
    ) -> Tuple[Optional[pd.DataFrame], str]:
        return self._run_awaitable_sync(
            self.get_daily_data(stock_code, start_date=start_date, end_date=end_date, days=days)
        )

    async def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """异步获取实时行情。"""
        self._ensure_runtime_state()
        if _market_tag(stock_code) == "hk":
            normalized_code = normalize_stock_code(stock_code)
            for fetcher in self._fetchers:
                if getattr(fetcher, "name", "") != "AkshareFetcher":
                    continue
                try:
                    if hasattr(fetcher, "get_realtime_quote"):
                        result = fetcher.get_realtime_quote(normalized_code, source="hk")
                        quote = await self._maybe_await(result)
                        if quote:
                            return quote
                except InsufficientQuotaError as exc:
                    logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
                except Exception:
                    continue
            return None

        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_realtime_quote"):
                    result = fetcher.get_realtime_quote(stock_code)
                    quote = await self._maybe_await(result)
                    if quote:
                        return quote
            except InsufficientQuotaError as exc:
                logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
                continue
            except Exception:
                continue
        return None

    def get_realtime_quote_sync(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        return self._run_awaitable_sync(self.get_realtime_quote(stock_code))

    async def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """异步获取筹码分布。"""
        self._ensure_runtime_state()
        for fetcher in self._fetchers:
            try:
                if hasattr(fetcher, "get_chip_distribution"):
                    result = fetcher.get_chip_distribution(stock_code)
                    data = await self._maybe_await(result)
                    if data:
                        return data
            except InsufficientQuotaError as exc:
                logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
                continue
            except Exception:
                continue
        return None

    def get_chip_distribution_sync(self, stock_code: str) -> Optional[ChipDistribution]:
        return self._run_awaitable_sync(self.get_chip_distribution(stock_code))

    @staticmethod
    def _empty_block(status: str = "not_supported", data: Optional[Dict[str, Any]] = None, errors=None):
        return {
            "status": status,
            "data": data or {},
            "source_chain": [],
            "errors": list(errors or []),
        }

    def build_failed_fundamental_context(self, stock_code: str, reason: str) -> Dict[str, Any]:
        market = _market_tag(stock_code)
        coverage = {
            "valuation": "failed",
            "growth": "failed",
            "earnings": "failed",
            "institution": "failed",
            "capital_flow": "failed",
            "dragon_tiger": "failed",
            "boards": "failed",
        }
        context = {
            "market": market,
            "status": "failed",
            "coverage": coverage,
            "valuation": self._empty_block("failed", errors=[reason]),
            "growth": self._empty_block("failed", errors=[reason]),
            "earnings": self._empty_block("failed", errors=[reason]),
            "institution": self._empty_block("failed", errors=[reason]),
            "capital_flow": self._empty_block("failed", errors=[reason]),
            "dragon_tiger": self._empty_block("failed", errors=[reason]),
            "boards": self._empty_block("failed", errors=[reason]),
            "source_chain": [],
            "errors": [reason],
        }
        if market != "cn":
            for key in coverage:
                coverage[key] = "not_supported"
                context[key] = self._empty_block("not_supported")
            context["status"] = "not_supported"
            context["errors"] = [reason]
        return context

    @staticmethod
    def _has_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return True
        if isinstance(value, (int, float)):
            return not pd.isna(value)
        if isinstance(value, str):
            return value.strip() not in {"", "-", "N/A", "None", "null", "nan"}
        if isinstance(value, dict):
            return any(DataFetcherManager._has_meaningful_value(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(DataFetcherManager._has_meaningful_value(item) for item in value)
        return True

    @staticmethod
    def _infer_block_status(payload: Optional[Dict[str, Any]], status_hint: str = "not_supported") -> str:
        if DataFetcherManager._has_meaningful_value(payload or {}):
            return "ok"
        return status_hint if status_hint in {"partial", "not_supported", "failed"} else "not_supported"

    def _get_fundamental_cache_key(self, stock_code: str, budget_seconds: Optional[float] = None) -> str:
        normalized = normalize_stock_code(stock_code)
        if budget_seconds is None:
            bucket = "default"
        else:
            bucket = f"{max(0.0, float(budget_seconds)):.1f}"
        return f"{normalized}|budget={bucket}"

    def _run_with_timeout(
        self,
        func,
        timeout_seconds: float,
        label: str,
        slots_attr: str = "_fundamental_timeout_slots",
    ):
        self._ensure_runtime_state()
        timeout_slots = getattr(self, slots_attr, None)
        if timeout_slots is None:
            timeout_slots = BoundedSemaphore(self._DEFAULT_FUNDAMENTAL_TIMEOUT_WORKERS)
            setattr(self, slots_attr, timeout_slots)

        if not timeout_slots.acquire(blocking=False):
            return None, f"{label}: worker pool exhausted", 0.0

        started = time.monotonic()
        outcome: Dict[str, Any] = {}

        def _runner() -> None:
            try:
                outcome["value"] = func()
            except Exception as exc:
                outcome["error"] = exc
            finally:
                timeout_slots.release()

        thread = Thread(target=_runner, daemon=True)
        thread.start()
        thread.join(timeout_seconds)
        elapsed = time.monotonic() - started
        if thread.is_alive():
            return None, f"{label}: timeout after {timeout_seconds:.2f}s", elapsed
        if "error" in outcome:
            return None, str(outcome["error"]), elapsed
        return outcome.get("value"), None, elapsed

    def _build_valuation_block(self, quote: Any) -> Dict[str, Any]:
        if quote is None:
            return self._empty_block("partial")

        data = {
            "price": getattr(quote, "price", None),
            "pe_ratio": getattr(quote, "pe_ratio", None),
            "pb_ratio": getattr(quote, "pb_ratio", None),
            "total_mv": getattr(quote, "total_mv", None),
            "circ_mv": getattr(quote, "circ_mv", None),
            "source": getattr(getattr(quote, "source", None), "value", getattr(quote, "source", None)),
        }
        status = self._infer_block_status(
            {
                "price": data["price"],
                "pe_ratio": data["pe_ratio"],
                "pb_ratio": data["pb_ratio"],
                "total_mv": data["total_mv"],
                "circ_mv": data["circ_mv"],
            },
            "partial",
        )
        return self._empty_block(status, data=data)

    def get_capital_flow_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        market = _market_tag(stock_code)
        if market != "cn" or _is_etf_code(stock_code):
            return self._empty_block("not_supported")

        timeout = float(budget_seconds or 0.5)
        payload, error, _ = self._run_with_timeout(
            lambda: self._fundamental_adapter.get_capital_flow(stock_code),
            timeout,
            "capital_flow",
        )
        if error is not None:
            return self._empty_block("failed", errors=[error])

        payload = payload or {}
        return {
            "status": payload.get("status", "not_supported"),
            "data": {
                "stock_flow": payload.get("stock_flow") or {},
                "sector_rankings": payload.get("sector_rankings") or {"top": [], "bottom": []},
            },
            "source_chain": list(payload.get("source_chain", [])),
            "errors": list(payload.get("errors", [])),
        }

    def get_dragon_tiger_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        market = _market_tag(stock_code)
        if market != "cn" or _is_etf_code(stock_code):
            return self._empty_block("not_supported")

        timeout = float(budget_seconds or 0.5)
        payload, error, _ = self._run_with_timeout(
            lambda: self._fundamental_adapter.get_dragon_tiger_flag(stock_code),
            timeout,
            "dragon_tiger",
        )
        if error is not None:
            return self._empty_block("failed", errors=[error])

        payload = payload or {}
        data = {
            "is_on_list": payload.get("is_on_list", False),
            "recent_count": payload.get("recent_count", 0),
            "latest_date": payload.get("latest_date"),
        }
        return {
            "status": payload.get("status", "not_supported"),
            "data": data if self._has_meaningful_value(data) else {},
            "source_chain": list(payload.get("source_chain", [])),
            "errors": list(payload.get("errors", [])),
        }

    def _normalize_board_entry(self, item: Any) -> Optional[Dict[str, Any]]:
        if item is None:
            return None
        if isinstance(item, str):
            text = item.strip()
            return {"name": text} if text else None
        if not isinstance(item, dict):
            return None

        name = (
            item.get("name")
            or item.get("board_name")
            or item.get("板块名称")
            or item.get("板块")
            or item.get("所属板块")
            or item.get("板块名")
            or item.get("industry")
            or item.get("行业")
        )
        name = str(name).strip() if name is not None else ""
        if not name:
            return None

        normalized = {"name": name}
        code = item.get("code") or item.get("board_code") or item.get("板块代码") or item.get("代码")
        if code not in (None, ""):
            normalized["code"] = str(code).strip()
        board_type = item.get("type") or item.get("board_type") or item.get("板块类型") or item.get("类别")
        if board_type not in (None, ""):
            normalized["type"] = str(board_type).strip()
        return normalized

    async def get_belong_boards(self, stock_code: str) -> List[Dict[str, Any]]:
        self._ensure_runtime_state()
        for fetcher in self._fetchers:
            try:
                method = None
                if hasattr(fetcher, "get_belong_boards"):
                    method = fetcher.get_belong_boards
                elif hasattr(fetcher, "get_belong_board"):
                    method = fetcher.get_belong_board
                if method is None:
                    continue

                result = method(stock_code)
                raw_boards = await self._maybe_await(result)
                normalized: List[Dict[str, Any]] = []
                for item in raw_boards or []:
                    mapped = self._normalize_board_entry(item)
                    if mapped is not None:
                        normalized.append(mapped)
                if normalized:
                    return normalized
            except InsufficientQuotaError as exc:
                logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
                continue
            except Exception:
                continue
        return []

    def get_belong_boards_sync(self, stock_code: str) -> List[Dict[str, Any]]:
        return self._run_awaitable_sync(self.get_belong_boards(stock_code))

    def _get_sector_rankings_with_meta(self, n: int = 5):
        source_chain: List[str] = []
        errors: List[str] = []
        for fetcher in self._fetchers:
            if getattr(fetcher, "name", "") == "TickFlowFetcher":
                continue
            if not hasattr(fetcher, "get_sector_rankings"):
                continue
            try:
                result = fetcher.get_sector_rankings(n)
                data = self._resolve_sync_result(result)
                if isinstance(data, tuple):
                    top = list(data[0] or [])
                    bottom = list(data[1] or [])
                elif data:
                    values = list(data)
                    top = values[:n]
                    bottom = values[-n:]
                else:
                    continue
                source_chain.append(f"boards:{getattr(fetcher, 'name', 'unknown')}")
                return top, bottom, source_chain, None
            except InsufficientQuotaError as exc:
                errors.append(f"{getattr(fetcher, 'name', 'unknown')}:{exc}")
            except Exception as exc:
                errors.append(f"{getattr(fetcher, 'name', 'unknown')}:{exc}")
        return [], [], source_chain, "; ".join(errors) if errors else None

    def get_board_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        del budget_seconds
        market = _market_tag(stock_code)
        if market != "cn" or _is_etf_code(stock_code):
            return self._empty_block("not_supported")

        belong_boards = self.get_belong_boards_sync(stock_code)
        top, bottom, source_chain, error = self._get_sector_rankings_with_meta()

        if not belong_boards and not top and not bottom:
            status = "failed" if error else "not_supported"
            return {
                "status": status,
                "data": {},
                "source_chain": source_chain,
                "errors": [error] if error else [],
            }

        data = {"top": top, "bottom": bottom}
        if belong_boards:
            data["belong_boards"] = belong_boards
        status = "ok" if top or bottom else "partial"
        return {
            "status": status,
            "data": data,
            "source_chain": source_chain,
            "errors": [error] if error else [],
        }

    def get_fundamental_context_sync(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        self._ensure_runtime_state()
        config = self._get_runtime_config()
        if config is not None and getattr(config, "enable_fundamental_pipeline", True) is False:
            return self.build_failed_fundamental_context(stock_code, "fundamental pipeline disabled")

        market = _market_tag(stock_code)
        if market != "cn":
            return self._fundamental_adapter.get_fundamental_context(stock_code)

        try:
            context = self._fundamental_adapter.get_fundamental_context(stock_code)
        except Exception as exc:
            return self.build_failed_fundamental_context(stock_code, str(exc))

        quote = None
        try:
            quote = self._resolve_sync_result(self.get_realtime_quote(stock_code))
        except Exception as exc:
            context.setdefault("errors", []).append(f"valuation:{exc}")

        valuation_block = self._build_valuation_block(quote)
        context["valuation"] = valuation_block
        context.setdefault("coverage", {})["valuation"] = valuation_block["status"]

        block_budget = float(budget_seconds or getattr(config, "fundamental_stage_timeout_seconds", 1.5) or 1.5)
        block_budget = max(0.1, block_budget)

        if _is_etf_code(stock_code):
            capital_flow_block = self._empty_block("not_supported")
            dragon_tiger_block = self._empty_block("not_supported")
            board_block = self._empty_block("not_supported")
        else:
            capital_flow_block = self.get_capital_flow_context(stock_code, budget_seconds=block_budget)
            dragon_tiger_block = self.get_dragon_tiger_context(stock_code, budget_seconds=block_budget)
            board_block = self.get_board_context(stock_code, budget_seconds=block_budget)

        context["capital_flow"] = capital_flow_block
        context["dragon_tiger"] = dragon_tiger_block
        context["boards"] = board_block
        context["coverage"]["capital_flow"] = capital_flow_block["status"]
        context["coverage"]["dragon_tiger"] = dragon_tiger_block["status"]
        context["coverage"]["boards"] = board_block["status"]

        dividend = (
            context.get("earnings", {})
            .get("data", {})
            .get("dividend", {})
        )
        if dividend:
            price = getattr(quote, "price", None) if quote is not None else None
            ttm_cash = dividend.get("ttm_cash_dividend_per_share")
            if ttm_cash is not None and isinstance(price, (int, float)) and price > 0:
                dividend["ttm_dividend_yield_pct"] = round(float(ttm_cash) / float(price) * 100, 6)
                dividend["yield_formula"] = "ttm_cash_dividend_per_share / price * 100"
            elif ttm_cash is not None:
                dividend["ttm_dividend_yield_pct"] = None
                context.setdefault("earnings", {}).setdefault("errors", []).append(
                    "invalid_price_for_ttm_dividend_yield"
                )

        source_chain: List[str] = list(context.get("source_chain", []))
        errors: List[str] = list(context.get("errors", []))
        for key in ("valuation", "growth", "earnings", "institution", "capital_flow", "dragon_tiger", "boards"):
            block = context.get(key, {})
            source_chain.extend(block.get("source_chain", []))
            errors.extend(block.get("errors", []))

        deduped_source_chain = list(dict.fromkeys(item for item in source_chain if item))
        deduped_errors = list(dict.fromkeys(item for item in errors if item))
        context["source_chain"] = deduped_source_chain
        context["errors"] = deduped_errors

        coverage_values = list((context.get("coverage") or {}).values())
        overall_status = "not_supported"
        if coverage_values and all(value == "ok" for value in coverage_values):
            overall_status = "ok"
        elif any(value in {"ok", "partial"} for value in coverage_values):
            overall_status = "partial"
        elif any(value == "failed" for value in coverage_values):
            overall_status = "failed"
        context["status"] = overall_status
        return context

    async def get_fundamental_context(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self.get_fundamental_context_sync,
            stock_code,
            budget_seconds,
        )

    async def get_main_indices(self, region: str = "cn") -> Optional[List[dict]]:
        """异步获取主要指数。"""
        self._ensure_runtime_state()
        tickflow_fetcher = None
        if region == "cn":
            tickflow_fetcher = self._get_tickflow_fetcher()
            if tickflow_fetcher is not None:
                try:
                    data = tickflow_fetcher.get_main_indices(region=region)
                    if data:
                        return data
                except Exception as exc:
                    logger.warning("[TickFlowFetcher] 获取主要指数失败，回退后续数据源: %s", exc)

        for fetcher in self._fetchers:
            if fetcher is tickflow_fetcher or getattr(fetcher, "name", "") == "TickFlowFetcher":
                continue
            try:
                if hasattr(fetcher, "get_main_indices"):
                    result = fetcher.get_main_indices(region=region)
                    data = await self._maybe_await(result)
                    if data:
                        return data
            except InsufficientQuotaError as exc:
                logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
                continue
            except Exception:
                continue
        return None

    def get_main_indices_sync(self, region: str = "cn") -> Optional[List[dict]]:
        return self._run_awaitable_sync(self.get_main_indices(region=region))

    async def get_market_stats(self) -> Optional[dict]:
        """异步获取市场统计数据。"""
        self._ensure_runtime_state()
        tickflow_fetcher = self._get_tickflow_fetcher()
        if tickflow_fetcher is not None:
            try:
                data = tickflow_fetcher.get_market_stats()
                if data:
                    return self._normalize_market_stats(data, getattr(tickflow_fetcher, "name", "unknown"))
            except Exception as exc:
                logger.warning("[TickFlowFetcher] 获取市场统计失败，回退后续数据源: %s", exc)

        for fetcher in self._fetchers:
            if fetcher is tickflow_fetcher or getattr(fetcher, "name", "") == "TickFlowFetcher":
                continue
            try:
                if hasattr(fetcher, "get_market_stats"):
                    result = fetcher.get_market_stats()
                    data = await self._maybe_await(result)
                    if data:
                        return self._normalize_market_stats(data, getattr(fetcher, "name", "unknown"))
            except InsufficientQuotaError as exc:
                logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
                continue
            except Exception:
                continue
        return None

    def get_market_stats_sync(self) -> Optional[dict]:
        return self._run_awaitable_sync(self.get_market_stats())

    async def get_sector_rankings(self, n: int = 5) -> Optional[Tuple[List[dict], List[dict]]]:
        """异步获取板块排名。"""
        self._ensure_runtime_state()
        for fetcher in self._fetchers:
            if getattr(fetcher, "name", "") == "TickFlowFetcher":
                continue
            try:
                if hasattr(fetcher, "get_sector_rankings"):
                    result = fetcher.get_sector_rankings(n)
                    data = await self._maybe_await(result)
                    if data:
                        return data
            except InsufficientQuotaError as exc:
                logger.warning("[%s] 积分配额不足，尝试下一个数据源: %s", fetcher.name, exc)
                continue
            except Exception:
                continue
        return None

    def get_sector_rankings_sync(self, n: int = 5) -> Optional[Tuple[List[dict], List[dict]]]:
        return self._run_awaitable_sync(self.get_sector_rankings(n=n))

    async def get_market_overview(self, region: str = "cn") -> Dict[str, Any]:
        """异步获取大盘概览。"""
        tasks = [
            self.get_main_indices(region=region),
            self.get_market_stats() if region == "cn" else asyncio.sleep(0, {}),
            self.get_sector_rankings() if region == "cn" else asyncio.sleep(0, {}),
        ]

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            cleaned_results = []
            for result in results:
                if isinstance(result, Exception):
                    logger.error("[Manager] 子任务失败: %s", result)
                    cleaned_results.append(None)
                else:
                    cleaned_results.append(result)

            overview = {
                "indices": cleaned_results[0] or [],
                "stats": cleaned_results[1] or {},
                "sector_rankings": {},
            }
            sector_data = cleaned_results[2]
            if sector_data:
                if isinstance(sector_data, tuple):
                    overview["sector_rankings"] = {"top": sector_data[0], "bottom": sector_data[1]}
                else:
                    overview["sector_rankings"] = {"top": sector_data[:5], "bottom": sector_data[-5:]}
            return overview
        except Exception as exc:
            logger.error("[Manager] 获取市场概览失败: %s", exc)
            return {"indices": [], "stats": {}, "sector_rankings": {}}

    def get_market_overview_sync(self, region: str = "cn") -> Dict[str, Any]:
        return self._run_awaitable_sync(self.get_market_overview(region=region))

    @staticmethod
    def _normalize_market_stats(data: dict, source: str = "unknown") -> dict:
        """Normalize provider-specific market stats into MarketAnalyzer fields."""
        if not isinstance(data, dict):
            return {}

        def pick(*keys, default=0):
            for key in keys:
                value = data.get(key)
                if value is not None:
                    return value
            return default

        amount = pick("volume_total", "total_amount", "amount_total", default=0.0)
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            amount = 0.0

        normalized = dict(data)
        normalized.update(
            {
                "up": pick("up", "rise_count", "up_count", default=0),
                "down": pick("down", "fall_count", "down_count", default=0),
                "flat": pick("flat", "flat_count", default=0),
                "limit_up": pick("limit_up", "limit_up_count", default=0),
                "limit_down": pick("limit_down", "limit_down_count", default=0),
                "volume_total": amount,
                "total_amount": amount,
                "source": data.get("source") or source,
            }
        )
        return normalized

    def close(self) -> None:
        self._ensure_runtime_state()
        dedicated_tickflow = self._tickflow_fetcher
        self._tickflow_fetcher = None
        self._tickflow_api_key = None

        if dedicated_tickflow is not None:
            try:
                dedicated_tickflow.close()
            except Exception:
                pass

        for fetcher in self._fetchers:
            close_fn = getattr(fetcher, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    continue
