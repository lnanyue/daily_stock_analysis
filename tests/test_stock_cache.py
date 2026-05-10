import tempfile
import shutil
from datetime import date, timedelta
from pathlib import Path
from unittest import TestCase
import pandas as pd
from src.core.stock_cache import StockCache, find_close_for_date


class TestStockCache(TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.cache = StockCache(cache_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_df(self, days=5):
        return pd.DataFrame({
            "date": [date.today() - timedelta(days=i) for i in range(days)],
            "open": [100.0 - i for i in range(days)],
            "close": [101.0 - i for i in range(days)],
            "high": [102.0 - i for i in range(days)],
            "low": [99.0 - i for i in range(days)],
            "volume": [100000] * days,
            "amount": [10000000] * days,
            "pct_chg": [0.5] * days,
        })

    def test_write_and_read(self):
        df = self._make_df()
        self.cache.write("600519", df)
        cached, source = self.cache.read("600519")
        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), 5)
        self.assertAlmostEqual(cached.iloc[0]["close"], 101.0)

    def test_read_returns_none_when_no_cache(self):
        cached, source = self.cache.read("NONEXIST")
        self.assertIsNone(cached)
        self.assertEqual(source, "none")

    def test_is_fresh_returns_true_for_today_write(self):
        df = self._make_df()
        self.cache.write("600519", df)
        self.assertTrue(self.cache.is_fresh("600519"))

    def test_is_fresh_returns_false_when_no_cache(self):
        self.assertFalse(self.cache.is_fresh("NONEXIST"))

    def test_cache_dir_created_on_first_write(self):
        new_dir = self.temp_dir / "subdir"
        cache = StockCache(cache_dir=new_dir)
        df = self._make_df()
        cache.write("600519", df)
        self.assertTrue((new_dir / "600519.parquet").exists())

    def test_find_close_for_date_prior(self):
        df = pd.DataFrame({
            "date": [date.today() - timedelta(days=3), date.today() - timedelta(days=1)],
            "close": [95.0, 105.0],
        })
        self.cache.write("600519", df)
        # No exact match for 2 days ago -> nearest prior (3 days ago = 95.0)
        cached_df, _ = self.cache.read("600519")
        close = find_close_for_date(cached_df, date.today() - timedelta(days=2))
        self.assertAlmostEqual(close, 95.0)

    def test_find_close_for_date_exact(self):
        df = pd.DataFrame({
            "date": [date.today()],
            "close": [100.0],
        })
        close = find_close_for_date(df, date.today())
        self.assertAlmostEqual(close, 100.0)

    def test_read_returns_cached_data(self):
        """Read succeeds after write, regardless of freshness."""
        df = pd.DataFrame({
            "date": [date.today() - timedelta(days=1)],
            "close": [99.0],
        })
        self.cache.write("600519", df)
        cached, source = self.cache.read("600519")
        self.assertIsNotNone(cached)
        self.assertEqual(source, "parquet_cache")

    def test_is_fresh_returns_false_when_meta_missing(self):
        """Parquet exists but meta file deleted — is_fresh returns False."""
        import json as _json
        df = self._make_df()
        self.cache.write("600519", df)
        # Delete meta file
        from src.core.stock_cache import _meta_path
        _meta_path(self.temp_dir, "600519").unlink()
        self.assertFalse(self.cache.is_fresh("600519"))
        # Read still works (graceful degradation)
        cached, source = self.cache.read("600519")
        self.assertIsNotNone(cached)
        self.assertEqual(source, "parquet_cache")

    def test_is_fresh_returns_false_when_meta_stale(self):
        """Meta file with yesterday's date — is_fresh returns False."""
        import json as _json
        df = self._make_df()
        self.cache.write("600519", df)
        # Overwrite meta with yesterday's date
        from src.core.stock_cache import _meta_path
        stale_meta = {"fetch_date": (date.today() - timedelta(days=1)).isoformat(), "code": "600519", "rows": 5}
        _meta_path(self.temp_dir, "600519").write_text(_json.dumps(stale_meta))
        self.assertFalse(self.cache.is_fresh("600519"))

    def test_find_close_for_date_empty_df(self):
        """Empty DataFrame returns None."""
        df = pd.DataFrame({"date": [], "close": []})
        self.assertIsNone(find_close_for_date(df, date.today()))

    def test_find_close_for_date_all_future(self):
        """All dates after target — returns earliest available."""
        df = pd.DataFrame({
            "date": [date.today() + timedelta(days=1), date.today() + timedelta(days=2)],
            "close": [110.0, 120.0],
        })
        close = find_close_for_date(df, date.today())
        self.assertAlmostEqual(close, 110.0)

    def test_find_close_for_date_chinese_columns(self):
        """Works with Chinese column names."""
        df = pd.DataFrame({
            "date": [date.today() - timedelta(days=1)],
            "收盘": [99.0],
        })
        close = find_close_for_date(df, date.today())
        self.assertAlmostEqual(close, 99.0)

    def test_find_close_for_date_missing_both_close_columns(self):
        """Returns None when neither 'close' nor '收盘' exists."""
        df = pd.DataFrame({
            "date": [date.today()],
            "open": [100.0],
        })
        self.assertIsNone(find_close_for_date(df, date.today()))

    def test_write_normalizes_chinese_date_column(self):
        """'日期' column is renamed to 'date' on write."""
        df = pd.DataFrame({
            "日期": [date.today()],
            "close": [100.0],
        })
        self.cache.write("600519", df)
        cached, _ = self.cache.read("600519")
        self.assertIsNotNone(cached)
        self.assertIn("date", cached.columns)
        self.assertNotIn("日期", cached.columns)
