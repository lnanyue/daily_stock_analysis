# -*- coding: utf-8 -*-
"""SerpAPI 搜索引擎 Provider。"""

import logging
from typing import List, Dict, Any

from ..types import SearchResult, SearchResponse
from ..base_provider import BaseSearchProvider
from ..http_utils import fetch_url_content

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
        """执行 SerpAPI 搜索"""
        try:
            from serpapi import GoogleSearch
        except ImportError:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="google-search-results 未安装，请运行: pip install google-search-results"
            )
        
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
                "num": max_results
            }
            
            search = GoogleSearch(params)
            response = search.get_dict()
            
            logger.debug("[SerpAPI] 原始响应 keys: %s", response.keys())
            
            results = []
            
            # 1. Knowledge Graph
            kg = response.get('knowledge_graph', {})
            if kg:
                title = kg.get('title', '知识图谱')
                desc = kg.get('description', '')
                
                details = []
                for key in ['type', 'founded', 'headquarters', 'employees', 'ceo']:
                    val = kg.get(key)
                    if val:
                        details.append(f"{key}: {val}")
                        
                snippet = f"{desc}\n" + " | ".join(details) if details else desc
                
                results.append(SearchResult(
                    title=f"[知识图谱] {title}",
                    snippet=snippet,
                    url=kg.get('source', {}).get('link', ''),
                    source="Google Knowledge Graph"
                ))
                
            # 2. Answer Box
            ab = response.get('answer_box', {})
            if ab:
                ab_title = ab.get('title', '精选回答')
                ab_snippet = ""
                
                if ab.get('type') == 'finance_results':
                    stock = ab.get('stock', '')
                    price = ab.get('price', '')
                    currency = ab.get('currency', '')
                    movement = ab.get('price_movement', {})
                    mv_val = movement.get('percentage', 0)
                    mv_dir = movement.get('movement', '')
                    
                    ab_title = f"[行情卡片] {stock}"
                    ab_snippet = f"价格: {price} {currency}\n涨跌: {mv_dir} {mv_val}%"
                    
                    if 'table' in ab:
                        table_data = []
                        for row in ab['table']:
                            if 'name' in row and 'value' in row:
                                table_data.append(f"{row['name']}: {row['value']}")
                        if table_data:
                            ab_snippet += "\n" + "; ".join(table_data)
                            
                elif 'snippet' in ab:
                    ab_snippet = ab.get('snippet', '')
                    list_items = ab.get('list', [])
                    if list_items:
                        ab_snippet += "\n" + "\n".join([f"- {item}" for item in list_items])
                
                elif 'answer' in ab:
                    ab_snippet = ab.get('answer', '')
                    
                if ab_snippet:
                    results.append(SearchResult(
                        title=f"[精选回答] {ab_title}",
                        snippet=ab_snippet,
                        url=ab.get('link', '') or ab.get('displayed_link', ''),
                        source="Google Answer Box"
                    ))

            # 3. Related Questions
            rqs = response.get('related_questions', [])
            for rq in rqs[:3]:
                question = rq.get('question', '')
                snippet = rq.get('snippet', '')
                link = rq.get('link', '')
                
                if question and snippet:
                     results.append(SearchResult(
                        title=f"[相关问题] {question}",
                        snippet=snippet,
                        url=link,
                        source="Google Related Questions"
                     ))

            # 4. Organic Results
            organic_results = response.get('organic_results', [])

            for item in organic_results[:max_results]:
                link = item.get('link', '')
                snippet = item.get('snippet', '')

                content = ""
                if link:
                   try:
                       fetched_content = fetch_url_content(link, timeout=5)
                       if fetched_content:
                           content = fetched_content
                           if len(content) > 500:
                               snippet = f"{snippet}\n\n【网页详情】\n{content[:500]}..."
                           else:
                               snippet = f"{snippet}\n\n【网页详情】\n{content}"
                   except Exception as e:
                       logger.debug("[SerpAPI] Fetch content failed: %s", e)

                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=snippet[:1000],
                    url=link,
                    source=item.get('source', self._extract_domain(link)),
                    published_date=item.get('date'),
                ))

            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            error_msg = str(e)
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
