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
from datetime import date
from typing import Dict, Any, List, Optional

from src.config import get_config
from src.search_service import SearchService
from src.core.market_profile import get_profile, MarketProfile
from src.core.market_strategy import get_market_strategy_blueprint
from data_provider import DataFetcherManager

logger = logging.getLogger(__name__)


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
    strategy_blueprint: Dict[str, Any] = field(default_factory=dict)


class MarketAnalyzer:
    """
    大盘分析核心类
    """
    
    def __init__(self, data_manager: Optional[DataFetcherManager] = None, analyzer = None, search_service: Optional[SearchService] = None, region: str = "cn"):
        self.config = get_config()
        self.data_manager = data_manager or DataFetcherManager(config=self.config)
        self.search_service = search_service or SearchService(
            bocha_keys=self.config.bocha_api_keys,
            tavily_keys=self.config.tavily_api_keys,
            exa_keys=self.config.exa_api_keys,
            news_max_age_days=self.config.news_max_age_days,
        )
        self.analyzer = analyzer  # GeminiAnalyzer instance
        self.region = region
        self.profile = get_profile(region)
        self.strategy = get_market_strategy_blueprint(region)

    def _get_market_name(self, region: str) -> str:
        names = {"cn": "A股", "us": "美股", "hk": "港股", "global": "全球联动"}
        return names.get(region, region)

    def _get_prompt_scaffold(self, context: MarketAnalysisContext) -> tuple[str, str, str]:
        """Return role, missing-data guidance, and output template by region."""
        if context.region == "global":
            role = "你是一位专业的全球市场分析师"
            missing_data_guidance = (
                "若市场新闻为空或缺少跨市场消息，不要把“无新闻”直接等同于“无法评估全球市场”；"
                "应明确说明消息面样本有限，并优先基于已提供的指数、统计与板块数据完成复盘。"
            )
            template = f"""## {context.date} 全球市场联动复盘

### 一、全球视野
（总结今日中美市场整体表现及联动主线，2-3句话）

### 二、行情联动点评
（仅基于已提供的数据，对比分析 A 股与美股主要指数的走势特征及相互影响；若缺少某一侧数据，需要明确说明，不要臆测。）

### 三、行业映射与热点
（重点解析强势板块及跨市场映射逻辑）

### 四、后市展望
（结合走势与背景，给出后续预判）

### 五、策略建议
（仓位与方向建议；最后补充“建议仅供参考，不构成投资建议”。）"""
            return role, missing_data_guidance, template

        if context.region == "us":
            role = "你是一位专业的美股市场分析师"
            missing_data_guidance = (
                "若市场新闻为空，请明确说明消息面样本有限，并以已提供的美股指数与主题线索为主完成复盘；"
                "不要臆测 A 股表现或中美联动。"
            )
            template = f"""## {context.date} 美股市场复盘

### 一、市场总览
（总结今日美股整体表现与主要驱动，2-3句话）

### 二、指数与风格点评
（分析标普、纳指、道指、波动率或风格轮动特征）

### 三、板块与主题
（重点解析强势/弱势板块及主题主线）

### 四、后市展望
（结合走势与背景，给出后续预判）

### 五、策略建议
（仓位与方向建议；最后补充“建议仅供参考，不构成投资建议”。）"""
            return role, missing_data_guidance, template

        role = "你是一位专业的A股市场分析师"
        missing_data_guidance = (
            "若市场新闻为空，请明确说明消息面样本有限，并以 A 股盘面数据为主完成复盘；"
            "不要臆测全球市场或跨市场联动。"
        )
        template = f"""## {context.date} A股市场复盘

### 一、市场总览
（总结今日A股整体表现与核心特征，2-3句话）

### 二、盘面结构点评
（结合主要指数、量能、涨跌分布，分析市场强弱与风格切换）

### 三、行业映射与热点
（重点解析强势板块及自身热点逻辑）

### 四、后市展望
（结合走势与背景，给出后续预判）

### 五、策略建议
（仓位与方向建议；最后补充“建议仅供参考，不构成投资建议”。）"""
        return role, missing_data_guidance, template

    async def run_daily_review(self) -> str:
        """运行每日复盘 (异步)"""
        return await self.analyze(self.region)

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
            if not indices:
                from src.storage import get_db
                db = get_db()
                last_record = db.get_latest_data('000001', days=1)
                if last_record:
                    logger.info(f"[大盘] 实时指数为空，尝试加载 {last_record[0].date} 的历史数据")
            
            if indices: context.indices = indices
        except Exception as e:
            logger.error(f"[大盘] 获取指数行情失败: {e}")

        # 2. 获取市场统计
        if target_region == "cn":
            try:
                stats = await self._maybe_await(self.data_manager.get_market_stats())
                if stats: context.stats = stats
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
            query = f"{market_name} 大盘 复盘"
            news_resp = await self.search_service.search_stock_news_async(stock_code=target_region, stock_name=market_name, focus_keywords=[query])
            if news_resp and news_resp.results:
                context.market_news = news_resp.results
        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")

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
        
        up = context.stats.get('up', 0) if isinstance(context.stats, dict) else 0
        down = context.stats.get('down', 0) if isinstance(context.stats, dict) else 0
        l_up = context.stats.get('limit_up', 0) if isinstance(context.stats, dict) else 0
        vol = context.stats.get('volume_total', 0) if isinstance(context.stats, dict) else 0
        stats_text = f"- 上涨: {up} | 下跌: {down} | 涨停: {l_up}\n- 成交额: {vol} 亿元\n"
        
        sectors_text = "- 领涨: 暂无\n- 领跌: 暂无\n"
        if isinstance(context.sector_rankings, dict):
            top_list = context.sector_rankings.get('top', [])
            bottom_list = context.sector_rankings.get('bottom', [])
            if top_list:
                t_str = ", ".join([f"{s.get('name', '未知')}({s.get('change_pct', 0)}%)" for s in top_list if isinstance(s, dict)])
                b_str = ", ".join([f"{s.get('name', '未知')}({s.get('change_pct', 0)}%)" for s in bottom_list if isinstance(s, dict)])
                sectors_text = f"- 领涨: {t_str}\n- 领跌: {b_str}\n"
            
        news_text = "暂无相关新闻\n"
        if context.market_news:
            lines = []
            for n in context.market_news[:5]:
                title = getattr(n, 'title', '') or (n.get('title') if isinstance(n, dict) else '')
                p_date = getattr(n, 'published_date', '今日') or (n.get('published_date') if isinstance(n, dict) else '今日')
                lines.append(f"- [{p_date}] {title}")
            if lines: news_text = "\n".join(lines) + "\n"

        blueprint = context.strategy_blueprint
        strategy_section_title = "## 策略计划" if context.region == "cn" else "## Strategy Plan"
        if blueprint and hasattr(blueprint, "to_prompt_block"):
            strategy_text = f"{strategy_section_title}\n{blueprint.to_prompt_block()}\n\n"
        else:
            blueprint_name = getattr(blueprint, 'name', '默认策略') if blueprint else '默认策略'
            blueprint_desc = getattr(blueprint, 'description', '') if blueprint else ''
            strategy_text = f"{strategy_section_title}\n## Strategy Blueprint: {blueprint_name}\n{blueprint_desc}\n\n"
        
        return f"""{role}，请根据以下数据生成一份简洁、深刻的大盘复盘报告。

【输出要求】：
- 必须使用纯 Markdown 格式
- 禁止 JSON 或代码块
- 标题处可少量使用 emoji
- 逻辑清晰，重点突出
- 只能基于已提供的数据进行判断，没有提供的信息不要扩写成确定性结论
- {missing_data_guidance}

---

# 今日市场数据 ({context.date})

## 主要指数
{indices_text}

## 市场统计 ({market_name})
{stats_text}

## 板块表现 ({market_name})
{sectors_text}

## 市场新闻
{news_text}

{strategy_text}

# 输出格式模板

{output_template}

---
请直接输出报告。
"""

    def _generate_fallback_report(self, context: MarketAnalysisContext) -> str:
        market_name = self._get_market_name(context.region)
        vol = context.stats.get('volume_total', 'N/A') if isinstance(context.stats, dict) else 'N/A'
        up = context.stats.get('up', 0) if isinstance(context.stats, dict) else 0
        down = context.stats.get('down', 0) if isinstance(context.stats, dict) else 0
        return f"# {context.date} {market_name} 简要复盘\n\n> 提示：AI 分析服务暂时不可用，以下为基于原始数据的简报。\n\n- 成交统计: {vol} 亿\n- 涨跌分布: 上涨 {up} / 下跌 {down}"
