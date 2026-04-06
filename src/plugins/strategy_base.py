# -*- coding: utf-8 -*-
"""
分析策略插件基类与数据结构
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd


@dataclass
class AnalysisContext:
    """传递给策略插件的分析上下文"""
    stock_code: str
    price_data: pd.DataFrame
    indicators: Dict[str, Any]
    search_results: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyResult:
    """策略返回结果"""
    name: str
    title: str
    summary: str
    content: Dict[str, Any] = field(default_factory=dict)
    raw_data: Optional[Any] = None
    error: Optional[str] = None


class AnalysisStrategy(ABC):
    """分析策略插件必须继承此基类"""

    name: str = "base_strategy"

    def __init__(self, config: Dict[str, Any], plugin_ctx):
        self.config = config
        self.ctx = plugin_ctx

    @abstractmethod
    def execute(self, analysis_ctx: AnalysisContext) -> StrategyResult:
        """
        执行分析策略。
        策略内部应自行处理异常或向上抛出，由 Pipeline 统一捕获。
        不应返回 None，应返回包含 error 字段的 StrategyResult。
        """
        ...
