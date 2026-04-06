# -*- coding: utf-8 -*-
"""
===================================
大盘复盘分析模块
===================================

职责：
1. 获取大盘指数数据（上证、深证、创业板、标普、纳指等）
2. 搜索市场新闻形成复盘情报
3. 使用大模型生成每日大盘复盘报告（支持 A 股、美股及全球联动模式）
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

import pandas as pd
import anyio

from src.config import get_config
from src.search_service import SearchService
from src.core.market_profile import get_profile, MarketProfile
from src.core.market_strategy import get_market_strategy_blueprint
from data_provider.base import DataFetcherManager

logger = logging.getLogger(__name__)


@dataclass
class MarketIndex:
    """大盘指数数据"""
    code: str                    # 指数代码
    name: str                    # 指数名称
    current: float = 0.0         # 当前点位
    change: float = 0.0          # 涨跌点数
    change_pct: float = 0.0      # 涨跌幅(%)
    open: float = 0.0            # 开盘点位
    high: float = 0.0            # 最高点位
    low: float = 0.0             # 最低点位
    prev_close: float = 0.0      # 昨收点位
    volume: float = 0.0          # 成交量（手）
    amount: float = 0.0          # 成交额（元）
    amplitude: float = 0.0       # 振幅(%)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'current': self.current,
            'change': self.change,
            'change_pct': self.change_pct,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'volume': self.volume,
            'amount': self.amount,
            'amplitude': self.amplitude,
        }


@dataclass
class MarketOverview:
    """市场概览数据"""
    date: str                           # 日期
    indices: List[MarketIndex] = field(default_factory=list)  # 主要指数
    up_count: int = 0                   # 上涨家数
    down_count: int = 0                 # 下跌家数
    flat_count: int = 0                 # 平盘家数
    limit_up_count: int = 0             # 涨停家数
    limit_down_count: int = 0           # 跌停家_count
    total_amount: float = 0.0           # 两市成交额（亿元）
    
    # 板块涨幅榜
    top_sectors: List[Dict] = field(default_factory=list)     # 涨幅前5板块
    bottom_sectors: List[Dict] = field(default_factory=list)  # 跌幅前5板块

    # 全球模式额外数据
    us_indices: List[MarketIndex] = field(default_factory=list)
    cn_indices: List[MarketIndex] = field(default_factory=list)


class MarketAnalyzer:
    """
    大盘复盘分析器
    
    功能：
    1. 获取大盘指数实时行情
    2. 获取市场涨跌统计
    3. 获取板块涨跌榜
    4. 搜索市场新闻
    5. 生成大盘复盘报告（支持全球联动模式）
    """
    
    def __init__(
        self,
        search_service: Optional[SearchService] = None,
        analyzer=None,
        region: str = "cn",
    ):
        """
        初始化大盘分析器

        Args:
            search_service: 搜索服务实例
            analyzer: AI分析器实例
            region: 市场区域 cn=A股 us=美股 global=全球联动
        """
        self.config = get_config()
        self.search_service = search_service
        self.analyzer = analyzer
        self.data_manager = DataFetcherManager()
        self.region = region if region in ("cn", "us", "global") else "cn"
        self.profile: MarketProfile = get_profile(self.region)
        self.strategy = get_market_strategy_blueprint(self.region)

    def get_market_overview(self) -> MarketOverview:
        """
        获取市场概览数据
        
        Returns:
            MarketOverview: 市场概览数据对象
        """
        today = datetime.now().strftime('%Y-%m-%d')
        overview = MarketOverview(date=today)
        
        if self.region == "global":
            # 全球模式：同时获取 A 股和美股指数
            overview.cn_indices = self._get_main_indices_by_region("cn")
            overview.us_indices = self._get_main_indices_by_region("us")
            overview.indices = overview.cn_indices + overview.us_indices
            
            # 全球模式下依然获取 A 股统计数据（美股暂无）
            self._get_market_statistics(overview)
            self._get_sector_rankings(overview)
        else:
            # 1. 获取主要指数行情（按 region 切换 A 股/美股）
            overview.indices = self._get_main_indices_by_region(self.region)

            # 2. 获取涨跌统计（A 股有，美股无等效数据）
            if self.profile.has_market_stats:
                self._get_market_statistics(overview)

            # 3. 获取板块涨跌榜（A 股有，美股暂无）
            if self.profile.has_sector_rankings:
                self._get_sector_rankings(overview)
        
        return overview

    def _get_main_indices_by_region(self, region: str) -> List[MarketIndex]:
        """按区域获取主要指数实时行情"""
        indices = []
        try:
            logger.info(f"[大盘] 获取 {region} 主要指数实时行情...")
            data_list = self.data_manager.get_main_indices(region=region)
            if data_list:
                for item in data_list:
                    index = MarketIndex(
                        code=item['code'],
                        name=item['name'],
                        current=item['current'],
                        change=item['change'],
                        change_pct=item['change_pct'],
                        open=item['open'],
                        high=item['high'],
                        low=item['low'],
                        prev_close=item['prev_close'],
                        volume=item['volume'],
                        amount=item['amount'],
                        amplitude=item['amplitude']
                    )
                    indices.append(index)
            if not indices:
                logger.warning(f"[大盘] {region} 指数行情获取为空")
        except Exception as e:
            logger.error(f"[大盘] 获取 {region} 指数行情失败: {e}")
        return indices

    def _get_main_indices(self) -> List[MarketIndex]:
        """获取主要指数实时行情 (兼容旧调用)"""
        return self._get_main_indices_by_region(self.region)

    def _get_market_statistics(self, overview: MarketOverview):
        """获取市场涨跌统计"""
        try:
            logger.info("[大盘] 获取市场涨跌统计...")
            stats = self.data_manager.get_market_stats()
            if stats:
                overview.up_count = stats.get('up_count', 0)
                overview.down_count = stats.get('down_count', 0)
                overview.flat_count = stats.get('flat_count', 0)
                overview.limit_up_count = stats.get('limit_up_count', 0)
                overview.limit_down_count = stats.get('limit_down_count', 0)
                overview.total_amount = stats.get('total_amount', 0.0)
                logger.info(f"[大盘] 涨:{overview.up_count} 跌:{overview.down_count} 成交额:{overview.total_amount:.0f}亿")
        except Exception as e:
            logger.error(f"[大盘] 获取涨跌统计失败: {e}")

    def _get_sector_rankings(self, overview: MarketOverview):
        """获取板块涨跌榜"""
        try:
            logger.info("[大盘] 获取板块涨跌榜...")
            top_sectors, bottom_sectors = self.data_manager.get_sector_rankings(5)
            if top_sectors or bottom_sectors:
                overview.top_sectors = top_sectors
                overview.bottom_sectors = bottom_sectors
                logger.info(f"[大盘] 领涨板块: {[s['name'] for s in overview.top_sectors[:3]]}")
        except Exception as e:
            logger.error(f"[大盘] 获取板块涨跌榜失败: {e}")
    
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
        try:
            for query in self.profile.news_queries:
                logger.info(f"[大盘] 搜索新闻: {query}...")
                # SearchService.search_stock_news requires (stock_name, max_results)
                # For market news, we use the query as the stock_name
                results = self.search_service.search_stock_news(query, max_results=3)
                if results and results.success:
                    all_news.extend(results.results)
                # 避免请求过快
                time.sleep(0.5)
        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")
            
        return all_news

    def generate_market_review(self, overview: MarketOverview, news: List[Dict]) -> str:
        """
        生成大盘复盘报告
        
        Args:
            overview: 市场概览数据
            news: 市场新闻列表
            
        Returns:
            复盘报告文本
        """
        if not self.analyzer:
            logger.warning("[大盘] AI 分析器未配置，使用模板生成报告")
            return self._generate_template_review(overview, news)
            
        # 1. 构建 Prompt
        prompt = self._build_review_prompt(overview, news)
        
        # 2. 调用大模型
        try:
            logger.info("[大盘] 正在调用 AI 生成复盘报告...")
            # Use generate_text for free-form market review (analyze is for single stock JSON)
            content = self.analyzer.generate_text(
                prompt=prompt,
                max_tokens=2048,
                temperature=0.7
            )
            return content if content else self._generate_template_review(overview, news)
        except Exception as e:
            logger.error(f"[大盘] AI 生成报告失败: {e}")
            return self._generate_template_review(overview, news)

    def _build_review_prompt(self, overview: MarketOverview, news: List) -> str:
        """构建复盘报告 Prompt"""
        # 指数行情信息
        indices_text = ""
        if self.region == "global":
            indices_text += "### A股指数\n"
            for idx in overview.cn_indices:
                dir_sym = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
                indices_text += f"- {idx.name}: {idx.current:.2f} ({dir_sym}{abs(idx.change_pct):.2f}%)\n"
            indices_text += "\n### 美股指数\n"
            for idx in overview.us_indices:
                dir_sym = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
                indices_text += f"- {idx.name}: {idx.current:.2f} ({dir_sym}{abs(idx.change_pct):.2f}%)\n"
        else:
            for idx in overview.indices:
                dir_sym = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
                indices_text += f"- {idx.name}: {idx.current:.2f} ({dir_sym}{abs(idx.change_pct):.2f}%)\n"
        
        # 板块信息
        top_sectors_text = ", ".join([f"{s['name']}({s['change_pct']:+.2f}%)" for s in overview.top_sectors[:3]])
        bottom_sectors_text = ", ".join([f"{s['name']}({s['change_pct']:+.2f}%)" for s in overview.bottom_sectors[:3]])
        
        # 新闻信息
        news_text = ""
        for i, n in enumerate(news[:8], 1):
            title = getattr(n, 'title', n.get('title', ''))[:60]
            snippet = getattr(n, 'snippet', n.get('snippet', ''))[:120]
            news_text += f"{i}. {title}\n   {snippet}\n"
        
        # 组装数据区块
        stats_block = f"""## 市场统计 (A股)
- 上涨: {overview.up_count} | 下跌: {overview.down_count} | 涨停: {overview.limit_up_count}
- 成交额: {overview.total_amount:.0f} 亿元""" if self.region != "us" else ""

        sector_block = f"""## 板块表现 (A股)
- 领涨: {top_sectors_text if top_sectors_text else "暂无"}
- 领跌: {bottom_sectors_text if bottom_sectors_text else "暂无"}""" if self.region != "us" else ""

        # 全球模式特别提示
        global_hint = ""
        if self.region == "global":
            global_hint = """
【重点分析要求】：
1. 深入分析 **美股领先板块对 A 股相关行业** 的启发性与联动效应（例如：美股 AI/半导体强势如何映射到 A 股科技股）。
2. 分析全球宏观因子（美元、美债等）对两市风险偏好的统一影响。
3. 比较两市目前的强弱关系，给出跨市场的投资视角。
"""

        # 构造最终 Prompt
        return f"""你是一位专业的全球市场分析师，请根据以下数据生成一份简洁、深刻的大盘复盘报告。

【输出要求】：
- 必须使用纯 Markdown 格式
- 禁止 JSON 或代码块
- 标题处可少量使用 emoji
- 逻辑清晰，重点突出

---

# 今日市场数据 ({overview.date})

## 主要指数
{indices_text}

{stats_block}

{sector_block}

## 市场新闻
{news_text if news_text else "暂无相关新闻"}

{global_hint}

{self.strategy.to_prompt_block()}

---

# 输出格式模板

## {overview.date} 全球市场复盘

### 一、全球视野
（总结今日中美市场整体表现及联动主线，2-3句话）

### 二、指数联动点评
（对比分析 A 股与美股主要指数的走势特征及相互影响）

### 三、行业映射与热点
（重点解析美股强势板块对 A 股相关行业的启发性映射，以及 A 股自身热点逻辑）

### 四、后市展望
（结合两市走势与宏观背景，给出后续预判）

### 五、策略建议
（根据全球联动情况，给出针对性的仓位与方向建议；最后补充“建议仅供参考，不构成投资建议”。）

---
请直接输出报告。
"""

    def _generate_template_review(self, overview: MarketOverview, news: List) -> str:
        """模板生成逻辑（省略，保持原有逻辑或简单合并）"""
        return f"## {overview.date} 复盘报告\n\n指数表现：\n" + "\n".join([f"- {i.name}: {i.change_pct:+.2f}%" for i in overview.indices])

    def run_daily_review(self) -> str:
        """执行每日大盘复盘流程"""
        logger.info(f"========== 开始 [{self.region}] 大盘复盘分析 ==========")
        overview = self.get_market_overview()
        news = self.search_market_news()
        report = self.generate_market_review(overview, news)
        logger.info(f"========== [{self.region}] 大盘复盘分析完成 ==========")
        return report
