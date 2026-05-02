# -*- coding: utf-8 -*-
"""AkShare 东方财富个股新闻 Provider（覆盖 A 股 / 港股 / 美股）。"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from ..base_provider import BaseSearchProvider
from ..types import SearchResult, SearchResponse

logger = logging.getLogger(__name__)


class AkshareNewsProvider(BaseSearchProvider):
    """
    东方财富个股新闻（通过 AkShare）。

    使用 ``ak.stock_news_em`` 获取股票相关新闻，覆盖 A 股／港股／美股。
    无需 API Key，AkShare 已是项目依赖。
    """

    def __init__(self, enabled: bool = True):
        super().__init__(["akshare"] if enabled else [], "AkShare")

    def _record_error(self, key: str) -> None:
        """避免记录虚拟 API Key 的详细错误。"""
        self._key_errors[key] = self._key_errors.get(key, 0) + 1
        logger.warning("[%s] 错误计数: %s", self._name, self._key_errors[key])

    @staticmethod
    def _extract_symbol(query: str) -> Optional[str]:
        raw = (query or "").strip()
        if not raw:
            return None

        # 先尝试匹配 6 位 A 股代码
        m = re.search(r"(?<!\d)([03689]\d{5})(?!\d)", raw)
        if m:
            return m.group(1)

        # 匹配 hk 前缀的港股代码（hk00700 -> 00700）
        m = re.search(r"(?i)\bhk(\d{4,5})\b", raw)
        if m:
            return m.group(1)

        # 匹配纯数字 5 位港股代码
        m = re.search(r"(?<!\d)(\d{5})(?!\d)", raw)
        if m:
            return m.group(1)

        # 匹配美股的字母代码
        m = re.search(r"\b([A-Z]{1,5})\b", raw)
        if m:
            return m.group(1)

        return None

    @staticmethod
    def _format_publish_date(value: str) -> Optional[str]:
        if not value:
            return None
        try:
            dt = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return value[:10] if len(value) >= 10 else value

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
                error_message="无法从查询中提取股票代码",
            )

        try:
            import akshare as ak
        except ImportError:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="AkShare 未安装",
            )

        try:
            df = ak.stock_news_em(symbol=symbol)
        except Exception as e:
            logger.warning("[AkShare] stock_news_em(%s) 失败: %s", symbol, e)
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=f"东方财富新闻请求失败: {e}",
            )

        if df is None or df.empty:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=True,
            )

        results: List[SearchResult] = []
        seen_urls: set = set()
        for _, row in df.iterrows():
            url = str(row.get("新闻链接", "") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            title = str(row.get("新闻标题", "") or "").strip()
            snippet = str(row.get("新闻内容", "") or "").strip()[:500]
            source = str(row.get("文章来源", "") or "").strip()
            published = self._format_publish_date(
                str(row.get("发布时间", "") or "")
            )

            results.append(
                SearchResult(
                    title=title or "无标题",
                    snippet=snippet,
                    url=url,
                    source=source or "东方财富",
                    published_date=published,
                )
            )
            if len(results) >= max_results:
                break

        logger.info(
            "[AkShare] 东方财富新闻完成, symbol=%s, results=%s",
            symbol,
            len(results),
        )
        return SearchResponse(
            query=query, results=results, provider=self.name, success=True
        )
