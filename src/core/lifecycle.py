# -*- coding: utf-8 -*-
"""
应用生命周期管理：资源清理与预配置日志引导。

职责：
1. ``cleanup()`` —— 关闭 LiteLLM worker、HTTP 客户端、数据库引擎、取消逾期的 asyncio task
2. ``bootstrap_logging()`` —— 在配置加载前置顶 stderr 日志处理器
3. ``run_with_cleanup()`` —— 在协程完成后执行清理
"""

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


def bootstrap_logging(debug: bool = False) -> None:
    """在配置加载前初始化仅 stderr 的日志。

    文件处理器推迟到知道了 ``config.log_dir`` 后才通过 ``setup_logging()`` 添加，
    这样健康运行的日志不会写入硬编码目录。
    """
    import os
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", category=ResourceWarning)
    os.environ.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning,ignore::ResourceWarning")

    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)


async def cleanup() -> None:
    """关闭所有共享资源以避免 ResourceWarning。"""
    # 0. 刷入 / 停止 LiteLLM 后台日志 worker
    try:
        from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
        if GLOBAL_LOGGING_WORKER is not None:
            try:
                await asyncio.wait_for(GLOBAL_LOGGING_WORKER.flush(), timeout=1.0)
            except Exception as e:
                logger.debug("LiteLLM logging flush cleanup: %s", e)
            try:
                await asyncio.wait_for(GLOBAL_LOGGING_WORKER.stop(), timeout=1.0)
            except Exception as e:
                logger.debug("LiteLLM logging stop cleanup: %s", e)
    except Exception as e:
        logger.debug("LiteLLM worker cleanup: %s", e)

    # 1. 关闭共享 async HTTP 客户端
    try:
        from src.utils.async_http import AsyncHttpClientManager
        await AsyncHttpClientManager().close()
    except Exception as e:
        logger.debug("AsyncHttpClient cleanup: %s", e)

    # 2. 关闭数据库引擎
    try:
        from src.storage import StorageManager
        mgr = StorageManager.get_instance()
        if hasattr(mgr, "_engine") and mgr._engine is not None:
            mgr._engine.dispose(close=True)
    except Exception as e:
        logger.debug("Database cleanup: %s", e)

    # 3. 取消所有逾期的 asyncio task（当前 task 除外）
    try:
        current_task = asyncio.current_task()
        tasks = [t for t in asyncio.all_tasks() if t is not current_task]
        if tasks:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.debug("Task cleanup: %s", e)

    # 4. 等待 LiteLLM worker 优雅退出
    await asyncio.sleep(0.5)


async def run_with_cleanup(coro) -> int:
    """执行协程，完成后执行清理。"""
    try:
        await coro
        return 0
    except Exception:
        logger.exception("run_with_cleanup 捕获异常，返回 1")
        return 1
    finally:
        await cleanup()
