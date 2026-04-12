# -*- coding: utf-8 -*-
"""
通知内容渲染逻辑 - 负责生成各种 Markdown 报告
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
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
from .utils import get_source_display_name, format_price, format_pct

logger = logging.getLogger(__name__)

class ReportRenderer:
    """
    负责生成各种格式的 Markdown 报告
    """
    
    def __init__(self):
        self._history_compare_cache = {}

    def _get_report_language(self, result: AnalysisResult) -> str:
        return normalize_report_language(getattr(result, "report_language", "zh"))

    def generate_aggregate_report(self, results: List[AnalysisResult]) -> str:
        """生成多股汇总报告 (个股决策仪表盘)"""
        if not results:
            return "无分析结果"

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        # 确定主语言
        report_language = self._get_report_language(results[0]) if results else "zh"
        labels = get_report_labels(report_language)
        
        report = [f"# 🚀 {labels['aggregate_report_title']} ({now})\n"]
        
        # 1. 摘要表
        report.extend([
            f"## 📊 {labels['summary_heading']}",
            "",
            f"| {labels['stock_label']} | {labels['advice_label']} | {labels['score_label']} | {labels['confidence_label']} | {labels['core_conclusion_label']} |",
            "|:---|:---:|:---:|:---:|:---|",
        ])
        
        for res in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
            _, emoji, _ = get_signal_level(res.operation_advice, res.sentiment_score, report_language)
            name = get_localized_stock_name(res.name, res.code, report_language)
            report.append(
                f"| {name}({res.code}) | **{res.operation_advice}** {emoji} | "
                f"{res.sentiment_score} | {res.get_confidence_stars()} | {res.get_core_conclusion()} |"
            )
        
        # 2. 个股详情 (如果不是 summary_only)
        report.append(f"\n## 🔍 {labels['details_heading']}")
        for res in results:
            report.append(f"\n---\n")
            report.append(self.generate_dashboard_report([res], is_nested=True))
            
        report.append("\n---")
        report.append(f"*{labels['not_investment_advice']}*")
        
        return "\n".join(report)

    def generate_dashboard_report(self, results: List[AnalysisResult], is_nested: bool = False) -> str:
        """生成决策仪表盘风格报告"""
        if not results: return ""
        result = results[0]
        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)
        
        lines = []
        if not is_nested:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            lines.append(f"# 🎯 {labels['decision_dashboard']} ({now})\n")

        emoji = result.get_emoji()
        name = get_localized_stock_name(result.name, result.code, report_language)
        lines.append(f"## {emoji} {name} ({result.code})")
        
        # 核心指标卡片
        lines.extend([
            "",
            f"> **{labels['sentiment_score']}**: `{result.sentiment_score}/100` | "
            f"**{labels['trend_prediction']}**: `{result.trend_prediction}` | "
            f"**{labels['confidence_level']}**: `{result.get_confidence_stars()}`",
            f"> **{labels['operation_advice']}**: <font color='red'>**{result.operation_advice}**</font>",
            "",
            f"### 💡 {labels['core_conclusion_label']}",
            f"{result.get_core_conclusion()}",
            "",
        ])

        # 狙击点位与计划
        sp = result.get_sniper_points()
        if sp:
            lines.extend([
                f"### 🎯 {labels['sniper_points_label']}",
                f"- 🟢 **{labels['ideal_buy_label']}**: {sp.get('ideal_buy', 'N/A')}",
                f"- 🟡 **{labels['secondary_buy_label']}**: {sp.get('secondary_buy', 'N/A')}",
                f"- 🛑 **{labels['stop_loss_label']}**: **{sp.get('stop_loss', 'N/A')}**",
                f"- 🏁 **{labels['take_profit_label']}**: {sp.get('take_profit', 'N/A')}",
                "",
            ])

        # 情报与风险
        risks = result.get_risk_alerts()
        if risks:
            lines.append(f"### 🚨 {labels['risk_warning']}")
            for r in risks: lines.append(f"- {r}")
            lines.append("")

        self._append_market_snapshot(lines, result)
        
        if not is_nested:
            lines.append("---")
            model_used = normalize_model_used(getattr(result, "model_used", None))
            if model_used: lines.append(f"*{labels['analysis_model_label']}: {model_used}*")
            lines.append(f"*{labels['not_investment_advice']}*")

        return "\n".join(lines)

    def _append_market_snapshot(self, lines: List[str], result: AnalysisResult) -> None:
        snapshot = getattr(result, 'market_snapshot', None)
        if not snapshot: return

        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)

        lines.extend([
            f"### 📈 {labels['market_snapshot_heading']}",
            "",
            f"| {labels['close_label']} | {labels['change_pct_label']} | {labels['volume_label']} | {labels['turnover_rate_label']} |",
            "|:---|:---:|:---:|:---:|",
            f"| {snapshot.get('close', 'N/A')} | {snapshot.get('pct_chg', 'N/A')} | {snapshot.get('volume', 'N/A')} | {snapshot.get('turnover_rate', 'N/A')} |",
            "",
        ])

    def generate_daily_report(self, results: List[AnalysisResult]) -> str:
        """生成标准每日分析报告"""
        return self.generate_aggregate_report(results)

    def generate_brief_report(self, results: List[AnalysisResult]) -> str:
        """生成简要报告"""
        report_language = self._get_report_language(results[0]) if results else "zh"
        labels = get_report_labels(report_language)
        lines = [f"📊 **{labels['summary_heading']}**", ""]
        
        for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
            _, emoji, _ = get_signal_level(r.operation_advice, r.sentiment_score, report_language)
            name = get_localized_stock_name(r.name, r.code, report_language)
            lines.append(f"{emoji} {name}({r.code}): {r.operation_advice} | {r.sentiment_score}")
        
        return "\n".join(lines)
