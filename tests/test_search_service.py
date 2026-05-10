# -*- coding: utf-8 -*-
"""Tests for search degradation logging when providers return empty."""

import asyncio
import sys
from unittest import TestCase
from unittest.mock import MagicMock, patch

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
