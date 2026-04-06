# -*- coding: utf-8 -*-
"""
===================================
批量分析命令
===================================

批量分析自选股列表中的所有股票。
"""

import logging
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


class BatchCommand(BotCommand):
    """
    批量分析命令
    
    批量分析配置中的自选股列表，生成汇总报告。
    
    用法：
        /batch      - 分析所有自选股
        /batch 3    - 只分析前3只
    """
    
    @property
    def name(self) -> str:
        return "batch"
    
    @property
    def aliases(self) -> List[str]:
        return ["b", "批量", "全部"]
    
    @property
    def description(self) -> str:
        return "批量分析自选股"
    
    @property
    def usage(self) -> str:
        return "/batch [数量]"
    
    @property
    def admin_only(self) -> bool:
        """批量分析需要管理员权限（防止滥用）"""
        return False  # 可以根据需要设为 True
    
    async def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """执行批量分析命令"""
        from src.config import get_config
        from src.services.task_service import get_task_service
        
        config = get_config()
        config.refresh_stock_list()
        
        stock_list = config.stock_list
        
        if not stock_list:
            return BotResponse.error_response(
                "自选股列表为空，请先配置 STOCK_LIST"
            )
        
        # 解析数量参数
        limit = None
        if args:
            try:
                limit = int(args[0])
                if limit <= 0:
                    return BotResponse.error_response("数量必须大于0")
            except ValueError:
                return BotResponse.error_response(f"无效的数量: {args[0]}")
        
        # 限制分析数量
        if limit:
            stock_list = stock_list[:limit]
        
        logger.info("[BatchCommand] 开始批量分析 %s 只股票", len(stock_list))
        
        # 获取异步任务服务
        service = await get_task_service()
        
        # 逐个提交异步分析任务（submit_analysis 内部会 create_task，所以这里 await 很快）
        for code in stock_list:
            await service.submit_analysis(code, source_message=message)
        
        return BotResponse.markdown_response(
            f"✅ **批量分析任务已启动**\n\n"
            f"• 分析数量: {len(stock_list)} 只\n"
            f"• 股票列表: {', '.join(stock_list[:5])}"
            f"{'...' if len(stock_list) > 5 else ''}\n\n"
            f"分析完成后将自动推送结果。"
        )

