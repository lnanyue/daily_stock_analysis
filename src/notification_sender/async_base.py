# -*- coding: utf-8 -*-
"""
Bridging notification senders to the global AsyncHttpClientManager.
"""
import asyncio
import logging
from src.utils.async_http import get_global_client

logger = logging.getLogger(__name__)

async def get_sender_http_client():
    """Shared client for all notification senders."""
    return await get_global_client()

async def send_with_retry(send_func, channel_name, max_retries=3, **kwargs):
    """Generic async retry wrapper for send functions."""
    attempt = 1
    while attempt <= max_retries:
        try:
            return await send_func(**kwargs)
        except Exception as e:
            if attempt < max_retries:
                delay = 2 ** attempt
                logger.warning(f"{channel_name} 发送失败，{delay}s 后重试 ({attempt}/{max_retries}): {e}")
                await asyncio.sleep(delay)
            else:
                logger.error(f"{channel_name} 发送失败，已重试 {max_retries} 次: {e}")
        attempt += 1
    return False
