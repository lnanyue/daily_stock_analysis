# -*- coding: utf-8 -*-
"""
===================================
大盘复盘分析器
===================================

职责：
1. 汇总全市场行情数据
2. 结合宏观新闻进行 AI 分析
3. 生成全局视角复盘报告
"""

import logging
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

    def _get_market_name(self, region: str) -> str:
        names = {"cn": "A股", "us": "美股", "hk": "港股", "global": "全球联动"}
        return names.get(region, region)

    def run_daily_review(self) -> str:
        """运行每日复盘"""
        return self.analyze(self.region)

    def analyze(self, region: Optional[str] = None) -> str:
        target_region = region or self.region
        market_name = self._get_market_name(target_region)
        logger.info(f"========== 开始 [{target_region}] {market_name} 复盘分析 ==========")
        
        context = MarketAnalysisContext(region=target_region)
        
        # 1. 获取指数行情 (带数据库兜底)
        try:
            indices = self.data_manager.get_main_indices(region=target_region)
            if not indices:
                # 尝试从数据库获取最近一次的指数记录
                from src.storage import get_db
                db = get_db()
                # A 股以 000001 (上证指数) 为基准查找最后日期
                last_record = db.get_latest_data('000001', days=1)
                if last_record:
                    last_date = last_record[0].date
                    logger.info(f"[大盘] 实时指数为空，尝试加载 {last_date} 的历史数据作为参考")
                    # 这里可以进一步补全其他指数，暂以日志提示为主
            
            if indices:
                context.indices = indices
        except Exception as e:
            logger.error(f"[大盘] 获取指数行情失败: {e}")

        # 2. 获取市场统计 (带数据库兜底)
        if target_region == "cn":
            try:
                stats = self.data_manager.get_market_stats()
                if not stats:
                    # 模拟一个基于最后交易日的统计（或标记为昨日数据）
                    pass
                if stats: context.stats = stats
            except Exception as e:
                logger.error(f"[大盘] 获取市场统计失败: {e}")

            # 3. 获取板块涨跌榜
            try:
                sector_rankings = self.data_manager.get_sector_rankings()
                if sector_rankings:
                    context.sector_rankings = {
                        'top': sector_rankings[:5],
                        'bottom': sector_rankings[-5:]
                    }
            except Exception as e:
                logger.error(f"[大盘] 获取板块涨跌榜失败: {e}")

        # 4. 联网搜索大盘情报
        try:
            query = f"{market_name} 大盘 复盘"
            news_resp = self.search_service.search_stock_news(
                stock_code=target_region,
                stock_name=market_name,
                focus_keywords=[query]
            )
            if news_resp and news_resp.results:
                context.market_news = news_resp.results
        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")

        context.strategy_blueprint = get_market_strategy_blueprint(target_region)
        return self._generate_report(context)

    def _generate_report(self, context: MarketAnalysisContext) -> str:
        logger.info("[大盘] 正在调用 AI 生成复盘报告...")
        prompt = self._build_prompt(context)
        
        if not self.analyzer or not self.analyzer.is_available():
            return self._generate_fallback_report(context)
            
        try:
            report = self.analyzer.generate_text(prompt)
            return report if report else self._generate_fallback_report(context)
        except Exception as e:
            logger.error(f"[大盘] AI 生成报告失败: {e}")
            return self._generate_fallback_report(context)

    def _build_prompt(self, context: MarketAnalysisContext) -> str:
        market_name = self._get_market_name(context.region)
        
        # 安全地获取指数数据
        indices_text = ""
        if isinstance(context.indices, dict):
            for idx_name, idx_data in context.indices.items():
                if isinstance(idx_data, dict):
                    price = idx_data.get('price', 'N/A')
                    change = idx_data.get('change_pct', 'N/A')
                    indices_text += f"- {idx_name}: {price} ({change}%)\n"
        
        # 安全地获取统计数据
        up = context.stats.get('up', 0) if isinstance(context.stats, dict) else 0
        down = context.stats.get('down', 0) if isinstance(context.stats, dict) else 0
        l_up = context.stats.get('limit_up', 0) if isinstance(context.stats, dict) else 0
        vol = context.stats.get('volume_total', 0) if isinstance(context.stats, dict) else 0
        stats_text = f"- 上涨: {up} | 下跌: {down} | 涨停: {l_up}\n- 成交额: {vol} 亿元\n"
        
        # 安全地获取板块数据
        sectors_text = "- 领涨: 暂无\n- 领跌: 暂无\n"
        if isinstance(context.sector_rankings, dict):
            top_list = context.sector_rankings.get('top', [])
            bottom_list = context.sector_rankings.get('bottom', [])
            if top_list:
                t_str = ", ".join([f"{s.get('name', '未知')}({s.get('change_pct', 0)}%)" for s in top_list if isinstance(s, dict)])
                b_str = ", ".join([f"{s.get('name', '未知')}({s.get('change_pct', 0)}%)" for s in bottom_list if isinstance(s, dict)])
                sectors_text = f"- 领涨: {t_str}\n- 领跌: {b_str}\n"
            
        # 安全地获取新闻数据
        news_text = "暂无相关新闻\n"
        if context.market_news:
            lines = []
            for n in context.market_news[:5]:
                title = getattr(n, 'title', '') or (n.get('title') if isinstance(n, dict) else '')
                p_date = getattr(n, 'published_date', '今日') or (n.get('published_date') if isinstance(n, dict) else '今日')
                lines.append(f"- [{p_date}] {title}")
            if lines: news_text = "\n".join(lines) + "\n"

        blueprint = context.strategy_blueprint
        blueprint_name = getattr(blueprint, 'name', '默认策略') if blueprint else '默认策略'
        blueprint_desc = getattr(blueprint, 'description', '') if blueprint else ''
        strategy_text = f"## Strategy Blueprint: {blueprint_name}\n{blueprint_desc}\n\n"
        
        return f"""你是一位专业的全球市场分析师，请根据以下数据生成一份简洁、深刻的大盘复盘报告。

【输出要求】：
- 必须使用纯 Markdown 格式
- 禁止 JSON 或代码块
- 标题处可少量使用 emoji
- 逻辑清晰，重点突出

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

## {context.date} {market_name}市场复盘

### 一、全球视野
（总结今日中美市场整体表现及联动主线，2-3句话）

### 二、行情联动点评
（对比分析 A 股与美股主要指数的走势特征及相互影响）

### 三、行业映射与热点
（重点解析强势板块及自身热点逻辑）

### 四、后市展望
（结合走势与背景，给出后续预判）

### 五、策略建议
（仓位与方向建议；最后补充“建议仅供参考，不构成投资建议”。）

---
请直接输出报告。
"""

    def _generate_fallback_report(self, context: MarketAnalysisContext) -> str:
        market_name = self._get_market_name(context.region)
        vol = context.stats.get('volume_total', 'N/A') if isinstance(context.stats, dict) else 'N/A'
        up = context.stats.get('up', 0) if isinstance(context.stats, dict) else 0
        down = context.stats.get('down', 0) if isinstance(context.stats, dict) else 0
        return f"# {context.date} {market_name} 简要复盘\n\n> 提示：AI 分析服务暂时不可用，以下为基于原始数据的简报。\n\n- 成交统计: {vol} 亿\n- 涨跌分布: 上涨 {up} / 下跌 {down}"
