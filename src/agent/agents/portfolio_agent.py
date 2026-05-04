# -*- coding: utf-8 -*-
"""
PortfolioAgent — analyses a *set* of stocks as a whole portfolio,
rather than one-by-one.

Responsibilities:
- Position sizing suggestions (equal-weight / volatility-adjusted)
- Correlation & sector concentration warnings
- Portfolio-level risk metrics (beta, drawdown, sector exposure)
- Cross-market linkage (A-share ↔ HK ↔ US spillover)

The PortfolioAgent consumes pre-computed per-stock opinions
(from the normal orchestrator pipeline) and overlays portfolio
analytics.

Typical usage::

    from src.agent.agents.portfolio_agent import PortfolioAgent
    agent = PortfolioAgent(model=model, registry=registry)
    result = await agent.run(ctx)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.quantitative.portfolio_optimizer import PortfolioOptimizer
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)


class PortfolioAgent(BaseAgent):
    """Portfolio-level analysis agent.

    This agent operates *after* per-stock analysis is already done.
    It reads per-stock opinions from ``ctx.data["stock_opinions"]``
    (a dict of stock_code → opinion) and produces a portfolio-level
    assessment.
    """

    agent_name = "portfolio"
    description = "Portfolio-level risk and allocation analysis"

    tool_names = [
        "get_realtime_quote",
        "get_stock_info",
    ]

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            "You are a professional **portfolio analyst** specializing in "
            "multi-asset allocation for A-share, HK, and US equity portfolios.\n\n"
            "## Your task\n"
            "Given individual stock analysis opinions, produce a **Portfolio Assessment** "
            "that covers:\n"
            "1. **Position Sizing** — suggested weight per stock (equal-weight baseline, "
            "adjusted by conviction and volatility).\n"
            "2. **Sector Concentration** — warn if > 40% in one sector.\n"
            "3. **Correlation Risk** — flag highly correlated pairs.\n"
            "4. **Cross-Market Linkage** — note HK/US spill-over effects on A-shares.\n"
            "5. **Portfolio Risk Score** — 1-10 scale.\n"
            "6. **Rebalance Suggestions** — trim/add recommendations.\n\n"
            "## Output format\n"
            "Return a single JSON object (no markdown fences):\n"
            "{\n"
            '  "portfolio_risk_score": 6,\n'
            '  "total_stocks": 5,\n'
            '  "positions": [\n'
            '    {"code": "600519", "suggested_weight": 0.25, "signal": "buy", "note": "..."},\n'
            "    ...\n"
            "  ],\n"
            '  "sector_warnings": ["Consumer sector > 40%"],\n'
            '  "correlation_warnings": ["600519 & 000858 high correlation"],\n'
            '  "cross_market_notes": ["US tariff risk may impact export-heavy positions"],\n'
            '  "rebalance_suggestions": ["Trim 000858, add defensive sector exposure"],\n'
            '  "summary": "Portfolio is moderately concentrated ..."\n'
            "}\n"
        )

    def build_user_message(self, ctx: AgentContext) -> str:
        # Gather per-stock opinions from context
        stock_opinions = ctx.data.get("stock_opinions", {})
        stock_list = ctx.data.get("stock_list", [])

        parts = [f"Analyze the following portfolio of {len(stock_list) or len(stock_opinions)} stocks:\n"]

        if stock_opinions:
            for code, opinion in stock_opinions.items():
                if isinstance(opinion, AgentOpinion):
                    parts.append(
                        f"- **{code}**: signal={opinion.signal}, "
                        f"confidence={opinion.confidence:.0%}, "
                        f"summary={opinion.reasoning[:200]}"
                    )
                elif isinstance(opinion, dict):
                    parts.append(
                        f"- **{code}**: signal={opinion.get('signal', 'unknown')}, "
                        f"confidence={opinion.get('confidence', 'N/A')}, "
                        f"summary={str(opinion.get('summary', ''))[:200]}"
                    )
        elif stock_list:
            for code in stock_list:
                parts.append(f"- {code}")

        # Include risk flags if any
        if ctx.risk_flags:
            parts.append("\n### Risk Flags from Individual Analysis:")
            for flag in ctx.risk_flags:
                parts.append(f"- ⚠️ {flag}")

        if ctx.query:
            parts.append(f"\nUser request: {ctx.query}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Quantitative analysis helpers
    # ------------------------------------------------------------------

    def _fetch_historical_prices(self, ctx: AgentContext) -> Optional[Dict[str, List[Tuple[str, float]]]]:
        """Fetch historical close prices for all stocks in the portfolio.

        Returns:
            {stock_code: [(date_str, close_price), ...]} or None if failed.
        """
        stock_opinions = ctx.data.get("stock_opinions", {})
        stock_list = ctx.data.get("stock_list", [])
        codes = list(stock_opinions.keys()) if stock_opinions else stock_list

        if not codes:
            logger.warning("[PortfolioAgent] No stocks found for quantitative analysis")
            return None

        # Try to get prices from context first (already fetched)
        price_dict: Dict[str, List[Tuple[str, float]]] = {}
        for code in codes:
            prices_key = f"{code}_daily_prices"
            if prices_key in ctx.data:
                price_dict[code] = ctx.data[prices_key]
            else:
                # Fetch via DataFetcherManager
                try:
                    from data_provider.manager import DataFetcherManager
                    manager = DataFetcherManager()
                    df, _ = manager.get_daily_data_sync(code, days=252)
                    if df is not None and not df.empty and "close" in df.columns:
                        prices = [
                            (row.name.strftime("%Y-%m-%d"), float(row["close"]))
                            for _, row in df.iterrows()
                            if pd.notna(row.get("close"))
                        ]
                        price_dict[code] = prices
                except Exception as e:
                    logger.warning("[PortfolioAgent] Failed to fetch prices for %s: %s", code, e)

        if not price_dict:
            logger.warning("[PortfolioAgent] No historical price data available")
            return None

        return price_dict

    def _compute_quantitative_metrics(self, ctx: AgentContext) -> Dict[str, Any]:
        """Run MPT and Risk Parity calculations.

        Returns:
            Dict with "mpt_analysis" and "risk_parity" keys, or {"error": ...}
        """
        price_dict = self._fetch_historical_prices(ctx)
        if price_dict is None:
            return {"error": "No historical price data available"}

        optimizer = PortfolioOptimizer.from_price_dict(price_dict)
        if optimizer is None:
            return {"error": "Failed to create optimizer from price data"}

        result: Dict[str, Any] = {}
        mpt_result = optimizer.compute_mpt()
        if "error" not in mpt_result:
            result["mpt_analysis"] = mpt_result
        else:
            result["mpt_error"] = mpt_result["error"]

        rp_result = optimizer.compute_risk_parity()
        if "error" not in rp_result:
            result["risk_parity"] = rp_result
        else:
            result["risk_parity_error"] = rp_result["error"]

        return result

    def post_process(self, ctx: AgentContext, raw_response: str) -> Optional[AgentOpinion]:
        """Extract portfolio assessment and store in context."""
        data = try_parse_json(raw_response)
        llm_success = data is not None

        if not llm_success:
            logger.debug("[PortfolioAgent] post_process: failed to parse JSON")
            data = {"raw": raw_response[:1000]}

        # Store portfolio assessment in context
        ctx.data["portfolio_assessment"] = data

        # Run quantitative analysis (MPT + Risk Parity)
        quant_result = self._compute_quantitative_metrics(ctx)
        if quant_result and "error" not in quant_result:
            data["quantitative"] = quant_result
            ctx.data["portfolio_quantitative"] = quant_result
            logger.info("[PortfolioAgent] Quantitative analysis completed")
        else:
            logger.warning(
                "[PortfolioAgent] Quantitative analysis skipped or failed: %s",
                quant_result.get("error") if quant_result else "unknown error",
            )

        # Determine signal from LLM analysis or use default
        if llm_success:
            risk_score = data.get("portfolio_risk_score", 5)
            signal = "hold"
            if risk_score <= 3:
                signal = "buy"
            elif risk_score >= 7:
                signal = "sell"
            reasoning = data.get("summary", raw_response[:300])
        else:
            signal = "hold"
            reasoning = raw_response[:500]

        return AgentOpinion(
            agent_name="portfolio",
            signal=signal,
            confidence=0.6 if llm_success else 0.3,
            reasoning=reasoning,
            raw_data=data,
        )
