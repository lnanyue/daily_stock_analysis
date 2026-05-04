# -*- coding: utf-8 -*-
"""
环境引导与 .env 热重载。

职责：
1. 在 config 包导入时捕获进程环境快照（``_INITIAL_PROCESS_ENV``）
2. 提供幂等的 ``bootstrap_environment()`` 给外部消费者
3. 提供 ``reload_runtime_config()`` 给定时模式热重载
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

# 在 .env 加载前的进程环境快照 —— 用于定时模式区分进程覆盖与 .env 值。
_INITIAL_PROCESS_ENV = dict(os.environ)


def _get_active_env_path() -> Path:
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parents[2] / ".env"  # up to project root


def _read_active_env_values() -> Optional[Dict[str, str]]:
    env_path = _get_active_env_path()
    if not env_path.exists():
        return {}

    try:
        values = dotenv_values(env_path)
    except Exception as exc:
        logger.warning("读取配置文件 %s 失败，继续沿用当前环境变量: %s", env_path, exc)
        return None

    return {
        str(key): "" if value is None else str(value)
        for key, value in values.items()
        if key is not None
    }


_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {
    key for key in _ACTIVE_ENV_FILE_VALUES
    if key not in _INITIAL_PROCESS_ENV
}


def reload_env_overrides() -> None:
    """刷新 .env 管理的环境变量，不覆盖进程自身的环境变量覆盖。"""
    global _RUNTIME_ENV_FILE_KEYS

    latest_values = _read_active_env_values()
    if latest_values is None:
        return

    managed_keys = {
        key for key in latest_values
        if key not in _INITIAL_PROCESS_ENV
    }

    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)

    for key in managed_keys:
        os.environ[key] = latest_values[key]

    _RUNTIME_ENV_FILE_KEYS = managed_keys


def reload_runtime_config():
    """从最新的 .env 值重新加载配置（定时模式使用）。"""
    from src.config.manager import Config, get_config

    reload_env_overrides()
    Config.reset_instance()
    return get_config()


_env_bootstrapped = False


def bootstrap_environment() -> None:
    """确保 .env 和代理设置已应用。幂等，可供 API / bot 消费者使用。"""
    global _env_bootstrapped
    if _env_bootstrapped:
        return

    from src.config import setup_env
    setup_env()

    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
        proxy_port = os.getenv("PROXY_PORT", "10809")
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url

    _env_bootstrapped = True
