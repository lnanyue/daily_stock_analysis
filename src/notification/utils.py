# -*- coding: utf-8 -*-
"""
通知层辅助工具与常量
"""

from typing import Any, Optional, Dict
from src.report_language import normalize_report_language

# Display name mapping for realtime data sources
SOURCE_DISPLAY_NAMES = {
    "tencent": {"zh": "腾讯财经", "en": "Tencent Finance"},
    "akshare_em": {"zh": "东方财富", "en": "Eastmoney"},
    "akshare_sina": {"zh": "新浪财经", "en": "Sina Finance"},
    "akshare_qq": {"zh": "腾讯财经", "en": "Tencent Finance"},
    "efinance": {"zh": "东方财富(efinance)", "en": "Eastmoney (efinance)"},
    "tushare": {"zh": "Tushare Pro", "en": "Tushare Pro"},
    "sina": {"zh": "新浪财经", "en": "Sina Finance"},
    "fallback": {"zh": "降级兜底", "en": "Fallback"},
}

def get_source_display_name(source: Any, language: Optional[str]) -> str:
    """获取数据源的展示名称"""
    raw_source = str(source or "N/A")
    mapping = SOURCE_DISPLAY_NAMES.get(raw_source)
    if not mapping:
        return raw_source
    return mapping[normalize_report_language(language)]

def format_price(value: Any) -> str:
    """格式化价格"""
    if value is None: return "N/A"
    try: return f"{float(value):.2f}"
    except: return str(value)

def format_pct(value: Any) -> str:
    """格式化百分比"""
    if value is None: return "N/A"
    try: return f"{float(value):+.2f}%"
    except: return str(value)
