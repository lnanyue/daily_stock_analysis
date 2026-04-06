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
    return _read("trading_dashboard.md")


def load_trading_dashboard_legacy(core_trading_skill_policy: str) -> str:
    """Return the legacy trading-dashboard prompt with CORE_TRADING_SKILL_POLICY_ZH injected."""
    template = _read("trading_dashboard_legacy.md")
    return template.replace("{core_trading_skill_policy}", core_trading_skill_policy)


def load_text_analysis() -> str:
    """Return the text-analysis assistant system prompt."""
    return _read("text_analysis.md")
