# -*- coding: utf-8 -*-
"""
===================================
OpenBBFetcher - 可选市场数据源 (Priority 4)
===================================

数据来源：OpenBB Platform
特点：可复用 OpenBB 已安装 provider（默认 yfinance）获取历史价格、实时行情与股票名称。

设计原则：
1. OpenBB 作为可选依赖，不安装时不影响主流程。
2. 与项目现有股票代码规范保持一致（A股/HK/US 自动归一）。
3. 优先提供通用日线与实时行情能力，不把项目锁死在某个单一 OpenBB provider。
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from .base import BaseFetcher, DataFetchError, normalize_stock_code
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote, safe_float, safe_int
from .us_index_mapping import get_us_index_yf_symbol, is_us_stock_code

logger = logging.getLogger(__name__)


class OpenBBFetcher(BaseFetcher):
    """OpenBB 数据源实现。"""

    name = "OpenBBFetcher"
    priority = int(os.getenv("OPENBB_FETCHER_PRIORITY", "4"))

    def __init__(self, provider: Optional[str] = None, config=None):
        super().__init__(config=config)
        configured_provider = provider
        if configured_provider is None and config is not None:
            configured_provider = getattr(config, "openbb_fetcher_provider", None)
        self._provider_name = (configured_provider or "yfinance").strip().lower() or "yfinance"

    @property
    def openbb_provider(self) -> str:
        return self._provider_name

    @staticmethod
    def _format_hk_symbol(value: str) -> str:
        stripped = (value or "").strip()
        numeric = stripped.lstrip("0") or "0"
        return f"{numeric.zfill(4)}.HK"

    @staticmethod
    def _format_cn_symbol(value: str) -> str:
        code = (value or "").strip().upper()
        if code.startswith("6"):
            return f"{code}.SS"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8", "9")):
            return f"{code}.BJ"
        return code

    @classmethod
    def _to_openbb_symbol(cls, stock_code: str) -> str:
        raw = (stock_code or "").strip().upper()
        yf_symbol, _ = get_us_index_yf_symbol(raw)
        if yf_symbol:
            return yf_symbol

        normalized = normalize_stock_code(raw)
        if is_us_stock_code(raw) or is_us_stock_code(normalized):
            return normalized if is_us_stock_code(normalized) else raw

        if normalized.startswith("HK") and normalized[2:].isdigit():
            return cls._format_hk_symbol(normalized[2:])

        if normalized.isdigit() and len(normalized) == 5:
            return cls._format_hk_symbol(normalized)

        if normalized.isdigit() and len(normalized) == 6:
            return cls._format_cn_symbol(normalized)

        return raw

    @staticmethod
    def _get_openbb():
        try:
            from openbb import obb
        except ImportError as exc:
            raise DataFetchError("OpenBB 未安装；如需启用请安装 openbb 及对应 provider 扩展") from exc
        return obb

    @staticmethod
    def _coerce_rows(response: Any) -> List[Any]:
        if response is None:
            return []

        rows = getattr(response, "results", None)
        if rows is None and isinstance(response, dict):
            rows = response.get("results")
        if rows is None and hasattr(response, "to_dict"):
            try:
                payload = response.to_dict()
            except Exception:
                payload = None
            if isinstance(payload, dict):
                rows = payload.get("results")

        if rows is None:
            return [response]
        return list(rows)

    @staticmethod
    def _as_dict(item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        for method_name in ("model_dump", "dict"):
            method = getattr(item, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    pass
        if isinstance(item, SimpleNamespace):
            return vars(item)
        return {}

    @staticmethod
    def _pick(data: Dict[str, Any], item: Any, *names: str) -> Any:
        for name in names:
            if name in data and data[name] not in (None, ""):
                return data[name]
            value = getattr(item, name, None)
            if value not in (None, ""):
                return value
        return None

    def _call_historical(self, stock_code: str, start_date: str, end_date: str) -> Any:
        obb = self._get_openbb()
        symbol = self._to_openbb_symbol(stock_code)
        kwargs: Dict[str, Any] = {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "interval": "1d",
            "provider": self._provider_name,
        }
        if symbol.startswith("^"):
            return obb.index.price.historical(**kwargs)
        return obb.equity.price.historical(**kwargs)

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        response = self._call_historical(stock_code, start_date, end_date)

        to_df = getattr(response, "to_df", None)
        if callable(to_df):
            try:
                df = to_df()
            except Exception:
                df = None
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df

        rows = self._coerce_rows(response)
        if not rows:
            raise DataFetchError(f"OpenBB 未查询到 {stock_code} 的历史数据")

        records = [self._as_dict(item) or vars(item) for item in rows]
        df = pd.DataFrame(records)
        if df.empty:
            raise DataFetchError(f"OpenBB 未查询到 {stock_code} 的历史数据")
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        work_df = df.copy()
        work_df.columns = [str(col).strip().lower() for col in work_df.columns]

        if "date" not in work_df.columns:
            if isinstance(work_df.index, pd.DatetimeIndex):
                work_df = work_df.reset_index().rename(columns={"index": "date"})
            elif work_df.columns.size > 0 and str(work_df.columns[0]).lower() in {"datetime", "timestamp"}:
                work_df = work_df.rename(columns={work_df.columns[0]: "date"})

        rename_map = {
            "adj_close": "close",
            "close_adj": "close",
            "last_price": "close",
        }
        for source, target in rename_map.items():
            if source in work_df.columns and target not in work_df.columns:
                work_df = work_df.rename(columns={source: target})

        work_df["code"] = normalize_stock_code(stock_code)

        if "amount" not in work_df.columns:
            close_series = pd.to_numeric(work_df.get("close"), errors="coerce")
            volume_series = pd.to_numeric(work_df.get("volume"), errors="coerce")
            work_df["amount"] = close_series * volume_series

        close_series = pd.to_numeric(work_df.get("close"), errors="coerce")
        if "pct_chg" not in work_df.columns:
            previous_close = close_series.shift(1)
            work_df["pct_chg"] = ((close_series - previous_close) / previous_close) * 100

        for column in ("open", "high", "low", "close", "volume", "amount", "pct_chg"):
            if column not in work_df.columns:
                work_df[column] = None

        standard_columns = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "code"]
        return work_df[standard_columns]

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        obb = self._get_openbb()
        symbol = self._to_openbb_symbol(stock_code)
        response = obb.equity.price.quote(symbol=symbol, provider=self._provider_name)

        rows = self._coerce_rows(response)
        if not rows:
            return None

        item = rows[0]
        data = self._as_dict(item)
        price = safe_float(self._pick(data, item, "last_price", "price", "close"))
        pre_close = safe_float(self._pick(data, item, "prev_close", "previous_close", "close_prev"))
        change_amount = safe_float(self._pick(data, item, "change", "change_amount"))
        change_pct = safe_float(self._pick(data, item, "change_percent", "percent_change", "change_pct"))

        if change_amount is None and price is not None and pre_close not in (None, 0):
            change_amount = round(price - pre_close, 4)
        if change_pct is None and price is not None and pre_close not in (None, 0):
            change_pct = round((price - pre_close) / pre_close * 100, 4)

        total_mv = safe_float(self._pick(data, item, "market_cap", "total_mv"))
        quote = UnifiedRealtimeQuote(
            code=normalize_stock_code(stock_code),
            name=str(self._pick(data, item, "name", "company_name", "long_name", "short_name") or "").strip(),
            source=RealtimeSource.OPENBB,
            price=price,
            change_pct=change_pct,
            change_amount=change_amount,
            volume=safe_int(self._pick(data, item, "volume", "total_volume")),
            amount=safe_float(self._pick(data, item, "amount", "total_notional")),
            open_price=safe_float(self._pick(data, item, "open", "open_price")),
            high=safe_float(self._pick(data, item, "high", "high_price")),
            low=safe_float(self._pick(data, item, "low", "low_price")),
            pre_close=pre_close,
            pe_ratio=safe_float(self._pick(data, item, "pe_ratio", "pe", "price_earnings")),
            pb_ratio=safe_float(self._pick(data, item, "pb_ratio", "pb", "price_to_book")),
            total_mv=total_mv,
            circ_mv=safe_float(self._pick(data, item, "circ_mv", "float_market_cap")) or total_mv,
        )
        return quote if quote.has_basic_data() else None

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        quote = self.get_realtime_quote(stock_code)
        if quote is None:
            return None
        name = str(getattr(quote, "name", "") or "").strip()
        return name or None
