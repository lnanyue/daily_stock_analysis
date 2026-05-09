# -*- coding: utf-8 -*-
"""
===================================
大盘复盘分析器 (Async Enabled)
===================================

职责：
1. 汇总全市场行情数据
2. 结合宏观新闻进行 AI 分析
3. 生成全局视角复盘报告
"""

import logging
import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Any, List, Optional

from src.config import get_config
from src.report_language import normalize_report_language
from src.search_service import SearchService
from src.core.market_profile import get_profile, MarketProfile
from src.core.market_strategy import get_market_strategy_blueprint
from data_provider import DataFetcherManager

logger = logging.getLogger(__name__)


_ENGLISH_SECTION_PATTERNS = {
    "market_summary": r"###\s*(?:1\.\s*)?Market Summary",
    "index_commentary": r"###\s*(?:2\.\s*)?(?:Index Commentary|Major Indices)",
    "sector_highlights": r"###\s*(?:4\.\s*)?(?:Sector Highlights|Sector/Theme Highlights)",
}

_CHINESE_SECTION_PATTERNS = {
    "market_summary": r"###\s*一、(?:盘面总览|市场总结)",
    "index_commentary": r"###\s*二、(?:指数结构|指数点评|主要指数)",
    "sector_highlights": r"###\s*三、(?:板块主线|热点解读|板块表现)",
    "funds_sentiment": r"###\s*四、(?:资金与情绪|资金动向)",
    "news_catalysts": r"###\s*五、(?:消息催化|后市展望)",
}


@dataclass
class MarketIndex:
    """主要指数数据"""
    code: str
    name: str
    current: float
    change: float
    change_pct: float


@dataclass
class MarketOverview:
    """大盘概览数据"""
    date: str
    indices: List[MarketIndex] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    news: List[Any] = field(default_factory=list)


@dataclass
class MarketAnalysisContext:
    """大盘分析上下文"""
    region: str = "cn"
    date: str = field(default_factory=lambda: date.today().isoformat())
    indices: Dict[str, Any] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    sector_rankings: Dict[str, Any] = field(default_factory=dict)
    market_news: List[Any] = field(default_factory=list)
    macro_news: List[Any] = field(default_factory=list)
    strategy_blueprint: Dict[str, Any] = field(default_factory=dict)


class MarketAnalyzer:
    """
    大盘分析核心类
    """
    
    def _get_review_language(self) -> str:
        configured = normalize_report_language(
            getattr(getattr(self, "config", None), "report_language", "zh")
        )
        if self.region == "us":
            return "en"
        return configured

    def _get_template_review_language(self) -> str:
        return normalize_report_language(
            getattr(getattr(self, "config", None), "report_language", "zh")
        )

    def _get_market_scope_name(self, review_language: str | None = None) -> str:
        review_language = review_language or self._get_review_language()
        if self.region == "us":
            return "US market"
        if self.region == "hk":
            return "Hong Kong market" if review_language == "en" else "港股市场"
        if review_language == "en":
            return "A-share market"
        return "A股市场"

    def _get_turnover_unit_label(self) -> str:
        """Return the turnover unit label for the current market/language."""
        if self.region == "us":
            return "USD bn" if self._get_review_language() == "en" else "十亿美元"
        if self.region == "hk":
            return "HKD bn" if self._get_review_language() == "en" else "十亿港元"
        return "CNY 100m" if self._get_review_language() == "en" else "亿"

    def _format_turnover_value(self, amount_raw: float) -> str:
        """Format raw turnover according to market-specific units."""
        if amount_raw == 0.0:
            return "N/A"
        if self.region in ("us", "hk"):
            return f"{amount_raw / 1e9:.2f}"
        if amount_raw > 1e6:
            return f"{amount_raw / 1e8:.0f}"
        return f"{amount_raw:.0f}"

    def _get_review_title(self, date: str) -> str:
        if self._get_review_language() == "en":
            market_names = {"us": "US Market Recap", "hk": "HK Market Recap"}
            market_name = market_names.get(self.region, "A-share Market Recap")
            return f"## {date} {market_name}"
        return f"## {date} 大盘复盘"

    def _get_index_hint(self) -> str:
        if self._get_review_language() == "en":
            if self.region == "us":
                return "Analyze the key moves in the S&P 500, Nasdaq, Dow, and other major indices."
            if self.region == "hk":
                return "Analyze the key moves in the HSI, Hang Seng Tech, HSCEI, and other major indices."
            return "Analyze the price action in the SSE, SZSE, ChiNext, and other major indices."
        profile = getattr(self, "profile", None)
        return getattr(profile, "prompt_index_hint", "分析主要指数的共振、分化和风格强弱。")

    def _get_strategy_prompt_block(self) -> str:
        if self.region == "hk" and self._get_review_language() == "en":
            return """## Strategy Blueprint: Hong Kong Market Regime Strategy
Focus on HSI trend, southbound flow dynamics, and sector rotation to define next-session risk posture.

### Strategy Principles
- Read market regime from HSI, HSTECH, and HSCEI alignment first.
- Track southbound capital flow as a key sentiment driver.
- Translate recap into actionable risk-on/risk-off stance with clear invalidation points.

### Analysis Dimensions
- Trend Regime: Classify the market as momentum, range, or risk-off.
  - Are HSI/HSTECH/HSCEI directionally aligned
  - Did volume confirm the move
  - Are key index levels reclaimed or lost
- Capital Flows: Map southbound flow and macro narrative into equity risk appetite.
  - Southbound net flow direction and magnitude
  - USD/HKD and China policy implications
  - Breadth and leadership concentration
- Sector Themes: Identify persistent leaders and vulnerable laggards.
  - Tech/internet platform trend persistence
  - Financials/property sensitivity to policy shifts
  - Defensive vs growth factor rotation

### Action Framework
- Risk-on: broad index breakout with expanding southbound participation.
- Neutral: mixed index signals; focus on selective relative strength.
- Risk-off: failed breakouts and rising volatility; prioritize capital preservation."""
        if not (self.region == "cn" and self._get_review_language() == "en"):
            strategy = getattr(self, "strategy", None)
            if strategy is None:
                strategy = get_market_strategy_blueprint(getattr(self, "region", "cn"))
            return strategy.to_prompt_block()
        return """## Strategy Blueprint: A-share Three-Phase Recap Strategy
Focus on index trend, liquidity, and sector rotation to shape the next-session trading plan.

### Strategy Principles
- Read index direction first, then confirm liquidity structure, and finally test sector persistence.
- Every conclusion must map to position sizing, trading pace, and risk-control actions.
- Base judgments on today's data and the latest 3-day news flow without inventing unverified information.

### Analysis Dimensions
- Trend Structure: Determine whether the market is in an uptrend, range, or defensive phase.
  - Are the SSE, SZSE, and ChiNext moving in the same direction
  - Is the market advancing on expanding volume or slipping on contracting volume
  - Have key support or resistance levels been reclaimed or broken
- Liquidity & Sentiment: Identify near-term risk appetite and market temperature.
  - Advance/decline breadth and limit-up/limit-down structure
  - Whether turnover is expanding or fading
  - Whether high-beta leaders are showing divergence
- Leading Themes: Distill tradable leadership and areas to avoid.
  - Whether leading sectors have clear event catalysts
  - Whether sector leaders are pulling the group higher
  - Whether weakness is broadening across lagging sectors

### Action Framework
- Offensive: indices rise in sync, turnover expands, and core themes strengthen.
- Balanced: index divergence or low-volume consolidation; keep sizing controlled and wait for confirmation.
- Defensive: indices weaken and laggards broaden; prioritize risk control and de-risking."""

    def _get_strategy_markdown_block(self, review_language: str | None = None) -> str:
        review_language = review_language or self._get_review_language()
        if self.region == "hk" and review_language == "en":
            return """### 6. Strategy Framework
- **Trend Regime**: Classify the market as momentum, range, or risk-off based on HSI/HSTECH/HSCEI alignment.
- **Capital Flows**: Track southbound flow direction and macro narrative for risk appetite signals.
- **Sector Themes**: Focus on tech/internet platform persistence and financials/property policy sensitivity.
"""
        if not (self.region == "cn" and review_language == "en"):
            return self.strategy.to_markdown_block()
        return """### 6. Strategy Framework
- **Trend Structure**: Determine whether the market is in an uptrend, range, or defensive phase.
- **Liquidity & Sentiment**: Track breadth, turnover expansion, and whether leaders are diverging.
- **Leading Themes**: Focus on sectors with catalysts and sustained leadership while avoiding broadening weakness.
"""

    def _get_market_mood_text(self, mood_key: str, review_language: str | None = None) -> str:
        review_language = review_language or self._get_review_language()
        if review_language == "en":
            mapping = {
                "strong_up": "strong gains",
                "mild_up": "moderate gains",
                "mild_down": "mild losses",
                "strong_down": "clear weakness",
                "range": "range-bound trading",
            }
        else:
            mapping = {
                "strong_up": "强势上涨",
                "mild_up": "小幅上涨",
                "mild_down": "小幅下跌",
                "strong_down": "明显下跌",
                "range": "震荡整理",
            }
        return mapping[mood_key]

    def __init__(self, data_manager: Optional[DataFetcherManager] = None, analyzer = None, search_service: Optional[SearchService] = None, region: str = "cn"):
        self.config = get_config()
        self.data_manager = data_manager or DataFetcherManager(config=self.config)
        self.search_service = search_service or SearchService(
            tavily_keys=self.config.tavily_api_keys,
            finnhub_api_key=getattr(self.config, "finnhub_api_key", None),
            news_max_age_days=self.config.news_max_age_days,
        )
        self.analyzer = analyzer  # GeminiAnalyzer instance
        self.region = region
        self.profile = get_profile(region)
        self.strategy = get_market_strategy_blueprint(region)

    def _get_market_name(self, region: str) -> str:
        names = {"cn": "A股", "us": "美股", "hk": "港股", "global": "全球联动"}
        return names.get(region, region)

    def _pick_value(self, data: Any, *keys: str, default: Any = "N/A") -> Any:
        """Return the first non-empty value from a provider payload."""
        if not isinstance(data, dict):
            return default
        for key in keys:
            value = data.get(key)
            if value is not None and value != "":
                return value
        return default

    def _as_float(self, value: Any) -> Optional[float]:
        if value is None or value == "" or value == "N/A":
            return None
        try:
            return float(str(value).replace(",", "").replace("%", ""))
        except (TypeError, ValueError):
            return None

    def _format_number(self, value: Any, digits: int = 2) -> str:
        number = self._as_float(value)
        if number is None:
            return str(value) if value not in (None, "") else "N/A"
        if number.is_integer():
            return str(int(number))
        return f"{number:.{digits}f}".rstrip("0").rstrip(".")

    def _format_pct_with_direction(self, value: Any) -> str:
        number = self._as_float(value)
        if number is None:
            return self._format_signed_pct(value)
        icon = "🟢" if number > 0 else "🔴" if number < 0 else "⚪"
        return f"{icon} {self._format_signed_pct(number)}"

    def _format_index_amount_yi(self, value: Any) -> str:
        number = self._as_float(value)
        if number is None or number == 0:
            return "N/A"
        # 实时指数接口常返回"元"，表格统一展示为"亿元"。
        if abs(number) > 1_000_000:
            number = number / 100_000_000
        return self._format_number(number, digits=0 if abs(number) >= 100 else 2)

    def _iter_index_rows(self, context: MarketAnalysisContext) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        indices = context.indices
        if isinstance(indices, dict):
            iterable = indices.items()
            for key, value in iterable:
                if isinstance(value, dict):
                    row = dict(value)
                    row.setdefault("name", row.get("name") or key)
                    rows.append(row)
        elif isinstance(indices, list):
            for item in indices:
                if isinstance(item, dict):
                    rows.append(dict(item))
                elif hasattr(item, "__dict__"):
                    rows.append(dict(item.__dict__))
        return rows

    def _build_index_table(self, context: MarketAnalysisContext) -> str:
        rows = self._iter_index_rows(context)
        if not rows:
            return "暂无指数数据"

        lines = [
            "| 指数 | 最新 | 涨跌幅 | 成交额(亿) |",
            "| --- | ---: | ---: | ---: |",
        ]
        for row in rows:
            name = self._pick_value(row, "name", "code", default="N/A")
            current = self._pick_value(row, "current", "price", "close", default="N/A")
            change_pct = self._pick_value(row, "change_pct", "pct_chg", "涨跌幅", default="N/A")
            amount = self._pick_value(
                row,
                "amount",
                "turnover",
                "turnover_amount",
                "total_amount",
                "volume_total",
                "成交额",
                default="N/A",
            )
            lines.append(
                f"| {name} | {self._format_number(current)} | "
                f"{self._format_pct_with_direction(change_pct)} | {self._format_index_amount_yi(amount)} |"
            )
        return "\n".join(lines)

    def _build_breadth_line(self, context: MarketAnalysisContext) -> str:
        stats = context.stats if isinstance(context.stats, dict) else {}
        up = self._pick_value(stats, "up", "up_count", "rise_count")
        down = self._pick_value(stats, "down", "down_count", "fall_count")
        flat = self._pick_value(stats, "flat", "flat_count", "unchanged_count", default="N/A")
        limit_up = self._pick_value(stats, "limit_up", "limit_up_count")
        limit_down = self._pick_value(stats, "limit_down", "limit_down_count")
        turnover = self._pick_value(stats, "volume_total", "total_amount", "amount_total")

        up_num = self._as_float(up)
        down_num = self._as_float(down)
        icon = "📊"
        if up_num is not None and down_num is not None:
            icon = "📈" if up_num >= down_num else "📉"

        return (
            f"{icon} 上涨 {self._format_number(up)} 家 / 下跌 {self._format_number(down)} 家 / "
            f"平盘 {self._format_number(flat)} 家 | 涨停 {self._format_number(limit_up)} / "
            f"跌停 {self._format_number(limit_down)} | 成交额 {self._format_number(turnover)} 亿"
        )

    def _format_sector_item(self, item: Any) -> Optional[str]:
        if not isinstance(item, dict):
            return None
        name = self._pick_value(item, "name", "sector", "板块", default="未知")
        change_pct = self._pick_value(item, "change_pct", "pct_chg", "涨跌幅", "change", default="N/A")
        return f"{name}({self._format_signed_pct(change_pct)})"

    def _build_sector_lines(self, context: MarketAnalysisContext) -> List[str]:
        top_list: List[Any] = []
        bottom_list: List[Any] = []
        if isinstance(context.sector_rankings, dict):
            top_list = context.sector_rankings.get("top") or context.sector_rankings.get("leaders") or []
            bottom_list = context.sector_rankings.get("bottom") or context.sector_rankings.get("laggards") or []

        top_items = [text for text in (self._format_sector_item(item) for item in top_list[:5]) if text]
        bottom_items = [text for text in (self._format_sector_item(item) for item in bottom_list[:5]) if text]
        return [
            f"🔥 领涨: {' | '.join(top_items) if top_items else '暂无'}",
            f"💧 领跌: {' | '.join(bottom_items) if bottom_items else '暂无'}",
        ]

    def _infer_market_state(self, context: MarketAnalysisContext) -> str:
        stats = context.stats if isinstance(context.stats, dict) else {}
        up = self._as_float(self._pick_value(stats, "up", "up_count", "rise_count", default=None))
        down = self._as_float(self._pick_value(stats, "down", "down_count", "fall_count", default=None))
        changes = [
            value
            for value in (
                self._as_float(self._pick_value(row, "change_pct", "pct_chg", "涨跌幅", default=None))
                for row in self._iter_index_rows(context)
            )
            if value is not None
        ]
        avg_change = sum(changes) / len(changes) if changes else 0

        if up is not None and down is not None:
            if up >= down * 1.4 and avg_change >= 0:
                return "进攻"
            if down >= up * 1.2 and avg_change <= 0:
                return "防守"
        if avg_change > 0.3:
            return "进攻"
        if avg_change < -0.3:
            return "防守"
        return "均衡"

    def _get_prompt_scaffold(self, context: MarketAnalysisContext) -> tuple[str, str, str]:
        """Return role, missing-data guidance, and output template by region."""
        if context.region == "global":
            role = "你是一位专业的全球市场分析师，擅长结合宏观环境（利率、汇率、政策、流动性）解读跨市场联动"
            missing_data_guidance = (
                '若市场新闻为空或缺少跨市场消息，不要把"无新闻"直接等同于"无法评估全球市场"；'
                "应明确说明消息面样本有限，并优先基于已提供的指数、统计与板块数据完成复盘。"
            )
            template = f"""## {context.date} 全球市场联动复盘

### 一、全球视野
（总结今日中美市场整体表现及联动主线，结合宏观新闻分析利率、汇率、政策对全球资金流动的影响，2-3句话）

### 二、行情联动点评
（仅基于已提供的数据，对比分析 A 股与美股主要指数的走势特征及相互影响；若缺少某一侧数据，需要明确说明，不要臆测。）

### 三、行业映射与热点
（重点解析强势板块及跨市场映射逻辑）

### 四、后市展望
（结合走势、宏观背景与消息催化，给出后续预判）

### 五、策略建议
（仓位与方向建议；最后补充"建议仅供参考，不构成投资建议"。）"""
            return role, missing_data_guidance, template

        if context.region == "us":
            role = "你是一位专业的美股市场分析师，擅长结合宏观环境（美联储利率、通胀、美债收益率、美元）解读美股走势"
            missing_data_guidance = (
                "若市场新闻为空，请明确说明消息面样本有限，并以已提供的美股指数与主题线索为主完成复盘；"
                "不要臆测 A 股表现或中美联动。"
            )
            template = f"""## {context.date} 美股市场复盘

### 一、市场总览
（总结今日美股整体表现与主要驱动，结合宏观新闻分析利率和通胀预期对市场的影响，2-3句话）

### 二、指数与风格点评
（分析标普、纳指、道指、波动率或风格轮动特征）

### 三、板块与主题
（重点解析强势/弱势板块及主题主线）

### 四、后市展望
（结合走势、宏观背景与消息催化，给出后续预判）

### 五、策略建议
（仓位与方向建议；最后补充"建议仅供参考，不构成投资建议"。）"""
            return role, missing_data_guidance, template

        role = "你是一位专业的A股市场分析师，擅长结合宏观环境（利率、汇率、政策、流动性）解读大盘走势"
        missing_data_guidance = (
            "若市场数据（指数、成交额等）缺失或显示为 N/A，但提供了市场新闻，请务必以新闻和历史背景为主要依据进行推断性复盘；"
            "若当前日期为回溯的交易日，请在报告中明确说明；不要臆测全球市场或跨市场联动。"
        )
        template = f"""## {context.date} 大盘复盘

> 一句话给出今日市场状态、核心矛盾和明日优先观察方向。

### 一、盘面总览
（2-3句话概括指数、涨跌家数、成交额和情绪温度，明确"强势/偏暖/震荡/偏弱"判断；必须保留提供的市场宽度行。）

### 二、指数结构
（{self._get_index_hint()}，说明谁在护盘、谁在拖累，以及关键支撑/压力；必须输出提供的指数表。）

### 三、板块主线
（分析领涨/领跌板块背后的逻辑、持续性和是否形成主线；必须输出"🔥 领涨"和"💧 领跌"两行。）

### 四、资金与情绪
（解读成交额、涨跌停结构、市场宽度和风险偏好。）

### 五、消息催化
（结合近三日新闻和宏观新闻，提炼真正影响明日交易的催化或扰动；必须明确提及利率、汇率、政策或流动性等宏观因子对大盘的影响。）

### 六、明日交易计划
（给出进攻/均衡/防守结论、仓位区间、关注方向、回避方向和一个触发失效条件。）

### 七、风险提示
（列出需要关注的风险点；最后补充"建议仅供参考，不构成投资建议"。）"""
        return role, missing_data_guidance, template

    def search_market_news(self) -> List[Dict]:
        """
        搜索市场新闻
        
        Returns:
            新闻列表
        """
        if not self.search_service:
            logger.warning("[大盘] 搜索服务未配置，跳过新闻搜索")
            return []
        
        all_news = []

        # 按 region 使用不同的新闻搜索词
        search_queries = self.profile.news_queries
        
        try:
            logger.info("[大盘] 开始搜索市场新闻...")
            
            # 根据 region 设置搜索上下文名称，避免美股搜索被解读为 A 股语境
            market_names = {"cn": "大盘", "us": "US market", "hk": "HK market"}
            market_name = market_names.get(self.region, "大盘")
            for query in search_queries:
                response = self.search_service.search_stock_news(
                    stock_code="market",
                    stock_name=market_name,
                    max_results=3,
                    focus_keywords=query.split()
                )
                if response and response.results:
                    all_news.extend(response.results)
                    logger.info(f"[大盘] 搜索 '{query}' 获取 {len(response.results)} 条结果")
            
            logger.info(f"[大盘] 共获取 {len(all_news)} 条市场新闻")
            
        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")
        
        return all_news
    
    def _inject_data_into_review(
        self,
        review: str,
        overview: MarketOverview,
        news: Optional[List] = None,
    ) -> str:
        """Inject structured data tables into the corresponding LLM prose sections."""
        # Build data blocks
        stats_block = self._build_stats_block(overview)
        indices_block = self._build_indices_block(overview)
        sector_block = self._build_sector_block(overview)
        news_block = self._build_news_block(news or [])
        patterns = (
            _ENGLISH_SECTION_PATTERNS
            if self._get_review_language() == "en"
            else _CHINESE_SECTION_PATTERNS
        )

        if stats_block:
            review = self._insert_after_section(
                review,
                patterns["market_summary"],
                stats_block,
            )

        if indices_block:
            review = self._insert_after_section(
                review,
                patterns["index_commentary"],
                indices_block,
            )

        if sector_block:
            review = self._insert_after_section(
                review,
                patterns["sector_highlights"],
                sector_block,
            )

        if news_block and "news_catalysts" in patterns:
            review = self._insert_after_section(
                review,
                patterns["news_catalysts"],
                news_block,
            )

        return review

    @staticmethod
    def _insert_after_section(text: str, heading_pattern: str, block: str) -> str:
        """Insert a data block at the end of a markdown section (before the next ### heading)."""
        import re
        # Find the heading
        match = re.search(heading_pattern, text)
        if not match:
            return text
        start = match.end()
        # Find the next ### heading after this one
        next_heading = re.search(r'\n###\s', text[start:])
        if next_heading:
            insert_pos = start + next_heading.start()
        else:
            # No next heading — append at end
            insert_pos = len(text)
        # Insert the block before the next heading, with spacing
        return text[:insert_pos].rstrip() + '\n\n' + block + '\n\n' + text[insert_pos:].lstrip('\n')

    def _build_stats_block(self, overview: MarketOverview) -> str:
        """Build market statistics block."""
        has_stats = overview.up_count or overview.down_count or overview.total_amount
        if not has_stats:
            return ""
        if self._get_review_language() == "en":
            return (
                f"> 📈 Advancers **{overview.up_count}** / Decliners **{overview.down_count}** / "
                f"Flat **{overview.flat_count}** | "
                f"Limit-up **{overview.limit_up_count}** / Limit-down **{overview.limit_down_count}** | "
                f"Turnover **{overview.total_amount:.0f}** ({self._get_turnover_unit_label()})"
            )
        score, label = self._build_market_temperature(overview)
        participation = overview.up_count + overview.down_count + overview.flat_count
        up_ratio = overview.up_count / participation if participation else 0.0
        limit_spread = overview.limit_up_count - overview.limit_down_count
        lines = [
            f"> **盘面温度**：{label} **{score}/100** {self._build_temperature_bar(score)}",
            "",
            "| 指标 | 数值 | 观察 |",
            "|------|------|------|",
            f"| 上涨/下跌/平盘 | {overview.up_count} / {overview.down_count} / {overview.flat_count} | 上涨占比 {up_ratio:.1%} |",
            f"| 涨停/跌停 | {overview.limit_up_count} / {overview.limit_down_count} | 涨跌停差 {limit_spread:+d} |",
            f"| 两市成交额 | {overview.total_amount:.0f} 亿 | {self._describe_turnover(overview.total_amount)} |",
        ]
        return "\n".join(lines)

    def _build_indices_block(self, overview: MarketOverview) -> str:
        """构建指数行情表格"""
        if not overview.indices:
            return ""
        if self._get_review_language() == "en":
            lines = [
                f"| Index | Last | Change % | Open | High | Low | Amplitude | Turnover ({self._get_turnover_unit_label()}) |",
                "|-------|------|----------|------|------|-----|-----------|-----------------|",
            ]
        else:
            lines = [
                "| 指数 | 最新 | 涨跌幅 | 开盘 | 最高 | 最低 | 振幅 | 成交额(亿) |",
                "|------|------|--------|------|------|------|------|-----------|",
            ]
        for idx in overview.indices:
            arrow = "🔴" if idx.change_pct < 0 else "🟢" if idx.change_pct > 0 else "⚪"
            amount_raw = idx.amount or 0.0
            amount_str = self._format_turnover_value(amount_raw)
            lines.append(
                f"| {idx.name} | {idx.current:.2f} | {arrow} {idx.change_pct:+.2f}% | "
                f"{self._format_optional_number(idx.open)} | {self._format_optional_number(idx.high)} | "
                f"{self._format_optional_number(idx.low)} | {self._format_optional_pct(idx.amplitude)} | {amount_str} |"
            )
        return "\n".join(lines)

    def _build_sector_block(self, overview: MarketOverview) -> str:
        """Build sector ranking block."""
        if not overview.top_sectors and not overview.bottom_sectors:
            return ""
        lines = []
        if overview.top_sectors:
            if self._get_review_language() == "en":
                lines.extend([
                    "#### Leading Sectors",
                    "| Rank | Sector | Change |",
                    "|------|--------|--------|",
                ])
            else:
                lines.extend([
                    "#### 领涨板块 Top 5",
                    "| 排名 | 板块 | 涨跌幅 |",
                    "|------|------|--------|",
                ])
            for rank, sector in enumerate(overview.top_sectors[:5], 1):
                lines.append(
                    f"| {rank} | {sector.get('name', '-')} | {self._format_signed_pct(sector.get('change_pct'))} |"
                )
        if overview.bottom_sectors:
            if lines:
                lines.append("")
            if self._get_review_language() == "en":
                lines.extend([
                    "#### Lagging Sectors",
                    "| Rank | Sector | Change |",
                    "|------|--------|--------|",
                ])
            else:
                lines.extend([
                    "#### 领跌板块 Top 5",
                    "| 排名 | 板块 | 涨跌幅 |",
                    "|------|------|--------|",
                ])
            for rank, sector in enumerate(overview.bottom_sectors[:5], 1):
                lines.append(
                    f"| {rank} | {sector.get('name', '-')} | {self._format_signed_pct(sector.get('change_pct'))} |"
                )
        return "\n".join(lines)

    def _build_news_block(self, news: List) -> str:
        """Build a compact news catalyst table for the rendered report."""
        if not news:
            return ""
        if self._get_review_language() == "en":
            lines = [
                "#### News Catalysts",
                "| # | Headline | Signal |",
                "|---|----------|--------|",
            ]
        else:
            lines = [
                "#### 近三日催化线索",
                "| 序号 | 事件/标题 | 关注点 |",
                "|------|-----------|--------|",
            ]

        for idx, item in enumerate(news[:5], 1):
            if hasattr(item, "title"):
                title = getattr(item, "title", "") or "-"
                snippet = getattr(item, "snippet", "") or ""
            else:
                title = item.get("title", "-") or "-"
                snippet = item.get("snippet", "") or ""
            title = self._escape_table_cell(str(title).strip()[:42])
            signal = self._escape_table_cell(str(snippet).strip().replace("\n", " ")[:58] or "-")
            lines.append(f"| {idx} | {title} | {signal} |")
        return "\n".join(lines)

    def _build_fallback_news_section(self, context: MarketAnalysisContext) -> str:
        """Build the news catalyst section for fallback (no-LLM) reports."""
        lines = []
        if context.market_news:
            for n in context.market_news[:3]:
                title = getattr(n, 'title', '') or (n.get('title') if isinstance(n, dict) else '')
                if title:
                    lines.append(f"- {title}")
        if context.macro_news:
            macro_titles = []
            for n in context.macro_news[:3]:
                title = getattr(n, 'title', '') or (n.get('title') if isinstance(n, dict) else '')
                if title:
                    macro_titles.append(title)
            if macro_titles:
                lines.append(f"- 🌐 宏观: {'; '.join(macro_titles)}")
        if lines:
            return "\n".join(lines)
        return "- 暂无可用新闻时，应降低对题材持续性的确定性判断。"

    @staticmethod
    def _format_optional_number(value: float) -> str:
        return "N/A" if value in (None, 0, 0.0) else f"{value:.2f}"

    @staticmethod
    def _format_optional_pct(value: float) -> str:
        return "N/A" if value in (None, 0, 0.0) else f"{value:.2f}%"

    @staticmethod
    def _format_signed_pct(value: Any) -> str:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "N/A"
        return f"{numeric_value:+.2f}%"

    @staticmethod
    def _escape_table_cell(value: str) -> str:
        return value.replace("|", "\\|")

    @staticmethod
    def _build_temperature_bar(score: int) -> str:
        filled = max(0, min(10, round(score / 10)))
        return "█" * filled + "░" * (10 - filled)

    @staticmethod
    def _describe_turnover(total_amount: float) -> str:
        if total_amount >= 15000:
            return "高活跃度"
        if total_amount >= 9000:
            return "中等活跃"
        if total_amount > 0:
            return "缩量观望"
        return "暂无数据"

    def _build_market_temperature(self, overview: MarketOverview) -> tuple[int, str]:
        participants = overview.up_count + overview.down_count
        breadth_score = 50
        if participants:
            breadth_score = int(overview.up_count / participants * 100)

        index_changes = [idx.change_pct for idx in overview.indices if idx.change_pct is not None]
        index_score = 50
        if index_changes:
            avg_change = sum(index_changes) / len(index_changes)
            index_score = int(max(0, min(100, 50 + avg_change * 12)))

        limit_total = overview.limit_up_count + overview.limit_down_count
        limit_score = 50
        if limit_total:
            limit_score = int(overview.limit_up_count / limit_total * 100)

        score = int(round(breadth_score * 0.45 + index_score * 0.35 + limit_score * 0.20))
        if self._get_review_language() == "en":
            if score >= 70:
                label = "risk-on"
            elif score >= 55:
                label = "constructive"
            elif score >= 40:
                label = "mixed"
            else:
                label = "defensive"
        else:
            if score >= 70:
                label = "强势"
            elif score >= 55:
                label = "偏暖"
            elif score >= 40:
                label = "震荡"
            else:
                label = "偏弱"
        return score, label

    def generate_market_review(self, context: MarketAnalysisContext, news: List[Any]) -> str:
        """同步复盘分析入口 (封装异步调用以兼容旧代码)"""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            # 适配旧参数：如果传入的是 MarketOverview，则转换为 MarketAnalysisContext
            if not isinstance(context, MarketAnalysisContext):
                ctx = MarketAnalysisContext(
                    date=getattr(context, "date", date.today().isoformat()),
                    region=getattr(self, "region", "cn"),
                    indices={idx.code: idx.__dict__ for idx in getattr(context, "indices", [])},
                    stats=getattr(context, "stats", {}) if hasattr(context, "stats") else {},
                    market_news=news,
                )
            else:
                ctx = context
            return loop.run_until_complete(self._generate_report(ctx))
        finally:
            loop.close()

    def _build_review_prompt(self, overview: MarketOverview, news: List[Any]) -> str:
        """向后兼容的旧 Prompt 构建入口。"""
        context = MarketAnalysisContext(
            date=getattr(overview, "date", date.today().isoformat()),
            region=getattr(self, "region", "cn"),
            indices={idx.code: idx.__dict__ for idx in getattr(overview, "indices", [])},
            stats=getattr(overview, "stats", {}) if hasattr(overview, "stats") else {},
            market_news=news or [],
            strategy_blueprint=get_market_strategy_blueprint(getattr(self, "region", "cn")),
        )
        return self._build_prompt(context)

    async def _maybe_await(self, value):
        if inspect.isawaitable(value):
            return await value
        return value

    async def analyze(self, region: Optional[str] = None) -> str:
        """执行完整的大盘分析流程 (异步)"""
        target_region = region or self.region
        market_name = self._get_market_name(target_region)
        logger.info(f"========== 开始 [{target_region}] {market_name} 复盘分析 ==========")

        context = MarketAnalysisContext(region=target_region)

        # 1. 获取指数行情 (带数据库兜底)
        try:
            indices = await self._maybe_await(self.data_manager.get_main_indices(region=target_region))

            # 检查 indices 是否有效（排除全为 0 的情况）
            is_valid_indices = False
            if indices:
                for idx in indices:
                    if isinstance(idx, dict) and idx.get('current', 0) > 0:
                        is_valid_indices = True
                        break

            # 如果实时指数无效（非交易日），尝试从远程历史接口恢复
            if not is_valid_indices:
                context.date = date.today().isoformat()
                logger.info(f"[大盘] 实时行情不可用，尝试获取远程历史数据 (日期: {context.date})")

                # 2. 尝试从远程接口获取真实的指数历史 (000001, 399001, 399006)
                major_indices = [
                    ('上证指数', '000001'),
                    ('深证成指', '399001'),
                    ('创业板指', '399006'),
                    ('沪深300', '000300')
                ]

                fetched_indices = []
                for name, code in major_indices:
                    try:
                        # 强制通过历史日线接口获取
                        df, _ = await self._maybe_await(self.data_manager.get_daily_data(code, days=1))
                        if df is not None and not df.empty:
                            last_row = df.iloc[-1]
                            fetched_indices.append({
                                'name': name,
                                'code': code,
                                'current': last_row.get('close', 0),
                                'change_pct': last_row.get('pct_chg', 0)
                            })
                    except Exception as e:
                        logger.debug(f"[大盘] 补偿获取指数 {code} 失败: {e}")

                if fetched_indices:
                    context.indices = fetched_indices
                    is_valid_indices = True

                # 3. 尝试获取真实的全市场统计
                if target_region == "cn":
                    # 遍历所有 fetcher 寻找有非零成交额的源（如 efinance）
                    for f in self.data_manager.fetchers:
                        try:
                            if hasattr(f, 'get_market_stats'):
                                stats = await asyncio.to_thread(f.get_market_stats)
                                if stats and hasattr(self.data_manager, "_normalize_market_stats"):
                                    stats = self.data_manager._normalize_market_stats(stats, getattr(f, "name", "unknown"))
                                if stats and stats.get('volume_total', 0) > 100: # 成交额通常 > 100亿才可信
                                    context.stats = stats
                                    logger.info(f"[大盘] 从 {f.name} 获取到真实的非零统计: 成交额={stats.get('volume_total')}亿")
                                    break
                        except Exception:
                            logger.warning("[大盘] get_market_stats failed for %s", getattr(f, "name", "unknown"))
                            continue
            else:
                context.indices = indices
        except Exception as e:
            logger.error(f"[大盘] 获取指数行情失败: {e}")

        # 2. 获取市场统计 (如果之前没拿到或无效)
        if target_region == "cn" and (not context.stats or context.stats.get('volume_total', 0) == 0):
            try:
                stats = await self._maybe_await(self.data_manager.get_market_stats())
                if stats and stats.get('volume_total', 0) > 0:
                    context.stats = stats
                else:
                    logger.info("[大盘] 远程统计不可用，跳过样本推算（DB 已移除）")
            except Exception as e:
                logger.error(f"[大盘] 获取市场统计失败: {e}")

        # 3. 获取板块涨跌榜
        try:
            sector_rankings = await self._maybe_await(self.data_manager.get_sector_rankings())
            if sector_rankings:
                if isinstance(sector_rankings, tuple) and len(sector_rankings) == 2:
                    top, bottom = sector_rankings
                    context.sector_rankings = {'top': top, 'bottom': bottom}
                else:
                    context.sector_rankings = {'top': sector_rankings[:5], 'bottom': sector_rankings[-5:]}
        except Exception as e:
            logger.error(f"[大盘] 获取板块涨跌榜失败: {e}")

        # 4. 联网搜索大盘情报
        try:
            # 如果是回溯的日期，搜索词带上日期
            date_hint = context.date if context.date != date.today().isoformat() else ""
            query = f"{date_hint} {market_name} 大盘 复盘"
            news_resp = await self.search_service.search_stock_news_async(stock_code=target_region, stock_name=market_name, focus_keywords=[query])
            if news_resp and news_resp.results:
                context.market_news = news_resp.results
        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")

        # 5. 搜索宏观新闻（利率、汇率、政策、流动性等）
        try:
            macro_resp = await self.search_service.search_macro_news_async(
                stock_code=target_region,
                stock_name=market_name,
                max_results=5,
            )
            if macro_resp and macro_resp.results:
                context.macro_news = macro_resp.results
                logger.info(f"[大盘] 获取 {len(macro_resp.results)} 条宏观新闻")
        except Exception as e:
            logger.error(f"[大盘] 搜索宏观新闻失败: {e}")

        context.strategy_blueprint = get_market_strategy_blueprint(target_region)
        return await self._generate_report(context)

    async def _generate_report(self, context: MarketAnalysisContext) -> str:
        """异步生成报告"""
        logger.info("[大盘] 正在调用 AI 生成复盘报告...")
        prompt = self._build_prompt(context)

        if not self.analyzer or not self.analyzer.is_available():
            return self._generate_fallback_report(context)

        try:
            report = None

            # 优先走同步兼容接口，避免旧实现/测试继续依赖私有属性或异步细节。
            if hasattr(self.analyzer, "generate_text"):
                report = await asyncio.to_thread(self.analyzer.generate_text, prompt)
            if not report and hasattr(self.analyzer, "generate_text_async"):
                report = await self.analyzer.generate_text_async(prompt)

            return report if report else self._generate_fallback_report(context)
        except Exception as e:
            logger.error(f"[大盘] AI 生成报告失败: {e}")
            return self._generate_fallback_report(context)

    def _build_prompt(self, context: MarketAnalysisContext) -> str:
        market_name = self._get_market_name(context.region)
        role, missing_data_guidance, output_template = self._get_prompt_scaffold(context)
        index_table = self._build_index_table(context)
        breadth_line = self._build_breadth_line(context)
        sector_lines = self._build_sector_lines(context)

        # 检查是否为历史数据
        is_historical = context.date != date.today().isoformat()
        historical_hint = f"注意：当前数据日期为 {context.date}，是最近一个交易日的收盘数据，请基于此进行分析。" if is_historical else ""

        indices_text = "暂无指数数据\n"
        if context.indices:
            indices_text = ""
            # 支持 dict 格式（旧格式）
            if isinstance(context.indices, dict):
                for idx_name, idx_data in context.indices.items():
                    if isinstance(idx_data, dict):
                        price = idx_data.get('price', 'N/A')
                        change = idx_data.get('change_pct', 'N/A')
                        indices_text += f"- {idx_name}: {price} ({change}%)\n"
            # 支持 list 格式（各 fetcher 实际返回的格式）
            elif isinstance(context.indices, list):
                for idx in context.indices:
                    if isinstance(idx, dict):
                        name = idx.get('name', idx.get('code', 'N/A'))
                        price = idx.get('current', idx.get('price', 'N/A'))
                        change = idx.get('change_pct', 'N/A')
                        indices_text += f"- {name}: {price} ({change}%)\n"

        if context.stats:
            up = context.stats.get('up', context.stats.get('up_count', context.stats.get('rise_count', 'N/A')))
            down = context.stats.get('down', context.stats.get('down_count', context.stats.get('fall_count', 'N/A')))
            l_up = context.stats.get('limit_up', context.stats.get('limit_up_count', 'N/A'))
            vol = context.stats.get('volume_total', context.stats.get('total_amount', 'N/A'))
            stats_text = f"- 上涨: {up} | 下跌: {down} | 涨停: {l_up}\n- 成交额: {vol} 亿元\n"
        else:
            stats_text = "暂无统计数据\n"

        sectors_text = "\n".join([f"- {line}" for line in sector_lines]) + "\n"

        news_text = "暂无相关新闻\n"
        if context.market_news:
            lines = []
            for n in context.market_news[:5]:
                title = getattr(n, 'title', '') or (n.get('title') if isinstance(n, dict) else '')
                p_date = getattr(n, 'published_date', '今日') or (n.get('published_date') if isinstance(n, dict) else '今日')
                lines.append(f"- [{p_date}] {title}")
            if lines: news_text = "\n".join(lines) + "\n"

        macro_news_text = "暂无宏观新闻\n"
        if context.macro_news:
            lines = []
            for n in context.macro_news[:5]:
                title = getattr(n, 'title', '') or (n.get('title') if isinstance(n, dict) else '')
                p_date = getattr(n, 'published_date', '今日') or (n.get('published_date') if isinstance(n, dict) else '今日')
                lines.append(f"- [{p_date}] {title}")
            if lines: macro_news_text = "\n".join(lines) + "\n"

        blueprint = context.strategy_blueprint
        strategy_section_title = "## 策略计划" if context.region == "cn" else "## Strategy Plan"
        if blueprint and hasattr(blueprint, "to_prompt_block"):
            strategy_text = f"{strategy_section_title}\n{blueprint.to_prompt_block()}\n\n"
        else:
            blueprint_name = getattr(blueprint, 'name', '默认策略') if blueprint else '默认策略'
            blueprint_desc = getattr(blueprint, 'description', '') if blueprint else ''
            strategy_text = f"{strategy_section_title}\n## Strategy Blueprint: {blueprint_name}\n{blueprint_desc}\n\n"

        cn_requirements = ""
        if context.region == "cn":
            cn_requirements = """- A 股复盘必须严格使用模板中的七段标题，不要改写成"市场总结 / 指数点评 / 策略计划"
- A 股复盘的"一、盘面总览"必须保留下方市场宽度行
- A 股复盘的"二、指数结构"必须输出表头为"指数 / 最新 / 涨跌幅 / 成交额(亿)"的指数表
- A 股复盘的"三、板块主线"必须保留"🔥 领涨"和"💧 领跌"两行
- A 股复盘的"六、明日交易计划"必须包含"结论 / 仓位 / 关注方向 / 回避方向 / 失效条件"
"""

        return f"""{role}，请根据以下数据生成一份结构化的{self._get_market_scope_name('zh')}大盘复盘报告。

【重要】输出要求：
- 必须输出纯 Markdown 文本格式
- 禁止输出 JSON 格式
- 禁止输出代码块
- emoji 仅在标题处少量使用（每个标题最多1个）
- 报告要像交易员盘后工作台：先给结论，再按数据表、主线、催化、计划展开
- 不要重复列出已由系统注入的表格数据；正文负责解释表格背后的含义
{cn_requirements.rstrip()}
- {missing_data_guidance}

---

# 今日市场数据 ({context.date})

## 结构化快照
{breadth_line}

## 主要指数表
{index_table}

## 主要指数
{indices_text}

## 市场统计 ({market_name})
{stats_text}

## 板块表现 ({market_name})
{sectors_text}

## 市场新闻
{news_text}

## 宏观新闻（利率/汇率/政策/流动性）
{macro_news_text}

{strategy_text}

# 输出格式模板

{output_template}
{self._get_strategy_prompt_block()}

---
请直接输出报告。
"""

    def _generate_fallback_report(self, context: MarketAnalysisContext) -> str:
        if context.region == "cn":
            return self._generate_cn_fallback_report(context)

        market_name = self._get_market_name(context.region)
        vol = context.stats.get('volume_total', context.stats.get('total_amount', 'N/A')) if isinstance(context.stats, dict) else 'N/A'
        up = context.stats.get('up', context.stats.get('up_count', 0)) if isinstance(context.stats, dict) else 0
        down = context.stats.get('down', context.stats.get('down_count', 0)) if isinstance(context.stats, dict) else 0
        return f"# {context.date} {market_name} 简要复盘\n\n> 提示：AI 分析服务暂时不可用，以下为基于原始数据的简报。\n\n- 成交统计: {vol} 亿\n- 涨跌分布: 上涨 {up} / 下跌 {down}"

    def _generate_cn_fallback_report(self, context: MarketAnalysisContext) -> str:
        """Generate a deterministic A-share recap when the LLM is unavailable."""
        breadth_line = self._build_breadth_line(context)
        index_table = self._build_index_table(context)
        sector_lines = self._build_sector_lines(context)
        stats = context.stats if isinstance(context.stats, dict) else {}
        turnover = self._format_number(self._pick_value(stats, "volume_total", "total_amount", "amount_total"))
        up = self._as_float(self._pick_value(stats, "up", "up_count", "rise_count", default=None))
        down = self._as_float(self._pick_value(stats, "down", "down_count", "fall_count", default=None))
        state = self._infer_market_state(context)

        rows = self._iter_index_rows(context)
        valid_rows = [
            (row, self._as_float(self._pick_value(row, "change_pct", "pct_chg", "涨跌幅", default=None)))
            for row in rows
        ]
        valid_rows = [(row, pct) for row, pct in valid_rows if pct is not None]
        if valid_rows:
            leader_row, leader_pct = max(valid_rows, key=lambda item: item[1])
            laggard_row, laggard_pct = min(valid_rows, key=lambda item: item[1])
            index_comment = (
                f"{self._pick_value(leader_row, 'name', 'code', default='主要指数')}领涨"
                f"（{self._format_signed_pct(leader_pct)}），"
                f"{self._pick_value(laggard_row, 'name', 'code', default='弱势指数')}相对偏弱"
                f"（{self._format_signed_pct(laggard_pct)}），指数间分化体现风格轮动节奏。"
            )
        else:
            index_comment = "主要指数数据不足，指数点评以市场宽度、成交额与板块强弱为主。"

        if up is not None and down is not None:
            breadth_comment = "赚钱效应占优" if up >= down else "市场分化偏弱"
            summary = f"今日A股市场呈现{breadth_comment}格局，量能与涨跌分布是判断后续延续性的核心线索。"
        else:
            summary = "今日A股复盘数据存在缺口，以下按已获取的指数、成交额与板块排序做结构化归纳。"

        position_map = {
            "进攻": "可维持积极仓位，但优先围绕强势主线分批参与，避免追高扩散不足的题材。",
            "均衡": "保持中性仓位，等待指数方向、成交额和主线持续性进一步确认。",
            "防守": "降低仓位并优先控制回撤，等待领跌扩散收敛或指数重新企稳。",
        }
        invalidation_map = {
            "进攻": "若后续出现放量滞涨、缩量下跌，或领涨板块集体回落，应降至均衡策略。",
            "均衡": "若指数共振放量上行且领涨板块延续，可上调风险偏好；若跌停与领跌扩散，应转为防守。",
            "防守": "若主要指数重新收复关键位置、成交额回升且上涨家数明显修复，可逐步转回均衡。",
        }

        return f"""## {context.date} 大盘复盘

> 今日A股市场整体呈现**{state}**态势，优先观察指数承接、成交额变化和板块持续性。

### 一、盘面总览
{summary}
{breadth_line}

### 二、指数结构
{index_comment}
{index_table}

### 三、板块主线
领涨板块体现当日资金进攻方向，领跌板块反映调仓或防御压力。
{sector_lines[0]}
{sector_lines[1]}

### 四、资金与情绪
两市成交额为 {turnover} 亿。结合上涨/下跌家数与涨跌停结构看，当前资金风险偏好处于"{state}"状态，后续需要观察成交额是否继续配合。

### 五、消息催化
{self._build_fallback_news_section(context)}

### 六、明日交易计划
- **结论**：{state}观察。
- **仓位**：{position_map[state]}
- **关注方向**：领涨板块中强于指数、且成交额配合的方向。
- **回避方向**：连续走弱且缺少修复信号的方向。
- **失效条件**：{invalidation_map[state]}

### 七、风险提示
- 热点轮动过快可能带来追高回撤风险。
- 若成交额萎缩，指数上行动能会受到制约。
- 领跌板块若继续扩散，可能削弱短线风险偏好。

---
*复盘时间: {datetime.now().strftime('%H:%M')}*"""
    def _generate_template_review(self, overview: MarketOverview, news: List) -> str:
        """使用模板生成复盘报告（无大模型时的备选方案）"""
        template_language = self._get_template_review_language()
        mood_code = self.profile.mood_index_code
        # 根据 mood_index_code 查找对应指数
        # cn: mood_code="000001"，idx.code 可能为 "sh000001"（以 mood_code 结尾）
        # us: mood_code="SPX"，idx.code 直接为 "SPX"
        mood_index = next(
            (
                idx
                for idx in overview.indices
                if idx.code == mood_code or idx.code.endswith(mood_code)
            ),
            None,
        )
        if mood_index:
            if mood_index.change_pct > 1:
                market_mood = self._get_market_mood_text("strong_up", template_language)
            elif mood_index.change_pct > 0:
                market_mood = self._get_market_mood_text("mild_up", template_language)
            elif mood_index.change_pct > -1:
                market_mood = self._get_market_mood_text("mild_down", template_language)
            else:
                market_mood = self._get_market_mood_text("strong_down", template_language)
        else:
            market_mood = self._get_market_mood_text("range", template_language)
        
        # 指数行情（简洁格式）
        indices_text = ""
        for idx in overview.indices[:4]:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            indices_text += f"- **{idx.name}**: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%)\n"
        
        # 板块信息
        separator = ", " if template_language == "en" else "、"
        top_text = separator.join([s['name'] for s in overview.top_sectors[:3]])
        bottom_text = separator.join([s['name'] for s in overview.bottom_sectors[:3]])

        if template_language == "en":
            stats_section = ""
            if self.profile.has_market_stats:
                stats_section = f"""
### 3. Breadth & Liquidity
| Metric | Value |
|--------|-------|
| Advancers | {overview.up_count} |
| Decliners | {overview.down_count} |
| Limit-up | {overview.limit_up_count} |
| Limit-down | {overview.limit_down_count} |
| Turnover ({self._get_turnover_unit_label()}) | {overview.total_amount:.0f} |
"""
            sector_section = ""
            if self.profile.has_sector_rankings and (top_text or bottom_text):
                sector_section = f"""
### 4. Sector Highlights
- **Leaders**: {top_text or "N/A"}
- **Laggards**: {bottom_text or "N/A"}
"""
            market_names = {"us": "US Market Recap", "hk": "HK Market Recap"}
            market_name = market_names.get(self.region, "A-share Market Recap")
            report = f"""## {overview.date} {market_name}

### 1. Market Summary
Today's {self._get_market_scope_name(template_language)} showed **{market_mood}**.

### 2. Major Indices
{indices_text or "- No index data available"}
{stats_section}
{sector_section}
### 5. Risk Alerts
Market conditions can change quickly. The data above is for reference only and does not constitute investment advice.

{self._get_strategy_markdown_block(template_language)}

---
*Review Time: {datetime.now().strftime('%H:%M')}*
"""
            return report

        market_labels = {"cn": "A股", "us": "美股", "hk": "港股"}
        market_label = market_labels.get(self.region, "A股")
        dashboard_block = self._build_stats_block(overview)
        indices_block = self._build_indices_block(overview)
        sector_block = self._build_sector_block(overview)
        return f"""## {overview.date} 大盘复盘

> 今日{market_label}市场整体呈现**{market_mood}**态势，优先观察指数承接、成交额变化和板块持续性。

### 一、盘面总览
{dashboard_block or "暂无市场宽度数据。"}

### 二、指数结构
{indices_block or indices_text or "暂无指数数据。"}

### 三、板块主线
{sector_block or "- 暂无板块涨跌榜数据。"}

### 四、资金与情绪
- 结合成交额和涨跌家数看，当前更适合等待确认，避免仅凭单一热点追高。

### 五、消息催化
- 暂无可用新闻时，应降低对题材持续性的确定性判断。

### 六、明日交易计划
- **结论**：均衡观察。
- **仓位**：控制在中性区间，等待指数与主线共振。
- **关注方向**：{top_text or "强于指数的主线板块"}。
- **回避方向**：{bottom_text or "连续走弱且缺少修复信号的方向"}。

### 七、风险提示
- 市场有风险，投资需谨慎。以上数据仅供参考，不构成投资建议。

---
*复盘时间: {datetime.now().strftime('%H:%M')}*
"""
    
    async def run_daily_review(self) -> str:
        """执行每日大盘复盘流程。"""
        return await self.analyze(self.region)

    def run_daily_review_sync(self) -> str:
        """同步兼容入口，供旧调用方显式使用。"""
        return asyncio.run(self.run_daily_review())


# 测试入口
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '.')

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    )

    analyzer = MarketAnalyzer()
    report = asyncio.run(analyzer.run_daily_review())
    print(f"\n=== 复盘报告 ===")
    print(report)
