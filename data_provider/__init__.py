# -*- coding: utf-8 -*-
"""
数据提供层
"""

from .exceptions import (
    DataFetchError,
    RateLimitError,
    DataSourceUnavailableError,
)
from .base import BaseFetcher
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
    pick_random_user_agent,
    DEFAULT_USER_AGENTS,
)
from .realtime_types import (
    UnifiedRealtimeQuote,
    ChipDistribution,
    RealtimeSource,
)
from .us_index_mapping import (
    is_us_stock_code,
    is_us_index_code,
)

# 补充缺失的常用逻辑
def is_hk_stock_code(code: str) -> bool:
    """判定是否为港股代码"""
    from .utils import _is_hk_market
    return _is_hk_market(code)

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
    "is_us_stock_code",
    "is_us_index_code",
    "is_hk_stock_code",
    "summarize_exception",
    "STANDARD_COLUMNS",
    "pick_random_user_agent",
    "UnifiedRealtimeQuote",
    "ChipDistribution",
    "RealtimeSource",
]
