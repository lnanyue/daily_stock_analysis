# -*- coding: utf-8 -*-
"""Shared async HTTP client with connection pooling and SOCKS proxy support."""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx

from src.config import get_config

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_proxy: str | None = None


def _resolve_proxy() -> str | None:
    config = get_config()
    use_proxy = getattr(config, "use_proxy", False)
    if not use_proxy:
        return None
    host = getattr(config, "proxy_host", "127.0.0.1")
    port = getattr(config, "proxy_port", "10809")
    return f"socks5://{host}:{port}"


async def get_async_client(**kwargs) -> httpx.AsyncClient:
    """
    Get (or create) a shared httpx.AsyncClient.

    Connection pooling is shared across all callers, eliminating the
    per-request connect overhead from the old `with httpx.Client()` pattern.
    """
    global _client, _proxy

    proxy = _resolve_proxy()
    recreated = proxy != _proxy

    if recreated or _client is None or _client.is_closed:
        if _client and not _client.is_closed:
            await _client.aclose()
        _proxy = proxy
        _client = httpx.AsyncClient(
            proxy=proxy,
            timeout=kwargs.pop("timeout", httpx.Timeout(30.0, connect=10.0)),
            http2=False,
            **kwargs,
        )
    return _client


@asynccontextmanager
async def managed_async_client(**kwargs) -> AsyncGenerator[httpx.AsyncClient, None]:
    """
    Context manager version for callers that want guaranteed lifecycle management.
    """
    client = await get_async_client(**kwargs)
    try:
        yield client
    finally:
        _client = None


async def close_async_client() -> None:
    """Explicitly close the shared client (for shutdown)."""
    global _client, _proxy
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
    _proxy = None
