# -*- coding: utf-8 -*-
"""
通知内容渲染逻辑 - 负责生成各种 Markdown 报告
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.analyzer import AnalysisResult
from src.enums import ReportType
from src.report_language import (
    get_localized_stock_name,
    get_report_labels,
    get_signal_level,
    localize_chip_health,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.utils.data_processing import normalize_model_used

logger = logging.getLogger(__name__)

class ReportRenderer:
    """
    负责生成各种格式的 Markdown 报告
    """
    
    @staticmethod
    def _format_price(value: Any) -> str:
        if value is None: return "N/A"
        try: return f"{float(value):.2f}"
        except: return str(value)

    @staticmethod
    def _format_pct(value: Any) -> str:
        if value is None: return "N/A"
        try: return f"{float(value):+.2f}%"
        except: return str(value)

    def generate_single_stock_report(self, result: AnalysisResult) -> str:
        """生成单股分析报告"""
        labels = get_report_labels(result.report_language)
        emoji = result.get_emoji()
        
        report = [
            f"# {emoji} {result.name} ({result.code}) {labels['analysis_report']}",
            f"- **{labels['sentiment_score']}**: {result.sentiment_score}/100",
            f"- **{labels['trend_prediction']}**: {result.trend_prediction}",
            f"- **{labels['operation_advice']}**: **{result.operation_advice}**",
            f"- **{labels['confidence_level']}**: {result.get_confidence_stars()}",
            "",
            f"## {labels['analysis_summary']}",
            result.analysis_summary,
            "",
            f"## {labels['risk_warning']}",
            result.risk_warning or labels['no_data'],
        ]
        
        if result.dashboard:
            report.append(f"\n## {labels['decision_dashboard']}")
            # 这里可以添加更复杂的仪表盘渲染逻辑
            
        return "\n".join(report)

    def generate_aggregate_report(self, results: List[AnalysisResult]) -> str:
        """生成多股汇总报告"""
        if not results: return "无分析结果"
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        report = [f"# 🚀 个股决策汇总报告 ({now})\n"]
        
        for res in results:
            emoji = res.get_emoji()
            report.append(f"### {emoji} {res.name} ({res.code})")
            report.append(f"- **建议**: {res.operation_advice} | **评分**: {res.sentiment_score}")
            report.append(f"- **核心结论**: {res.get_core_conclusion()}")
            report.append("")
            
        return "\n".join(report)
