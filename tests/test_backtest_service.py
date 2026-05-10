from datetime import date
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pandas as pd


class TestBacktestService(TestCase):
    def test_run_backtest_handles_timestamp_daily_dates(self):
        """回测日线 date 为 datetime64 时不应触发 date 比较异常。"""
        from src.services.backtest_service import BacktestService

        service = BacktestService.__new__(BacktestService)
        service.db = MagicMock()
        service.repo = MagicMock()
        service.repo.get_candidates.return_value = [
            SimpleNamespace(
                id=1,
                code="600519",
                operation_advice="买入",
                stop_loss=None,
                take_profit=None,
                context_snapshot={},
                created_at=None,
            )
        ]
        service.repo.parse_analysis_date_from_snapshot.return_value = date(2026, 5, 1)
        service.repo.save_results_batch.return_value = 0

        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05"]),
            "high": [101.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.0, 102.0, 103.0],
        })

        with patch.object(service, "_try_fill_daily_data", return_value=df), \
             patch("src.services.backtest_service.BacktestEngine.evaluate_single", return_value={
                 "eval_status": "completed",
                 "analysis_date": date(2026, 5, 1),
                 "eval_window_days": 2,
                 "engine_version": "v1",
                 "operation_advice": "买入",
             }):
            result = service.run_backtest(
                code="600519",
                eval_window_days=2,
                min_age_days=1,
            )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["errors"], 0)
