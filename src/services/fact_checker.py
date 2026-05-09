# -*- coding: utf-8 -*-
"""T+5 prediction fact-checking service.

Evaluates AI predictions against actual market movements after 5 trading days.
Call ``evaluate_pending()`` before displaying historical data to auto-evaluate
unchecked predictions.  Use ``get_stats()`` / ``get_model_ranking()`` to view
accuracy aggregates.
"""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

_THRESHOLD_PCT = 1.0  # minimum change (%) to count as "up" or "down"


class FactChecker:
    """Evaluate prediction accuracy against T+5 market data."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_pending(self, limit: int = 50) -> int:
        """Evaluate unchecked predictions whose eval_date has passed.

        Returns the number of predictions evaluated.
        """
        pending = self.db.get_pending_evaluations(limit=limit)
        if not pending:
            return 0

        evaluated = 0
        now = datetime.now()
        for rec in pending:
            try:
                self._evaluate_one(rec, now)
                evaluated += 1
            except Exception as exc:
                logger.warning(
                    "[%s] Fact-check failed for %s: %s",
                    rec.get("code"), rec.get("query_id"), exc,
                )
        logger.info("Fact-check: evaluated %d / %d pending", evaluated, len(pending))
        return evaluated

    def get_stats(
        self, model: Optional[str] = None, code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate accuracy stats, optionally filtered by model or stock."""
        rows = self.db.get_evaluation_stats(model=model, code=code)
        total_all = 0
        correct_all = 0
        models = []
        for r in rows:
            total = int(r.get("total", 0) or 0)
            correct = int(r.get("correct", 0) or 0)
            total_all += total
            correct_all += correct
            models.append({
                "model": r.get("model_used", "unknown") or "unknown",
                "total": total,
                "correct": correct,
                "win_rate": round(correct / total * 100, 1) if total > 0 else 0.0,
            })
        return {
            "total_predictions": total_all,
            "total_correct": correct_all,
            "overall_win_rate": round(correct_all / total_all * 100, 1) if total_all > 0 else 0.0,
            "models": models,
        }

    def get_model_ranking(self) -> List[Dict[str, Any]]:
        """Return models sorted by win rate descending."""
        stats = self.get_stats()
        models = stats.get("models", [])
        models.sort(key=lambda m: m["win_rate"], reverse=True)
        return models

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evaluate_one(self, rec: Dict[str, Any], now: datetime) -> None:
        """Evaluate a single prediction record."""
        query_id = rec["query_id"]
        code = rec["code"]
        decision = rec.get("decision_type", "").strip().lower()
        eval_date = rec.get("eval_date")
        close_analysis = rec.get("close_at_analysis")

        if not eval_date or close_analysis is None or not decision:
            logger.debug("[%s] Skipping %s: missing fields", code, query_id)
            return

        # Fetch close price on eval_date from stock_daily
        close_eval = self._get_close_price(code, eval_date)
        if close_eval is None or close_eval <= 0:
            return  # data not available yet — leave as pending

        change_pct = (close_eval - close_analysis) / close_analysis * 100

        verdict = self._judge(decision, change_pct)
        self.db.update_prediction_verdict(
            query_id=query_id,
            verdict=verdict,
            change_pct_5d=round(change_pct, 2),
            close_at_eval=close_eval,
            evaluated_at=now,
        )
        logger.debug(
            "[%s] %s → verdict=%s (change=%.2f%%)",
            code, query_id, verdict, change_pct,
        )

    def _get_close_price(self, code: str, eval_date: date) -> Optional[float]:
        """Look up close price from DataFetcherManager for given code and date."""
        try:
            from data_provider import DataFetcherManager
            manager = DataFetcherManager()
            df, _ = manager.get_daily_data_sync(
                code,
                start_date=(eval_date - timedelta(days=5)).isoformat(),
                end_date=(eval_date + timedelta(days=1)).isoformat(),
                days=10,
            )
            if df is not None and not df.empty:
                date_col = 'date' if 'date' in df.columns else df.columns[0]
                match = df[df[date_col] == eval_date]
                if not match.empty:
                    close_col = 'close' if 'close' in df.columns else '收盘'
                    return float(match.iloc[0].get(close_col, 0))
                # Try nearest previous date
                prior = df[df[date_col] < eval_date].sort_values(date_col, ascending=False)
                if not prior.empty:
                    close_col = 'close' if 'close' in df.columns else '收盘'
                    logger.debug("[%s] No data for %s, using nearest prior", code, eval_date)
                    return float(prior.iloc[0].get(close_col, 0))
            return None
        except Exception as exc:
            logger.debug("[%s] get_daily_data_sync failed for %s: %s", code, eval_date, exc)
            return None

    @staticmethod
    def _judge(decision: str, change_pct: float) -> str:
        """Map decision + actual change to correct/wrong."""
        up = change_pct >= _THRESHOLD_PCT
        down = change_pct <= -_THRESHOLD_PCT

        if decision == "buy":
            return "correct" if up else "wrong"
        elif decision == "sell":
            return "correct" if down else "wrong"
        else:  # hold / neutral
            return "correct" if not up and not down else "wrong"
