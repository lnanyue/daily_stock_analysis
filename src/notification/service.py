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
    AstrbotSender,
    CustomWebhookSender,
    DiscordSender,
    EmailSender,
    FeishuSender,
    PushoverSender,
    PushplusSender,
    Serverchan3Sender,
    SlackSender,
    TelegramSender,
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
    TELEGRAM = "telegram"
    EMAIL = "email"
    PUSHOVER = "pushover"
    PUSHPLUS = "pushplus"
    SERVERCHAN3 = "serverchan3"
    CUSTOM = "custom"
    DISCORD = "discord"
    SLACK = "slack"
    ASTRBOT = "astrbot"
    UNKNOWN = "unknown"


class ChannelDetector:
    """渠道检测器"""
    @staticmethod
    def get_channel_name(channel: NotificationChannel) -> str:
        names = {
            NotificationChannel.WECHAT: "企业微信",
            NotificationChannel.FEISHU: "飞书",
            NotificationChannel.TELEGRAM: "Telegram",
            NotificationChannel.EMAIL: "邮件",
            NotificationChannel.PUSHOVER: "Pushover",
            NotificationChannel.PUSHPLUS: "PushPlus",
            NotificationChannel.SERVERCHAN3: "Server酱3",
            NotificationChannel.CUSTOM: "自定义Webhook",
            NotificationChannel.DISCORD: "Discord机器人",
            NotificationChannel.SLACK: "Slack",
            NotificationChannel.ASTRBOT: "ASTRBOT机器人",
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
        self._wechat = WechatSender(config)
        self._feishu = FeishuSender(config)
        self._telegram = TelegramSender(config)
        self._email = EmailSender(config)
        self._pushover = PushoverSender(config)
        self._pushplus = PushplusSender(config)
        self._serverchan3 = Serverchan3Sender(config)
        self._custom = CustomWebhookSender(config)
        self._discord = DiscordSender(config)
        self._slack = SlackSender(config)
        self._astrbot = AstrbotSender(config)

        self._available_channels = self._detect_all_channels()

    def _detect_all_channels(self) -> List[NotificationChannel]:
        """检测已配置的渠道"""
        config = get_config()
        channels = []
        if config.wechat_webhook_url: channels.append(NotificationChannel.WECHAT)
        if config.feishu_webhook_url: channels.append(NotificationChannel.FEISHU)
        if config.telegram_bot_token and config.telegram_chat_id: channels.append(NotificationChannel.TELEGRAM)
        if config.email_sender and config.email_password: channels.append(NotificationChannel.EMAIL)
        if config.pushover_user_key and config.pushover_api_token: channels.append(NotificationChannel.PUSHOVER)
        if config.pushplus_token: channels.append(NotificationChannel.PUSHPLUS)
        if config.serverchan3_sendkey: channels.append(NotificationChannel.SERVERCHAN3)
        if config.custom_webhook_urls: channels.append(NotificationChannel.CUSTOM)
        if config.discord_webhook_url or (config.discord_bot_token and config.discord_main_channel_id):
            channels.append(NotificationChannel.DISCORD)
        if config.slack_webhook_url or (config.slack_bot_token and config.slack_channel_id): channels.append(NotificationChannel.SLACK)
        if config.astrbot_url: channels.append(NotificationChannel.ASTRBOT)
        return channels

    def get_channel_names(self) -> List[str]:
        return [ChannelDetector.get_channel_name(ch) for ch in self._available_channels]

    def is_available(self) -> bool:
        return bool(self._available_channels)

    def get_available_channels(self) -> List[NotificationChannel]:
        """获取当前已配置的可用渠道"""
        return self._available_channels

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
    # Forwarding methods to Senders
    # ------------------------------------------------------------------
    async def send_to_wechat(self, content: str) -> bool: return await self._wechat.send_to_wechat(content)
    async def send_to_feishu(self, content: str) -> bool: return await self._feishu.send_to_feishu(content)
    async def send_to_telegram(self, content: str) -> bool: return await self._telegram.send_to_telegram(content)
    async def send_to_email(self, content: str, receivers=None) -> bool: return await self._email.send_to_email(content, receivers=receivers)
    async def send_to_pushover(self, content: str) -> bool: return await self._pushover.send_to_pushover(content)
    async def send_to_pushplus(self, content: str) -> bool: return await self._pushplus.send_to_pushplus(content)
    async def send_to_serverchan3(self, content: str) -> bool: return await self._serverchan3.send_to_serverchan3(content)
    async def send_to_custom(self, content: str) -> bool: return await self._custom.send_to_custom(content)
    async def send_to_discord(self, content: str) -> bool: return await self._discord.send_to_discord(content)
    async def send_to_slack(self, content: str) -> bool: return await self._slack.send_to_slack(content)
    async def send_to_astrbot(self, content: str) -> bool: return await self._astrbot.send_to_astrbot(content)

    async def send(self, content: str, email_stock_codes: Optional[List[str]] = None, email_send_to_all: bool = False) -> bool:
        if not self._available_channels: return False
        
        image_bytes = None
        # Simplification: only convert if at least one channel needs it
        if any(ch.value in self._markdown_to_image_channels for ch in self._available_channels):
            from src.md2img import markdown_to_image
            image_bytes = markdown_to_image(content, max_chars=self._markdown_to_image_max_chars)

        coros = []
        for channel in self._available_channels:
            coros.append(self._send_channel_with_retry(channel, content, image_bytes, email_stock_codes, email_send_to_all))
        
        results = await asyncio.gather(*coros, return_exceptions=True)
        return any(res is True for res in results)

    async def _send_channel_with_retry(self, channel, content, image_bytes, email_stock_codes, email_send_to_all) -> bool:
        for attempt in range(self._notification_max_retries + 1):
            try:
                success = False
                if channel == NotificationChannel.WECHAT:
                    if image_bytes:
                        success = await self._wechat._send_wechat_image(image_bytes)
                    else:
                        success = await self.send_to_wechat(content)
                elif channel == NotificationChannel.FEISHU:
                    success = await self.send_to_feishu(content)
                elif channel == NotificationChannel.TELEGRAM:
                    if image_bytes:
                        success = await self._telegram._send_telegram_photo(image_bytes)
                    else:
                        success = await self.send_to_telegram(content)
                elif channel == NotificationChannel.EMAIL:
                    receivers = None
                    if email_send_to_all:
                        receivers = self._email.get_all_email_receivers()
                    elif email_stock_codes:
                        receivers = self._email.get_receivers_for_stocks(email_stock_codes)
                    
                    if image_bytes:
                        success = await self._email._send_email_with_inline_image(image_bytes, receivers=receivers)
                    else:
                        success = await self.send_to_email(content, receivers=receivers)
                else:
                    success = await self._send_single_channel(channel, content)
                
                if success:
                    return True
                
                if attempt < self._notification_max_retries:
                    logger.warning(f"渠道 {channel.value} 推送失败，准备重试 ({attempt + 1}/{self._notification_max_retries})")
                    await asyncio.sleep(0.5 * (2**attempt))
            except Exception as e:
                logger.error(f"渠道 {channel.value} 推送异常: {e}")
                if attempt >= self._notification_max_retries:
                    return False
                await asyncio.sleep(0.5 * (2**attempt))
        return False

    async def _send_single_channel(self, channel, content) -> bool:
        # Implementation moved to specialized senders
        if channel == NotificationChannel.PUSHOVER: return await self.send_to_pushover(content)
        if channel == NotificationChannel.PUSHPLUS: return await self.send_to_pushplus(content)
        if channel == NotificationChannel.SERVERCHAN3: return await self.send_to_serverchan3(content)
        if channel == NotificationChannel.CUSTOM: return await self.send_to_custom(content)
        if channel == NotificationChannel.DISCORD: return await self.send_to_discord(content)
        if channel == NotificationChannel.SLACK: return await self.send_to_slack(content)
        if channel == NotificationChannel.ASTRBOT: return await self.send_to_astrbot(content)
        return False

    def save_report_to_file(self, content: str, filename: Optional[str] = None) -> str:
        from pathlib import Path
        if filename is None:
            filename = f"report_{datetime.now().strftime('%Y%m%d')}.md"
        reports_dir = Path(__file__).parent.parent.parent / 'report'
        reports_dir.mkdir(parents=True, exist_ok=True)
        filepath = reports_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return str(filepath)

def get_notification_service() -> NotificationService:
    return NotificationService()
