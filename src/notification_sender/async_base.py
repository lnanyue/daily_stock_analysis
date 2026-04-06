# -*- coding: utf-8 -*-
"""
Async retry and HTTP client utilities for notification senders.

Provides:
- Shared httpx.AsyncClient with proxy/timeout config
- send_with_retry() for async-capable send functions
"""
import asyncio
import logging
from typing import Any

import httpx

from src.config import get_config
from src.notification import NOTIFICATION_DEFAULT_TIMEOUT_SEC, NOTIFICATION_DEFAULT_MAX_RETRIES

logger = logging.getLogger(__name__)

# Module-level shared client to avoid per-call connect overhead
_async_http_client: httpx.AsyncClient | None = None


async def get_sender_http_client() -> httpx.AsyncClient:
    """Get a shared httpx.AsyncClient for notification HTTP requests."""
    global _async_http_client
    if _async_http_client is None or _async_http_client.is_closed:
        config = get_config()
        proxy = None
        if getattr(config, "use_proxy", False):
            host = getattr(config, "proxy_host", "127.0.0.1")
            port = getattr(config, "proxy_port", "10809")
            proxy = f"socks5://{host}:{port}"
        _async_http_client = httpx.AsyncClient(
            proxy=proxy,
            verify=getattr(config, "webhook_verify_ssl", True),
            timeout=httpx.Timeout(
                getattr(config, "notification_timeout_sec", NOTIFICATION_DEFAULT_TIMEOUT_SEC),
                connect=10.0,
            ),
        )
    return _async_http_client


async def close_sender_http_client() -> None:
    """Close the shared HTTP client."""
    global _async_http_client
    if _async_http_client and not _async_http_client.is_closed:
        await _async_http_client.aclose()
    _async_http_client = None


async def send_with_retry(
    send_fn,
    channel_name: str,
    max_retries: int = NOTIFICATION_DEFAULT_MAX_RETRIES,
) -> bool:
    """Send with exponential backoff retry, async version."""
    attempt = 0
    while True:
        try:
            result = await send_fn()
            if result:
                return True
        except Exception as e:
            if attempt < max_retries:
                delay = min(0.5 * (2 ** attempt), 30.0)
                logger.warning(
                    f"{channel_name} 发送失败，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries}): {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"{channel_name} 发送失败，已重试 {max_retries} 次: {e}")
        attempt += 1
        if attempt > max_retries:
            return False
