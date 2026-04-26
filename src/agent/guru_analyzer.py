# -*- coding: utf-8 -*-
"""
===================================
大师思维模型分析器 (Buffett & Munger & Leader)
===================================

职责：
1. 模拟巴菲特的价值投资逻辑（护城河、高 ROE）。
2. 模拟芒格的多元思维模型（逆向思维、Lollapalooza 效应）。
3. 识别“龙头股”特征（市场地位、溢价能力）。
"""

import logging
from typing import Dict, Any, Optional
from src.analyzer import GeminiAnalyzer

logger = logging.getLogger(__name__)

class GuruAnalyzer:
    def __init__(self, analyzer: GeminiAnalyzer):
        self.analyzer = analyzer

    async def analyze(self, context: Dict[str, Any], news_context: Optional[str] = None) -> str:
        """
        执行大师维度的深度评估
        """
        stock_name = context.get('stock_name', '未知股票')
        code = context.get('code', '未知代码')
        
        # 准备财务质量数据
        metrics = context.get('fundamental', {}).get('quality_metrics', {})
        roe_status = f"{metrics.get('avg_roe', 0):.2f}%" if metrics else "未知"
        margin_status = f"毛利 {metrics.get('avg_gross_margin', 0):.2f}% / 净利 {metrics.get('avg_net_margin', 0):.2f}%" if metrics else "未知"

        # 准备 Prompt
        prompt = f"""
你现在是巴菲特（Warren Buffett）和查理·芒格（Charlie Munger）的合体分析师。
请针对股票 {stock_name}({code})，结合以下数据进行一次深刻的“灵魂审视”。

### 待分析数据
1. 财务质量：ROE={roe_status}, 利润率={margin_status}
2. 资金面与题材：{context.get('money_flow', '暂无数据')}
3. 市场情报：{news_context}

### 审视维度
1. 【巴菲特视角：护城河与商业模式】
   - 这个生意是“湿的雪”和“长的坡”吗？
   - 基于财务数据，它具备特许经营权（Moat）吗？
   - 它的盈利是否具有确定性和简单性？

2. 【芒格视角：逆向思维与检查清单】
   - 反过来想：如果这个公司在未来5年破产，最可能的原因是什么？
   - 是否存在由于“多种心理倾向共同作用”导致的 Lollapalooza 效应（极端估值或极端共识）？
   - 是否在自己的“能力圈”范围内？

3. 【龙头地位识别】
   - 它在所属行业中是具备溢价权的“真龙头”，还是被题材裹挟的“伪龙头”？
   - 是否具备“强者恒强”的马太效应？

### 输出要求
- 用巴菲特和芒格那种智慧、幽默、且充满洞察力的口吻。
- 给出最终的【大师评级】：(极品/优选/平庸/回避)
- 给出最核心的一句【大师赠言】。

请开始你的深度审判：
"""
        
        try:
            content = await self.analyzer.generate_text_async(
                prompt,
                max_tokens=2048,
                temperature=0.8, # 稍微高一点随机性，增加大师的“智慧感”
            )
            return content or "大师陷入了沉思，未给结论。"
        except Exception as e:
            logger.error("Guru Analysis failed: %s", e)
            return f"由于系统波动，大师拒绝了本次面谈: {e}"
