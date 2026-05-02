# -*- coding: utf-8 -*-
"""Notification helpers for ``StockAnalysisPipeline``."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from typing import Any, Optional

from src.analyzer import AnalysisResult
from src.enums import ReportType

logger = logging.getLogger(__name__)


from data_provider.utils import maybe_await  # noqa: F811


def sync_maybe_await(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    raise RuntimeError("Cannot synchronously wait for a coroutine while an event loop is running")


def build_single_stock_report_content(notifier: Any, result: AnalysisResult, report_type: ReportType) -> str:
    if report_type == ReportType.FULL:
        return notifier.generate_dashboard_report([result])
    if report_type == ReportType.BRIEF:
        return notifier.generate_brief_report([result])
    return notifier.generate_single_stock_report(result)


async def send_single_stock_notification(
    *,
    notifier: Any,
    result: AnalysisResult,
    report_type: ReportType = ReportType.SIMPLE,
    fallback_code: Optional[str] = None,
    notify_lock: Optional[threading.Lock] = None,
) -> bool:
    if not notifier.is_available():
        return False

    stock_code = getattr(result, "code", None) or fallback_code or "unknown"
    if notify_lock is not None:
        with notify_lock:
            report_content = build_single_stock_report_content(notifier, result, report_type)
    else:
        report_content = build_single_stock_report_content(notifier, result, report_type)

    if report_type == ReportType.FULL:
        logger.info("[%s] 使用完整报告格式", stock_code)
    elif report_type == ReportType.BRIEF:
        logger.info("[%s] 使用简洁报告格式", stock_code)
    else:
        logger.info("[%s] 使用精简报告格式", stock_code)

    success = bool(await maybe_await(notifier.send(report_content, email_stock_codes=[stock_code])))
    if success:
        logger.info("[%s] 单股推送成功", stock_code)
    else:
        logger.warning("[%s] 单股推送失败", stock_code)
    return success


def send_single_stock_notification_sync(**kwargs: Any) -> bool:
    return bool(sync_maybe_await(send_single_stock_notification(**kwargs)))
