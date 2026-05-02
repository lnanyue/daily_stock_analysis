# -*- coding: utf-8 -*-
"""
Agent 事实核查中间件 - 识别并纠正 LLM 的数值幻觉。
"""

import logging
from typing import Dict, Any, List, Tuple, Optional
from src.analyzer import AnalysisResult

logger = logging.getLogger(__name__)

class FactChecker:
    """
    负责校验 LLM 生成的 AnalysisResult 是否与底层真实数据一致。
    """

    def __init__(self, context: Dict[str, Any]):
        """
        Args:
            context: 增强后的上下文（包含真实的行情、均线、基本面数据）
        """
        self.context = context

    def verify(self, result: AnalysisResult) -> Tuple[bool, List[str]]:
        """
        执行多维度事实校验。
        
        Returns:
            (是否通过, 错误原因列表)
        """
        issues = []
        
        # 1. 价格校验
        real_price = self.context.get("realtime", {}).get("price") or self.context.get("today", {}).get("close")
        if real_price is not None and result.current_price is not None:
            # 允许 0.5% 的舍入误差
            if abs(float(result.current_price) - float(real_price)) / float(real_price) > 0.005:
                issues.append(f"价格幻觉：真实价格为 {real_price}，AI 输出为 {result.current_price}")

        # 2. 涨跌幅校验
        real_chg = self.context.get("realtime", {}).get("change_pct") or self.context.get("price_change_ratio")
        if real_chg is not None and result.change_pct is not None:
            if abs(float(result.change_pct) - float(real_chg)) > 0.1: # 允许 0.1% 的偏差
                issues.append(f"涨跌幅幻觉：真实涨跌幅为 {real_chg}%，AI 输出为 {result.change_pct}%")

        # 3. 均线状态校验 (MA Status)
        real_ma_status = self.context.get("ma_status")
        if real_ma_status and result.trend_prediction:
            is_en = getattr(result, "report_language", "zh") == "en"
            bull_keywords = {"多头", "Bullish", "Upward"} if is_en else {"多头"}
            bear_keywords = {"空头", "Bearish", "Downward"} if is_en else {"空头"}
            
            real_is_bull = any(k in real_ma_status for k in bull_keywords)
            ai_is_bear = any(k in result.trend_prediction for k in bear_keywords)
            
            if real_is_bull and ai_is_bear:
                issues.append(f"技术面幻觉：当前均线为{real_ma_status}，但 AI 预测为{result.trend_prediction}")

        return len(issues) == 0, issues

    def build_correction_prompt(self, issues: List[str], report_language: str = "zh") -> str:
        """构建纠错提示词，要求 LLM 重新评估决策。"""
        issues_text = "\n".join([f"- {issue}" for issue in issues])
        
        if report_language == "en":
            return f"""
### ⚠️ Fact-Check Failed
The following data points in your previous analysis are inconsistent with the ground truth data provided in the context:
{issues_text}

### Task:
Please re-evaluate your core conclusion and trading advice based on the CORRECT data points above. 
Regenerate the full Decision Dashboard JSON. Do not hallucinate numbers again.
"""
        
        return f"""
### ⚠️ 事实核查未通过
你上一次输出的分析中，以下数据与上下文提供的真实数据不符：
{issues_text}

### 任务：
请基于上方修正后的【真实数据】重新评估你的核心结论和交易建议。
请重新输出完整的【决策仪表盘】JSON，确保数据准确无误。
"""
