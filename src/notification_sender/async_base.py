# -*- coding: utf-8 -*-
"""
Bridging notification senders to the global AsyncHttpClientManager.

Provides:
- get_sender_http_client(): shared httpx.AsyncClient
- send_with_retry(): classifies errors, retries only transient ones
"""
import asyncio
import logging

import httpx

from src.exceptions import RetryableError, NonRetryableError
from src.utils.async_http import get_global_client

logger = logging.getLogger(__name__)

async def get_sender_http_client():
    """Shared client for all notification senders."""
    return await get_global_client()


def _classify_http_error(exc: Exception) -> type:
    """Return RetryableError or NonRetryableError based on exception type/status."""
    # Already classified by the sender
    if isinstance(exc, (RetryableError, NonRetryableError)):
        return type(exc)

    # Network-layer errors are usually transient
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)):
        return RetryableError

    # Server responded — inspect status code
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return RetryableError  # rate-limit → back off and retry
        if code < 500:
            return NonRetryableError  # 4xx (except 429) → client/config error, won't fix itself
        # 5xx → server error, may be transient
        return RetryableError

    # Unknown exception → treat as retryable so we don't silently swallow new bugs
    return RetryableError


async def send_with_retry(send_func, channel_name, max_retries=3, **kwargs):
    """
    Send with retry, classifying errors into retryable vs non-retryable.

    - RetryableError / transient network errors → exponential backoff + retry
    - NonRetryableError (401, 403, bad config, etc.) → fail immediately
    - Unexpected exceptions → logged with full info, treated as retryable
    """
    import traceback

    attempt = 1
    while attempt <= max_retries:
        try:
            return await send_func(**kwargs)
        except (RetryableError, NonRetryableError) as e:
            if isinstance(e, NonRetryableError):
                logger.error(f"{channel_name} 不可重试的错误: {e}")
                return False
            # RetryableError
            if attempt < max_retries:
                delay = 2 ** attempt
                logger.warning(f"{channel_name} 发送失败（可重试），{delay}s 后重试 ({attempt}/{max_retries}): {e}")
                await asyncio.sleep(delay)
            else:
                logger.error(f"{channel_name} 发送失败，已重试 {max_retries} 次: {e}")
        except Exception as e:
            error_type = _classify_http_error(e)
            if error_type is NonRetryableError:
                logger.error(f"{channel_name} 不可重试的错误: {e}")
                return False
            # Retryable
            if attempt < max_retries:
                delay = 2 ** attempt
                logger.warning(f"{channel_name} 发送失败（网络异常），{delay}s 后重试 ({attempt}/{max_retries}): {e}")
                await asyncio.sleep(delay)
            else:
                logger.error(f"{channel_name} 发送失败，已重试 {max_retries} 次: {e}\n{traceback.format_exc()}")
        attempt += 1
    return False
