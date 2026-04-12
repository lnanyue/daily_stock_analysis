# -*- coding: utf-8 -*-
"""MiniMax Web Search Provider。"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

import requests

from ..types import SearchResult, SearchResponse
from ..base_provider import BaseSearchProvider
from src.utils.async_http import get_global_client, async_retry

logger = logging.getLogger(__name__)


class MiniMaxSearchProvider(BaseSearchProvider):
    """
    MiniMax Web Search (Coding Plan API)

    Features:
    - Backed by MiniMax Coding Plan subscription
    - Returns structured organic results with title/link/snippet/date
    - No native time-range parameter; time filtering is done via query
      augmentation and client-side date filtering
    - Circuit-breaker protection: 3 consecutive failures -> 300s cooldown

    API endpoint: POST https://api.minimaxi.com/v1/coding_plan/search
    """

    API_ENDPOINT = "https://api.minimaxi.com/v1/coding_plan/search"

    _CB_FAILURE_THRESHOLD = 3
    _CB_COOLDOWN_SECONDS = 300

    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "MiniMax")
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0

    @async_retry(max_attempts=2, min_wait=1.0)
    async def _do_search_async(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行异步 MiniMax 搜索"""
        has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in query)
        time_hint = self._time_hint(days, is_chinese=has_cjk)
        augmented_query = f"{query} {time_hint}"

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'MM-API-Source': 'Minimax-MCP',
        }
        payload = {"q": augmented_query}

        try:
            client = await get_global_client()
            response = await client.post(self.API_ENDPOINT, headers=headers, json=payload, timeout=15)
            
            if response.status_code != 200:
                return SearchResponse(query=query, results=[], provider=self.name, success=False, error_message=f"HTTP {response.status_code}")
            
            data = response.json()
            base_resp = data.get('base_resp', {})
            if base_resp.get('status_code', 0) != 0:
                return SearchResponse(query=query, results=[], provider=self.name, success=False, error_message=base_resp.get('status_msg', 'Unknown API error'))

            results = []
            for item in data.get('organic', []):
                date_val = item.get('date')
                if not self._is_within_days(date_val, days): continue

                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=(item.get('snippet', '') or '')[:500],
                    url=item.get('link', ''),
                    source=self._extract_domain(item.get('link', '')),
                    published_date=date_val,
                ))
                if len(results) >= max_results: break
            
            return SearchResponse(query=query, results=results, provider=self.name, success=True)
        except Exception as e:
            return SearchResponse(query=query, results=[], provider=self.name, success=False, error_message=str(e))

    @property
    def is_available(self) -> bool:
        if not super().is_available:
            return False
        if self._consecutive_failures >= self._CB_FAILURE_THRESHOLD:
            if time.time() < self._circuit_open_until:
                return False
        return True

    def _record_success(self, key: str) -> None:
        super()._record_success(key)
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _record_error(self, key: str) -> None:
        super()._record_error(key)
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._CB_FAILURE_THRESHOLD:
            self._circuit_open_until = time.time() + self._CB_COOLDOWN_SECONDS
            logger.warning(
                f"[MiniMax] Circuit breaker OPEN – "
                f"{self._consecutive_failures} consecutive failures, "
                f"cooldown {self._CB_COOLDOWN_SECONDS}s"
            )

    @staticmethod
    def _time_hint(days: int, is_chinese: bool = True) -> str:
        if is_chinese:
            if days <= 1:
                return "今天"
            elif days <= 3:
                return "最近三天"
            elif days <= 7:
                return "最近一周"
            else:
                return "最近一个月"
        else:
            if days <= 1:
                return "today"
            elif days <= 3:
                return "past 3 days"
            elif days <= 7:
                return "past week"
            else:
                return "past month"

    @staticmethod
    def _is_within_days(date_str: Optional[str], days: int) -> bool:
        if not date_str:
            return True
        try:
            from dateutil import parser as dateutil_parser
            dt = dateutil_parser.parse(date_str, fuzzy=True)
            now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
            return (now - dt) <= timedelta(days=days + 1)
        except Exception:
            return True

    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        try:
            has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in query)
            time_hint = self._time_hint(days, is_chinese=has_cjk)
            augmented_query = f"{query} {time_hint}"

            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'MM-API-Source': 'Minimax-MCP',
            }
            payload = {"q": augmented_query}

            response = post_with_retry(
                self.API_ENDPOINT, headers=headers, json=payload, timeout=15
            )

            if response.status_code != 200:
                error_msg = self._parse_http_error(response)
                logger.warning("[MiniMax] Search failed: %s", error_msg)
                return SearchResponse(
                    query=query, results=[], provider=self.name,
                    success=False, error_message=error_msg,
                )

            data = response.json()

            base_resp = data.get('base_resp', {})
            if base_resp.get('status_code', 0) != 0:
                error_msg = base_resp.get('status_msg', 'Unknown API error')
                return SearchResponse(
                    query=query, results=[], provider=self.name,
                    success=False, error_message=error_msg,
                )

            logger.info("[MiniMax] Search done, query='%s'", query)
            logger.debug("[MiniMax] Raw response keys: %s", list(data.keys()))

            results: List[SearchResult] = []
            for item in data.get('organic', []):
                date_val = item.get('date')
                if not self._is_within_days(date_val, days):
                    continue

                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=(item.get('snippet', '') or '')[:500],
                    url=item.get('link', ''),
                    source=self._extract_domain(item.get('link', '')),
                    published_date=date_val,
                ))

                if len(results) >= max_results:
                    break

            logger.info("[MiniMax] Parsed %s results (after time filter)", len(results))

            return SearchResponse(
                query=query, results=results, provider=self.name, success=True,
            )

        except requests.exceptions.Timeout:
            error_msg = "Request timeout"
            logger.error("[MiniMax] %s", error_msg)
            return SearchResponse(
                query=query, results=[], provider=self.name,
                success=False, error_message=error_msg,
            )
        except requests.exceptions.RequestException as e:
            error_msg = f"Network error: {e}"
            logger.error("[MiniMax] %s", error_msg)
            return SearchResponse(
                query=query, results=[], provider=self.name,
                success=False, error_message=error_msg,
            )
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logger.error("[MiniMax] %s", error_msg)
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
                base_resp = err.get('base_resp', {})
                msg = base_resp.get('status_msg') or err.get('message') or str(err)
                return msg
            return response.text[:200]
        except Exception:
            return f"HTTP {response.status_code}: {response.text[:200]}"
