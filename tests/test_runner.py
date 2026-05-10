import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch


class TestRunFullAnalysis(TestCase):
    def test_raises_when_all_stock_analysis_results_are_missing(self):
        """有待分析股票但结果为空时，完整分析应向外抛错让退出码变为非 0。"""
        from src.core.runner import run_full_analysis

        config = MagicMock()
        config.market_review_enabled = False
        config.single_stock_notify = False
        config.merge_email_notification = False
        config.backtest_enabled = False
        config.analysis_delay = 0

        args = SimpleNamespace(
            workers=1,
            dry_run=False,
            no_notify=True,
            no_market_review=True,
            no_context_snapshot=True,
            single_notify=False,
            market_review=False,
            force_run=True,
        )

        pipeline = MagicMock()
        pipeline.run = AsyncMock(return_value=[])

        with patch("src.core.runner._compute_trading_day_filter", return_value=(["600519"], "", False)), \
             patch("src.core.pipeline.StockAnalysisPipeline", return_value=pipeline):
            with self.assertRaises(RuntimeError):
                asyncio.run(run_full_analysis(config, args, ["600519"]))
