# -*- coding: utf-8 -*-
"""
Search tools — wraps SearchService methods as agent-callable tools.

Tools:
- search_stock_news: search latest stock news
- search_comprehensive_intel: multi-dimensional intelligence search
"""

import logging
from typing import Optional

import trafilatura

from src.agent.tools.registry import ToolParameter, ToolDefinition

logger = logging.getLogger(__name__)

# Maximum number of search results to attempt full-text extraction on
_MAX_EXTRACT_URLS = 3


def _extract_full_text(url: str, timeout: int = 10) -> Optional[str]:
    """Fetch and extract readable text from a URL using trafilatura.

    Returns None on timeout, network error, or paywalled content.
    """
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded, include_links=False, include_images=False)
        return text.strip() if text else None
    except Exception as exc:
        logger.debug("Full-text extraction failed for %s: %s", url, exc)
        return None


def _build_results_with_full_text(results: list) -> list:
    """Build result dicts with full-text extraction for the first N results."""
    built = []
    for i, r in enumerate(results):
        item = {
            "title": r.title,
            "url": r.url,
            "source": r.source,
            "published_date": r.published_date,
        }
        # Extract full text for first _MAX_EXTRACT_URLS results
        if i < _MAX_EXTRACT_URLS:
            full_text = _extract_full_text(r.url)
            item["extracted"] = full_text is not None
            item["full_text_snippet"] = full_text[:500] if full_text else ""
            item["full_text"] = full_text or ""
        else:
            item["extracted"] = False
            item["full_text_snippet"] = r.snippet or ""
            item["full_text"] = ""
        built.append(item)
    return built


def _get_search_service():
    """Return shared SearchService singleton."""
    from src.search_service import get_search_service
    return get_search_service()


def _canonical_search_code(stock_code: str) -> str:
    from data_provider.base import canonical_stock_code, normalize_stock_code

    return canonical_stock_code(normalize_stock_code(str(stock_code or "").strip()))


def _handle_search_stock_news(stock_code: str, stock_name: str) -> dict:
    """Search latest news for a stock."""
    service = _get_search_service()

    if not service.is_available:
        return {"error": "No search engine available (no API keys configured)"}

    response = service.search_stock_news(stock_code, stock_name, max_results=5)

    if not response.success:
        return {
            "query": response.query,
            "success": False,
            "error": response.error_message,
        }

    return {
        "query": response.query,
        "provider": response.provider,
        "success": True,
        "results_count": len(response.results),
        "results": _build_results_with_full_text(response.results),
    }


search_stock_news_tool = ToolDefinition(
    name="search_stock_news",
    description="Search for the latest news articles about a specific stock. "
                "Requires both stock_code and stock_name for accurate search. "
                "Returns news titles, snippets, sources, and URLs.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_stock_news,
    category="search",
)


# ============================================================
# search_comprehensive_intel
# ============================================================

def _handle_search_comprehensive_intel(stock_code: str, stock_name: str) -> dict:
    """Multi-dimensional intelligence search."""
    service = _get_search_service()

    if not service.is_available:
        return {"error": "No search engine available (no API keys configured)"}

    intel_results = service.search_comprehensive_intel(
        stock_code=stock_code,
        stock_name=stock_name,
        max_searches=6,
    )

    if not intel_results:
        return {"error": "Comprehensive intel search returned no results"}

    # Format into readable report
    report = service.format_intel_report(intel_results, stock_name)

    # Also return structured data
    dimensions = {}
    for dim_name, response in intel_results.items():
        if response and response.success:
            dimensions[dim_name] = {
                "query": response.query,
                "results_count": len(response.results),
                "results": [
                    {
                        "title": r.title,
                        "snippet": r.snippet,
                        "source": r.source,
                        "url": r.url or "",
                        "published_date": r.published_date or "",
                    }
                    for r in response.results[:3]  # limit to 3 per dimension to save tokens
                ],
            }

    return {
        "report": report,
        "dimensions": dimensions,
    }


search_comprehensive_intel_tool = ToolDefinition(
    name="search_comprehensive_intel",
    description="Multi-dimensional intelligence search: latest news, market analysis, "
                "risk checking, earnings outlook, and industry trends for a stock. "
                "Returns a formatted report and structured results.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_comprehensive_intel,
    category="search",
)


ALL_SEARCH_TOOLS = [
    search_stock_news_tool,
    search_comprehensive_intel_tool,
]
