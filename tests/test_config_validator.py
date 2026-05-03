import pytest
from src.config.validator import ConfigValidationError, ConfigValidator


def test_validation_error_is_exception():
    """ConfigValidationError should be a subclass of Exception"""
    assert issubclass(ConfigValidationError, Exception)


def test_validation_error_stores_messages():
    """ConfigValidationError should store validation messages"""
    error = ConfigValidationError(["[REQUIRED] API_KEY is missing"])
    assert len(error.messages) == 1
    assert "[REQUIRED] API_KEY is missing" in error.messages


def test_validation_error_str_format():
    """ConfigValidationError __str__ should format messages"""
    error = ConfigValidationError([
        "[REQUIRED] GEMINI_API_KEY is missing",
        "[TYPE] MAX_WORKERS=abc is not valid integer"
    ])
    str_repr = str(error)
    assert "Config validation failed:" in str_repr
    assert "[REQUIRED] GEMINI_API_KEY is missing" in str_repr


def test_validate_required_field_missing(monkeypatch):
    """Should raise ConfigValidationError when required field is missing"""
    # Mock get_registered_field_keys to return a test key
    def mock_get_registered_field_keys():
        return ["TEST_REQUIRED_KEY"]

    # Mock get_field_definition to return a required field
    def mock_get_field_definition(key):
        return {
            "key": "TEST_REQUIRED_KEY",
            "is_required": True,
            "data_type": "string",
            "is_sensitive": False,
        }

    monkeypatch.setattr("src.config.validator.get_registered_field_keys", mock_get_registered_field_keys)
    monkeypatch.setattr("src.config.validator.get_field_definition", mock_get_field_definition)

    env_dict = {}
    config_dict = {}

    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)

    assert exc_info.value.messages  # Should have at least one message


def test_validate_no_error_when_valid():
    """Should not raise when all required fields are present"""
    env_dict = {"GEMINI_API_KEY": "valid_key_12345678"}
    config_dict = {}
    # Should NOT raise
    ConfigValidator.validate_all(env_dict, config_dict)
