# -*- coding: utf-8 -*-
"""
数据标准化工具 - 负责将各数据源的原始结果转换为系统内部统一格式。
"""

from typing import Any, Dict, List, Optional
import pandas as pd
import numpy as np

def normalize_source_chain(
    entries: Any,
    provider: str = "unknown",
    result: str = "ok",
    duration_ms: int = 0
) -> List[Dict[str, Any]]:
    """将松散的来源追踪记录转换为结构化列表。"""
    if entries is None:
        return [{"provider": provider, "result": result, "duration_ms": duration_ms}]

    normalized: List[Dict[str, Any]] = []
    if not isinstance(entries, (list, tuple)):
        entries = [entries]

    for item in entries:
        if isinstance(item, dict):
            normalized.append({
                "provider": str(item.get("provider") or provider),
                "result": str(item.get("result") or result),
                "duration_ms": int(item.get("duration_ms", duration_ms)),
            })
            continue

        if item is None: continue

        normalized.append({
            "provider": str(item),
            "result": result,
            "duration_ms": duration_ms,
        })

    return normalized or [{"provider": provider, "result": result, "duration_ms": duration_ms}]

def normalize_belong_boards(raw_data: Any) -> List[Dict[str, Any]]:
    """标准化所属板块数据。"""
    if not raw_data or not isinstance(raw_data, list):
        return []
        
    normalized = []
    for item in raw_data:
        if isinstance(item, str):
            normalized.append({"name": item.strip()})
        elif isinstance(item, dict):
            name = (
                item.get("name")
                or item.get("board_name")
                or item.get("板块名称")
                or item.get("板块")
                or item.get("所属板块")
                or item.get("板块名")
                or item.get("industry")
                or item.get("行业")
            )
            if name:
                board = {"name": str(name).strip()}
                code = item.get("code") or item.get("board_code") or item.get("板块代码") or item.get("代码")
                board_type = item.get("type") or item.get("board_type") or item.get("板块类型") or item.get("类别")
                if code:
                    board["code"] = str(code).strip()
                if board_type:
                    board["type"] = str(board_type).strip()
                normalized.append(board)
    return normalized
