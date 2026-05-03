# -*- coding: utf-8 -*-
"""
===================================
多智能体辩论分析器 (Red vs Blue) — 增强版
===================================

通过模拟多空双方多轮辩论，提供更平衡、深度、排查风险后的投资决策。
支持：
- 多轮辩论（默认 2 轮，可配置）
- 每轮后双方可见对方观点进行反驳
- 裁判量化评分（bull_score, bear_score, confidence）
- 结构化输出（含每轮观点、评分、最终决策）
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional

from src.analyzer import GeminiAnalyzer, AnalysisResult
from src.enums import ReportType

logger = logging.getLogger(__name__)


class DebateAnalyzer:
    """多轮红蓝对垒辩论分析器。"""

    def __init__(self, config, analyzer: GeminiAnalyzer):
        self.config = config
        self.analyzer = analyzer
        # 辩论轮数（1-3，默认 2）
        self.max_rounds = max(1, min(getattr(config, 'debate_rounds', 2), 3))
        # 是否启用裁判量化评分
        self.enable_judge_scoring = getattr(config, 'debate_judge_scoring', True)

    async def analyze(self, context: Dict[str, Any], news_context: Optional[str] = None) -> AnalysisResult:
        """
        执行多轮红蓝对垒辩论分析流程
        """
        stock_name = context.get('stock_name', '未知股票')
        code = context.get('code', '未知代码')

        logger.info("[%s] 启动多轮红蓝对垒辩论模式（最多 %d 轮）...", code, self.max_rounds)

        # 1. 初始化辩论状态
        debate_history = []
        bull_view = ""
        bear_view = ""

        # 2. 多轮辩论
        for round_num in range(1, self.max_rounds + 1):
            logger.info("[%s] 辩论第 %d 轮开始...", code, round_num)

            # 构建带历史的 prompt
            bull_prompt = self._build_debater_prompt(
                "bull", context, news_context,
                bear_view if round_num > 1 else None, round_num
            )
            bear_prompt = self._build_debater_prompt(
                "bear", context, news_context,
                bull_view if round_num > 1 else None, round_num
            )

            # 并发调用红蓝双方
            bull_view, bear_view = await asyncio.gather(
                self._call_agent(bull_prompt, "红方(多头)"),
                self._call_agent(bear_prompt, "蓝方(空头)"),
            )

            debate_history.append({
                "round": round_num,
                "bull_view": bull_view,
                "bear_view": bear_view,
            })

        # 3. 裁判量化评分并给出最终决策
        judge_result = await self._call_judge(context, news_context, debate_history)

        # 4. 将裁判决策转换为 AnalysisResult
        final_result = self._build_final_result(context, judge_result, debate_history)

        return final_result

    def _build_debater_prompt(
        self, side: str, context: Dict, news: Optional[str],
        opponent_view: Optional[str], round_num: int
    ) -> str:
        """构建辩论者 Prompt，支持多轮和历史"""
        if side == "bull":
            system = "你是一位极度乐观的【金牌交易员】。"
            system += "你的任务是挖掘该股所有潜在的利好、增长动能、主力吸筹证据和技术面突破信号。"
            system += "请给出最强力的买入理由。"
        else:
            system = "你是一位极度审慎的【首席风控官】。"
            system += "你的任务是吹毛求疵地寻找所有可能的陷阱、利空隐忧、技术面破位、估值过高证据或潜在的暴雷风险。"
            system += "请给出最强力的卖空或规避理由。"

        prompt_parts = [
            f"# {system}",
            "",
            f"请基于以下数据分析股票 {context.get('stock_name')}({context.get('code')})：",
            f"",
            f"## 数据",
            f"{context}",
            "",
        ]

        if self._has_content(news):
            prompt_parts.extend([f"## 新闻", f"{news}", ""])

        if opponent_view:
            prompt_parts.extend([
                f"---",
                f"## 对方第 {round_num-1} 轮观点",
                f"",
                f"{opponent_view}",
                "",
                f"## 你的反驳",
                f"请针对对方观点进行反驳，并强化己方论据。",
                ""
            ])
        else:
            prompt_parts.append("请给出你的分析观点。")

        return "\n".join(prompt_parts)

    async def _call_judge(
        self, context: Dict, news: Optional[str],
        debate_history: List[Dict]
    ) -> Dict[str, Any]:
        """裁判量化评分并给出最终决策"""
        if not self.enable_judge_scoring:
            # 降级：使用原有逻辑
            return await self._fallback_judge(context, news, debate_history)

        # 构建裁判 Prompt
        judge_prompt = self._build_judge_prompt(context, news, debate_history)

        judge_system = """你是一位冷静中立的【投资委员会主席】。
你的任务是听取多空双方的辩论，剔除情绪化的成分，基于事实和概率，给出量化评分和最终决策。

## 输出格式（严格 JSON）
{
  "bull_score": 75,      // 多头说服力评分 (0-100)
  "bear_score": 40,      // 空头说服力评分 (0-100)
  "confidence": 0.8,     // 裁判置信度 (0.0-1.0)
  "final_signal": "buy",  // 最终信号: "buy" | "sell" | "hold"
  "reasoning": "决策理由摘要",
  "key_points": ["要点1", "要点2", "要点3"]
}

## 规则
- bull_score + bear_score 不需要等于 100，反映双方论据质量
- confidence 反映裁判对决策的信心程度
- final_signal 基于论据质量，而非简单多数
"""

        try:
            response, model_used, _ = await self.analyzer._call_litellm_async(
                judge_prompt,
                {"max_tokens": 2048, "temperature": 0.3},
                system_prompt=judge_system,
            )

            if not response:
                logger.warning("裁判评分返回为空，使用降级逻辑")
                return await self._fallback_judge(context, news, debate_history)

            # 解析 JSON
            from src.agent.runner import try_parse_json
            parsed = try_parse_json(response)
            if parsed is None:
                logger.warning("裁判评分 JSON 解析失败，使用降级逻辑")
                return await self._fallback_judge(context, news, debate_history)

            return parsed

        except Exception as e:
            logger.error("裁判评分失败: %s", e, exc_info=True)
            return await self._fallback_judge(context, news, debate_history)

    async def _fallback_judge(
        self, context: Dict, news: Optional[str],
        debate_history: List[Dict]
    ) -> Dict[str, Any]:
        """降级逻辑：使用原有 analyze_async 方法"""
        logger.info("使用降级逻辑（原有 analyze_async）")

        debate_summary = self._build_debate_summary(debate_history)
        final_news_context = (news or "") + "\n\n" + debate_summary

        try:
            result = await self.analyzer.analyze_async(context, final_news_context)
            if result:
                return {
                    "final_signal": result.decision_type or "hold",
                    "confidence": result.sentiment_score / 100.0 if result.sentiment_score else 0.5,
                    "reasoning": result.analysis_summary or "",
                    "bull_score": result.sentiment_score if result.sentiment_score else 50,
                    "bear_score": 100 - (result.sentiment_score if result.sentiment_score else 50),
                    "key_points": [],
                }
        except Exception as e:
            logger.error("降级逻辑也失败: %s", e)

        return {
            "final_signal": "hold",
            "confidence": 0.5,
            "reasoning": "辩论分析未完成，默认持有",
            "bull_score": 50,
            "bear_score": 50,
            "key_points": [],
        }

    def _build_judge_prompt(
        self, context: Dict, news: Optional[str],
        debate_history: List[Dict]
    ) -> str:
        """构建裁判 Prompt，包含所有辩论历史"""
        prompt_parts = [
            f"# 辩论总结 - {context.get('stock_name')}({context.get('code')})",
            "",
            "## 原始数据",
            f"{context}",
            "",
        ]

        if self._has_content(news):
            prompt_parts.extend([f"## 新闻上下文", f"{news}", ""])

        for round_data in debate_history:
            prompt_parts.extend([
                f"---",
                f"## 第 {round_data['round']} 轮辩论",
                "",
                f"### 🔴 红方观点 (多头)",
                f"{round_data['bull_view']}",
                "",
                f"### 🔵 蓝方观点 (空头)",
                f"{round_data['bear_view']}",
                "",
            ])

        prompt_parts.extend([
            "---",
            "请给出量化评分和最终决策（严格 JSON 格式）。",
        ])

        return "\n".join(prompt_parts)

    def _build_debate_summary(self, debate_history: List[Dict]) -> str:
        """构建辩论摘要（用于降级逻辑）"""
        parts = ["### 【多空对垒辩论记录】", ""]
        for rd in debate_history:
            parts.extend([
                f"---",
                f"#### 第 {rd['round']} 轮",
                "",
                f"**🔴 红方观点 (多头优先)**",
                f"{rd['bull_view']}",
                "",
                f"**🔵 蓝方观点 (风险优先)**",
                f"{rd['bear_view']}",
                "",
            ])
        return "\n".join(parts)

    def _build_final_result(
        self, context: Dict, judge_result: Dict,
        debate_history: List[Dict]
    ) -> Optional[AnalysisResult]:
        """将裁判决策转换为 AnalysisResult"""
        code = context.get('code', 'unknown')
        name = context.get('stock_name', code)

        # 映射信号
        final_signal = judge_result.get("final_signal", "hold")
        if final_signal not in ("buy", "sell", "hold"):
            final_signal = "hold"

        # 映射置信度
        confidence = judge_result.get("confidence", 0.5)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.5

        # 计算 sentiment_score（0-100）
        bull_score = judge_result.get("bull_score", 50)
        sentiment_score = int(bull_score) if bull_score else 50

        # 置信度等级
        confidence_level = "高" if confidence > 0.7 else "中" if confidence > 0.4 else "低"

        result = AnalysisResult(
            code=code,
            name=name,
            sentiment_score=sentiment_score,
            decision_type=final_signal,
            confidence_level=confidence_level,
            analysis_summary=f"【多轮辩论结论】{judge_result.get('reasoning', '')}",
        )

        # 保存辩论历史到 dashboard
        result.dashboard = {
            "debate_history": debate_history,
            "judge_score": {
                "bull_score": judge_result.get("bull_score"),
                "bear_score": judge_result.get("bear_score"),
                "confidence": confidence,
            },
            "final_signal": final_signal,
            "key_points": judge_result.get("key_points", []),
        }

        # 新增字段
        result.debate_history = debate_history
        result.judge_score = {
            "bull_score": judge_result.get("bull_score"),
            "bear_score": judge_result.get("bear_score"),
            "confidence": confidence,
        }
        result.debate_rounds = len(debate_history)

        return result

    async def _call_agent(self, prompt: str, agent_name: str) -> str:
        """内部助手：调用特定视角的 AI"""
        try:
            content, model_used, _ = await self.analyzer._call_litellm_async(
                prompt,
                {"max_tokens": 2048, "temperature": 0.7},
            )
            return content or f"{agent_name} 未能给出有效观点。"

        except Exception as e:
            logger.error("%s 分析异常: %s", agent_name, e, exc_info=True)
            return f"{agent_name} 分析出错。"

    @staticmethod
    def _has_content(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return bool(value)
