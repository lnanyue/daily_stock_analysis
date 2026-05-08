# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================

职责：
1. 命令行解析 (argparse)
2. 环境与日志初始化调度
3. 模式分发 (Mode Routing)

每个模式的具体实现位于 ``src.core.runner``。
系统生命周期管理位于 ``src.core.lifecycle``。
"""

import argparse
import logging
import sys
from typing import List, Optional

from data_provider import canonical_stock_code

logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="A股自选股智能分析系统")
    parser.add_argument("--debug", action="store_true", help="启用调试模式")
    parser.add_argument("--dry-run", action="store_true", help="仅获取数据，不进行 AI 分析")
    parser.add_argument("--stocks", type=str, help="指定股票代码，逗号分隔")
    parser.add_argument("--no-notify", action="store_true", help="不发送推送通知")
    parser.add_argument("--single-notify", action="store_true", help="启用单股推送模式")
    parser.add_argument("--workers", type=int, default=None, help="并发数")
    parser.add_argument("--schedule", action="store_true", help="启用定时任务模式")
    parser.add_argument("--no-run-immediately", action="store_true", help="定时任务启动时不立即执行一次")
    parser.add_argument("--market-review", action="store_true", help="仅运行大盘复盘")
    parser.add_argument("--no-market-review", action="store_true", help="跳过大盘复盘")
    parser.add_argument("--force-run", action="store_true", help="强制执行分析")
    parser.add_argument("--no-context-snapshot", action="store_true", help="不保存上下文快照")
    parser.add_argument("--backtest", action="store_true", help="运行回测")
    parser.add_argument("--backtest-code", type=str, help="指定回测代码")
    parser.add_argument("--backtest-days", type=int, help="回测天数")
    parser.add_argument("--backtest-force", action="store_true", help="强制回测")
    parser.add_argument("--risk-screen", action="store_true", help="运行独立排雷筛选流程（--stocks 指定股票）")
    return parser.parse_args()


def _parse_cli_stock_codes(args: argparse.Namespace) -> Optional[List[str]]:
    """将命令行 --stocks 参数转换为规范化股票代码列表。"""
    if not getattr(args, "stocks", None):
        return None
    return [canonical_stock_code(c) for c in args.stocks.split(",")]


def main() -> int:
    """系统入口。"""
    # 1. 初始解析与早期日志引导
    args = parse_arguments()
    from src.core.lifecycle import bootstrap_logging, run_with_cleanup
    bootstrap_logging(debug=args.debug)

    # 2. 环境初始化 (.env, 代理)
    from src.config.env import bootstrap_environment
    bootstrap_environment()

    # 3. 加载完整配置与正式日志
    from src.config import get_config
    config = get_config()
    from src.logging_config import setup_logging
    setup_logging(log_prefix="stock_analysis", debug=args.debug, log_dir=config.log_dir)

    logger.info("=" * 40 + " 系统启动 " + "=" * 40)
    config.validate()

    # 4. 后台服务 (机器人 Stream 等)
    from src.core.runner import (
        run_full_analysis,
        run_backtest,
        run_market_review_only,
        run_schedule_mode,
        run_risk_screen,
        start_bot_stream_clients,
    )
    start_bot_stream_clients(config)

    # 5. 模式路由 (White-box Routing)
    import asyncio
    try:
        # 模式 A: 回测
        if getattr(args, "backtest", False):
            return run_backtest(getattr(args, "backtest_code", None))

        # 模式 A.5: 排雷筛选
        if getattr(args, "risk_screen", False):
            stock_codes = _parse_cli_stock_codes(args)
            return asyncio.run(run_risk_screen(config, args, stock_codes))

        # 模式 B: 仅大盘复盘
        if args.market_review:
            return asyncio.run(run_market_review_only(config, args))

        # 模式 C: 定时任务 (长驻进程)
        if args.schedule or config.schedule_enabled:
            return run_schedule_mode(config, args)

        # 模式 D: 默认分析流程 (个股 + 复盘)
        stock_codes = _parse_cli_stock_codes(args)
        return asyncio.run(run_with_cleanup(run_full_analysis(config, args, stock_codes)))

    except KeyboardInterrupt:
        logger.info("用户中断执行")
        return 130
    except Exception as e:
        logger.exception("系统运行异常: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
