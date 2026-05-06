# -*- coding: utf-8 -*-
"""
应用模式运行函数 —— main.py 模式路由的"身体"。

每个公开函数对应一种运行模式，由 ``main()`` 的 if/else 分发：
- ``run_backtest``
- ``run_market_review_only``
- ``run_schedule_mode``
- ``run_full_analysis``（4 种模式共享的异步编排）
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from data_provider import canonical_stock_code
from src.config import get_config, Config
from src.config.env import _INITIAL_PROCESS_ENV, reload_runtime_config
from src.core.lifecycle import run_with_cleanup
from src.core.portfolio import run_portfolio_aggregation

logger = logging.getLogger(__name__)


# ── 模式路由的具体实现 ──────────────────────────────────────────


def run_backtest(backtest_code: Optional[str] = None) -> int:
    """回测模式。"""
    from src.services.backtest_service import BacktestService
    service = BacktestService()
    service.run_backtest(backtest_code)
    return 0


async def run_market_review_only(config: Config, args) -> int:
    """仅执行大盘复盘。"""
    import asyncio

    effective_region = _compute_market_review_region(config, args)
    if effective_region is None:
        return 0

    logger.info("模式: 仅大盘复盘")
    notifier = _build_notifier()
    search_service = _build_search_service(config)
    analyzer = _build_analyzer_for_review(config)

    from src.core.market_review import run_market_review as do_market_review

    result = do_market_review(
        notifier=notifier,
        analyzer=analyzer,
        search_service=search_service,
        send_notification=not getattr(args, "no_notify", False),
        override_region=effective_region,
    )
    if asyncio.iscoroutine(result):
        from src.core.lifecycle import cleanup
        try:
            await result
            return 0
        finally:
            await cleanup()
    return 0


def run_schedule_mode(config: Config, args) -> int:
    """定时调度模式。"""
    from src.scheduler import run_with_schedule
    from src.config.env import reload_runtime_config

    stock_codes = _parse_cli_stock_codes(args) if getattr(args, "stocks", None) else None
    scheduled_stock_codes = _resolve_scheduled_stock_codes(stock_codes)

    logger.info("每日执行时间: %s", config.schedule_time)
    should_run_immediately = config.schedule_run_immediately
    if getattr(args, "no_run_immediately", False):
        should_run_immediately = False
    logger.info("启动时立即执行: %s", should_run_immediately)

    schedule_time_provider = _build_schedule_time_provider(config.schedule_time)

    def scheduled_task():
        runtime_config = reload_runtime_config()
        result = run_full_analysis(runtime_config, args, scheduled_stock_codes)
        if asyncio.iscoroutine(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(run_with_cleanup(result))
        return result

    background_tasks = _build_background_tasks(config)

    run_with_schedule(
        task=scheduled_task,
        schedule_time=config.schedule_time,
        run_immediately=should_run_immediately,
        background_tasks=background_tasks,
        schedule_time_provider=schedule_time_provider,
    )
    return 0


def start_bot_stream_clients(config: Config) -> None:
    """启动 Stream 机器人（后台线程）。"""
    if config.dingtalk_stream_enabled:
        from bot.platforms import start_dingtalk_stream_background
        start_dingtalk_stream_background()
    if getattr(config, "feishu_stream_enabled", False):
        from bot.platforms import start_feishu_stream_background
        start_feishu_stream_background()


# ── 完整分析编排 ────────────────────────────────────────────────


async def run_full_analysis(
    config: Config,
    args,
    stock_codes: Optional[List[str]] = None,
) -> None:
    """执行完整的分析流程（个股分析 + 大盘复盘）。

    这是定时任务和单次 CLI 运行共同调用的异步编排函数。
    """
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

    try:
        if stock_codes is None:
            config.refresh_stock_list()
        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config, args, effective_codes,
        )

        is_manual_market_review = getattr(args, "market_review", False)
        if should_skip and not is_manual_market_review:
            logger.info("今日非交易日，跳过执行。")
            return

        if is_manual_market_review and (effective_region or "") == "":
            effective_region = getattr(config, "market_review_region", "cn") or "cn"

        stock_codes_to_use = filtered_codes
        if getattr(args, "single_notify", False):
            config.single_stock_notify = True

        merge_notification = (
            getattr(config, "merge_email_notification", False)
            and config.market_review_enabled
            and not getattr(args, "no_market_review", False)
            and not config.single_stock_notify
        )

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
            config=config,
            max_workers=args.workers,
            query_id=uuid.uuid4().hex,
            query_source="cli",
            save_context_snapshot=not getattr(args, "no_context_snapshot", False),
            progress_callback=cli_progress_callback,
        )

        # 1. 个股分析
        results = await pipeline.run(
            stock_codes=stock_codes_to_use,
            dry_run=args.dry_run,
            send_notification=not args.no_notify,
            merge_notification=merge_notification,
        )

        portfolio_summary = None
        report_text = ""

        if results and not args.dry_run:
            date_str = datetime.now().strftime("%Y%m%d")
            report_text = pipeline.notifier.generate_dashboard_report(results)

            # 1b. 组合综述（Portfolio Aggregation）
            portfolio_summary = await run_portfolio_aggregation(
                pipeline.analyzer, results,
            )
            if portfolio_summary:
                report_text += f"\n\n## 📊 组合综述\n\n{portfolio_summary}"
                logger.info("组合综述已生成")

            report_filename = f"stock_analysis_{date_str}.md"
            filepath = pipeline.notifier.save_report_to_file(report_text, report_filename)
            logger.info("个股分析报告已保存: %s", filepath)

        analysis_delay = getattr(config, "analysis_delay", 0)
        if analysis_delay > 0 and config.market_review_enabled and not args.no_market_review:
            await asyncio.sleep(analysis_delay)

        # 2. 大盘复盘
        market_report = ""
        if config.market_review_enabled and not args.no_market_review and effective_region != "":
            market_report = (
                await run_market_review(
                    notifier=pipeline.notifier,
                    analyzer=pipeline.analyzer,
                    search_service=pipeline.search_service,
                    send_notification=not args.no_notify,
                    merge_notification=merge_notification,
                    override_region=effective_region,
                )
                or ""
            )

        # 3. 合并推送
        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# \U0001f4c8 大盘复盘\n\n{market_report}")
            if results:
                dashboard = pipeline.notifier.generate_aggregate_report(
                    results, getattr(config, "report_type", "simple"),
                )
                parts.append(f"# \U0001f680 个股决策仪表盘\n\n{dashboard}")
            if portfolio_summary:
                parts.append(f"# \U0001f4ca 组合综述\n\n{portfolio_summary}")
            if parts:
                combined = "\n\n---\n\n".join(parts)
                await pipeline.notifier.send(combined, email_send_to_all=True)

        # 4. 飞书文档
        try:
            from src.feishu_doc import FeishuDocManager
            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report):
                tz_cn = timezone(timedelta(hours=8))
                doc_title = f"{datetime.now(tz_cn).strftime('%Y-%m-%d %H:%M')} 大盘复盘"
                full_content = (
                    f"# \U0001f4c8 大盘复盘\n\n{market_report}\n\n---\n\n"
                    if market_report
                    else ""
                )
                if results:
                    full_content += (
                        f"# \U0001f680 个股决策仪表盘\n\n"
                        f"{pipeline.notifier.generate_aggregate_report(results, getattr(config, 'report_type', 'simple'))}"
                    )
                if portfolio_summary:
                    full_content += f"\n\n# \U0001f4ca 组合综述\n\n{portfolio_summary}"
                doc_url = await asyncio.to_thread(feishu_doc.create_daily_doc, doc_title, full_content)
                if doc_url and not args.no_notify:
                    await pipeline.notifier.send(f"复盘文档已生成: {doc_url}")
        except Exception as e:
            logger.error("飞书文档生成失败: %s", e)

        # 5. 自动回测
        if getattr(config, "backtest_enabled", False):
            try:
                from src.services.backtest_service import BacktestService
                service = BacktestService()
                await asyncio.to_thread(service.run_backtest)
            except Exception as e:
                logger.warning("自动回测失败: %s", e)

    except Exception as e:
        logger.exception("分析流程执行失败: %s", e)


# ── 辅助函数 ──────────────────────────────────────────────────────


def _parse_cli_stock_codes(args) -> Optional[List[str]]:
    if not getattr(args, "stocks", None):
        return None
    return [canonical_stock_code(c) for c in args.stocks.split(",")]


def _compute_trading_day_filter(
    config: Config, args, stock_codes: List[str],
) -> Tuple[List[str], Optional[str], bool]:
    """根据交易日历过滤股票代码并计算复盘区域。"""
    force_run = getattr(args, "force_run", False)
    if force_run or not getattr(config, "trading_day_check_enabled", True):
        return (stock_codes, None, False)

    from src.core.trading_calendar import get_market_for_stock, get_open_markets_today
    from src.core.trading_calendar import compute_effective_region as _compute_region

    open_markets = get_open_markets_today()
    filtered_codes = [
        c for c in stock_codes
        if get_market_for_stock(c) in open_markets or get_market_for_stock(c) is None
    ]

    effective_region: Optional[str] = None
    if config.market_review_enabled and not getattr(args, "no_market_review", False):
        effective_region = _compute_region(
            getattr(config, "market_review_region", "cn") or "cn",
            open_markets,
        )

    should_skip_all = (not filtered_codes) and (effective_region or "") == ""
    return (filtered_codes, effective_region, should_skip_all)


def _compute_market_review_region(config: Config, args) -> Optional[str]:
    """计算大盘复盘模式下的有效区域。返回 None 表示跳过。"""
    if not getattr(args, "force_run", False) and getattr(config, "trading_day_check_enabled", True):
        from src.core.trading_calendar import get_open_markets_today
        from src.core.trading_calendar import compute_effective_region as _compute_region

        open_markets = get_open_markets_today()
        effective_region = _compute_region(
            getattr(config, "market_review_region", "cn") or "cn",
            open_markets,
        )
        if effective_region == "":
            logger.info(
                "今日大盘复盘相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。"
            )
            return None
        return effective_region

    region = getattr(config, "market_review_region", "cn") or "cn"
    return region


def _build_notifier():
    """构造通知服务。"""
    from src.notification import NotificationService
    return NotificationService()


def _build_search_service(config: Config):
    """构造搜索服务（如果已配置）。"""
    has_search = getattr(config, "has_search_capability_enabled", None)
    if callable(has_search) and has_search():
        from src.search_service import get_search_service
        return get_search_service()
    return None


def _build_analyzer_for_review(config: Config):
    """构造大盘复盘专用的 AI 分析器。"""
    from src.analyzer import GeminiAnalyzer

    if getattr(config, "gemini_api_key", None) or getattr(config, "openai_api_key", None):
        analyzer = GeminiAnalyzer(api_key=getattr(config, "gemini_api_key", None))
        if not analyzer.is_available():
            logger.warning("AI 分析器初始化后不可用，请检查 API Key 配置")
            return None
        return analyzer
    else:
        logger.warning("未检测到 API Key (Gemini/OpenAI)，将仅使用模板生成报告")
        return None


def _resolve_scheduled_stock_codes(stock_codes: Optional[List[str]]) -> Optional[List[str]]:
    """定时模式应始终读取最新的持久化自选股列表。"""
    if stock_codes is not None:
        logger.warning(
            "定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，"
            "并在每次运行前重新读取最新的 STOCK_LIST。"
        )
    return None


def _build_schedule_time_provider(default_schedule_time: str) -> Callable[[], str]:
    """构造定时时间提供者闭包。

    优先级：进程环境变量 > 持久化配置文件（WebUI 写入）> 系统默认 ``"18:00"``。
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


def _build_background_tasks(config: Config) -> List[Dict[str, Any]]:
    """构造定时模式下的后台周期任务列表。"""
    background_tasks: List[Dict[str, Any]] = []

    if getattr(config, "agent_event_monitor_enabled", False):
        from src.agent.events import build_event_monitor_from_config, run_event_monitor_once

        monitor = build_event_monitor_from_config(config)
        if monitor is not None:
            interval_minutes = max(1, getattr(config, "agent_event_monitor_interval_minutes", 5))

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

    return background_tasks
