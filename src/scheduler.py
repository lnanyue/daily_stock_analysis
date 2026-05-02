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
import re
import signal
import time
import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

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

    def __init__(
        self,
        schedule_time: str = "18:00",
        schedule_time_provider: Optional[Callable[[], str]] = None,
    ):
        try:
            import schedule
            self.schedule = schedule
        except ImportError:
            raise ImportError("请安装 schedule 库: pip install schedule")
        
        self.schedule_time = schedule_time
        self._schedule_time_provider = schedule_time_provider
        self.shutdown_handler = None # Initialized in run()
        self._task_callback: Optional[Callable] = None
        self._daily_job: Optional[Any] = None
        self._background_tasks: List[Dict[str, Any]] = []
        self._running = False

    def set_daily_task(self, task: Callable[[], Any], run_immediately: bool = True):
        """设置每日任务 (可以是 sync 函数或 async 函数)"""
        self._task_callback = task
        if not self._configure_daily_task(self.schedule_time):
            raise ValueError(f"无效的定时执行时间: {self.schedule_time!r}")
        self._should_run_now = run_immediately

    @staticmethod
    def _is_valid_schedule_time(schedule_time: str) -> bool:
        return bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", (schedule_time or "").strip()))

    def _cancel_daily_job(self) -> None:
        if self._daily_job is None:
            return
        if hasattr(self.schedule, "cancel_job"):
            self.schedule.cancel_job(self._daily_job)
        else:  # pragma: no cover - schedule compatibility fallback
            jobs = getattr(self.schedule, "jobs", None)
            if isinstance(jobs, list) and self._daily_job in jobs:
                jobs.remove(self._daily_job)
        self._daily_job = None

    def _configure_daily_task(self, schedule_time: str) -> bool:
        candidate = (schedule_time or "").strip()
        if not self._is_valid_schedule_time(candidate):
            logger.warning("检测到无效的定时执行时间 %r，继续沿用当前时间 %s", schedule_time, self.schedule_time)
            return False

        previous_time = self.schedule_time
        self._cancel_daily_job()
        self._daily_job = self.schedule.every().day.at(candidate).do(self._trigger_task)
        self.schedule_time = candidate
        if previous_time == candidate:
            logger.info("已设置每日定时任务，执行时间: %s", self.schedule_time)
        else:
            logger.info("检测到 SCHEDULE_TIME 变更，已将每日定时任务从 %s 更新为 %s", previous_time, candidate)
        return True

    def _refresh_daily_schedule_if_needed(self) -> None:
        if self._task_callback is None or self._schedule_time_provider is None:
            return
        try:
            latest_schedule_time = (self._schedule_time_provider() or "").strip()
        except Exception as exc:
            logger.warning("读取最新 SCHEDULE_TIME 失败，继续沿用 %s: %s", self.schedule_time, exc)
            return
        if latest_schedule_time and latest_schedule_time != self.schedule_time:
            self._configure_daily_task(latest_schedule_time)

    def _trigger_task(self):
        """Internal bridge from schedule to asyncio."""
        asyncio.create_task(self._safe_run_task())

    async def _safe_run_task(self):
        """安全执行任务"""
        if self._task_callback is None:
            return
        
        try:
            logger.info("=" * 50)
            logger.info("定时任务开始执行 - %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

            res = self._task_callback()
            if asyncio.iscoroutine(res):
                await res

            logger.info("定时任务执行完成 - %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            logger.info("=" * 50)
        except Exception as e:
            logger.exception("定时任务执行失败: %s", e)

    def add_background_task(
        self,
        task: Callable[[], Any],
        interval_seconds: int,
        run_immediately: bool = False,
        name: Optional[str] = None,
    ) -> None:
        """Register a periodic background task inside the scheduler loop."""
        interval = max(10, int(interval_seconds))
        entry = {
            "task": task,
            "interval_seconds": interval,
            "last_run": 0.0 if run_immediately else time.time(),
            "name": name or getattr(task, "__name__", "background_task"),
            "handle": None,
        }
        self._background_tasks.append(entry)
        logger.info("已注册后台任务: %s（间隔 %s 秒，立即执行=%s）", entry["name"], interval, run_immediately)

    async def _safe_run_background_task(self, entry: Dict[str, Any]) -> None:
        try:
            logger.info("后台任务开始执行: %s", entry["name"])
            res = entry["task"]()
            if asyncio.iscoroutine(res):
                await res
        except Exception as exc:
            logger.exception("后台任务执行失败 [%s]: %s", entry["name"], exc)
        finally:
            entry["last_run"] = time.time()
            entry["handle"] = None

    def _run_background_tasks(self) -> None:
        if not self._background_tasks:
            return
        now = time.time()
        for entry in self._background_tasks:
            handle = entry.get("handle")
            if handle is not None and not handle.done():
                continue
            if handle is not None and handle.done():
                entry["handle"] = None
            if now - entry["last_run"] < entry["interval_seconds"]:
                continue
            entry["handle"] = asyncio.create_task(self._safe_run_background_task(entry))

    async def run(self):
        """运行调度器主循环 (Async)"""
        self.shutdown_handler = GracefulShutdown()
        self._running = True
        logger.info("异步调度器开始运行，执行时间: %s", self.schedule_time)
        
        if getattr(self, '_should_run_now', False):
            await self._safe_run_task()

        while self._running and not self.shutdown_handler.should_shutdown:
            self._refresh_daily_schedule_if_needed()
            self.schedule.run_pending()
            self._run_background_tasks()
            await asyncio.sleep(10)
            
            if datetime.now().minute == 0 and datetime.now().second < 15:
                logger.debug("调度器运行中...")
        
        logger.info("调度器已停止")

    def stop(self):
        self._running = False


def run_with_schedule(
    task: Callable,
    schedule_time: str = "18:00",
    run_immediately: bool = True,
    background_tasks: Optional[List[Dict[str, Any]]] = None,
    schedule_time_provider: Optional[Callable[[], str]] = None,
):
    """兼容旧版同步入口的包装器"""
    scheduler = AsyncScheduler(
        schedule_time=schedule_time,
        schedule_time_provider=schedule_time_provider,
    )
    for entry in background_tasks or []:
        scheduler.add_background_task(
            task=entry["task"],
            interval_seconds=entry["interval_seconds"],
            run_immediately=entry.get("run_immediately", False),
            name=entry.get("name"),
        )
    scheduler.set_daily_task(task, run_immediately=run_immediately)
    asyncio.run(scheduler.run())

async def run_with_schedule_async(
    task: Callable,
    schedule_time: str = "18:00",
    run_immediately: bool = True,
    background_tasks: Optional[List[Dict[str, Any]]] = None,
    schedule_time_provider: Optional[Callable[[], str]] = None,
):
    """异步定时入口"""
    scheduler = AsyncScheduler(
        schedule_time=schedule_time,
        schedule_time_provider=schedule_time_provider,
    )
    for entry in background_tasks or []:
        scheduler.add_background_task(
            task=entry["task"],
            interval_seconds=entry["interval_seconds"],
            run_immediately=entry.get("run_immediately", False),
            name=entry.get("name"),
        )
    scheduler.set_daily_task(task, run_immediately=run_immediately)
    await scheduler.run()
