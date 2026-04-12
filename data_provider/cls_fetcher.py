# -*- coding: utf-8 -*-
"""
财联社 (cls.cn) 电报抓取器 - 实时 A 股情报源
"""

import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from ._async_client import get_async_client
from .utils import summarize_exception

logger = logging.getLogger(__name__)

class ClsTelegramFetcher:
    """
    专门负责获取财联社实时电报和个股快讯
    """
    
    BASE_URL = "https://www.cls.cn/nodeapi/telegraphList"
    
    async def fetch_latest_telegrams(self, last_time: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取最新的电报流
        
        Args:
            last_time: 起始时间戳（秒），用于翻页或增量获取
        """
        params = {
            "refresh_type": 1,
            "rn": 20,
            "has_ast": 0
        }
        if last_time:
            params["last_time"] = last_time
            
        try:
            client = await get_async_client()
            # 财联社 API 通常需要特定的 User-Agent
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.cls.cn/telegraph"
            }
            
            response = await client.get(self.BASE_URL, params=params, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.error(f"[财联社] API 请求失败: {response.status_code}")
                return []
                
            data = response.json()
            articles = data.get("data", {}).get("roll_data", [])
            
            results = []
            for art in articles:
                results.append({
                    "id": art.get("id"),
                    "content": art.get("content"),
                    "title": art.get("title"),
                    "ctime": art.get("ctime"), # 时间戳
                    "date": datetime.fromtimestamp(art.get("ctime")).strftime("%Y-%m-%d %H:%M:%S") if art.get("ctime") else None,
                    "subjects": [s.get("name") for s in art.get("subjects", [])], # 相关题材
                    "stocks": [s.get("name") for s in art.get("stocks", [])],     # 相关股票
                })
            
            return results
        except Exception as e:
            error_type, reason = summarize_exception(e)
            logger.warning(f"[财联社] 抓取异常: {error_type} - {reason}")
            return []

    async def get_stock_news(self, stock_name: str, stock_code: str, days: int = 1) -> List[Dict[str, Any]]:
        """
        从电报流中筛选特定股票的新闻
        注意：这通常作为 SearchService 的有力补充，因为它是 7x24 实时且带股票标签的
        """
        # 由于财联社没有公开的按股票代码搜索的 API，我们通过关键词在最近的电报流中筛选
        # 实际生产中可以建立本地索引库
        all_news = await self.fetch_latest_telegrams()
        
        filtered = []
        # 同时匹配名称和代码
        for news in all_news:
            content = news.get("content", "")
            if stock_name in content or stock_code in content:
                filtered.append(news)
            elif stock_name in str(news.get("stocks", [])):
                filtered.append(news)
                
        return filtered
