"""Tests for src.core.lifecycle — run_with_cleanup exit code behavior."""

import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, patch


class TestRunWithCleanup(TestCase):
    """run_with_cleanup returns 0 on success, 1 on exception."""

    @patch("src.core.lifecycle.cleanup", new_callable=AsyncMock)
    def test_returns_0_on_success(self, mock_cleanup):
        from src.core.lifecycle import run_with_cleanup

        async def success():
            return 42

        result = asyncio.run(run_with_cleanup(success()))
        self.assertEqual(result, 0)
        mock_cleanup.assert_awaited_once()

    @patch("src.core.lifecycle.cleanup", new_callable=AsyncMock)
    def test_returns_1_on_exception(self, mock_cleanup):
        from src.core.lifecycle import run_with_cleanup

        async def failure():
            raise RuntimeError("test failure")

        result = asyncio.run(run_with_cleanup(failure()))
        self.assertEqual(result, 1)
        mock_cleanup.assert_awaited_once()
