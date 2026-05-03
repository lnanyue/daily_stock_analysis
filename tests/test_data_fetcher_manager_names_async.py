# -*- coding: utf-8 -*-
"""Regression tests for async stock-name lookup shortcuts."""

import asyncio
import time
import unittest
from threading import Event
from unittest.mock import MagicMock

from data_provider.manager import DataFetcherManager


class TestDataFetcherManagerAsyncNames(unittest.TestCase):
    def test_fetchers_none_loads_default_fetchers(self) -> None:
        class _TestManager(DataFetcherManager):
            @classmethod
            def _create_default_fetchers(cls, config=None, skip_names=None):
                skip = set(skip_names or [])
                defaults = [MagicMock(), MagicMock()]
                defaults[0].name = "EfinanceFetcher"
                defaults[0].priority = 0
                defaults[1].name = "AkshareFetcher"
                defaults[1].priority = 1
                return [fetcher for fetcher in defaults if fetcher.name not in skip]

        manager = _TestManager(fetchers=None)

        self.assertEqual(
            [fetcher.name for fetcher in manager.fetchers],
            ["EfinanceFetcher", "AkshareFetcher"],
        )

    def test_explicit_empty_fetchers_stays_empty(self) -> None:
        manager = DataFetcherManager(fetchers=[])

        self.assertEqual(manager.fetchers, [])

    def test_get_stock_name_prefers_static_mapping_before_remote_fetchers(self) -> None:
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = []
        manager._stock_name_cache = {}

        remote_fetcher = MagicMock()
        remote_fetcher.name = "RemoteFetcher"
        remote_fetcher.get_stock_name.return_value = "远程名称"
        manager._fetchers = [remote_fetcher]

        name = asyncio.run(DataFetcherManager.get_stock_name(manager, "600519"))

        self.assertEqual(name, "贵州茅台")
        remote_fetcher.get_stock_name.assert_not_called()
        self.assertEqual(manager._stock_name_cache["600519"], "贵州茅台")

    def test_normalize_market_stats_keeps_turnover_in_yi_yuan(self) -> None:
        stats = DataFetcherManager._normalize_market_stats(
            {
                "up_count": 2800,
                "down_count": 1900,
                "flat_count": 120,
                "limit_up_count": 72,
                "limit_down_count": 18,
                "total_amount": 11234.56,
            },
            "AkshareFetcher",
        )

        self.assertEqual(stats["up"], 2800)
        self.assertEqual(stats["down"], 1900)
        self.assertEqual(stats["limit_up"], 72)
        self.assertEqual(stats["volume_total"], 11234.56)
        self.assertEqual(stats["total_amount"], 11234.56)
        self.assertEqual(stats["source"], "AkshareFetcher")

    def test_get_main_indices_falls_back_when_first_source_returns_none(self) -> None:
        primary = MagicMock()
        primary.name = "EfinanceFetcher"
        primary.priority = 0
        primary.get_main_indices.return_value = None

        backup = MagicMock()
        backup.name = "AkshareFetcher"
        backup.priority = 1
        backup.get_main_indices.return_value = [{"code": "sh000001", "name": "上证指数"}]

        manager = DataFetcherManager(fetchers=[primary, backup])

        data = asyncio.run(manager.get_main_indices("cn"))

        self.assertEqual(data, [{"code": "sh000001", "name": "上证指数"}])
        primary.get_main_indices.assert_called_once_with(region="cn")
        backup.get_main_indices.assert_called_once_with(region="cn")

    def test_get_stock_name_times_out_and_falls_back_to_next_fetcher(self) -> None:
        slow_fetcher = MagicMock()
        slow_fetcher.name = "SlowFetcher"
        slow_fetcher.priority = 0

        unblock = Event()

        def _slow_name_lookup(_stock_code: str) -> None:
            unblock.wait(timeout=5.0)
            return None

        slow_fetcher.get_stock_name.side_effect = _slow_name_lookup

        backup_fetcher = MagicMock()
        backup_fetcher.name = "BackupFetcher"
        backup_fetcher.priority = 1
        backup_fetcher.get_stock_name.return_value = "示例股票"

        manager = DataFetcherManager(fetchers=[slow_fetcher, backup_fetcher])
        manager._stock_name_timeout_seconds = 0.01

        started = time.monotonic()
        # Set unblock before asyncio.run() completes so the background thread
        # parked in to_thread can finish before shutdown_default_executor.
        unblock.set()
        try:
            name = asyncio.run(manager.get_stock_name("123456"))
        finally:
            time.sleep(0.02)
        elapsed = time.monotonic() - started

        self.assertEqual(name, "示例股票")
        self.assertLess(elapsed, 0.12)
        backup_fetcher.get_stock_name.assert_called_once_with("123456")


if __name__ == "__main__":
    unittest.main()
