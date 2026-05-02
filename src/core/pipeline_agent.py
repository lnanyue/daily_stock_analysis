# -*- coding: utf-8 -*-
"""Agent analysis helpers for ``StockAnalysisPipeline``."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from src.analyzer import (
    AnalysisResult,
    build_chief_synthesizer_prompt,
    format_expert_instruction,
    get_persona_system_prompt,
)
from src.analyzer.prompt_builder import format_analysis_prompt
from src.analyzer.utils import build_market_snapshot
from src.report_language import normalize_report_language
from src.schemas.analysis_result import apply_placeholder_fill, check_content_integrity

logger = logging.getLogger(__name__)


def _quote_value(quote: Any, field: str) -> Any:
    value = getattr(quote, field, None)
    if value is None and isinstance(quote, dict):
        value = quote.get(field)
    return value


async def run_agent_analysis(
    *,
    code: str,
    report_type: Any,
    query_id: str,
    config: Any,
    analyzer: Any,
    db: Any,
    search_service: Any,
    emit_progress: Callable[[int, str], None],
    enhance_context: Callable[..., Dict[str, Any]],
    stock_name: Optional[str] = None,
    realtime_quote: Any = None,
    chip_data: Any = None,
    fundamental_context: Optional[Dict[str, Any]] = None,
    trend_result: Any = None,
    today_k: Optional[Dict[str, Any]] = None,
    yesterday_k: Optional[Dict[str, Any]] = None,
    peer_comparison: Optional[Dict[str, Any]] = None,
    news_context: str = "",
    route_reasons: Optional[List[str]] = None,
) -> Optional[AnalysisResult]:
    route_suffix = f" ({', '.join(route_reasons)})" if route_reasons else ""
    logger.info("[%s] 正在执行混合 Agent 分析（单 LLM 调用）%s...", code, route_suffix)
    emit_progress(62, f"{stock_name}：正在生成分析 Prompt")

    prompt_name = stock_name or code
    report_language = normalize_report_language(getattr(config, "report_language", "zh"))
    base_context = {
        "code": code,
        "stock_name": prompt_name,
        "date": date.today().isoformat(),
        "today": today_k or {},
        "yesterday": yesterday_k or {},
    }
    enhanced_context = enhance_context(
        base_context,
        realtime_quote,
        chip_data,
        trend_result,
        stock_name,
        fundamental_context,
        None,
        peer_comparison,
    )

    emit_progress(68, f"{stock_name}：专家组正在并行会诊")

    async def _run_expert(persona: str) -> str:
        expert_prompt = format_analysis_prompt(
            context=enhanced_context,
            name=prompt_name,
            news_context=news_context if persona == "risk" else None,
            report_language=report_language,
            use_legacy_default_prompt=False,
            output_format="standard",
        )
        expert_prompt += "\n\n" + format_expert_instruction(persona, prompt_name, code, report_language)
        try:
            out, _, _ = await analyzer._call_litellm_async(
                expert_prompt,
                {"max_tokens": 2048, "temperature": 0.3},
                system_prompt=get_persona_system_prompt(persona, report_language),
            )
            return out
        except Exception as exc:
            logger.warning("[%s] %s 专家调用失败: %s", code, persona, exc)
            return f"Analysis failed: {exc}"

    expert_results = await asyncio.gather(
        _run_expert("technical"),
        _run_expert("risk"),
        return_exceptions=True,
    )
    expert_map = {
        "technical": expert_results[0] if not isinstance(expert_results[0], Exception) else str(expert_results[0]),
        "risk": expert_results[1] if not isinstance(expert_results[1], Exception) else str(expert_results[1]),
    }

    emit_progress(82, f"{stock_name}：首席策略师正在汇总")
    synthesis_prompt = build_chief_synthesizer_prompt(
        enhanced_context,
        expert_map,
        prompt_name,
        report_language,
    )

    model_used = "unknown"
    try:
        response_text, model_used, _ = await analyzer._call_litellm_async(
            synthesis_prompt,
            {"max_tokens": 8192, "temperature": getattr(config, "llm_temperature", 0.7)},
            system_prompt=get_persona_system_prompt("chief", report_language),
        )
    except Exception as exc:
        logger.error("[%s] 首席策略师 LLM 调用失败: %s", code, exc)
        return AnalysisResult(
            code=code,
            name=prompt_name or code,
            sentiment_score=50,
            trend_prediction="震荡",
            operation_advice="观望",
            decision_type="hold",
            confidence_level="中",
            analysis_summary=f"分析汇总失败: {exc}",
            success=False,
            error_message=str(exc),
            query_id=query_id,
            data_sources=f"multi-agent:{model_used}",
        )

    emit_progress(88, f"{stock_name}：正在生成最终决策")
    from src.agent.fact_checker import FactChecker

    checker = FactChecker(enhanced_context)
    result: Optional[AnalysisResult] = None
    max_correction_attempts = 1
    for attempt in range(max_correction_attempts + 1):
        try:
            result = analyzer._parse_response(response_text, code, prompt_name)
            if result.analysis_metadata is None:
                result.analysis_metadata = {}

            passed, fact_issues = checker.verify(result)
            if passed:
                break

            if attempt < max_correction_attempts:
                logger.warning(
                    "[%s] 事实核查失败，触发 AI 自我修正 (%d/%d): %s",
                    code,
                    attempt + 1,
                    max_correction_attempts,
                    fact_issues,
                )
                emit_progress(91, f"{stock_name}：检测到数据幻觉，正在修正")
                correction_prompt = synthesis_prompt + "\n\n" + checker.build_correction_prompt(
                    fact_issues,
                    report_language,
                )
                response_text, _, _ = await analyzer._call_litellm_async(
                    correction_prompt,
                    {"max_tokens": 8192, "temperature": 0.2},
                    system_prompt=get_persona_system_prompt("chief", report_language),
                )
            else:
                logger.error("[%s] 事实核查最终失败，使用代码兜底覆盖: %s", code, fact_issues)
                result.analysis_metadata.setdefault("fact_check", {})["status"] = "failed_and_overridden"
                result.analysis_metadata["fact_check"]["issues"] = fact_issues
        except Exception as exc:
            logger.error("[%s] 结果解析或事实核查异常: %s", code, exc)
            if attempt >= max_correction_attempts:
                return analyzer._make_error_result(code, prompt_name, f"核查系统故障: {exc}")

    if result is None:
        return analyzer._make_error_result(code, prompt_name, "核查系统未返回有效结果")

    if result.analysis_metadata is None:
        result.analysis_metadata = {}
    result.query_id = query_id
    result.model_used = model_used
    result.report_language = report_language
    result.historical_performance = enhanced_context.get("historical_performance")
    result.peer_comparison = peer_comparison
    result.data_sources = f"multi-agent:{model_used or 'unknown'}" + (
        f"({','.join(route_reasons)})" if route_reasons else ""
    )
    result.analysis_metadata.update({
        "agent_route": {
            "used_agent": True,
            "selection_source": "forced"
            if (route_reasons and any(reason.startswith("config:") for reason in route_reasons))
            else "auto",
            "reasons": route_reasons or [],
            "arch": "multi-agent",
            "mode": "concurrent",
        },
        "agent_runtime": {
            "arch": "multi-agent",
            "success": True,
            "model": model_used or "",
            "provider": (model_used or "").split("/")[0] if model_used else "",
        },
    })

    if realtime_quote:
        quote_price = _quote_value(realtime_quote, "price")
        if quote_price is not None:
            result.current_price = quote_price
        quote_change = _quote_value(realtime_quote, "change_pct")
        if quote_change is not None:
            result.change_pct = quote_change
    result.market_snapshot = build_market_snapshot(enhanced_context)

    passed, missing_fields = check_content_integrity(result)
    if not passed:
        logger.warning("[%s] 混合 Agent 结果完整性检查未通过，不足字段: %s", code, missing_fields)
        apply_placeholder_fill(result, missing_fields)

    emit_progress(94, f"{stock_name}：正在保存分析结果")
    await db.save_analysis_history_async(
        result,
        query_id,
        getattr(report_type, "value", str(report_type)),
        news_context,
        None,
        None,
        getattr(config, "save_context_snapshot", False),
    )

    if result and getattr(search_service, "is_available", False):
        try:
            news_response = search_service.search_stock_news(
                stock_code=code,
                stock_name=result.name,
                max_results=5,
            )
            news_items = getattr(news_response, "results", None) or []
            if news_items:
                try:
                    db.save_news_intel(
                        news_items=news_items,
                        code=code,
                        name=result.name,
                        query_id=query_id,
                    )
                except TypeError:
                    db.save_news_intel(news_items)
                    logger.debug("[%s] 新闻保存使用兼容签名", code)
        except Exception:
            logger.debug("[%s] 混合 Agent 新闻持久化跳过", code, exc_info=True)

    logger.info("[%s] 混合 Agent 分析完成，评分: %s", code, result.sentiment_score)
    return result
