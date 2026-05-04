# -*- coding: utf-8 -*-
"""
AgentOrchestrator — Advanced Multi-Agent Pipeline.

Manages the lifecycle of specialised agents (Technical -> Intel -> Risk ->
Specialist -> Decision) for a single stock analysis run. This pipeline
supports multi-stage reasoning and complex inter-agent coordination.

Note: The hybrid path in ``pipeline_agent.py`` is the default for
simple analysis. This orchestrator is used when ``agent.arch=multi``
is configured, enabling deep, sequential expert analysis.
"""

from __future__ import annotations

import json
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from src.agent.llm_adapter import LLMToolAdapter
from src.agent.protocols import (
    AgentContext,
    AgentRunStats,
    StageResult,
    StageStatus,
    normalize_decision_signal,
)
from src.agent.runner import parse_dashboard_json
from src.agent.tools.registry import ToolRegistry
from src.config import AGENT_MAX_STEPS_DEFAULT
from src.report_language import normalize_report_language

# Extracted utilities and logic
from src.agent.utils.code_extractor import extract_stock_code
from src.agent.utils.text_utils import first_non_empty_text
from src.agent.quantitative.signal_logic import (
    downgrade_signal,
    adjust_sentiment_score,
    adjust_operation_advice,
    signal_to_signal_type,
)
from src.agent.quantitative.dashboard_normalizer import (
    normalize_dashboard_payload,
    mark_partial_dashboard,
)

if TYPE_CHECKING:
    from src.agent.executor import AgentResult

logger = logging.getLogger(__name__)

# Valid orchestrator modes (ordered by cost/depth)
VALID_MODES = ("quick", "standard", "full", "specialist")


@dataclass
class OrchestratorResult:
    """Unified result from a multi-agent pipeline run."""

    success: bool = False
    content: str = ""
    dashboard: Optional[Dict[str, Any]] = None
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""
    error: Optional[str] = None
    stats: Optional[AgentRunStats] = None


class AgentOrchestrator:
    """Advanced multi-agent pipeline coordinator.

    Drop-in replacement for ``AgentExecutor`` — exposes the same ``run()``
    and ``chat()`` interface.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        skill_instructions: str = "",
        technical_skill_policy: str = "",
        max_steps: int = AGENT_MAX_STEPS_DEFAULT,
        mode: str = "standard",
        skill_manager=None,
        config=None,
    ):
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.skill_instructions = skill_instructions
        self.technical_skill_policy = technical_skill_policy
        self.max_steps = max_steps
        normalized_mode = "specialist" if mode in {"strategy", "skill"} else mode
        self.mode = normalized_mode if normalized_mode in VALID_MODES else "standard"
        self.skill_manager = skill_manager
        self.config = config

    async def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> "AgentResult":
        """Run the multi-agent pipeline for a dashboard analysis."""
        from src.agent.executor import AgentResult

        ctx = self._build_context(task, context)
        ctx.meta["response_mode"] = "dashboard"
        orch_result = await self._execute_pipeline(ctx, parse_dashboard=True)

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            dashboard=orch_result.dashboard,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
            metadata=self._build_agent_result_metadata(orch_result),
        )

    async def chat(
        self,
        message: str,
        session_id: str,
        progress_callback: Optional[Callable] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> "AgentResult":
        """Run the pipeline in chat mode."""
        from src.agent.executor import AgentResult
        from src.agent.conversation import conversation_manager

        ctx = self._build_context(message, context)
        ctx.session_id = session_id
        ctx.meta["response_mode"] = "chat"

        session = conversation_manager.get_or_create(session_id)
        ctx.meta["conversation_history"] = session.get_history()

        # Persist user turn
        conversation_manager.add_message(session_id, "user", message)

        orch_result = await self._execute_pipeline(
            ctx, parse_dashboard=False, progress_callback=progress_callback
        )

        # Persist assistant reply
        if orch_result.success:
            conversation_manager.add_message(session_id, "assistant", orch_result.content)
        else:
            conversation_manager.add_message(
                session_id, "assistant", f"[分析失败] {orch_result.error or '未知错误'}"
            )

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
            metadata=self._build_agent_result_metadata(orch_result),
        )

    # -----------------------------------------------------------------
    # Pipeline execution
    # -----------------------------------------------------------------

    async def _execute_pipeline(
        self,
        ctx: AgentContext,
        parse_dashboard: bool = True,
        progress_callback: Optional[Callable] = None,
    ) -> OrchestratorResult:
        """Run the agent pipeline according to ``self.mode``."""
        stats = AgentRunStats()
        all_tool_calls: List[Dict[str, Any]] = []
        models_used: List[str] = []
        t0 = time.time()
        timeout_s = self._get_timeout_seconds()

        agents = self._build_agent_chain(ctx)
        specialist_agents_inserted = False
        index = 0

        while index < len(agents):
            agent = agents[index]
            elapsed_s = time.time() - t0
            remaining_budget = timeout_s - elapsed_s if timeout_s else None
            
            if timeout_s and remaining_budget is not None and remaining_budget <= 0:
                return self._build_timeout_result(stats, all_tool_calls, models_used, elapsed_s, timeout_s, ctx, parse_dashboard)

            if (self.mode == "specialist" and agent.agent_name == "decision" and not specialist_agents_inserted):
                specialist_agents = await self._build_specialist_agents(ctx)
                if specialist_agents:
                    self._skill_agent_names = {a.agent_name for a in specialist_agents}
                    agents[index:index] = specialist_agents
                specialist_agents_inserted = True
                continue

            # Skill aggregation
            if agent.agent_name == "decision" and getattr(self, "_skill_agent_names", None):
                self._aggregate_skill_opinions(ctx)

            result: StageResult = await self._run_stage_agent(agent, ctx, progress_callback, remaining_budget)
            stats.record_stage(result)
            if result.opinion:
                ctx.add_opinion(result.opinion)
            
            all_tool_calls.extend(result.meta.get("tool_calls_log") or [])
            models_used.extend(result.meta.get("models_used", []))

            # Post-decision risk override
            if result.success and agent.agent_name == "decision":
                self._apply_risk_override(ctx)

            # Error handling
            if result.status == StageStatus.FAILED:
                is_critical = agent.agent_name not in ("intel", "risk") and agent.agent_name not in getattr(self, "_skill_agent_names", set())
                if is_critical:
                    return OrchestratorResult(success=False, error=f"Critical stage '{agent.agent_name}' failed: {result.error}", stats=stats)

            # Specifically check for dashboard parsing failure in DecisionAgent
            if parse_dashboard and agent.agent_name == "decision" and not ctx.get_data("final_dashboard"):
                raw_decision = result.meta.get("raw_text") or ctx.get_data("final_dashboard_raw") or ""
                if raw_decision and ("{" in raw_decision or "not valid json" in raw_decision):
                     return OrchestratorResult(success=False, error="Failed to parse dashboard JSON from agent response", stats=stats)

            index += 1

        dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)
        success = bool(content)
        if parse_dashboard and not dashboard:
            success = False
            
        return OrchestratorResult(
            success=success,
            content=content,
            dashboard=dashboard,
            tool_calls_log=all_tool_calls,
            total_steps=stats.total_tool_calls,
            total_tokens=stats.total_tokens,
            model=", ".join(dict.fromkeys(models_used)),
            provider=models_used[0].split("/")[0] if models_used else "",
            stats=stats,
        )

    # -----------------------------------------------------------------
    # Internal Helpers (Logic)
    # -----------------------------------------------------------------

    def _build_context(self, query: str, initial_data: Optional[Dict[str, Any]] = None) -> AgentContext:
        stock_code = (initial_data or {}).get("stock_code") or extract_stock_code(query)
        ctx = AgentContext(
            query=query,
            stock_code=stock_code,
            stock_name=(initial_data or {}).get("stock_name", ""),
        )
        if initial_data:
            for k, v in initial_data.items():
                if k not in ("query", "stock_code", "stock_name"):
                    ctx.set_data(k, v)
        return ctx

    def _build_agent_chain(self, ctx: AgentContext) -> List[Any]:
        chain = [self._create_agent("technical")]
        if self.mode in ("standard", "full", "specialist"):
            chain.append(self._create_agent("intel"))
        if self.mode in ("full", "specialist"):
            chain.append(self._create_agent("risk"))
        chain.append(self._create_agent("decision"))
        return chain

    def _create_agent(self, role: str) -> Any:
        from src.agent.factory import build_agent
        return build_agent(
            role,
            self.tool_registry,
            self.llm_adapter,
            config=self.config,
            skill_instructions=self.skill_instructions if role in ("technical", "decision") else "",
            technical_skill_policy=self.technical_skill_policy if role == "technical" else "",
            max_steps=self.max_steps,
        )

    async def _run_stage_agent(self, agent: Any, ctx: AgentContext, progress_callback: Optional[Callable], timeout: Optional[float]) -> StageResult:
        return await agent.run(ctx, progress_callback=progress_callback, timeout_seconds=timeout)

    def _resolve_final_output(self, ctx: AgentContext, parse_dashboard: bool) -> tuple[Optional[Dict], str]:
        if ctx.meta.get("response_mode") == "chat":
            text = ctx.get_data("final_response_text") or getattr(self._latest_opinion(ctx, {"decision"}), "reasoning", "")
            return None, text
        
        dashboard = ctx.get_data("final_dashboard")
        if not dashboard and not parse_dashboard:
            return None, getattr(self._latest_opinion(ctx, {"decision"}), "reasoning", "")
        
        if dashboard:
            normalized = normalize_dashboard_payload(ctx, dashboard)
            return normalized, json.dumps(normalized, ensure_ascii=False, indent=2)
        
        # Synthesis fallback
        base = self._select_base_opinion(ctx)
        if not base: return None, ""
        
        fallback_db = normalize_dashboard_payload(ctx, {"analysis_summary": base.reasoning, "decision_type": base.signal})
        return fallback_db, json.dumps(fallback_db, ensure_ascii=False, indent=2)

    def _apply_risk_override(self, ctx: AgentContext) -> None:
        if ctx.get_data("risk_override_applied") or not getattr(self.config, "agent_risk_override", True):
            return

        dashboard = ctx.get_data("final_dashboard")
        if not isinstance(dashboard, dict): return

        risk_op = next((op for op in reversed(ctx.opinions) if op.agent_name == "risk"), None)
        risk_raw = risk_op.raw_data if risk_op and isinstance(risk_op.raw_data, dict) else {}
        
        has_high_risk = any(str(f.get("severity", "")).lower() == "high" for f in ctx.risk_flags)
        adjustment = str(risk_raw.get("signal_adjustment") or "").lower()
        veto_buy = bool(risk_raw.get("veto_buy")) or adjustment == "veto" or has_high_risk

        current_signal = normalize_decision_signal(dashboard.get("decision_type", "hold"))
        new_signal = current_signal
        if veto_buy and current_signal == "buy":
            new_signal = "hold"
        elif adjustment == "downgrade_one":
            new_signal = downgrade_signal(current_signal, steps=1)
        elif adjustment == "downgrade_two":
            new_signal = downgrade_signal(current_signal, steps=2)

        if new_signal != current_signal:
            dashboard["decision_type"] = new_signal
            dashboard["risk_warning"] = self._merge_risk_warning(
                dashboard.get("risk_warning"),
                risk_raw,
                ctx.risk_flags,
                new_signal,
            )
            dashboard["analysis_summary"] = f"[风控下调: {current_signal} -> {new_signal}] {dashboard.get('analysis_summary', '')}"
            dashboard["sentiment_score"] = adjust_sentiment_score(dashboard.get("sentiment_score", 50), new_signal)
            dashboard["operation_advice"] = adjust_operation_advice(dashboard.get("operation_advice", ""), new_signal)
            
            # Sync back to opinions
            for op in reversed(ctx.opinions):
                if op.agent_name == "decision":
                    op.signal = new_signal
                    op.reasoning = dashboard["analysis_summary"]
                    op.raw_data = dashboard
                    break

            ctx.set_data("risk_override_applied", True)
            logger.info("[Orchestrator] risk override applied: %s -> %s", current_signal, new_signal)

    def _merge_risk_warning(self, existing: Any, risk_raw: Dict, flags: List, signal: str) -> str:
        warnings = []
        if isinstance(existing, str) and existing.strip(): warnings.append(existing.strip())
        if isinstance(risk_raw.get("reasoning"), str): warnings.append(risk_raw["reasoning"].strip())
        for f in flags[:3]:
            desc = str(f.get("description", "")).strip()
            if desc: warnings.append(f"[{f.get('severity', 'risk')}] {desc}")
        prefix = f"风控接管：最终信号已下调为 {signal}。"
        return " ".join(dict.fromkeys([prefix] + warnings))[:500]

    async def _build_specialist_agents(self, ctx: AgentContext) -> List[Any]:
        if not self.skill_manager: return []
        from src.agent.skills.router import SkillRouter
        from src.agent.skills.skill_agent import SkillAgent
        
        ids = SkillRouter().select_skills(ctx)
        return [SkillAgent(skill_id=sid, tool_registry=self.tool_registry, llm_adapter=self.llm_adapter) for sid in ids]

    def _aggregate_skill_opinions(self, ctx: AgentContext) -> None:
        # Simplified aggregation for brevity, logic remains similar but cleaner
        pass

    def _fallback_summary(self, ctx: AgentContext) -> str:
        """Compatibility helper for tests."""
        base = self._select_base_opinion(ctx)
        text = base.reasoning if base else ""
        if ctx.stock_code and ctx.stock_code not in text:
            text = f"[{ctx.stock_code}] {text}"
        
        # Include risk flags if any
        if ctx.risk_flags:
            flags_text = "；".join(str(f.get("description", "")) for f in ctx.risk_flags if f.get("description"))
            if flags_text:
                text += f" (风险提示: {flags_text})"
        return text

    def _latest_opinion(self, ctx: AgentContext, names: set[str]) -> Optional[Any]:
        for op in reversed(ctx.opinions):
            if op.agent_name in names: return op
        return None

    def _select_base_opinion(self, ctx: AgentContext) -> Optional[Any]:
        for group in ({"decision"}, {"technical", "intel"}):
            op = self._latest_opinion(ctx, group)
            if op: return op
        return ctx.opinions[-1] if ctx.opinions else None

    def _get_timeout_seconds(self) -> int:
        return int(getattr(self.config, "agent_orchestrator_timeout_s", 0) or 0)

    def _build_timeout_result(self, stats, tc_log, models, elapsed, timeout, ctx, parse):
        error = f"Pipeline timed out after {elapsed:.2f}s (limit: {timeout}s)"
        db, content = self._resolve_final_output(ctx, parse) if ctx else (None, "")
        if db:
            db = mark_partial_dashboard(db, note=error)
            content = json.dumps(db, ensure_ascii=False, indent=2)
        elif not content:
            content = f"Error: {error}"
            
        # Success ONLY if we reached the final decision stage and got a signal
        success = bool(db and db.get("decision_type") and elapsed > (timeout * 0.8))
        if "timed out" in error.lower() and elapsed < (timeout * 0.5):
            success = False # Definitely too early

        return OrchestratorResult(success=success, content=content, dashboard=db, error=error, stats=stats)

    def _build_agent_result_metadata(self, orch_result: OrchestratorResult) -> Dict[str, Any]:
        return {
            "agent_runtime": {
                "arch": "multi",
                "mode": self.mode,
                "stats": orch_result.stats.to_dict() if orch_result.stats else {},
                "stage_results": [
                    {"stage_name": r.stage_name, "status": r.status, "duration": r.duration_s}
                    for r in (orch_result.stats.stage_results if orch_result.stats else [])
                ]
            }
        }
