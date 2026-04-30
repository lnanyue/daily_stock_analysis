# -*- coding: utf-8 -*-
"""
Regression tests for stock-name prefetch behavior.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import DataFetcherManager


class _DummyFetcher:
    name = "DummyFetcher"

    @staticmethod
    def get_stock_name(_stock_code):
        return "测试股票"


class TestPrefetchStockNames(unittest.TestCase):
    def test_prefetch_stock_names_calls_get_stock_name_without_realtime(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager.get_stock_name_sync = MagicMock(return_value="")

        DataFetcherManager.prefetch_stock_names(manager, ["SH600519", "000001"], use_bulk=False)

        manager.get_stock_name_sync.assert_has_calls(
            [
                call("600519", allow_realtime=False),
                call("000001", allow_realtime=False),
            ]
        )

    def test_get_stock_name_skips_realtime_when_allow_realtime_false(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = [_DummyFetcher()]
        manager.get_realtime_quote = MagicMock(return_value=MagicMock(name="实时名称"))

        name = DataFetcherManager.get_stock_name_sync(manager, "123456", allow_realtime=False)

        self.assertEqual(name, "测试股票")
        manager.get_realtime_quote.assert_not_called()

    def test_get_stock_name_prefers_static_mapping_before_remote_fetchers(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        remote_fetcher = MagicMock()
        remote_fetcher.name = "RemoteFetcher"
        remote_fetcher.get_stock_name.return_value = "远程名称"
        manager._fetchers = [remote_fetcher]
        manager.get_realtime_quote = MagicMock()

        name = DataFetcherManager.get_stock_name_sync(manager, "600519", allow_realtime=False)

        self.assertEqual(name, "贵州茅台")
        manager.get_realtime_quote.assert_not_called()
        remote_fetcher.get_stock_name.assert_not_called()
        self.assertEqual(manager._stock_name_cache["600519"], "贵州茅台")

if __name__ == "__main__":
    unittest.main()
