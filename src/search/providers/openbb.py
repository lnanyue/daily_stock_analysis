# -*- coding: utf-8 -*-
"""OpenBB company news provider."""

import logging
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from ..base_provider import BaseSearchProvider
from ..types import SearchResult, SearchResponse

logger = logging.getLogger(__name__)


class OpenBBNewsProvider(BaseSearchProvider):
    """
    OpenBB company news provider.

    Uses ``obb.news.company`` when OpenBB is installed. This provider is optional
    and intentionally does not make OpenBB a hard dependency for the project.
    """

    _STOPWORDS = {
        "STOCK",
        "NEWS",
        "RISK",
        "PRICE",
        "EVENT",
        "EVENTS",
        "LEGAL",
        "SUIT",
        "TODAY",
    }

    def __init__(self, provider: str = "yfinance", enabled: bool = True):
        super().__init__(["openbb"] if enabled else [], "OpenBB")
        self._provider_name = (provider or "yfinance").strip().lower() or "yfinance"

    @property
    def openbb_provider(self) -> str:
        return self._provider_name

    def _record_error(self, key: str) -> None:
        """Avoid logging a fake API key for OpenBB's keyless provider wrapper."""
        self._key_errors[key] = self._key_errors.get(key, 0) + 1
        logger.warning("[%s] 错误计数: %s", self._name, self._key_errors[key])

    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        symbol = self._extract_symbol(query)
        if not symbol:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="无法从查询中识别 OpenBB 公司新闻所需的股票代码",
            )

        try:
            from openbb import obb
        except ImportError:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="OpenBB 未安装；如需启用请安装 openbb 及对应数据扩展",
            )

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=max(1, int(days)))
        limit = max(1, int(max_results))

        try:
            response = self._call_company_news(
                obb,
                symbol=symbol,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                limit=limit,
            )
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=f"OpenBB company news failed for {symbol}: {e}",
            )

        try:
            results = self._parse_results(response, query=query, max_results=limit)
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=f"OpenBB company news parse failed for {symbol}: {e}",
            )
        logger.info(
            "[OpenBB] company news complete, symbol=%s, provider=%s, results=%s",
            symbol,
            self._provider_name,
            len(results),
        )
        return SearchResponse(query=query, results=results, provider=self.name, success=True)

    def _call_company_news(
        self,
        obb: Any,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        limit: int,
    ) -> Any:
        """Call OpenBB, retrying without dates for providers with narrower args."""
        kwargs: Dict[str, Any] = {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit,
            "provider": self._provider_name,
        }
        try:
            return obb.news.company(**kwargs)
        except TypeError:
            return obb.news.company(
                symbol=symbol,
                limit=limit,
                provider=self._provider_name,
            )

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
            if upper in cls._STOPWORDS:
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
    def _parse_results(cls, response: Any, *, query: str, max_results: int) -> List[SearchResult]:
        rows = cls._coerce_rows(response)
        results: List[SearchResult] = []
        seen_urls = set()

        for item in rows:
            data = cls._as_dict(item)
            title = str(cls._pick(data, item, "title", "headline") or "").strip()
            url = str(cls._pick(data, item, "url", "link") or "").strip()
            if not title:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            snippet = str(
                cls._pick(data, item, "excerpt", "summary", "description", "body", "content")
                or ""
            ).strip()
            source = str(cls._pick(data, item, "source", "publisher") or "").strip()
            if not source and url:
                source = cls._extract_domain(url)
            if not source:
                source = "OpenBB"

            published = cls._format_date(cls._pick(data, item, "date", "published_date", "published"))
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

    @staticmethod
    def _coerce_rows(response: Any) -> List[Any]:
        if response is None:
            return []
        rows = getattr(response, "results", None)
        if rows is None and isinstance(response, dict):
            rows = response.get("results")
        if rows is None and hasattr(response, "to_dict"):
            try:
                rows = response.to_dict().get("results")
            except Exception:
                rows = None
        if rows is None:
            return []
        return list(rows)

    @staticmethod
    def _as_dict(item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        for method in ("model_dump", "dict"):
            fn = getattr(item, method, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:
                    pass
        if isinstance(item, SimpleNamespace):
            return vars(item)
        return {}

    @staticmethod
    def _pick(data: Dict[str, Any], item: Any, *names: str) -> Any:
        for name in names:
            if name in data and data[name] not in (None, ""):
                return data[name]
            value = getattr(item, name, None)
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _format_date(value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else text
