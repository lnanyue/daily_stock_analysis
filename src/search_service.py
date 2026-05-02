# -*- coding: utf-8 -*-
"""
Backward-compatible re-export shim.

New code should ``from src.search import ...`` instead.
This file is kept so that existing ``from src.search_service import ...``
statements continue to work without modification.
"""

import time  # re-exported for legacy tests/callers that patch src.search_service.time

from src.search import (  # noqa: F401 – re-export
    SearchResult,
    SearchResponse,
    BaseSearchProvider,
    SearchService,
    TavilySearchProvider,
    OpenBBNewsProvider,
    AkshareNewsProvider,
    get_search_service,
    reset_search_service,
    fetch_url_content,
)
