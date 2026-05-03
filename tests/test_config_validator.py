import pytest
from src.config.validator import ConfigValidationError


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
