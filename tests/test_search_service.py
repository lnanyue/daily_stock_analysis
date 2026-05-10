# -*- coding: utf-8 -*-
"""Tests for search degradation logging when providers return empty."""

import asyncio
import sys
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from src.search.types import SearchResponse, SearchResult

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search.service import SearchService


class TestSearchDegradationLogging(TestCase):
    """搜索返回空结果时记录 warning。"""

    def setUp(self):
        # Construct SearchService with no providers (all defaults)
        self.service = SearchService()

    @patch("src.search.service.logger")
    def test_stock_news_degradation_logged(self, mock_logger):
        """No providers should trigger degradation warning for stock news async."""
        asyncio.run(self.service.search_stock_news_async("000000", "测试股票"))
        mock_logger.warning.assert_any_call("[%s] 所有搜索 provider 均不可用或搜索失败", "000000")

    @patch("src.search.service.logger")
    def test_macro_news_degradation_logged(self, mock_logger):
        """No providers should trigger degradation warning for macro news async."""
        asyncio.run(self.service.search_macro_news_async("000000", "测试股票"))
        mock_logger.warning.assert_any_call("[%s] 宏观新闻搜索 provider 均不可用或搜索失败", "000000")

    def test_macro_news_uses_non_tavily_provider(self):
        """非 Tavily provider 也能服务宏观新闻。"""
        mock_provider = MagicMock()
        mock_provider.is_available = True
        mock_provider.name = "mock_provider"
        mock_provider.search_async = AsyncMock(return_value=SearchResponse(
            query="test", results=[
                SearchResult(
                    title="r1", url="http://x.com", snippet="c1",
                    source="mock", published_date="2026-05-10",
                ),
            ], provider="mock_provider", success=True,
        ))

        service = SearchService()
        service._providers = [mock_provider]

        result = asyncio.run(service.search_macro_news_async("000000", "测试", max_results=3))
        self.assertTrue(result.success)
        self.assertGreater(len(result.results), 0)
