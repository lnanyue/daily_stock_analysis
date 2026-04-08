# -*- coding: utf-8 -*-
"""
配置模型与数据类定义
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Literal


@dataclass
class ConfigIssue:
    """结构化配置验证问题"""
    severity: Literal["error", "warning", "info"]
    message: str
    field: str = ""

    def __str__(self) -> str:
        return self.message


@dataclass
class LLMChannelConfig:
    """单个 LLM 通道的配置"""
    name: str
    protocol: str  # openai, anthropic, gemini, etc.
    api_key: str
    models: List[str]
    base_url: Optional[str] = None
    api_version: Optional[str] = None
    priority: int = 10
    enabled: bool = True
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    extra_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformConfig:
    """机器人平台配置基类"""
    enabled: bool = False
    webhook_url: Optional[str] = None
    secret: Optional[str] = None


@dataclass
class DingTalkConfig(PlatformConfig):
    """钉钉配置"""
    access_token: Optional[str] = None


@dataclass
class FeishuConfig(PlatformConfig):
    """飞书配置"""
    app_id: Optional[str] = None
    app_secret: Optional[str] = None


@dataclass
class TelegramConfig(PlatformConfig):
    """Telegram 配置"""
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
