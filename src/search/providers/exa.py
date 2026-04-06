# -*- coding: utf-8 -*-
"""Exa (formerly Metaphor) 搜索引擎 Provider。"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from ..types import SearchResult, SearchResponse
from ..base_provider import BaseSearchProvider
from ..http_utils import post_with_retry

logger = logging.getLogger(__name__)


class ExaSearchProvider(BaseSearchProvider):
    """
    Exa (formerly Metaphor) search engine.
    Uses semantic search to find high-quality content.
    """
    API_ENDPOINT = "https://api.exa.ai/search"

    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "Exa")

    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        try:
            headers = {
                'x-api-key': api_key,
                'Content-Type': 'application/json',
            }
            
            start_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

            payload = {
                "query": query,
                "numResults": max_results,
                "startPublishedDate": start_date,
                "useAutoprompt": True,
                "type": "neural"
            }

            response = post_with_retry(
                self.API_ENDPOINT, headers=headers, json=payload, timeout=15
            )

            if response.status_code != 200:
                error_msg = self._parse_http_error(response)
                logger.warning(f"[Exa] Search failed: {error_msg}")
                return SearchResponse(
                    query=query, results=[], provider=self.name,
                    success=False, error_message=error_msg,
                )

            data = response.json()
            results = []
            for item in data.get('results', []):
                pub_date = item.get('publishedDate')
                if pub_date:
                    try:
                        dt = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                        pub_date = dt.strftime('%Y-%m-%d')
                    except Exception:
                        pass

                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=(item.get('text', '') or item.get('snippet', '') or '')[:500],
                    url=item.get('url', ''),
                    source=self._extract_domain(item.get('url', '')),
                    published_date=pub_date,
                ))

            logger.info(f"[Exa] Search done, query='{query}', results={len(results)}")

            return SearchResponse(
                query=query, results=results, provider=self.name, success=True,
            )

        except Exception as e:
            error_msg = f"Exa search error: {e}"
            logger.error(f"[Exa] {error_msg}")
            return SearchResponse(
                query=query, results=[], provider=self.name,
                success=False, error_message=error_msg,
            )

    @staticmethod
    def _parse_http_error(response) -> str:
        try:
            ct = response.headers.get('content-type', '')
            if 'json' in ct:
                err = response.json()
                msg = err.get('message') or str(err)
                return msg
            return response.text[:200]
        except Exception:
            return f"HTTP {response.status_code}: {response.text[:200]}"
