# -*- coding: utf-8 -*-
"""
Plugin system — unified plugin registry and interfaces.
"""

from .config import ConfigLoader, resolve_env_refs
from .loader import scan_and_register
from .registry import PluginRegistry
from .strategy_base import AnalysisContext, AnalysisStrategy, StrategyResult
from .plugin_context import PluginContext

__all__ = [
    "ConfigLoader",
    "resolve_env_refs",
    "scan_and_register",
    "PluginRegistry",
    "AnalysisContext",
    "AnalysisStrategy",
    "StrategyResult",
    "PluginContext",
]
