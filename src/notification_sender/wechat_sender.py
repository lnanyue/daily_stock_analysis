# -*- coding: utf-8 -*-
"""
Wechat 发送提醒服务

职责：
1. 通过企业微信 Webhook 发送文本消息
2. 通过企业微信 Webhook 发送图片消息
"""
import logging
import asyncio
import base64
import hashlib
from typing import Optional

from src.config import Config
from src.formatters import chunk_content_by_max_bytes
from src.notification_constants import NOTIFICATION_DEFAULT_TIMEOUT_SEC


logger = logging.getLogger(__name__)


# WeChat Work image msgtype limit ~2MB (base64 payload)
WECHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024

class WechatSender:

    def __init__(self, config: Config):
        """
        初始化企业微信配置

        Args:
            config: 配置对象
        """
        self._wechat_url = config.wechat_webhook_url
        self._wechat_max_bytes = getattr(config, 'wechat_max_bytes', 4000)
        self._wechat_msg_type = getattr(config, 'wechat_msg_type', 'markdown')
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)
        self._timeout = getattr(config, 'notification_timeout_sec', NOTIFICATION_DEFAULT_TIMEOUT_SEC)

    async def send_to_wechat(self, content: str) -> bool:
        """
        推送消息到企业微信机器人

        注意：企业微信 Markdown 限制 4096 字节（非字符）, Text 类型限制 2048 字节，超长内容也会自动分批发送
        可通过环境变量 WECHAT_MAX_BYTES 调整限制值

        Args:
            content: Markdown 格式的消息内容

        Returns:
            是否发送成功
        """
        if not self._wechat_url:
            logger.warning("企业微信 Webhook 未配置，跳过推送")
            return False

        # 根据消息类型动态限制上限，避免 text 类型超过企业微信 2048 字节限制
        if self._wechat_msg_type == 'text':
            max_bytes = min(self._wechat_max_bytes, 2000)  # 预留一定字节给系统/分页标记
        else:
            max_bytes = self._wechat_max_bytes  # markdown 默认 4000 字节

        # 检查字节长度，超长则分批发送
        content_bytes = len(content.encode('utf-8'))
        if content_bytes > max_bytes:
            logger.info(f"消息内容超长({content_bytes}字节/{len(content)}字符)，将分批发送")
            return await self._send_wechat_chunked(content, max_bytes)

        return await self._send_wechat_message(content)

    async def _send_wechat_image(self, image_bytes: bytes) -> bool:
        """Send image via WeChat Work webhook msgtype image (Issue #289).

        If the image exceeds the 2MB limit, attempt to compress it using PIL.
        """
        from .async_base import get_sender_http_client
        if not self._wechat_url:
            return False
        if len(image_bytes) > WECHAT_IMAGE_MAX_BYTES:
            image_bytes = self._compress_image(image_bytes)
            if image_bytes is None:
                logger.warning(
                    "企业微信图片超限 (%d > %d bytes)，且无法压缩，回退为文本",
                    len(image_bytes), WECHAT_IMAGE_MAX_BYTES,
                )
                return False
        b64 = base64.b64encode(image_bytes).decode("ascii")
        md5_hash = hashlib.md5(image_bytes).hexdigest()
        payload = {
            "msgtype": "image",
            "image": {"base64": b64, "md5": md5_hash},
        }
        client = await get_sender_http_client()
        response = await client.post(self._wechat_url, json=payload)
        if response.status_code == 200:
            result = response.json()
            if result.get("errcode") == 0:
                logger.info("企业微信图片发送成功")
                return True
            logger.error("企业微信图片发送失败: %s", result.get("errmsg", ""))
        else:
            logger.error("企业微信请求失败: HTTP %s", response.status_code)
        return False

    def _compress_image(self, image_bytes: bytes) -> Optional[bytes]:
        """尝试压缩图片至 2MB 以下（使用 PIL 调整质量）。"""
        try:
            from io import BytesIO
            from PIL import Image
        except ImportError:
            logger.debug("PIL 未安装，无法压缩图片，请安装: pip install Pillow")
            return None

        try:
            img = Image.open(BytesIO(image_bytes))
            max_dim = 1280
            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)

            quality = 85
            while quality >= 30:
                buf = BytesIO()
                fmt = "JPEG" if img.mode != "RGBA" else "PNG"
                save_kwargs = {"quality": quality} if fmt == "JPEG" else {}
                if fmt == "JPEG" and img.mode == "RGBA":
                    img = img.convert("RGB")
                img.save(buf, format=fmt, **save_kwargs)
                compressed = buf.getvalue()
                if len(compressed) <= WECHAT_IMAGE_MAX_BYTES:
                    logger.info(
                        "图片已压缩: %d -> %d bytes (quality=%d)",
                        len(image_bytes), len(compressed), quality,
                    )
                    return compressed
                quality -= 10

            logger.warning("图片压缩失败: 无法降至 %d bytes 以下", WECHAT_IMAGE_MAX_BYTES)
            return None
        except Exception as e:
            logger.warning("图片压缩失败: %s", e)
            return None

    async def _send_wechat_message(self, content: str) -> bool:
        """发送企业微信消息"""
        from .async_base import get_sender_http_client
        payload = self._gen_wechat_payload(content)

        client = await get_sender_http_client()
        response = await client.post(self._wechat_url, json=payload)

        if response.status_code == 200:
            result = response.json()
            if result.get('errcode') == 0:
                logger.info("企业微信消息发送成功")
                return True
            logger.error(f"企业微信返回错误: {result}")
            return False
        logger.error(f"企业微信请求失败: HTTP {response.status_code}")
        return False

    async def _send_wechat_chunked(self, content: str, max_bytes: int) -> bool:
        """
        分批发送长消息到企业微信

        按股票分析块（以 --- 或 ### 分隔）智能分割，确保每批不超过限制

        Args:
            content: 完整消息内容
            max_bytes: 单条消息最大字节数

        Returns:
            是否全部发送成功
        """
        chunks = chunk_content_by_max_bytes(content, max_bytes, add_page_marker=True)
        total_chunks = len(chunks)
        success_count = 0
        for i, chunk in enumerate(chunks):
            if await self._send_wechat_message(chunk):
                success_count += 1
            else:
                logger.error(f"企业微信第 {i+1}/{total_chunks} 批发送失败")
            if i < total_chunks - 1:
                await asyncio.sleep(1)
        return success_count == len(chunks)

    def _gen_wechat_payload(self, content: str) -> dict:
        """生成企业微信消息 payload"""
        if self._wechat_msg_type == 'text':
            return {
                "msgtype": "text",
                "text": {"content": content}
            }
        else:
            return {
                "msgtype": "markdown",
                "markdown": {"content": content}
            }
