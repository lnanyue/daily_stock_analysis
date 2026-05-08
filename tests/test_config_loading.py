"""Tests for YAML config loading order and env key mapping."""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from src.config import Config


class ConfigDeepMergeTestCase(unittest.TestCase):
    """Pure-function tests for Config._deep_merge."""

    def test_deep_merge_override(self) -> None:
        base = {"system": {"report_dir": "./report", "log_level": "INFO"}}
        override = {"system": {"report_dir": "./custom"}}
        merged = Config._deep_merge(base, override)
        self.assertEqual(merged["system"]["report_dir"], "./custom")
        self.assertEqual(merged["system"]["log_level"], "INFO")

    def test_deep_merge_adds_new_key(self) -> None:
        base = {"system": {"log_level": "INFO"}}
        override = {"system": {"report_dir": "./report"}}
        merged = Config._deep_merge(base, override)
        self.assertIn("report_dir", merged["system"])
        self.assertEqual(merged["system"]["report_dir"], "./report")

    def test_deep_merge_new_section(self) -> None:
        base = {"system": {"log_level": "INFO"}}
        override = {"analysis": {"mode": "simple"}}
        merged = Config._deep_merge(base, override)
        self.assertIn("analysis", merged)
        self.assertEqual(merged["analysis"]["mode"], "simple")

    def test_deep_merge_scalar_overrides_dict(self) -> None:
        base = {"system": {"log_level": "INFO"}}
        override = {"system": "disabled"}
        merged = Config._deep_merge(base, override)
        self.assertEqual(merged["system"], "disabled")


class ConfigYamlOverrideTestCase(unittest.TestCase):
    """Integration tests for config.yaml overriding config.example.yaml.

    Each test runs in a temp directory with isolated YAML files.
    ``os.environ`` is cleared to prevent other tests from leaking env
    vars that would interfere with ``setdefault()`` semantics.
    """

    def setUp(self):
        self.addCleanup(Config.reset_instance)
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._original_cwd = os.getcwd()
        self.addCleanup(os.chdir, self._original_cwd)
        os.chdir(self._temp_dir.name)

        # Start each test with a clean env (except HOME so the process
        # can find its way).  This matters because _apply_config_yaml_defaults
        # uses os.environ.setdefault and other tests may have set REPORT_DIR.
        self._env_patch = unittest.mock.patch.dict(
            os.environ,
            {"HOME": os.environ.get("HOME", "/tmp")},
            clear=True,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_config_yaml_overrides_example(self) -> None:
        (Path(self._temp_dir.name) / "config.example.yaml").write_text(
            "system:\n  report_dir: ./reports/example\n",
            encoding="utf-8",
        )
        (Path(self._temp_dir.name) / "config.yaml").write_text(
            "system:\n  report_dir: ./reports/local\n",
            encoding="utf-8",
        )

        Config._apply_config_yaml_defaults()
        self.assertEqual(
            os.environ.get("REPORT_DIR"),
            "./reports/local",
            "config.yaml should override config.example.yaml",
        )

    def test_config_example_only_defaults_used(self) -> None:
        (Path(self._temp_dir.name) / "config.example.yaml").write_text(
            "system:\n  report_dir: ./reports/default\n",
            encoding="utf-8",
        )

        Config._apply_config_yaml_defaults()
        self.assertEqual(os.environ.get("REPORT_DIR"), "./reports/default")

    def test_config_yaml_full_chain_to_config_report_dir(self) -> None:
        """Full chain: YAML → env → Config.report_dir."""
        (Path(self._temp_dir.name) / "config.example.yaml").write_text(
            "system:\n  report_dir: ./reports/custom_chain\n",
            encoding="utf-8",
        )

        Config._apply_config_yaml_defaults()

        with (
            unittest.mock.patch("src.config.setup_env"),
            unittest.mock.patch.object(Config, "_parse_litellm_yaml", return_value=[]),
        ):
            config = Config._load_from_env()

        self.assertEqual(
            config.report_dir,
            "./reports/custom_chain",
            "YAML system.report_dir → env REPORT_DIR → Config.report_dir",
        )

    def test_system_report_dir_env_key_mapping(self) -> None:
        """Verify that system.report_dir maps to REPORT_DIR, not SYSTEM_REPORT_DIR."""
        (Path(self._temp_dir.name) / "config.example.yaml").write_text(
            "system:\n  report_dir: ./reports/mapped\n",
            encoding="utf-8",
        )

        Config._apply_config_yaml_defaults()
        self.assertEqual(
            os.environ.get("REPORT_DIR"),
            "./reports/mapped",
            "Should use flat REPORT_DIR, not section-prefixed SYSTEM_REPORT_DIR",
        )
        self.assertIsNone(
            os.environ.get("SYSTEM_REPORT_DIR"),
            "SYSTEM_REPORT_DIR should NOT be set",
        )


if __name__ == "__main__":
    unittest.main()
