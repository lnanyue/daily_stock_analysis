# -*- coding: utf-8 -*-
"""
Plugin configuration loader — parses plugins.yaml with environment variable resolution.
"""
import os
import re
from pathlib import Path


def resolve_env_refs(value):
    """
    Recursively replace ${ENV_VAR} in strings with actual os.environ values.
    Unset env vars resolve to empty string. Non-string types pass through.
    """
    if isinstance(value, str):
        pattern = re.compile(r"\$\{(\w+)\}")
        def replacer(match):
            return os.environ.get(match.group(1), "")
        return pattern.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: resolve_env_refs(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_env_refs(item) for item in value]
    return value


class ConfigLoader:
    """Load and parse plugins.yaml configuration."""

    def __init__(self, config_path: str = "plugins.yaml"):
        self.config_path = config_path
        self._raw: dict = {"fetchers": [], "strategies": []}
        self._load()

    def _load(self) -> None:
        import yaml

        path = Path(self.config_path)
        if not path.exists():
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self._raw = resolve_env_refs(raw)

    @property
    def fetchers(self) -> list:
        return self._raw.get("fetchers", [])

    @property
    def strategies(self) -> list:
        return self._raw.get("strategies", [])
