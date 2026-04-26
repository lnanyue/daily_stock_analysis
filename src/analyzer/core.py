# -*- coding: utf-8 -*-
"""
AI 分析核心逻辑 (GeminiAnalyzer) - 异步全连通版
"""

import logging
import asyncio
import json
import os
import re
import time
from typing import Dict, Any, Optional, List, Tuple

import litellm
from litellm import Router

from src.config import (
    get_config,
    get_configured_llm_models,
    get_api_keys_for_model,
    extra_litellm_params,
    resolve_news_window_days,
)
from src.schemas.analysis_result import (
    AnalysisResult,
    check_content_integrity,
    apply_placeholder_fill,
)
from src.agent.llm_adapter import get_thinking_extra_body
from src.storage import persist_llm_usage
from .prompt_builder import format_analysis_prompt, build_integrity_retry_prompt
from .utils import (
    fill_chip_structure_if_needed,
    fill_price_position_if_needed,
    get_stock_name_multi_source,
    build_market_snapshot,
)
from src.market_context import get_market_role, get_market_guidelines

logger = logging.getLogger(__name__)


class GeminiAnalyzer:
    """
    基于 LiteLLM 的统一 AI 分析器
    """
    
    TEXT_SYSTEM_PROMPT = "你是一位专业的股票分析助手。\n\n- 回答必须基于用户提供的数据与上下文\n- 若信息不足，要明确指出不确定性\n- 不要编造价格、财报或新闻事实\n"

    def __init__(
        self,
        config=None,
        *,
        skill_instructions: str = "",
        default_skill_policy: str = "",
        use_legacy_default_prompt: bool = False,
    ):
        self.config = config
        self.skill_instructions = skill_instructions
        self.default_skill_policy = default_skill_policy
        self.use_legacy_default_prompt = use_legacy_default_prompt
        self._router: Optional[Router] = None
        self._init_litellm()

    def _get_runtime_config(self):
        config = getattr(self, "config", None)
        if config:
            return config
        try:
            from src import analyzer as analyzer_pkg

            return analyzer_pkg.get_config()
        except Exception:
            return get_config()

    def _init_router(self):
        config = self._get_runtime_config()
        
        # 注入 DeepSeek 新模型的成本映射，防止 LiteLLM 警告或解析阻塞
        try:
            import litellm
            models_to_register = ["deepseek-v4-pro", "deepseek-v4-flash"]
            for m in models_to_register:
                if m not in litellm.model_cost:
                    litellm.model_cost[m] = {
                        "max_tokens": 128000,
                        "input_cost_per_token": 0.0000001,
                        "output_cost_per_token": 0.0000002,
                        "litellm_provider": "deepseek",
                        "mode": "chat"
                    }
        except Exception:
            pass

        if not config.llm_model_list:
            logger.warning("Analyzer LLM: No model list configured, router disabled.")
            return

        logger.info(f"Analyzer LLM: 初始化 Router, 模型列表: {[m.get('model_name') for m in config.llm_model_list]}")
        try:
            self._router = Router(
                model_list=config.llm_model_list,
                routing_strategy="simple-shuffle",
                num_retries=2
            )
            logger.info("Analyzer LLM: Router initialized successfully.")
        except Exception as e:
            logger.error(f"Analyzer LLM: Failed to init router: {e}")

    def _init_litellm(self):
        """向后兼容旧测试与旧初始化钩子名称。"""
        self._init_router()

    def _resolve_prompt_state(self):
        if self.skill_instructions or self.default_skill_policy or self.use_legacy_default_prompt:
            return {
                "skill_instructions": self.skill_instructions,
                "default_skill_policy": self.default_skill_policy,
                "use_legacy_default_prompt": self.use_legacy_default_prompt,
            }

        try:
            from src.agent.factory import resolve_skill_prompt_state

            state = resolve_skill_prompt_state(self._get_runtime_config())
            return {
                "skill_instructions": getattr(state, "skill_instructions", "") or "",
                "default_skill_policy": getattr(state, "default_skill_policy", "") or "",
                "use_legacy_default_prompt": bool(getattr(state, "use_legacy_default_prompt", False)),
            }
        except Exception:
            return {
                "skill_instructions": "",
                "default_skill_policy": "",
                "use_legacy_default_prompt": False,
            }

    def _call_litellm(
        self,
        prompt: str,
        generation_config: dict,
        *,
        system_prompt: Optional[str] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """同步调用 LiteLLM (封装异步调用以兼容旧代码)"""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._call_litellm_async(prompt, generation_config, system_prompt=system_prompt))
        finally:
            loop.close()

    async def _call_litellm_async(
        self,
        prompt: str,
        generation_config: dict,
        *,
        system_prompt: Optional[str] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        config = self._get_runtime_config()
        max_tokens = generation_config.get('max_tokens', 8192)
        temperature = generation_config.get('temperature', 0.7)

        primary_model = config.litellm_model
        if not primary_model and config.llm_model_list:
            primary_model = config.llm_model_list[0].get("model_name")
            
        models_to_try = [primary_model] + (config.litellm_fallback_models or [])
        models_to_try = [m for m in models_to_try if m]

        effective_system_prompt = system_prompt or self.TEXT_SYSTEM_PROMPT
        
        for model in models_to_try:
            try:
                model_short = model.split("/")[-1] if "/" in model else model
                call_kwargs = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": effective_system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": 120,
                }
                extra = get_thinking_extra_body(model_short)
                if extra: call_kwargs["extra_body"] = extra


                _router_model_names = set(get_configured_llm_models(config.llm_model_list))

                # --- 重要：强制 Key 注入逻辑 (解决报告为空的关键) ---
                keys = get_api_keys_for_model(model, config)
                if not keys:
                    # 尝试用短名再搜一遍
                    keys = get_api_keys_for_model(model_short, config)

                if keys:
                    call_kwargs["api_key"] = keys[0]
                    logger.debug(f"[AI] 已手动注入 {model_short} 认证 Key")
                # -----------------------------------------------

                if self._router and (model in _router_model_names or model_short in _router_model_names):
                    effective_model = model if model in _router_model_names else model_short
                    call_kwargs["model"] = effective_model
                    response = await self._router.acompletion(**call_kwargs)
                else:
                    call_kwargs.update(extra_litellm_params(model, config))
                    response = await litellm.acompletion(**call_kwargs)


                if response and response.choices:
                    content = response.choices[0].message.content
                    usage = getattr(response, "usage", {})
                    usage_dict = {
                        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(usage, "completion_tokens", 0),
                        "total_tokens": getattr(usage, "total_tokens", 0),
                    }
                    return content, model, usage_dict
                
            except Exception as e:
                logger.warning(f"[LiteLLM Async] {model} 尝试失败: {e}")
                continue

        raise Exception("所有 LLM 模型调用均已失败")

    async def analyze_async(self, context: Dict[str, Any], news_context: Optional[str] = None) -> AnalysisResult:
        """个股异步分析"""
        code = context.get('code', 'Unknown')
        name = get_stock_name_multi_source(code, context)
        config = self._get_runtime_config()
        
        try:
            prompt = self._format_prompt(context, name, news_context=news_context)
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("分析 Prompt 生成失败")
            response_text, model_used, usage = await self._call_litellm_async(
                prompt,
                {"max_tokens": 8192, "temperature": config.llm_temperature},
                system_prompt=self._get_analysis_system_prompt(
                    getattr(config, "report_language", "zh"),
                    stock_code=code,
                ),
            )
            
            result = self._parse_response(response_text, code, name)
            result.market_snapshot = build_market_snapshot(context)
            result.model_used = model_used
            result.report_language = config.report_language
            
            persist_llm_usage(usage, model_used, call_type="analysis", stock_code=code)
            return result
        except Exception as e:
            logger.error(f"AI 分析 (Async) 失败: {e}")
            return self._make_error_result(code, name, str(e))

    async def generate_text_async(
        self,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs,
    ) -> Optional[str]:
        """原生的异步文本生成接口 (供大盘分析使用)"""
        try:
            res, _, _ = await self._call_litellm_async(
                prompt,
                {"max_tokens": max_tokens, "temperature": temperature, **kwargs},
            )
            return res
        except Exception as e:
            logger.error(f"generate_text_async 失败: {e}")
            return None

    def generate_text(self, prompt: str, max_tokens: int = 2048, temperature: float = 0.7, **kwargs) -> Optional[str]:
        """向后兼容的同步文本生成 (警告：不应在已有 Loop 中调用)"""
        try:
            # 如果是在测试中，直接调用 _call_litellm 以允许 mocking
            if hasattr(self, "_call_litellm"):
                result = self._call_litellm(
                    prompt,
                    generation_config={"max_tokens": max_tokens, "temperature": temperature, **kwargs},
                )
                if isinstance(result, tuple):
                    content, _, _ = result
                else:
                    content = result
                return content
            return asyncio.run(self.generate_text_async(prompt))
        except Exception:
            return None

    def analyze(self, context: Dict[str, Any], news_context: Optional[str] = None) -> AnalysisResult:
        """同步分析包装器"""
        try:
            return asyncio.run(self.analyze_async(context, news_context))
        except Exception:
            code = context.get('code', 'Unknown')
            name = context.get('name', 'Unknown')
            return self._make_error_result(code, name, "同步环境启动分析失败")

    def _get_analysis_system_prompt(self, report_language: str = "zh", *, stock_code: str = "") -> str:
        from src.agent.executor import (
            AGENT_SYSTEM_PROMPT,
            LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT,
            _build_language_section,
        )

        prompt_state = self._resolve_prompt_state()
        skills_section = ""
        if prompt_state["skill_instructions"]:
            skills_section = f"## 激活的交易技能\n\n{prompt_state['skill_instructions']}"
        default_skill_policy_section = ""
        if prompt_state["default_skill_policy"]:
            default_skill_policy_section = f"\n{prompt_state['default_skill_policy']}\n"

        market_role = get_market_role(stock_code, report_language)
        market_guidelines = get_market_guidelines(stock_code, report_language)
        prompt_template = (
            LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT
            if prompt_state["use_legacy_default_prompt"]
            else AGENT_SYSTEM_PROMPT
        )
        return prompt_template.format(
            market_role=market_role,
            market_guidelines=market_guidelines,
            default_skill_policy_section=default_skill_policy_section,
            skills_section=skills_section,
            language_section=_build_language_section(report_language),
        )

    def _format_prompt(self, context: Dict[str, Any], name: str, news_context: Optional[str] = None) -> str:
        config = self._get_runtime_config()
        prompt_state = self._resolve_prompt_state()
        news_window_days = context.get("news_window_days")
        if not news_window_days:
            news_window_days = resolve_news_window_days(
                getattr(config, "news_max_age_days", None),
                getattr(config, "news_strategy_profile", "short"),
            )
        return format_analysis_prompt(
            context,
            name,
            news_context,
            report_language=getattr(config, "report_language", "zh"),
            use_legacy_default_prompt=prompt_state["use_legacy_default_prompt"],
            news_window_days_config=news_window_days,
        )

    def _parse_text_response(self, text: str, code: str, name: str) -> AnalysisResult:
        config = self._get_runtime_config()
        lowered = (text or "").lower()
        is_en = str(getattr(config, "report_language", "zh")).lower().startswith("en")

        if any(token in lowered for token in ("strong sell", "bearish", "sell")):
            trend = "Bearish" if is_en else "看空"
            advice = "Sell" if is_en else "卖出"
            decision = "sell"
        elif any(token in lowered for token in ("strong buy", "bullish", "buy")):
            trend = "Bullish" if is_en else "看多"
            advice = "Buy" if is_en else "买入"
            decision = "buy"
        else:
            trend = "Neutral" if is_en else "震荡"
            advice = "Hold" if is_en else "持有"
            decision = "hold"

        confidence = "Low" if is_en else "低"
        return AnalysisResult(
            code=code,
            name=name,
            sentiment_score=50,
            trend_prediction=trend,
            operation_advice=advice,
            decision_type=decision,
            confidence_level=confidence,
            report_language=getattr(config, "report_language", "zh"),
            analysis_summary=text or "",
            success=True,
            raw_response=text,
        )

    @staticmethod
    def _first_text(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
            else:
                text = str(value).strip()
                if text:
                    return text
        return ""

    @classmethod
    def _normalize_position_advice(cls, payload: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        no_position = cls._first_text(
            payload.get("no_position"),
            payload.get("empty"),
            payload.get("empty_position"),
            payload.get("for_empty_positions"),
            payload.get("entry_plan"),
        )
        has_position = cls._first_text(
            payload.get("has_position"),
            payload.get("holding"),
            payload.get("holding_position"),
            payload.get("for_holding_positions"),
            payload.get("risk_control"),
        )
        result: Dict[str, str] = {}
        if no_position:
            result["no_position"] = no_position
        if has_position:
            result["has_position"] = has_position
        return result

    @staticmethod
    def _normalize_action_checklist(payload: Any) -> List[str]:
        if isinstance(payload, list):
            items: List[str] = []
            for item in payload:
                if isinstance(item, dict):
                    result = str(item.get("result") or item.get("status") or "").strip()
                    question = str(item.get("question") or "").strip()
                    detail = str(item.get("detail") or "").strip()
                    line = " ".join(part for part in (result, question, detail) if part)
                else:
                    line = str(item).strip()
                if line:
                    items.append(line)
            return items
        if not isinstance(payload, dict):
            return []

        items: List[str] = []
        for key, value in payload.items():
            question = str(key).replace("_", " ").strip()
            if isinstance(value, dict):
                status = str(value.get("status") or "").strip()
                if not question:
                    question = str(value.get("question") or "").strip()
                detail = str(value.get("detail") or "").strip()
                line = " ".join(part for part in (status, question, detail) if part)
            else:
                detail = str(value).strip()
                line = " ".join(part for part in (question, detail) if part)
            if line:
                items.append(line)
        return items

    @classmethod
    def _normalize_text_list(cls, payload: Any) -> List[str]:
        if isinstance(payload, list):
            items: List[str] = []
            for item in payload:
                text = cls._first_text(item)
                if text:
                    items.append(text)
            return items
        text = cls._first_text(payload)
        return [text] if text else []

    @staticmethod
    def _normalize_latest_news(payload: Any) -> str:
        if isinstance(payload, list):
            lines = []
            for item in payload:
                if isinstance(item, dict):
                    text = GeminiAnalyzer._first_text(
                        item.get("date"),
                        item.get("title"),
                        item.get("content"),
                    )
                else:
                    text = str(item).strip()
                if text:
                    lines.append(text)
            return "\n".join(lines)
        return str(payload).strip() if payload is not None else ""

    @staticmethod
    def _normalize_decision_type(decision_type: Any, operation_advice: Any) -> str:
        raw = str(decision_type or "").strip().lower()
        if raw in {"buy", "hold", "sell"}:
            return raw

        advice = str(operation_advice or "").strip()
        if any(token in advice for token in ("买", "加仓", "试多")):
            return "buy"
        if any(token in advice for token in ("卖", "减仓", "离场")):
            return "sell"
        return "hold"

    @staticmethod
    def _default_operation_advice(decision_type: str) -> str:
        return {
            "buy": "买入",
            "hold": "持有",
            "sell": "卖出",
        }.get(decision_type, "持有")

    @staticmethod
    def _default_trend_prediction(decision_type: str) -> str:
        return {
            "buy": "看多",
            "hold": "震荡",
            "sell": "看空",
        }.get(decision_type, "震荡")

    @classmethod
    def _normalize_dashboard_payload(cls, payload: Dict[str, Any], fallback_summary: str = "") -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}

        existing = payload.get("dashboard") if isinstance(payload.get("dashboard"), dict) else {}
        dashboard: Dict[str, Any] = dict(existing)

        core_existing = dashboard.get("core_conclusion") if isinstance(dashboard.get("core_conclusion"), dict) else {}
        core_conclusion = cls._first_text(
            core_existing.get("one_sentence"),
            payload.get("core_conclusion"),
            payload.get("summary"),
            payload.get("technical_summary"),
            payload.get("technical_analysis_note"),
            fallback_summary,
        )
        position_advice = cls._normalize_position_advice(
            payload.get("position_advice") if isinstance(payload.get("position_advice"), dict) else core_existing.get("position_advice", {})
        )
        dashboard["core_conclusion"] = {
            **core_existing,
            "one_sentence": core_conclusion,
            "position_advice": position_advice or core_existing.get("position_advice", {}),
        }

        intelligence_existing = dashboard.get("intelligence") if isinstance(dashboard.get("intelligence"), dict) else {}
        risk_alerts = payload.get("risk_alerts")
        positive_catalysts = payload.get("positive_catalysts")
        latest_news = cls._normalize_latest_news(payload.get("latest_news"))
        dashboard["intelligence"] = {
            **intelligence_existing,
            "latest_news": latest_news or intelligence_existing.get("latest_news", ""),
            "risk_alerts": cls._normalize_text_list(risk_alerts) or intelligence_existing.get("risk_alerts", []),
            "positive_catalysts": cls._normalize_text_list(positive_catalysts) or intelligence_existing.get("positive_catalysts", []),
            "sentiment_summary": cls._first_text(
                intelligence_existing.get("sentiment_summary"),
                payload.get("summary"),
                payload.get("technical_summary"),
                payload.get("technical_analysis_note"),
            ),
        }

        battle_existing = dashboard.get("battle_plan") if isinstance(dashboard.get("battle_plan"), dict) else {}
        sniper_existing = battle_existing.get("sniper_points") if isinstance(battle_existing.get("sniper_points"), dict) else {}
        sniper_source = {}
        for candidate_key in ("sniper_levels", "specific_targets", "sniper_points"):
            candidate = payload.get(candidate_key)
            if isinstance(candidate, dict) and candidate:
                sniper_source = candidate
                break
        sniper_points = {
            **sniper_existing,
            "ideal_buy": cls._first_text(sniper_existing.get("ideal_buy"), sniper_source.get("ideal_buy"), sniper_source.get("buy_price")),
            "secondary_buy": cls._first_text(sniper_existing.get("secondary_buy"), sniper_source.get("secondary_buy")),
            "stop_loss": cls._first_text(sniper_existing.get("stop_loss"), sniper_source.get("stop_loss"), sniper_source.get("stop_loss_price")),
            "take_profit": cls._first_text(sniper_existing.get("take_profit"), sniper_source.get("take_profit"), sniper_source.get("target_price")),
        }
        action_checklist = cls._normalize_action_checklist(
            payload.get("checklist") if payload.get("checklist") is not None else battle_existing.get("action_checklist")
        )
        dashboard["battle_plan"] = {
            **battle_existing,
            "sniper_points": sniper_points,
            "action_checklist": action_checklist,
        }

        if "data_perspective" not in dashboard or not isinstance(dashboard.get("data_perspective"), dict):
            dashboard["data_perspective"] = {}

        return dashboard

    @classmethod
    def _parse_score_value(cls, value: Any) -> Optional[int]:
        """Parse an explicit score without treating arbitrary dates/prices as scores."""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                return None

        text = str(value).strip()
        if not text:
            return None

        score_patterns = (
            r"(?i)(?:sentiment_score|system_score|signal_score)\s*[:：=]\s*(-?\d+(?:\.\d+)?)",
            r"(?:综合评分|系统评分|情绪评分|评分|分数)\s*[:：为是]?\s*(-?\d+(?:\.\d+)?)\s*(?:/100|分)?",
            r"(-?\d+(?:\.\d+)?)\s*/\s*100",
            r"^(-?\d+(?:\.\d+)?)\s*(?:分|%)?$",
        )
        for pattern in score_patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            try:
                return int(float(match.group(1)))
            except (TypeError, ValueError, OverflowError):
                continue
        return None

    @classmethod
    def _extract_sentiment_score(
        cls,
        payload: Any,
        data: Any,
        *,
        raw_text: str = "",
        default: int = 50,
    ) -> int:
        def _walk(value: Any, path: Tuple[str, ...]) -> Any:
            current = value
            for key in path:
                if not isinstance(current, dict):
                    return None
                current = current.get(key)
            return current

        candidate_paths = (
            ("sentiment_score",),
            ("system_score",),
            ("signal_score",),
            ("dashboard", "sentiment_score"),
            ("dashboard", "system_score"),
            ("dashboard", "signal_score"),
            ("analysis_summary", "sentiment_score"),
            ("analysis_summary", "system_score"),
            ("analysis_summary", "signal_score"),
        )

        for source in (payload, data):
            if not isinstance(source, dict):
                continue
            for path in candidate_paths:
                parsed = cls._parse_score_value(_walk(source, path))
                if parsed is not None:
                    return parsed

        text_candidates: List[Any] = []
        if isinstance(payload, dict):
            text_candidates.extend([
                payload.get("analysis_summary"),
                payload.get("summary"),
                payload.get("technical_summary"),
                payload.get("technical_analysis_note"),
                payload.get("comment"),
            ])
            dashboard = payload.get("dashboard")
            if isinstance(dashboard, dict):
                core = dashboard.get("core_conclusion")
                if isinstance(core, dict):
                    text_candidates.append(core.get("one_sentence"))
        text_candidates.append(raw_text)

        for candidate in text_candidates:
            parsed = cls._parse_score_value(candidate)
            if parsed is not None:
                return parsed
        return default

    def _parse_response(self, text: str, code: str, name: str) -> AnalysisResult:
        from src.utils.data_processing import extract_json_from_text

        data = extract_json_from_text(text) or {}
        payload = data.get('decision_dashboard') if isinstance(data.get('decision_dashboard'), dict) else data
        summary_text = self._first_text(
            payload.get('analysis_summary') if isinstance(payload, dict) else "",
            payload.get('summary') if isinstance(payload, dict) else "",
            payload.get('technical_summary') if isinstance(payload, dict) else "",
            payload.get('technical_analysis_note') if isinstance(payload, dict) else "",
            payload.get('comment') if isinstance(payload, dict) else "",
            payload.get('core_conclusion') if isinstance(payload, dict) and isinstance(payload.get('core_conclusion'), str) else "",
        )
        operation_advice = self._first_text(
            payload.get('operation_advice') if isinstance(payload, dict) else "",
            data.get('operation_advice'),
        )
        decision_type = self._normalize_decision_type(
            payload.get('decision_type') if isinstance(payload, dict) else data.get('decision_type'),
            operation_advice,
        )
        if not operation_advice:
            operation_advice = self._default_operation_advice(decision_type)
        trend_prediction = self._first_text(
            payload.get('trend_prediction') if isinstance(payload, dict) else "",
            data.get('trend_prediction'),
        ) or self._default_trend_prediction(decision_type)
        dashboard = self._normalize_dashboard_payload(
            payload if isinstance(payload, dict) else {},
            fallback_summary=summary_text,
        )

        return AnalysisResult(
            code=code,
            name=self._first_text(
                payload.get('stock_name') if isinstance(payload, dict) else "",
                data.get('stock_name'),
                name,
            ),
            sentiment_score=self._extract_sentiment_score(payload, data, raw_text=text),
            trend_prediction=trend_prediction,
            operation_advice=operation_advice,
            decision_type=decision_type,
            confidence_level=self._first_text(
                payload.get('confidence_level') if isinstance(payload, dict) else "",
                data.get('confidence_level'),
                '中',
            ),
            report_language=data.get('report_language', 'zh'),
            trend_analysis=self._first_text(
                payload.get('trend_analysis') if isinstance(payload, dict) else "",
                data.get('trend_analysis'),
            ),
            technical_analysis=self._first_text(
                payload.get('technical_analysis') if isinstance(payload, dict) else "",
                payload.get('technical_summary') if isinstance(payload, dict) else "",
                payload.get('technical_analysis_note') if isinstance(payload, dict) else "",
                data.get('technical_analysis'),
            ),
            fundamental_analysis=self._first_text(
                payload.get('fundamental_analysis') if isinstance(payload, dict) else "",
                data.get('fundamental_analysis'),
            ),
            news_summary=self._first_text(
                payload.get('news_summary') if isinstance(payload, dict) else "",
                data.get('news_summary'),
                dashboard.get('intelligence', {}).get('latest_news', ""),
            ),
            analysis_summary=summary_text or data.get('analysis_summary', text[:1000] if not data else ""),
            risk_warning=self._first_text(
                payload.get('risk_warning') if isinstance(payload, dict) else "",
                data.get('risk_warning'),
                "\n".join(dashboard.get('intelligence', {}).get('risk_alerts', [])),
            ),
            dashboard=dashboard,
            success=True,
            raw_response=text,
        )

    def is_available(self) -> bool:
        config = self._get_runtime_config()
        return bool(
            getattr(config, "llm_model_list", None)
            or getattr(config, "litellm_model", None)
            or getattr(config, "gemini_api_key", None)
            or getattr(config, "gemini_api_keys", None)
            or getattr(config, "anthropic_api_key", None)
            or getattr(config, "anthropic_api_keys", None)
            or getattr(config, "openai_api_key", None)
            or getattr(config, "openai_api_keys", None)
            or getattr(config, "deepseek_api_keys", None)
            or os.getenv("DEEPSEEK_API_KEY")
        )

    def _make_error_result(self, code: str, name: str, msg: str) -> AnalysisResult:
        return AnalysisResult(
            code=code, name=name, sentiment_score=50,
            trend_prediction="错误", operation_advice="出错",
            analysis_summary=f"分析失败: {msg}", success=False, error_message=msg
        )
