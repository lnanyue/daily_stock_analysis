# -*- coding: utf-8 -*-
"""
===================================
多智能体辩论分析器 (Red vs Blue)
===================================

通过模拟多空双方辩论，提供更平衡、深度、排查风险后的投资决策。
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from src.analyzer import GeminiAnalyzer, AnalysisResult
from src.enums import ReportType

logger = logging.getLogger(__name__)

class DebateAnalyzer:
    def __init__(self, config, analyzer: GeminiAnalyzer):
        self.config = config
        self.analyzer = analyzer

    async def analyze(self, context: Dict[str, Any], news_context: Optional[str] = None) -> AnalysisResult:
        """
        执行红蓝对垒分析流程
        """
        stock_name = context.get('stock_name', '未知股票')
        code = context.get('code', '未知代码')
        
        logger.info("[%s] 启动红蓝对垒辩论模式...", code)

        # 1. 定义三方视角 Prompt
        bull_system = "你是一位极度乐观的【金牌交易员】。你的任务是挖掘该股所有潜在的利好、增长动能、主力吸筹证据和技术面突破信号。请给出最强力的买入理由。"
        bear_system = "你是一位极度审慎的【首席风控官】。你的任务是吹毛求疵地寻找所有可能的陷阱、利空隐忧、技术面破位、估值过高证据或潜在的暴雷风险。请给出最强力的卖空或规避理由。"
        judge_system = "你是一位冷静中立的【投资委员会主席】。你的任务是听取多空双方（红方和蓝方）的辩论，剔除情绪化的成分，基于事实和概率，给出最终的客观评价和操作建议。"

        # 2. 并发调用红蓝双方
        # 我们复用 GeminiAnalyzer 的 generate_text 方法
        tasks = [
            self._call_agent(bull_system, context, news_context, "红方(多头)"),
            self._call_agent(bear_system, context, news_context, "蓝方(空头)")
        ]
        
        bull_view, bear_view = await asyncio.gather(*tasks)

        # 3. 汇总辩论内容提交给裁判
        debate_summary = f"""
### 【多空对垒辩论记录】

---
#### 🔴 红方观点 (多头优先)
{bull_view}

---
#### 🔵 蓝方观点 (风险优先)
{bear_view}
"""
        
        logger.info("[%s] 辩论完成，正在汇总最终决策...", code)

        # 4. 裁判给出最终结构化结果
        # 这里使用 analyze_async 方法
        final_news_context = (news_context or "") + "\n\n" + debate_summary
        
        result = await self.analyzer.analyze_async(
            context, 
            final_news_context
        )
        
        # 将辩论过程保存到结果中（可选，用于展示）
        if result and hasattr(result, 'analysis_summary'):
            result.analysis_summary = f"【红蓝对垒结论】\n{result.analysis_summary}"
            
        return result

    async def _call_agent(self, system_prompt: str, context: Dict[str, Any], news_context: Optional[str], agent_name: str) -> str:
        """内部助手：调用特定视角的 AI"""
        # 构建一个简单的 Prompt 给 Agent
        prompt = f"请基于以下数据分析股票 {context.get('stock_name')}({context.get('code')})：\n\n数据：{context}\n\n新闻：{news_context}"
        
        try:
            # 使用原生异步方法
            content = await self.analyzer.generate_text_async(
                prompt,
                2048,
                0.7
            )
            return content or f"{agent_name} 未能给出有效观点。"
        except Exception as e:
            logger.error("%s 分析异常: %s", agent_name, e)
            return f"{agent_name} 分析出错。"
