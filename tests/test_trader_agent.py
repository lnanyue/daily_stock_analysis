"""Tests for TraderAgent — LLM call parameter correctness."""
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock


class TestTraderAgentCallLiteLlm(TestCase):
    """TraderAgent._call_litellm_async 不传 timeout。"""

    def test_run_does_not_pass_timeout(self):
        from src.agent.agents.trader_agent import TraderAgent
        from src.agent.protocols import AgentContext

        agent = TraderAgent.__new__(TraderAgent)
        agent.analyzer = MagicMock()
        agent.analyzer._call_litellm_async = AsyncMock(
            return_value=("result", "model", {}),
        )
        agent._post_process = MagicMock(return_value=MagicMock())

        ctx = AgentContext(stock_code="600519", stock_name="test")
        import asyncio
        asyncio.run(agent.run(ctx, timeout_seconds=None))

        call_kwargs = agent.analyzer._call_litellm_async.call_args.kwargs
        self.assertNotIn("timeout", call_kwargs)
