# -*- coding: utf-8 -*-
"""
配置管理包
"""

from .manager import Config, get_config, get_api_keys_for_model, extra_litellm_params
from .models import ConfigIssue, LLMChannelConfig
from .utils import (
    parse_env_bool,
    parse_env_int,
    parse_env_float,
    setup_env,
    load_stocks_from_yaml,
    load_settings_from_yaml,
    get_configured_llm_models,
    get_effective_agent_primary_model,
    get_effective_agent_models_to_try,
    resolve_unified_llm_temperature,
    resolve_news_window_days,
    parse_litellm_yaml,
    NEWS_STRATEGY_WINDOWS,
    normalize_news_strategy_profile,
)

__all__ = [
    "Config",
    "get_config",
    "get_api_keys_for_model",
    "extra_litellm_params",
    "ConfigIssue",
    "LLMChannelConfig",
    "parse_env_bool",
    "parse_env_int",
    "parse_env_float",
    "setup_env",
    "load_stocks_from_yaml",
    "load_settings_from_yaml",
    "get_configured_llm_models",
    "get_effective_agent_primary_model",
    "get_effective_agent_models_to_try",
    "resolve_unified_llm_temperature",
    "resolve_news_window_days",
    "parse_litellm_yaml",
    "NEWS_STRATEGY_WINDOWS",
    "normalize_news_strategy_profile",
]
