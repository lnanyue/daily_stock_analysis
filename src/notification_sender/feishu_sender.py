# -*- coding: utf-8 -*-
"""
飞书 发送提醒服务

职责：
1. 通过 webhook 发送飞书消息
"""
import base64
import hashlib
import hmac
import logging
import asyncio
import time
from typing import Dict, Any, Optional, List

from src.config import Config
from src.formatters import (
    MIN_MAX_BYTES,
    PAGE_MARKER_SAFE_BYTES,
    chunk_content_by_max_bytes,
    format_feishu_markdown,
)
from src.notification_constants import NOTIFICATION_DEFAULT_TIMEOUT_SEC

logger = logging.getLogger(__name__)


from .base import BaseNotificationSender

class FeishuSender(BaseNotificationSender):

    def __init__(self, config: Config):
        super().__init__(config)
        self._feishu_url = getattr(config, 'feishu_webhook_url', None)
        self._feishu_secret = (getattr(config, 'feishu_webhook_secret', None) or '').strip()
        self._feishu_keyword = (getattr(config, 'feishu_webhook_keyword', None) or '').strip()
        self._feishu_max_bytes = getattr(config, 'feishu_max_bytes', 20000)
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)
        self._timeout = getattr(config, 'notification_timeout_sec', NOTIFICATION_DEFAULT_TIMEOUT_SEC)

    def _check_enabled(self) -> bool:
        return bool(self.config.feishu_webhook_url)

    def _get_keyword_prefix(self) -> str:
        if not self._feishu_keyword:
            return ""
        return f"{self._feishu_keyword}\n"

    def _apply_keyword_prefix(self, content: str) -> str:
        prefix = self._get_keyword_prefix()
        if not prefix:
            return content
        return f"{prefix}{content}" if content else self._feishu_keyword

    def _build_security_fields(self) -> Dict[str, str]:
        if not self._feishu_secret:
            return {}
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self._feishu_secret}"
        sign = base64.b64encode(
            hmac.new(
                string_to_sign.encode('utf-8'),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode('utf-8')
        return {"timestamp": timestamp, "sign": sign}

    @property
    def name(self) -> str:
        return "飞书"

    async def send(self, content: str, image_bytes: Optional[bytes] = None, **kwargs) -> bool:
        """统一发送接口"""
        if not self.enabled:
            return False
        
        # 飞书目前主要支持 Markdown，图片暂未统一封装到 send 接口
        return await self.send_to_feishu(content)

    async def send_to_feishu(self, content: str) -> bool:
        if not self._feishu_url:
            logger.warning("飞书 Webhook 未配置，跳过推送")
            return False

        formatted_content = format_feishu_markdown(content)
        max_bytes = self._feishu_max_bytes
        keyword_overhead = len(self._get_keyword_prefix().encode('utf-8'))
        effective_max_bytes = max_bytes - keyword_overhead

        if effective_max_bytes <= 0:
            logger.error("飞书关键词过长，超过单条消息允许的最大字节数，无法发送")
            return False

        content_bytes = len(formatted_content.encode('utf-8')) + keyword_overhead
        if content_bytes > max_bytes:
            min_chunk_bytes = MIN_MAX_BYTES + PAGE_MARKER_SAFE_BYTES
            if effective_max_bytes < min_chunk_bytes:
                logger.error(
                    "飞书关键词过长，剩余分片预算(%s字节)不足以安全分页发送，至少需要 %s 字节",
                    effective_max_bytes,
                    min_chunk_bytes,
                )
                return False
            logger.info("飞书消息内容超长(%s字节/%s字符)，将分批发送", content_bytes, len(content))
            return await self._send_feishu_chunked(formatted_content, effective_max_bytes)

        return await self._send_feishu_message(formatted_content, effective_max_bytes)

    async def _send_feishu_chunked(self, content: str, max_bytes: int) -> bool:
        from .async_base import send_chunked
        try:
            chunks = chunk_content_by_max_bytes(content, max_bytes, add_page_marker=True)
        except ValueError as e:
            logger.error("飞书消息分片失败: %s", e)
            return False
        return await send_chunked(chunks, "飞书",
            lambda i, c: self._send_feishu_message(c, max_bytes))

    async def _send_feishu_message(self, content: str, _max_bytes: int = 0) -> bool:
        from .async_base import get_sender_http_client

        prepared_content = self._apply_keyword_prefix(content)
        security_fields = self._build_security_fields()

        async def _post_payload(payload: Dict[str, Any]) -> bool:
            request_payload = dict(payload)
            request_payload.update(security_fields)
            client = await get_sender_http_client()
            response = await client.post(
                self._feishu_url,
                json=request_payload,
                timeout=30,
            )
            if response.status_code == 200:
                result = response.json()
                code = result.get('code') if 'code' in result else result.get('StatusCode')
                if code == 0:
                    logger.info("飞书消息发送成功")
                    return True
                error_msg = result.get('msg') or result.get('StatusMessage', '未知错误')
                logger.error("飞书返回错误 [code=%s]: %s", code, error_msg)
                return False
            logger.error("飞书请求失败: HTTP %s", response.status_code)
            return False

        card_payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "股票智能分析报告"}
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": prepared_content}
                    }
                ]
            }
        }
        if await _post_payload(card_payload):
            return True

        text_payload = {"msg_type": "text", "content": {"text": content}}
        return await _post_payload(text_payload)
