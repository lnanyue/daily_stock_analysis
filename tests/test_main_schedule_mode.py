# -*- coding: utf-8 -*-
"""Regression tests for scheduled mode stock selection behavior."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock
from unittest.mock import patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

import main
from src.config import Config


class _DummyConfig(SimpleNamespace):
    def validate(self):
        return []


class MainScheduleModeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.env_path.write_text("STOCK_LIST=600519\n", encoding="utf-8")
        self.env_patch = patch.dict(os.environ, {"ENV_FILE": str(self.env_path)}, clear=False)
        self.env_patch.start()
        Config.reset_instance()

    def tearDown(self) -> None:
        Config.reset_instance()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _make_args(self, **overrides):
        defaults = {
            "debug": False,
            "stocks": None,
            "webui": False,
            "webui_only": False,
            "serve": False,
            "serve_only": False,
            "host": "0.0.0.0",
            "port": 8000,
            "backtest": False,
            "market_review": False,
            "schedule": False,
            "no_run_immediately": False,
            "no_notify": False,
            "no_market_review": False,
            "dry_run": False,
            "workers": 1,
            "force_run": False,
            "single_notify": False,
            "no_context_snapshot": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_config(self, **overrides):
        defaults = {
            "log_dir": self.temp_dir.name,
            "webui_enabled": False,
            "dingtalk_stream_enabled": False,
            "schedule_enabled": False,
            "schedule_time": "18:00",
            "schedule_run_immediately": True,
            "run_immediately": True,
        }
        defaults.update(overrides)
        return _DummyConfig(**defaults)

    def test_schedule_mode_ignores_cli_stock_snapshot(self) -> None:
        args = self._make_args(schedule=True, stocks="600519,000001")
        config = self._make_config(schedule_enabled=False)
        scheduled_call = {}

        def fake_run_with_schedule(task, schedule_time, run_immediately, **kwargs):
            scheduled_call["schedule_time"] = schedule_time
            scheduled_call["run_immediately"] = run_immediately
            task()

        with patch("main.parse_arguments", return_value=args), \
             patch("src.config.get_config", return_value=config), \
             patch("src.logging_config.setup_logging"), \
             patch("src.core.runner.run_full_analysis") as run_full_analysis, \
             patch("src.core.runner.logger.warning") as warning_log, \
             patch("src.scheduler.run_with_schedule", side_effect=fake_run_with_schedule):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(scheduled_call, {"schedule_time": "18:00", "run_immediately": True})
        run_full_analysis.assert_called_once_with(mock.ANY, args, None)
        warning_log.assert_any_call(
            "定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，并在每次运行前重新读取最新的 STOCK_LIST。"
        )

    def test_single_run_keeps_cli_stock_override(self) -> None:
        args = self._make_args(stocks="600519,000001")
        config = self._make_config(run_immediately=True)

        with patch("main.parse_arguments", return_value=args), \
             patch("src.config.get_config", return_value=config), \
             patch("src.logging_config.setup_logging"), \
             patch("src.core.runner.run_full_analysis") as run_full_analysis:
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        run_full_analysis.assert_called_once_with(config, args, ["600519", "000001"])


class MainCleanupTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_flushes_litellm_worker_and_closes_resources(self) -> None:
        fake_worker = SimpleNamespace(
            flush=mock.AsyncMock(),
            stop=mock.AsyncMock(),
        )
        fake_litellm = ModuleType("litellm")
        fake_litellm_core = ModuleType("litellm.litellm_core_utils")
        fake_logging_worker = ModuleType("litellm.litellm_core_utils.logging_worker")
        fake_logging_worker.GLOBAL_LOGGING_WORKER = fake_worker

        pending = asyncio.create_task(asyncio.sleep(10))
        try:
            with patch.dict(
                sys.modules,
                {
                    "litellm": fake_litellm,
                    "litellm.litellm_core_utils": fake_litellm_core,
                    "litellm.litellm_core_utils.logging_worker": fake_logging_worker,
                },
            ), patch("src.utils.async_http.AsyncHttpClientManager") as manager_cls, patch(
                "src.storage.StorageManager.get_instance"
            ) as get_instance:
                manager_cls.return_value.close = mock.AsyncMock()
                engine = SimpleNamespace(dispose=mock.Mock())
                get_instance.return_value = SimpleNamespace(_engine=engine)

                from src.core.lifecycle import cleanup
                await cleanup()

            fake_worker.flush.assert_awaited_once()
            fake_worker.stop.assert_awaited_once()
            manager_cls.return_value.close.assert_awaited_once()
            engine.dispose.assert_called_once_with(close=True)
            self.assertTrue(pending.cancelled())
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
