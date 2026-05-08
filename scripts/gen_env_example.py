#!/usr/bin/env python3
"""Generate .env.example from Config field metadata.

Usage:
    python scripts/gen_env_example.py              # print to stdout
    python scripts/gen_env_example.py --write       # overwrite .env.example in place
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

# Allow running from repo root: python scripts/gen_env_example.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.contract import iter_config_fields


def group_fields_by_group() -> Dict[str, List[Tuple[str, type, object, dict]]]:
    """Group Config fields by their metadata group."""
    groups = defaultdict(list)
    for name, typ, default, meta in iter_config_fields():
        group = meta.get("group", "other")
        groups[group].append((name, typ, default, meta))
    return dict(groups)


def format_default(default: object) -> str:
    """Format a default value for .env.example output."""
    if default is None or default == "":
        return ""
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, (list, dict)):
        return ""
    return str(default)


def type_hint(typ: type) -> str:
    """Return a short type label for comments."""
    name = getattr(typ, "__name__", str(typ))
    if name == "Optional":
        return "optional"
    return "required" if name in ("str", "int", "float", "bool") else "optional"


def generate_env_example() -> str:
    """Return the full .env.example content as a string."""
    lines = [
        "# =======================================================================",
        "# A股自选股智能分析系统 - 环境变量配置（自动生成）",
        "# =======================================================================",
        "# 敏感 Key/Token 放在此文件；业务参数见 config.yaml",
        "# 验证：python3 -c \"from src.config import get_config; get_config()\"",
        "# =======================================================================",
        "",
    ]

    group_order = [
        ("core", "核心 Token"),
        ("llm", "LLM 模型配置"),
        ("search", "搜索引擎"),
        ("agent", "Agent 配置"),
        ("risk_screen", "排雷筛选"),
        ("notification", "通知渠道"),
        ("data", "数据源"),
        ("system", "系统配置"),
        ("other", "其他"),
    ]

    groups = group_fields_by_group()
    seen_groups = set()

    for group_key, group_title in group_order:
        if group_key not in groups:
            continue
        seen_groups.add(group_key)
        fields_in_group = groups[group_key]
        lines.append(f"")
        lines.append(f"# === {group_title} ===")

        for _name, typ, default, meta in sorted(fields_in_group, key=lambda x: x[0]):
            env_name = meta.get("env", "")
            if not env_name:
                continue
            default_str = format_default(default)
            hint = type_hint(typ)

            comment_parts = [hint]
            if "deprecated" in meta and meta["deprecated"]:
                comment_parts.append("deprecated")
            if comment_parts:
                lines.append(f"# {', '.join(comment_parts)}")

            if default_str:
                lines.append(f"{env_name}={default_str}")
            else:
                lines.append(f"{env_name}=")

    # Any group not in the explicit order list
    for group_key in groups:
        if group_key not in seen_groups:
            lines.append(f"")
            lines.append(f"# === {group_key} ===")
            for _name, typ, default, meta in sorted(groups[group_key], key=lambda x: x[0]):
                env_name = meta.get("env", "")
                if not env_name:
                    continue
                default_str = format_default(default)
                if default_str:
                    lines.append(f"{env_name}={default_str}")
                else:
                    lines.append(f"{env_name}=")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Generate .env.example from Config metadata")
    parser.add_argument("--write", action="store_true", help="Overwrite .env.example in place")
    args = parser.parse_args()

    content = generate_env_example()

    if args.write:
        target = Path(os.getcwd()) / ".env.example"
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Written to {target}")
    else:
        sys.stdout.write(content)


if __name__ == "__main__":
    main()
