# -*- coding: utf-8 -*-
"""Plugin registry unit tests."""
import os
import pytest
from src.plugins.config import ConfigLoader, resolve_env_refs
from src.plugins.loader import scan_and_register


class TestResolveEnvRefs:
    def test_plain_string(self):
        assert resolve_env_refs("hello") == "hello"

    def test_single_env_ref(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret_value")
        assert resolve_env_refs("${MY_KEY}") == "secret_value"

    def test_missing_env_returns_empty(self):
        assert resolve_env_refs("${UNDEFINED_VAR_123}") == ""

    def test_mixed_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "example.com")
        assert resolve_env_refs("https://${HOST}/api") == "https://example.com/api"

    def test_dict_recursion(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "tok123")
        data = {"auth": {"token": "${TOKEN}"}, "timeout": 10}
        assert resolve_env_refs(data) == {"auth": {"token": "tok123"}, "timeout": 10}

    def test_list_recursion(self, monkeypatch):
        monkeypatch.setenv("URL", "https://api.test.com")
        data = ["${URL}", "static", 42]
        assert resolve_env_refs(data) == ["https://api.test.com", "static", 42]


class TestConfigLoader:
    def test_missing_file_returns_empty(self):
        loader = ConfigLoader(config_path="/nonexistent/plugins.yaml")
        assert loader.fetchers == []
        assert loader.strategies == []

    def test_loads_yaml_config(self, tmp_path):
        config_file = tmp_path / "plugins.yaml"
        config_file.write_text(
            "fetchers:\n  - name: test_fetcher\n    module: test_fetcher\n    enabled: true\n    priority: 5\n    config:\n      api_key: test\n"
        )
        loader = ConfigLoader(config_path=str(config_file))
        assert len(loader.fetchers) == 1
        assert loader.fetchers[0]["name"] == "test_fetcher"
        assert loader.fetchers[0]["priority"] == 5


class TestScanAndRegister:
    def test_empty_directory_returns_empty(self):
        results = scan_and_register(["/nonexistent/path"])
        assert results == []
