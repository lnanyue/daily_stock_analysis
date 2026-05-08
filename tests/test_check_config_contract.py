"""Tests for scripts/check_config_contract.py."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.check_config_contract import check_env_example, check_config_yaml


class TestCheckEnvExample:
    def test_missing_file_returns_warning(self):
        issues = check_env_example(Path("/nonexistent/.env.example"), strict=False)
        assert issues == 0  # no strict => warning only, 0 issues

    def test_missing_file_returns_issue_when_strict(self):
        issues = check_env_example(Path("/nonexistent/.env.example"), strict=True)
        assert issues == 1

    def test_env_vars_with_metadata_should_not_be_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env.example"
            env_path.write_text("STOCK_LIST=600519\nLOG_LEVEL=INFO\n", encoding="utf-8")
            with patch("scripts.check_config_contract.get_env_map", return_value={"stock_list": "STOCK_LIST", "log_level": "LOG_LEVEL", "report_dir": "REPORT_DIR"}):
                issues = check_env_example(env_path, strict=True)
                assert issues == 1  # REPORT_DIR is missing


class TestCheckConfigYaml:
    def test_missing_file_returns_warning(self):
        issues = check_config_yaml(Path("/nonexistent/config.yaml"), strict=False)
        assert issues == 0

    def test_yaml_paths_with_metadata_should_not_be_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "config.yaml"
            yaml_path.write_text("system:\n  log_level: INFO\n", encoding="utf-8")
            with patch("scripts.check_config_contract.get_yaml_map", return_value={"report_dir": "system.report_dir", "log_level": "system.log_level"}):
                issues = check_config_yaml(yaml_path, strict=True)
                assert issues == 1  # system.report_dir is missing
