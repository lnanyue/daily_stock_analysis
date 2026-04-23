# -*- coding: utf-8 -*-
"""Regression tests for async stock-name lookup shortcuts."""

import asyncio
import unittest
from unittest.mock import MagicMock

from data_provider.manager import DataFetcherManager


class TestDataFetcherManagerAsyncNames(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
