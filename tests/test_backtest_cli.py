import unittest
from unittest.mock import patch, MagicMock


class TestBacktestCli(unittest.TestCase):
    @patch("src.services.backtest_service.BacktestService")
    def test_run_backtest_passes_code_as_keyword(self, mock_service_cls):
        from src.core.runner import run_backtest

        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service

        result = run_backtest(backtest_code="600519")

        mock_service.run_backtest.assert_called_once_with(code="600519")
        self.assertEqual(result, 0)
