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

from pathlib import Path
from typing import Dict, Optional

from dotenv import dotenv_values
from src.config import setup_env

_INITIAL_PROCESS_ENV = dict(os.environ)
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
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

from data_provider import canonical_stock_code
from src.core.pipeline import StockAnalysisPipeline
from src.core.market_review import run_market_review
from src.config import get_config, Config
from src.logging_config import setup_logging


logger = logging.getLogger(__name__)
_RUNTIME_ENV_FILE_KEYS = set()


def _get_active_env_path() -> Path:
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parent / ".env"


def _read_active_env_values() -> Optional[Dict[str, str]]:
    env_path = _get_active_env_path()
    if not env_path.exists():
        return {}

    try:
        values = dotenv_values(env_path)
    except Exception as exc:  # pragma: no cover - defensive branch
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

# setup_env() already ran at import time above.
_env_bootstrapped = True


def _bootstrap_environment() -> None:
    """Load .env and apply optional local proxy settings.

    Guarded to be idempotent so it can safely be called from lazy-import
    paths used by API / bot consumers.
    """
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


def _setup_bootstrap_logging(debug: bool = False) -> None:
    """Initialize stderr-only logging before config is loaded.

    File handlers are deferred until ``config.log_dir`` is known (via the
    subsequent ``setup_logging()`` call) so that healthy runs never create
    log files in a hard-coded directory.
    """
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


def _get_stock_analysis_pipeline():
    """Lazily import StockAnalysisPipeline for external consumers.

    Also ensures env/proxy bootstrap has run so that API / bot consumers
    that never call ``main()`` still get ``USE_PROXY`` applied.
    """
    _bootstrap_environment()
    from src.core.pipeline import StockAnalysisPipeline as _Pipeline

    return _Pipeline


class _LazyPipelineDescriptor:
    """Descriptor that resolves StockAnalysisPipeline on first attribute access."""

    _resolved = None

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if self._resolved is None:
            self._resolved = _get_stock_analysis_pipeline()
        return self._resolved


class _ModuleExports:
    StockAnalysisPipeline = _LazyPipelineDescriptor()


_exports = _ModuleExports()


def __getattr__(name: str):
    if name == "StockAnalysisPipeline":
        return _exports.StockAnalysisPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reload_env_file_values_preserving_overrides() -> None:
    """Refresh `.env`-managed env vars without clobbering process env overrides."""
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


async def run_full_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None
):
    """
    执行完整的分析流程（个股 + 大盘复盘）

    这是定时任务调用的主函数
    """
    # Import pipeline modules outside the broad try/except so that import-time
    # failures propagate to the caller instead of being silently swallowed.
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

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

        def cli_progress_callback(progress, message=None):
            if isinstance(progress, dict):
                p = progress.get("progress", 0)
                m = progress.get("progress_message", "")
            else:
                p = progress
                m = message
            if m:
                print(f"➜ [{p}%] {m}")

        pipeline = StockAnalysisPipeline(
            config=config, max_workers=args.workers, query_id=uuid.uuid4().hex,
            query_source="cli", save_context_snapshot=not getattr(args, 'no_context_snapshot', False),
            progress_callback=cli_progress_callback
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


def _is_truthy_env(var_name: str, default: str = "true") -> bool:
    """Parse common truthy / falsy environment values."""
    value = os.getenv(var_name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}


def start_bot_stream_clients(config: Config) -> None:
    """启动 Stream 机器人 (Retained sync background tasks)"""
    if config.dingtalk_stream_enabled:
        from bot.platforms import start_dingtalk_stream_background
        start_dingtalk_stream_background()
    if getattr(config, 'feishu_stream_enabled', False):
        from bot.platforms import start_feishu_stream_background
        start_feishu_stream_background()


def _resolve_scheduled_stock_codes(stock_codes: Optional[List[str]]) -> Optional[List[str]]:
    """Scheduled runs should always read the latest persisted watchlist."""
    if stock_codes is not None:
        logger.warning(
            "定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，并在每次运行前重新读取最新的 STOCK_LIST。"
        )
    return None


def _reload_runtime_config() -> Config:
    """Reload config from the latest persisted `.env` values for scheduled runs."""
    _reload_env_file_values_preserving_overrides()
    Config.reset_instance()
    return get_config()


def _build_schedule_time_provider(default_schedule_time: str):
    """Read the latest schedule time directly from the active config file.

    Fallback order:
    1. Process-level env override (set before launch) → honour it.
    2. Persisted config file value (written by WebUI) → use it.
    3. Documented system default ``"18:00"`` → always fall back here so
       that clearing SCHEDULE_TIME in WebUI correctly resets the schedule.
    """
    from src.core.config_manager import ConfigManager

    _SYSTEM_DEFAULT_SCHEDULE_TIME = "18:00"
    manager = ConfigManager()

    def _provider() -> str:
        if "SCHEDULE_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("SCHEDULE_TIME", default_schedule_time)

        config_map = manager.read_config_map()
        schedule_time = (config_map.get("SCHEDULE_TIME", "") or "").strip()
        if schedule_time:
            return schedule_time
        return _SYSTEM_DEFAULT_SCHEDULE_TIME

    return _provider


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

        if args.market_review:
            from src.analyzer import GeminiAnalyzer
            from src.core.market_review import run_market_review
            from src.notification import NotificationService
            from src.search_service import SearchService

            effective_region = None
            if not getattr(args, 'force_run', False) and getattr(config, 'trading_day_check_enabled', True):
                from src.core.trading_calendar import get_open_markets_today, compute_effective_region as _compute_region
                open_markets = get_open_markets_today()
                effective_region = _compute_region(
                    getattr(config, 'market_review_region', 'cn') or 'cn',
                    open_markets,
                )
                if effective_region == '':
                    logger.info("今日大盘复盘相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。")
                    return 0

            logger.info("模式: 仅大盘复盘")
            notifier = NotificationService()
            search_service = None
            has_search = getattr(config, "has_search_capability_enabled", None)
            if callable(has_search) and has_search():
                from src.search_service import get_search_service
                search_service = get_search_service()

            analyzer = None
            if getattr(config, "gemini_api_key", None) or getattr(config, "openai_api_key", None):
                analyzer = GeminiAnalyzer(api_key=getattr(config, "gemini_api_key", None))
                if not analyzer.is_available():
                    logger.warning("AI 分析器初始化后不可用，请检查 API Key 配置")
                    analyzer = None
            else:
                logger.warning("未检测到 API Key (Gemini/OpenAI)，将仅使用模板生成报告")

            result = run_market_review(
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
                send_notification=not args.no_notify,
                override_region=effective_region,
            )
            if asyncio.iscoroutine(result):
                return asyncio.run(_run_single_shot_with_cleanup(result))
            return 0

        if args.schedule or config.schedule_enabled:
            _warn_schedule_stock_override(args)
            logger.info(f"每日执行时间: {config.schedule_time}")

            # Determine whether to run immediately:
            # Command line arg --no-run-immediately overrides config if present.
            # Otherwise use config (defaults to True).
            should_run_immediately = config.schedule_run_immediately
            if getattr(args, 'no_run_immediately', False):
                should_run_immediately = False

            logger.info(f"启动时立即执行: {should_run_immediately}")

            from src.scheduler import run_with_schedule
            scheduled_stock_codes = _resolve_scheduled_stock_codes(stock_codes)
            schedule_time_provider = _build_schedule_time_provider(config.schedule_time)

            def scheduled_task():
                runtime_config = _reload_runtime_config()
                result = run_full_analysis(runtime_config, args, scheduled_stock_codes)
                if asyncio.iscoroutine(result):
                    try:
                        asyncio.get_running_loop()
                    except RuntimeError:
                        return asyncio.run(_run_single_shot_with_cleanup(result))
                return result

            background_tasks = []
            if getattr(config, 'agent_event_monitor_enabled', False):
                from src.agent.events import build_event_monitor_from_config, run_event_monitor_once

                monitor = build_event_monitor_from_config(config)
                if monitor is not None:
                    interval_minutes = max(1, getattr(config, 'agent_event_monitor_interval_minutes', 5))

                    def event_monitor_task():
                        triggered = run_event_monitor_once(monitor)
                        if triggered:
                            logger.info("[EventMonitor] 本轮触发 %d 条提醒", len(triggered))

                    background_tasks.append({
                        "task": event_monitor_task,
                        "interval_seconds": interval_minutes * 60,
                        "run_immediately": True,
                        "name": "agent_event_monitor",
                    })
                else:
                    logger.info("EventMonitor 已启用，但未加载到有效规则，跳过后台提醒任务")

            run_with_schedule(
                task=scheduled_task,
                schedule_time=config.schedule_time,
                run_immediately=should_run_immediately,
                background_tasks=background_tasks,
                schedule_time_provider=schedule_time_provider,
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
    # 0. Flush/stop LiteLLM background logging worker before tearing down the loop.
    try:
        from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

        if GLOBAL_LOGGING_WORKER is not None:
            try:
                await asyncio.wait_for(GLOBAL_LOGGING_WORKER.flush(), timeout=1.0)
            except Exception as e:
                logger.debug(f"LiteLLM logging flush cleanup: {e}")

            try:
                await asyncio.wait_for(GLOBAL_LOGGING_WORKER.stop(), timeout=1.0)
            except Exception as e:
                logger.debug(f"LiteLLM logging stop cleanup: {e}")
    except Exception as e:
        logger.debug(f"LiteLLM worker cleanup: {e}")

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


if __name__ == "__main__":
    sys.exit(main())
