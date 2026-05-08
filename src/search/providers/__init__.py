# -*- coding: utf-8 -*-
"""搜索引擎 Provider 集合。"""

from .tavily import TavilySearchProvider
from .openbb import OpenBBNewsProvider
from .akshare import AkshareNewsProvider
from .finnhub import FinnhubNewsProvider

__all__ = [
    "TavilySearchProvider",
    "OpenBBNewsProvider",
    "AkshareNewsProvider",
    "FinnhubNewsProvider",
]
