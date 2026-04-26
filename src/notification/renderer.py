# -*- coding: utf-8 -*-
"""
通知内容渲染逻辑 - 负责生成各种 Markdown 报告
"""

import ast
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.analyzer import AnalysisResult
from src.report_language import (
    get_localized_stock_name,
    get_report_labels,
    get_signal_level,
    get_bias_status_emoji,
    localize_bias_status,
    localize_chip_health,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.utils.data_processing import normalize_model_used

from .utils import format_pct, format_price, get_source_display_name

logger = logging.getLogger(__name__)


class ReportRenderer:
    """
    负责生成各种格式的 Markdown 报告
    """

    def __init__(self):
        self._history_compare_cache = {}

    def _get_report_language(self, result: AnalysisResult) -> str:
        return normalize_report_language(getattr(result, "report_language", "zh"))

    @staticmethod
    def _has_content(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            lowered = value.strip().lower()
            return lowered not in {"", "n/a", "none", "null", "待补充", "数据缺失"}
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    @staticmethod
    def _clean_text(value: Any, default: str = "N/A") -> str:
        if value is None:
            return default
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return default
        return text

    @staticmethod
    def _clean_sniper_value(value: Any) -> str:
        text = ReportRenderer._clean_text(value)
        return "N/A" if text in {"-", "—", ""} else text

    @staticmethod
    def _format_number(value: Any, decimals: int = 2) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return "N/A"
            return text
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _format_percent_value(value: Any, signed: bool = False) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return "N/A"
            return text
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        sign = "+" if signed else ""
        return f"{number:{sign}.2f}%"

    def _format_checklist_item(self, item: Any) -> Optional[str]:
        parsed = item
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return None
            if text.startswith("{") and text.endswith("}"):
                try:
                    parsed = ast.literal_eval(text)
                except (SyntaxError, ValueError):
                    return text
            else:
                return text

        if isinstance(parsed, dict):
            result = self._clean_text(parsed.get("result"), default="").strip()
            question = self._clean_text(parsed.get("question"), default="").strip()
            detail = self._clean_text(parsed.get("detail"), default="").strip()
            line = " ".join(part for part in (result, question, detail) if part)
            return line or None

        text = str(parsed).strip()
        return text or None

    def generate_aggregate_report(self, results: List[AnalysisResult]) -> str:
        """生成多股汇总报告 (个股决策仪表盘)"""
        if not results:
            return "无分析结果"

        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        report_language = self._get_report_language(sorted_results[0]) if sorted_results else "zh"
        labels = get_report_labels(report_language)

        report = [f"# 🚀 {labels['aggregate_report_title']} ({now})\n"]
        report.extend([
            f"## 📊 {labels['summary_heading']}",
            "",
            f"| {labels['stock_label']} | {labels['advice_label']} | {labels['score_label']} | {labels['confidence_label']} | {labels['core_conclusion_label']} |",
            "|:---|:---:|:---:|:---:|:---|",
        ])

        for res in sorted_results:
            _, emoji, _ = get_signal_level(res.operation_advice, res.sentiment_score, report_language)
            name = get_localized_stock_name(res.name, res.code, report_language)
            report.append(
                f"| {name}({res.code}) | **{localize_operation_advice(res.operation_advice, report_language)}** {emoji} | "
                f"{res.sentiment_score} | {res.get_confidence_stars()} | {res.get_core_conclusion()} |"
            )

        report.append(f"\n## 🔍 {labels['details_heading']}")
        for res in sorted_results:
            report.append("\n---\n")
            report.append(self.generate_dashboard_report([res], is_nested=True))

        report.append("\n---")
        report.append(f"*{labels['not_investment_advice']}*")
        return "\n".join(report)

    def generate_dashboard_report(self, results: List[AnalysisResult], is_nested: bool = False) -> str:
        """生成决策仪表盘风格报告"""
        if not results:
            return ""
        if len(results) > 1 and not is_nested:
            return self.generate_aggregate_report(results)

        result = results[0]
        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)

        lines: List[str] = []
        if not is_nested:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            lines.append(f"# 🎯 {labels['decision_dashboard']} ({now})\n")

        self._append_stock_header(lines, result)
        self._append_intelligence(lines, result)
        self._append_market_snapshot(lines, result)
        self._append_data_perspective(lines, result)
        self._append_battle_plan(lines, result)
        self._append_analysis_sections(lines, result)

        if not is_nested:
            lines.append("---")
            model_used = normalize_model_used(getattr(result, "model_used", None))
            if model_used:
                lines.append(f"*{labels['analysis_model_label']}: {model_used}*")
            lines.append(f"*{labels['not_investment_advice']}*")

        return "\n".join(lines)

    def generate_single_stock_report(self, result: AnalysisResult) -> str:
        """兼容单股即时推送入口，输出完整单股报告。"""
        return self.generate_dashboard_report([result])

    def _append_stock_header(self, lines: List[str], result: AnalysisResult) -> None:
        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)
        emoji = result.get_emoji()
        name = get_localized_stock_name(result.name, result.code, report_language)
        localized_trend = localize_trend_prediction(result.trend_prediction, report_language)
        localized_advice = localize_operation_advice(result.operation_advice, report_language)
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        core = dashboard.get("core_conclusion") if isinstance(dashboard.get("core_conclusion"), dict) else {}
        one_sentence = self._clean_text(core.get("one_sentence") or result.analysis_summary)
        pos_advice = core.get("position_advice") if isinstance(core.get("position_advice"), dict) else {}

        lines.extend([
            f"## {emoji} {name} ({result.code})",
            "",
            f"> **{labels['sentiment_score']}**: `{result.sentiment_score}/100` | "
            f"**{labels['trend_prediction']}**: `{localized_trend}` | "
            f"**{labels['confidence_level']}**: `{result.get_confidence_stars()}`",
            f"> **{labels['operation_advice']}**: <font color='red'>**{localized_advice}**</font>",
            "",
            f"### 💡 {labels['core_conclusion_label']}",
            f"{one_sentence}",
            "",
        ])

        if self._has_content(result.analysis_summary) and result.analysis_summary.strip() != one_sentence:
            summary_heading = "综合分析" if report_language == "zh" else "Summary"
            lines.extend([
                f"**🧠 {summary_heading}**",
                result.analysis_summary.strip(),
                "",
            ])

        if pos_advice:
            lines.extend([
                f"| {labels['position_status_label']} | {labels['action_advice_label']} |",
                "|:---|:---|",
                f"| 🆕 **{labels['no_position_label']}** | {self._clean_text(pos_advice.get('no_position'), localized_advice)} |",
                f"| 💼 **{labels['has_position_label']}** | {self._clean_text(pos_advice.get('has_position'), labels['continue_holding'])} |",
                "",
            ])

    def _append_intelligence(self, lines: List[str], result: AnalysisResult) -> None:
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        intel = dashboard.get("intelligence") if isinstance(dashboard.get("intelligence"), dict) else {}
        if not intel:
            return

        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)
        sentiment_summary = self._clean_text(intel.get("sentiment_summary"), default="")
        latest_news = self._clean_text(intel.get("latest_news"), default="")
        risk_alerts = intel.get("risk_alerts") if isinstance(intel.get("risk_alerts"), list) else []
        catalysts = intel.get("positive_catalysts") if isinstance(intel.get("positive_catalysts"), list) else []

        if not any([sentiment_summary, latest_news, risk_alerts, catalysts]):
            return

        lines.extend([
            f"### 📰 {labels['info_heading']}",
            "",
        ])

        if sentiment_summary:
            lines.append(f"**💭 {labels['sentiment_summary_label']}**: {sentiment_summary}")

        if catalysts:
            lines.append("")
            lines.append(f"**✨ {labels['positive_catalysts_label']}**:")
            for item in catalysts:
                lines.append(f"- {self._clean_text(item)}")

        if risk_alerts:
            lines.append("")
            lines.append(f"**🚨 {labels['risk_alerts_label']}**:")
            for item in risk_alerts:
                lines.append(f"- {self._clean_text(item)}")

        if latest_news:
            lines.append("")
            lines.append(f"**📢 {labels['latest_news_label']}**: {latest_news}")

        lines.append("")

    def _append_market_snapshot(self, lines: List[str], result: AnalysisResult) -> None:
        snapshot = getattr(result, "market_snapshot", None)
        if not snapshot:
            return

        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)
        source_display = get_source_display_name(snapshot.get("source"), report_language)

        lines.extend([
            f"### 📈 {labels['market_snapshot_heading']}",
            "",
            f"| {labels['close_label']} | {labels['change_pct_label']} | {labels['open_label']} | {labels['high_label']} | {labels['low_label']} |",
            "|:---|:---:|:---:|:---:|:---:|",
            f"| {self._clean_text(snapshot.get('close'))} | {self._clean_text(snapshot.get('pct_chg'))} | {self._clean_text(snapshot.get('open'))} | {self._clean_text(snapshot.get('high'))} | {self._clean_text(snapshot.get('low'))} |",
            "",
            f"| {labels['volume_label']} | {labels['amount_label']} | {labels['volume_ratio_label']} | {labels['turnover_rate_label']} | {labels['source_label']} |",
            "|:---|:---:|:---:|:---:|:---|",
            f"| {self._clean_text(snapshot.get('volume'))} | {self._clean_text(snapshot.get('amount'))} | {self._clean_text(snapshot.get('volume_ratio'))} | {self._clean_text(snapshot.get('turnover_rate'))} | {source_display} |",
            "",
        ])

    def _append_data_perspective(self, lines: List[str], result: AnalysisResult) -> None:
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        data_persp = dashboard.get("data_perspective") if isinstance(dashboard.get("data_perspective"), dict) else {}
        if not data_persp:
            return

        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)
        trend_data = data_persp.get("trend_status") if isinstance(data_persp.get("trend_status"), dict) else {}
        price_data = data_persp.get("price_position") if isinstance(data_persp.get("price_position"), dict) else {}
        vol_data = data_persp.get("volume_analysis") if isinstance(data_persp.get("volume_analysis"), dict) else {}
        chip_data = data_persp.get("chip_structure") if isinstance(data_persp.get("chip_structure"), dict) else {}

        if not any([trend_data, price_data, vol_data, chip_data]):
            return

        lines.extend([
            f"### 📊 {labels['data_perspective_heading']}",
            "",
        ])

        if trend_data:
            ma_alignment = self._clean_text(trend_data.get("ma_alignment"), default="N/A")
            trend_score = self._clean_text(trend_data.get("trend_score", trend_data.get("trend_strength")), default="N/A")
            is_bullish = (
                f"✅ {labels['yes_label']}"
                if trend_data.get("is_bullish") is True
                else f"❌ {labels['no_label']}" if trend_data.get("is_bullish") is False else "N/A"
            )
            lines.extend([
                f"**{labels['ma_alignment_label']}**: {ma_alignment} | "
                f"{labels['bullish_alignment_label']}: {is_bullish} | "
                f"{labels['trend_strength_label']}: {trend_score}",
                "",
            ])

        if price_data:
            raw_bias_status = price_data.get("bias_status")
            bias_status = localize_bias_status(raw_bias_status, report_language) if self._has_content(raw_bias_status) else ""
            bias_emoji = get_bias_status_emoji(raw_bias_status) if self._has_content(raw_bias_status) else ""
            bias_value = self._format_percent_value(price_data.get("bias_ma5"))
            if bias_status:
                bias_value = f"{bias_value} {bias_emoji}{bias_status}".strip()
            lines.extend([
                f"| {labels['price_metrics_label']} | {labels['current_price_label']} |",
                "|:---|:---|",
                f"| {labels['current_price_label']} | {format_price(price_data.get('current_price'))} |",
                f"| {labels['ma5_label']} | {format_price(price_data.get('ma5'))} |",
                f"| {labels['ma10_label']} | {format_price(price_data.get('ma10'))} |",
                f"| {labels['ma20_label']} | {format_price(price_data.get('ma20'))} |",
                f"| {labels['bias_ma5_label']} | {bias_value} |",
                f"| {labels['support_level_label']} | {format_price(price_data.get('support_level'))} |",
                f"| {labels['resistance_level_label']} | {format_price(price_data.get('resistance_level'))} |",
                "",
            ])

        if vol_data:
            volume_ratio = self._clean_text(vol_data.get("volume_ratio"))
            turnover_rate = self._clean_text(vol_data.get("turnover_rate"))
            volume_status = self._clean_text(vol_data.get("volume_status"), default="")
            volume_meaning = self._clean_text(vol_data.get("volume_meaning"), default="")
            lines.append(
                f"**{labels['volume_label']}**: {labels['volume_ratio_label']} {volume_ratio} "
                f"| {labels['turnover_rate_label']} {turnover_rate}"
            )
            if volume_status or volume_meaning:
                detail = " ".join(part for part in (volume_status, volume_meaning) if part)
                lines.append(f"💡 *{detail}*")
            lines.append("")

        if chip_data:
            raw_chip_health = chip_data.get("chip_health")
            chip_health = localize_chip_health(raw_chip_health, report_language) if self._has_content(raw_chip_health) else "N/A"
            pattern = self._clean_text(
                chip_data.get("pattern") or chip_data.get("pattern_desc") or chip_data.get("pattern_description"),
                default="",
            )
            pattern_line = f" | 形态: {pattern}" if pattern and report_language == "zh" else f" | Pattern: {pattern}" if pattern else ""
            lines.append(
                f"**{labels['chip_label']}**: {self._clean_text(chip_data.get('profit_ratio'))} | "
                f"{self._clean_text(chip_data.get('avg_cost'))} | "
                f"{self._clean_text(chip_data.get('concentration'))} | {chip_health}{pattern_line}"
            )
            lines.append("")

    def _append_battle_plan(self, lines: List[str], result: AnalysisResult) -> None:
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        battle = dashboard.get("battle_plan") if isinstance(dashboard.get("battle_plan"), dict) else {}
        if not battle:
            return

        report_language = self._get_report_language(result)
        labels = get_report_labels(report_language)
        sniper = battle.get("sniper_points") if isinstance(battle.get("sniper_points"), dict) else {}
        checklist = battle.get("action_checklist") if isinstance(battle.get("action_checklist"), list) else []

        if not sniper and not checklist:
            return

        lines.extend([
            f"### 🎯 {labels['battle_plan_heading']}",
            "",
        ])

        sniper_values = {
            "ideal_buy": self._clean_sniper_value(sniper.get("ideal_buy")),
            "secondary_buy": self._clean_sniper_value(sniper.get("secondary_buy")),
            "stop_loss": self._clean_sniper_value(sniper.get("stop_loss")),
            "take_profit": self._clean_sniper_value(sniper.get("take_profit")),
        }
        if any(value != "N/A" for value in sniper_values.values()):
            lines.extend([
                f"| {labels['action_points_heading']} | {labels['current_price_label']} |",
                "|:---|:---|",
                f"| 🎯 {labels['ideal_buy_label']} | {sniper_values['ideal_buy']} |",
                f"| 🔵 {labels['secondary_buy_label']} | {sniper_values['secondary_buy']} |",
                f"| 🛑 {labels['stop_loss_label']} | {sniper_values['stop_loss']} |",
                f"| 🎊 {labels['take_profit_label']} | {sniper_values['take_profit']} |",
                "",
            ])

        normalized_items = [self._format_checklist_item(item) for item in checklist]
        normalized_items = [item for item in normalized_items if item]
        if normalized_items:
            lines.extend([
                f"**✅ {labels['checklist_heading']}**",
                "",
            ])
            for item in normalized_items:
                lines.append(f"- {item}")
            lines.append("")

    def _append_analysis_sections(self, lines: List[str], result: AnalysisResult) -> None:
        report_language = self._get_report_language(result)
        technical_heading = "技术面" if report_language == "zh" else "Technicals"
        fundamental_heading = "基本面" if report_language == "zh" else "Fundamentals"
        news_heading = "消息面" if report_language == "zh" else "News Flow"
        risk_heading = "风险提示" if report_language == "zh" else "Risk Warning"

        technical_parts = [
            self._clean_text(result.technical_analysis, default=""),
            self._clean_text(result.trend_analysis, default=""),
            self._clean_text(result.ma_analysis, default=""),
            self._clean_text(result.volume_analysis, default=""),
            self._clean_text(result.pattern_analysis, default=""),
        ]
        technical_parts = [part for part in technical_parts if part]
        if technical_parts:
            lines.extend([
                f"### 📐 {technical_heading}",
                "",
                "\n\n".join(dict.fromkeys(technical_parts)),
                "",
            ])

        fundamental_parts = [
            self._clean_text(result.fundamental_analysis, default=""),
            self._clean_text(result.sector_position, default=""),
            self._clean_text(result.company_highlights, default=""),
        ]
        fundamental_parts = [part for part in fundamental_parts if part]
        if fundamental_parts:
            lines.extend([
                f"### 🏢 {fundamental_heading}",
                "",
                "\n\n".join(dict.fromkeys(fundamental_parts)),
                "",
            ])

        if self._has_content(result.news_summary):
            lines.extend([
                f"### 🗞️ {news_heading}",
                "",
                result.news_summary.strip(),
                "",
            ])

        if self._has_content(result.risk_warning):
            lines.extend([
                f"### ⚠️ {risk_heading}",
                "",
                result.risk_warning.strip(),
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
            lines.append(
                f"{emoji} {name}({r.code}): "
                f"{localize_operation_advice(r.operation_advice, report_language)} | {r.sentiment_score}"
            )

        return "\n".join(lines)
