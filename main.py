# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序 (异步版)
===================================

职责：
1. 协调各模块完成股票分析流程 (Async-first)
2. 全局异常处理，确保单股失败不影响整体
3. 提供命令行入口
"""
import os
import sys
import warnings

# Suppress warnings that cannot be easily controlled per-module.
# Must be set via PYTHONWARNINGS env var BEFORE interpreter startup.
# - DeprecationWarning: from lark_oapi/websockets (upstream libs using deprecated datetime APIs)
# - ResourceWarning: from SQLAlchemy's SQLite pool (delayed GC of pooled connections)
# Re-apply in case the interpreter already processed the defaults
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=ResourceWarning)

# Also set PYTHONWARNINGS for C-level warnings that bypass the Python filter stack
os.environ.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning,ignore::ResourceWarning")

from src.config import setup_env
setup_env()

# 代理配置
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import argparse
import logging
import sys
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from data_provider import canonical_stock_code
from src.core.pipeline import StockAnalysisPipeline
from src.core.market_review import run_market_review
from src.config import get_config, Config
from src.logging_config import setup_logging


logger = logging.getLogger(__name__)


def _parse_cli_stock_codes(args: argparse.Namespace) -> Optional[List[str]]:
    if not getattr(args, "stocks", None):
        return None
    return [canonical_stock_code(c) for c in args.stocks.split(',')]


def _resolve_schedule_run_immediately(config: Config, args: argparse.Namespace) -> bool:
    if getattr(args, 'no_run_immediately', False):
        return False
    return getattr(config, 'schedule_run_immediately', True)


def _warn_schedule_stock_override(args: argparse.Namespace) -> None:
    if getattr(args, "stocks", None):
        logger.warning(
            "定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，并在每次运行前重新读取最新的 STOCK_LIST。"
        )


def _build_schedule_task(config: Config, args: argparse.Namespace):
    def _task():
        result = run_full_analysis(config, args, None)
        if asyncio.iscoroutine(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_run_single_shot_with_cleanup(result))
        return result

    return _task


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='A股自选股智能分析系统')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')
    parser.add_argument('--dry-run', action='store_true', help='仅获取数据，不进行 AI 分析')
    parser.add_argument('--stocks', type=str, help='指定股票代码，逗号分隔')
    parser.add_argument('--no-notify', action='store_true', help='不发送推送通知')
    parser.add_argument('--single-notify', action='store_true', help='启用单股推送模式')
    parser.add_argument('--workers', type=int, default=None, help='并发数')
    parser.add_argument('--schedule', action='store_true', help='启用定时任务模式')
    parser.add_argument('--no-run-immediately', action='store_true', help='定时任务启动时不立即执行一次')
    parser.add_argument('--market-review', action='store_true', help='仅运行大盘复盘')
    parser.add_argument('--no-market-review', action='store_true', help='跳过大盘复盘')
    parser.add_argument('--force-run', action='store_true', help='强制执行分析')
    parser.add_argument('--no-context-snapshot', action='store_true', help='不保存上下文快照')
    parser.add_argument('--backtest', action='store_true', help='运行回测')
    parser.add_argument('--backtest-code', type=str, help='指定回测代码')
    parser.add_argument('--backtest-days', type=int, help='回测天数')
    parser.add_argument('--backtest-force', action='store_true', help='强制回测')
    return parser.parse_args()


def _compute_trading_day_filter(config: Config, args: argparse.Namespace, stock_codes: List[str]):
    force_run = getattr(args, 'force_run', False)
    if force_run or not getattr(config, 'trading_day_check_enabled', True):
        return (stock_codes, None, False)

    from src.core.trading_calendar import get_market_for_stock, get_open_markets_today, compute_effective_region
    open_markets = get_open_markets_today()
    filtered_codes = [c for c in stock_codes if get_market_for_stock(c) in open_markets or get_market_for_stock(c) is None]

    effective_region = None
    if config.market_review_enabled and not getattr(args, 'no_market_review', False):
        effective_region = compute_effective_region(getattr(config, 'market_review_region', 'cn') or 'cn', open_markets)

    should_skip_all = (not filtered_codes) and (effective_region or '') == ''
    return (filtered_codes, effective_region, should_skip_all)


async def run_full_analysis(config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]] = None):
    """异步执行完整流程"""
    try:
        if stock_codes is None: config.refresh_stock_list()
        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(config, args, effective_codes)

        # 允许非交易日手动运行大盘复盘
        is_manual_market_review = getattr(args, 'market_review', False)
        if should_skip and not is_manual_market_review:
            logger.info("今日非交易日，跳过执行。")
            return

        # 如果是手动运行大盘复盘且处于非交易日，强制指定有效区域
        if is_manual_market_review and (effective_region or '') == '':
            effective_region = getattr(config, 'market_review_region', 'cn') or 'cn'

        stock_codes = filtered_codes
        if getattr(args, 'single_notify', False): config.single_stock_notify = True

        merge_notification = (getattr(config, 'merge_email_notification', False)
                            and config.market_review_enabled
                            and not getattr(args, 'no_market_review', False)
                            and not config.single_stock_notify)

        pipeline = StockAnalysisPipeline(
            config=config, max_workers=args.workers, query_id=uuid.uuid4().hex,
            query_source="cli", save_context_snapshot=not getattr(args, 'no_context_snapshot', False)
        )

        # 1. 个股分析 (Async)
        results = await pipeline.run(
            stock_codes=stock_codes, dry_run=args.dry_run,
            send_notification=not args.no_notify, merge_notification=merge_notification
        )

        if results and not args.dry_run:
            date_str = datetime.now().strftime("%Y%m%d")
            report_text = pipeline.notifier.generate_dashboard_report(results)
            report_filename = f"stock_analysis_{date_str}.md"
            filepath = pipeline.notifier.save_report_to_file(report_text, report_filename)
            logger.info("个股分析报告已保存: %s", filepath)

        analysis_delay = getattr(config, 'analysis_delay', 0)
        if analysis_delay > 0 and config.market_review_enabled and not args.no_market_review:
            await asyncio.sleep(analysis_delay)

        # 2. 大盘复盘 (Async)
        market_report = ""
        if config.market_review_enabled and not args.no_market_review and effective_region != '':
            market_report = await run_market_review(
                notifier=pipeline.notifier, analyzer=pipeline.analyzer,
                search_service=pipeline.search_service, send_notification=not args.no_notify,
                merge_notification=merge_notification, override_region=effective_region
            ) or ""

        # 3. 合并推送
        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report: parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard = pipeline.notifier.generate_aggregate_report(results, getattr(config, 'report_type', 'simple'))
                parts.append(f"# 🚀 个股决策仪表盘\n\n{dashboard}")
            if parts:
                combined = "\n\n---\n\n".join(parts)
                await pipeline.notifier.send(combined, email_send_to_all=True)

        # 4. 飞书文档 (wrap sync)
        try:
            from src.feishu_doc import FeishuDocManager
            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report):
                tz_cn = timezone(timedelta(hours=8))
                doc_title = f"{datetime.now(tz_cn).strftime('%Y-%m-%d %H:%M')} 大盘复盘"
                full_content = (f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n" if market_report else "")
                if results:
                    full_content += f"# 🚀 个股决策仪表盘\n\n{pipeline.notifier.generate_aggregate_report(results, getattr(config, 'report_type', 'simple'))}"
                doc_url = await asyncio.to_thread(feishu_doc.create_daily_doc, doc_title, full_content)
                if doc_url and not args.no_notify:
                    await pipeline.notifier.send(f"复盘文档已生成: {doc_url}")
        except Exception as e: logger.error(f"飞书文档生成失败: {e}")

        # 5. 自动回测 (wrap sync)
        if getattr(config, 'backtest_enabled', False):
            try:
                from src.services.backtest_service import BacktestService
                service = BacktestService()
                await asyncio.to_thread(service.run_backtest)
            except Exception as e: logger.warning(f"自动回测失败: {e}")

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")


def start_bot_stream_clients(config: Config) -> None:
    """启动 Stream 机器人 (Retained sync background tasks)"""
    if config.dingtalk_stream_enabled:
        from bot.platforms import start_dingtalk_stream_background
        start_dingtalk_stream_background()
    if getattr(config, 'feishu_stream_enabled', False):
        from bot.platforms import start_feishu_stream_background
        start_feishu_stream_background()


async def main_async() -> int:
    args = parse_arguments()
    config = get_config()
    setup_logging(log_prefix="stock_analysis", debug=args.debug, log_dir=config.log_dir)

    logger.info("=" * 40 + " 系统启动 (Async) " + "=" * 40)
    config.validate()
    stock_codes = _parse_cli_stock_codes(args)

    start_bot_stream_clients(config)

    try:
        # 回测模式
        if getattr(args, 'backtest', False):
            from src.services.backtest_service import BacktestService
            service = BacktestService()
            await asyncio.to_thread(service.run_backtest, getattr(args, 'backtest_code', None))
            return 0

        # 定时模式
        if args.schedule or config.schedule_enabled:
            _warn_schedule_stock_override(args)
            from src.scheduler import run_with_schedule_async
            await run_with_schedule_async(
                task=_build_schedule_task(config, args),
                schedule_time=config.schedule_time,
                run_immediately=_resolve_schedule_run_immediately(config, args)
            )
            return 0

        # 单次运行
        if config.run_immediately or args.market_review:
            await run_full_analysis(config, args, stock_codes)

        return 0
    except KeyboardInterrupt: return 130
    except Exception as e:
        logger.exception(f"执行失败: {e}")
        return 1


async def _run_single_shot_with_cleanup(coro) -> int:
    try:
        await coro
        return 0
    finally:
        await _cleanup()


def main() -> int:
    """向后兼容的同步入口。"""
    args = parse_arguments()
    config = get_config()
    setup_logging(log_prefix="stock_analysis", debug=args.debug, log_dir=config.log_dir)

    logger.info("=" * 40 + " 系统启动 " + "=" * 40)
    config.validate()
    stock_codes = _parse_cli_stock_codes(args)

    start_bot_stream_clients(config)

    try:
        if getattr(args, 'backtest', False):
            from src.services.backtest_service import BacktestService

            service = BacktestService()
            service.run_backtest(getattr(args, 'backtest_code', None))
            return 0

        if args.schedule or config.schedule_enabled:
            _warn_schedule_stock_override(args)
            from src.scheduler import run_with_schedule

            run_with_schedule(
                task=_build_schedule_task(config, args),
                schedule_time=config.schedule_time,
                run_immediately=_resolve_schedule_run_immediately(config, args),
            )
            return 0

        if config.run_immediately or args.market_review:
            result = run_full_analysis(config, args, stock_codes)
            if asyncio.iscoroutine(result):
                return asyncio.run(_run_single_shot_with_cleanup(result))

        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.exception(f"执行失败: {e}")
        return 1


async def _cleanup():
    """Shutdown hook: close all shared resources to avoid ResourceWarning."""
    # 1. Close shared async HTTP client
    try:
        from src.utils.async_http import AsyncHttpClientManager
        await AsyncHttpClientManager().close()
    except Exception as e:
        logger.debug(f"AsyncHttpClient cleanup: {e}")

    # 2. Close database engine (dispose pool + checked-out connections)
    try:
        from src.storage import StorageManager
        mgr = StorageManager.get_instance()
        if hasattr(mgr, '_engine') and mgr._engine is not None:
            mgr._engine.dispose(close=True)
    except Exception as e:
        logger.debug(f"Database cleanup: {e}")

    # 3. Cancel all pending asyncio tasks (except current) to prevent blocking
    try:
        current_task = asyncio.current_task()
        tasks = [t for t in asyncio.all_tasks() if t is not current_task]
        if tasks:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.debug(f"Task cleanup: {e}")

    # 4. Give LiteLLM's background workers time to shut down gracefully
    await asyncio.sleep(0.5)


async def _async_main_wrapper() -> int:
    """Top-level entry with guaranteed cleanup."""
    try:
        return await main_async()
    finally:
        await _cleanup()


if __name__ == "__main__":
    sys.exit(main())
