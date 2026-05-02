# -*- coding: utf-8 -*-
"""搜索引擎 Provider 集合。"""

from .tavily import TavilySearchProvider
from .openbb import OpenBBNewsProvider
from .akshare import AkshareNewsProvider

__all__ = [
    "TavilySearchProvider",
    "OpenBBNewsProvider",
    "AkshareNewsProvider",
]
