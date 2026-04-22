# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 通知服务单元测试
===================================
"""
import os
import sys
import unittest
from unittest import mock
from typing import Optional
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep this test runnable when optional LLM/runtime deps are not installed.
for optional_module in ("litellm", "json_repair"):
    try:
        __import__(optional_module)
    except ModuleNotFoundError:
        sys.modules[optional_module] = mock.MagicMock()

from src.config import Config
from src.notification import NotificationService, NotificationChannel
from src.analyzer import AnalysisResult
import httpx


def _make_config(**overrides) -> Config:
    """Create a Config instance overriding only notification-related fields."""
    # Filter overrides to only include fields present in Config dataclass
    from dataclasses import fields
    valid_fields = {f.name for f in fields(Config)}
    filtered_overrides = {k: v for k, v in overrides.items() if k in valid_fields}
    return Config(stock_list=[], **filtered_overrides)


def _make_response(status_code: int, json: Optional[dict] = None) -> httpx.Response:
    return httpx.Response(status_code, json=json)


class TestNotificationServiceSendToMethods(unittest.IsolatedAsyncioTestCase):
    """测试通知发送服务（异步版）"""

    @mock.patch("src.notification.service.get_config")
    async def test_no_channels_service_unavailable_and_send_returns_false(self, mock_get_config):
        mock_get_config.return_value = _make_config()
        service = NotificationService()
        self.assertFalse(service.is_available())
        result = await service.send("test content")
        self.assertFalse(result)

    @mock.patch("src.notification.service.get_config")
    @mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock)
    async def test_send_to_astrbot_via_notification_service(self, mock_post, mock_get_config):
        cfg = _make_config(astrbot_url="https://astrbot.example")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.ASTRBOT, service.get_available_channels())

        ok = await service.send("astrbot content")
        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.service.get_config")
    @mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock)
    async def test_send_to_custom_webhook_via_notification_service(self, mock_post, mock_get_config):
        cfg = _make_config(custom_webhook_urls=["https://custom.example"])
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.CUSTOM, service.get_available_channels())

        ok = await service.send("custom content")
        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.service.get_config")
    @mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock)
    async def test_send_to_discord_via_notification_service_with_webhook(self, mock_post, mock_get_config):
        cfg = _make_config(discord_webhook_url="https://discord.webhook")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(204)

        service = NotificationService()
        self.assertIn(NotificationChannel.DISCORD, service.get_available_channels())

        ok = await service.send("discord webhook content")
        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.service.get_config")
    @mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock)
    async def test_send_to_discord_via_notification_service_with_bot(self, mock_post, mock_get_config):
        cfg = _make_config(discord_bot_token="TOKEN", discord_main_channel_id="CHANNEL")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.DISCORD, service.get_available_channels())

        ok = await service.send("discord bot content")
        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.service.get_config")
    @mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock)
    async def test_send_to_discord_via_notification_service_with_bot_requires_chunking(self, mock_post, mock_get_config):
        cfg = _make_config(discord_bot_token="TOKEN", discord_main_channel_id="CHANNEL", discord_max_words=10)
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        long_content = "word " * 50
        ok = await service.send(long_content)
        self.assertTrue(ok)
        self.assertGreater(mock_post.call_count, 1)


class TestNotificationServiceReportGeneration(unittest.IsolatedAsyncioTestCase):
    """测试报告生成与发送逻辑"""

    def setUp(self):
        self.test_results = [
            AnalysisResult(
                code='600519', name='Moutai', sentiment_score=80,
                operation_advice='buy', trend_prediction='bullish', analysis_summary='Good'
            )
        ]

    def test_generate_aggregate_report_routes_by_report_type(self):
        from src.enums import ReportType
        service = NotificationService()
        
        with mock.patch.object(service, "generate_brief_report", return_value="brief") as m_brief:
            res = service.generate_aggregate_report(self.test_results, ReportType.BRIEF)
            self.assertEqual(res, "brief")
            m_brief.assert_called_once()

        with mock.patch.object(service, "generate_dashboard_report", return_value="full") as m_full:
            res = service.generate_aggregate_report(self.test_results, ReportType.FULL)
            self.assertEqual(res, "full")
            m_full.assert_called_once()

    @mock.patch("src.notification.service.get_config")
    @mock.patch("smtplib.SMTP_SSL")
    async def test_send_to_email_via_notification_service(self, mock_smtp_ssl, mock_get_config):
        cfg = _make_config(email_sender="test@example.com", email_password="pass", email_receivers=["r@ex.com"])
        mock_get_config.return_value = cfg
        
        service = NotificationService()
        ok = await service.send("email content")
        self.assertTrue(ok)
        mock_smtp_ssl.assert_called_once()

    @mock.patch("src.notification.service.get_config")
    @mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock)
    async def test_send_to_feishu_via_notification_service(self, mock_post, mock_get_config):
        cfg = _make_config(feishu_webhook_url="https://feishu.ex")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"code": 0})

        service = NotificationService()
        ok = await service.send("feishu content")
        self.assertTrue(ok)
        mock_post.assert_called()

    @mock.patch("src.notification.service.get_config")
    @mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock)
    async def test_send_to_telegram_via_notification_service(self, mock_post, mock_get_config):
        cfg = _make_config(telegram_bot_token="T", telegram_chat_id="C")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"ok": True})

        service = NotificationService()
        ok = await service.send("hello telegram")
        self.assertTrue(ok)
        mock_post.assert_called()

    @mock.patch("src.notification.service.get_config")
    @mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock)
    async def test_send_to_wechat_via_notification_service(self, mock_post, mock_get_config):
        cfg = _make_config(wechat_webhook_url="https://wechat.ex")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"errcode": 0})

        service = NotificationService()
        ok = await service.send("hello wechat")
        self.assertTrue(ok)
        mock_post.assert_called()

if __name__ == "__main__":
    unittest.main()
