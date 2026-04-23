# -*- coding: utf-8 -*-
"""Regression tests for Akshare telegraph compatibility helpers."""

import unittest
from unittest.mock import AsyncMock, patch

from data_provider.akshare_fetcher import AkshareFetcher


class TestAkshareTelegraphCompat(unittest.TestCase):
    def test_get_latest_telegraph_filters_by_keywords(self) -> None:
        fetcher = AkshareFetcher()
        payload = [
            {
                "title": "贵州茅台盘中异动",
                "content": "贵州茅台获资金关注",
                "date": "2026-04-23 10:00:00",
                "stocks": ["贵州茅台"],
                "subjects": ["白酒"],
            },
            {
                "title": "其他公司快讯",
                "content": "与目标股票无关",
                "date": "2026-04-23 10:05:00",
                "stocks": ["其他公司"],
                "subjects": ["其他"],
            },
        ]

        with patch(
            "data_provider.cls_fetcher.ClsTelegramFetcher.fetch_latest_telegrams",
            new=AsyncMock(return_value=payload),
        ):
            result = fetcher.get_latest_telegraph(["贵州茅台"])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "贵州茅台盘中异动")
        self.assertEqual(result[0]["source"], "cls")


if __name__ == "__main__":
    unittest.main()
