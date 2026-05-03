import pytest
import os
from pathlib import Path
from unittest.mock import patch

from src.config.loader import UnifiedConfigLoader


@pytest.fixture
def loader():
    return UnifiedConfigLoader()


def test_loader_initialization(loader):
    """UnifiedConfigLoader should initialize without error"""
    assert loader is not None
    assert hasattr(loader, 'load')
    assert hasattr(loader, '_load_env')
    assert hasattr(loader, '_load_yaml')
    assert hasattr(loader, '_merge')


def test_load_env_returns_dict(loader):
    """_load_env should return a dictionary"""
    with patch('src.config.loader.os.path.exists', return_value=True):
        with patch('src.config.loader.dotenv_values', return_value={"GEMINI_API_KEY": "test_key"}):
            result = loader._load_env()
            assert isinstance(result, dict)


def test_load_yaml_returns_dict(loader, tmp_path):
    """_load_yaml should return a flat dictionary from YAML file"""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("system:\n  max_workers: 2\n")
    loader._config_path = yaml_file
    result = loader._load_yaml()
    assert isinstance(result, dict)
    # Should be flat with uppercase keys
    assert "MAX_WORKERS" in result or "SYSTEM_MAX_WORKERS" in result


def test_merge_env_overrides_yaml(loader):
    """_merge should let env override yaml values"""
    # Simulating flat yaml_dict from _load_yaml()
    yaml_dict = {"MAX_WORKERS": 2, "ANALYSIS_MODE": "simple"}
    env_dict = {"MAX_WORKERS": "5"}
    result = loader._merge(env_dict, yaml_dict)
    assert result["MAX_WORKERS"] == "5"  # env overrides


def test_load_valid_config(tmp_path):
    """load() should load, validate, and return config"""
    # Create a minimal config.yaml
    yaml_content = """
system:
  max_workers: 2
analysis:
  mode: simple
"""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml_content)

    # Create a minimal .env
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=test_key_12345678\n")

    loader = UnifiedConfigLoader(env_path=str(env_file), config_path=str(yaml_file))
    result = loader.load()
    assert isinstance(result, dict)
