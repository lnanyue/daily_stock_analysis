# -*- coding: utf-8 -*-
"""
自定义 Webhook 发送提醒服务

职责：
1. 发送自定义 Webhook 消息
"""
import logging
import json
import asyncio
from typing import Optional, List, Dict, Any

from src.config import Config
from src.formatters import chunk_content_by_max_bytes, slice_at_max_bytes
from src.notification_constants import NOTIFICATION_DEFAULT_TIMEOUT_SEC
from .async_base import get_sender_http_client


logger = logging.getLogger(__name__)


from .base import BaseNotificationSender

class CustomWebhookSender(BaseNotificationSender):

    def __init__(self, config: Config):
        """
        初始化自定义 Webhook 配置

        Args:
            config: 配置对象
        """
        # 必须在 super().__init__() 之前初始化，因为 _check_enabled 会访问
        self._custom_webhook_urls = getattr(config, 'custom_webhook_urls', []) or []
        self._custom_webhook_bearer_token = getattr(config, 'custom_webhook_bearer_token', None)
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)
        self._timeout = getattr(config, 'notification_timeout_sec', NOTIFICATION_DEFAULT_TIMEOUT_SEC)
        super().__init__(config)

    def _check_enabled(self) -> bool:
        return bool(self._custom_webhook_urls)

    @property
    def name(self) -> str:
        return "自定义Webhook"

    async def send(self, content: str, image_bytes: Optional[bytes] = None, **kwargs) -> bool:
        """统一发送接口"""
        if not self.enabled:
            return False
            
        if image_bytes:
            return await self._send_custom_webhook_image(image_bytes, fallback_content=content)
            
        return await self.send_to_custom(content)

    async def send_to_custom(self, content: str) -> bool:
        """
        推送消息到自定义 Webhook
        
        支持任意接受 POST JSON 的 Webhook 端点
        默认发送格式：{"text": "消息内容", "content": "消息内容"}
        
        适用于：
        - 钉钉机器人
        - Slack Incoming Webhook
        - 自建通知服务
        - 其他支持 POST JSON 的服务
        
        Args:
            content: 消息内容（Markdown 格式）
            
        Returns:
            是否至少有一个 Webhook 发送成功
        """
        if not self._custom_webhook_urls:
            logger.warning("未配置自定义 Webhook，跳过推送")
            return False
        
        success_count = 0
        
        for i, url in enumerate(self._custom_webhook_urls):
            try:
                # 通用 JSON 格式，兼容大多数 Webhook
                # 钉钉格式: {"msgtype": "text", "text": {"content": "xxx"}}
                # Slack 格式: {"text": "xxx"}
                
                # 钉钉机器人对 body 有字节上限（约 20000 bytes），超长需要分批发送
                if self._is_dingtalk_webhook(url):
                    if await self._send_dingtalk_chunked(url, content, max_bytes=20000):
                        logger.info("自定义 Webhook %s（钉钉）推送成功", i+1)
                        success_count += 1
                    else:
                        logger.error("自定义 Webhook %s（钉钉）推送失败", i+1)
                    continue

                # 其他 Webhook：单次发送
                payload = self._build_custom_webhook_payload(url, content)
                if await self._post_custom_webhook(url, payload):
                    logger.info("自定义 Webhook %s 推送成功", i+1)
                    success_count += 1
                else:
                    logger.error("自定义 Webhook %s 推送失败", i+1)
                    
            except Exception:
                logger.exception("自定义 Webhook %s 推送异常", i+1)
        
        logger.info("自定义 Webhook 推送完成：成功 %s/%s", success_count, len(self._custom_webhook_urls))
        return success_count > 0

    
    async def _send_custom_webhook_image(
        self, image_bytes: bytes, fallback_content: str = ""
    ) -> bool:
        """Send image to Custom Webhooks."""
        if not self._custom_webhook_urls:
            return False
        success_count = 0
        client = await get_sender_http_client()
        for i, url in enumerate(self._custom_webhook_urls):
            try:
                if fallback_content:
                    payload = self._build_custom_webhook_payload(url, fallback_content)
                    if await self._post_custom_webhook(url, payload):
                        logger.info(
                            "自定义 Webhook %d（图片不支持，回退文本）推送成功", i + 1
                        )
                        success_count += 1
                else:
                    logger.warning(
                        "自定义 Webhook %d 不支持图片，且无回退内容，跳过", i + 1
                    )
            except Exception:
                logger.exception("自定义 Webhook %d 图片推送异常", i + 1)
        return success_count > 0

    async def _post_custom_webhook(self, url: str, payload: dict, timeout: int | None = None) -> bool:
        headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'User-Agent': 'StockAnalysis/1.0',
        }
        # 支持 Bearer Token 认证（#51）
        if self._custom_webhook_bearer_token:
            headers['Authorization'] = f'Bearer {self._custom_webhook_bearer_token}'
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        
        client = await get_sender_http_client()
        response = await client.post(url, content=body, headers=headers)
        if response.status_code == 200:
            return True
        logger.error("自定义 Webhook 推送失败: HTTP %s", response.status_code)
        logger.debug("响应内容: %s", response.text[:200])
        return False
    
    def _build_custom_webhook_payload(self, url: str, content: str) -> dict:
        """
        根据 URL 构建对应的 Webhook payload
        
        自动识别常见服务并使用对应格式
        """
        url_lower = url.lower()
        
        # 钉钉机器人
        if 'dingtalk' in url_lower or 'oapi.dingtalk.com' in url_lower:
            return {
                "msgtype": "markdown",
                "markdown": {
                    "title": "股票分析报告",
                    "text": content
                }
            }
        
        # Slack Incoming Webhook
        if 'hooks.slack.com' in url_lower:
            return {
                "text": content,
                "mrkdwn": True
            }
        
        # Bark (iOS 推送)
        if 'api.day.app' in url_lower:
            return {
                "title": "股票分析报告",
                "body": content[:4000],  # Bark 限制
                "group": "stock"
            }
        
        # 通用格式（兼容大多数服务）
        return {
            "text": content,
            "content": content,
            "message": content,
            "body": content
        }
    
    async def _send_dingtalk_chunked(self, url: str, content: str, max_bytes: int = 20000) -> bool:
        # 为 payload 开销预留空间，避免 body 超限
        budget = max(1000, max_bytes - 1500)
        chunks = chunk_content_by_max_bytes(content, budget)
        if not chunks:
            return False

        total = len(chunks)
        ok = 0

        for idx, chunk in enumerate(chunks):
            marker = f"\n\n📄 *({idx+1}/{total})*" if total > 1 else ""
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "股票分析报告",
                    "text": chunk + marker,
                },
            }

            # 如果仍超限（极端情况下），再按字节硬截断一次
            body_bytes = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            if body_bytes > max_bytes:
                hard_budget = max(200, budget - (body_bytes - max_bytes) - 200)
                payload["markdown"]["text"], _ = slice_at_max_bytes(payload["markdown"]["text"], hard_budget)

            if await self._post_custom_webhook(url, payload):
                ok += 1
            else:
                logger.error("钉钉分批发送失败: 第 %s/%s 批", idx+1, total)

            if idx < total - 1:
                await asyncio.sleep(1)

    
    @staticmethod
    def _is_dingtalk_webhook(url: str) -> bool:
        url_lower = (url or "").lower()
        return 'dingtalk' in url_lower or 'oapi.dingtalk.com' in url_lower
