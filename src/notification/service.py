# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 通知层 (Refactored)
===================================

职责：
1. 调度各种通知渠道进行消息推送
2. 委托 ReportRenderer 生成各种 Markdown 报告
3. 支持 Markdown 转图片发送
"""
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

from src.notification_constants import (
    NOTIFICATION_DEFAULT_MAX_RETRIES,
    NOTIFICATION_DEFAULT_TIMEOUT_SEC,
)

from src.config import get_config
from src.analyzer import AnalysisResult
from src.enums import ReportType
from src.report_language import normalize_report_language
from bot.models import BotMessage
from src.notification_sender import (
    CustomWebhookSender,
    DiscordSender,
    EmailSender,
    FeishuSender,
    PushoverSender,
    PushplusSender,
    Serverchan3Sender,
    WechatSender,
    WECHAT_IMAGE_MAX_BYTES
)
from .renderer import ReportRenderer
from .utils import get_source_display_name

logger = logging.getLogger(__name__)


class NotificationChannel(Enum):
    """通知渠道类型"""
    WECHAT = "wechat"
    FEISHU = "feishu"
    EMAIL = "email"
    PUSHOVER = "pushover"
    PUSHPLUS = "pushplus"
    SERVERCHAN3 = "serverchan3"
    CUSTOM = "custom"
    DISCORD = "discord"
    UNKNOWN = "unknown"


class ChannelDetector:
    """渠道检测器"""
    @staticmethod
    def get_channel_name(channel: NotificationChannel) -> str:
        names = {
            NotificationChannel.WECHAT: "企业微信",
            NotificationChannel.FEISHU: "飞书",
            NotificationChannel.EMAIL: "邮件",
            NotificationChannel.PUSHOVER: "Pushover",
            NotificationChannel.PUSHPLUS: "PushPlus",
            NotificationChannel.SERVERCHAN3: "Server酱3",
            NotificationChannel.CUSTOM: "自定义Webhook",
            NotificationChannel.DISCORD: "Discord机器人",
        }
        return names.get(channel, "未知渠道")


class NotificationService:
    """
    通知服务 - 调度器角色
    """
    def __init__(self, source_message: Optional[BotMessage] = None):
        config = get_config()
        self._source_message = source_message
        self._renderer = ReportRenderer()
        self._context_channels: List[str] = []

        self._markdown_to_image_channels = set(getattr(config, 'markdown_to_image_channels', []) or [])
        self._markdown_to_image_max_chars = getattr(config, 'markdown_to_image_max_chars', 15000)
        self._notification_max_retries = getattr(config, 'notification_max_retries', 2)

        # 初始化各渠道发送器 (组合模式)
        self._senders: Dict[NotificationChannel, Any] = {
            NotificationChannel.WECHAT: WechatSender(config),
            NotificationChannel.FEISHU: FeishuSender(config),
            NotificationChannel.EMAIL: EmailSender(config),
            NotificationChannel.PUSHOVER: PushoverSender(config),
            NotificationChannel.PUSHPLUS: PushplusSender(config),
            NotificationChannel.SERVERCHAN3: Serverchan3Sender(config),
            NotificationChannel.CUSTOM: CustomWebhookSender(config),
            NotificationChannel.DISCORD: DiscordSender(config),
        }

        self._available_channels = [ch for ch, s in self._senders.items() if s.enabled]
        self._last_delivery_results: List[Dict[str, Any]] = []
        self._last_delivery_summary: str = "尚未执行通知发送"

    def _detect_all_channels(self) -> List[NotificationChannel]:
        """(Legacy) 已由 __init__ 中的列表推导式替代"""
        return self._available_channels

    def get_channel_names(self) -> List[str]:
        return [self._senders[ch].name for ch in self._available_channels]

    def is_available(self) -> bool:
        return bool(self._available_channels)

    def get_available_channels(self) -> List[NotificationChannel]:
        """获取当前已配置的可用渠道"""
        return self._available_channels

    def get_last_delivery_results(self) -> List[Dict[str, Any]]:
        """Return the latest channel delivery results for diagnostics."""
        return [dict(item) for item in self._last_delivery_results]

    def get_last_delivery_summary(self) -> str:
        """Return a human-readable summary of the latest delivery attempt."""
        return self._last_delivery_summary

    # ------------------------------------------------------------------
    # Content Generation (Delegated to Renderer)
    # ------------------------------------------------------------------
    def generate_aggregate_report(self, results: List[AnalysisResult], report_type: Optional[ReportType] = None) -> str:
        if report_type == ReportType.BRIEF:
            return self.generate_brief_report(results)
        if report_type == ReportType.FULL:
            return self.generate_dashboard_report(results)
        return self._renderer.generate_aggregate_report(results)

    def generate_daily_report(self, results: List[AnalysisResult]) -> str:
        return self._renderer.generate_daily_report(results)

    def generate_dashboard_report(self, results: List[AnalysisResult]) -> str:
        return self._renderer.generate_dashboard_report(results)

    def generate_brief_report(self, results: List[AnalysisResult]) -> str:
        return self._renderer.generate_brief_report(results)

    def generate_single_stock_report(self, result: AnalysisResult) -> str:
        return self._renderer.generate_single_stock_report(result)

    # ------------------------------------------------------------------
    # Forwarding methods to Senders (Legacy compatibility)
    # ------------------------------------------------------------------
    async def send_to_wechat(self, content: str) -> bool: return await self._senders[NotificationChannel.WECHAT].send(content)
    async def send_to_feishu(self, content: str) -> bool: return await self._senders[NotificationChannel.FEISHU].send(content)
    async def send_to_telegram(self, content: str) -> bool: return False # Telegram handled via Bot dispatcher usually
    async def send_to_email(self, content: str, receivers=None) -> bool: return await self._senders[NotificationChannel.EMAIL].send(content, receivers=receivers)
    async def send_to_pushover(self, content: str) -> bool: return await self._senders[NotificationChannel.PUSHOVER].send(content)
    async def send_to_pushplus(self, content: str) -> bool: return await self._senders[NotificationChannel.PUSHPLUS].send(content)
    async def send_to_serverchan3(self, content: str) -> bool: return await self._senders[NotificationChannel.SERVERCHAN3].send(content)
    async def send_to_custom(self, content: str) -> bool: return await self._senders[NotificationChannel.CUSTOM].send(content)
    async def send_to_discord(self, content: str) -> bool: return await self._senders[NotificationChannel.DISCORD].send(content)

    async def send(self, content: str, email_stock_codes: Optional[List[str]] = None, email_send_to_all: bool = False) -> bool:
        if not self._available_channels:
            self._last_delivery_results = []
            self._last_delivery_summary = "未配置任何通知渠道"
            return False
        
        image_bytes = None
        # Simplification: only convert if at least one channel needs it
        if any(ch.value in self._markdown_to_image_channels for ch in self._available_channels):
            from src.md2img import markdown_to_image
            image_bytes = markdown_to_image(content, max_chars=self._markdown_to_image_max_chars)

        coros = []
        for channel in self._available_channels:
            kwargs = {}
            if channel == NotificationChannel.EMAIL:
                if email_send_to_all:
                    kwargs['receivers'] = self._senders[channel].get_all_email_receivers()
                elif email_stock_codes:
                    kwargs['receivers'] = self._senders[channel].get_receivers_for_stocks(email_stock_codes)
            
            coros.append(self._send_channel_with_retry(channel, content, image_bytes, **kwargs))
        
        raw_results = await asyncio.gather(*coros, return_exceptions=True)
        normalized_results: List[Dict[str, Any]] = []
        for channel, result in zip(self._available_channels, raw_results):
            if isinstance(result, Exception):
                error_message = f"{type(result).__name__}: {result}"
                normalized_results.append({
                    "channel": channel.value,
                    "channel_name": self._senders[channel].name,
                    "success": False,
                    "attempts": self._notification_max_retries + 1,
                    "error": error_message,
                })
                logger.error("渠道 %s 推送任务异常结束: %s", channel.value, error_message)
                continue

            if isinstance(result, dict):
                normalized_results.append(result)
                continue

            normalized_results.append({
                "channel": channel.value,
                "channel_name": self._senders[channel].name,
                "success": bool(result),
                "attempts": 1,
                "error": "" if result else "sender returned False",
            })

        self._last_delivery_results = normalized_results
        self._last_delivery_summary = self._summarize_delivery_results(normalized_results)
        return any(item.get("success") for item in normalized_results)

    def _summarize_delivery_results(self, results: List[Dict[str, Any]]) -> str:
        if not results:
            return "没有可汇总的通知结果"

        succeeded: List[str] = []
        failed: List[str] = []
        for item in results:
            channel_name = item.get("channel_name") or item.get("channel") or "unknown"
            attempts = item.get("attempts", 0)
            if item.get("success"):
                succeeded.append(f"{channel_name}({attempts}次)")
                continue

            error = (item.get("error") or "未知原因").strip()
            failed.append(f"{channel_name}({attempts}次): {error}")

        parts: List[str] = []
        if succeeded:
            parts.append("成功[" + "；".join(succeeded) + "]")
        if failed:
            parts.append("失败[" + "；".join(failed) + "]")
        return "，".join(parts) if parts else "所有渠道均未返回有效结果"

    async def _send_channel_with_retry(self, channel: NotificationChannel, content: str, image_bytes: Optional[bytes] = None, **kwargs) -> Dict[str, Any]:
        attempts = 0
        last_error = ""
        sender = self._senders[channel]
        
        for attempt in range(self._notification_max_retries + 1):
            attempts = attempt + 1
            try:
                # 某些渠道即使启用了图片模式，也可能因为图片转换失败而回退到文本
                # 目前由 sender 内部处理，或在此处传递 image_bytes
                send_image = image_bytes if channel.value in self._markdown_to_image_channels else None
                
                success = await sender.send(content, image_bytes=send_image, **kwargs)
                
                if success:
                    return {
                        "channel": channel.value,
                        "channel_name": sender.name,
                        "success": True,
                        "attempts": attempts,
                        "error": "",
                    }
                
                last_error = "sender returned False"
                if attempt < self._notification_max_retries:
                    logger.warning(f"渠道 {channel.value} 推送失败，准备重试 ({attempt + 1}/{self._notification_max_retries})")
                    await asyncio.sleep(0.5 * (2**attempt))
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.error(f"渠道 {channel.value} 推送异常: {e}")
                if attempt >= self._notification_max_retries:
                    break
                await asyncio.sleep(0.5 * (2**attempt))
        return {
            "channel": channel.value,
            "channel_name": sender.name,
            "success": False,
            "attempts": attempts,
            "error": last_error or "unknown failure",
        }

    async def _send_single_channel(self, channel: NotificationChannel, content: str) -> bool:
        return await self._senders[channel].send(content)

    def save_report_to_file(self, content: str, filename: Optional[str] = None) -> str:
        from pathlib import Path
        config = get_config()
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        folder_str = now.strftime("%Y-%m-%d")
        
        if filename is None:
            filename = f"report_{date_str}.md"
            
        # 1. 确定基础报告目录
        base_dir = Path(config.report_dir)
        if not base_dir.is_absolute():
            base_dir = Path(__file__).resolve().parents[2] / base_dir
            
        # 2. 创建日期子目录
        reports_dir = base_dir / folder_str
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        filepath = reports_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return str(filepath)

def get_notification_service() -> NotificationService:
    return NotificationService()
