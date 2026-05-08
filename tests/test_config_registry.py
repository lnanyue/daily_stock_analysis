# -*- coding: utf-8 -*-
"""Tests for config_registry field definitions and schema building.

Ensures every notification channel that has a sender implementation also
has its config keys registered in _FIELD_DEFINITIONS so the Web settings
page and /api/v1/system/config/schema can expose them.
"""
import unittest

from src.core.config_registry import (
    build_schema_response,
    get_field_definition,
    get_registered_field_keys,
)


class TestSensitiveFieldsUsePasswordControl(unittest.TestCase):
    """Every is_sensitive field must use ui_control='password' to avoid
    leaking secrets in the Web settings page."""

    def test_all_sensitive_fields_use_password(self):
        schema = build_schema_response()
        violations = []
        for cat in schema["categories"]:
            for field in cat["fields"]:
                if field.get("is_sensitive") and field.get("ui_control") != "password":
                    violations.append(field["key"])
        self.assertEqual(violations, [],
                         f"Sensitive fields with non-password ui_control: {violations}")


def test_no_new_registry_entries():
    """Config registry must not grow — use Config.metadata instead."""
    count = len(get_registered_field_keys())
    assert count <= 107, f"Registry grew to {count} — use Config metadata instead"


if __name__ == "__main__":
    unittest.main()
