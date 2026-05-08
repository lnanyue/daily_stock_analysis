# -*- coding: utf-8 -*-
"""
Tests for agent-mode pipeline integration.

Covers:
- Config: agent_mode, agent_max_steps, agent_skills fields
- _analyze_with_agent method
- _agent_result_to_analysis_result conversion
- YAML strategy loading (load_builtin_strategies)
"""

import asyncio
import json
import importlib
import types
import unittest
import sys
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _builtin_strategy_names() -> set[str]:
    strategies_dir = Path(__file__).resolve().parent.parent / "strategies"
    return {path.stem for path in strategies_dir.glob("*.yaml")}


# ============================================================
# Config tests
# ============================================================

class TestAgentConfig(unittest.TestCase):
    """Test agent-related configuration fields load correctly."""

    @patch.dict(os.environ, {}, clear=True)
    @patch('src.config.load_dotenv')
    def test_default_agent_config(self, _mock_dotenv):
        """Agent mode should be disabled by default."""
        from src.config import Config
        Config._instance = None
        config = Config._load_from_env()
        self.assertEqual(config.agent_litellm_model, "")
        self.assertFalse(config.agent_mode)
        self.assertFalse(config.agent_auto_route_analysis)
        self.assertEqual(config.agent_max_steps, 10)
        self.assertEqual(config.agent_skills, [])

    @patch.dict(os.environ, {
        'AGENT_MODE': 'true',
        'AGENT_AUTO_ROUTE_ANALYSIS': 'true',
        'AGENT_MAX_STEPS': '15',
        'AGENT_SKILLS': 'dragon_head,shrink_pullback,volume_breakout',
    }, clear=True)
    def test_agent_config_from_env(self):
        """Agent config should be loaded from environment."""
        from src.config import Config
        Config._instance = None
        config = Config._load_from_env()
        self.assertTrue(config.agent_mode)
        self.assertTrue(config.agent_auto_route_analysis)
        self.assertEqual(config.agent_max_steps, 15)
        self.assertEqual(config.agent_skills, ['dragon_head', 'shrink_pullback', 'volume_breakout'])

    @patch.dict(os.environ, {'AGENT_MODE': 'false'}, clear=True)
    def test_agent_mode_disabled(self):
        """Explicitly disabled agent mode."""
        from src.config import Config
        Config._instance = None
        config = Config._load_from_env()
        self.assertFalse(config.agent_mode)

    @patch.dict(os.environ, {'AGENT_SKILLS': ''}, clear=True)
    def test_empty_skills_list(self):
        """Empty AGENT_SKILLS should produce empty list."""
        from src.config import Config
        Config._instance = None
        config = Config._load_from_env()
        self.assertEqual(config.agent_skills, [])

    @patch.dict(os.environ, {'AGENT_SKILLS': '  dragon_head , shrink_pullback  '}, clear=True)
    def test_skills_whitespace_handling(self):
        """Skills should have whitespace trimmed."""
        from src.config import Config
        Config._instance = None
        config = Config._load_from_env()
        self.assertEqual(config.agent_skills, ['dragon_head', 'shrink_pullback'])

    @patch.dict(os.environ, {'AGENT_LITELLM_MODEL': 'gpt-4o-mini'}, clear=True)
    def test_agent_is_available_when_agent_primary_model_is_configured(self):
        """Agent availability auto-detection should use effective Agent primary model."""
        from src.config import Config
        Config._instance = None
        config = Config._load_from_env()
        self.assertEqual(config.agent_litellm_model, 'openai/gpt-4o-mini')
        self.assertTrue(config.is_agent_available())


class TestAgentFactorySkillBaseline(unittest.TestCase):
    """Ensure explicit skill selection does not silently re-apply the default bull-trend baseline."""

    @staticmethod
    def _make_skill(
        name: str,
        *,
        default_active: bool = False,
        default_priority: int = 100,
        source: str = "builtin",
    ):
        return SimpleNamespace(
            name=name,
            display_name=name,
            description=f"{name} desc",
            instructions=f"{name} instructions",
            default_active=default_active,
            default_router=default_active,
            default_priority=default_priority,
            user_invocable=True,
            source=source,
        )

    def _run_factory_case(self, config, *, request_skills, skill_catalog, instructions):
        skill_manager = MagicMock()
        skill_manager.list_skills.return_value = skill_catalog
        skill_manager.get_skill_instructions.return_value = instructions

        fake_llm_module = types.ModuleType("src.agent.llm_adapter")
        fake_llm_module.LLMToolAdapter = MagicMock(return_value=MagicMock())
        fake_executor_module = types.ModuleType("src.agent.executor")
        fake_executor_cls = MagicMock(return_value=MagicMock())
        fake_executor_module.AgentExecutor = fake_executor_cls

        with patch.dict(sys.modules, {
            "litellm": MagicMock(),
            "src.agent.llm_adapter": fake_llm_module,
            "src.agent.executor": fake_executor_module,
        }):
            factory_module = importlib.import_module("src.agent.factory")

            with patch.object(factory_module, "get_skill_manager", return_value=skill_manager), \
                 patch.object(factory_module, "get_tool_registry", return_value=MagicMock()):
                factory_module.build_agent_executor(config, skills=request_skills)

        return fake_executor_cls.call_args.kwargs, skill_manager

    def test_explicit_request_disables_default_skill_policy(self):
        config = SimpleNamespace(
            agent_arch="single",
            agent_skills=[],
            agent_max_steps=10,
            agent_orchestrator_timeout_s=600,
        )
        kwargs, skill_manager = self._run_factory_case(
            config,
            request_skills=["chan_theory"],
            skill_catalog=[
                self._make_skill("bull_trend", default_active=True, default_priority=10),
                self._make_skill("chan_theory", default_priority=20),
            ],
            instructions="chan_theory instructions",
        )

        self.assertEqual(kwargs["default_skill_policy"], "")
        self.assertFalse(kwargs["use_legacy_default_prompt"])
        skill_manager.activate.assert_called_once_with(["chan_theory"])

    def test_configured_skills_disable_default_skill_policy(self):
        config = SimpleNamespace(
            agent_arch="single",
            agent_skills=["wave_theory"],
            agent_max_steps=10,
            agent_orchestrator_timeout_s=600,
        )
        kwargs, skill_manager = self._run_factory_case(
            config,
            request_skills=None,
            skill_catalog=[
                self._make_skill("bull_trend", default_active=True, default_priority=10),
                self._make_skill("wave_theory", default_priority=20),
            ],
            instructions="wave_theory instructions",
        )

        self.assertEqual(kwargs["default_skill_policy"], "")
        self.assertFalse(kwargs["use_legacy_default_prompt"])
        skill_manager.activate.assert_called_once_with(["wave_theory"])

    def test_implicit_default_run_keeps_default_skill_policy(self):
        config = SimpleNamespace(
            agent_arch="single",
            agent_skills=[],
            agent_max_steps=10,
            agent_orchestrator_timeout_s=600,
        )
        kwargs, skill_manager = self._run_factory_case(
            config,
            request_skills=None,
            skill_catalog=[self._make_skill("bull_trend", default_active=True, default_priority=10)],
            instructions="bull_trend instructions",
        )

        self.assertIn("严进策略", kwargs["default_skill_policy"])
        self.assertTrue(kwargs["use_legacy_default_prompt"])
        skill_manager.activate.assert_called_once_with(["bull_trend"])

    def test_explicit_empty_request_falls_back_to_primary_default_skill(self):
        config = SimpleNamespace(
            agent_arch="single",
            agent_skills=[],
            agent_max_steps=10,
            agent_orchestrator_timeout_s=600,
        )
        kwargs, skill_manager = self._run_factory_case(
            config,
            request_skills=[],
            skill_catalog=[
                self._make_skill("bull_trend", default_active=True, default_priority=10),
                self._make_skill("chan_theory", default_priority=20),
            ],
            instructions="bull_trend instructions",
        )

        self.assertIn("严进策略", kwargs["default_skill_policy"])
        self.assertTrue(kwargs["use_legacy_default_prompt"])
        skill_manager.activate.assert_called_once_with(["bull_trend"])

    def test_explicit_primary_default_skill_uses_skill_aware_prompt_mode(self):
        config = SimpleNamespace(
            agent_arch="single",
            agent_skills=[],
            agent_max_steps=10,
            agent_orchestrator_timeout_s=600,
        )
        kwargs, skill_manager = self._run_factory_case(
            config,
            request_skills=["bull_trend"],
            skill_catalog=[
                self._make_skill("bull_trend", default_active=True, default_priority=10),
                self._make_skill("chan_theory", default_priority=20),
            ],
            instructions="bull_trend instructions",
        )

        self.assertEqual(kwargs["default_skill_policy"], "")
        self.assertFalse(kwargs["use_legacy_default_prompt"])
        skill_manager.activate.assert_called_once_with(["bull_trend"])

    def test_invalid_configured_skills_fall_back_to_primary_default_skill(self):
        config = SimpleNamespace(
            agent_arch="single",
            agent_skills=["missing_skill"],
            agent_max_steps=10,
            agent_orchestrator_timeout_s=600,
        )
        kwargs, skill_manager = self._run_factory_case(
            config,
            request_skills=None,
            skill_catalog=[
                self._make_skill("bull_trend", default_active=True, default_priority=10),
                self._make_skill("chan_theory", default_priority=20),
            ],
            instructions="bull_trend instructions",
        )

        self.assertIn("严进策略", kwargs["default_skill_policy"])
        self.assertTrue(kwargs["use_legacy_default_prompt"])
        skill_manager.activate.assert_called_once_with(["bull_trend"])

    def test_custom_default_skill_does_not_use_legacy_bull_prompt(self):
        config = SimpleNamespace(
            agent_arch="single",
            agent_skills=[],
            agent_max_steps=10,
            agent_orchestrator_timeout_s=600,
        )
        kwargs, skill_manager = self._run_factory_case(
            config,
            request_skills=None,
            skill_catalog=[
                self._make_skill("custom_default", default_active=True, default_priority=10),
                self._make_skill("bull_trend", default_priority=20),
            ],
            instructions="custom_default instructions",
        )

        self.assertEqual(kwargs["default_skill_policy"], "")
        self.assertFalse(kwargs["use_legacy_default_prompt"])
        skill_manager.activate.assert_called_once_with(["custom_default"])

    def test_custom_bull_trend_override_does_not_use_legacy_prompt(self):
        config = SimpleNamespace(
            agent_arch="single",
            agent_skills=[],
            agent_max_steps=10,
            agent_orchestrator_timeout_s=600,
        )
        kwargs, skill_manager = self._run_factory_case(
            config,
            request_skills=None,
            skill_catalog=[
                self._make_skill(
                    "bull_trend",
                    default_active=True,
                    default_priority=10,
                    source="/tmp/custom-skills/bull_trend.yaml",
                ),
            ],
            instructions="custom bull_trend instructions",
        )

        self.assertEqual(kwargs["default_skill_policy"], "")
        self.assertFalse(kwargs["use_legacy_default_prompt"])
        skill_manager.activate.assert_called_once_with(["bull_trend"])


# TestAgentResultConversion removed — _agent_result_to_analysis_result no longer exists


# ============================================================
# Skill registration in pipeline
# ============================================================

class TestPipelineSkillRegistration(unittest.TestCase):
    """Test built-in strategies load from YAML via SkillManager."""

    def test_load_builtin_strategies(self):
        """SkillManager.load_builtin_strategies() should load all YAML strategies."""
        from src.agent.skills.base import SkillManager

        skill_manager = SkillManager()
        expected = _builtin_strategy_names()
        count = skill_manager.load_builtin_strategies()
        self.assertEqual(count, len(expected))

        skills = skill_manager.list_skills()
        self.assertEqual(len(skills), len(expected))

        names = {s.name for s in skills}
        self.assertEqual(names, expected)

        # All should be disabled by default
        active = skill_manager.list_active_skills()
        self.assertEqual(len(active), 0)

        # All should have source='builtin'
        for s in skills:
            self.assertEqual(s.source, "builtin")


# ============================================================
# Pipeline dual-path routing
# ============================================================

class TestPipelineRouting(unittest.TestCase):
    """Test that analyze_stock routes to agent mode when config.agent_mode is True."""

    def test_agent_mode_routes_to_agent(self):
        """When agent_mode=True, analyze_stock should call _analyze_with_agent."""
        with patch('src.core.pipeline.get_config') as mock_config, \
             patch('src.core.pipeline.get_db'), \
             patch('src.core.pipeline.DataFetcherManager'), \
             patch('src.core.pipeline.GeminiAnalyzer'), \
             patch('src.core.pipeline.NotificationService'), \
             patch('src.core.pipeline.SearchService'):

            mock_cfg = MagicMock()
            mock_cfg.max_workers = 2
            mock_cfg.agent_mode = True
            mock_cfg.agent_max_steps = 5
            mock_cfg.agent_skills = []
            mock_cfg.tavily_api_keys = []
            mock_cfg.news_max_age_days = 7
            mock_cfg.enable_realtime_quote = True
            mock_cfg.enable_chip_distribution = True
            mock_cfg.realtime_source_priority = []
            mock_cfg.save_context_snapshot = False
            mock_config.return_value = mock_cfg

            from src.core.pipeline import StockAnalysisPipeline
            from src.enums import ReportType
            pipeline = StockAnalysisPipeline(config=mock_cfg)

            # Mock executor.analyze to verify it gets called
            pipeline.executor.analyze = AsyncMock(return_value=None)
            pipeline.fetcher_manager.get_stock_name = AsyncMock(return_value="贵州茅台")
            pipeline.fetcher_manager.get_realtime_quote = AsyncMock(return_value=None)
            pipeline.fetcher_manager.get_chip_distribution = AsyncMock(return_value=None)
            pipeline.fetcher_manager.get_fundamental_context = AsyncMock(return_value={})
            pipeline.fetcher_manager.get_market_overview = AsyncMock(return_value={})
            pipeline.db.get_data_range_async = AsyncMock(return_value=[])
            pipeline.search_service.is_available = False

            asyncio.run(pipeline.analyze_stock("600519", ReportType.SIMPLE, "q1"))

            pipeline.executor.analyze.assert_called_once()
            call_args = pipeline.executor.analyze.call_args
            self.assertEqual(call_args[0][0], "600519")
            self.assertEqual(call_args[0][1], ReportType.SIMPLE)
            self.assertEqual(call_args[0][2], "q1")
            # 4th arg should be a StockDataCollectionResult
            from src.core.pipeline_data_collector import StockDataCollectionResult
            self.assertIsInstance(call_args[0][3], StockDataCollectionResult)

    def test_auto_agent_route_escalates_complex_single_stock_runs(self):
        """When auto routing is enabled, complex runs should switch to Agent mode."""
        with patch('src.core.pipeline.get_config') as mock_config, \
             patch('src.core.pipeline.get_db'), \
             patch('src.core.pipeline.DataFetcherManager'), \
             patch('src.core.pipeline.GeminiAnalyzer'), \
             patch('src.core.pipeline.NotificationService'), \
             patch('src.core.pipeline.SearchService'):

            mock_cfg = MagicMock()
            mock_cfg.max_workers = 2
            mock_cfg.agent_mode = False
            mock_cfg.agent_auto_route_analysis = True
            mock_cfg.agent_skills = []
            mock_cfg.tavily_api_keys = []
            mock_cfg.news_max_age_days = 7
            mock_cfg.enable_realtime_quote = True
            mock_cfg.enable_chip_distribution = True
            mock_cfg.realtime_source_priority = []
            mock_cfg.save_context_snapshot = False
            mock_cfg.is_agent_available.return_value = True
            mock_config.return_value = mock_cfg

            from src.core.pipeline import StockAnalysisPipeline
            from src.enums import ReportType
            pipeline = StockAnalysisPipeline(config=mock_cfg)

            pipeline.executor.analyze = AsyncMock(return_value=None)
            pipeline.fetcher_manager.get_stock_name = AsyncMock(return_value="贵州茅台")
            pipeline.fetcher_manager.get_realtime_quote = AsyncMock(return_value=None)
            pipeline.fetcher_manager.get_chip_distribution = AsyncMock(return_value=None)
            pipeline.fetcher_manager.get_fundamental_context = AsyncMock(return_value={})
            pipeline.fetcher_manager.get_market_overview = AsyncMock(return_value={})
            pipeline.db.get_data_range_async = AsyncMock(return_value=[])
            pipeline.search_service.is_available = False

            asyncio.run(pipeline.analyze_stock("600519", ReportType.SIMPLE, "q-auto"))

            pipeline.executor.analyze.assert_called_once()
            args, kwargs = pipeline.executor.analyze.call_args
            self.assertEqual(args[0], "600519")

    def test_legacy_mode_still_routes_through_unified_analysis(self):
        """When agent_mode=False, analyze_stock still calls _analyze_with_agent (unified entry point)."""
        with patch('src.core.pipeline.get_config') as mock_config, \
             patch('src.core.pipeline.get_db') as mock_db, \
             patch('src.core.pipeline.DataFetcherManager') as mock_fm, \
             patch('src.core.pipeline.GeminiAnalyzer') as mock_analyzer, \
             patch('src.core.pipeline.NotificationService'), \
             patch('src.core.pipeline.SearchService') as mock_search:

            mock_cfg = MagicMock()
            mock_cfg.max_workers = 2
            mock_cfg.agent_mode = False
            mock_cfg.is_agent_available.return_value = False
            mock_cfg.agent_max_steps = 10
            mock_cfg.agent_skills = []
            mock_cfg.tavily_api_keys = []
            mock_cfg.news_max_age_days = 7
            mock_cfg.enable_realtime_quote = True
            mock_cfg.enable_chip_distribution = True
            mock_cfg.realtime_source_priority = []
            mock_cfg.save_context_snapshot = False
            mock_config.return_value = mock_cfg

            from src.core.pipeline import StockAnalysisPipeline
            from src.enums import ReportType
            pipeline = StockAnalysisPipeline(config=mock_cfg)

            # Mock the fetcher_manager to return None for realtime
            pipeline.fetcher_manager.get_realtime_quote.return_value = None
            pipeline.fetcher_manager.get_chip_distribution.return_value = None
            pipeline.fetcher_manager.get_stock_name = AsyncMock(return_value="贵州茅台")
            pipeline.fetcher_manager.get_realtime_quote = AsyncMock(return_value=None)
            pipeline.fetcher_manager.get_chip_distribution = AsyncMock(return_value=None)
            pipeline.fetcher_manager.get_fundamental_context = AsyncMock(return_value={})
            pipeline.fetcher_manager.get_market_overview = AsyncMock(return_value={})
            # Mock search service
            pipeline.search_service.is_available = False
            # Mock DB context
            pipeline.db.get_analysis_context.return_value = None
            pipeline.db.get_data_range_async = AsyncMock(return_value=[])
            pipeline.db.save_analysis_history_async = AsyncMock()
            # Mock executor
            pipeline.executor.analyze = AsyncMock(return_value=None)

            asyncio.run(pipeline.analyze_stock("600519", ReportType.SIMPLE, "q1"))

            pipeline.executor.analyze.assert_called_once()


# TestAnalyzeWithAgentStockName removed — _analyze_with_agent refactored to multi-agent path

class TestAgentConstructionChain(unittest.TestCase):
    """Test that the agent construction chain wires up correctly."""

    def test_llm_adapter_accepts_config(self):
        """LLMToolAdapter should accept an optional config parameter."""
        mock_cfg = MagicMock()
        mock_cfg.gemini_api_key = ""
        mock_cfg.anthropic_api_key = ""
        mock_cfg.openai_api_key = ""
        mock_cfg.openai_base_url = ""
        mock_cfg.openai_model = ""

        from src.agent.llm_adapter import LLMToolAdapter
        adapter = LLMToolAdapter(config=mock_cfg)
        self.assertIsNotNone(adapter)

    def test_llm_adapter_no_args(self):
        """LLMToolAdapter should also work with no arguments (uses get_config)."""
        with patch('src.agent.llm_adapter.get_config') as mock_get_config:
            mock_cfg = MagicMock()
            mock_cfg.gemini_api_key = ""
            mock_cfg.anthropic_api_key = ""
            mock_cfg.openai_api_key = ""
            mock_cfg.openai_base_url = ""
            mock_cfg.openai_model = ""
            mock_get_config.return_value = mock_cfg

            from src.agent.llm_adapter import LLMToolAdapter
            adapter = LLMToolAdapter()
            self.assertIsNotNone(adapter)

    def test_full_construction_chain(self):
        """Test ToolRegistry + SkillManager + LLMToolAdapter + AgentExecutor wiring."""
        from src.agent.tools.registry import ToolRegistry, ToolDefinition, ToolParameter
        from src.agent.skills.base import SkillManager, Skill
        from src.agent.llm_adapter import LLMToolAdapter
        from src.agent.executor import AgentExecutor

        # Build registry with a dummy tool
        registry = ToolRegistry()

        def dummy_handler(x: str) -> str:
            return f"echo {x}"

        dummy_tool = ToolDefinition(
            name="dummy_echo",
            description="A test tool for echoing input.",
            category="test",
            parameters=[ToolParameter(name="x", type="string", description="input string", required=True)],
            handler=dummy_handler,
        )
        registry.register(dummy_tool)

        # Build skill manager with a fresh skill instance (avoid module singleton state)
        skill_manager = SkillManager()
        test_skill = Skill(
            name="test_skill",
            display_name="测试策略",
            description="A test skill",
            instructions="Test instructions for analysis.",
            category="trend",
            core_rules=[1, 2],
        )
        skill_manager.register(test_skill)
        skill_manager.activate(["test_skill"])
        instructions = skill_manager.get_skill_instructions()
        self.assertIn("测试策略", instructions)

        # Build LLM adapter with mocked config (no real API keys)
        mock_cfg = MagicMock()
        mock_cfg.gemini_api_key = ""
        mock_cfg.anthropic_api_key = ""
        mock_cfg.openai_api_key = ""
        mock_cfg.openai_base_url = ""
        mock_cfg.openai_model = ""
        adapter = LLMToolAdapter(config=mock_cfg)

        # Build executor
        executor = AgentExecutor(
            tool_registry=registry,
            llm_adapter=adapter,
            skill_instructions=instructions,
            max_steps=3,
        )
        self.assertEqual(executor.max_steps, 3)
        self.assertIsNotNone(executor.tool_registry)
        self.assertIsNotNone(executor.llm_adapter)

    @patch("src.agent.llm_adapter.Router")
    def test_llm_adapter_call_completion_uses_effective_agent_models_order(self, _mock_router):
        """call_completion should use Agent effective model chain in order."""
        mock_cfg = MagicMock()
        mock_cfg.agent_litellm_model = "gpt-4o-mini"
        mock_cfg.litellm_model = "gemini/gemini-2.5-flash"
        mock_cfg.litellm_fallback_models = ["openai/gpt-4o-mini", "anthropic/claude-3-5-sonnet-20241022"]
        mock_cfg.llm_model_list = []
        mock_cfg.llm_temperature = 0.7
        mock_cfg.gemini_api_keys = []
        mock_cfg.anthropic_api_keys = []
        mock_cfg.openai_api_keys = []
        mock_cfg.deepseek_api_keys = []
        mock_cfg.openai_base_url = None

        from src.agent.llm_adapter import LLMToolAdapter
        adapter = LLMToolAdapter(config=mock_cfg)

        calls = []

        def fake_call(_messages, _tools, model, **_kwargs):
            calls.append(model)
            if model == "openai/gpt-4o-mini":
                raise RuntimeError("primary failed")
            return MagicMock(content="ok")

        adapter._call_litellm_model = MagicMock(side_effect=fake_call)

        result = adapter.call_completion(messages=[{"role": "user", "content": "hi"}], tools=[])

        self.assertEqual(calls, ["openai/gpt-4o-mini", "anthropic/claude-3-5-sonnet-20241022"])
        self.assertEqual(result.content, "ok")

    @patch("src.agent.llm_adapter.Router")
    def test_llm_adapter_recomputes_timeout_for_each_fallback_attempt(self, _mock_router):
        """Each fallback model attempt should receive only the remaining timeout budget."""
        mock_cfg = MagicMock()
        mock_cfg.agent_litellm_model = "gpt-4o-mini"
        mock_cfg.litellm_model = None
        mock_cfg.litellm_fallback_models = ["anthropic/claude-3-5-sonnet-20241022"]
        mock_cfg.llm_model_list = []
        mock_cfg.llm_temperature = 0.7
        mock_cfg.gemini_api_keys = []
        mock_cfg.anthropic_api_keys = []
        mock_cfg.openai_api_keys = []
        mock_cfg.deepseek_api_keys = []
        mock_cfg.openai_base_url = None

        from src.agent.llm_adapter import LLMToolAdapter
        adapter = LLMToolAdapter(config=mock_cfg)

        timeouts = []

        def fake_call(_messages, _tools, model, **kwargs):
            timeouts.append((model, kwargs.get("timeout")))
            if model == "openai/gpt-4o-mini":
                raise RuntimeError("primary failed")
            return MagicMock(content="ok")

        adapter._call_litellm_model = MagicMock(side_effect=fake_call)

        with patch("src.agent.llm_adapter.time.time", side_effect=[0.0, 0.0, 7.0, 7.0]):
            result = adapter.call_completion(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                timeout=10.0,
            )

        self.assertEqual(result.content, "ok")
        self.assertEqual(timeouts[0], ("openai/gpt-4o-mini", 10.0))
        self.assertEqual(timeouts[1], ("anthropic/claude-3-5-sonnet-20241022", 3.0))

    @patch("src.agent.llm_adapter.Router")
    def test_llm_adapter_rate_limit_backoff_is_bounded_by_remaining_timeout(self, _mock_router):
        """Rate-limit backoff should sleep, but never longer than the remaining timeout budget."""
        mock_cfg = MagicMock()
        mock_cfg.agent_litellm_model = "gpt-4o-mini"
        mock_cfg.litellm_model = None
        mock_cfg.litellm_fallback_models = ["openai/gpt-4.1-mini"]
        mock_cfg.llm_model_list = []
        mock_cfg.llm_temperature = 0.7
        mock_cfg.gemini_api_keys = []
        mock_cfg.anthropic_api_keys = []
        mock_cfg.openai_api_keys = []
        mock_cfg.deepseek_api_keys = []
        mock_cfg.openai_base_url = None

        from src.agent.llm_adapter import LLMToolAdapter
        adapter = LLMToolAdapter(config=mock_cfg)

        class FakeRateLimitError(Exception):
            pass

        timeouts = []
        sleep_calls = []
        clock = {"value": 0.0}

        def fake_time():
            return clock["value"]

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            clock["value"] += seconds

        def fake_call(_messages, _tools, model, **kwargs):
            timeouts.append((model, kwargs.get("timeout")))
            if model == "openai/gpt-4o-mini":
                clock["value"] += 8.0
                raise FakeRateLimitError("rate limited")
            return MagicMock(content="ok")

        adapter._call_litellm_model = MagicMock(side_effect=fake_call)

        with patch("src.agent.llm_adapter.litellm.RateLimitError", FakeRateLimitError), \
             patch("src.agent.llm_adapter.logger.warning"), \
             patch("src.agent.llm_adapter.time.time", side_effect=fake_time), \
             patch("src.agent.llm_adapter.time.sleep", side_effect=fake_sleep) as mock_sleep:
            result = adapter.call_completion(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                timeout=10.0,
            )

        self.assertEqual(result.content, "ok")
        self.assertEqual(timeouts[0], ("openai/gpt-4o-mini", 10.0))
        self.assertEqual(timeouts[1][0], "openai/gpt-4.1-mini")
        expected_backoff = min(2.0, 8.0 * 0.1 + 0.5)
        expected_next_timeout = 10.0 - (8.0 + expected_backoff)
        self.assertAlmostEqual(timeouts[1][1], expected_next_timeout)
        mock_sleep.assert_called_once()
        self.assertAlmostEqual(mock_sleep.call_args.args[0], expected_backoff)
        self.assertAlmostEqual(sleep_calls[0], expected_backoff)
        self.assertAlmostEqual(clock["value"], 8.0 + expected_backoff)

    @patch("src.agent.llm_adapter.Router")
    def test_llm_adapter_context_window_error_skips_sleep(self, _mock_router):
        """Context-window errors should continue fallback immediately without backoff."""
        mock_cfg = MagicMock()
        mock_cfg.agent_litellm_model = "gpt-4o-mini"
        mock_cfg.litellm_model = None
        mock_cfg.litellm_fallback_models = ["anthropic/claude-3-5-sonnet-20241022"]
        mock_cfg.llm_model_list = []
        mock_cfg.llm_temperature = 0.7
        mock_cfg.gemini_api_keys = []
        mock_cfg.anthropic_api_keys = []
        mock_cfg.openai_api_keys = []
        mock_cfg.deepseek_api_keys = []
        mock_cfg.openai_base_url = None

        from src.agent.llm_adapter import LLMToolAdapter
        adapter = LLMToolAdapter(config=mock_cfg)

        class FakeContextWindowExceededError(Exception):
            pass

        def fake_call(_messages, _tools, model, **_kwargs):
            if model == "openai/gpt-4o-mini":
                raise FakeContextWindowExceededError("window exceeded")
            return MagicMock(content="ok")

        adapter._call_litellm_model = MagicMock(side_effect=fake_call)

        with patch(
            "src.agent.llm_adapter.litellm.ContextWindowExceededError",
            FakeContextWindowExceededError,
        ), patch("src.agent.llm_adapter.time.sleep") as mock_sleep:
            result = adapter.call_completion(messages=[{"role": "user", "content": "hi"}], tools=[])

        self.assertEqual(result.content, "ok")
        mock_sleep.assert_not_called()

    @patch("src.agent.llm_adapter.Router")
    def test_llm_adapter_reports_rate_limit_suffix_when_any_fallback_hit_limit(self, _mock_router):
        """Final error should note earlier rate limiting even if the last error differs."""
        mock_cfg = MagicMock()
        mock_cfg.agent_litellm_model = "gpt-4o-mini"
        mock_cfg.litellm_model = None
        mock_cfg.litellm_fallback_models = ["anthropic/claude-3-5-sonnet-20241022"]
        mock_cfg.llm_model_list = []
        mock_cfg.llm_temperature = 0.7
        mock_cfg.gemini_api_keys = []
        mock_cfg.anthropic_api_keys = []
        mock_cfg.openai_api_keys = []
        mock_cfg.deepseek_api_keys = []
        mock_cfg.openai_base_url = None

        from src.agent.llm_adapter import LLMToolAdapter
        adapter = LLMToolAdapter(config=mock_cfg)

        class FakeRateLimitError(Exception):
            pass

        class FakeContextWindowExceededError(Exception):
            pass

        def fake_call(_messages, _tools, model, **_kwargs):
            if model == "openai/gpt-4o-mini":
                raise FakeRateLimitError("rate limited")
            raise FakeContextWindowExceededError("window exceeded")

        adapter._call_litellm_model = MagicMock(side_effect=fake_call)

        with patch("src.agent.llm_adapter.litellm.RateLimitError", FakeRateLimitError), \
             patch(
                 "src.agent.llm_adapter.litellm.ContextWindowExceededError",
                 FakeContextWindowExceededError,
             ), \
             patch("src.agent.llm_adapter.time.sleep") as mock_sleep:
            result = adapter.call_completion(messages=[{"role": "user", "content": "hi"}], tools=[])

        self.assertEqual(result.provider, "error")
        self.assertIn("All LLM models failed (rate-limit encountered during fallback).", result.content)
        self.assertIn("window exceeded", result.content)
        mock_sleep.assert_not_called()


# _safe_int tests removed — method no longer exists on StockAnalysisPipeline


# ============================================================
# Skill activation semantics
# ============================================================

class TestSkillActivation(unittest.TestCase):
    """Test that skill activation follows the correct semantics."""

    def test_skills_default_disabled(self):
        """After registration, skills should be disabled by default."""
        from src.agent.skills.base import SkillManager, Skill

        manager = SkillManager()
        # Create a fresh Skill with default enabled=False
        test_skill = Skill(
            name="test_disabled",
            display_name="Test",
            description="test",
            instructions="test",
        )
        manager.register(test_skill)
        active = manager.list_active_skills()
        self.assertEqual(len(active), 0, "Skills should be disabled by default")

    def test_activate_all(self):
        """activate(['all']) should enable all registered skills."""
        from src.agent.skills.base import SkillManager, Skill

        manager = SkillManager()
        # Create test skills instead of importing deleted Python modules
        skill1 = Skill(name="dragon_head", display_name="龙头策略",
                       description="test", instructions="test")
        skill2 = Skill(name="shrink_pullback", display_name="缩量回踩",
                       description="test", instructions="test")
        manager.register(skill1)
        manager.register(skill2)
        manager.activate(["all"])
        active = manager.list_active_skills()
        self.assertEqual(len(active), 2)

    def test_activate_specific(self):
        """activate with specific names should only enable those."""
        from src.agent.skills.base import SkillManager, Skill

        manager = SkillManager()
        skill1 = Skill(name="dragon_head", display_name="龙头策略",
                       description="test", instructions="test")
        skill2 = Skill(name="shrink_pullback", display_name="缩量回踩",
                       description="test", instructions="test")
        skill3 = Skill(name="volume_breakout", display_name="放量突破",
                       description="test", instructions="test")
        manager.register(skill1)
        manager.register(skill2)
        manager.register(skill3)
        manager.activate(["dragon_head"])
        active = manager.list_active_skills()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].name, "dragon_head")

    def test_empty_config_uses_primary_default_skill(self):
        """Empty agent_skills config should activate the primary default skill only."""
        from src.agent.skills.base import SkillManager
        from src.agent.skills.defaults import get_default_active_skill_ids

        skill_manager = SkillManager()
        count = skill_manager.load_builtin_strategies()
        self.assertEqual(count, len(_builtin_strategy_names()), "Should load all built-in strategies from YAML")

        default_ids = get_default_active_skill_ids(skill_manager.list_skills())
        self.assertEqual(default_ids, ["bull_trend"])
        skill_manager.activate(default_ids)

        active = skill_manager.list_active_skills()
        self.assertEqual([skill.name for skill in active], ["bull_trend"])

    # (Dead tests for removed _agent_result_to_analysis_result removed)


# ============================================================
# Hybrid mode tests
# ============================================================

class TestHybridAgentConversion(unittest.TestCase):
    """Test the hybrid agent path (single LLM call over pre-collected data)."""

    def setUp(self):
        self.code = "600519"
        self.stock_name = "贵州茅台"
        self.query_id = "test-hybrid-001"

    def test_dashboard_schema_constant_defined(self):
        """DASHBOARD_OUTPUT_SCHEMA should be a non-empty string with required keys."""
        from src.schemas.analysis_result import DASHBOARD_OUTPUT_SCHEMA
        self.assertIsInstance(DASHBOARD_OUTPUT_SCHEMA, str)
        self.assertGreater(len(DASHBOARD_OUTPUT_SCHEMA.strip()), 200)
        self.assertIn("stock_name", DASHBOARD_OUTPUT_SCHEMA)
        self.assertIn("battle_plan", DASHBOARD_OUTPUT_SCHEMA)
        self.assertIn("sniper_points", DASHBOARD_OUTPUT_SCHEMA)

    def test_format_analysis_prompt_output_format_param(self):
        """format_analysis_prompt should accept output_format parameter."""
        from src.analyzer.prompt_builder import format_analysis_prompt
        prompt_standard = format_analysis_prompt({"code": "600519"}, "测试", output_format="standard")
        prompt_dashboard = format_analysis_prompt({"code": "600519"}, "测试", output_format="dashboard")
        self.assertIsInstance(prompt_standard, str)
        self.assertIsInstance(prompt_dashboard, str)
        # Dashboard prompt should be longer (contains schema)
        self.assertGreater(len(prompt_dashboard), len(prompt_standard))
        # Dashboard prompt should contain the schema intro
        self.assertIn("严格输出格式要求", prompt_dashboard)
        self.assertNotIn("严格输出格式要求", prompt_standard)

    def test_hybrid_result_price_override(self):
        """Hybrid path should override current_price and change_pct from realtime quote."""
        from src.schemas.analysis_result import AnalysisResult

        # Build a mock realtime quote
        rt = SimpleNamespace(price=152.30, change_pct=1.25, name=self.stock_name)

        # Simulate what _analyze_with_agent does: override deterministic fields
        llm_result = AnalysisResult(
            code=self.code, name=self.stock_name,
            sentiment_score=75, trend_prediction="看多",
            operation_advice="买入", decision_type="buy",
            confidence_level="中", current_price=150.0,  # LLM might guess wrong
            change_pct=0.5,
        )

        # Override (simulating Step 5 in hybrid path)
        llm_result.current_price = rt.price
        llm_result.change_pct = rt.change_pct

        self.assertEqual(llm_result.current_price, 152.30)
        self.assertEqual(llm_result.change_pct, 1.25)

    def test_analysis_metadata_structure(self):
        """Hybrid analysis_metadata should have expected structure."""
        from src.schemas.analysis_result import AnalysisResult

        result = AnalysisResult(
            code=self.code, name=self.stock_name,
            sentiment_score=65, trend_prediction="震荡",
            operation_advice="持有", decision_type="hold",
            confidence_level="中",
        )
        # Simulate what hybrid path sets
        model_used = "gemini/gemini-2.0-flash"
        result.analysis_metadata = {
            "agent_route": {
                "used_agent": True,
                "selection_source": "auto",
                "reasons": [],
                "arch": "hybrid",
                "mode": "single",
            },
            "agent_runtime": {
                "arch": "hybrid",
                "success": True,
                "model": model_used,
                "provider": "gemini",
            },
        }

        meta = result.analysis_metadata
        self.assertEqual(meta["agent_route"]["arch"], "hybrid")
        self.assertEqual(meta["agent_route"]["mode"], "single")
        self.assertTrue(meta["agent_route"]["used_agent"])
        self.assertEqual(meta["agent_runtime"]["model"], "gemini/gemini-2.0-flash")
        self.assertEqual(meta["agent_runtime"]["provider"], "gemini")


class TestHybridAgentIntegration(unittest.TestCase):
    """Integration tests for the hybrid agent path (_analyze_with_agent)."""

    def setUp(self):
        self.code = "600519"
        self.stock_name = "贵州茅台"
        self.query_id = "test-hybrid-integration-001"

    @patch("src.core.pipeline.get_config")
    def test_hybrid_analysis_prompt_contains_dashboard_schema(self, mock_config):
        """The LLM prompt should contain DASHBOARD_OUTPUT_SCHEMA when output_format=dashboard."""
        from src.analyzer.prompt_builder import format_analysis_prompt

        context = {"code": "600519", "stock_name": "贵州茅台", "date": "2026-05-04"}
        prompt = format_analysis_prompt(context, "贵州茅台", output_format="dashboard")
        self.assertIn("严格输出格式要求", prompt)
        self.assertIn("sentiment_score", prompt)
        self.assertIn("battle_plan", prompt)


if __name__ == '__main__':
    unittest.main()
