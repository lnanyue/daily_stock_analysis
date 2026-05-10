"""Parquet-based stock data cache — replaces SQLite k-line cache.

Zero ORM dependencies. Uses pandas/parquet for O(1) per-stock reads.
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "stock-data"


def _cache_path(cache_dir: Path, code: str) -> Path:
    return cache_dir / f"{code}.parquet"


def _meta_path(cache_dir: Path, code: str) -> Path:
    return cache_dir / f"{code}.meta.json"


class StockCache:
    """Per-stock parquet cache for daily k-line data."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self._cache_dir = Path(cache_dir or _DEFAULT_CACHE_DIR)

    def is_fresh(self, code: str) -> bool:
        """Has the stock been fetched today?"""
        path = _cache_path(self._cache_dir, code)
        if not path.exists():
            return False
        meta_path = _meta_path(self._cache_dir, code)
        if not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text())
            return meta.get("fetch_date") == date.today().isoformat()
        except Exception:
            return False

    def read(self, code: str) -> Tuple[Optional[pd.DataFrame], str]:
        """Read cached DataFrame. Returns (None, 'none') on miss."""
        path = _cache_path(self._cache_dir, code)
        if not path.exists():
            return None, "none"
        try:
            df = pd.read_parquet(path)
            return df, "parquet_cache"
        except Exception as exc:
            logger.debug("Cache read failed for %s: %s", code, exc)
            return None, "none"

    def write(self, code: str, df: pd.DataFrame) -> None:
        """Write DataFrame to parquet with companion metadata."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path = _cache_path(self._cache_dir, code)
        # Normalize date column name
        if "date" not in df.columns:
            for alias in ("日期", "trade_date", "tradeDate"):
                if alias in df.columns:
                    df = df.rename(columns={alias: "date"})
                    break
        # Ensure date column is datetime type for comparison
        if "date" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["date"]):
            df["date"] = pd.to_datetime(df["date"])
        df.to_parquet(path, index=False)
        # Write companion metadata
        meta = {
            "fetch_date": date.today().isoformat(),
            "code": code,
            "rows": len(df),
        }
        _meta_path(self._cache_dir, code).write_text(json.dumps(meta, ensure_ascii=False))


def find_close_for_date(df: pd.DataFrame, target_date: date) -> Optional[float]:
    """Find the close price nearest to and <= target_date."""
    date_col = "date"
    if date_col not in df.columns:
        return None
    close_col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
    if close_col is None:
        return None
    # Normalize date column to datetime64 for reliable comparison
    if not pd.api.types.is_datetime64_any_dtype(df[date_col]):
        df = df.copy()
        df[date_col] = pd.to_datetime(df[date_col])
    ts_target = pd.Timestamp(target_date)
    match = df[df[date_col] <= ts_target]
    if not match.empty:
        match = match.sort_values(date_col, ascending=False)
        return float(match.iloc[0][close_col])
    # Fallback to earliest available
    if not df.empty:
        df_sorted = df.sort_values(date_col, ascending=True)
        return float(df_sorted.iloc[0][close_col])
    return None
