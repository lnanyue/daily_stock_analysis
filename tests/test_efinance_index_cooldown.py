# -*- coding: utf-8 -*-
"""Regression coverage for efinance index quote fallback behavior."""

import json
import sys
import time
import types
import unittest
from unittest.mock import patch

import data_provider.efinance_fetcher as efinance_module
from data_provider.efinance_fetcher import EfinanceFetcher


class TestEfinanceIndexCooldown(unittest.TestCase):
    def setUp(self) -> None:
        self._old_failure_until = efinance_module._index_quotes_failure_until
        efinance_module._index_quotes_failure_until = 0.0

    def tearDown(self) -> None:
        efinance_module._index_quotes_failure_until = self._old_failure_until

    def _make_fetcher(self) -> EfinanceFetcher:
        config = types.SimpleNamespace(enable_eastmoney_patch=False)
        with patch("data_provider.efinance_fetcher.get_config", return_value=config):
            return EfinanceFetcher(sleep_min=0, sleep_max=0)

    def test_get_main_indices_cools_down_after_json_decode_error(self) -> None:
        fetcher = self._make_fetcher()
        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(get_realtime_quotes=lambda *args, **kwargs: None)
        )

        with patch.dict(sys.modules, {"efinance": fake_efinance}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                with patch(
                    "data_provider.efinance_fetcher._ef_call_with_timeout",
                    side_effect=json.JSONDecodeError("Expecting value", "", 0),
                ) as api_call:
                    with self.assertLogs("data_provider.efinance_fetcher", level="WARNING") as captured:
                        self.assertIsNone(fetcher.get_main_indices("cn"))

                self.assertEqual(api_call.call_count, 1)
                self.assertGreater(efinance_module._index_quotes_failure_until, time.time())
                self.assertIn("获取指数行情失败", "\n".join(captured.output))
                self.assertIn("交给后续数据源兜底", "\n".join(captured.output))

                with patch("data_provider.efinance_fetcher._ef_call_with_timeout") as api_call:
                    self.assertIsNone(fetcher.get_main_indices("cn"))

                api_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
