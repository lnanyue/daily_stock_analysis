#!/usr/bin/env python3
"""Generate config.example.yaml from Config field metadata.

Usage:
    python scripts/gen_config_example.py              # print to stdout
    python scripts/gen_config_example.py --write       # overwrite config.example.yaml
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

# Allow running from repo root: python scripts/gen_config_example.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.contract import iter_config_fields


def _set_nested(d: dict, path: str, value: Any) -> None:
    """Set a value in a nested dict by dotted path (e.g. 'system.report_dir')."""
    parts = path.split(".")
    current = d
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def format_yaml_default(default: object) -> object:
    """Format a default value for YAML output."""
    if isinstance(default, list):
        return default if default else None  # empty list => null in YAML
    if isinstance(default, dict):
        return default if default else None
    if default == "":
        return None  # empty string => null (let user fill)
    if default is None:
        return None
    return default


def generate_config_example() -> str:
    """Return config.example.yaml content as a string."""
    tree: Dict[str, Any] = {}

    for name, typ, default, meta in iter_config_fields():
        yaml_path = meta.get("yaml")
        if not yaml_path:
            continue
        value = format_yaml_default(default)
        _set_nested(tree, yaml_path, value)

    header = (
        "# A股自选股智能分析系统 - 业务配置示例（自动生成）\n"
        "# 本文件是入库模板，不包含任何个人路径或敏感信息。\n"
        "# 首次使用：cp config.example.yaml config.yaml 并编辑。\n"
        "# 敏感 Key/Token 请放在 .env 文件中。\n"
    )

    return header + yaml.dump(tree, default_flow_style=False, indent=2, allow_unicode=True, sort_keys=False)


def main():
    parser = argparse.ArgumentParser(description="Generate config.example.yaml from Config metadata")
    parser.add_argument("--write", action="store_true", help="Overwrite config.example.yaml in place")
    args = parser.parse_args()

    content = generate_config_example()

    if args.write:
        target = Path(os.getcwd()) / "config.example.yaml"
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Written to {target}")
    else:
        sys.stdout.write(content)


if __name__ == "__main__":
    main()
