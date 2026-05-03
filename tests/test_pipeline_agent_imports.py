# -*- coding: utf-8 -*-
"""Test that pipeline_agent.py can import required agent modules."""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

class TestPipelineAgentImports(unittest.TestCase):
    """Test imports needed for TechnicalAgent and IntelAgent integration."""

    @patch('src.agent.factory.get_tool_registry')
    @patch('src.agent.llm_adapter.LLMToolAdapter')
    def test_imports_available(self, mock_adapter, mock_registry):
        """Test that all required modules can be imported."""
        # Test that we can import the agents
        from src.agent.agents.technical_agent import TechnicalAgent
        from src.agent.agents.intel_agent import IntelAgent
        from src.agent.protocols import AgentContext, AgentOpinion
        from src.agent.tools.registry import ToolRegistry
        from src.agent.llm_adapter import LLMToolAdapter

        # Verify the classes exist
        self.assertTrue(callable(TechnicalAgent))
        self.assertTrue(callable(IntelAgent))
        self.assertTrue(callable(AgentContext))
        self.assertTrue(callable(AgentOpinion))


if __name__ == '__main__':
    unittest.main()
