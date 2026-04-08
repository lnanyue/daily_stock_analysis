# -*- coding: utf-8 -*-
"""SerpAPI 搜索引擎 Provider。"""

import logging
from typing import List, Dict, Any

from ..types import SearchResult, SearchResponse
from ..base_provider import BaseSearchProvider
from ..http_utils import fetch_url_content, fetch_url_content_async

logger = logging.getLogger(__name__)


class SerpAPISearchProvider(BaseSearchProvider):
    """
    SerpAPI 搜索引擎
    
    特点：
    - 支持 Google、Bing、百度等多种搜索引擎
    - 免费版每月 100 次请求
    - 返回真实的搜索结果
    
    文档：https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis
    """
    
    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "SerpAPI")
    
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行 SerpAPI 搜索 (同步回退)"""
        # 为了简洁且保持逻辑一致，我们重构异步版本并在同步版本中通过 asyncio.run 调用（或保持原有库逻辑）
        # 这里我们直接实现异步版，因为这是性能关键点。
        import asyncio
        try:
            return asyncio.run(self._do_search_async(query, api_key, max_results, days))
        except RuntimeError:
            # 如果已经在事件循环中运行，则直接抛出或尝试其他方式
            # 实际上在 pipeline.py 中我们将直接调用 search_async
            raise

    async def _do_search_async(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行异步 SerpAPI 搜索"""
        from src.utils.async_http import get_global_client
        
        try:
            # 确定时间范围参数 tbs
            tbs = "qdr:w"
            if days <= 1:
                tbs = "qdr:d"
            elif days <= 7:
                tbs = "qdr:w"
            elif days <= 30:
                tbs = "qdr:m"
            else:
                tbs = "qdr:y"

            params = {
                "engine": "google",
                "q": query,
                "api_key": api_key,
                "google_domain": "google.com.hk",
                "hl": "zh-cn",
                "gl": "cn",
                "tbs": tbs,
                "num": max_results,
                "output": "json"
            }
            
            # 直接调用 REST API 避免官方 SDK 的同步阻塞
            client = await get_global_client()
            response_raw = await client.get("https://serpapi.com/search", params=params, timeout=30)
            
            if response_raw.status_code != 200:
                return SearchResponse(
                    query=query, results=[], provider=self.name, success=False,
                    error_message=f"SerpAPI HTTP {response_raw.status_code}: {response_raw.text}"
                )
                
            response = response_raw.json()
            results = []
            
            # 1. Knowledge Graph (保持原有逻辑)
            kg = response.get('knowledge_graph', {})
            if kg:
                title = kg.get('title', '知识图谱')
                desc = kg.get('description', '')
                details = []
                for key in ['type', 'founded', 'headquarters', 'employees', 'ceo']:
                    val = kg.get(key)
                    if val: details.append(f"{key}: {val}")
                snippet = f"{desc}\n" + " | ".join(details) if details else desc
                results.append(SearchResult(
                    title=f"[知识图谱] {title}", snippet=snippet,
                    url=kg.get('source', {}).get('link', ''), source="Google Knowledge Graph"
                ))
                
            # 2. Answer Box (保持原有逻辑)
            ab = response.get('answer_box', {})
            if ab:
                ab_title = ab.get('title', '精选回答')
                ab_snippet = ""
                if ab.get('type') == 'finance_results':
                    stock = ab.get('stock', ''); price = ab.get('price', ''); currency = ab.get('currency', '')
                    movement = ab.get('price_movement', {}); mv_val = movement.get('percentage', 0); mv_dir = movement.get('movement', '')
                    ab_title = f"[行情卡片] {stock}"; ab_snippet = f"价格: {price} {currency}\n涨跌: {mv_dir} {mv_val}%"
                    if 'table' in ab:
                        table_data = [f"{row['name']}: {row['value']}" for row in ab['table'] if 'name' in row and 'value' in row]
                        if table_data: ab_snippet += "\n" + "; ".join(table_data)
                elif 'snippet' in ab:
                    ab_snippet = ab.get('snippet', '')
                    list_items = ab.get('list', [])
                    if list_items: ab_snippet += "\n" + "\n".join([f"- {item}" for item in list_items])
                elif 'answer' in ab:
                    ab_snippet = ab.get('answer', '')
                if ab_snippet:
                    results.append(SearchResult(
                        title=f"[精选回答] {ab_title}", snippet=ab_snippet,
                        url=ab.get('link', '') or ab.get('displayed_link', ''), source="Google Answer Box"
                    ))

            # 3. Related Questions
            rqs = response.get('related_questions', [])
            for rq in rqs[:3]:
                question = rq.get('question', ''); snippet = rq.get('snippet', ''); link = rq.get('link', '')
                if question and snippet:
                     results.append(SearchResult(title=f"[相关问题] {question}", snippet=snippet, url=link, source="Google Related Questions"))

            # 4. Organic Results + Asynchronous Content Fetching
            organic_results = response.get('organic_results', [])
            
            # 为有机搜索结果准备异步抓取任务
            fetch_tasks = []
            valid_organic_items = []
            for item in organic_results[:max_results]:
                link = item.get('link', '')
                if link:
                    valid_organic_items.append(item)
                    fetch_tasks.append(fetch_url_content_async(link, timeout=5))
            
            # 并发执行网页内容抓取
            if fetch_tasks:
                fetched_contents = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for item, fetched_content in zip(valid_organic_items, fetched_contents):
                    link = item.get('link', '')
                    snippet = item.get('snippet', '')
                    if isinstance(fetched_content, str) and fetched_content:
                        content_preview = fetched_content[:500] + "..." if len(fetched_content) > 500 else fetched_content
                        snippet = f"{snippet}\n\n【网页详情】\n{content_preview}"
                    
                    results.append(SearchResult(
                        title=item.get('title', ''),
                        snippet=snippet[:1200], # 增加长度限制到 1200
                        url=link,
                        source=item.get('source', self._extract_domain(link)),
                        published_date=item.get('date'),
                    ))
            
            return SearchResponse(query=query, results=results, provider=self.name, success=True)
            
        except Exception as e:
            logger.error(f"[SerpAPI Async] Exception: {e}")
            return SearchResponse(query=query, results=[], provider=self.name, success=False, error_message=str(e))
            
        except Exception as e:
            error_msg = str(e)
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
