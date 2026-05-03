"""Unified configuration loader."""

import os
from pathlib import Path
from typing import Any, Dict

from dotenv import dotenv_values


class UnifiedConfigLoader:
    """Loads and merges .env and config.yaml with unified validation."""

    def __init__(self, env_path: str = None, config_path: str = None):
        self._env_path = Path(env_path) if env_path else Path(os.getcwd()) / ".env"
        self._config_path = Path(config_path) if config_path else Path(os.getcwd()) / "config.yaml"

    def load(self) -> Dict[str, Any]:
        """Load, merge, validate, and return the full configuration."""
        env_dict = self._load_env()
        yaml_dict = self._load_yaml()
        merged = self._merge(env_dict, yaml_dict)
        from src.config.validator import ConfigValidator
        ConfigValidator.validate_all(env_dict, merged)
        return merged

    def _load_env(self) -> Dict[str, Any]:
        """Load .env file into a dictionary."""
        if not self._env_path.exists():
            return {}
        values = dotenv_values(self._env_path)
        return {str(k): ("" if v is None else str(v)) for k, v in values.items() if k is not None}

    def _load_yaml(self) -> Dict[str, Any]:
        """Load config.yaml into a flat dictionary with uppercase keys."""
        if not self._config_path.exists():
            return {}
        try:
            import yaml
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    return {}
                # Flatten nested YAML: system.max_workers -> MAX_WORKERS
                return self._flatten_yaml(data)
        except ImportError:
            return {}
        except Exception:
            return {}

    def _flatten_yaml(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten nested YAML dict into uppercase key-value pairs.

        Maps nested keys like system.max_workers to uppercase keys like MAX_WORKERS
        by looking up config_registry to find the correct mapping.
        Falls back to {SECTION}_{KEY} if no mapping found.
        """
        result = {}
        try:
            from src.core.config_registry import get_registered_field_keys
            registry_keys = set(get_registered_field_keys())
        except Exception:
            registry_keys = set()

        for section, values in data.items():
            if isinstance(values, dict):
                for sub_key, sub_value in values.items():
                    # Try to find matching key in registry
                    # system + max_workers -> look for MAX_WORKERS
                    sub_key_upper = sub_key.upper()
                    # Direct match in registry
                    if sub_key_upper in registry_keys:
                        result[sub_key_upper] = sub_value
                    else:
                        # Try with section prefix: SYSTEM_MAX_WORKERS
                        combined = f"{section.upper()}_{sub_key_upper}"
                        if combined in registry_keys:
                            result[combined] = sub_value
                        else:
                            # Fallback: use combined key
                            result[combined] = sub_value
            else:
                result[section.upper()] = values
        return result

    def _merge(self, env_dict: Dict[str, Any], yaml_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Merge yaml_dict and env_dict with env taking priority."""
        merged = dict(yaml_dict)  # Start with yaml defaults (flat, uppercase)
        # Override with env values
        for key, value in env_dict.items():
            merged[key] = value
        # Override with environment variables (highest priority)
        for key in os.environ:
            if key in merged or key in env_dict:
                merged[key] = os.environ[key]
        return merged
