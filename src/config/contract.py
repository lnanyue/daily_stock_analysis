"""Shared utilities for Config field metadata introspection."""

from dataclasses import fields, MISSING
from typing import Any, Dict, Iterator, Tuple

from src.config.manager import Config


# Field names to skip in metadata enumeration (internal fields).
_INTERNAL_FIELDS = frozenset({"_instance", "_agent_mode_explicit"})


def iter_config_fields() -> Iterator[Tuple[str, type, Any, Dict[str, Any]]]:
    """Yield (field_name, field_type, default_value, metadata) for every non-internal Config field."""
    for f in fields(Config):
        if f.name in _INTERNAL_FIELDS:
            continue
        default = f.default if f.default is not MISSING else None
        yield f.name, f.type, default, f.metadata


def get_env_map() -> Dict[str, str]:
    """Return a dict mapping Config field name to env var name.

    Only fields whose metadata contains an ``"env"`` key are included.
    """
    result = {}
    for name, _typ, _default, meta in iter_config_fields():
        env = meta.get("env")
        if env:
            result[name] = env
    return result


def get_yaml_map() -> Dict[str, str]:
    """Return a dict mapping Config field name to YAML path.

    Only fields whose metadata contains a ``"yaml"`` key are included.
    """
    result = {}
    for name, _typ, _default, meta in iter_config_fields():
        yaml_path = meta.get("yaml")
        if yaml_path:
            result[name] = yaml_path
    return result
