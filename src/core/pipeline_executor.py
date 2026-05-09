"""
Analysis execution — extracted from StockAnalysisPipeline.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Callable

from src.config import Config
from src.analyzer import (
    AnalysisResult,
    fill_chip_structure_if_needed,
    fill_price_position_if_needed,
    format_analysis_prompt,
    get_persona_system_prompt,
    build_market_snapshot,
)
from src.report_language import normalize_report_language
from src.schemas.analysis_result import (
    check_content_integrity,
    apply_placeholder_fill,
    validate_numerical_fields,
)
from src.agent.signal_layer import normalize_all_signals, detect_conflicts
from src.enums import ReportType
from src.core.pipeline_helpers import (
    override_sniper_points,
    compute_ma_status,
    extract_risk_keywords,
    estimate_intel_bullet_count,
)
from src.core.pipeline_data_collector import StockDataCollectionResult
from src.core.trading_calendar import advance_trading_days, get_market_for_stock

logger = logging.getLogger(__name__)


class AnalysisExecutor:
    """
    Executes AI analysis over pre-collected stock data.

    Responsibilities:
    - Signal layer normalization
    - Historical analysis comparison + logic backtracking
    - LLM call (simple or debate mode)
    - Post-processing (price override, sniper override, numerical validation)
    - Content integrity check
    - Optional TraderAgent post-processing
    - DB persistence (save_analysis_history, save_prediction_eval)
    """

    def __init__(
        self,
        config: Config,
        db: Any,
        analyzer: Any,
        search_service: Any,
        fetcher_manager: Any,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ):
        self.config = config
        self.db = db
        self.analyzer = analyzer
        self.search_service = search_service
        self.fetcher_manager = fetcher_manager
        self._progress_callback = progress_callback
        self._cached_market_overview: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Progress delegation
    # ------------------------------------------------------------------
    def _emit_progress(self, progress: int, message: str) -> None:
        if self._progress_callback is not None:
            try:
                self._progress_callback(progress, message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def analyze(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        collected: StockDataCollectionResult,
        route_reasons: Optional[List[str]] = None,
        analysis_mode: str = "simple",
    ) -> Optional[AnalysisResult]:
        """
        Unified analysis path over pre-collected data.
        Replaces _analyze_with_agent.
        """
        name = collected.stock_name or code
        report_language = normalize_report_language(
            getattr(self.config, "report_language", "zh"),
        )

        self._emit_progress(62, f"{name}：正在生成分析 Prompt")

        market_overview = await self.fetch_market_overview()
        base_context = {
            "code": code,
            "stock_name": name,
            "date": collected.analysis_date.isoformat(),
            "today": collected.today_k or {},
            "yesterday": collected.yesterday_k or {},
        }

        from src.core.pipeline_context import enhance_analysis_context
        enhanced_context = enhance_analysis_context(
            context=base_context,
            realtime_quote=collected.realtime_quote,
            chip_data=collected.chip_data,
            trend_result=collected.trend_result,
            stock_name=name,
            search_service=self.search_service,
            fetcher_manager=self.fetcher_manager,
            db=self.db,
            compute_ma_status=compute_ma_status,
            fundamental_context=collected.fundamental_context,
            market_overview=market_overview,
            peer_comparison=collected.peer_comparison,
        )

        # ----- Signal layer -----
        signals = normalize_all_signals(
            trend_result=collected.trend_result,
            chip_data=collected.chip_data,
            sentiment_score=None,
            news_context=collected.final_news,
            realtime_quote=collected.realtime_quote,
            fundamental_context=collected.fundamental_context,
        )
        enhanced_context["normalized_signals"] = [s.__dict__ for s in signals]
        conflict_warnings = detect_conflicts(signals)
        enhanced_context["conflict_warnings"] = conflict_warnings

        # ----- Historical context -----
        try:
            from functools import partial as _partial
            prev_rows = (
                await asyncio.to_thread(
                    _partial(self.db.get_analysis_history, code=code, limit=2, days=365)
                )
                if self.db
                else []
            )
            prev_list: List[Dict[str, Any]] = []
            if prev_rows:
                for r in prev_rows:
                    created = str(getattr(r, "created_at", "") or "")
                    prev_list.append({
                        "date": created[:10] if created else "",
                        "decision": getattr(r, "operation_advice", "") or "",
                        "score": getattr(r, "sentiment_score", 0) or 0,
                        "summary": (getattr(r, "analysis_summary", "") or "")[:150],
                    })
                enhanced_context["previous_analyses"] = prev_list

            # Logic backtracking
            if prev_list and collected.trend_result is not None:
                prev = prev_list[0]
                prev_decision = prev.get("decision", "")
                bullish = {"买入", "加仓"}
                bearish = {"卖出", "减仓"}
                if prev_decision in bullish:
                    prev_label = "看多"
                elif prev_decision in bearish:
                    prev_label = "看空"
                else:
                    prev_label = "中性"
                signal_score = getattr(collected.trend_result, "signal_score", 50) or 50
                if signal_score >= 60:
                    curr_label = "看多"
                elif signal_score <= 40:
                    curr_label = "看空"
                else:
                    curr_label = "中性"
                if prev_label != curr_label:
                    enhanced_context["logic_turnover"] = {
                        "previous_decision": prev_decision or prev_label,
                        "previous_summary": prev.get("summary", ""),
                        "previous_date": prev.get("date", ""),
                        "current_direction": curr_label,
                    }
        except Exception as exc:
            logger.warning("[%s] Failed to fetch previous analysis: %s", code, exc)

        # ----- Data freshness -----
        now_str = datetime.now().strftime("%m-%d %H:%M")
        enhanced_context["data_freshness"] = now_str

        # ----- LLM call -----
        model_used = "unknown"
        result: Optional[AnalysisResult] = None

        if analysis_mode == "debate":
            from src.agent.debate_analyzer import DebateAnalyzer

            self._emit_progress(68, f"{name}：正在调用辩论分析 (DebateAnalyzer)")
            debate = DebateAnalyzer(self.config, self.analyzer)
            enhanced_prompt = format_analysis_prompt(
                enhanced_context, name,
                news_context=collected.final_news,
                report_language=report_language,
                output_format="dashboard",
                normalized_signals=enhanced_context.get("normalized_signals"),
                conflict_warnings=enhanced_context.get("conflict_warnings"),
            )
            debate_context = (
                f"{enhanced_prompt}\n\n【新闻信息】\n{collected.final_news}"
                if collected.final_news else enhanced_prompt
            )
            result = await debate.analyze(debate_context, collected.final_news)
            if result is None:
                logger.error("[%s] Debate analysis returned None", code)
                error_result = self.analyzer._make_error_result(code, name, "辩论分析失败")
                error_result.query_id = query_id
                return error_result
            model_used = "debate"
            self._emit_progress(82, f"{name}：辩论分析完成")
        else:
            prompt = format_analysis_prompt(
                enhanced_context, name,
                news_context=collected.final_news,
                report_language=report_language,
                output_format="dashboard",
                normalized_signals=enhanced_context.get("normalized_signals"),
                conflict_warnings=enhanced_context.get("conflict_warnings"),
            )
            system_prompt = get_persona_system_prompt("chief", report_language)

            self._emit_progress(68, f"{name}：正在调用 LLM 分析")

            try:
                response_text, model_used, _ = await self.analyzer._call_litellm_async(
                    prompt,
                    {"max_tokens": 8192, "temperature": getattr(self.config, "llm_temperature", 0.7)},
                    system_prompt=system_prompt,
                )
            except Exception as exc:
                logger.error("[%s] Analysis LLM call failed: %s", code, exc)
                error_result = self.analyzer._make_error_result(code, name, str(exc))
                error_result.query_id = query_id
                return error_result

            self._emit_progress(82, f"{name}：正在解析分析结果")
            result = self.analyzer._parse_response(response_text, code, name)
            if result is None:
                logger.error("[%s] Failed to parse LLM response", code)
                error_result = self.analyzer._make_error_result(code, name, "结果解析失败")
                error_result.query_id = query_id
                return error_result

            # Numerical validation retry (single LLM mode only)
            if getattr(self.config, "validation_retry_enabled", True):
                self._emit_progress(84, f"{name}：校验数值合理性")
                rt_price = (
                    getattr(collected.realtime_quote, "price", None)
                    if collected.realtime_quote else None
                )
                num_warnings = validate_numerical_fields(result, current_price=rt_price)
                if num_warnings:
                    logger.info("[%s] Numerical validation warnings, retrying: %s", code, num_warnings)
                    retry_prompt = (
                        prompt
                        + "\n\n【数值校验警告，请修正生成的价格点位】\n"
                        + "\n".join(f"- ⚠️ {w}" for w in num_warnings)
                    )
                    try:
                        response_text, model_used, _ = await self.analyzer._call_litellm_async(
                            retry_prompt,
                            {"max_tokens": 8192, "temperature": 0.3},
                            system_prompt=system_prompt,
                        )
                    except Exception as exc:
                        logger.error("[%s] Retry LLM call failed, keeping original: %s", code, exc)
                    else:
                        retry_result = self.analyzer._parse_response(response_text, code, name)
                        if retry_result is not None:
                            result = retry_result
                            logger.info("[%s] Retry LLM succeeded, using corrected result", code)

        # ----- Post-processing -----
        if collected.realtime_quote:
            rt_price = getattr(collected.realtime_quote, "price", None)
            rt_change = getattr(collected.realtime_quote, "change_pct", None)
            if rt_price is not None and rt_price > 0:
                result.current_price = float(rt_price)
            if rt_change is not None:
                result.change_pct = float(rt_change)

        fill_price_position_if_needed(result, collected.trend_result, collected.realtime_quote)
        fill_chip_structure_if_needed(result, collected.chip_data)

        rt_price = (
            getattr(collected.realtime_quote, "price", None)
            if collected.realtime_quote else None
        )
        num_warnings = validate_numerical_fields(result, current_price=rt_price)
        if num_warnings:
            logger.info("[%s] Numerical validation warnings: %s", code, num_warnings)
            result.analysis_metadata["numerical_warnings"] = num_warnings

        if collected.trend_result is not None:
            sniper_overrides = override_sniper_points(result, collected.trend_result, rt_price)
            if sniper_overrides:
                logger.info("[%s] Overrode %d sniper_point(s)", code, sniper_overrides)

        # ----- Metadata -----
        self._emit_progress(88, f"{name}：正在保存分析结果")
        result.query_id = query_id
        result.historical_performance = enhanced_context.get("historical_performance")
        result.peer_comparison = collected.peer_comparison
        result.report_language = report_language
        result.model_used = model_used
        result.data_sources = f"hybrid:{model_used or 'unknown'}" + (
            f"({','.join(route_reasons)})" if route_reasons else ""
        )
        result.analysis_metadata.update({
            "agent_route": {
                "used_agent": True,
                "selection_source": "forced"
                if (route_reasons and any(reason.startswith("config:") for reason in route_reasons))
                else "auto",
                "reasons": route_reasons or [],
                "arch": "hybrid",
                "mode": "single",
            },
            "agent_runtime": {
                "arch": "hybrid",
                "success": True,
                "model": model_used or "",
                "provider": (model_used or "").split("/")[0] if model_used else "",
            },
        })

        result.market_snapshot = build_market_snapshot(enhanced_context)

        if not result.success:
            logger.warning("[%s] Analysis failed for %s: %s", code, name, result.error_message or "unknown")
        else:
            passed, missing_fields = check_content_integrity(result)
            if not passed:
                logger.warning("[%s] Content integrity check failed, missing: %s", code, missing_fields)
                apply_placeholder_fill(result, missing_fields)

        # ----- TraderAgent -----
        if getattr(self.config, "trader_agent_enabled", True):
            self._emit_progress(92, f"{name}：正在生成交易决策（Trader Agent）")
            await self._run_trader_agent(
                code=code, stock_name=name,
                enhanced_context=enhanced_context,
                query_id=query_id, report_type=report_type,
                trend_result=collected.trend_result,
                news_context=collected.final_news,
                route_reasons=route_reasons or [], result=result,
                realtime_quote=collected.realtime_quote,
            )

        # ----- DB persistence -----
        await self.db.save_analysis_history_async(
            result,
            query_id,
            getattr(report_type, "value", str(report_type)),
            collected.final_news,
            {},
            getattr(self.config, "save_context_snapshot", False),
        )

        try:
            close_price = getattr(result, "current_price", None)
            if close_price is None and collected.realtime_quote is not None:
                close_price = getattr(collected.realtime_quote, "price", None)
            if close_price is not None:
                analysis_date = collected.analysis_date
                eval_date = advance_trading_days(
                    get_market_for_stock(code), analysis_date, n=5
                )
                await asyncio.to_thread(
                    self.db.save_prediction_eval, {
                        "query_id": query_id,
                        "code": code,
                        "analysis_date": analysis_date,
                        "eval_date": eval_date,
                        "decision_type": getattr(result, "decision_type", "hold") or "hold",
                        "sentiment_score": getattr(result, "sentiment_score", 50) or 50,
                        "model_used": model_used or "",
                        "close_at_analysis": float(close_price),
                    }
                )
        except Exception as exc:
            logger.warning("[%s] Failed to write prediction_eval: %s", code, exc)

        self._emit_progress(94, f"{name}：分析完成")
        logger.info("[%s] Analysis done, score: %s", code, result.sentiment_score)
        return result

    # ------------------------------------------------------------------
    # TraderAgent post-processing
    # ------------------------------------------------------------------
    async def _run_trader_agent(
        self,
        code: str,
        stock_name: str,
        enhanced_context: Dict[str, Any],
        query_id: str,
        report_type: Any,
        trend_result: Any = None,
        news_context: str = "",
        route_reasons: Optional[List[str]] = None,
        result: Optional[Any] = None,
        realtime_quote: Optional[Any] = None,
    ) -> None:
        """Run optional TraderAgent post-processing."""
        try:
            from src.agent.agents.trader_agent import TraderAgent
            from src.agent.protocols import AgentContext, AgentOpinion

            agent = TraderAgent(analyzer=self.analyzer, config=self.config)

            current_price = None
            yesterday_close = None

            if realtime_quote is not None:
                rt_price = getattr(realtime_quote, "price", None)
                if rt_price is not None and rt_price > 0:
                    current_price = float(rt_price)
            if current_price is None:
                today_data = enhanced_context.get("today", {})
                if isinstance(today_data, dict) and today_data.get("close"):
                    current_price = float(today_data["close"])
            if current_price is not None:
                yesterday_data = enhanced_context.get("yesterday", {})
                if isinstance(yesterday_data, dict) and yesterday_data.get("close"):
                    yesterday_close = float(yesterday_data["close"])

            trader_meta: Dict[str, Any] = {
                "report_language": getattr(self.config, "report_language", "zh"),
            }
            normalized_signals = enhanced_context.get("normalized_signals")
            if normalized_signals:
                trader_meta["normalized_signals"] = normalized_signals
            if current_price is not None:
                trader_meta["current_price"] = current_price
            else:
                logger.warning("[%s] TraderAgent: no current_price available", code)
            if yesterday_close is not None:
                trader_meta["yesterday_close"] = yesterday_close

            trader_ctx = AgentContext(
                stock_code=code,
                stock_name=stock_name or "",
                query=f"Trading decision for {code}",
                meta=trader_meta,
            )

            if result is not None:
                if result.technical_analysis:
                    trader_ctx.add_opinion(AgentOpinion(
                        agent_name="technical",
                        signal=result.decision_type or "hold",
                        confidence=result.sentiment_score / 100.0 if result.sentiment_score else 0.5,
                        reasoning=result.technical_analysis[:200] if result.technical_analysis else "",
                    ))
                if result.fundamental_analysis:
                    trader_ctx.add_opinion(AgentOpinion(
                        agent_name="fundamental",
                        signal=result.decision_type or "hold",
                        confidence=0.6,
                        reasoning=result.fundamental_analysis[:200] if result.fundamental_analysis else "",
                    ))
                if result.news_summary:
                    trader_ctx.add_opinion(AgentOpinion(
                        agent_name="intel",
                        signal="hold",
                        confidence=0.5,
                        reasoning=result.news_summary[:200] if result.news_summary else "",
                    ))

            opinion = await agent.run(trader_ctx)
            if opinion is None:
                logger.warning(f"[{code}] TraderAgent returned None")
                return

            if result is not None:
                result.trader_decision = opinion.raw_data
                if opinion.raw_data:
                    result.position_sizing_pct = opinion.raw_data.get("position_sizing", {}).get("recommended_pct")
                    result.holding_period_days = opinion.raw_data.get("holding_period", {}).get("expected_days")
                    result.risk_reward_ratio = opinion.raw_data.get("risk_assessment", {}).get("risk_reward_ratio")

        except Exception as e:
            logger.error(f"[{code}] TraderAgent failed: {e}", exc_info=True)

    def should_auto_route_from_context(
        self,
        *,
        code: str,
        report_type: ReportType,
        enhanced_context: Dict[str, Any],
        final_news: str,
        fundamental_context: Optional[Dict[str, Any]],
        trend_result: Any,
        a_stock_intelligence: str,
        money_flow_intelligence: str,
        guru_insight: str,
    ) -> Tuple[bool, List[str]]:
        """Original _should_auto_route_to_agent logic."""
        from src.core.pipeline_helpers import estimate_intel_bullet_count, extract_risk_keywords
        import logging as _logging
        _log = _logging.getLogger(__name__)

        if not getattr(self.config, "agent_auto_route_analysis", False):
            return False, []
        if not self._is_agent_runtime_available():
            _log.info("[%s] Auto Agent routing enabled but runtime unavailable", code)
            return False, []

        major_reasons: List[str] = []
        minor_reasons: List[str] = []

        today = dict(enhanced_context.get("today") or {})
        if trend_result is None or not today or today.get("close") in (None, "", 0):
            major_reasons.append("core_data_gap")

        coverage = (fundamental_context or {}).get("coverage") or {}
        failing_blocks = sorted(
            key for key, status in coverage.items()
            if str(status).strip().lower() in {"failed", "partial"}
        )
        if failing_blocks:
            minor_reasons.append(f"fundamental_coverage:{','.join(failing_blocks[:2])}")

        bullet_count = estimate_intel_bullet_count(final_news)
        if bullet_count >= 6 or len(final_news or "") >= 1600:
            major_reasons.append(f"dense_news_flow:{bullet_count}")

        risk_hits = extract_risk_keywords(final_news)
        if risk_hits:
            major_reasons.append(f"risk_sensitive_intel:{','.join(risk_hits[:2])}")

        a_share_layers = sum(
            1 for section in (a_stock_intelligence, money_flow_intelligence, guru_insight)
            if isinstance(section, str) and section.strip()
        )
        if a_share_layers >= 2:
            minor_reasons.append("multi_layer_a_share_intel")

        report_type_value = getattr(report_type, "value", str(report_type))
        if report_type_value != "simple" and (major_reasons or minor_reasons):
            minor_reasons.append(f"report_type:{report_type_value}")

        reasons = major_reasons + minor_reasons
        should_route = bool(major_reasons) or len(minor_reasons) >= 2
        return should_route, reasons

    # ------------------------------------------------------------------
    # Agent history prefetch
    # ------------------------------------------------------------------
    async def ensure_agent_history(self, code: str, min_days: int = 240) -> None:
        """Ensure at least min_days of K-line history in DB for agent tools."""
        from src.services.history_loader import get_frozen_target_date
        from src.core.pipeline_helpers import resolve_resume_target_date

        target = get_frozen_target_date()
        if target is None:
            target = resolve_resume_target_date(code)
        start = target - timedelta(days=int(min_days * 1.8))
        bars = await self.db.get_data_range_async(code, start, target)
        if bars and len(bars) >= min(min_days, 200):
            logger.debug("[%s] Agent history: %d bars in DB, sufficient", code, len(bars))
            return
        try:
            df, source = await self.fetcher_manager.get_daily_data(code, days=min_days)
            if df is not None and not df.empty:
                await self.db.save_daily_data_async(df, code, source)
                logger.info("[%s] Prefetched %d rows of history (source: %s)", code, len(df), source)
        except Exception as e:
            logger.warning("[%s] Agent history prefetch failed: %s", code, e)

    # ------------------------------------------------------------------
    # Market overview
    # ------------------------------------------------------------------
    async def fetch_market_overview(self, region: str = "cn") -> Optional[Dict[str, Any]]:
        if self._cached_market_overview is not None:
            return self._cached_market_overview
        if self.fetcher_manager is None:
            return None
        result: Dict[str, Any] = {}
        try:
            indices = await self.fetcher_manager.get_main_indices(region=region)
            if indices:
                result["indices"] = indices
        except Exception as exc:
            logger.warning("[大盘] get_main_indices failed: %s", exc)
        try:
            sectors = await self.fetcher_manager.get_sector_rankings(n=5)
            if sectors and len(sectors) == 2:
                result["sectors"] = {"top": sectors[0], "bottom": sectors[1]}
        except Exception as exc:
            logger.warning("[大盘] get_sector_rankings failed: %s", exc)
        self._cached_market_overview = result if result else None
        return self._cached_market_overview

    # ------------------------------------------------------------------
    # Agent runtime check
    # ------------------------------------------------------------------
    def _is_agent_runtime_available(self) -> bool:
        checker = getattr(self.config, "is_agent_available", None)
        if callable(checker):
            try:
                available = checker()
            except Exception:
                available = None
            if isinstance(available, bool):
                return available

        for field_name in ("agent_litellm_model", "litellm_model"):
            value = getattr(self.config, field_name, None)
            if isinstance(value, str) and value.strip():
                return True

        return bool(getattr(self.config, "agent_mode", False))

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------
    @staticmethod
    def apply_trend_fallback(
        result: AnalysisResult,
        trend_result: Optional[Any],
        report_language: str,
    ) -> None:
        if trend_result is None:
            result.sentiment_score = 50
            result.operation_advice = "Watch" if report_language == "en" else "观望"
            return

        score = getattr(trend_result, "signal_score", None)
        try:
            numeric_score = int(score)
        except (TypeError, ValueError):
            numeric_score = 50
        result.sentiment_score = numeric_score if numeric_score > 0 else 50

        trend_status = getattr(trend_result, "trend_status", None)
        trend_label = getattr(trend_status, "value", None) or str(trend_status or "").strip()
        if trend_label:
            result.trend_prediction = trend_label

        buy_signal = getattr(trend_result, "buy_signal", None)
        signal_label = getattr(buy_signal, "value", None) or str(buy_signal or "").strip()
        if signal_label:
            result.operation_advice = signal_label
        else:
            result.operation_advice = "Watch" if report_language == "en" else "观望"

        from src.agent.protocols import normalize_decision_signal
        signal_name = getattr(buy_signal, "name", "").lower()
        signal_to_decision = {
            "strong_buy": "buy", "buy": "buy", "hold": "hold",
            "wait": "hold", "sell": "sell", "strong_sell": "sell",
        }
        result.decision_type = signal_to_decision.get(signal_name, result.decision_type or "hold")
        result.decision_type = normalize_decision_signal(result.decision_type)
        result.data_sources = f"{result.data_sources},trend:fallback" if result.data_sources else "trend:fallback"
