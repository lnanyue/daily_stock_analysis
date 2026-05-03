# -*- coding: utf-8 -*-
"""
Portfolio optimization using Modern Portfolio Theory (MPT) and Risk Parity.

Provides quantitative portfolio analysis that complements LLM-based qualitative analysis.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PortfolioOptimizer:
    """MPT and Risk Parity quantitative computation engine."""

    def __init__(
        self,
        historical_prices: pd.DataFrame,
        risk_free_rate: float = 0.0,
    ):
        """
        Args:
            historical_prices: DataFrame with index=date, columns=stock_code, values=close price
            risk_free_rate: Annual risk-free rate (default 0.0)
        """
        self.prices = historical_prices
        self.risk_free_rate = risk_free_rate
        self.returns: pd.DataFrame = historical_prices.pct_change().dropna()

    def compute_mpt(self) -> Dict[str, Any]:
        """Compute MPT metrics and optimal portfolios using PyPortfolioOpt."""
        try:
            from pypfopt import EfficientFrontier, risk_models, expected_returns
        except ImportError:
            logger.error("[PortfolioOptimizer] PyPortfolioOpt not installed")
            return {"error": "PyPortfolioOpt not installed"}

        if len(self.returns) < 2 or len(self.returns.columns) < 1:
            return {"error": "Insufficient data for MPT calculation"}

        try:
            mu = expected_returns.mean_historical_return(self.prices)
            S = risk_models.sample_cov(self.prices)

            ef = EfficientFrontier(mu, S)

            max_sharpe_weights = ef.max_sharpe(self.risk_free_rate)
            ef2 = EfficientFrontier(mu, S)
            min_vol_weights = ef2.min_volatility()

            return {
                "expected_returns": self._series_to_dict(mu),
                "covariance_matrix": self._df_to_dict(S),
                "max_sharpe_weights": dict(max_sharpe_weights),
                "min_volatility_weights": dict(min_vol_weights),
                "efficient_frontier": self._get_efficient_frontier(mu, S),
            }
        except Exception as e:
            logger.error("[PortfolioOptimizer] MPT computation failed: %s", e)
            return {"error": str(e)}

    def compute_risk_parity(
        self, target_risk_contribution: str = "equal"
    ) -> Dict[str, Any]:
        """Compute Risk Parity weights using PyPortfolioOpt."""
        try:
            from pypfopt.risk_parity import RiskParityPortfolio
        except ImportError:
            logger.error("[PortfolioOptimizer] PyPortfolioOpt not installed")
            return {"error": "PyPortfolioOpt not installed"}

        if len(self.returns) < 2 or len(self.returns.columns) < 1:
            return {"error": "Insufficient data for Risk Parity calculation"}

        try:
            cov_matrix = self.returns.cov()

            rp = RiskParityPortfolio(
                cov_matrix=cov_matrix,
                target_risk_contribution=target_risk_contribution,
            )
            rp.weights = rp.solve()

            return {
                "risk_parity_weights": dict(zip(self.returns.columns, rp.weights)),
                "risk_contributions": dict(zip(self.returns.columns, rp.risk_contributions)),
                "portfolio_volatility": float(rp.portfolio_volatility),
            }
        except Exception as e:
            logger.error("[PortfolioOptimizer] Risk Parity computation failed: %s", e)
            return {"error": str(e)}

    def _get_efficient_frontier(
        self,
        mu: pd.Series,
        S: pd.DataFrame,
        points: int = 50,
    ) -> List[Dict[str, float]]:
        """Generate efficient frontier data points using efficient_return."""
        try:
            from pypfopt import EfficientFrontier
        except ImportError:
            return []

        try:
            ef = EfficientFrontier(mu, S)
            # Calculate return range
            min_ret = float(mu.min())
            max_ret = float(mu.max())
            if min_ret >= max_ret:
                return []
            # Generate points along the efficient frontier
            step = (max_ret - min_ret) / (points + 1)
            frontier = []
            for i in range(1, points + 1):
                target = min_ret + step * i
                try:
                    ef.efficient_return(target_return=target)
                    ret, vol, _ = ef.portfolio_performance(verbose=False)
                    frontier.append({"return": float(ret), "volatility": float(vol)})
                except Exception:
                    continue  # Skip infeasible targets
            return frontier
        except Exception as e:
            logger.warning("[PortfolioOptimizer] Efficient frontier generation failed: %s", e)
            return []

    @staticmethod
    def _series_to_dict(series: pd.Series) -> Dict[str, float]:
        """Convert pandas Series to dict with Python native types."""
        return {str(k): float(v) for k, v in series.items()}

    @staticmethod
    def _df_to_dict(df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
        """Convert pandas DataFrame to nested dict with Python native types."""
        return {
            str(i): {str(j): float(df.loc[i, j]) for j in df.columns}
            for i in df.index
        }

    @classmethod
    def from_price_dict(
        cls,
        price_dict: Dict[str, List[Tuple[str, float]]],
        risk_free_rate: float = 0.0,
    ) -> Optional[PortfolioOptimizer]:
        """Create optimizer from dict of {stock_code: [(date, close), ...]}.

        Args:
            price_dict: {stock_code: [(date_str, close_price), ...]}
            risk_free_rate: Annual risk-free rate
        """
        try:
            dfs = []
            for stock_code, price_list in price_dict.items():
                df = pd.DataFrame(price_list, columns=["date", "close"])
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
                df = df.rename(columns={"close": stock_code})
                dfs.append(df)

            if not dfs:
                return None

            prices = pd.concat(dfs, axis=1).sort_index()
            prices = prices.dropna(how="all")

            if prices.empty or len(prices.columns) < 1:
                return None

            return cls(prices, risk_free_rate)
        except Exception as e:
            logger.error("[PortfolioOptimizer] Failed to create from price dict: %s", e)
            return None
