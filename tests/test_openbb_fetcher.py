# -*- coding: utf-8 -*-
"""Tests for the optional OpenBB market-data fetcher."""

import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

from data_provider.openbb_fetcher import OpenBBFetcher
from data_provider.realtime_types import RealtimeSource
from src.config import Config


class TestOpenBBFetcher(unittest.TestCase):
    def test_get_daily_data_normalizes_cn_history_rows(self) -> None:
        calls = []

        class _FakePrice:
            @staticmethod
            def historical(**kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(
                    results=[
                        types.SimpleNamespace(date=date(2026, 4, 28), open=10.0, high=10.8, low=9.9, close=10.5, volume=1000),
                        types.SimpleNamespace(date=date(2026, 4, 29), open=10.6, high=10.9, low=10.2, close=10.8, volume=1200),
                    ]
                )

        fake_openbb = types.ModuleType("openbb")
        fake_openbb.obb = types.SimpleNamespace(
            equity=types.SimpleNamespace(price=_FakePrice()),
            index=types.SimpleNamespace(price=types.SimpleNamespace(historical=lambda **_: None)),
        )

        fetcher = OpenBBFetcher(provider="yfinance")
        with patch.dict(sys.modules, {"openbb": fake_openbb}):
            df = fetcher.get_daily_data("600519", start_date="2026-04-28", end_date="2026-04-30")

        self.assertEqual(calls[0]["symbol"], "600519.SS")
        self.assertEqual(calls[0]["provider"], "yfinance")
        self.assertEqual(df["code"].iloc[0], "600519")
        self.assertIn("amount", df.columns)
        self.assertIn("pct_chg", df.columns)
        self.assertEqual(len(df), 2)

    def test_get_realtime_quote_maps_payload(self) -> None:
        calls = []

        class _FakePrice:
            @staticmethod
            def quote(**kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(
                    results=[
                        types.SimpleNamespace(
                            symbol="0700.HK",
                            name="腾讯控股",
                            last_price=500.0,
                            previous_close=480.0,
                            open=490.0,
                            high=505.0,
                            low=489.0,
                            volume=123456,
                            market_cap=4.5e12,
                            pe_ratio=22.1,
                            pb_ratio=3.4,
                        )
                    ]
                )

        fake_openbb = types.ModuleType("openbb")
        fake_openbb.obb = types.SimpleNamespace(
            equity=types.SimpleNamespace(price=_FakePrice()),
        )

        fetcher = OpenBBFetcher(provider="yfinance")
        with patch.dict(sys.modules, {"openbb": fake_openbb}):
            quote = fetcher.get_realtime_quote("HK00700")

        self.assertIsNotNone(quote)
        self.assertEqual(calls[0]["symbol"], "0700.HK")
        self.assertEqual(quote.code, "HK00700")
        self.assertEqual(quote.name, "腾讯控股")
        self.assertEqual(quote.price, 500.0)
        self.assertEqual(quote.pre_close, 480.0)
        self.assertAlmostEqual(quote.change_pct or 0.0, 4.1667, places=3)
        self.assertEqual(quote.source, RealtimeSource.OPENBB)


class ConfigEnvCompatibilityOpenBBFetcherTestCase(unittest.TestCase):
    def tearDown(self):
        Config.reset_instance()

    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    def test_load_from_env_reads_openbb_fetcher_config(
        self, _mock_parse_litellm_yaml, _mock_setup_env
    ):
        with patch.dict(
            "os.environ",
            {
                "STOCK_LIST": "AAPL",
                "OPENBB_FETCHER_ENABLED": "true",
                "OPENBB_FETCHER_PROVIDER": "fmp",
            },
            clear=True,
        ):
            config = Config._load_from_env()

        self.assertTrue(config.openbb_fetcher_enabled)
        self.assertEqual(config.openbb_fetcher_provider, "fmp")


if __name__ == "__main__":
    unittest.main()
