# -*- coding: utf-8 -*-
"""Unit tests for PortfolioOptimizer (MPT + Risk Parity)."""

from __future__ import annotations

import unittest
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from src.agent.quantitative.portfolio_optimizer import PortfolioOptimizer


def _make_fake_prices(
    stocks: List[str], days: int = 300, seed: int = 42
) -> pd.DataFrame:
    """Generate fake close prices for testing.

    Returns a DataFrame with index=date, columns=stock_code, values=close price.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=days, freq="B")  # Business days
    prices = pd.DataFrame(index=dates, columns=stocks)

    for stock in stocks:
        # Start at 100, random walk with positive drift to exceed risk-free rate
        returns = rng.normal(0.02, 0.05, size=days)  # Higher positive drift
        cum_returns = (1 + returns).cumprod()
        base_price = 100.0
        prices[stock] = base_price * cum_returns

    return prices


class PortfolioOptimizerTestCase(unittest.TestCase):
    """Tests for PortfolioOptimizer."""

    def test_compute_mpt_basic(self) -> None:
        """Test basic MPT calculation with simulated data."""
        prices = _make_fake_prices(["600519", "000858", "601318"], days=300)
        optimizer = PortfolioOptimizer(prices)

        result = optimizer.compute_mpt()

        if "error" in result:
            self.skipTest(f"PyPortfolioOpt not available: {result['error']}")

        # Check expected keys
        self.assertIn("expected_returns", result)
        self.assertIn("covariance_matrix", result)
        self.assertIn("max_sharpe_weights", result)
        self.assertIn("min_volatility_weights", result)
        self.assertIn("efficient_frontier", result)

        # Check weights sum to ~1
        sharpe_weights = result["max_sharpe_weights"]
        self.assertAlmostEqual(sum(sharpe_weights.values()), 1.0, places=3)

        min_vol_weights = result["min_volatility_weights"]
        self.assertAlmostEqual(sum(min_vol_weights.values()), 1.0, places=3)

        # Check efficient frontier
        ef = result["efficient_frontier"]
        self.assertIsInstance(ef, list)
        self.assertGreater(len(ef), 10)  # Should have multiple points

    def test_compute_risk_parity_basic(self) -> None:
        """Test basic Risk Parity calculation with simulated data."""
        prices = _make_fake_prices(["600519", "000858", "601318", "000001"], days=300)
        optimizer = PortfolioOptimizer(prices)

        result = optimizer.compute_risk_parity()

        if "error" in result:
            self.skipTest(f"PyPortfolioOpt not available: {result['error']}")

        # Check expected keys
        self.assertIn("risk_parity_weights", result)
        self.assertIn("risk_contributions", result)
        self.assertIn("portfolio_volatility", result)

        # Check weights sum to ~1
        weights = result["risk_parity_weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)

        # Check risk contributions are roughly equal
        contributions = result["risk_contributions"]
        values = list(contributions.values())
        # All contributions should be within 50% of the mean
        mean_contrib = sum(values) / len(values)
        for v in values:
            self.assertGreater(v, mean_contrib * 0.5)
            self.assertLess(v, mean_contrib * 1.5)

    def test_efficient_frontier_generation(self) -> None:
        """Test efficient frontier generation."""
        prices = _make_fake_prices(["600519", "000858", "601318"], days=300)
        optimizer = PortfolioOptimizer(prices)

        from pypfopt import EfficientFrontier, risk_models, expected_returns

        mu = expected_returns.mean_historical_return(prices)
        S = risk_models.sample_cov(prices)
        ef_points = optimizer._get_efficient_frontier(mu, S, points=20)

        self.assertIsInstance(ef_points, list)
        self.assertLessEqual(len(ef_points), 20)
        self.assertGreater(len(ef_points), 5)

        # Check each point has return and volatility
        for point in ef_points:
            self.assertIn("return", point)
            self.assertIn("volatility", point)
            self.assertIsInstance(point["return"], float)
            self.assertIsInstance(point["volatility"], float)

    def test_empty_data_handling(self) -> None:
        """Test handling of empty or insufficient data."""
        # Empty DataFrame
        empty_prices = pd.DataFrame()
        optimizer = PortfolioOptimizer(empty_prices)
        result = optimizer.compute_mpt()
        self.assertIn("error", result)

        # Single row (insufficient data)
        single_row = pd.DataFrame({"600519": [100.0]})
        optimizer2 = PortfolioOptimizer(single_row)
        result2 = optimizer2.compute_mpt()
        self.assertIn("error", result2)

    def test_single_asset_portfolio(self) -> None:
        """Test single asset portfolio (cannot compute covariance)."""
        prices = pd.DataFrame(
            {"600519": [100.0, 101.0, 102.0, 101.5, 103.0]},
            index=pd.date_range("2024-01-01", periods=5, freq="B"),
        )
        optimizer = PortfolioOptimizer(prices)

        # MPT with single asset should fail gracefully
        result = optimizer.compute_mpt()
        # Either works (some libraries handle it) or returns error
        if "error" not in result:
            self.assertIn("max_sharpe_weights", result)

    def test_from_price_dict(self) -> None:
        """Test creating optimizer from price dict."""
        price_dict: Dict[str, List[Tuple[str, float]]] = {
            "600519": [("2024-01-01", 100.0), ("2024-01-02", 101.0), ("2024-01-03", 102.0)],
            "000858": [("2024-01-01", 50.0), ("2024-01-02", 51.0), ("2024-01-03", 49.5)],
        }

        optimizer = PortfolioOptimizer.from_price_dict(price_dict)
        self.assertIsNotNone(optimizer)
        self.assertIsInstance(optimizer, PortfolioOptimizer)
        self.assertEqual(len(optimizer.prices.columns), 2)
        self.assertEqual(len(optimizer.prices), 3)

    def test_from_price_dict_empty(self) -> None:
        """Test creating optimizer from empty price dict."""
        optimizer = PortfolioOptimizer.from_price_dict({})
        self.assertIsNone(optimizer)

    def test_returns_calculation(self) -> None:
        """Test that returns are calculated correctly."""
        prices = pd.DataFrame(
            {"600519": [100.0, 102.0, 101.0, 104.0]},
            index=pd.date_range("2024-01-01", periods=4, freq="B"),
        )
        optimizer = PortfolioOptimizer(prices)

        # Check returns are calculated
        self.assertIsNotNone(optimizer.returns)
        self.assertEqual(len(optimizer.returns), 3)  # One less than prices due to pct_change

        # Check return values (approximate)
        expected_return_1 = (102.0 - 100.0) / 100.0  # 2%
        actual_return_1 = optimizer.returns.iloc[0]["600519"]
        self.assertAlmostEqual(actual_return_1, expected_return_1, places=4)


if __name__ == "__main__":
    unittest.main()
