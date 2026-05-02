# -*- coding: utf-8 -*-
import unittest
import pandas as pd
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

from data_provider.manager import DataFetcherManager


class _MockFetcher:
    def __init__(self, name: str, priority: int, daily_result=None, quote_result=None):
        self.name = name
        self.priority = priority
        self.daily_result = daily_result
        self.quote_result = quote_result

    async def get_daily_data_async(self, *args, **kwargs):
        return self.daily_result

    def get_realtime_quote(self, *args, **kwargs):
        return self.quote_result


def _empty_df():
    return pd.DataFrame()


def _valid_df():
    return pd.DataFrame([{"date": "2026-01-01", "open": 100, "close": 105}], columns=["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"])


class TestManagerMarketRouting(unittest.IsolatedAsyncioTestCase):
    """Test market-specific data source routing in DataFetcherManager."""

    @patch("src.config.get_config")
    async def test_us_stock_routes_longbridge_then_yfinance(self, mock_cfg):
        """US stocks try Longbridge first, then Yfinance."""
        mock_cfg.return_value = SimpleNamespace(enable_realtime_quote=True)
        longbridge = _MockFetcher("LongbridgeFetcher", 5, daily_result=_valid_df())
        yfinance = _MockFetcher("YfinanceFetcher", 4, daily_result=None)
        manager = DataFetcherManager(fetchers=[longbridge, yfinance])
        df, src = await manager.get_daily_data("AAPL", days=30)
        self.assertIsNotNone(df)
        self.assertEqual(src, "LongbridgeFetcher")

    @patch("src.config.get_config")
    async def test_us_stock_falls_back_to_yfinance(self, mock_cfg):
        """US stocks fall back to Yfinance when Longbridge returns empty."""
        mock_cfg.return_value = SimpleNamespace(enable_realtime_quote=True)
        longbridge = _MockFetcher("LongbridgeFetcher", 5, daily_result=_empty_df())
        yfinance = _MockFetcher("YfinanceFetcher", 4, daily_result=_valid_df())
        manager = DataFetcherManager(fetchers=[longbridge, yfinance])
        df, src = await manager.get_daily_data("AAPL", days=30)
        self.assertIsNotNone(df)
        self.assertEqual(src, "YfinanceFetcher")

    @patch("src.config.get_config")
    async def test_hk_stock_routes_longbridge_then_akshare(self, mock_cfg):
        """HK stocks try Longbridge first, then Akshare."""
        mock_cfg.return_value = SimpleNamespace(enable_realtime_quote=True)
        longbridge = _MockFetcher("LongbridgeFetcher", 5, daily_result=_valid_df())
        akshare = _MockFetcher("AkshareFetcher", 1, daily_result=None)
        manager = DataFetcherManager(fetchers=[longbridge, akshare])
        df, src = await manager.get_daily_data("HK00700", days=30)
        self.assertIsNotNone(df)
        self.assertEqual(src, "LongbridgeFetcher")

    @patch("src.config.get_config")
    async def test_hk_stock_falls_back_to_akshare(self, mock_cfg):
        """HK stocks fall back to Akshare when Longbridge returns empty."""
        mock_cfg.return_value = SimpleNamespace(enable_realtime_quote=True)
        longbridge = _MockFetcher("LongbridgeFetcher", 5, daily_result=_empty_df())
        akshare = _MockFetcher("AkshareFetcher", 1, daily_result=_valid_df())
        manager = DataFetcherManager(fetchers=[longbridge, akshare])
        df, src = await manager.get_daily_data("HK00700", days=30)
        self.assertIsNotNone(df)
        self.assertEqual(src, "AkshareFetcher")

    @patch("src.config.get_config")
    async def test_cn_stock_uses_generic_loop(self, mock_cfg):
        """A-shares use the generic priority loop (not market-specific path)."""
        mock_cfg.return_value = SimpleNamespace(enable_realtime_quote=True)
        akshare = _MockFetcher("AkshareFetcher", 1, daily_result=_valid_df())
        efinance = _MockFetcher("EfinanceFetcher", 0, daily_result=None)
        manager = DataFetcherManager(fetchers=[efinance, akshare])
        df, src = await manager.get_daily_data("600519", days=30)
        self.assertIsNotNone(df)
        self.assertEqual(src, "AkshareFetcher")

    @patch("src.config.get_config")
    async def test_us_falls_to_generic_loop_after_chain_exhausted(self, mock_cfg):
        """US stocks fall to generic loop after Longbridge+Yfinance both fail."""
        mock_cfg.return_value = SimpleNamespace(enable_realtime_quote=True)
        longbridge = _MockFetcher("LongbridgeFetcher", 5, daily_result=_empty_df())
        yfinance = _MockFetcher("YfinanceFetcher", 4, daily_result=_empty_df())
        akshare = _MockFetcher("AkshareFetcher", 1, daily_result=_valid_df())
        manager = DataFetcherManager(fetchers=[akshare, longbridge, yfinance])
        df, src = await manager.get_daily_data("AAPL", days=30)
        self.assertIsNotNone(df)
        self.assertEqual(src, "AkshareFetcher")

    @patch("src.config.get_config")
    async def test_hk_realtime_tries_longbridge_first(self, mock_cfg):
        """HK realtime quotes try Longbridge first, then Akshare."""
        mock_cfg.return_value = SimpleNamespace(enable_realtime_quote=True)
        quote_result = SimpleNamespace(code="HK00700", price=100.0)
        longbridge = _MockFetcher("LongbridgeFetcher", 5, quote_result=quote_result)
        akshare = _MockFetcher("AkshareFetcher", 1, quote_result=None)
        manager = DataFetcherManager(fetchers=[longbridge, akshare])
        result = await manager.get_realtime_quote("HK00700")
        self.assertIsNotNone(result)
        self.assertEqual(result.code, "HK00700")


if __name__ == "__main__":
    unittest.main()
