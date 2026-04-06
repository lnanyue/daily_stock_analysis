# -*- coding: utf-8 -*-
"""Brave Search 搜索引擎 Provider。"""

import logging
from datetime import datetime
from typing import List

import requests

from ..types import SearchResult, SearchResponse
from ..base_provider import BaseSearchProvider

logger = logging.getLogger(__name__)


class BraveSearchProvider(BaseSearchProvider):
    """
    Brave Search 搜索引擎

    特点：
    - 隐私优先的独立搜索引擎
    - 索引超过300亿页面
    - 免费层可用
    - 支持时间范围过滤

    文档：https://brave.com/search/api/
    """

    API_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "Brave")

    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        try:
            headers = {
                'X-Subscription-Token': api_key,
                'Accept': 'application/json'
            }

            if days <= 1:
                freshness = "pd"
            elif days <= 7:
                freshness = "pw"
            elif days <= 30:
                freshness = "pm"
            else:
                freshness = "py"

            params = {
                "q": query,
                "count": min(max_results, 20),
                "freshness": freshness,
                "search_lang": "en",
                "country": "US",
                "safesearch": "moderate"
            }

            response = requests.get(
                self.API_ENDPOINT,
                headers=headers,
                params=params,
                timeout=10
            )

            if response.status_code != 200:
                error_msg = self._parse_error(response)
                logger.warning("[Brave] 搜索失败: %s", error_msg)
                return SearchResponse(
                    query=query, results=[], provider=self.name,
                    success=False, error_message=error_msg
                )

            try:
                data = response.json()
            except ValueError as e:
                error_msg = f"响应JSON解析失败: {str(e)}"
                logger.error("[Brave] %s", error_msg)
                return SearchResponse(
                    query=query, results=[], provider=self.name,
                    success=False, error_message=error_msg
                )

            logger.info("[Brave] 搜索完成，query='%s'", query)
            logger.debug("[Brave] 原始响应: %s", data)

            results = []
            web_data = data.get('web', {})
            web_results = web_data.get('results', [])

            for item in web_results[:max_results]:
                published_date = None
                age = item.get('age') or item.get('page_age')
                if age:
                    try:
                        dt = datetime.fromisoformat(age.replace('Z', '+00:00'))
                        published_date = dt.strftime('%Y-%m-%d')
                    except (ValueError, AttributeError):
                        published_date = age

                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=item.get('description', '')[:500],
                    url=item.get('url', ''),
                    source=self._extract_domain(item.get('url', '')),
                    published_date=published_date
                ))

            logger.info("[Brave] 成功解析 %s 条结果", len(results))

            return SearchResponse(
                query=query, results=results, provider=self.name, success=True
            )

        except requests.exceptions.Timeout:
            error_msg = "请求超时"
            logger.error("[Brave] %s", error_msg)
            return SearchResponse(
                query=query, results=[], provider=self.name,
                success=False, error_message=error_msg
            )
        except requests.exceptions.RequestException as e:
            error_msg = f"网络请求失败: {str(e)}"
            logger.error("[Brave] %s", error_msg)
            return SearchResponse(
                query=query, results=[], provider=self.name,
                success=False, error_message=error_msg
            )
        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
            logger.error("[Brave] %s", error_msg)
            return SearchResponse(
                query=query, results=[], provider=self.name,
                success=False, error_message=error_msg
            )

    def _parse_error(self, response) -> str:
        try:
            if response.headers.get('content-type', '').startswith('application/json'):
                error_data = response.json()
                if 'message' in error_data:
                    return error_data['message']
                if 'error' in error_data:
                    return error_data['error']
                return str(error_data)
            return response.text[:200]
        except Exception:
            return f"HTTP {response.status_code}: {response.text[:200]}"
