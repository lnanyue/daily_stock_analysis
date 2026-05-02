# -*- coding: utf-8 -*-
"""
===================================
大盘复盘命令
===================================

执行大盘复盘分析，生成市场概览报告。
"""

import logging
import inspect
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


class MarketCommand(BotCommand):
    """
    大盘复盘命令
    
    执行大盘复盘分析，包括：
    - 主要指数表现
    - 板块热点
    - 市场情绪
    - 后市展望
    
    用法：
        /market - 执行大盘复盘
    """

    @property
    def name(self) -> str:
        return "market"

    @property
    def aliases(self) -> List[str]:
        return ["m", "大盘", "复盘", "行情"]

    @property
    def description(self) -> str:
        return "大盘复盘分析"

    @property
    def usage(self) -> str:
        return "/market"

    async def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """执行大盘复盘命令"""
        logger.info("[MarketCommand] 开始大盘复盘分析")

        import asyncio
        # 在后台异步执行复盘（避免阻塞）
        asyncio.create_task(self._run_market_review(message))

        return BotResponse.markdown_response(
            "✅ **大盘复盘任务已启动**\n\n"
            "正在分析：\n"
            "• 主要指数表现\n"
            "• 板块热点分析\n"
            "• 市场情绪判断\n"
            "• 后市展望\n\n"
            "分析完成后将自动推送结果。"
        )

    async def _run_market_review(self, message: BotMessage) -> None:
        """后台执行大盘复盘"""
        try:
            from src.config import get_config
            from src.notification import NotificationService
            from src.analyzer import GeminiAnalyzer
            from src.core.market_review import run_market_review

            config = get_config()
            notifier = NotificationService(source_message=message)

            # 与 CLI/main.py 保持一致：交易日过滤后只跑实际开市的相关市场。
            override_region = None
            if getattr(config, "trading_day_check_enabled", True):
                try:
                    from src.core.trading_calendar import (
                        compute_effective_region,
                        get_open_markets_today,
                    )

                    open_markets = get_open_markets_today()
                    override_region = compute_effective_region(
                        getattr(config, "market_review_region", "cn") or "cn",
                        open_markets,
                    )
                except Exception as calendar_err:
                    logger.warning(
                        "[MarketCommand] 交易日过滤 fail-open: %s", calendar_err
                    )
                    override_region = None

                if override_region == "":
                    logger.info("[MarketCommand] 今日相关市场休市，跳过大盘复盘")
                    if notifier.is_available():
                        await self._maybe_await(
                            notifier.send(
                                "🎯 大盘复盘\n\n今日相关市场休市，已跳过大盘复盘。",
                                email_send_to_all=True,
                            )
                        )
                    return

            # 初始化搜索服务
            search_service = None
            if config.has_search_capability_enabled():
                from src.search_service import get_search_service
                search_service = get_search_service()

            # 初始化 AI 分析器
            analyzer = None
            if (
                getattr(config, "llm_model_list", None)
                or getattr(config, "gemini_api_key", None)
                or getattr(config, "anthropic_api_key", None)
                or getattr(config, "openai_api_key", None)
                or getattr(config, "deepseek_api_keys", None)
            ):
                analyzer = GeminiAnalyzer()

            # 执行复盘（调用核心模块的异步函数）
            review_report = await self._maybe_await(run_market_review(
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
                send_notification=True,
                override_region=override_region,
            ))

            if review_report:
                logger.info("[MarketCommand] 大盘复盘完成并已推送")
            else:
                logger.warning("[MarketCommand] 大盘复盘返回空结果")

        except Exception as e:
            logger.error("[MarketCommand] 大盘复盘失败: %s", e)
            logger.exception(e)

    @staticmethod
    async def _maybe_await(value):
        if inspect.isawaitable(value):
            return await value
        return value
