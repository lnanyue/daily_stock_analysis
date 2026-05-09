# -*- coding: utf-8 -*-
"""
搜索服务 — 管理多个搜索引擎、自动故障转移、结果聚合和格式化。
"""

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import asyncio
from typing import List, Dict, Any, Optional, Tuple

from data_provider.us_index_mapping import is_us_index_code
from src.config import (
    NEWS_STRATEGY_WINDOWS,
    normalize_news_strategy_profile,
    resolve_news_window_days,
)

from .types import SearchResult, SearchResponse
from .base_provider import BaseSearchProvider
from .providers import (
    TavilySearchProvider,
    OpenBBNewsProvider,
    AkshareNewsProvider,
    FinnhubNewsProvider,
)

logger = logging.getLogger(__name__)


class SearchService:
    """
    搜索服务
    
    功能：
    1. 管理多个搜索引擎
    2. 自动故障转移
    3. 结果聚合和格式化
    4. 数据源失败时的增强搜索（股价、走势等）
    5. 港股/美股自动使用英文搜索关键词
    """
    
    # 增强搜索关键词模板（A股 中文）
    ENHANCED_SEARCH_KEYWORDS = [
        "{name} 股票 今日 股价",
        "{name} {code} 最新 行情 走势",
        "{name} 股票 分析 走势图",
        "{name} K线 技术分析",
        "{name} {code} 涨跌 成交量",
    ]

    # 增强搜索关键词模板（港股/美股 英文）
    ENHANCED_SEARCH_KEYWORDS_EN = [
        "{name} stock price today",
        "{name} {code} latest quote trend",
        "{name} stock analysis chart",
        "{name} technical analysis",
        "{name} {code} performance volume",
    ]
    NEWS_OVERSAMPLE_FACTOR = 2
    NEWS_OVERSAMPLE_MAX = 10
    FUTURE_TOLERANCE_DAYS = 1
    
    def __init__(
        self,
        tavily_keys: Optional[List[str]] = None,
        anspire_keys: Optional[List[str]] = None,
        bocha_keys: Optional[List[str]] = None,
        brave_keys: Optional[List[str]] = None,
        serpapi_keys: Optional[List[str]] = None,
        minimax_keys: Optional[List[str]] = None,
        searxng_base_urls: Optional[List[str]] = None,
        searxng_public_instances_enabled: bool = False,
        openbb_news_enabled: bool = False,
        openbb_news_provider: str = "yfinance",
        finnhub_api_key: Optional[str] = None,
        news_max_age_days: int = 3,
        news_strategy_profile: str = "short",
        **_legacy_kwargs,
    ):
        self._providers: List[BaseSearchProvider] = []
        self.news_max_age_days = max(1, news_max_age_days)
        raw_profile = (news_strategy_profile or "short").strip().lower()
        self.news_strategy_profile = normalize_news_strategy_profile(news_strategy_profile)
        if raw_profile != self.news_strategy_profile:
            logger.warning(
                "NEWS_STRATEGY_PROFILE '%s' 无效，已回退为 'short'",
                news_strategy_profile,
            )
        self.news_window_days = resolve_news_window_days(
            news_max_age_days=self.news_max_age_days,
            news_strategy_profile=self.news_strategy_profile,
        )
        self.news_profile_days = NEWS_STRATEGY_WINDOWS.get(
            self.news_strategy_profile,
            NEWS_STRATEGY_WINDOWS["short"],
        )

        # 1. 注册搜索引擎 Provider
        if tavily_keys:
            self._providers.append(TavilySearchProvider(tavily_keys))
            logger.info(f"已配置 Tavily 搜索，共 {len(tavily_keys)} 个 API Key")

        if finnhub_api_key:
            self._providers.append(FinnhubNewsProvider(api_key=finnhub_api_key))
            logger.info("已启用 Finnhub 公司新闻源")

        if openbb_news_enabled:

            self._providers.append(OpenBBNewsProvider(provider=openbb_news_provider))
            logger.info("已启用 OpenBB 公司新闻源")

        try:
            import akshare  # noqa: F401
            self._providers.append(AkshareNewsProvider(enabled=True))
            logger.info("已启用 AkShare 东方财富新闻源")
        except ImportError:
            logger.debug("AkShare 未安装，跳过东方财富新闻源")

        if not self._providers:
            logger.warning("未配置任何搜索能力，新闻搜索功能将不可用")

        self._cache: Dict[str, Tuple[float, SearchResponse]] = {}
        self._cache_ttl: int = 600
        logger.info(
            "新闻时效策略已启用: profile=%s, profile_days=%s, NEWS_MAX_AGE_DAYS=%s, effective_window=%s",
            self.news_strategy_profile,
            self.news_profile_days,
            self.news_max_age_days,
            self.news_window_days,
        )
    
    @staticmethod
    def _is_foreign_stock(stock_code: str) -> bool:
        code = stock_code.strip()
        if re.match(r'^[A-Za-z]{1,5}(\.[A-Za-z])?$', code):
            return True
        lower = code.lower()
        if lower.startswith('hk'):
            return True
        if code.isdigit() and len(code) == 5:
            return True
        return False

    _A_ETF_PREFIXES = ('51', '52', '56', '58', '15', '16', '18')
    _ETF_NAME_KEYWORDS = ('ETF', 'FUND', 'TRUST', 'INDEX', 'TRACKER', 'UNIT')

    @staticmethod
    def is_index_or_etf(stock_code: str, stock_name: str) -> bool:
        code = (stock_code or '').strip().split('.')[0]
        if not code:
            return False
        if code.isdigit() and len(code) == 6 and code.startswith(SearchService._A_ETF_PREFIXES):
            return True
        if is_us_index_code(code):
            return True
        if SearchService._is_foreign_stock(code):
            name_upper = (stock_name or '').upper()
            return any(kw in name_upper for kw in SearchService._ETF_NAME_KEYWORDS)
        return False

    @staticmethod
    def _is_hk_stock_code(stock_code: str) -> bool:
        code = (stock_code or "").strip().lower()
        return code.startswith("hk") or (code.isdigit() and len(code) == 5)

    @classmethod
    def _build_macro_news_query(cls, stock_code: str, stock_name: str) -> str:
        """Build a broad macro-news query for the stock's market context."""
        if cls._is_hk_stock_code(stock_code):
            return (
                "Federal Reserve interest rates HKMA liquidity China policy "
                "Hong Kong stocks market risk appetite latest news"
            )
        if cls._is_foreign_stock(stock_code):
            return (
                "Federal Reserve interest rates inflation treasury yields "
                "dollar market risk appetite latest news"
            )
        return (
            "美联储 利率 美债收益率 美元 人民币 中国央行 政策 "
            "A股 风险偏好 最新消息"
        )

    @property
    def is_available(self) -> bool:
        return any(p.is_available for p in self._providers)

    def _cache_key(self, query: str, max_results: int, days: int) -> str:
        return f"{query}|{max_results}|{days}"

    def _get_cached(self, key: str) -> Optional[SearchResponse]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, response = entry
        if time.time() - ts > self._cache_ttl:
            del self._cache[key]
            return None
        logger.debug(f"Search cache hit: {key[:60]}...")
        return response

    def _put_cache(self, key: str, response: SearchResponse) -> None:
        _MAX_CACHE_SIZE = 500
        if len(self._cache) >= _MAX_CACHE_SIZE:
            now = time.time()
            expired = [k for k, (ts, _) in self._cache.items() if now - ts > self._cache_ttl]
            for k in expired:
                del self._cache[k]
            if len(self._cache) >= _MAX_CACHE_SIZE:
                excess = len(self._cache) - _MAX_CACHE_SIZE + 1
                oldest = sorted(self._cache.keys(), key=lambda k: self._cache[k][0])[:excess]
                for k in oldest:
                    del self._cache[k]
        self._cache[key] = (time.time(), response)

    def _effective_news_window_days(self) -> int:
        return resolve_news_window_days(
            news_max_age_days=self.news_max_age_days,
            news_strategy_profile=self.news_strategy_profile,
        )

    @classmethod
    def _provider_request_size(cls, max_results: int) -> int:
        target = max(1, int(max_results))
        return max(target, min(target * cls.NEWS_OVERSAMPLE_FACTOR, cls.NEWS_OVERSAMPLE_MAX))

    @staticmethod
    def _parse_relative_news_date(text: str, now: datetime) -> Optional[date]:
        raw = (text or "").strip()
        if not raw:
            return None

        lower = raw.lower()
        if raw in {"今天", "今日", "刚刚"} or lower in {"today", "just now", "now"}:
            return now.date()
        if raw == "昨天" or lower == "yesterday":
            return (now - timedelta(days=1)).date()
        if raw == "前天":
            return (now - timedelta(days=2)).date()

        zh = re.match(r"^\s*(\d+)\s*(分钟|小时|天|周|个月|月|年)\s*前\s*$", raw)
        if zh:
            amount = int(zh.group(1))
            unit = zh.group(2)
            if unit == "分钟":
                return (now - timedelta(minutes=amount)).date()
            if unit == "小时":
                return (now - timedelta(hours=amount)).date()
            if unit == "天":
                return (now - timedelta(days=amount)).date()
            if unit == "周":
                return (now - timedelta(weeks=amount)).date()
            if unit in {"个月", "月"}:
                return (now - timedelta(days=amount * 30)).date()
            if unit == "年":
                return (now - timedelta(days=amount * 365)).date()

        en = re.match(
            r"^\s*(\d+)\s*(minute|minutes|min|mins|hour|hours|day|days|week|weeks|month|months|year|years)\s*ago\s*$",
            lower,
        )
        if en:
            amount = int(en.group(1))
            unit = en.group(2)
            if unit in {"minute", "minutes", "min", "mins"}:
                return (now - timedelta(minutes=amount)).date()
            if unit in {"hour", "hours"}:
                return (now - timedelta(hours=amount)).date()
            if unit in {"day", "days"}:
                return (now - timedelta(days=amount)).date()
            if unit in {"week", "weeks"}:
                return (now - timedelta(weeks=amount)).date()
            if unit in {"month", "months"}:
                return (now - timedelta(days=amount * 30)).date()
            if unit in {"year", "years"}:
                return (now - timedelta(days=amount * 365)).date()

        return None

    @classmethod
    def _normalize_news_publish_date(cls, value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                local_tz = datetime.now().astimezone().tzinfo or timezone.utc
                return value.astimezone(local_tz).date()
            return value.date()
        if isinstance(value, date):
            return value

        text = str(value).strip()
        if not text:
            return None
        now = datetime.now()
        local_tz = now.astimezone().tzinfo or timezone.utc

        relative_date = cls._parse_relative_news_date(text, now)
        if relative_date:
            return relative_date

        if text.isdigit() and len(text) in (10, 13):
            try:
                ts = int(text[:10]) if len(text) == 13 else int(text)
                return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(local_tz).date()
            except (OSError, OverflowError, ValueError):
                pass

        iso_candidate = text.replace("Z", "+00:00")
        try:
            parsed_iso = datetime.fromisoformat(iso_candidate)
            if parsed_iso.tzinfo is not None:
                return parsed_iso.astimezone(local_tz).date()
            return parsed_iso.date()
        except ValueError:
            pass

        normalized = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE)

        try:
            parsed_rfc = parsedate_to_datetime(normalized)
            if parsed_rfc:
                if parsed_rfc.tzinfo is not None:
                    return parsed_rfc.astimezone(local_tz).date()
                return parsed_rfc.date()
        except (TypeError, ValueError):
            pass

        zh_match = re.search(r"(\d{4})\s*[年/\-\.]\s*(\d{1,2})\s*[月/\-\.]\s*(\d{1,2})\s*日?", text)
        if zh_match:
            try:
                return date(int(zh_match.group(1)), int(zh_match.group(2)), int(zh_match.group(3)))
            except ValueError:
                pass

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d",
            "%Y%m%d",
            "%b %d, %Y",
            "%B %d, %Y",
            "%d %b %Y",
            "%d %B %Y",
            "%a, %d %b %Y %H:%M:%S %z",
        ):
            try:
                parsed_dt = datetime.strptime(normalized, fmt)
                if parsed_dt.tzinfo is not None:
                    return parsed_dt.astimezone(local_tz).date()
                return parsed_dt.date()
            except ValueError:
                continue

        return None

    # 最少需要的新闻条数，低于此数量会触发降级
    MIN_NEWS_COUNT = 3

    def _apply_date_filter(
        self,
        results: List[SearchResult],
        search_days: int,
        max_results: int,
    ) -> List[SearchResult]:
        """按日期窗口过滤新闻。"""
        today = datetime.now().date()
        earliest = today - timedelta(days=max(0, int(search_days) - 1))
        latest = today + timedelta(days=self.FUTURE_TOLERANCE_DAYS)

        filtered: List[SearchResult] = []
        for item in results:
            published = self._normalize_news_publish_date(item.published_date)
            if published is None:
                continue
            if published < earliest or published > latest:
                continue
            filtered.append(
                SearchResult(
                    title=item.title,
                    snippet=item.snippet,
                    url=item.url,
                    source=item.source,
                    published_date=published.isoformat(),
                )
            )
            if len(filtered) >= max_results:
                break
        return filtered

    def _filter_news_response(
        self,
        response: SearchResponse,
        *,
        search_days: int,
        max_results: int,
        log_scope: str,
        strict: bool = False,
    ) -> SearchResponse:
        if not response.success or not response.results:
            return response

        # 第一次过滤：使用原始 search_days
        filtered = self._apply_date_filter(response.results, search_days, max_results)

        # 降级策略：如果过滤后为空或太少，逐步放宽时间窗口（strict模式不降级）
        fallback_windows = [7, 14, 30]
        used_window = search_days
        if not strict and (not filtered or len(filtered) < self.MIN_NEWS_COUNT):
            for window in fallback_windows:
                if window <= search_days:
                    continue
                filtered = self._apply_date_filter(response.results, window, max_results)
                if filtered:
                    used_window = window
                    logger.info(
                        "[新闻过滤] %s: 原始窗口 %s天无结果，放宽到 %s天获得 %s 条新闻",
                        log_scope, search_days, window, len(filtered)
                    )
                    break

        # 最终降级：如果所有时间窗口都为空，保留所有新闻（不过滤日期，strict模式不降级）
        if not strict and not filtered:
            filtered = [
                SearchResult(
                    title=item.title,
                    snippet=item.snippet,
                    url=item.url,
                    source=item.source,
                    published_date=item.published_date,
                )
                for item in response.results[:max_results]
            ]
            logger.info(
                "[新闻过滤] %s: 所有时间窗口均无结果，保留 %s 条新闻（不过滤日期）",
                log_scope, len(filtered)
            )

        return SearchResponse(
            query=response.query,
            results=filtered,
            provider=response.provider,
            success=response.success,
            error_message=response.error_message,
            search_time=response.search_time,
        )

    def _normalize_and_limit_response(
        self,
        response: SearchResponse,
        *,
        max_results: int,
    ) -> SearchResponse:
        if not response.success or not response.results:
            return response

        normalized_results: List[SearchResult] = []
        for item in response.results[:max_results]:
            normalized_date = self._normalize_news_publish_date(item.published_date)
            normalized_results.append(
                SearchResult(
                    title=item.title,
                    snippet=item.snippet,
                    url=item.url,
                    source=item.source,
                    published_date=(
                        normalized_date.isoformat() if normalized_date is not None else item.published_date
                    ),
                )
            )

        return SearchResponse(
            query=response.query,
            results=normalized_results,
            provider=response.provider,
            success=response.success,
            error_message=response.error_message,
            search_time=response.search_time,
        )
    
    async def search_stock_news_async(
        self,
        stock_code: str,
        stock_name: str,
        max_results: int = 5,
        focus_keywords: Optional[List[str]] = None
    ) -> SearchResponse:
        """异步搜索股票新闻"""
        search_days = self._effective_news_window_days()
        provider_max_results = self._provider_request_size(max_results)

        is_foreign = self._is_foreign_stock(stock_code)
        if focus_keywords:
            query = " ".join(focus_keywords)
        elif is_foreign:
            query = f"{stock_name} {stock_code} stock latest news"
        else:
            query = f"{stock_name} {stock_code} 股票 最新消息"

        logger.info(
            f"[搜索新闻 Async] {stock_name}({stock_code}), query='{query}', 范围: {search_days}d"
        )

        cache_key = self._cache_key(query, max_results, search_days)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        had_provider_success = False
        for provider in self._providers:
            if not provider.is_available: continue

            search_kwargs: Dict[str, Any] = {}
            if isinstance(provider, TavilySearchProvider):
                search_kwargs["topic"] = "news"
            if hasattr(provider, "search_async"):
                response = await provider.search_async(query, provider_max_results, days=search_days, **search_kwargs)
                filtered_response = self._filter_news_response(
                    response, search_days=search_days, max_results=max_results,
                    log_scope=f"{stock_code}:{provider.name}:stock_news_async",
                )
                had_provider_success = had_provider_success or bool(response.success)
                if filtered_response.success and filtered_response.results:
                    self._put_cache(cache_key, filtered_response)
                    return filtered_response
            
        return SearchResponse(query=query, results=[], provider="None", success=had_provider_success)

    async def search_macro_news_async(
        self,
        stock_code: str,
        stock_name: str,
        max_results: int = 5,
    ) -> SearchResponse:
        """Search recent market-level macro news for the stock's region."""
        search_days = self._effective_news_window_days()
        provider_max_results = self._provider_request_size(max_results)
        query = self._build_macro_news_query(stock_code, stock_name)

        cache_key = self._cache_key(f"macro:{query}", max_results, search_days)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        had_provider_success = False
        for provider in self._providers:
            if not provider.is_available:
                continue
            if not isinstance(provider, TavilySearchProvider):
                continue
            if not hasattr(provider, "search_async"):
                continue

            response = await provider.search_async(
                query,
                provider_max_results,
                days=search_days,
                topic="news",
            )
            filtered_response = self._filter_news_response(
                response,
                search_days=search_days,
                max_results=max_results,
                log_scope=f"{stock_code}:{provider.name}:macro_news_async",
                strict=True,
            )
            had_provider_success = had_provider_success or bool(response.success)
            if filtered_response.success and filtered_response.results:
                self._put_cache(cache_key, filtered_response)
                return filtered_response

        return SearchResponse(query=query, results=[], provider="None", success=had_provider_success)

    async def search_comprehensive_intel_async(
        self,
        stock_code: str,
        stock_name: str,
        max_searches: int = 3
    ) -> Dict[str, SearchResponse]:
        """并发执行多维度的异步深度情报搜索"""
        is_index_etf = self.is_index_or_etf(stock_code, stock_name)

        search_dimensions = [
            {
                'name': 'latest_news',
                'query': f"{stock_name} {stock_code} latest news events",
                'desc': '最新消息',
                'strict_freshness': True,
            },
            {
                'name': 'risk_check',
                'query': f"{stock_name} {stock_code} risk insider selling lawsuit" if not is_index_etf else f"{stock_name} tracking error outlook",
                'desc': '风险排查',
                'strict_freshness': not is_index_etf,
            },
            {
                'name': 'bearish_check',
                'query': f"{stock_name} {stock_code} 利空 风险 下跌 处罚 诉讼 预警" if not is_index_etf else f"{stock_name} downside risk warning",
                'desc': '利空排查',
                'strict_freshness': True,
            },
            {
                'name': 'earnings',
                'query': f"{stock_name} {stock_code} earnings forecast revenue profit" if not is_index_etf else f"{stock_name} performance outlook",
                'desc': '业绩预期',
                'strict_freshness': False,
            },
            {
                'name': 'macro_news',
                'query': self._build_macro_news_query(stock_code, stock_name),
                'desc': '宏观新闻',
                'strict_freshness': True,
            }
        ]

        # 限制维度数量
        search_dimensions = search_dimensions[:max_searches]
        
        async def _single_dimension_search(dim):
            if dim['name'] == 'macro_news':
                return dim['name'], await self.search_macro_news_async(
                    stock_code, stock_name, max_results=5
                )
            return dim['name'], await self.search_stock_news_async(
                stock_code, stock_name, max_results=5, focus_keywords=[dim['query']]
            )

        tasks = [asyncio.create_task(_single_dimension_search(dim)) for dim in search_dimensions]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        
        final_results = []
        for i, res in enumerate(results_list):
            if isinstance(res, Exception):
                logger.error(f"[搜索情报] 维度 {search_dimensions[i]['name']} 失败: {res}")
                continue
            final_results.append(res)

        return {name: resp for name, resp in final_results}

    def search_stock_news(
        self,
        stock_code: str,
        stock_name: str,
        max_results: int = 5,
        focus_keywords: Optional[List[str]] = None
    ) -> SearchResponse:
        search_days = self._effective_news_window_days()
        provider_max_results = self._provider_request_size(max_results)

        is_foreign = self._is_foreign_stock(stock_code)
        if focus_keywords:
            query = " ".join(focus_keywords)
        elif is_foreign:
            query = f"{stock_name} {stock_code} stock latest news"
        else:
            query = f"{stock_name} {stock_code} 股票 最新消息"

        logger.info(
            (
                "搜索股票新闻: %s(%s), query='%s', 时间范围: 近%s天 "
                "(profile=%s, NEWS_MAX_AGE_DAYS=%s), 目标条数=%s, provider请求条数=%s"
            ),
            stock_name, stock_code, query, search_days,
            self.news_strategy_profile, self.news_max_age_days,
            max_results, provider_max_results,
        )

        cache_key = self._cache_key(query, max_results, search_days)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.info(f"使用缓存搜索结果: {stock_name}({stock_code})")
            return cached

        had_provider_success = False
        for provider in self._providers:
            if not provider.is_available:
                continue

            search_kwargs: Dict[str, Any] = {}
            if isinstance(provider, TavilySearchProvider):
                search_kwargs["topic"] = "news"

            response = provider.search(query, provider_max_results, days=search_days, **search_kwargs)
            filtered_response = self._filter_news_response(
                response,
                search_days=search_days,
                max_results=max_results,
                log_scope=f"{stock_code}:{provider.name}:stock_news",
                strict=True,
            )
            had_provider_success = had_provider_success or bool(response.success)

            if filtered_response.success and filtered_response.results:
                logger.info(f"使用 {provider.name} 搜索成功")
                self._put_cache(cache_key, filtered_response)
                return filtered_response
            else:
                if response.success and not filtered_response.results:
                    logger.info(
                        "%s 搜索成功但过滤后无有效新闻，继续尝试下一引擎",
                        provider.name,
                    )
                else:
                    logger.warning(
                        "%s 搜索失败: %s，尝试下一个引擎",
                        provider.name,
                        response.error_message,
                    )

        if had_provider_success:
            return SearchResponse(
                query=query, results=[], provider="Filtered",
                success=True, error_message=None,
            )
        
        return SearchResponse(
            query=query, results=[], provider="None",
            success=False, error_message="所有搜索引擎都不可用或搜索失败"
        )

    def search_macro_news(
        self,
        stock_code: str,
        stock_name: str,
        max_results: int = 5,
    ) -> SearchResponse:
        """Search recent market-level macro news for the stock's region."""
        search_days = self._effective_news_window_days()
        provider_max_results = self._provider_request_size(max_results)
        query = self._build_macro_news_query(stock_code, stock_name)

        logger.info(
            (
                "搜索宏观新闻: %s(%s), query='%s', 时间范围: 近%s天 "
                "(profile=%s, NEWS_MAX_AGE_DAYS=%s), 目标条数=%s, provider请求条数=%s"
            ),
            stock_name, stock_code, query, search_days,
            self.news_strategy_profile, self.news_max_age_days,
            max_results, provider_max_results,
        )

        cache_key = self._cache_key(f"macro:{query}", max_results, search_days)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        had_provider_success = False
        for provider in self._providers:
            if not provider.is_available:
                continue
            if not isinstance(provider, TavilySearchProvider):
                continue

            response = provider.search(
                query,
                provider_max_results,
                days=search_days,
                topic="news",
            )
            filtered_response = self._filter_news_response(
                response,
                search_days=search_days,
                max_results=max_results,
                log_scope=f"{stock_code}:{provider.name}:macro_news",
                strict=True,
            )
            had_provider_success = had_provider_success or bool(response.success)
            if filtered_response.success and filtered_response.results:
                self._put_cache(cache_key, filtered_response)
                return filtered_response

        return SearchResponse(query=query, results=[], provider="None", success=had_provider_success)
    
    def search_stock_events(
        self,
        stock_code: str,
        stock_name: str,
        event_types: Optional[List[str]] = None
    ) -> SearchResponse:
        if event_types is None:
            if self._is_foreign_stock(stock_code):
                event_types = ["earnings report", "insider selling", "quarterly results"]
            else:
                event_types = ["年报预告", "减持公告", "业绩快报"]
        
        event_query = " OR ".join(event_types)
        query = f"{stock_name} ({event_query})"
        
        logger.info(f"搜索股票事件: {stock_name}({stock_code}) - {event_types}")
        
        for provider in self._providers:
            if not provider.is_available:
                continue
            response = provider.search(query, max_results=5)
            if response.success:
                return response
        
        return SearchResponse(
            query=query, results=[], provider="None",
            success=False, error_message="事件搜索失败"
        )
    
    def search_comprehensive_intel(
        self,
        stock_code: str,
        stock_name: str,
        max_searches: int = 3
    ) -> Dict[str, SearchResponse]:
        results = {}
        search_count = 0

        is_foreign = self._is_foreign_stock(stock_code)
        is_index_etf = self.is_index_or_etf(stock_code, stock_name)

        if is_foreign:
            search_dimensions = [
                {
                    'name': 'latest_news',
                    'query': f"{stock_name} {stock_code} latest news events",
                    'desc': '最新消息',
                    'tavily_topic': 'news',
                    'strict_freshness': True,
                },
                {
                    'name': 'market_analysis',
                    'query': f"{stock_name} analyst rating target price report",
                    'desc': '机构分析',
                    'tavily_topic': None,
                    'strict_freshness': False,
                },
                {
                    'name': 'risk_check',
                    'query': (
                        f"{stock_name} {stock_code} index performance outlook tracking error"
                        if is_index_etf else f"{stock_name} risk insider selling lawsuit litigation"
                    ),
                    'desc': '风险排查',
                    'tavily_topic': None if is_index_etf else 'news',
                    'strict_freshness': not is_index_etf,
                },
                {
                    'name': 'macro_news',
                    'query': self._build_macro_news_query(stock_code, stock_name),
                    'desc': '宏观新闻',
                    'tavily_topic': 'news',
                    'strict_freshness': True,
                },
                {
                    'name': 'earnings',
                    'query': (
                        f"{stock_name} {stock_code} index performance composition outlook"
                        if is_index_etf else f"{stock_name} earnings revenue profit growth forecast"
                    ),
                    'desc': '业绩预期',
                    'tavily_topic': None,
                    'strict_freshness': False,
                },
                {
                    'name': 'industry',
                    'query': (
                        f"{stock_name} {stock_code} index sector allocation holdings"
                        if is_index_etf else f"{stock_name} industry competitors market share outlook"
                    ),
                    'desc': '行业分析',
                    'tavily_topic': None,
                    'strict_freshness': False,
                },
            ]
        else:
            search_dimensions = [
                {
                    'name': 'latest_news',
                    'query': f"{stock_name} {stock_code} 最新 新闻 重大 事件",
                    'desc': '最新消息',
                    'tavily_topic': 'news',
                    'strict_freshness': True,
                },
                {
                    'name': 'market_analysis',
                    'query': f"{stock_name} 研报 目标价 评级 深度分析",
                    'desc': '机构分析',
                    'tavily_topic': None,
                    'strict_freshness': False,
                },
                {
                    'name': 'risk_check',
                    'query': (
                        f"{stock_name} 指数走势 跟踪误差 净值 表现"
                        if is_index_etf else f"{stock_name} 减持 处罚 违规 诉讼 利空 风险"
                    ),
                    'desc': '风险排查',
                    'tavily_topic': None if is_index_etf else 'news',
                    'strict_freshness': not is_index_etf,
                },
                {
                    'name': 'announcements',
                    'query': (
                        f"{stock_name} {stock_code} 公告 指数调整 成分变化"
                        if is_index_etf else f"{stock_name} {stock_code} 公司公告 重要公告 上交所 深交所 cninfo"
                    ),
                    'desc': '公司公告',
                    'tavily_topic': 'news',
                    'strict_freshness': True,
                },
                {
                    'name': 'macro_news',
                    'query': self._build_macro_news_query(stock_code, stock_name),
                    'desc': '宏观新闻',
                    'tavily_topic': 'news',
                    'strict_freshness': True,
                },
                {
                    'name': 'earnings',
                    'query': (
                        f"{stock_name} 指数成分 净值 跟踪表现"
                        if is_index_etf else f"{stock_name} 业绩预告 财报 营收 净利润 同比增长"
                    ),
                    'desc': '业绩预期',
                    'tavily_topic': None,
                    'strict_freshness': False,
                },
                {
                    'name': 'industry',
                    'query': (
                        f"{stock_name} 指数成分股 行业配置 权重"
                        if is_index_etf else f"{stock_name} 所在行业 竞争对手 市场份额 行业前景"
                    ),
                    'desc': '行业分析',
                    'tavily_topic': None,
                    'strict_freshness': False,
                },
            ]
        
        search_days = self._effective_news_window_days()
        target_per_dimension = 3
        provider_max_results = self._provider_request_size(target_per_dimension)

        logger.info(
            (
                "开始多维度情报搜索: %s(%s), 时间范围: 近%s天 "
                "(profile=%s, NEWS_MAX_AGE_DAYS=%s), 目标条数=%s, provider请求条数=%s"
            ),
            stock_name, stock_code, search_days,
            self.news_strategy_profile, self.news_max_age_days,
            target_per_dimension, provider_max_results,
        )
        
        for dim in search_dimensions:
            if search_count >= max_searches:
                break
            
            available_providers = [p for p in self._providers if p.is_available]
            if not available_providers:
                break

            response = None
            provider = None
            if dim['name'] == 'macro_news':
                filtered_response = self.search_macro_news(
                    stock_code,
                    stock_name,
                    max_results=target_per_dimension,
                )
                results[dim['name']] = filtered_response
                search_count += 1
                logger.info(
                    "[情报搜索] %s: 过滤后=%s条",
                    dim['desc'],
                    len(filtered_response.results),
                )
                time.sleep(0.5)
                continue

            for candidate in available_providers:
                provider = candidate
                logger.info(f"[情报搜索] {dim['desc']}: 使用 {provider.name}")
                if isinstance(provider, TavilySearchProvider) and dim.get('tavily_topic'):
                    response = provider.search(
                        dim['query'],
                        max_results=provider_max_results,
                        days=search_days,
                        topic=dim['tavily_topic'],
                    )
                else:
                    response = provider.search(
                        dim['query'],
                        max_results=provider_max_results,
                        days=search_days,
                    )
                if response.success:
                    break

            if response is None or provider is None:
                continue
            if dim['strict_freshness']:
                filtered_response = self._filter_news_response(
                    response,
                    search_days=search_days,
                    max_results=target_per_dimension,
                    log_scope=f"{stock_code}:{provider.name}:{dim['name']}",
                    strict=dim['strict_freshness'],
                )
            else:
                filtered_response = self._normalize_and_limit_response(
                    response,
                    max_results=target_per_dimension,
                )
            results[dim['name']] = filtered_response
            search_count += 1
            
            if response.success:
                logger.info(
                    "[情报搜索] %s: 原始=%s条, 过滤后=%s条",
                    dim['desc'],
                    len(response.results),
                    len(filtered_response.results),
                )
            else:
                logger.warning(f"[情报搜索] {dim['desc']}: 搜索失败 - {response.error_message}")
            
            time.sleep(0.5)
        
        return results
    
    def format_intel_report(self, intel_results: Dict[str, SearchResponse], stock_name: str) -> str:
        lines = [f"【{stock_name} 情报搜索结果】"]
        
        display_order = ['latest_news', 'macro_news', 'announcements', 'market_analysis', 'risk_check', 'bearish_check', 'earnings', 'industry']

        dim_labels = {
            'latest_news': '📰 最新消息',
            'macro_news': '🌐 宏观新闻',
            'announcements': '📋 公司公告',
            'market_analysis': '📈 机构分析',
            'risk_check': '⚠️ 风险排查',
            'bearish_check': '🐻 利空排查',
            'earnings': '📊 业绩预期',
            'industry': '🏭 行业分析',
        }

        for dim_name in display_order:
            if dim_name not in intel_results:
                continue
                
            resp = intel_results[dim_name]
            dim_desc = dim_labels.get(dim_name, dim_name)
            
            lines.append(f"\n{dim_desc} (来源: {resp.provider}):")
            if resp.success and resp.results:
                for i, r in enumerate(resp.results[:4], 1):
                    date_str = f" [{r.published_date}]" if r.published_date else ""
                    lines.append(f"  {i}. {r.title}{date_str}")
                    snippet = r.snippet[:150] if len(r.snippet) > 20 else r.snippet
                    lines.append(f"     {snippet}...")
            else:
                lines.append("  未找到相关信息")
        
        return "\n".join(lines)
    
    def batch_search(
        self,
        stocks: List[Dict[str, str]],
        max_results_per_stock: int = 3,
        delay_between: float = 1.0
    ) -> Dict[str, SearchResponse]:
        results = {}
        for i, stock in enumerate(stocks):
            if i > 0:
                time.sleep(delay_between)
            code = stock.get('code', '')
            name = stock.get('name', '')
            response = self.search_stock_news(code, name, max_results_per_stock)
            results[code] = response
        return results

    def search_stock_price_fallback(
        self,
        stock_code: str,
        stock_name: str,
        max_attempts: int = 3,
        max_results: int = 5
    ) -> SearchResponse:
        if not self.is_available:
            return SearchResponse(
                query=f"{stock_name} 股价走势", results=[], provider="None",
                success=False, error_message="未配置搜索能力"
            )
        
        logger.info(f"[增强搜索] 数据源失败，启动增强搜索: {stock_name}({stock_code})")
        
        all_results = []
        seen_urls = set()
        successful_providers = []
        
        is_foreign = self._is_foreign_stock(stock_code)
        keywords = self.ENHANCED_SEARCH_KEYWORDS_EN if is_foreign else self.ENHANCED_SEARCH_KEYWORDS
        for i, keyword_template in enumerate(keywords[:max_attempts]):
            query = keyword_template.format(name=stock_name, code=stock_code)
            
            logger.info(f"[增强搜索] 第 {i+1}/{max_attempts} 次搜索: {query}")
            
            for provider in self._providers:
                if not provider.is_available:
                    continue
                try:
                    response = provider.search(query, max_results=3)
                    if response.success and response.results:
                        for result in response.results:
                            if result.url not in seen_urls:
                                seen_urls.add(result.url)
                                all_results.append(result)
                        if provider.name not in successful_providers:
                            successful_providers.append(provider.name)
                        logger.info(f"[增强搜索] {provider.name} 返回 {len(response.results)} 条结果")
                        break
                    else:
                        logger.debug(f"[增强搜索] {provider.name} 无结果或失败")
                except Exception as e:
                    logger.warning(f"[增强搜索] {provider.name} 搜索异常: {e}")
                    continue
            
            if i < max_attempts - 1:
                time.sleep(0.5)
        
        if all_results:
            final_results = all_results[:max_results]
            provider_str = ", ".join(successful_providers) if successful_providers else "None"
            logger.info(f"[增强搜索] 完成，共获取 {len(final_results)} 条结果（来源: {provider_str}）")
            return SearchResponse(
                query=f"{stock_name}({stock_code}) 股价走势",
                results=final_results, provider=provider_str, success=True,
            )
        else:
            logger.warning(f"[增强搜索] 所有搜索均未返回结果")
            return SearchResponse(
                query=f"{stock_name}({stock_code}) 股价走势",
                results=[], provider="None",
                success=False, error_message="增强搜索未找到相关信息"
            )

    def search_stock_with_enhanced_fallback(
        self,
        stock_code: str,
        stock_name: str,
        include_news: bool = True,
        include_price: bool = False,
        max_results: int = 5
    ) -> Dict[str, SearchResponse]:
        results = {}
        if include_news:
            results['news'] = self.search_stock_news(stock_code, stock_name, max_results=max_results)
        if include_price:
            results['price'] = self.search_stock_price_fallback(
                stock_code, stock_name, max_attempts=3, max_results=max_results
            )
        return results

    def format_price_search_context(self, response: SearchResponse) -> str:
        if not response.success or not response.results:
            return "【股价走势搜索】未找到相关信息，请以其他渠道数据为准。"
        
        lines = [
            f"【股价走势搜索结果】（来源: {response.provider}）",
            "⚠️ 注意：以下信息来自网络搜索，仅供参考，可能存在延迟或不准确。",
            ""
        ]
        
        for i, result in enumerate(response.results, 1):
            date_str = f" [{result.published_date}]" if result.published_date else ""
            lines.append(f"{i}. 【{result.source}】{result.title}{date_str}")
            lines.append(f"   {result.snippet[:200]}...")
            lines.append("")
        
        return "\n".join(lines)


# === 便捷函数 ===
_search_service: Optional[SearchService] = None


def get_search_service() -> SearchService:
    """获取搜索服务单例"""
    global _search_service
    
    if _search_service is None:
        from src.config import get_config
        config = get_config()
        
        _search_service = SearchService(
            tavily_keys=getattr(config, "tavily_api_keys", None),
            finnhub_api_key=getattr(config, "finnhub_api_key", None),
            openbb_news_enabled=getattr(config, "openbb_news_enabled", False),
            openbb_news_provider=getattr(config, "openbb_news_provider", "yfinance"),
            news_max_age_days=getattr(config, "news_max_age_days", 3),
            news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
        )
    
    return _search_service


def reset_search_service() -> None:
    """重置搜索服务（用于测试）"""
    global _search_service
    _search_service = None
