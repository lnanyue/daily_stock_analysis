"""Tests for src.config.contract helpers."""

from src.config.contract import iter_config_fields, get_env_map, get_yaml_map


def test_iter_config_fields_skips_internal():
    names = {name for name, _typ, _default, _meta in iter_config_fields()}
    assert "_instance" not in names
    assert "_agent_mode_explicit" not in names


def test_iter_config_fields_yields_all_public_fields():
    from dataclasses import fields
    from src.config.manager import Config
    public = {f.name for f in fields(Config) if not f.name.startswith("_")}
    yielded = {name for name, _typ, _default, _meta in iter_config_fields()}
    assert yielded == public


def test_get_env_map_returns_only_annotated_fields():
    env_map = get_env_map()
    assert isinstance(env_map, dict)
    for field_name, env_name in env_map.items():
        assert env_name.isupper(), f"{field_name} to {env_name} is not uppercase"


def test_get_yaml_map_returns_only_annotated_fields():
    yaml_map = get_yaml_map()
    assert isinstance(yaml_map, dict)
    # At minimum, fields with "yaml" metadata should appear with non-empty paths
    for field_name, yaml_path in yaml_map.items():
        assert len(yaml_path) > 0, f"{field_name} has empty yaml path"
