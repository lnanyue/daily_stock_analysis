# -*- coding: utf-8 -*-
"""
Agent-specific text processing utilities.
"""

from __future__ import annotations

from typing import Any, Dict, List


def first_non_empty_text(*values: Any) -> str:
    """Return the first string that is not None or whitespace."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def truncate_text(text: Any, limit: int) -> str:
    """Truncate text to limit, adding an ellipsis if needed."""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def extract_evidence_text(item: Any) -> str:
    """Extract a descriptive string from an evidence item (string or dict)."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("description") or item.get("title") or "").strip()
    return ""


def extract_latest_news_title(intelligence: Dict[str, Any]) -> str:
    """Extract the most relevant news title from an intelligence block."""
    key_news = intelligence.get("key_news")
    if isinstance(key_news, list):
        for item in key_news:
            if isinstance(item, dict):
                title = str(item.get("title", "")).strip()
                if title:
                    return title
    latest_news = intelligence.get("latest_news")
    if isinstance(latest_news, str) and latest_news.strip():
        return latest_news.strip()
    return ""
