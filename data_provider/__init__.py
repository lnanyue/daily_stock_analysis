# -*- coding: utf-8 -*-
"""
数据提供层
"""

from .base import (
    BaseFetcher,
    DataFetchError,
    RateLimitError,
    DataSourceUnavailableError,
)
from .manager import (
    DataFetcherManager,
    canonical_stock_code,
)
from .utils import (
    normalize_stock_code,
    is_bse_code,
    is_st_stock,
    is_kc_cy_stock,
    summarize_exception,
    STANDARD_COLUMNS,
)
from .realtime_types import (
    UnifiedRealtimeQuote,
    ChipDistribution,
    RealtimeSource,
)

__all__ = [
    "BaseFetcher",
    "DataFetchError",
    "RateLimitError",
    "DataSourceUnavailableError",
    "DataFetcherManager",
    "canonical_stock_code",
    "normalize_stock_code",
    "is_bse_code",
    "is_st_stock",
    "is_kc_cy_stock",
    "summarize_exception",
    "STANDARD_COLUMNS",
    "UnifiedRealtimeQuote",
    "ChipDistribution",
    "RealtimeSource",
]
