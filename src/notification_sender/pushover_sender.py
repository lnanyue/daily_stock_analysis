# -*- coding: utf-8 -*-
"""
Pushover 发送提醒服务

职责：
1. 通过 Pushover API 发送 Pushover 消息
"""
import logging
import asyncio
from typing import Optional
from datetime import datetime

from src.config import Config
from src.formatters import markdown_to_plain_text
from src.notification_constants import NOTIFICATION_DEFAULT_TIMEOUT_SEC
from .async_base import get_sender_http_client


logger = logging.getLogger(__name__)


class PushoverSender:
    
    def __init__(self, config: Config):
        """
        初始化 Pushover 配置

        Args:
            config: 配置对象
        """
        self._pushover_config = {
            'user_key': getattr(config, 'pushover_user_key', None),
            'api_token': getattr(config, 'pushover_api_token', None),
        }
        self._timeout = getattr(config, 'notification_timeout_sec', NOTIFICATION_DEFAULT_TIMEOUT_SEC)
        
    def _is_pushover_configured(self) -> bool:
        """检查 Pushover 配置是否完整"""
        return bool(self._pushover_config['user_key'] and self._pushover_config['api_token'])

    async def send_to_pushover(self, content: str, title: Optional[str] = None) -> bool:
        """
        推送消息到 Pushover
        
        Pushover API 格式：
        POST https://api.pushover.net/1/messages.json
        {
            "token": "应用 API Token",
            "user": "用户 Key",
            "message": "消息内容",
            "title": "标题（可选）"
        }
        
        Pushover 特点：
        - 支持 iOS/Android/桌面多平台推送
        - 消息限制 1024 字符
        - 支持优先级设置
        - 支持 HTML 格式
        
        Args:
            content: 消息内容（Markdown 格式，会转为纯文本）
            title: 消息标题（可选，默认为"股票分析报告"）
            
        Returns:
            是否发送成功
        """
        if not self._is_pushover_configured():
            logger.warning("Pushover 配置不完整，跳过推送")
            return False
        
        user_key = self._pushover_config['user_key']
        api_token = self._pushover_config['api_token']
        
        # Pushover API 端点
        api_url = "https://api.pushover.net/1/messages.json"
        
        # 处理消息标题
        if title is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
            title = f"📈 股票分析报告 - {date_str}"
        
        # Pushover 消息限制 1024 字符
        max_length = 1024
        
        # 转换 Markdown 为纯文本（Pushover 支持 HTML，但纯文本更通用）
        plain_content = markdown_to_plain_text(content)
        
        if len(plain_content) <= max_length:
            # 单条消息发送
            return await self._send_pushover_message(api_url, user_key, api_token, plain_content, title)
        else:
            # 分段发送长消息
            return await self._send_pushover_chunked(api_url, user_key, api_token, plain_content, title, max_length)
      
    async def _send_pushover_message(
        self, 
        api_url: str, 
        user_key: str, 
        api_token: str, 
        message: str, 
        title: str,
        priority: int = 0
    ) -> bool:
        """
        发送单条 Pushover 消息
        
        Args:
            api_url: Pushover API 端点
            user_key: 用户 Key
            api_token: 应用 API Token
            message: 消息内容
            title: 消息标题
            priority: 优先级 (-2 ~ 2，默认 0)
        """
        payload = {
            "token": api_token,
            "user": user_key,
            "message": message,
            "title": title,
            "priority": priority,
        }

        client = await get_sender_http_client()
        response = await client.post(api_url, data=payload)

        if response.status_code == 200:
            result = response.json()
            if result.get('status') == 1:
                logger.info("Pushover 消息发送成功")
                return True
            logger.error("Pushover 返回错误: %s", result.get('errors', ['未知错误']))
            return False
        logger.error("Pushover 请求失败: HTTP %s", response.status_code)
        return False
    
    async def _send_pushover_chunked(
        self, 
        api_url: str, 
        user_key: str, 
        api_token: str, 
        content: str, 
        title: str,
        max_length: int
    ) -> bool:
        """
        分段发送长 Pushover 消息
        
        按段落分割，确保每段不超过最大长度
        """
        # 按段落（分隔线或双换行）分割
        if "────────" in content:
            sections = content.split("────────")
            separator = "────────"
        else:
            sections = content.split("\n\n")
            separator = "\n\n"
        
        chunks = []
        current_chunk = []
        current_length = 0
        
        for section in sections:
            # 计算添加这个 section 后的实际长度
            # join() 只在元素之间放置分隔符，不是每个元素后面
            # 所以：第一个元素不需要分隔符，后续元素需要一个分隔符连接
            if current_chunk:
                # 已有元素，添加新元素需要：当前长度 + 分隔符 + 新 section
                new_length = current_length + len(separator) + len(section)
            else:
                # 第一个元素，不需要分隔符
                new_length = len(section)
            
            if new_length > max_length:
                if current_chunk:
                    chunks.append(separator.join(current_chunk))
                current_chunk = [section]
                current_length = len(section)
            else:
                current_chunk.append(section)
                current_length = new_length
        
        if current_chunk:
            chunks.append(separator.join(current_chunk))
        
        total_chunks = len(chunks)
        success_count = 0
        
        logger.info("Pushover 分批发送：共 %s 批", total_chunks)
        
        for i, chunk in enumerate(chunks):
            # 添加分页标记到标题
            chunk_title = f"{title} ({i+1}/{total_chunks})" if total_chunks > 1 else title
            
            if await self._send_pushover_message(api_url, user_key, api_token, chunk, chunk_title):
                success_count += 1
                logger.info("Pushover 第 %s/%s 批发送成功", i+1, total_chunks)
            else:
                logger.error("Pushover 第 %s/%s 批发送失败", i+1, total_chunks)
            
            # 批次间隔，避免触发频率限制
            if i < total_chunks - 1:
                await asyncio.sleep(1)
        
        return success_count == total_chunks
    