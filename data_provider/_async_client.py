# -*- coding: utf-8 -*-
"""
Bridging data provider to the global AsyncHttpClientManager.
"""
from src.utils.async_http import get_global_client, managed_client, AsyncHttpClientManager

async def get_async_client():
    """Compatibility wrapper for data providers."""
    return await get_global_client()

async def close_async_client():
    """Compatibility wrapper for shutdown."""
    await AsyncHttpClientManager().close()
