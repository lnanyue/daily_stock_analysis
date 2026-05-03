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


def test_validate_type_integer_valid():
    """Integer type fields should accept valid integers"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "MAX_WORKERS": 3}
    config_dict = {}
    ConfigValidator.validate_all(env_dict, config_dict)  # Should not raise


def test_validate_type_integer_invalid():
    """Integer type fields should reject non-integer values"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "MAX_WORKERS": "abc"}
    config_dict = {}
    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)
    assert any("MAX_WORKERS" in m and "integer" in m.lower() for m in exc_info.value.messages)


def test_validate_enum_valid():
    """Enum fields should accept values in the enum list"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "REPORT_TYPE": "simple"}
    config_dict = {}
    ConfigValidator.validate_all(env_dict, config_dict)  # Should not raise


def test_validate_enum_invalid():
    """Enum fields should reject values not in the enum list"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "REPORT_TYPE": "invalid_type"}
    config_dict = {}
    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)
    assert any("REPORT_TYPE" in m and "invalid_type" in m for m in exc_info.value.messages)


def test_validate_range_below_min():
    """Range check should catch values below minimum"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "BIAS_THRESHOLD": -1.0}
    config_dict = {}
    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)
    assert any("BIAS_THRESHOLD" in m and "below minimum" in m for m in exc_info.value.messages)


def test_validate_range_exceeds_max():
    """Range check should catch values above maximum"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "BIAS_THRESHOLD": 60.0}
    config_dict = {}
    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)
    assert any("BIAS_THRESHOLD" in m and "exceeds maximum" in m for m in exc_info.value.messages)


def test_validate_sensitive_key_short():
    """Sensitive keys should be at least 8 characters"""
    env_dict = {"GEMINI_API_KEY": "short"}
    config_dict = {}
    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)
    assert any("GEMINI_API_KEY" in m and "too short" in m for m in exc_info.value.messages)
