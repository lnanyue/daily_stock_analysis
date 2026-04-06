# -*- coding: utf-8 -*-
"""
===================================
定时调度模块 - 异步增强版
===================================

职责：
1. 支持每日定时执行股票分析 (支持 async 回调)
2. 支持定时执行大盘复盘
3. 优雅处理信号，确保可靠退出
"""

import logging
import signal
import sys
import time
import asyncio
import threading
from datetime import datetime
from typing import Callable, Optional, Any, Coroutine

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """优雅退出处理器"""
    def __init__(self):
        self.shutdown_requested = False
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._set_shutdown)
        except RuntimeError:
            # Not in an event loop
            signal.signal(signal.SIGINT, self._sync_handler)
            signal.signal(signal.SIGTERM, self._sync_handler)
    
    def _set_shutdown(self):
        if not self.shutdown_requested:
            logger.info("收到退出信号，等待当前任务完成...")
            self.shutdown_requested = True

    def _sync_handler(self, signum, frame):
        self._set_shutdown()
    
    @property
    def should_shutdown(self) -> bool:
        return self.shutdown_requested


class AsyncScheduler:
    """定时任务调度器 (支持异步任务)"""
    
    def __init__(self, schedule_time: str = "18:00"):
        try:
            import schedule
            self.schedule = schedule
        except ImportError:
            raise ImportError("请安装 schedule 库: pip install schedule")
        
        self.schedule_time = schedule_time
        self.shutdown_handler = None # Initialized in run()
        self._task_callback: Optional[Callable] = None
        self._running = False
        
    def set_daily_task(self, task: Callable[[], Any], run_immediately: bool = True):
        """设置每日任务 (可以是 sync 函数或 async 函数)"""
        self._task_callback = task
        self.schedule.every().day.at(self.schedule_time).do(self._trigger_task)
        self._should_run_now = run_immediately

    def _trigger_task(self):
        """Internal bridge from schedule to asyncio."""
        asyncio.create_task(self._safe_run_task())

    async def _safe_run_task(self):
        """安全执行任务"""
        if self._task_callback is None:
            return
        
        try:
            logger.info("=" * 50)
            logger.info("定时任务开始执行 - %%M:%S')", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            
            res = self._task_callback()
            if asyncio.iscoroutine(res):
                await res
            
            logger.info("定时任务执行完成 - %%M:%S')", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            logger.info("=" * 50)
        except Exception as e:
            logger.exception("定时任务执行失败: %s", e)
    
    async def run(self):
        """运行调度器主循环 (Async)"""
        self.shutdown_handler = GracefulShutdown()
        self._running = True
        logger.info("异步调度器开始运行，执行时间: %s", self.schedule_time)
        
        if getattr(self, '_should_run_now', False):
            await self._safe_run_task()
        
        while self._running and not self.shutdown_handler.should_shutdown:
            self.schedule.run_pending()
            await asyncio.sleep(10)
            
            if datetime.now().minute == 0 and datetime.now().second < 15:
                logger.debug("调度器运行中...")
        
        logger.info("调度器已停止")

    def stop(self):
        self._running = False


def run_with_schedule(
    task: Callable,
    schedule_time: str = "18:00",
    run_immediately: bool = True
):
    """兼容旧版同步入口的包装器"""
    scheduler = AsyncScheduler(schedule_time=schedule_time)
    scheduler.set_daily_task(task, run_immediately=run_immediately)
    asyncio.run(scheduler.run())

async def run_with_schedule_async(
    task: Callable,
    schedule_time: str = "18:00",
    run_immediately: bool = True
):
    """异步定时入口"""
    scheduler = AsyncScheduler(schedule_time=schedule_time)
    scheduler.set_daily_task(task, run_immediately=run_immediately)
    await scheduler.run()
