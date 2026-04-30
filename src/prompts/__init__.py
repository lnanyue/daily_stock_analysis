# -*- coding: utf-8 -*-
"""Load LLM prompt templates from bundled files.

This replaces hard-coded prompt strings that previously lived in
`src/analyzer.py`, keeping the module focused on orchestration logic.
"""
from __future__ import annotations

import importlib.resources

_PKG = "src.prompts"


def _read(name: str) -> str:
    """Read a bundled prompt file and return its decoded content."""
    try:
        return importlib.resources.files(_PKG).joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise RuntimeError(f"Bundled prompt template missing or unreadable: {name}") from exc


# ------------------------------------------------------------------
# Public helpers
# ------------------------------------------------------------------

def load_trading_dashboard() -> str:
    """Return the main trading-dashboard system prompt."""
    template = _read("trading_dashboard.md")
    return template.replace(
        "{dashboard_schema_block}",
        load_trading_dashboard_schema("操作理由，引用激活技能或风险框架"),
    )


def load_trading_dashboard_legacy(core_trading_skill_policy: str) -> str:
    """Return the legacy trading-dashboard prompt with CORE_TRADING_SKILL_POLICY_ZH injected."""
    template = _read("trading_dashboard_legacy.md")
    return (
        template
        .replace("{core_trading_skill_policy}", core_trading_skill_policy)
        .replace(
            "{dashboard_schema_block}",
            load_trading_dashboard_schema("操作理由，引用交易理念"),
        )
    )


def load_trading_dashboard_schema(buy_reason_hint: str) -> str:
    """Return the shared trading-dashboard JSON schema block."""
    template = _read("trading_dashboard_schema.md")
    return template.replace("{buy_reason_hint}", buy_reason_hint)


def load_text_analysis() -> str:
    """Return the text-analysis assistant system prompt."""
    return _read("text_analysis.md")
