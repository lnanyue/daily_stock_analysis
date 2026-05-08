# 配置契约 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Config` dataclass fields the single source of truth for all configuration definitions, with automated consistency checks and code generation.

**Architecture:** Add `metadata` dicts to existing `Config` dataclass fields. Write three standalone scripts: a consistency checker (CI-runnable), a `.env.example` generator, and a `config.example.yaml` generator. The scripts read Config field metadata and validate/generate against it. `config_registry.py` is gradually deprecated.

**Tech Stack:** Python 3.11+, dataclasses (stdlib), pyyaml (existing dependency), pathlib (stdlib).

---

### Task 1: Add metadata infrastructure and core helper

**Files:**
- Modify: `src/config/manager.py` — add `_iter_config_fields()` helper and metadata support
- Create: `src/config/contract.py` — shared utilities for reading Config field metadata

- [ ] **Step 1: Create `src/config/contract.py`**

```python
"""Shared utilities for Config field metadata introspection."""

from dataclasses import fields, MISSING
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.config.manager import Config


# Field names to skip in metadata enumeration (internal fields).
_INTERNAL_FIELDS = frozenset({"_instance", "_agent_mode_explicit"})


def iter_config_fields() -> Iterator[Tuple[str, type, Any, Dict[str, Any]]]:
    """Yield (field_name, field_type, default_value, metadata) for every non-internal Config field."""
    for f in fields(Config):
        if f.name in _INTERNAL_FIELDS:
            continue
        default = f.default if f.default is not MISSING else None
        yield f.name, f.type, default, f.metadata


def get_env_map() -> Dict[str, str]:
    """Return a dict mapping Config field name → env var name.

    Only fields whose metadata contains an ``"env"`` key are included.
    """
    result = {}
    for name, _typ, _default, meta in iter_config_fields():
        env = meta.get("env")
        if env:
            result[name] = env
    return result


def get_yaml_map() -> Dict[str, str]:
    """Return a dict mapping Config field name → YAML path.

    Only fields whose metadata contains a ``"yaml"`` key are included.
    """
    result = {}
    for name, _typ, _default, meta in iter_config_fields():
        yaml_path = meta.get("yaml")
        if yaml_path:
            result[name] = yaml_path
    return result
```

- [ ] **Step 2: Write test for contract helpers**

Create `tests/test_config_contract.py`:

```python
"""Tests for src.config.contract helpers."""

import pytest
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
    # At minimum, annotated fields should appear
    assert isinstance(env_map, dict)
    for field_name, env_name in env_map.items():
        assert env_name.isupper(), f"{field_name} → {env_name} is not uppercase"


def test_get_yaml_map_returns_only_annotated_fields():
    yaml_map = get_yaml_map()
    assert isinstance(yaml_map, dict)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config_contract.py -v`
Expected: ImportError or similar since `contract.py` doesn't exist yet

- [ ] **Step 4: Add first metadata annotations to 10 representative Config fields**

In `src/config/manager.py`, find these fields and add `metadata=`:

```python
stock_list: List[str] = field(default_factory=list, metadata={
    "env": "STOCK_LIST",
    "group": "core",
})
report_dir: str = field(default="./report", metadata={
    "env": "REPORT_DIR",
    "yaml": "system.report_dir",
    "group": "system",
})
wechat_webhook_url: Optional[str] = field(default=None, metadata={
    "env": "WECHAT_WEBHOOK_URL",
    "yaml": "notification.wechat_webhook_url",
    "group": "notification",
})
email_sender: Optional[str] = field(default=None, metadata={
    "env": "EMAIL_SENDER",
    "group": "notification",
})
litellm_model: str = field(default="", metadata={
    "env": "LITELLM_MODEL",
    "yaml": "llm.primary_model",
    "group": "llm",
})
report_language: str = field(default="zh", metadata={
    "env": "REPORT_LANGUAGE",
    "yaml": "notification.report_language",
    "group": "notification",
})
news_max_age_days: int = field(default=7, metadata={
    "env": "NEWS_MAX_AGE_DAYS",
    "yaml": "data.news_max_age_days",
    "group": "data",
})
schedule_enabled: bool = field(default=False, metadata={
    "env": "SCHEDULE_ENABLED",
    "yaml": "system.schedule_enabled",
    "group": "system",
})
debug: bool = field(default=False, metadata={
    "env": "DEBUG",
    "yaml": "system.debug",
    "group": "system",
})
log_level: str = field(default="INFO", metadata={
    "env": "LOG_LEVEL",
    "yaml": "system.log_level",
    "group": "system",
})
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m pytest tests/test_config_contract.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/config/contract.py tests/test_config_contract.py src/config/manager.py
git commit -m "feat: add config contract metadata infra and 10 annotated fields"
```

---

### Task 2: Config consistency checker script

**Files:**
- Create: `scripts/check_config_contract.py`
- Create: `tests/test_check_config_contract.py`
- Modify: `.github/workflows/ci.yml` — add config-contract job

- [ ] **Step 1: Write the checker script**

`scripts/check_config_contract.py`:

```python
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
```

- [ ] **Step 2: Write tests for the checker**

`tests/test_check_config_contract.py`:

```python
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
        assert issues == 0  # no strict → warning only, 0 issues

    def test_missing_file_returns_issue_when_strict(self):
        issues = check_env_example(Path("/nonexistent/.env.example"), strict=True)
        assert issues == 1

    def test_env_vars_with_metadata_should_not_be_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env.example"
            env_path.write_text("STOCK_LIST=600519\nLOG_LEVEL=INFO\n", encoding="utf-8")
            with patch("src.config.contract.get_env_map", return_value={"stock_list": "STOCK_LIST", "log_level": "LOG_LEVEL", "report_dir": "REPORT_DIR"}):
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
            with patch("src.config.contract.get_yaml_map", return_value={"report_dir": "system.report_dir", "log_level": "system.log_level"}):
                issues = check_config_yaml(yaml_path, strict=True)
                assert issues == 1  # system.report_dir is missing
```

- [ ] **Step 3: Run checker tests**

Run: `python3 -m pytest tests/test_check_config_contract.py -v`
Expected: Tests pass (or some fail initially — fix until green)

- [ ] **Step 4: Integrate into CI workflow**

Add to `.github/workflows/ci.yml` as a `config-contract` job:

```yaml
  config-contract:
    name: 📋 Configuration Contract
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'
      - run: pip install pyyaml
      - run: python scripts/check_config_contract.py --strict
```

- [ ] **Step 5: Commit**

```bash
git add scripts/check_config_contract.py tests/test_check_config_contract.py .github/workflows/ci.yml
git commit -m "feat: add config contract consistency checker and CI job"
```

---

### Task 3: .env.example auto-generator

**Files:**
- Create: `scripts/gen_env_example.py`
- Modify: `.env.example` (regenerated)

- [ ] **Step 1: Write the generator script**

`scripts/gen_env_example.py`:

```python
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
    from src.config.manager import Config

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

        for name, typ, default, meta in sorted(fields_in_group, key=lambda x: x[0]):
            env_name = meta.get("env", "")
            if not env_name:
                continue
            default_str = format_default(default)
            hint = type_hint(typ)

            comment_parts = [hint]
            if "deprecated" in meta and meta["deprecated"]:
                comment_parts.append("deprecated")
            comment = "  # " + ", ".join(comment_parts) if comment_parts else ""

            if default_str:
                lines.append(f"{env_name}={default_str}{comment}")
            else:
                lines.append(f"{env_name}={comment}")

    # Any group not in the explicit order list
    for group_key in groups:
        if group_key not in seen_groups:
            lines.append(f"")
            lines.append(f"# === {group_key} ===")
            for name, typ, default, meta in sorted(groups[group_key], key=lambda x: x[0]):
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
```

- [ ] **Step 2: Run generator to verify output**

Run: `python3 scripts/gen_env_example.py | head -40`
Expected: See structured .env.example output with sections and env vars

- [ ] **Step 3: Regenerate .env.example in place**

Run: `python3 scripts/gen_env_example.py --write`
Expected: `.env.example` overwritten with generated content

- [ ] **Step 4: Verify .env.example passes the consistency check**

Run: `python3 scripts/check_config_contract.py --env-only`
Expected: "OK" or minimal warnings (orphaned vars that lack metadata are expected at this stage)

- [ ] **Step 5: Commit**

```bash
git add scripts/gen_env_example.py .env.example
git commit -m "feat: add .env.example auto-generator and regenerate"
```

---

### Task 4: config.example.yaml auto-generator

**Files:**
- Create: `scripts/gen_config_example.py`
- Modify: `config.example.yaml` (regenerated)

- [ ] **Step 1: Write the generator script**

`scripts/gen_config_example.py`:

```python
#!/usr/bin/env python3
"""Generate config.example.yaml from Config field metadata.

Usage:
    python scripts/gen_config_example.py              # print to stdout
    python scripts/gen_config_example.py --write       # overwrite config.example.yaml
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

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
        return default if default else None  # empty list → null in YAML
    if isinstance(default, dict):
        return default if default else None
    if default == "":
        return None  # empty string → null (let user fill)
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
```

- [ ] **Step 2: Run generator to verify output**

Run: `python3 scripts/gen_config_example.py`
Expected: See structured YAML output

- [ ] **Step 3: Regenerate config.example.yaml in place**

Run: `python3 scripts/gen_config_example.py --write`
Expected: `config.example.yaml` overwritten

- [ ] **Step 4: Verify config.example.yaml passes the consistency check**

Run: `python3 scripts/check_config_contract.py --yaml-only`
Expected: "OK" or minimal warnings

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `python3 -m pytest -x -q --tb=short`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add scripts/gen_config_example.py config.example.yaml
git commit -m "feat: add config.example.yaml auto-generator and regenerate"
```

---

### Task 5: Annotate remaining Config fields

**Files:**
- Modify: `src/config/manager.py` — add metadata to all remaining fields

- [ ] **Step 1: Add metadata to remaining ~100 Config fields**

For each remaining field in `Config` (those not annotated in Task 1), add `metadata={...}`. Use this pattern:

```python
# ── Optional fields get env + group ──
max_workers: int = field(default=3, metadata={
    "env": "MAX_WORKERS",
    "yaml": "system.max_workers",
    "group": "system",
})

# ── Fields without a direct env mapping (derived/list fields) ──
stock_email_groups: List[Tuple[List[str], List[str]]] = field(default_factory=list, metadata={
    "group": "notification",
    "internal": True,  # no direct env mapping, set via _parse_stock_email_groups()
})
```

Rules:
- Every non-internal field gets `env`, `yaml` (where applicable), and `group`
- `group` must be one of: `core`, `llm`, `search`, `agent`, `risk_screen`, `notification`, `data`, `system`, `other`
- `yaml` is optional for purely env-driven fields (e.g., API keys)
- Fields that are derived/set internally (no direct env var) get `"internal": True`

- [ ] **Step 2: Re-run the consistency checker**

Run: `python3 scripts/check_config_contract.py`
Expected: Close to zero warnings; any remaining issues are legitimate orphaned vars

- [ ] **Step 3: Run full test suite**

Run: `python3 -m pytest -x -q --tb=short`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/config/manager.py
git commit -m "feat: annotate all Config fields with metadata"
```

---

### Task 6: config_registry deprecation gate

**Files:**
- Modify: `src/core/config_registry.py` — add deprecation warning to module docstring and add new-entries guard

- [ ] **Step 1: Add deprecation note to config_registry.py**

Add at the top of `_FIELD_DEFINITIONS` or as a module-level comment:

```python
# NOTE: This registry is deprecated. New configuration fields must be
# added to the Config dataclass in src/config/manager.py with metadata.
# This file is kept for WebUI backward compatibility and will be removed
# after all existing entries are migrated.
```

- [ ] **Step 2: Add a simple validation test**

In `tests/test_config_registry.py`:

```python
def test_no_new_registry_entries():
    """Config registry must not grow — use Config.metadata instead."""
    from src.core.config_registry import get_registered_field_keys
    # Allow existing entries; flag any new ones
    count = len(get_registered_field_keys())
    assert count <= 107, f"Registry grew to {count} — use Config metadata instead"
```

This test acts as a speed bump — if someone adds a new entry to the registry, the test fails and directs them to use Config metadata instead.

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/test_config_registry.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/core/config_registry.py tests/test_config_registry.py
git commit -m "chore: add deprecation guard to config_registry — use Config metadata for new fields"
```

---

### Task 7: Integrate check into ci_gate.sh

**Files:**
- Modify: `scripts/ci_gate.sh`

- [ ] **Step 1: Add config contract check to ci_gate.sh**

Find the section in `ci_gate.sh` where deterministic checks run, and add:

```bash
echo ""
echo "============================================"
echo "📋 Config Contract Check"
echo "============================================"
python3 scripts/check_config_contract.py --strict
CONTRACT_EXIT=$?
if [ $CONTRACT_EXIT -ne 0 ]; then
    echo "❌ Config contract check failed"
    exit $CONTRACT_EXIT
fi
echo "✅ Config contract is consistent"
```

- [ ] **Step 2: Run ci_gate.sh to verify**

Run: `bash scripts/ci_gate.sh`
Expected: config contract check passes

- [ ] **Step 3: Commit**

```bash
git add scripts/ci_gate.sh
git commit -m "feat: integrate config contract check into ci_gate.sh"
```

---

## Self-Review

- [ ] Spec coverage: All major sections from the spec are covered (metadata infra, checker, env gen, yaml gen, registry deprecation, CI integration)
- [ ] Placeholder scan: no TBD/TODO, no "add appropriate error handling", no missing code blocks
- [ ] Type consistency: `iter_config_fields()` usage is consistent across all tasks

## Gaps vs Spec

- The CI config-contract GitHub Actions job (spec P0) is in Task 2 but as a `ci.yml` job. The `ci_gate.sh` integration is in Task 7. Both are covered.
- The `gen_env_example.py` and `gen_config_example.py` scripts (spec P1/P2) produce structured output but preserve minimal manual commentary. The auto-generated files will lose some hand-crafted comments — this is by design, since the entire point is that hand-crafted content drifts.
- Config metadata descriptions for generated comments are omitted (spec doesn't require `desc` — generated files use type hints instead).
