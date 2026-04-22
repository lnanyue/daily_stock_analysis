# -*- coding: utf-8 -*-
"""
配置解析与辅助工具
"""

import logging
import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

_FALSEY_ENV_VALUES = {"0", "false", "no", "off"}
SUPPORTED_LLM_CHANNEL_PROTOCOLS = ("openai", "anthropic", "gemini", "vertex_ai", "deepseek", "ollama")
NEWS_STRATEGY_WINDOWS: Dict[str, int] = {
    "ultra_short": 1,
    "short": 3,
    "medium": 7,
    "long": 30,
}


def parse_env_bool(value: Any, default: bool = False) -> bool:
    """解析布尔值（支持环境变量字符串或 YAML 布尔对象）"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    return normalized not in _FALSEY_ENV_VALUES


def parse_env_int(
    value: Any,
    default: int,
    *,
    field_name: str,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """解析整数（支持环境变量字符串或 YAML 整数对象）"""
    if isinstance(value, int):
        parsed = value
    elif value is None or not str(value).strip():
        parsed = default
    else:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            logger.warning("%s=%r 无效整数，使用默认值 %s", field_name, value, default)
            parsed = default

    if minimum is not None and parsed < minimum:
        parsed = minimum
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed


def parse_env_float(
    value: Any,
    default: float,
    *,
    field_name: str,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """解析浮点数（支持环境变量字符串或 YAML 数值对象）"""
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif value is None or not str(value).strip():
        parsed = default
    else:
        try:
            parsed = float(str(value).strip())
        except (TypeError, ValueError):
            logger.warning("%s=%r 无效数值，使用默认值 %s", field_name, value, default)
            parsed = default

    if minimum is not None and parsed < minimum:
        parsed = minimum
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed


def normalize_news_strategy_profile(value: Optional[str]) -> str:
    """规范化新闻策略配置文件"""
    candidate = (value or "short").strip().lower()
    return candidate if candidate in NEWS_STRATEGY_WINDOWS else "short"


def resolve_news_window_days(news_max_age_days: int, news_strategy_profile: Optional[str]) -> int:
    """解析实际的新闻窗口天数"""
    profile = normalize_news_strategy_profile(news_strategy_profile)
    profile_days = NEWS_STRATEGY_WINDOWS.get(profile, NEWS_STRATEGY_WINDOWS["short"])
    return max(1, min(max(1, int(news_max_age_days)), profile_days))


def canonicalize_llm_channel_protocol(value: Optional[str]) -> str:
    """标准化 LLM 通道协议"""
    candidate = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "openai_compatible": "openai",
        "openai_compat": "openai",
        "claude": "anthropic",
        "google": "gemini",
        "vertex": "vertex_ai",
        "vertexai": "vertex_ai",
    }
    return aliases.get(candidate, candidate)


def resolve_llm_channel_protocol(
    protocol: Optional[str],
    *,
    base_url: Optional[str] = None,
    models: Optional[List[str]] = None,
    channel_name: Optional[str] = None,
) -> str:
    """解析通道的实际协议"""
    from urllib.parse import urlparse
    explicit = canonicalize_llm_channel_protocol(protocol)
    if explicit in SUPPORTED_LLM_CHANNEL_PROTOCOLS:
        return explicit

    for model in models or []:
        if "/" not in model: continue
        prefix = canonicalize_llm_channel_protocol(model.split("/", 1)[0])
        if prefix in SUPPORTED_LLM_CHANNEL_PROTOCOLS: return prefix

    if channel_name:
        name_protocol = canonicalize_llm_channel_protocol(channel_name)
        if name_protocol in SUPPORTED_LLM_CHANNEL_PROTOCOLS: return name_protocol

    if base_url:
        parsed = urlparse(base_url)
        if parsed.hostname in {"127.0.0.1", "localhost", "0.0.0.0"}: return "openai"
        return "openai"
    return ""


def channel_allows_empty_api_key(protocol: Optional[str], base_url: Optional[str]) -> bool:
    """Return True when a channel can run without an API key."""
    from urllib.parse import urlparse
    resolved_protocol = resolve_llm_channel_protocol(protocol, base_url=base_url)
    if resolved_protocol == "ollama": return True
    parsed = urlparse(base_url or "")
    return parsed.hostname in {"127.0.0.1", "localhost", "0.0.0.0"}


def normalize_llm_channel_model(model: str, protocol: Optional[str], base_url: Optional[str] = None) -> str:
    """Attach a provider prefix when the model omits it."""
    normalized_model = model.strip()
    if not normalized_model: return normalized_model

    resolved_protocol = resolve_llm_channel_protocol(protocol, base_url=base_url, models=[normalized_model])
    if "/" in normalized_model:
        prefix, rest = normalized_model.split("/", 1)
        canonical_prefix = canonicalize_llm_channel_protocol(prefix)
        if canonical_prefix in SUPPORTED_LLM_CHANNEL_PROTOCOLS and rest:
            return f"{canonical_prefix}/{rest}"
        if resolved_protocol == "openai":
            return f"openai/{normalized_model}"
        return normalized_model

    if resolved_protocol == "anthropic" and not normalized_model.lower().startswith("claude"):
        return f"anthropic/{normalized_model}"
    if resolved_protocol == "gemini" and not normalized_model.lower().startswith("gemini"):
        return f"gemini/{normalized_model}"
    if resolved_protocol in ("vertex_ai", "deepseek", "openai", "ollama"):
        return f"{resolved_protocol}/{normalized_model}"
    return normalized_model


def get_configured_llm_models(model_list: List[Dict[str, Any]]) -> List[str]:
    """Return unique model names from a Router-ready model_list."""
    models = []
    seen = set()
    for entry in model_list:
        name = entry.get("model_name")
        if not name:
            params = entry.get("litellm_params", {}) or {}
            name = params.get("model")
        if name and name not in seen:
            models.append(name)
            seen.add(name)
    return models


def get_effective_agent_primary_model(config: Any) -> Optional[str]:
    """Resolve the effective primary model for Agent mode."""
    configured_router_models = set(
        get_configured_llm_models(getattr(config, "llm_model_list", []) or [])
    )
    if hasattr(config, "agent_litellm_model") and config.agent_litellm_model:
        return normalize_agent_litellm_model(
            config.agent_litellm_model,
            configured_models=configured_router_models,
        )
    litellm_model = getattr(config, "litellm_model", None)
    return normalize_agent_litellm_model(
        litellm_model,
        configured_models=configured_router_models,
    ) if litellm_model else litellm_model


def get_effective_agent_models_to_try(config: Any) -> List[str]:
    """Resolve the list of models to try in Agent mode."""
    configured_router_models = set(
        get_configured_llm_models(getattr(config, "llm_model_list", []) or [])
    )
    primary = get_effective_agent_primary_model(config)
    models = [primary] if primary else []
    fallbacks = getattr(config, "litellm_fallback_models", [])
    if fallbacks:
        models.extend(fallbacks)

    seen = set()
    ordered_models: List[str] = []
    for model in models:
        normalized_model = (model or "").strip()
        if not normalized_model:
            continue
        dedupe_key = normalize_agent_litellm_model(
            normalized_model,
            configured_models=configured_router_models,
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ordered_models.append(normalized_model)
    return ordered_models


def normalize_agent_litellm_model(
    model: str,
    configured_models: Optional[set[str]] = None,
) -> str:
    """Normalize AGENT_LITELLM_MODEL while preserving configured router aliases."""
    normalized_model = (model or "").strip()
    if not normalized_model:
        return ""
    if "/" not in normalized_model:
        if configured_models and normalized_model in configured_models:
            return normalized_model
        return f"openai/{normalized_model}"
    return normalized_model


def resolve_unified_llm_temperature(model: str) -> float:
    """Resolve the unified LLM temperature with backward-compatible fallbacks."""
    llm_temperature_raw = os.getenv("LLM_TEMPERATURE")
    if llm_temperature_raw and llm_temperature_raw.strip():
        try:
            return float(llm_temperature_raw)
        except (ValueError, TypeError):
            pass

    provider_temperature_env = {
        "gemini": "GEMINI_TEMPERATURE",
        "vertex_ai": "GEMINI_TEMPERATURE",
        "anthropic": "ANTHROPIC_TEMPERATURE",
        "openai": "OPENAI_TEMPERATURE",
        "deepseek": "OPENAI_TEMPERATURE",
    }
    preferred_env = provider_temperature_env.get(_get_litellm_provider(model))
    if preferred_env:
        preferred_value = os.getenv(preferred_env)
        if preferred_value and preferred_value.strip():
            try:
                return float(preferred_value)
            except (ValueError, TypeError):
                pass

    for env_name in ("GEMINI_TEMPERATURE", "ANTHROPIC_TEMPERATURE", "OPENAI_TEMPERATURE"):
        env_value = os.getenv(env_name)
        if env_value and env_value.strip():
            try:
                return float(env_value)
            except (ValueError, TypeError):
                continue

    if not model:
        return 0.7
    m_lower = model.lower()
    if "o1-" in m_lower or "o3-" in m_lower or "deepseek-reasoner" in m_lower:
        return 0.0
    return 0.7


def _get_litellm_provider(model: str) -> str:
    """Extract provider prefix from a LiteLLM model string."""
    if not model: return ""
    if "/" in model: return model.split("/")[0].lower()
    return "openai"


def _uses_direct_env_provider(model: str) -> bool:
    """True when the model's provider is one that LiteLLM handles via direct env vars."""
    provider = _get_litellm_provider(model)
    managed = {"gemini", "vertex_ai", "anthropic", "openai", "deepseek"}
    return provider not in managed


def parse_llm_channels(channels_str: str) -> List[Dict[str, Any]]:
    """Parse LLM_CHANNELS env var and per-channel env vars."""
    channels: List[Dict[str, Any]] = []
    for raw_name in channels_str.split(','):
        ch_name = raw_name.strip()
        if not ch_name: continue
        ch_upper = ch_name.upper()
        base_url = os.getenv(f'LLM_{ch_upper}_BASE_URL', '').strip() or None
        protocol_raw = os.getenv(f'LLM_{ch_upper}_PROTOCOL', '').strip()
        enabled = parse_env_bool(os.getenv(f'LLM_{ch_upper}_ENABLED'), default=True)
        api_keys_raw = os.getenv(f'LLM_{ch_upper}_API_KEYS', '')
        api_keys = [k.strip() for k in api_keys_raw.split(',') if k.strip()]
        if not api_keys:
            single_key = os.getenv(f'LLM_{ch_upper}_API_KEY', '').strip()
            if single_key: api_keys = [single_key]
        models_raw = os.getenv(f'LLM_{ch_upper}_MODELS', '')
        raw_models = [m.strip() for m in models_raw.split(',') if m.strip()]
        protocol = resolve_llm_channel_protocol(protocol_raw, base_url=base_url, models=raw_models, channel_name=ch_name)
        if not api_keys and channel_allows_empty_api_key(protocol, base_url):
            api_keys = [""]
        models = [normalize_llm_channel_model(m, protocol, base_url) for m in raw_models]
        extra_headers_raw = os.getenv(f'LLM_{ch_upper}_EXTRA_HEADERS', '').strip()
        extra_headers = None
        if extra_headers_raw:
            try: extra_headers = json.loads(extra_headers_raw)
            except: pass
        if not enabled or not api_keys or not models: continue
        channels.append({
            'name': ch_name.lower(), 'protocol': protocol, 'enabled': enabled,
            'base_url': base_url, 'api_keys': api_keys, 'models': models,
            'extra_headers': extra_headers,
        })
    return channels


def channels_to_model_list(channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert parsed LLM channels to LiteLLM Router model_list format."""
    model_list: List[Dict[str, Any]] = []
    for ch in channels:
        for model_name in ch['models']:
            for api_key in ch['api_keys']:
                litellm_params: Dict[str, Any] = {'model': model_name}
                if api_key: litellm_params['api_key'] = api_key
                if ch['base_url']: litellm_params['api_base'] = ch['base_url']
                headers = dict(ch.get('extra_headers') or {})
                if ch['base_url'] and 'aihubmix.com' in ch['base_url']:
                    headers.setdefault('APP-Code', 'GPIJ3886')
                if headers: litellm_params['extra_headers'] = headers
                model_list.append({'model_name': model_name, 'litellm_params': litellm_params})
    return model_list


def legacy_keys_to_model_list(
    gemini_keys: List[str],
    anthropic_keys: List[str],
    openai_keys: List[str],
    openai_base_url: Optional[str],
    deepseek_keys: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build Router model_list from legacy per-provider keys (backward compat)."""
    model_list: List[Dict[str, Any]] = []
    for k in gemini_keys:
        if k and len(k) >= 8:
            model_list.append({'model_name': '__legacy_gemini__', 'litellm_params': {'model': '__legacy_gemini__', 'api_key': k}})
    for k in anthropic_keys:
        if k and len(k) >= 8:
            model_list.append({'model_name': '__legacy_anthropic__', 'litellm_params': {'model': '__legacy_anthropic__', 'api_key': k}})
    for k in openai_keys:
        if k and len(k) >= 8:
            params: Dict[str, Any] = {'model': '__legacy_openai__', 'api_key': k}
            if openai_base_url: params['api_base'] = openai_base_url
            if openai_base_url and 'aihubmix.com' in openai_base_url:
                params['extra_headers'] = {'APP-Code': 'GPIJ3886'}
            model_list.append({'model_name': '__legacy_openai__', 'litellm_params': params})
    for k in (deepseek_keys or []):
        if k and len(k) >= 8:
            model_list.append({'model_name': '__legacy_deepseek__', 'litellm_params': {'model': '__legacy_deepseek__', 'api_key': k}})
    return model_list


def parse_litellm_yaml(config_path: str) -> List[Dict[str, Any]]:
    """Parse a standard LiteLLM config YAML file into Router model_list."""
    try:
        import yaml
    except ImportError: return []
    path = Path(config_path)
    if not path.exists(): return []
    try:
        with open(path, encoding='utf-8') as f:
            yaml_config = yaml.safe_load(f) or {}
    except: return []
    model_list = yaml_config.get('model_list', [])
    if not isinstance(model_list, list): return []
    for entry in model_list:
        params = entry.get('litellm_params', {})
        for key in list(params.keys()):
            val = params.get(key)
            if isinstance(val, str) and val.startswith('os.environ/'):
                env_name = val.split('/', 1)[1]
                params[key] = os.getenv(env_name, '')
    return model_list


def load_stocks_from_yaml(file_path: str) -> List[str]:
    """从 YAML 文件加载股票列表"""
    try:
        import yaml
    except ImportError: return []
    path = Path(file_path)
    if not path.exists(): return []
    try:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        if not data: return []
        stocks = []
        if isinstance(data, list): stocks = data
        elif isinstance(data, dict):
            if 'stocks' in data and isinstance(data['stocks'], list): stocks.extend(data['stocks'])
            if 'groups' in data and isinstance(data['groups'], dict):
                for gs in data['groups'].values():
                    if isinstance(gs, list): stocks.extend(gs)
        return list(dict.fromkeys([str(s).strip().upper() for s in stocks if s]))
    except: return []


def load_settings_from_yaml(file_path: str) -> Dict[str, Any]:
    """从 YAML 文件加载系统设置"""
    try:
        import yaml
    except ImportError: return {}
    path = Path(file_path)
    if not path.exists(): return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except: return {}


def setup_env(override: bool = False):
    """Load environment variables from the active .env file."""
    from dotenv import load_dotenv
    env_file = os.getenv("ENV_FILE")
    env_path = Path(env_file) if env_file else (Path(__file__).resolve().parents[2] / ".env")
    if env_path.exists():
        load_dotenv(env_path, override=override)
