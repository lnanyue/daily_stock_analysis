# -*- coding: utf-8 -*-
"""
AI 分析包
"""

from .core import GeminiAnalyzer
from src.config import get_config
from src.schemas.analysis_result import (
    AnalysisResult,
    check_content_integrity,
    apply_placeholder_fill,
)
from .prompt_builder import format_analysis_prompt
from .utils import (
    _is_value_placeholder,
    _derive_chip_health,
    _build_chip_structure_from_data,
    fill_chip_structure_if_needed,
    fill_price_position_if_needed,
    get_stock_name_multi_source,
    build_market_snapshot,
)

__all__ = [
    "GeminiAnalyzer",
    "AnalysisResult",
    "check_content_integrity",
    "apply_placeholder_fill",
    "format_analysis_prompt",
    "_is_value_placeholder",
    "_derive_chip_health",
    "_build_chip_structure_from_data",
    "fill_chip_structure_if_needed",
    "fill_price_position_if_needed",
    "get_stock_name_multi_source",
    "build_market_snapshot",
]
