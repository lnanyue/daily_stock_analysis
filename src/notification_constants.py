# -*- coding: utf-8 -*-
"""Notification timeout constants, shared by src.notification and senders.
Defining these in a separate module avoids circular imports.
"""

# 统一超时配置（秒）— 所有通知渠道的默认超时值
NOTIFICATION_DEFAULT_TIMEOUT_SEC = 20

# 通知发送默认最大重试次数
NOTIFICATION_DEFAULT_MAX_RETRIES = 2
