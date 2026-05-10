# -*- coding: utf-8 -*-
"""Regression tests for pipeline data-fetch error handling."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from src.core.pipeline import StockAnalysisPipeline
from src.core.stock_cache import StockCache


class PipelineFetchErrorTestCase(unittest.TestCase):
    """`prefetch_stock_data` should preserve the original exception."""

    def test_prefetch_handles_stock_name_lookup_failure(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.fetcher_manager = MagicMock()
        pipeline.db = MagicMock()
        pipeline.cache = StockCache(cache_dir=Path(tempfile.mkdtemp()))
        pipeline.fetcher_manager.get_stock_name = AsyncMock(side_effect=RuntimeError("name lookup failed"))

        success, error = asyncio.run(StockAnalysisPipeline.prefetch_stock_data(pipeline, "600519"))

        self.assertFalse(success)
        self.assertIn("name lookup failed", error or "")


if __name__ == "__main__":
    unittest.main()
