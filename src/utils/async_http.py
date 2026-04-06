# -*- coding: utf-8 -*-
"""
Shared Async HTTP Client Manager with connection pooling and proxy support.
"""
import logging
import httpx
import asyncio
from typing import Optional, Any
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class AsyncHttpClientManager:
    _instance: Optional['AsyncHttpClientManager'] = None
    _client: Optional[httpx.AsyncClient] = None
    _lock = asyncio.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(AsyncHttpClientManager, cls).__new__(cls)
        return cls._instance

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create the shared AsyncClient instance."""
        async with self._lock:
            if self._client is None or self._client.is_closed:
                from src.config import get_config
                config = get_config()
                
                # Proxy configuration
                proxy = None
                if getattr(config, 'use_proxy', False):
                    proxy_host = getattr(config, 'proxy_host', '127.0.0.1')
                    proxy_port = getattr(config, 'proxy_port', '10809')
                    proxy = f"http://{proxy_host}:{proxy_port}"
                
                limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
                timeout = httpx.Timeout(getattr(config, 'http_timeout_sec', 30.0), connect=10.0)
                
                self._client = httpx.AsyncClient(
                    proxy=proxy,
                    limits=limits,
                    timeout=timeout,
                    follow_redirects=True,
                    verify=getattr(config, 'webhook_verify_ssl', True)
                )
                logger.info("Shared AsyncHttpClient created (proxy=%s)", 'enabled' if proxy else 'disabled')
            
            return self._client

    async def close(self):
        """Close the shared client instance."""
        async with self._lock:
            if self._client and not self._client.is_closed:
                await self._client.aclose()
                logger.info("Shared AsyncHttpClient closed")
            self._client = None

# Global helper
async def get_global_client() -> httpx.AsyncClient:
    return await AsyncHttpClientManager().get_client()

@asynccontextmanager
async def managed_client():
    client = await get_global_client()
    try:
        yield client
    finally:
        pass # Client is shared, don't close here
