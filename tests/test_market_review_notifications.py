# -*- coding: utf-8 -*-
"""Regression tests for market-review notification diagnostics."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.market_review import run_market_review


class TestMarketReviewNotifications(unittest.IsolatedAsyncioTestCase):
    @patch("src.core.market_review.get_config")
    @patch("src.core.market_review.MarketAnalyzer")
    async def test_run_market_review_logs_delivery_summary_when_push_fails(
        self,
        mock_market_analyzer,
        mock_get_config,
    ):
        mock_get_config.return_value = SimpleNamespace(market_review_region="cn")
        analyzer_instance = MagicMock()
        analyzer_instance.run_daily_review = AsyncMock(return_value="今日复盘内容")
        mock_market_analyzer.return_value = analyzer_instance

        notifier = MagicMock()
        notifier.save_report_to_file.return_value = "/tmp/market_review.md"
        notifier.is_available.return_value = True
        notifier.send = AsyncMock(return_value=False)
        notifier.get_last_delivery_summary.return_value = "失败[邮件(3次): timed out]"

        with patch("src.core.market_review.logger.warning") as warning_log:
            report = await run_market_review(
                notifier=notifier,
                analyzer=None,
                search_service=None,
                send_notification=True,
                merge_notification=False,
            )

        self.assertEqual(report, "今日复盘内容")
        warning_log.assert_any_call("大盘复盘推送失败: %s", "失败[邮件(3次): timed out]")
