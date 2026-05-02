# -*- coding: utf-8 -*-
"""
Server酱3 发送提醒服务

职责：
1. 通过 Server酱3 API 发送 Server酱3 消息
"""
import logging
from typing import Optional
from datetime import datetime
import re

from src.config import Config
from src.notification_constants import NOTIFICATION_DEFAULT_TIMEOUT_SEC


logger = logging.getLogger(__name__)


from .base import BaseNotificationSender

class Serverchan3Sender(BaseNotificationSender):

    def __init__(self, config: Config):
        # 必须在 super().__init__() 之前初始化，因为 _check_enabled 会访问
        self._serverchan3_sendkey = getattr(config, 'serverchan3_sendkey', None)
        self._timeout = getattr(config, 'notification_timeout_sec', NOTIFICATION_DEFAULT_TIMEOUT_SEC)
        super().__init__(config)

    def _check_enabled(self) -> bool:
        return bool(self._serverchan3_sendkey)

    @property
    def name(self) -> str:
        return "Server酱3"

    async def send(self, content: str, image_bytes: Optional[bytes] = None, **kwargs) -> bool:
        """统一发送接口"""
        if not self.enabled:
            return False
            
        return await self.send_to_serverchan3(content, title=kwargs.get('title'))

    async def send_to_serverchan3(self, content: str, title: Optional[str] = None) -> bool:
        """推送消息到 Server酱3"""
        if not self._serverchan3_sendkey:
            logger.warning("Server酱3 SendKey 未配置，跳过推送")
            return False

        if title is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
            title = f"📈 股票分析报告 - {date_str}"

        from .async_base import get_sender_http_client
        sendkey = self._serverchan3_sendkey
        if sendkey.startswith('sctp'):
            match = re.match(r'sctp(\d+)t', sendkey)
            if match:
                url = f"https://{match.group(1)}.push.ft07.com/send/{sendkey}.send"
            else:
                logger.error("Invalid sendkey format for sctp")
                return False
        else:
            url = f"https://sctapi.ftqq.com/{sendkey}.send"

        params = {'title': title, 'desp': content, 'options': {}}
        headers = {'Content-Type': 'application/json;charset=utf-8'}

        client = await get_sender_http_client()
        response = await client.post(url, json=params, headers=headers)

        if response.status_code == 200:
            logger.info("Server酱3 消息发送成功: %s", response.json())
            return True
        logger.error("Server酱3 请求失败: HTTP %s", response.status_code)
        return False
