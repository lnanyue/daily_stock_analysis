# -*- coding: utf-8 -*-
"""
Backward-compatible re-export shim.

New code should ``from src.search import ...`` instead.
This file is kept so that existing ``from src.search_service import ...``
statements continue to work without modification.
"""

from src.search import (  # noqa: F401 – re-export
    SearchResult,
    SearchResponse,
    BaseSearchProvider,
    SearchService,
    TavilySearchProvider,
    SerpAPISearchProvider,
    BochaSearchProvider,
    MiniMaxSearchProvider,
    ExaSearchProvider,
    BraveSearchProvider,
    SearXNGSearchProvider,
    get_search_service,
    reset_search_service,
    fetch_url_content,
)
import requests  # noqa: F401 - legacy tests/callers patch src.search_service.requests

# Legacy private names used by some callers
from src.search.http_utils import (  # noqa: F401
    post_with_retry as _post_with_retry,
    get_with_retry as _get_with_retry,
    SEARCH_TRANSIENT_EXCEPTIONS as _SEARCH_TRANSIENT_EXCEPTIONS,
)

__all__ = [
    "SearchResult",
    "SearchResponse",
    "BaseSearchProvider",
    "SearchService",
    "TavilySearchProvider",
    "SerpAPISearchProvider",
    "BochaSearchProvider",
    "MiniMaxSearchProvider",
    "ExaSearchProvider",
    "BraveSearchProvider",
    "SearXNGSearchProvider",
    "get_search_service",
    "reset_search_service",
    "fetch_url_content",
]
