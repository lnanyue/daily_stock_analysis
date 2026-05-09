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


def test_yaml_to_env_mapping_is_consistent():
    """Verify that the metadata-derived {yaml_path: env_var} mapping is correct.

    The loader (_apply_config_yaml_defaults) and the contract checker both use
    this mapping.  If it's wrong, YAML config values land on the wrong env vars
    and the checker won't detect the mismatch.
    """
    from dataclasses import fields
    from src.config.manager import Config

    yaml_to_env: dict[str, str] = {}
    for f in fields(Config):
        yaml_path = f.metadata.get("yaml")
        env_var = f.metadata.get("env")
        if yaml_path and env_var:
            yaml_to_env[yaml_path] = env_var

    # Critical paths that were previously mis-mapped by the old heuristic loader.
    # If any of these regress, the loader is writing to the wrong env var.
    assert yaml_to_env["llm.primary_model"] == "LITELLM_MODEL"
    assert yaml_to_env["notification.merge_email"] == "MERGE_EMAIL_NOTIFICATION"
    assert yaml_to_env["realtime.prefetch_quotes"] == "PREFETCH_REALTIME_QUOTES"
    assert yaml_to_env["risk_screen.max_workers"] == "RISK_SCREEN_MAX_WORKERS"
    # system.max_workers should NOT be overwritten by risk_screen.max_workers
    assert yaml_to_env["system.max_workers"] == "MAX_WORKERS"

    # No two YAML paths should map to the same env var (collision)
    seen_env: dict[str, str] = {}
    for yaml_path, env_var in yaml_to_env.items():
        if env_var in seen_env:
            raise AssertionError(
                f"env var collision: {seen_env[env_var]} and {yaml_path} "
                f"both map to {env_var}"
            )
        seen_env[env_var] = yaml_path
