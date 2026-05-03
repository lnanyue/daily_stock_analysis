"""Configuration validation module."""

from typing import Any, Dict

from src.core.config_registry import (
    get_registered_field_keys,
    get_field_definition,
)


class ConfigValidationError(Exception):
    """Raised when configuration validation fails (strict mode)."""

    def __init__(self, messages: list):
        self.messages = messages
        super().__init__(self._format_messages())

    def _format_messages(self) -> str:
        if not self.messages:
            return "Config validation failed."
        return "Config validation failed:\n  " + "\n  ".join(self.messages)


class ConfigValidator:
    """Strict validator for configuration, based on config_registry metadata."""

    @classmethod
    def validate_all(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> None:
        """Validate all configuration. Raises ConfigValidationError on failure."""
        messages = []

        # Check required fields
        messages.extend(cls._check_required_fields(env_dict, config_dict))

        if messages:
            raise ConfigValidationError(messages)

    @classmethod
    def _check_required_fields(
        cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]
    ) -> list:
        """Check that required fields are present and not empty."""
        messages = []
        # Only check fields marked as required in registry
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            if not field.get("is_required", False):
                continue
            # Check in env_dict first, then config_dict
            value = env_dict.get(key) or config_dict.get(key)
            if not value:
                messages.append(f"[REQUIRED] {key} is required but missing")
        return messages
