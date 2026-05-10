import unittest
from unittest.mock import patch, MagicMock


class TestBacktestCli(unittest.TestCase):
    @patch("src.services.backtest_service.BacktestService")
    def test_run_backtest_passes_code_as_keyword(self, mock_service_cls):
        from src.core.runner import run_backtest

        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service

        result = run_backtest(backtest_code="600519")

        mock_service.run_backtest.assert_called_once_with(
            code="600519",
            force=False,
            eval_window_days=None,
        )
        self.assertEqual(result, 0)

    @patch("src.services.backtest_service.BacktestService")
    def test_run_backtest_passes_optional_flags(self, mock_service_cls):
        from src.core.runner import run_backtest

        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service

        result = run_backtest(
            backtest_code="600519",
            force=True,
            eval_window_days=5,
        )

        mock_service.run_backtest.assert_called_once_with(
            code="600519",
            force=True,
            eval_window_days=5,
        )
        self.assertEqual(result, 0)

    def test_backtest_returns_nonzero_on_failure(self):
        """backtest 内部异常时 run_backtest 返回非 0。"""
        from src.core.runner import run_backtest

        with patch("src.services.backtest_service.BacktestService") as mock_cls:
            mock_service = MagicMock()
            mock_service.run_backtest.side_effect = RuntimeError("backtest failed")
            mock_cls.return_value = mock_service

            result = run_backtest(backtest_code="600519")
            self.assertNotEqual(result, 0)
