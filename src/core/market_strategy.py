# -*- coding: utf-8 -*-
"""Market strategy blueprints for CN/US daily market recap."""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class StrategyDimension:
    """Single strategy dimension used by market recap prompts."""

    name: str
    objective: str
    checkpoints: List[str]


@dataclass(frozen=True)
class MarketStrategyBlueprint:
    """Region specific market strategy blueprint."""

    region: str
    title: str
    positioning: str
    principles: List[str]
    dimensions: List[StrategyDimension]
    action_framework: List[str]

    def to_prompt_block(self) -> str:
        """Render blueprint as prompt instructions."""
        principles_text = "\n".join([f"- {item}" for item in self.principles])
        action_text = "\n".join([f"- {item}" for item in self.action_framework])

        dims = []
        for dim in self.dimensions:
            checkpoints = "\n".join([f"  - {cp}" for cp in dim.checkpoints])
            dims.append(f"- {dim.name}: {dim.objective}\n{checkpoints}")
        dimensions_text = "\n".join(dims)

        return (
            f"## Strategy Blueprint: {self.title}\n"
            f"{self.positioning}\n\n"
            f"### Strategy Principles\n{principles_text}\n\n"
            f"### Analysis Dimensions\n{dimensions_text}\n\n"
            f"### Action Framework\n{action_text}"
        )

    def to_markdown_block(self) -> str:
        """Render blueprint as markdown section for template fallback report."""
        dims = "\n".join([f"- **{dim.name}**: {dim.objective}" for dim in self.dimensions])
        section_title = "### 六、策略框架" if self.region == "cn" else "### VI. Strategy Framework"
        return f"{section_title}\n{dims}\n"


CN_BLUEPRINT = MarketStrategyBlueprint(
    region="cn",
    title="A股市场三段式复盘策略",
    positioning="聚焦指数趋势、资金博弈与板块轮动，形成次日交易计划。",
    principles=[
        "先看指数方向，再看量能结构，最后看板块持续性。",
        "结论必须映射到仓位、节奏与风险控制动作。",
        "判断使用当日数据与近3日新闻，不臆测未验证信息。",
    ],
    dimensions=[
        StrategyDimension(
            name="趋势结构",
            objective="判断市场处于上升、震荡还是防守阶段。",
            checkpoints=["上证/深证/创业板是否同向", "放量上涨或缩量下跌是否成立", "关键支撑阻力是否被突破"],
        ),
        StrategyDimension(
            name="资金情绪",
            objective="识别短线风险偏好与情绪温度。",
            checkpoints=["涨跌家数与涨跌停结构", "成交额是否扩张", "高位股是否出现分歧"],
        ),
        StrategyDimension(
            name="主线板块",
            objective="提炼可交易主线与规避方向。",
            checkpoints=["领涨板块是否具备事件催化", "板块内部是否有龙头带动", "领跌板块是否扩散"],
        ),
    ],
    action_framework=[
        "进攻：指数共振上行 + 成交额放大 + 主线强化。",
        "均衡：指数分化或缩量震荡，控制仓位并等待确认。",
        "防守：指数转弱 + 领跌扩散，优先风控与减仓。",
    ],
)

US_BLUEPRINT = MarketStrategyBlueprint(
    region="us",
    title="US Market Regime Strategy",
    positioning="Focus on index trend, macro narrative, and sector rotation to define next-session risk posture.",
    principles=[
        "Read market regime from S&P 500, Nasdaq, and Dow alignment first.",
        "Separate beta move from theme-driven alpha rotation.",
        "Translate recap into actionable risk-on/risk-off stance with clear invalidation points.",
    ],
    dimensions=[
        StrategyDimension(
            name="Trend Regime",
            objective="Classify the market as momentum, range, or risk-off.",
            checkpoints=[
                "Are SPX/NDX/DJI directionally aligned",
                "Did volume confirm the move",
                "Are key index levels reclaimed or lost",
            ],
        ),
        StrategyDimension(
            name="Macro & Flows",
            objective="Map policy/rates narrative into equity risk appetite.",
            checkpoints=[
                "Treasury yield and USD implications",
                "Breadth and leadership concentration",
                "Defensive vs growth factor rotation",
            ],
        ),
        StrategyDimension(
            name="Sector Themes",
            objective="Identify persistent leaders and vulnerable laggards.",
            checkpoints=[
                "AI/semiconductor/software trend persistence",
                "Energy/financials sensitivity to macro data",
                "Volatility signals from VIX and large-cap earnings",
            ],
        ),
    ],
    action_framework=[
        "Risk-on: broad index breakout with expanding participation.",
        "Neutral: mixed index signals; focus on selective relative strength.",
        "Risk-off: failed breakouts and rising volatility; prioritize capital preservation.",
    ],
)

GLOBAL_BLUEPRINT = MarketStrategyBlueprint(
    region="global",
    title="全球市场联动分析策略",
    positioning="整合 A 股与美股数据，识别全球宏观共振与行业联动机会。",
    principles=[
        "联动性分析：识别美股领先板块对 A 股相关行业的启发性（映射逻辑）。",
        "宏观共振：分析美元、美债对全球风险偏好的统一影响。",
        "差异化对冲：在两市分化时寻找相对强势市场或防御方向。",
    ],
    dimensions=[
        StrategyDimension(
            name="指数联动",
            objective="判断中美主要指数是否形成方向共识。",
            checkpoints=["美股纳指/标普对 A 股创业板/赛道股的带动作用", "北向资金与美股走势的关联度", "全球波动率 VIX 的外溢影响"],
        ),
        StrategyDimension(
            name="全球宏观",
            objective="识别驱动两市的统一宏观逻辑。",
            checkpoints=["美联储政策预期对全球流动性的影响", "地缘政治或大宗商品对中美市场的不同冲击", "汇率波动对资金流向的导向"],
        ),
        StrategyDimension(
            name="行业映射",
            objective="挖掘美股强势板块对 A 股的启发性机会。",
            checkpoints=["美股 AI/半导体强势是否映射到 A 股科技板块", "全球定价品种（资源/能化）的联动表现", "跨市场热点题材的接力性"],
        ),
    ],
    action_framework=[
        "进攻：全球风险偏好共振回升 + 美股领涨板块成功映射 A 股 + 量能配合。",
        "中性：两市表现分化，美股映射效应减弱，关注内资主导的独立行情。",
        "防守：全球流动性收紧 + 美股破位引发 A 股联动下跌，优先规避全球定价品种。",
    ],
)


def get_market_strategy_blueprint(region: str) -> MarketStrategyBlueprint:
    """Return strategy blueprint by market region."""
    if region == "us":
        return US_BLUEPRINT
    if region == "global":
        return GLOBAL_BLUEPRINT
    return CN_BLUEPRINT
