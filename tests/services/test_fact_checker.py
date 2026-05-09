# -*- coding: utf-8 -*-
"""Tests for src.services.fact_checker — T+5 prediction evaluation."""

from datetime import date, datetime
from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.services.fact_checker import FactChecker


class FactCheckerJudgeTest(TestCase):
    """_judge static method: maps decision + actual change to verdict."""

    def test_buy_up_is_correct(self):
        self.assertEqual(FactChecker._judge("buy", 5.0), "correct")

    def test_buy_down_is_wrong(self):
        self.assertEqual(FactChecker._judge("buy", -3.0), "wrong")

    def test_buy_flat_is_wrong(self):
        self.assertEqual(FactChecker._judge("buy", 0.5), "wrong")

    def test_sell_down_is_correct(self):
        self.assertEqual(FactChecker._judge("sell", -5.0), "correct")

    def test_sell_up_is_wrong(self):
        self.assertEqual(FactChecker._judge("sell", 3.0), "wrong")

    def test_sell_flat_is_wrong(self):
        self.assertEqual(FactChecker._judge("sell", 0.5), "wrong")

    def test_hold_flat_is_correct(self):
        self.assertEqual(FactChecker._judge("hold", 0.5), "correct")
        self.assertEqual(FactChecker._judge("neutral", -0.5), "correct")

    def test_hold_up_is_wrong(self):
        self.assertEqual(FactChecker._judge("hold", 2.0), "wrong")
        self.assertEqual(FactChecker._judge("hold", -2.0), "wrong")

    def test_edge_case_threshold(self):
        """1.0% exactly should count as up."""
        self.assertEqual(FactChecker._judge("buy", 1.0), "correct")
        self.assertEqual(FactChecker._judge("hold", 1.0), "wrong")


class FactCheckerEvaluateOneTest(TestCase):
    """_evaluate_one: individual prediction evaluation."""

    def setUp(self):
        self.db = MagicMock()
        self.checker = FactChecker(self.db)

    def test_skips_if_missing_fields(self):
        rec = {"query_id": "q1", "code": "600519"}
        self.checker._evaluate_one(rec, datetime.now())
        self.db.get_session.assert_not_called()
        self.db.update_prediction_verdict.assert_not_called()

    def test_skips_if_close_price_not_found(self):
        rec = {"query_id": "q1", "code": "600519", "decision_type": "buy",
               "eval_date": date(2026, 5, 13), "close_at_analysis": 100.0}
        self.checker._get_close_price = MagicMock(return_value=None)
        self.checker._evaluate_one(rec, datetime.now())
        self.db.update_prediction_verdict.assert_not_called()

    def test_evaluates_buy_correct(self):
        rec = {"query_id": "q1", "code": "600519", "decision_type": "buy",
               "eval_date": date(2026, 5, 13), "close_at_analysis": 100.0}
        self.checker._get_close_price = MagicMock(return_value=108.0)
        self.checker._evaluate_one(rec, datetime(2026, 5, 13, 15, 0))
        self.db.update_prediction_verdict.assert_called_once()
        kwargs = self.db.update_prediction_verdict.call_args[1]
        self.assertEqual(kwargs["verdict"], "correct")
        self.assertAlmostEqual(kwargs["change_pct_5d"], 8.0, places=1)

    def test_evaluates_buy_wrong(self):
        rec = {"query_id": "q1", "code": "600519", "decision_type": "buy",
               "eval_date": date(2026, 5, 13), "close_at_analysis": 100.0}
        self.checker._get_close_price = MagicMock(return_value=95.0)
        self.checker._evaluate_one(rec, datetime.now())
        kwargs = self.db.update_prediction_verdict.call_args[1]
        self.assertEqual(kwargs["verdict"], "wrong")

    def test_empty_decision_skips(self):
        rec = {"query_id": "q1", "code": "600519", "decision_type": "",
               "eval_date": date(2026, 5, 13), "close_at_analysis": 100.0}
        self.checker._evaluate_one(rec, datetime.now())
        self.db.update_prediction_verdict.assert_not_called()


class FactCheckerGetClosePriceTest(TestCase):
    """_get_close_price: DataFetcherManager network lookup of close price."""

    @patch("data_provider.DataFetcherManager")
    def test_returns_float_when_found(self, mock_manager_cls):
        import pandas as pd
        df = pd.DataFrame({
            "date": [date(2026, 5, 13)],
            "close": [105.5],
        })
        mock_manager_cls.return_value.get_daily_data_sync.return_value = (df, "test")

        checker = FactChecker(MagicMock())
        result = checker._get_close_price("600519", date(2026, 5, 13))
        self.assertAlmostEqual(result, 105.5)

    @patch("data_provider.DataFetcherManager")
    def test_returns_none_when_not_found(self, mock_manager_cls):
        import pandas as pd
        df = pd.DataFrame({"date": [], "close": []})
        mock_manager_cls.return_value.get_daily_data_sync.return_value = (df, "test")

        checker = FactChecker(MagicMock())
        self.assertIsNone(checker._get_close_price("600519", date(2026, 5, 13)))

    @patch("data_provider.DataFetcherManager")
    def test_returns_none_on_exception(self, mock_manager_cls):
        mock_manager_cls.return_value.get_daily_data_sync.side_effect = Exception("Network down")

        checker = FactChecker(MagicMock())
        self.assertIsNone(checker._get_close_price("600519", date(2026, 5, 13)))


class FactCheckerEvaluatePendingTest(TestCase):
    """evaluate_pending: batch evaluation."""

    def test_returns_zero_when_no_pending(self):
        db = MagicMock()
        db.get_pending_evaluations.return_value = []
        checker = FactChecker(db)
        self.assertEqual(checker.evaluate_pending(limit=50), 0)

    def test_evaluates_pending_records(self):
        db = MagicMock()
        db.get_pending_evaluations.return_value = [
            {"query_id": "q1", "code": "600519", "decision_type": "buy",
             "eval_date": date(2026, 5, 13), "close_at_analysis": 100.0},
            {"query_id": "q2", "code": "000858", "decision_type": "sell",
             "eval_date": date(2026, 5, 13), "close_at_analysis": 50.0},
        ]
        checker = FactChecker(db)
        with patch.object(checker, "_evaluate_one") as mock_eval:
            result = checker.evaluate_pending(limit=50)
            self.assertEqual(result, 2)
            self.assertEqual(mock_eval.call_count, 2)

    def test_continues_on_exception(self):
        db = MagicMock()
        db.get_pending_evaluations.return_value = [
            {"query_id": "q1", "code": "600519", "decision_type": "buy",
             "eval_date": date(2026, 5, 13), "close_at_analysis": 100.0},
        ]
        checker = FactChecker(db)
        with patch.object(checker, "_evaluate_one", side_effect=ValueError("bad")):
            result = checker.evaluate_pending(limit=50)
            self.assertEqual(result, 0)  # exception caught, counted as evaluated

    def test_passes_limit_to_db(self):
        db = MagicMock()
        db.get_pending_evaluations.return_value = []
        checker = FactChecker(db)
        checker.evaluate_pending(limit=10)
        db.get_pending_evaluations.assert_called_with(limit=10)


class FactCheckerGetStatsTest(TestCase):
    """get_stats: aggregate accuracy stats."""

    def test_returns_defaults_when_no_data(self):
        db = MagicMock()
        db.get_evaluation_stats.return_value = []
        checker = FactChecker(db)
        stats = checker.get_stats()
        self.assertEqual(stats["total_predictions"], 0)
        self.assertEqual(stats["total_correct"], 0)
        self.assertEqual(stats["overall_win_rate"], 0.0)

    def test_computes_win_rate(self):
        db = MagicMock()
        db.get_evaluation_stats.return_value = [
            {"model_used": "gpt-4", "total": 10, "correct": 7},
        ]
        checker = FactChecker(db)
        stats = checker.get_stats()
        self.assertEqual(stats["total_predictions"], 10)
        self.assertEqual(stats["total_correct"], 7)
        self.assertAlmostEqual(stats["overall_win_rate"], 70.0)
        self.assertEqual(len(stats["models"]), 1)

    def test_filters_by_model(self):
        db = MagicMock()
        checker = FactChecker(db)
        checker.get_stats(model="gpt-4")
        db.get_evaluation_stats.assert_called_with(model="gpt-4", code=None)

    def test_filters_by_code(self):
        db = MagicMock()
        checker = FactChecker(db)
        checker.get_stats(code="600519")
        db.get_evaluation_stats.assert_called_with(model=None, code="600519")


class FactCheckerGetModelRankingTest(TestCase):
    """get_model_ranking: models sorted by win rate."""

    def test_returns_sorted(self):
        db = MagicMock()
        db.get_evaluation_stats.return_value = [
            {"model_used": "model_a", "total": 10, "correct": 3},
            {"model_used": "model_b", "total": 10, "correct": 8},
        ]
        checker = FactChecker(db)
        ranking = checker.get_model_ranking()
        self.assertEqual(len(ranking), 2)
        self.assertEqual(ranking[0]["model"], "model_b")  # higher win rate first
        self.assertEqual(ranking[1]["model"], "model_a")

    def test_empty_when_no_data(self):
        db = MagicMock()
        db.get_evaluation_stats.return_value = []
        checker = FactChecker(db)
        self.assertEqual(checker.get_model_ranking(), [])
