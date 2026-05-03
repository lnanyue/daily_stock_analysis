# -*- coding: utf-8 -*-
"""Test TechnicalAgent and IntelAgent integration in pipeline."""
import asyncio
import unittest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import Optional

sys_path_inserted = False
if not sys_path_inserted:
    import sys
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    sys_path_inserted = True


class TestAgentCreation(unittest.TestCase):
    """Test that agents can be created and run in pipeline context."""

    @patch('src.agent.factory.get_tool_registry')
    @patch('src.agent.llm_adapter.LLMToolAdapter')
    def test_create_technical_agent(self, mock_adapter_cls, mock_get_registry):
        """Test TechnicalAgent can be created with required dependencies."""
        from src.agent.agents.technical_agent import TechnicalAgent
        from src.agent.tools.registry import ToolRegistry

        mock_registry = MagicMock(spec=ToolRegistry)
        mock_get_registry.return_value = mock_registry

        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        agent = TechnicalAgent(
            tool_registry=mock_registry,
            llm_adapter=mock_adapter,
            skill_instructions="",
            technical_skill_policy="",
        )

        self.assertEqual(agent.agent_name, "technical")
        self.assertTrue(len(agent.tool_names) > 0)

    @patch('src.agent.factory.get_tool_registry')
    @patch('src.agent.llm_adapter.LLMToolAdapter')
    def test_create_intel_agent(self, mock_adapter_cls, mock_get_registry):
        """Test IntelAgent can be created with required dependencies."""
        from src.agent.agents.intel_agent import IntelAgent
        from src.agent.tools.registry import ToolRegistry

        mock_registry = MagicMock(spec=ToolRegistry)
        mock_get_registry.return_value = mock_registry

        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        agent = IntelAgent(
            tool_registry=mock_registry,
            llm_adapter=mock_adapter,
        )

        self.assertEqual(agent.agent_name, "intel")
        self.assertTrue(len(agent.tool_names) > 0)

    @patch('src.agent.factory.get_tool_registry')
    @patch('src.agent.llm_adapter.LLMToolAdapter')
    def test_create_agent_context(self, mock_adapter_cls, mock_get_registry):
        """Test AgentContext can be created for agent run."""
        from src.agent.protocols import AgentContext

        ctx = AgentContext(
            stock_code="600519",
            stock_name="贵州茅台",
            query="Analysis for 贵州茅台",
            meta={"report_language": "zh"},
        )

        self.assertEqual(ctx.stock_code, "600519")
        self.assertEqual(len(ctx.opinions), 0)


if __name__ == '__main__':
    unittest.main()
