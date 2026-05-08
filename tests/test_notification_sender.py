# -*- coding: utf-8 -*-
"""
Unit tests for src.notification_sender module.

Tests sender classes in isolation (config, request shape, error handling).
Does not duplicate test_notification.py which tests NotificationService.send() flow.
"""
import asyncio
import os
import sys
import time
import unittest
from email.header import decode_header, make_header
from email.utils import parseaddr
from unittest import mock
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import Config
from src.notification_sender import (
    CustomWebhookSender,
    EmailSender,
    PushoverSender,
    PushplusSender,
    Serverchan3Sender,
    WechatSender,
    WECHAT_IMAGE_MAX_BYTES,
)


def _config(**overrides):
    """Minimal Config for sender tests."""
    return Config(stock_list=[], **overrides)


def _mock_http_response(status_code=200, json_body=None):
    """Create a mock httpx.Response-like object."""
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.text = "ok" if status_code == 200 else "error"
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


def _mock_async_client(response):
    """Create a mock httpx.AsyncClient whose .post returns the given response."""
    client = mock.AsyncMock()
    client.post = mock.AsyncMock(return_value=response)
    return client


class TestWechatSender(unittest.IsolatedAsyncioTestCase):
    """Unit tests for WechatSender."""

    async def test_send_returns_false_when_no_webhook_url(self):
        cfg = _config()
        sender = WechatSender(cfg)
        result = await sender.send_to_wechat("hello")
        self.assertFalse(result)

    async def test_send_success_returns_true(self):
        resp = _mock_http_response(200, {"errcode": 0})
        mock_client = _mock_async_client(resp)
        with mock.patch(
            "src.notification_sender.async_base.get_sender_http_client",
            return_value=mock_client,
        ):
            cfg = _config(wechat_webhook_url="https://wechat.example/hook")
            sender = WechatSender(cfg)
            result = await sender.send_to_wechat("hello")
        self.assertTrue(result)

    def test_gen_wechat_payload_markdown(self):
        cfg = _config(wechat_webhook_url="u", wechat_msg_type="markdown")
        sender = WechatSender(cfg)
        payload = sender._gen_wechat_payload("## title\nbody")
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertEqual(payload["markdown"]["content"], "## title\nbody")

    def test_gen_wechat_payload_text(self):
        cfg = _config(wechat_webhook_url="u", wechat_msg_type="text")
        sender = WechatSender(cfg)
        payload = sender._gen_wechat_payload("plain")
        self.assertEqual(payload["msgtype"], "text")
        self.assertEqual(payload["text"]["content"], "plain")

    async def test_send_wechat_image_over_limit_returns_false(self):
        cfg = _config(wechat_webhook_url="https://wechat.example/hook")
        sender = WechatSender(cfg)
        big = b"x" * (WECHAT_IMAGE_MAX_BYTES + 1)
        with mock.patch.object(sender, "_compress_image", return_value=None):
            result = await sender._send_wechat_image(big)
        self.assertFalse(result)


class TestEmailSender(unittest.IsolatedAsyncioTestCase):
    """Unit tests for EmailSender (config and receiver logic; send path covered via service)."""

    async def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = EmailSender(cfg)
        result = await sender.send_to_email("body")
        self.assertFalse(result)

    @mock.patch("smtplib.SMTP_SSL")
    def test_send_to_email_encodes_non_ascii_sender_name(self, mock_smtp_ssl):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com"],
            email_sender_name="daily_stock_analysis股票分析助手",
        )
        sender = EmailSender(cfg)

        result = asyncio.run(sender.send_to_email("body", subject="测试主题"))

        self.assertTrue(result)
        server = mock_smtp_ssl.return_value
        server.send_message.assert_called_once()
        msg = server.send_message.call_args[0][0]
        realname, addr = parseaddr(msg["From"])
        self.assertEqual(addr, "a@qq.com")
        self.assertEqual(
            str(make_header(decode_header(realname))),
            "daily_stock_analysis股票分析助手",
        )
        server.quit.assert_called_once()


class TestEmailSenderAsync(unittest.IsolatedAsyncioTestCase):
    async def test_send_to_email_times_out_without_using_default_executor(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com"],
        )
        sender = EmailSender(cfg)
        sender._timeout = 0.05

        def _slow_send(*args, **kwargs):
            time.sleep(0.2)
            return True

        start = time.perf_counter()
        with mock.patch.object(sender, "_send_to_email_sync", side_effect=_slow_send):
            result = await sender.send_to_email("body")
        elapsed = time.perf_counter() - start

        self.assertFalse(result)
        self.assertLess(elapsed, 0.4)

    @mock.patch("smtplib.SMTP_SSL")
    def test_send_image_email_encodes_non_ascii_sender_name(self, mock_smtp_ssl):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com"],
            email_sender_name="daily_stock_analysis股票分析助手",
        )
        sender = EmailSender(cfg)

        result = asyncio.run(
            sender._send_email_with_inline_image(b"PNG_BYTES", receivers=["b@qq.com"])
        )

        self.assertTrue(result)
        server = mock_smtp_ssl.return_value
        server.send_message.assert_called_once()
        msg = server.send_message.call_args[0][0]
        realname, addr = parseaddr(msg["From"])
        self.assertEqual(addr, "a@qq.com")
        self.assertEqual(
            str(make_header(decode_header(realname))),
            "daily_stock_analysis股票分析助手",
        )
        server.quit.assert_called_once()


class TestCustomWebhookSender(unittest.IsolatedAsyncioTestCase):
    """Unit tests for CustomWebhookSender."""

    async def test_send_returns_false_when_no_urls(self):
        cfg = _config()
        sender = CustomWebhookSender(cfg)
        result = await sender.send_to_custom("hello")
        self.assertFalse(result)

    async def test_send_success_payload_has_text_and_content(self):
        resp = _mock_http_response(200)
        mock_client = _mock_async_client(resp)
        with mock.patch(
            "src.notification_sender.custom_webhook_sender.get_sender_http_client",
            return_value=mock_client,
        ):
            cfg = _config(custom_webhook_urls=["https://example.com/webhook"])
            sender = CustomWebhookSender(cfg)
            result = await sender.send_to_custom("hello")
        self.assertTrue(result)
        body = mock_client.post.call_args[1]["content"].decode("utf-8")
        self.assertIn("hello", body)

    # ---- Dingtalk chunked webhook tests ----

    async def test_dingtalk_chunked_all_success_returns_true(self):
        """_send_dingtalk_chunked returns True when all chunks succeed."""
        cfg = _config(custom_webhook_urls=["https://oapi.dingtalk.com/robot/send"])
        sender = CustomWebhookSender(cfg)

        # Force chunking by using a tiny budget
        content = "A" * 3000
        with mock.patch.object(sender, "_post_custom_webhook", return_value=True):
            result = await sender._send_dingtalk_chunked(
                "https://oapi.dingtalk.com/robot/send",
                content,
                max_bytes=500,
            )
        self.assertTrue(result)

    async def test_dingtalk_chunked_partial_failure_returns_false(self):
        """_send_dingtalk_chunked returns False when any chunk fails."""
        cfg = _config(custom_webhook_urls=["https://oapi.dingtalk.com/robot/send"])
        sender = CustomWebhookSender(cfg)

        call_count = 0

        async def _mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return call_count == 1  # first succeeds, second fails

        content = "A" * 3000
        with mock.patch.object(sender, "_post_custom_webhook", side_effect=_mock_post):
            result = await sender._send_dingtalk_chunked(
                "https://oapi.dingtalk.com/robot/send",
                content,
                max_bytes=500,
            )
        self.assertFalse(result)


class TestPushoverSender(unittest.IsolatedAsyncioTestCase):
    """Unit tests for PushoverSender."""

    async def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = PushoverSender(cfg)
        result = await sender.send_to_pushover("hello")
        self.assertFalse(result)

    async def test_send_success_returns_true(self):
        resp = _mock_http_response(200, {"status": 1})
        mock_client = _mock_async_client(resp)
        with mock.patch(
            "src.notification_sender.pushover_sender.get_sender_http_client",
            return_value=mock_client,
        ):
            cfg = _config(pushover_user_key="U", pushover_api_token="T")
            sender = PushoverSender(cfg)
            result = await sender.send_to_pushover("hello")
        self.assertTrue(result)
        call_data = mock_client.post.call_args[1]["data"]
        self.assertEqual(call_data["user"], "U")
        self.assertEqual(call_data["token"], "T")


class TestPushplusSender(unittest.IsolatedAsyncioTestCase):
    """Unit tests for PushplusSender."""

    async def test_send_returns_false_when_no_token(self):
        cfg = _config()
        sender = PushplusSender(cfg)
        result = await sender.send_to_pushplus("hello")
        self.assertFalse(result)

    async def test_send_success_returns_true(self):
        resp = _mock_http_response(200, {"code": 200})
        mock_client = _mock_async_client(resp)
        with mock.patch(
            "src.notification_sender.pushplus_sender.get_sender_http_client",
            return_value=mock_client,
        ):
            cfg = _config(pushplus_token="TOKEN")
            sender = PushplusSender(cfg)
            result = await sender.send_to_pushplus("hello")
        self.assertTrue(result)

    async def test_send_long_message_chunks_pushplus_requests(self):
        resp = _mock_http_response(200, {"code": 200})
        mock_client = _mock_async_client(resp)
        with mock.patch(
            "src.notification_sender.pushplus_sender.get_sender_http_client",
            return_value=mock_client,
        ):
            cfg = _config(pushplus_token="TOKEN")
            sender = PushplusSender(cfg)
            result = await sender.send_to_pushplus("A" * 25000)
        self.assertTrue(result)
        self.assertGreaterEqual(mock_client.post.call_count, 2)


class TestServerchan3Sender(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Serverchan3Sender."""

    async def test_send_returns_false_when_no_sendkey(self):
        cfg = _config()
        sender = Serverchan3Sender(cfg)
        result = await sender.send_to_serverchan3("hello")
        self.assertFalse(result)

    async def test_send_success_returns_true(self):
        resp = _mock_http_response(200, {"code": 0})
        mock_client = _mock_async_client(resp)
        with mock.patch(
            "src.notification_sender.async_base.get_sender_http_client",
            return_value=mock_client,
        ):
            cfg = _config(serverchan3_sendkey="SCT123")
            sender = Serverchan3Sender(cfg)
            result = await sender.send_to_serverchan3("hello")
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
