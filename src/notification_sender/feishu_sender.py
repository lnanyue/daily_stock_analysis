# -*- coding: utf-8 -*-
"""
飞书 发送提醒服务

职责：
1. 通过 webhook 发送飞书消息
"""
import logging
import asyncio
from typing import Dict, Any

from src.config import Config
from src.formatters import format_feishu_markdown, chunk_content_by_max_bytes
from src.notification_constants import NOTIFICATION_DEFAULT_TIMEOUT_SEC


logger = logging.getLogger(__name__)


class FeishuSender:

    def __init__(self, config: Config):
        self._feishu_url = getattr(config, 'feishu_webhook_url', None)
        self._feishu_max_bytes = getattr(config, 'feishu_max_bytes', 20000)
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)
        self._timeout = getattr(config, 'notification_timeout_sec', NOTIFICATION_DEFAULT_TIMEOUT_SEC)

    async def send_to_feishu(self, content: str) -> bool:
        """推送消息到飞书机器人"""
        if not self._feishu_url:
            logger.warning("飞书 Webhook 未配置，跳过推送")
            return False

        formatted_content = format_feishu_markdown(content)
        max_bytes = self._feishu_max_bytes

        content_bytes = len(formatted_content.encode('utf-8'))
        if content_bytes > max_bytes:
            logger.info(f"飞书消息内容超长({content_bytes}字节/{len(content)}字符)，将分批发送")
            return await self._send_feishu_chunked(formatted_content, max_bytes)

        return await self._send_feishu_message(formatted_content)

    async def _send_feishu_chunked(self, content: str, max_bytes: int) -> bool:
        """分批发送长消息到飞书"""
        chunks = chunk_content_by_max_bytes(content, max_bytes, add_page_marker=True)
        total_chunks = len(chunks)
        success_count = 0

        logger.info(f"飞书分批发送：共 {total_chunks} 批")

        for i, chunk in enumerate(chunks):
            try:
                if await self._send_feishu_message(chunk):
                    success_count += 1
                    logger.info(f"飞书第 {i+1}/{total_chunks} 批发送成功")
                else:
                    logger.error(f"飞书第 {i+1}/{total_chunks} 批发送失败")
            except Exception:
                logger.exception(f"飞书第 {i+1}/{total_chunks} 批发送异常")

            if i < total_chunks - 1:
                await asyncio.sleep(1)

        return success_count == total_chunks

    async def _send_feishu_message(self, content: str) -> bool:
        """发送单条飞书消息（优先使用 Markdown 卡片）"""
        from .async_base import get_sender_http_client

        async def _post_payload(payload: Dict[str, Any]) -> bool:
            client = await get_sender_http_client()
            response = await client.post(self._feishu_url, json=payload)

            if response.status_code == 200:
                result = response.json()
                code = result.get('code') if 'code' in result else result.get('StatusCode')
                if code == 0:
                    logger.info("飞书消息发送成功")
                    return True
                error_msg = result.get('msg') or result.get('StatusMessage', '未知错误')
                logger.error(f"飞书返回错误 [code={code}]: {error_msg}")
                return False
            logger.error(f"飞书请求失败: HTTP {response.status_code}")
            return False

        # 1) 优先使用交互卡片
        card_payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": "A股智能分析报告"}},
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}]
            }
        }
        if await _post_payload(card_payload):
            return True

        # 2) 回退为普通文本
        text_payload = {"msg_type": "text", "content": {"text": content}}
        return await _post_payload(text_payload)
