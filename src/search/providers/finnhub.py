# -*- coding: utf-8 -*-
"""Finnhub company news provider."""

import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

from ..base_provider import BaseSearchProvider
from ..types import SearchResult, SearchResponse

logger = logging.getLogger(__name__)


class FinnhubNewsProvider(BaseSearchProvider):
    """
    Finnhub company news provider.

    Uses Finnhub's ``company-news`` endpoint via the ``finnhub-python`` SDK.
    Requires ``FINNHUB_API_KEY`` set in environment.
    """

    def __init__(self, api_key: str):
        keys = [api_key] if api_key else []
        super().__init__(keys, "Finnhub")
        self._api_key = api_key

    def _do_search(
        self, query: str, api_key: str, max_results: int, days: int = 7
    ) -> SearchResponse:
        symbol = self._extract_symbol(query)
        if not symbol:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="无法从查询中识别 Finnhub 公司新闻所需的股票代码",
            )

        try:
            import finnhub
        except ImportError:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="finnhub-python 未安装；执行 pip install finnhub-python",
            )

        end_date = date.today()
        start_date = end_date - timedelta(days=max(1, int(days)))

        try:
            client = finnhub.Client(api_key=self._api_key)
            raw = client.company_news(
                symbol,
                _from=start_date.isoformat(),
                to=end_date.isoformat(),
            )
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=f"Finnhub company news failed for {symbol}: {e}",
            )

        results = self._parse_results(raw, max_results=max(1, int(max_results)))
        logger.info(
            "[Finnhub] company news complete, symbol=%s, results=%s",
            symbol,
            len(results),
        )
        return SearchResponse(query=query, results=results, provider=self.name, success=True)

    @classmethod
    def _extract_symbol(cls, query: str) -> Optional[str]:
        raw = query or ""

        hk_match = re.search(r"(?i)\bhk[\s:-]?(\d{4,5})\b", raw)
        if hk_match:
            return cls._format_hk_symbol(hk_match.group(1))

        six_digit = re.search(r"(?<!\d)([036489]\d{5})(?!\d)", raw)
        if six_digit:
            return cls._format_cn_symbol(six_digit.group(1))

        five_digit = re.search(r"(?<!\d)(\d{5})(?!\d)", raw)
        if five_digit:
            return cls._format_hk_symbol(five_digit.group(1))

        candidates = re.findall(r"\b[A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?\b", raw)
        for token in candidates:
            upper = token.upper()
            if token != upper:
                continue
            return upper

        return None

    @staticmethod
    def _format_hk_symbol(value: str) -> str:
        stripped = (value or "").strip()
        numeric = stripped.lstrip("0") or "0"
        return f"{numeric.zfill(4)}.HK"

    @staticmethod
    def _format_cn_symbol(value: str) -> str:
        code = (value or "").strip()
        if code.startswith("6"):
            return f"{code}.SS"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8", "9")):
            return f"{code}.BJ"
        return code

    @classmethod
    def _parse_results(cls, raw: list, max_results: int) -> List[SearchResult]:
        results: List[SearchResult] = []
        seen_urls = set()

        for item in raw or []:
            if not isinstance(item, dict):
                continue

            title = str(item.get("headline") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            snippet = str(item.get("summary") or "").strip()
            source = str(item.get("source") or "").strip()
            if not source and url:
                source = cls._extract_domain(url)
            if not source:
                source = "Finnhub"

            published = None
            ts = item.get("datetime")
            if ts:
                try:
                    published = (
                        datetime.fromtimestamp(int(ts)).date().isoformat()
                    )
                except (ValueError, OSError):
                    pass

            results.append(
                SearchResult(
                    title=title,
                    snippet=snippet[:600],
                    url=url,
                    source=source,
                    published_date=published,
                )
            )
            if len(results) >= max_results:
                break

        return results
