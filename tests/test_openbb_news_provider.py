# -*- coding: utf-8 -*-
"""Tests for the optional OpenBB company-news provider."""

import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

from src.search.providers.openbb import OpenBBNewsProvider
from src.search.service import SearchService


class TestOpenBBNewsProvider(unittest.TestCase):
    def test_extract_symbol_normalizes_us_hk_and_cn_codes(self) -> None:
        self.assertEqual(
            OpenBBNewsProvider._extract_symbol("Alibaba BABA stock latest news"),
            "BABA",
        )
        self.assertEqual(
            OpenBBNewsProvider._extract_symbol("Tencent hk00700 stock latest news"),
            "0700.HK",
        )
        self.assertEqual(
            OpenBBNewsProvider._extract_symbol("贵州茅台 600519 股票 最新消息"),
            "600519.SS",
        )
        self.assertEqual(
            OpenBBNewsProvider._extract_symbol("宁德时代 300750 股票 最新消息"),
            "300750.SZ",
        )

    def test_search_maps_openbb_company_news_results(self) -> None:
        calls = []

        class _FakeNews:
            @staticmethod
            def company(**kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(
                    results=[
                        types.SimpleNamespace(
                            title="Alibaba shares rise after earnings",
                            summary="Revenue beat expectations.",
                            url="https://finance.example.com/baba",
                            source="Example Finance",
                            date=date(2026, 4, 28),
                        )
                    ]
                )

        fake_openbb = types.ModuleType("openbb")
        fake_openbb.obb = types.SimpleNamespace(news=_FakeNews())

        provider = OpenBBNewsProvider(provider="yfinance")
        with patch.dict(sys.modules, {"openbb": fake_openbb}):
            response = provider.search("Alibaba BABA stock latest news", max_results=3, days=7)

        self.assertTrue(response.success)
        self.assertEqual(response.provider, "OpenBB")
        self.assertEqual(calls[0]["symbol"], "BABA")
        self.assertEqual(calls[0]["provider"], "yfinance")
        self.assertEqual(response.results[0].title, "Alibaba shares rise after earnings")
        self.assertEqual(response.results[0].snippet, "Revenue beat expectations.")
        self.assertEqual(response.results[0].published_date, "2026-04-28")

    def test_search_service_adds_openbb_provider_when_enabled(self) -> None:
        service = SearchService(openbb_news_enabled=True, openbb_news_provider="yfinance")

        self.assertIn("OpenBB", [provider.name for provider in service._providers])


if __name__ == "__main__":
    unittest.main()
