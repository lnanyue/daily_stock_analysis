# -*- coding: utf-8 -*-
"""Notification helpers for ``StockAnalysisPipeline``."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from typing import Any, List, Optional

from data_provider.utils import maybe_await
from src.analyzer import AnalysisResult
from src.enums import ReportType
from src.notification import NotificationChannel

logger = logging.getLogger(__name__)


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


_SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD = threading.Lock()


_SINGLE_STOCK_NOTIFY_LOCK: threading.Lock = threading.Lock()


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
    lock = notify_lock if notify_lock is not None else _SINGLE_STOCK_NOTIFY_LOCK
    with lock:
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


# ---------------------------------------------------------------------------
#  Batch / bulk notification helpers  (moved from pipeline.py)
# ---------------------------------------------------------------------------


def send_notifications(
    notifier: Any,
    config: Any,
    results: List[AnalysisResult],
    report_type: ReportType = ReportType.SIMPLE,
) -> bool:
    """Synchronous batch notification: WeChat + email groups."""
    if not results or not notifier.is_available():
        return False

    channels = notifier.get_available_channels()
    sent = False

    def _channel_enabled(target: NotificationChannel) -> bool:
        return any(channel == target or getattr(channel, "value", None) == target.value for channel in channels)

    def _send_email_report(subset: List[AnalysisResult], receivers: Optional[List[str]] = None) -> None:
        nonlocal sent
        content = notifier.generate_dashboard_report(subset)
        image_bytes = None
        if "email" in getattr(notifier, "_markdown_to_image_channels", set()):
            from src.md2img import markdown_to_image

            image_bytes = markdown_to_image(
                content,
                max_chars=getattr(notifier, "_markdown_to_image_max_chars", 15000),
            )
        if notifier._should_use_image_for_channel(NotificationChannel.EMAIL, image_bytes):
            sent = bool(sync_maybe_await(
                notifier._send_email_with_inline_image(
                    content,
                    image_bytes,
                    receivers=receivers,
                )
            )) or sent
        else:
            sent = bool(sync_maybe_await(
                notifier.send_to_email(content, receivers=receivers)
            )) or sent

    if _channel_enabled(NotificationChannel.WECHAT):
        wechat_content = (
            notifier.generate_wechat_dashboard(results)
            if hasattr(notifier, "generate_wechat_dashboard")
            else notifier.generate_dashboard_report(results)
        )
        image_bytes = None
        if "wechat" in getattr(notifier, "_markdown_to_image_channels", set()):
            from src.md2img import markdown_to_image

            image_bytes = markdown_to_image(
                wechat_content,
                max_chars=getattr(notifier, "_markdown_to_image_max_chars", 15000),
            )
        if notifier._should_use_image_for_channel(NotificationChannel.WECHAT, image_bytes):
            sent = bool(sync_maybe_await(notifier._send_wechat_image(image_bytes))) or sent
        else:
            if "wechat" in getattr(notifier, "_markdown_to_image_channels", set()):
                from src.config import get_config

                engine = getattr(get_config(), "md2img_engine", "unknown")
                logger.warning("企业微信 Markdown 转图片失败，已回退文本推送，engine=%s", engine)
            sent = bool(sync_maybe_await(notifier.send_to_wechat(wechat_content))) or sent

    if _channel_enabled(NotificationChannel.EMAIL):
        groups = list(getattr(config, "stock_email_groups", []) or [])
        grouped_codes = set()
        for codes, receivers in groups:
            codes_set = set(codes or [])
            subset = [result for result in results if getattr(result, "code", None) in codes_set]
            if not subset:
                continue
            grouped_codes.update(getattr(result, "code", None) for result in subset)
            _send_email_report(subset, receivers=list(receivers or []))

        remaining = [result for result in results if getattr(result, "code", None) not in grouped_codes]
        if remaining:
            _send_email_report(remaining, receivers=None)

    return sent


def send_single_stock_notification_sync_wrapper(
    notifier: Any,
    result: AnalysisResult,
    report_type: ReportType = ReportType.SIMPLE,
    fallback_code: Optional[str] = None,
) -> None:
    """Synchronous single-stock notification wrapper."""
    try:
        sync_maybe_await(
            send_single_stock_notification_async_wrapper(
                notifier,
                result,
                report_type=report_type,
                fallback_code=fallback_code,
            )
        )
    except Exception as e:
        stock_code = getattr(result, "code", None) or fallback_code or "unknown"
        logger.error("[%s] 单股推送异常: %s", stock_code, e)


async def send_single_stock_notification_async_wrapper(
    notifier: Any,
    result: AnalysisResult,
    report_type: ReportType = ReportType.SIMPLE,
    fallback_code: Optional[str] = None,
) -> bool:
    """Async single-stock notification with per-component lock."""
    stock_code = getattr(result, "code", None) or fallback_code or "unknown"
    notify_lock: Optional[threading.Lock] = None
    try:
        return await send_single_stock_notification(
            notifier=notifier,
            result=result,
            report_type=report_type,
            fallback_code=stock_code,
            notify_lock=notify_lock,
        )
    except Exception as e:
        logger.error("[%s] 单股推送异常: %s", stock_code, e)
        return False
