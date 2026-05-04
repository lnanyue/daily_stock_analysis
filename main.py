# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序 (异步版)
===================================

职责：
1. 命令行入口
2. 参数解析
3. 模式路由
4. 全局异常处理

每个模式的具体实现在 ``src/core/runner.py``。
"""

import os
import sys
import warnings

# Suppress warnings that cannot be easily controlled per-module.
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=ResourceWarning)
os.environ.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning,ignore::ResourceWarning")

# .env loading — must happen before any application imports.
from dotenv import dotenv_values
from src.config import setup_env

_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

# 代理配置（模块级，使用 .env 中的值）
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import argparse
import asyncio
import logging
from typing import List, Optional

from data_provider import canonical_stock_code
from src.config import get_config, Config
from src.core.runner import run_full_analysis, run_backtest, run_market_review_only, run_schedule_mode
from src.logging_config import setup_logging

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
    return parser.parse_args()


def _parse_cli_stock_codes(args: argparse.Namespace) -> Optional[List[str]]:
    """将命令行 --stocks 参数转换为规范化股票代码列表。"""
    if not getattr(args, "stocks", None):
        return None
    return [canonical_stock_code(c) for c in args.stocks.split(",")]


def start_bot_stream_clients(config: Config) -> None:
    """启动 Stream 机器人（后台线程）。"""
    if config.dingtalk_stream_enabled:
        from bot.platforms import start_dingtalk_stream_background
        start_dingtalk_stream_background()
    if getattr(config, "feishu_stream_enabled", False):
        from bot.platforms import start_feishu_stream_background
        start_feishu_stream_background()


def main() -> int:
    """向后兼容的同步入口。"""
    args = parse_arguments()
    config = get_config()
    setup_logging(log_prefix="stock_analysis", debug=args.debug, log_dir=config.log_dir)

    logger.info("=" * 40 + " 系统启动 " + "=" * 40)
    config.validate()

    start_bot_stream_clients(config)

    try:
        # ── 回测模式 ──
        if getattr(args, "backtest", False):
            return run_backtest(getattr(args, "backtest_code", None))

        # ── 仅大盘复盘模式 ──
        if args.market_review:
            return asyncio.run(run_market_review_only(config, args))

        # ── 定时调度模式 ──
        if args.schedule or config.schedule_enabled:
            return run_schedule_mode(config, args)

        # ── 默认：单次个股分析 + 大盘复盘 ──
        stock_codes = _parse_cli_stock_codes(args)
        from src.core.lifecycle import run_with_cleanup

        return asyncio.run(run_with_cleanup(run_full_analysis(config, args, stock_codes)))

    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.exception("执行失败: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
