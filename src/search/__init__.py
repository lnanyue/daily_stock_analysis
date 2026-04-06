# -*- coding: utf-8 -*-
"""
搜索服务包 — 统一搜索接口、多引擎负载均衡和故障转移。

新代码应直接 ``from src.search import ...``，
旧代码通过 ``src/search_service.py`` 的 re-export shim 保持兼容。
"""

from .types import SearchResult, SearchResponse
from .http_utils import (
    fetch_url_content,
    extract_domain,
    post_with_retry,
    get_with_retry,
    SEARCH_TRANSIENT_EXCEPTIONS,
)
from .base_provider import BaseSearchProvider
from .providers import (
    TavilySearchProvider,
    SerpAPISearchProvider,
    BochaSearchProvider,
    MiniMaxSearchProvider,
    ExaSearchProvider,
    BraveSearchProvider,
    SearXNGSearchProvider,
)
from .service import SearchService, get_search_service, reset_search_service

__all__ = [
    # Types
    "SearchResult",
    "SearchResponse",
    # HTTP helpers
    "fetch_url_content",
    "extract_domain",
    "post_with_retry",
    "get_with_retry",
    "SEARCH_TRANSIENT_EXCEPTIONS",
    # Base
    "BaseSearchProvider",
    # Providers
    "TavilySearchProvider",
    "SerpAPISearchProvider",
    "BochaSearchProvider",
    "MiniMaxSearchProvider",
    "ExaSearchProvider",
    "BraveSearchProvider",
    "SearXNGSearchProvider",
    # Service
    "SearchService",
    "get_search_service",
    "reset_search_service",
]
