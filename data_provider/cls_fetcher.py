# -*- coding: utf-8 -*-
"""
财联社 (cls.cn) 电报抓取器 - 实时 A 股情报源
"""

import logging
import time
import asyncio
import random
from typing import List, Dict, Any, Optional
from datetime import datetime

from ._async_client import get_async_client
from .utils import summarize_exception, pick_random_user_agent
from src.utils.async_http import async_retry

logger = logging.getLogger(__name__)

class ClsTelegramFetcher:
    """
    专门负责获取财联社实时电报和个股快讯
    
    加固点：
    1. 随机 User-Agent 池
    2. 随机抖动延迟 (Jitter)
    3. 增量抓取支持
    4. 自动重试机制
    """
    
    BASE_URL = "https://www.cls.cn/nodeapi/telegraphList"
    
    @async_retry(max_attempts=3, min_wait=2.0)
    async def fetch_latest_telegrams(self, last_time: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取最新的电报流 (带隐身保护与自动重试)
        """
        # 反封锁 1: 随机微小延迟 (0.5 - 1.5s)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        params = {
            "refresh_type": 1,
            "rn": 20,
            "has_ast": 0
        }
        if last_time:
            params["last_time"] = last_time
            
        try:
            client = await get_async_client()
            
            # 反封锁 2: 随机 User-Agent
            headers = {
                "User-Agent": pick_random_user_agent(),
                "Referer": "https://www.cls.cn/telegraph",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            
            response = await client.get(self.BASE_URL, params=params, headers=headers, timeout=10)
            if response.status_code == 403:
                logger.error("[财联社] 被封锁 (403 Forbidden)，请考虑降低频率或更换代理")
                return []
            elif response.status_code != 200:
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
