# -*- coding: utf-8 -*-
"""Regression tests for bot MarketCommand trading-day filtering."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from bot.commands.market import MarketCommand
from bot.models import BotMessage, ChatType


def _make_message() -> BotMessage:
    return BotMessage(
        platform="feishu",
        message_id="m1",
        user_id="u1",
        user_name="tester",
        chat_id="c1",
        chat_type=ChatType.PRIVATE,
        content="/market",
        raw_content="/market",
        mentioned=False,
        timestamp=datetime.now(),
    )


class MarketCommandRegionFilterTestCase(unittest.IsolatedAsyncioTestCase):
    def _make_config(
        self,
        *,
        market_review_region: str,
        trading_day_check_enabled: bool = True,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            market_review_region=market_review_region,
            trading_day_check_enabled=trading_day_check_enabled,
            has_search_capability_enabled=lambda: False,
            llm_model_list=[],
            gemini_api_key=None,
            anthropic_api_key=None,
            openai_api_key=None,
            deepseek_api_keys=[],
        )

    async def _run_command(
        self,
        *,
        market_review_region: str,
        open_markets: set[str],
        trading_day_check_enabled: bool = True,
    ):
        notifier = MagicMock()
        notifier.is_available.return_value = True
        notifier.send = AsyncMock(return_value=True)

        run_market_review = AsyncMock(return_value="report")
        cmd = MarketCommand()

        with patch(
            "src.config.get_config",
            return_value=self._make_config(
                market_review_region=market_review_region,
                trading_day_check_enabled=trading_day_check_enabled,
            ),
        ), patch(
            "src.notification.NotificationService",
            return_value=notifier,
        ), patch(
            "src.core.trading_calendar.get_open_markets_today",
            return_value=open_markets,
        ), patch(
            "src.core.market_review.run_market_review",
            run_market_review,
        ):
            await cmd._run_market_review(_make_message())

        return notifier, run_market_review

    async def test_both_with_cn_us_open_passes_override_region_cn_us(self) -> None:
        notifier, run_market_review = await self._run_command(
            market_review_region="both",
            open_markets={"cn", "us"},
        )

        run_market_review.assert_awaited_once()
        kwargs = run_market_review.call_args.kwargs
        self.assertEqual(kwargs.get("override_region"), "cn,us")
        notifier.send.assert_not_called()

    async def test_all_relevant_markets_closed_skips_review(self) -> None:
        notifier, run_market_review = await self._run_command(
            market_review_region="cn",
            open_markets=set(),
        )

        run_market_review.assert_not_called()
        notifier.send.assert_awaited_once()
        self.assertIn("休市", notifier.send.call_args.args[0])

    async def test_trading_day_check_disabled_does_not_pass_override(self) -> None:
        _notifier, run_market_review = await self._run_command(
            market_review_region="both",
            open_markets={"cn"},
            trading_day_check_enabled=False,
        )

        run_market_review.assert_awaited_once()
        kwargs = run_market_review.call_args.kwargs
        self.assertIsNone(kwargs.get("override_region"))


if __name__ == "__main__":
    unittest.main()
