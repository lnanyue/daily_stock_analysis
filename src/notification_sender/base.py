# -*- coding: utf-8 -*-
"""
Notification Sender Base Class
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional, List

from src.config import Config

logger = logging.getLogger(__name__)

class BaseNotificationSender(ABC):
    """
    Abstract base class for all notification senders.
    """
    def __init__(self, config: Config):
        self.config = config
        self.enabled = self._check_enabled()

    @abstractmethod
    def _check_enabled(self) -> bool:
        """Check if this sender is configured and enabled."""
        pass

    @abstractmethod
    async def send(self, content: str, image_bytes: Optional[bytes] = None, **kwargs) -> bool:
        """
        Send a notification.
        
        Args:
            content: Markdown or text content.
            image_bytes: Optional image data to send.
            **kwargs: Sender-specific arguments (e.g., receivers for email).
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the display name of the channel."""
        pass
