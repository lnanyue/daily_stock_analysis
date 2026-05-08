#!/usr/bin/env python3
"""Check that .env.example and config.example.yaml are consistent with Config field metadata.

Usage:
    python scripts/check_config_contract.py              # check both
    python scripts/check_config_contract.py --strict     # exit non-zero on warnings
    python scripts/check_config_contract.py --env-only   # only check .env.example
    python scripts/check_config_contract.py --yaml-only  # only check config.example.yaml
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

# Allow running from repo root: python scripts/check_config_contract.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.contract import get_env_map, get_yaml_map


def check_env_example(env_path: Path, strict: bool = False) -> int:
    """Check that .env.example variables are consistent with Config field metadata.

    Returns count of issues found.
    """
    if not env_path.exists():
        print("MISSING: .env.example not found")
        return 1 if strict else 0

    env_map = get_env_map()
    env_vars_in_file = set()
    issues = 0

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                var_name = line.split("=", 1)[0].strip()
                env_vars_in_file.add(var_name)

    annotated_vars = set(env_map.values())

    # Vars in metadata but missing from .env.example
    missing_in_file = annotated_vars - env_vars_in_file
    if missing_in_file:
        print(f"WARNING: {len(missing_in_file)} env var(s) with metadata but missing from .env.example:")
        for v in sorted(missing_in_file):
            print(f"  + {v}")
        issues += len(missing_in_file)

    # Vars in .env.example but not in any metadata
    orphaned_in_file = env_vars_in_file - annotated_vars
    if orphaned_in_file:
        print(f"INFO: {len(orphaned_in_file)} env var(s) in .env.example without Config metadata (may still be read by _load_from_env):")
        for v in sorted(orphaned_in_file):
            print(f"  - {v}")

    return issues


def check_config_yaml(yaml_path: Path, strict: bool = False) -> int:
    """Check that config.example.yaml keys are consistent with Config field metadata.

    Returns count of issues found.
    """
    if not yaml_path.exists():
        print("MISSING: config.example.yaml not found")
        return 1 if strict else 0

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        print("WARNING: config.example.yaml is empty or not a dict")
        return 1 if strict else 0

    def _collect_yaml_paths(d, prefix=""):
        paths = set()
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                paths.update(_collect_yaml_paths(v, key))
            else:
                paths.add(key)
        return paths

    yaml_paths_in_file = _collect_yaml_paths(data)
    yaml_map = get_yaml_map()
    annotated_paths = set(yaml_map.values())

    issues = 0

    # Paths in metadata but missing from config.example.yaml
    missing_in_file = annotated_paths - yaml_paths_in_file
    if missing_in_file:
        print(f"WARNING: {len(missing_in_file)} YAML path(s) with metadata but missing from config.example.yaml:")
        for p in sorted(missing_in_file):
            print(f"  + {p}")
        issues += len(missing_in_file)

    # Paths in file but not in metadata (may be valid unannotated fields)
    orphaned_in_file = yaml_paths_in_file - annotated_paths
    if orphaned_in_file:
        print(f"INFO: {len(orphaned_in_file)} YAML path(s) in config.example.yaml without Config metadata:")
        for p in sorted(orphaned_in_file):
            print(f"  - {p}")

    return issues


def main():
    parser = argparse.ArgumentParser(description="Check config file consistency")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings")
    parser.add_argument("--env-only", action="store_true")
    parser.add_argument("--yaml-only", action="store_true")
    args = parser.parse_args()

    repo_root = Path(os.getcwd())
    total_issues = 0

    if not args.yaml_only:
        total_issues += check_env_example(repo_root / ".env.example", strict=args.strict)
    if not args.env_only:
        total_issues += check_config_yaml(repo_root / "config.example.yaml", strict=args.strict)

    if total_issues == 0:
        print("OK: Config contract is consistent")
        return 0

    print(f"\nFound {total_issues} issue(s)")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
