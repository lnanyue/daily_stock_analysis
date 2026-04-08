# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 分析服务层 (Async-First)
===================================

职责：
1. 封装核心分析逻辑，支持多调用方（CLI、WebUI、Bot）
2. 提供清晰的异步 API 接口，不依赖于命令行参数
3. 支持并发执行多股分析
"""

import uuid
import asyncio
import logging
from typing import List, Optional

from src.analyzer import AnalysisResult
from src.config import get_config, Config
from src.notification import NotificationService
from src.enums import ReportType
from src.core.pipeline import StockAnalysisPipeline
from src.core.market_review import run_market_review

logger = logging.getLogger(__name__)


async def analyze_stock_async(
    stock_code: str,
    config: Config = None,
    full_report: bool = False,
    notifier: Optional[NotificationService] = None
) -> Optional[AnalysisResult]:
    """
    分析单只股票 (异步)
    """
    if config is None:
        config = get_config()
    
    pipeline = StockAnalysisPipeline(
        config=config,
        query_id=uuid.uuid4().hex,
        query_source="cli"
    )
    
    if notifier:
        pipeline.notifier = notifier
    
    report_type = ReportType.FULL if full_report else ReportType.SIMPLE
    
    # process_single_stock is async
    result = await pipeline.process_single_stock(
        code=stock_code,
        skip_analysis=False,
        single_stock_notify=notifier is not None,
        report_type=report_type
    )
    
    return result

async def analyze_stocks_async(
    stock_codes: List[str],
    config: Config = None,
    full_report: bool = False,
    notifier: Optional[NotificationService] = None,
    max_concurrent: int = 5
) -> List[AnalysisResult]:
    """
    并发分析多只股票
    """
    if config is None:
        config = get_config()
    
    pipeline = StockAnalysisPipeline(
        config=config,
        query_id=uuid.uuid4().hex,
        query_source="cli",
        max_workers=max_concurrent
    )
    
    if notifier:
        pipeline.notifier = notifier

    report_type = ReportType.FULL if full_report else ReportType.SIMPLE
    
    results = await pipeline.run(
        stock_codes=stock_codes,
        dry_run=False,
        send_notification=notifier is not None,
        merge_notification=False
    )
    
    return results

async def perform_market_review_async(
    config: Config = None,
    notifier: Optional[NotificationService] = None
) -> Optional[str]:
    """
    执行大盘复盘 (异步)
    """
    if config is None:
        config = get_config()
    
    pipeline = StockAnalysisPipeline(
        config=config,
        query_id=uuid.uuid4().hex,
        query_source="cli"
    )
    
    review_notifier = notifier or pipeline.notifier
    
    return await run_market_review(
        notifier=review_notifier,
        analyzer=pipeline.analyzer,
        search_service=pipeline.search_service
    )

# --- Backward Compatibility (Legacy Sync Wrappers) ---

def analyze_stock(stock_code: str, config: Config = None, full_report: bool = False, notifier: Optional[NotificationService] = None):
    """Sync wrapper for analyze_stock_async."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # In an existing loop, we can't easily run sync. 
            # This is why the service should be async-first.
            # But for simple scripts, this works.
            return asyncio.run_coroutine_threadsafe(
                analyze_stock_async(stock_code, config, full_report, notifier), loop
            ).result()
    except RuntimeError:
        pass
    return asyncio.run(analyze_stock_async(stock_code, config, full_report, notifier))

def analyze_stocks(stock_codes: List[str], config: Config = None, full_report: bool = False, notifier: Optional[NotificationService] = None):
    """Sync wrapper for analyze_stocks_async."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(
                analyze_stocks_async(stock_codes, config, full_report, notifier), loop
            ).result()
    except RuntimeError:
        pass
    return asyncio.run(analyze_stocks_async(stock_codes, config, full_report, notifier))

def perform_market_review(config: Config = None, notifier: Optional[NotificationService] = None):
    """Sync wrapper for perform_market_review_async."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(
                perform_market_review_async(config, notifier), loop
            ).result()
    except RuntimeError:
        pass
    return asyncio.run(perform_market_review_async(config, notifier))
