# -*- coding: utf-8 -*-
"""
配置管理包
"""

import os

from dotenv import load_dotenv

from .manager import Config, get_config, get_api_keys_for_model, extra_litellm_params
from .models import ConfigIssue, LLMChannelConfig
from .utils import (
    SUPPORTED_LLM_CHANNEL_PROTOCOLS,
    _get_litellm_provider,
    _uses_direct_env_provider,
    canonicalize_llm_channel_protocol,
    channel_allows_empty_api_key,
    channels_to_model_list,
    parse_env_bool,
    parse_env_int,
    parse_env_float,
    parse_llm_channels,
    setup_env,
    load_stocks_from_yaml,
    load_settings_from_yaml,
    get_configured_llm_models,
    get_effective_agent_primary_model,
    get_effective_agent_models_to_try,
    legacy_keys_to_model_list,
    normalize_agent_litellm_model,
    normalize_llm_channel_model,
    resolve_unified_llm_temperature,
    resolve_news_window_days,
    resolve_llm_channel_protocol,
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
    "SUPPORTED_LLM_CHANNEL_PROTOCOLS",
    "_get_litellm_provider",
    "_uses_direct_env_provider",
    "canonicalize_llm_channel_protocol",
    "channel_allows_empty_api_key",
    "channels_to_model_list",
    "load_dotenv",
    "os",
    "parse_env_bool",
    "parse_env_int",
    "parse_env_float",
    "parse_llm_channels",
    "setup_env",
    "load_stocks_from_yaml",
    "load_settings_from_yaml",
    "get_configured_llm_models",
    "get_effective_agent_primary_model",
    "get_effective_agent_models_to_try",
    "legacy_keys_to_model_list",
    "normalize_agent_litellm_model",
    "normalize_llm_channel_model",
    "resolve_unified_llm_temperature",
    "resolve_news_window_days",
    "resolve_llm_channel_protocol",
    "parse_litellm_yaml",
    "NEWS_STRATEGY_WINDOWS",
    "normalize_news_strategy_profile",
]
