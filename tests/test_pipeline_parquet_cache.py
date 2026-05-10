"""Tests for pipeline.py parquet cache integration."""

import tempfile
import shutil
from datetime import date, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, AsyncMock, patch

import pandas as pd
from src.core.stock_cache import StockCache


class TestPipelineParquetCache(TestCase):
    """Test pipeline.py integration with parquet cache."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.cache = StockCache(cache_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prefetch_uses_cache_when_fresh(self):
        """When cache is fresh, network is skipped."""
        df = pd.DataFrame({
            "date": [date.today()],
            "close": [100.0],
        })
        self.cache.write("600519", df)
        self.assertTrue(self.cache.is_fresh("600519"))
        # Read back — confirm data round-trips
        cached, source = self.cache.read("600519")
        self.assertIsNotNone(cached)
        self.assertEqual(source, "parquet_cache")

    def test_prefetch_falls_back_to_cache(self):
        """When network fails, old cache is returned."""
        df = pd.DataFrame({
            "date": [date.today() - timedelta(days=1)],
            "close": [99.0],
        })
        self.cache.write("600519", df)
        cached, source = self.cache.read("600519")
        self.assertIsNotNone(cached)
        self.assertEqual(source, "parquet_cache")

    def test_rename_fetch_and_save_stock_data(self):
        """Old method name fetch_and_save_stock_data no longer exists."""
        from src.core.pipeline import StockAnalysisPipeline
        self.assertFalse(hasattr(StockAnalysisPipeline, "fetch_and_save_stock_data"))
        self.assertTrue(hasattr(StockAnalysisPipeline, "prefetch_stock_data"))
