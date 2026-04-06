# -*- coding: utf-8 -*-
"""
===================================
异步任务服务层 - 异步版
===================================

职责：
1. 管理异步分析任务 (Asyncio Task)
2. 执行股票分析并推送结果
3. 查询任务状态和历史
"""

from __future__ import annotations

import logging
import asyncio
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List, Union

from src.enums import ReportType
from src.storage import get_db
from bot.models import BotMessage

logger = logging.getLogger(__name__)


class TaskService:
    """
    异步任务服务 (Async-first)

    负责：
    1. 管理异步分析任务
    2. 执行股票分析
    3. 触发通知推送
    """

    _instance: Optional['TaskService'] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._tasks_lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> 'TaskService':
        """获取单例实例 (Async)"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def submit_analysis(
        self,
        code: str,
        report_type: Union[ReportType, str] = ReportType.SIMPLE,
        source_message: Optional[BotMessage] = None,
        save_context_snapshot: Optional[bool] = None,
        query_source: str = "bot"
    ) -> Dict[str, Any]:
        """
        提交异步分析任务 (不阻塞，立即返回)
        """
        if isinstance(report_type, str):
            report_type = ReportType.from_str(report_type)

        task_id = f"{code}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

        # 启动异步后台任务
        asyncio.create_task(
            self._run_analysis(
                code,
                task_id,
                report_type,
                source_message,
                save_context_snapshot,
                query_source
            )
        )

        logger.info(f"[TaskService] 已提交股票 {code} 的异步分析任务, task_id={task_id}")

        return {
            "success": True,
            "message": "分析任务已提交，将异步执行并推送通知",
            "code": code,
            "task_id": task_id,
            "report_type": report_type.value
        }

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with self._tasks_lock:
            return self._tasks.get(task_id)

    async def _run_analysis(
        self,
        code: str,
        task_id: str,
        report_type: ReportType = ReportType.SIMPLE,
        source_message: Optional[BotMessage] = None,
        save_context_snapshot: Optional[bool] = None,
        query_source: str = "bot"
    ) -> Dict[str, Any]:
        """
        异步执行单只股票分析
        """
        async with self._tasks_lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "code": code,
                "status": "running",
                "start_time": datetime.now().isoformat(),
                "result": None,
                "error": None,
                "report_type": report_type.value
            }

        try:
            from src.config import get_config
            from src.core.pipeline import StockAnalysisPipeline

            logger.info(f"[TaskService] 正在异步分析股票: {code}")

            config = get_config()
            pipeline = StockAnalysisPipeline(
                config=config,
                max_workers=1,
                source_message=source_message,
                query_id=task_id,
                query_source=query_source,
                save_context_snapshot=save_context_snapshot
            )

            # 执行单只股票分析 (Async)
            result = await pipeline.process_single_stock(
                code=code,
                skip_analysis=False,
                single_stock_notify=True,
                report_type=report_type
            )

            if result:
                result_data = {
                    "code": result.code,
                    "name": result.name,
                    "sentiment_score": result.sentiment_score,
                    "operation_advice": result.operation_advice,
                    "trend_prediction": result.trend_prediction,
                    "analysis_summary": result.analysis_summary,
                }

                async with self._tasks_lock:
                    self._tasks[task_id].update({
                        "status": "completed",
                        "end_time": datetime.now().isoformat(),
                        "result": result_data
                    })

                return {"success": True, "task_id": task_id, "result": result_data}
            else:
                raise Exception("分析返回空结果")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TaskService] 股票 {code} 分析异常: {error_msg}")

            async with self._tasks_lock:
                self._tasks[task_id].update({
                    "status": "failed",
                    "end_time": datetime.now().isoformat(),
                    "error": error_msg
                })

            return {"success": False, "task_id": task_id, "error": error_msg}


# ============================================================
# 便捷函数
# ============================================================

_task_service_instance: Optional[TaskService] = None

async def get_task_service() -> TaskService:
    """获取任务服务单例 (Async)"""
    global _task_service_instance
    if _task_service_instance is None:
        _task_service_instance = TaskService()
    return _task_service_instance
