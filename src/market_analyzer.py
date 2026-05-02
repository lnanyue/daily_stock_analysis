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
            tavily_keys=self.config.tavily_api_keys,
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

    def _format_signed_pct(self, value: Any) -> str:
        number = self._as_float(value)
        if number is None:
            return str(value) if value not in (None, "") else "N/A"
        sign = "+" if number > 0 else ""
        return f"{sign}{self._format_number(number)}%"

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
        # 实时指数接口常返回“元”，表格统一展示为“亿元”。
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
            "若市场数据（指数、成交额等）缺失或显示为 N/A，但提供了市场新闻，请务必以新闻和历史背景为主要依据进行推断性复盘；"
            "若当前日期为回溯的交易日，请在报告中明确说明；不要臆测全球市场或跨市场联动。"
        )
        template = f"""## {context.date} 大盘复盘

### 一、市场总结
（2-3句话概括指数方向、赚钱效应、量能温度；必须保留提供的市场宽度行。）

### 二、指数点评
（围绕主要指数共振/分化、权重与成长风格强弱展开；必须输出提供的指数表。）

### 三、资金动向
（结合成交额、涨跌家数、涨跌停结构判断风险偏好和短线情绪。）

### 四、热点解读
（解读领涨/领跌板块及可能的调仓含义；必须输出“🔥 领涨”和“💧 领跌”两行。）

### 五、后市展望
（结合走势、量能和板块持续性，给出下一交易日观察重点。）

### 六、风险提示
（列出2-3条最关键风险，不要空泛。）

### 七、策略计划
市场状态：（进攻/均衡/防守之一，并说明原因）
仓位建议：（给出可执行的仓位节奏）
失效条件：（写清触发降级或转向的条件）

> 建议仅供参考，不构成投资建议。"""
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

            # 检查 indices 是否有效（排除全为 0 的情况）
            is_valid_indices = False
            if indices:
                for idx in indices:
                    if isinstance(idx, dict) and idx.get('current', 0) > 0:
                        is_valid_indices = True
                        break

            # 如果实时指数无效（非交易日），尝试从远程历史接口恢复
            if not is_valid_indices:
                from src.storage import get_db
                db = get_db()

                # 1. 尝试从数据库获取最近一个交易日的日期
                target_date_obj = db.get_global_latest_date()
                if not target_date_obj:
                    target_date_obj = date.today()

                context.date = target_date_obj.isoformat()
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
                        df, _ = await self.data_manager.get_daily_data(code, days=1)
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
                                stats = f.get_market_stats()
                                if stats and hasattr(self.data_manager, "_normalize_market_stats"):
                                    stats = self.data_manager._normalize_market_stats(stats, getattr(f, "name", "unknown"))
                                if stats and stats.get('volume_total', 0) > 100: # 成交额通常 > 100亿才可信
                                    context.stats = stats
                                    logger.info(f"[大盘] 从 {f.name} 获取到真实的非零统计: 成交额={stats.get('volume_total')}亿")
                                    break
                        except Exception:
                            logger.warning("[大盘] get_market_stats failed for %s", getattr(f, "name", "unknown"), exc_info=True)
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
                    # 只有在完全拿不到远程真实快照时，才降级使用数据库个股样本推算
                    from src.storage import get_db
                    db = get_db()
                    target_date = date.fromisoformat(context.date)
                    with db.get_session() as session:
                        from src.storage import StockDaily
                        from sqlalchemy import select
                        all_today = session.execute(
                            select(StockDaily.pct_chg, StockDaily.amount).where(StockDaily.date == target_date)
                        ).all()
                        if all_today:
                            ups = len([r for r in all_today if (r[0] or 0) > 0])
                            downs = len([r for r in all_today if (r[0] or 0) < 0])
                            total_amt = sum([(r[1] or 0) for r in all_today]) / 100000000.0
                            context.stats = {
                                'up': ups,
                                'down': downs,
                                'volume_total': round(total_amt, 2),
                                'limit_up': 'N/A',
                                'is_sample': True
                            }
                            logger.info(f"[大盘] 远程统计不可用，使用 DB 样本推算: {ups}涨/{downs}跌")
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
            cn_requirements = """- A 股复盘必须严格使用模板中的七段标题，不要改写成“市场总览 / 盘面结构 / 策略建议”
- A 股复盘的“一、市场总结”必须保留下方市场宽度行
- A 股复盘的“二、指数点评”必须输出表头为“指数 / 最新 / 涨跌幅 / 成交额(亿)”的指数表
- A 股复盘的“四、热点解读”必须保留“🔥 领涨”和“💧 领跌”两行
- A 股复盘的“七、策略计划”必须包含“市场状态 / 仓位建议 / 失效条件”
"""

        return f"""{role}，请根据以下数据生成一份简洁、深刻的大盘复盘报告。
{historical_hint}

【输出要求】：
- 必须使用纯 Markdown 格式
- 禁止 JSON 或代码块
- 标题处可少量使用 emoji
- 逻辑清晰，重点突出
- 只能基于已提供的数据进行判断，没有提供的信息不要扩写成确定性结论
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

{strategy_text}

# 输出格式模板

{output_template}

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

> 提示：AI 分析服务暂时不可用，以下为基于原始数据的结构化复盘。

### 一、市场总结
{summary}
{breadth_line}

### 二、指数点评
{index_comment}
{index_table}

### 三、资金动向
两市成交额为 {turnover} 亿。结合上涨/下跌家数与涨跌停结构看，当前资金风险偏好处于“{state}”状态，后续需要观察成交额是否继续配合。

### 四、热点解读
领涨板块体现当日资金进攻方向，领跌板块反映调仓或防御压力。
{sector_lines[0]}
{sector_lines[1]}

### 五、后市展望
后续重点看三点：主要指数能否继续共振、成交额是否维持活跃、领涨板块能否形成持续主线。若量能回落或主线快速轮动，指数大概率转入震荡。

### 六、风险提示
- 热点轮动过快可能带来追高回撤风险。
- 若成交额萎缩，指数上行动能会受到制约。
- 领跌板块若继续扩散，可能削弱短线风险偏好。

### 七、策略计划
市场状态：{state}。判断依据为指数表现、成交额和涨跌分布的综合状态。
仓位建议：{position_map[state]}
失效条件：{invalidation_map[state]}

> 建议仅供参考，不构成投资建议。"""
