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
        # Check types
        messages.extend(cls._check_types(env_dict, config_dict))
        # Check enums
        messages.extend(cls._check_enums(env_dict, config_dict))
        # Check ranges
        messages.extend(cls._check_ranges(env_dict, config_dict))
        # Check sensitive keys
        messages.extend(cls._check_sensitive_keys(env_dict, config_dict))

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

    @classmethod
    def _check_types(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> list:
        """Check data types match field definitions."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            data_type = field.get("data_type", "string")
            value = env_dict.get(key) or config_dict.get(key)
            if value is None:
                continue
            if data_type == "integer":
                if not isinstance(value, int) and not (isinstance(value, str) and value.isdigit()):
                    messages.append(f"[TYPE] {key}={value} is not a valid integer")
            elif data_type == "number":
                try:
                    float(str(value))
                except (ValueError, TypeError):
                    messages.append(f"[TYPE] {key}={value} is not a valid number")
            elif data_type == "boolean":
                if str(value).lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                    messages.append(f"[TYPE] {key}={value} is not a valid boolean")
        return messages

    @classmethod
    def _check_enums(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> list:
        """Check enum fields have valid values."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            validation = field.get("validation", {})
            enum_values = validation.get("enum", [])
            if not enum_values:
                continue
            value = env_dict.get(key) or config_dict.get(key)
            if value is None:
                continue
            if str(value) not in [str(v) for v in enum_values]:
                messages.append(f"[ENUM] {key}={value} is not in {enum_values}")
        return messages

    @classmethod
    def _check_ranges(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> list:
        """Check numeric fields are within valid ranges."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            validation = field.get("validation", {})
            min_val = validation.get("min")
            max_val = validation.get("max")
            if min_val is None and max_val is None:
                continue
            value = env_dict.get(key) or config_dict.get(key)
            if value is None:
                continue
            try:
                num_value = float(str(value))
            except (ValueError, TypeError):
                continue
            if min_val is not None and num_value < min_val:
                messages.append(f"[RANGE] {key}={value} is below minimum {min_val}")
            if max_val is not None and num_value > max_val:
                messages.append(f"[RANGE] {key}={value} exceeds maximum {max_val}")
        return messages

    @classmethod
    def _check_sensitive_keys(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> list:
        """Check sensitive keys have valid format (e.g., API key length >= 8)."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            if not field.get("is_sensitive", False):
                continue
            value = env_dict.get(key) or config_dict.get(key)
            if value is None:
                continue
            str_value = str(value)
            # API keys should be at least 8 characters
            if field.get("data_type") == "string" and len(str_value) < 8:
                messages.append(f"[SENSITIVE] {key} is too short (min 8 chars)")
        return messages
