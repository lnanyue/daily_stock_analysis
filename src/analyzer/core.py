# -*- coding: utf-8 -*-
"""
AI 分析核心逻辑 (GeminiAnalyzer) - 异步全连通版
"""

import logging
import asyncio
import json
import os
import time
from typing import Dict, Any, Optional, List, Tuple

import litellm
from litellm import Router

from src.config import (
    get_config,
    get_configured_llm_models,
    get_api_keys_for_model,
    extra_litellm_params,
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

    def __init__(self, config=None):
        self.config = config or get_config()
        self._router: Optional[Router] = None
        self._init_router()

    def _get_runtime_config(self):
        config = getattr(self, "config", None)
        return config if config else get_config()

    def _init_router(self):
        config = self._get_runtime_config()
        if not config.llm_model_list:
            logger.warning("Analyzer LLM: No model list configured, router disabled.")
            return

        try:
            self._router = Router(
                model_list=config.llm_model_list,
                routing_strategy="simple-shuffle",
                num_retries=2
            )
            logger.info("Analyzer LLM: Router initialized successfully.")
        except Exception as e:
            logger.error(f"Analyzer LLM: Failed to init router: {e}")

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
            prompt = format_analysis_prompt(context, name, news_context, report_language=config.report_language, news_window_days_config=config.news_max_age_days)
            response_text, model_used, usage = await self._call_litellm_async(
                prompt, {"max_tokens": 8192, "temperature": config.llm_temperature}
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

    async def generate_text_async(self, prompt: str) -> Optional[str]:
        """原生的异步文本生成接口 (供大盘分析使用)"""
        try:
            res, _, _ = await self._call_litellm_async(prompt, {"max_tokens": 4096, "temperature": 0.7})
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

    def _parse_response(self, text: str, code: str, name: str) -> AnalysisResult:
        from src.utils.data_processing import extract_json_from_text
        data = extract_json_from_text(text) or {}
        return AnalysisResult(
            code=code, name=data.get('stock_name', name), sentiment_score=data.get('sentiment_score', 50),
            trend_prediction=data.get('trend_prediction', '震荡'), operation_advice=data.get('operation_advice', '持有'),
            decision_type=data.get('decision_type', 'hold'), confidence_level=data.get('confidence_level', '中'),
            report_language=data.get('report_language', 'zh'), trend_analysis=data.get('trend_analysis', ''),
            technical_analysis=data.get('technical_analysis', ''), fundamental_analysis=data.get('fundamental_analysis', ''),
            news_summary=data.get('news_summary', ''), analysis_summary=data.get('analysis_summary', text[:1000] if not data else ""),
            risk_warning=data.get('risk_warning', ''), dashboard=data.get('dashboard', {}), success=True, raw_response=text
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
