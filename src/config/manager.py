# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 配置管理核心
===================================

职责：
1. 维护全局配置单例
2. 聚合 YAML 文件与环境变量
3. 保持重构前的兼容配置入口，避免 CLI / Service / Tests 失效
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import dotenv_values

from src.report_language import (
    is_supported_report_language_value,
    normalize_report_language,
)

from .models import ConfigIssue, LLMChannelConfig
from .utils import (
    SUPPORTED_LLM_CHANNEL_PROTOCOLS,
    _get_litellm_provider,
    _uses_direct_env_provider,
    channel_allows_empty_api_key,
    channels_to_model_list,
    get_configured_llm_models,
    get_effective_agent_primary_model,
    legacy_keys_to_model_list,
    load_settings_from_yaml,
    load_stocks_from_yaml,
    normalize_agent_litellm_model,
    normalize_llm_channel_model,
    normalize_news_strategy_profile,
    parse_env_bool,
    parse_env_float,
    parse_env_int,
    parse_litellm_yaml,
    parse_llm_channels,
    resolve_llm_channel_protocol,
    resolve_news_window_days,
    resolve_unified_llm_temperature,
    setup_env,
)

logger = logging.getLogger(__name__)


@dataclass(init=False)
class Config:
    """系统配置类 - 单例模式。"""

    def __init__(self, **kwargs: Any):
        litellm_model_explicit = "litellm_model" in kwargs
        litellm_fallback_explicit = "litellm_fallback_models" in kwargs
        legacy_gemini_model = kwargs.pop("gemini_model", None)
        legacy_gemini_fallback = kwargs.pop("gemini_model_fallback", None)
        for item in fields(self):
            name = item.name
            if name == "_instance":
                continue
            if name in kwargs:
                value = kwargs.pop(name)
            elif item.default_factory is not MISSING:  # type: ignore[attr-defined]
                value = item.default_factory()  # type: ignore[misc]
            elif item.default is not MISSING:
                value = item.default
            else:
                value = None
            object.__setattr__(self, name, value)

        if legacy_gemini_model and not litellm_model_explicit and not self.litellm_model:
            model = str(legacy_gemini_model).strip()
            self.litellm_model = model if "/" in model else f"gemini/{model}"
        if legacy_gemini_fallback and not litellm_fallback_explicit and not self.litellm_fallback_models:
            fallback_model = str(legacy_gemini_fallback).strip()
            self.litellm_fallback_models = [
                fallback_model if "/" in fallback_model else f"gemini/{fallback_model}"
            ]
        for name, value in kwargs.items():
            object.__setattr__(self, name, value)

    stock_list: List[str] = field(default_factory=list)
    stock_config_path: str = "stocks.yaml"

    report_language: str = "zh"
    news_max_age_days: int = 3
    news_strategy_profile: str = "short"
    bias_threshold: float = 5.0
    gemini_request_delay: float = 2.0
    report_integrity_enabled: bool = True
    report_integrity_retry: int = 1
    analysis_mode: str = "simple"

    max_workers: int = 3
    log_level: str = "INFO"
    log_dir: str = "./logs"
    webui_enabled: bool = False
    webui_port: int = 8000
    debug: bool = False
    config_validate_mode: str = "warn"

    report_type: str = "simple"
    report_summary_only: bool = False
    report_templates_dir: str = "templates"
    merge_email_notification: bool = False
    single_stock_notify: bool = False
    feishu_webhook_url: Optional[str] = None
    wechat_webhook_url: Optional[str] = None
    dingtalk_webhook_url: Optional[str] = None
    dingtalk_stream_enabled: bool = False
    feishu_stream_enabled: bool = False
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_message_thread_id: Optional[str] = None
    email_sender: Optional[str] = None
    email_sender_name: str = "股票分析助手"
    email_password: Optional[str] = None
    email_receivers: List[str] = field(default_factory=list)
    stock_email_groups: List[Tuple[List[str], List[str]]] = field(default_factory=list)
    pushover_user_key: Optional[str] = None
    pushover_api_token: Optional[str] = None
    pushplus_token: Optional[str] = None
    pushplus_topic: Optional[str] = None
    serverchan3_sendkey: Optional[str] = None
    discord_bot_token: Optional[str] = None
    discord_main_channel_id: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    discord_max_words: int = 2000
    slack_webhook_url: Optional[str] = None
    slack_bot_token: Optional[str] = None
    slack_channel_id: Optional[str] = None
    custom_webhook_urls: List[str] = field(default_factory=list)
    custom_webhook_bearer_token: Optional[str] = None
    astrbot_url: Optional[str] = None
    astrbot_token: Optional[str] = None
    webhook_verify_ssl: bool = True
    notification_timeout_sec: int = 15
    wechat_msg_type: str = "markdown"
    wechat_max_bytes: int = 4000
    feishu_max_bytes: int = 20000

    schedule_enabled: bool = False
    schedule_time: str = "18:00"
    schedule_run_immediately: bool = True
    run_immediately: bool = True
    market_review_enabled: bool = True
    market_review_region: str = "cn"
    trading_day_check_enabled: bool = True

    prefetch_realtime_quotes: bool = True
    realtime_cache_ttl: int = 600
    realtime_source_priority: str = "tencent,akshare_sina,efinance,akshare_em"
    enable_eastmoney_patch: bool = False
    database_path: str = "./data/stock_analysis.db"
    save_context_snapshot: bool = True
    tushare_token: Optional[str] = None
    tickflow_api_key: Optional[str] = None
    enable_realtime_quote: bool = True
    enable_realtime_technical_indicators: bool = True
    enable_chip_distribution: bool = True
    sqlite_wal_enabled: bool = True
    sqlite_busy_timeout_ms: int = 5000
    sqlite_write_retry_max: int = 3
    sqlite_write_retry_base_delay: float = 0.1

    litellm_model: str = ""
    litellm_fallback_models: List[str] = field(default_factory=list)
    litellm_config_path: Optional[str] = None
    llm_channels: List[Dict[str, Any]] = field(default_factory=list)
    llm_model_list: List[Dict[str, Any]] = field(default_factory=list)
    llm_temperature: float = 0.7
    llm_models_source: str = "legacy_env"
    agent_litellm_model: str = ""

    agent_mode: bool = False
    _agent_mode_explicit: bool = False
    agent_max_steps: int = 10
    agent_skills: List[str] = field(default_factory=list)
    agent_arch: str = "single"
    agent_orchestrator_mode: str = "standard"
    agent_orchestrator_timeout_s: int = 600
    agent_risk_override: bool = True
    agent_memory_enabled: bool = False
    agent_skill_autoweight: bool = True
    agent_skill_routing: str = "auto"

    gemini_api_keys: List[str] = field(default_factory=list)
    gemini_api_key: Optional[str] = None
    anthropic_api_keys: List[str] = field(default_factory=list)
    anthropic_api_key: Optional[str] = None
    openai_api_keys: List[str] = field(default_factory=list)
    deepseek_api_keys: List[str] = field(default_factory=list)
    bocha_api_keys: List[str] = field(default_factory=list)
    minimax_api_keys: List[str] = field(default_factory=list)
    tavily_api_keys: List[str] = field(default_factory=list)
    exa_api_keys: List[str] = field(default_factory=list)
    serpapi_keys: List[str] = field(default_factory=list)
    brave_api_keys: List[str] = field(default_factory=list)

    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_folder_token: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_vision_model: Optional[str] = None
    vision_model: str = ""
    vision_provider_priority: str = "gemini,anthropic,openai"
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    social_sentiment_api_key: Optional[str] = None
    social_sentiment_api_url: str = "https://api.adanos.org"
    searxng_base_urls: List[str] = field(default_factory=list)
    searxng_public_instances_enabled: bool = False

    _instance: Optional["Config"] = None

    @classmethod
    def get_instance(cls) -> "Config":
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance

    @classmethod
    def _call_setup_env(cls, *, override: bool = False) -> None:
        config_pkg = sys.modules.get("src.config")
        setup = getattr(config_pkg, "setup_env", None) if config_pkg is not None else None
        if callable(setup):
            setup(override=override)
            return
        setup_env(override=override)

    @classmethod
    def _load_from_env(cls) -> "Config":
        preexisting_report_language = os.getenv("REPORT_LANGUAGE")
        cls._call_setup_env()

        settings = load_settings_from_yaml("settings.yaml")
        ana_s = settings.get("analysis", {})
        sys_s = settings.get("system", {})
        not_s = settings.get("notification", {})
        dat_s = settings.get("data", {})
        sch_s = settings.get("schedule", {})

        def _get_keys(plural: str, singular: str) -> List[str]:
            val = os.getenv(plural, "")
            if val:
                return [k.strip() for k in val.split(",") if k.strip()]
            single = os.getenv(singular, "").strip()
            return [single] if single else []

        stock_config_path = os.getenv("STOCK_CONFIG_PATH", "stocks.yaml")
        stock_list = load_stocks_from_yaml(stock_config_path)
        if not stock_list:
            stock_list = [s.strip().upper() for s in os.getenv("STOCK_LIST", "").split(",") if s.strip()]
        if not stock_list:
            stock_list = ["600519", "000001", "300750"]

        report_language = cls._parse_report_language(
            cls._resolve_report_language_env_value(preexisting_report_language)
            or ana_s.get("language", "zh")
        )

        gemini_keys = _get_keys("GEMINI_API_KEYS", "GEMINI_API_KEY")
        anthropic_keys = _get_keys("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY")
        openai_keys = _get_keys("OPENAI_API_KEYS", "OPENAI_API_KEY")
        if not openai_keys:
            aihubmix_key = os.getenv("AIHUBMIX_KEY", "").strip()
            if aihubmix_key:
                openai_keys = [aihubmix_key]
        deepseek_keys = _get_keys("DEEPSEEK_API_KEYS", "DEEPSEEK_API_KEY")

        litellm_model = (os.getenv("LITELLM_MODEL") or "").strip()
        if not litellm_model:
            if gemini_keys:
                litellm_model = f"gemini/{(os.getenv('GEMINI_MODEL') or 'gemini-3-flash-preview').strip()}"
            elif anthropic_keys:
                litellm_model = f"anthropic/{(os.getenv('ANTHROPIC_MODEL') or 'claude-3-5-sonnet-20241022').strip()}"
            elif deepseek_keys:
                litellm_model = "deepseek/deepseek-chat"
            elif openai_keys:
                openai_model_name = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
                litellm_model = openai_model_name if "/" in openai_model_name else f"openai/{openai_model_name}"

        fallback_raw = os.getenv("LITELLM_FALLBACK_MODELS", "")
        if fallback_raw.strip():
            litellm_fallback_models = [m.strip() for m in fallback_raw.split(",") if m.strip()]
        else:
            gemini_fallback = (os.getenv("GEMINI_MODEL_FALLBACK") or "gemini-2.5-flash").strip()
            if litellm_model.startswith("gemini/") and gemini_fallback:
                litellm_fallback_models = [
                    gemini_fallback if "/" in gemini_fallback else f"gemini/{gemini_fallback}"
                ]
            else:
                litellm_fallback_models = []

        litellm_config_path = (os.getenv("LITELLM_CONFIG") or "litellm_config.yaml").strip()
        llm_models_source = "legacy_env"
        llm_channels: List[Dict[str, Any]] = []
        llm_model_list: List[Dict[str, Any]] = []

        if litellm_config_path:
            llm_model_list = cls._parse_litellm_yaml(litellm_config_path)
            if llm_model_list:
                llm_models_source = "litellm_config"

        if not llm_model_list:
            channels_str = (os.getenv("LLM_CHANNELS") or "").strip()
            if channels_str:
                llm_channels = cls._parse_llm_channels(channels_str)
                llm_model_list = cls._channels_to_model_list(llm_channels)
                if llm_model_list:
                    llm_models_source = "llm_channels"

        if not llm_model_list:
            openai_base_url = os.getenv("OPENAI_BASE_URL") or (
                "https://aihubmix.com/v1" if os.getenv("AIHUBMIX_KEY") else None
            )
            llm_model_list = cls._legacy_keys_to_model_list(
                gemini_keys,
                anthropic_keys,
                openai_keys,
                openai_base_url,
                deepseek_keys,
            )
            if llm_model_list:
                llm_models_source = "legacy_env"

        if not litellm_model and llm_channels:
            for channel in llm_channels:
                models = channel.get("models") or []
                if models:
                    litellm_model = models[0]
                    break

        if not litellm_fallback_models and llm_channels and litellm_model:
            seen = {litellm_model}
            inferred_fallbacks: List[str] = []
            for channel in llm_channels:
                for model in channel.get("models", []) or []:
                    if model in seen:
                        continue
                    seen.add(model)
                    inferred_fallbacks.append(model)
            litellm_fallback_models = inferred_fallbacks

        configured_models = set(get_configured_llm_models(llm_model_list))
        agent_litellm_model = normalize_agent_litellm_model(
            os.getenv("AGENT_LITELLM_MODEL", ""),
            configured_models=configured_models,
        )

        legacy_run_immediately_env = os.getenv("RUN_IMMEDIATELY")
        legacy_run_immediately = (
            legacy_run_immediately_env.lower() == "true"
            if legacy_run_immediately_env is not None
            else True
        )
        schedule_run_immediately_env = os.getenv("SCHEDULE_RUN_IMMEDIATELY")
        schedule_run_immediately = (
            schedule_run_immediately_env.lower() == "true"
            if schedule_run_immediately_env is not None
            else legacy_run_immediately
        )

        wechat_msg_type = (os.getenv("WECHAT_MSG_TYPE") or "markdown").strip().lower() or "markdown"
        wechat_max_bytes_env = os.getenv("WECHAT_MAX_BYTES")
        if wechat_max_bytes_env not in (None, ""):
            wechat_max_bytes = parse_env_int(
                wechat_max_bytes_env,
                2048 if wechat_msg_type == "text" else 4000,
                field_name="WECHAT_MAX_BYTES",
                minimum=1,
            )
        else:
            wechat_max_bytes = 2048 if wechat_msg_type == "text" else 4000

        notification_timeout_sec = parse_env_int(
            os.getenv("NOTIFICATION_TIMEOUT_SEC"),
            15,
            field_name="NOTIFICATION_TIMEOUT_SEC",
            minimum=1,
        )

        agent_mode_env = os.getenv("AGENT_MODE")
        agent_arch = (os.getenv("AGENT_ARCH") or "single").strip().lower() or "single"
        if agent_arch not in {"single", "multi"}:
            agent_arch = "single"

        return cls(
            stock_list=stock_list,
            stock_config_path=stock_config_path,
            report_language=report_language,
            news_max_age_days=parse_env_int(
                os.getenv("NEWS_MAX_AGE_DAYS"),
                3,
                field_name="NEWS_MAX_AGE_DAYS",
                minimum=1,
            ),
            news_strategy_profile=normalize_news_strategy_profile(
                os.getenv("NEWS_STRATEGY_PROFILE") or ana_s.get("strategy_profile", "short")
            ),
            bias_threshold=parse_env_float(
                os.getenv("BIAS_THRESHOLD"),
                float(ana_s.get("bias_threshold", 5.0)),
                field_name="BIAS_THRESHOLD",
            ),
            gemini_request_delay=parse_env_float(
                os.getenv("ANALYSIS_REQUEST_DELAY"),
                float(ana_s.get("request_delay", 2.0)),
                field_name="ANALYSIS_REQUEST_DELAY",
                minimum=0.0,
            ),
            report_integrity_enabled=parse_env_bool(
                os.getenv("REPORT_INTEGRITY_ENABLED"),
                default=True,
            ),
            report_integrity_retry=parse_env_int(
                os.getenv("REPORT_INTEGRITY_RETRY"),
                int(ana_s.get("integrity_retry", 1)),
                field_name="REPORT_INTEGRITY_RETRY",
                minimum=0,
            ),
            analysis_mode=(os.getenv("ANALYSIS_MODE") or ana_s.get("mode", "simple")).strip() or "simple",
            max_workers=parse_env_int(
                os.getenv("MAX_WORKERS"),
                3,
                field_name="MAX_WORKERS",
                minimum=1,
            ),
            log_level=(os.getenv("LOG_LEVEL") or sys_s.get("log_level", "INFO")).strip() or "INFO",
            log_dir=(os.getenv("LOG_DIR") or sys_s.get("log_dir", "./logs")).strip() or "./logs",
            webui_enabled=parse_env_bool(
                os.getenv("WEBUI_ENABLED"),
                default=bool(sys_s.get("webui_enabled", False)),
            ),
            webui_port=parse_env_int(
                os.getenv("WEBUI_PORT"),
                8000,
                field_name="WEBUI_PORT",
                minimum=1,
                maximum=65535,
            ),
            debug=parse_env_bool(os.getenv("DEBUG"), default=bool(sys_s.get("debug", False))),
            config_validate_mode=(os.getenv("CONFIG_VALIDATE_MODE") or "warn").strip().lower() or "warn",
            report_type=((os.getenv("REPORT_TYPE") or not_s.get("report_type", "simple")).strip().lower() or "simple"),
            report_summary_only=parse_env_bool(
                os.getenv("REPORT_SUMMARY_ONLY"),
                default=bool(not_s.get("summary_only", False)),
            ),
            merge_email_notification=parse_env_bool(
                os.getenv("MERGE_EMAIL_NOTIFICATION"),
                default=bool(not_s.get("merge_email", False)),
            ),
            single_stock_notify=parse_env_bool(os.getenv("SINGLE_STOCK_NOTIFY"), default=False),
            feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL"),
            wechat_webhook_url=os.getenv("WECHAT_WEBHOOK_URL"),
            dingtalk_webhook_url=os.getenv("DINGTALK_WEBHOOK_URL"),
            dingtalk_stream_enabled=parse_env_bool(os.getenv("DINGTALK_STREAM_ENABLED"), default=False),
            feishu_stream_enabled=parse_env_bool(os.getenv("FEISHU_STREAM_ENABLED"), default=False),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            telegram_message_thread_id=os.getenv("TELEGRAM_MESSAGE_THREAD_ID"),
            email_sender=os.getenv("EMAIL_SENDER"),
            email_sender_name=os.getenv("EMAIL_SENDER_NAME", "股票分析助手"),
            email_password=os.getenv("EMAIL_PASSWORD"),
            email_receivers=[r.strip() for r in os.getenv("EMAIL_RECEIVERS", "").split(",") if r.strip()],
            stock_email_groups=cls._parse_stock_email_groups(),
            pushover_user_key=os.getenv("PUSHOVER_USER_KEY"),
            pushover_api_token=os.getenv("PUSHOVER_API_TOKEN"),
            pushplus_token=os.getenv("PUSHPLUS_TOKEN"),
            pushplus_topic=os.getenv("PUSHPLUS_TOPIC"),
            serverchan3_sendkey=os.getenv("SERVERCHAN3_SENDKEY"),
            discord_bot_token=os.getenv("DISCORD_BOT_TOKEN"),
            discord_main_channel_id=os.getenv("DISCORD_MAIN_CHANNEL_ID") or os.getenv("DISCORD_CHANNEL_ID"),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            discord_max_words=parse_env_int(
                os.getenv("DISCORD_MAX_WORDS"),
                2000,
                field_name="DISCORD_MAX_WORDS",
                minimum=1,
            ),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN"),
            slack_channel_id=os.getenv("SLACK_CHANNEL_ID"),
            custom_webhook_urls=[u.strip() for u in os.getenv("CUSTOM_WEBHOOK_URLS", "").split(",") if u.strip()],
            custom_webhook_bearer_token=os.getenv("CUSTOM_WEBHOOK_BEARER_TOKEN"),
            astrbot_url=os.getenv("ASTRBOT_URL"),
            astrbot_token=os.getenv("ASTRBOT_TOKEN"),
            webhook_verify_ssl=parse_env_bool(os.getenv("WEBHOOK_VERIFY_SSL"), default=True),
            notification_timeout_sec=notification_timeout_sec,
            wechat_msg_type=wechat_msg_type,
            wechat_max_bytes=wechat_max_bytes,
            feishu_max_bytes=parse_env_int(
                os.getenv("FEISHU_MAX_BYTES"),
                20000,
                field_name="FEISHU_MAX_BYTES",
                minimum=1,
            ),
            schedule_enabled=parse_env_bool(
                os.getenv("SCHEDULE_ENABLED"),
                default=bool(sch_s.get("enabled", False)),
            ),
            schedule_time=(os.getenv("SCHEDULE_TIME") or sch_s.get("time", "18:00")).strip() or "18:00",
            schedule_run_immediately=schedule_run_immediately,
            run_immediately=legacy_run_immediately,
            market_review_enabled=parse_env_bool(os.getenv("MARKET_REVIEW_ENABLED"), default=True),
            market_review_region=((os.getenv("MARKET_REVIEW_REGION") or "cn").strip().lower() or "cn"),
            trading_day_check_enabled=parse_env_bool(
                os.getenv("TRADING_DAY_CHECK_ENABLED"),
                default=True,
            ),
            prefetch_realtime_quotes=parse_env_bool(
                os.getenv("PREFETCH_REALTIME_QUOTES"),
                default=bool(dat_s.get("prefetch_quotes", True)),
            ),
            realtime_cache_ttl=parse_env_int(
                os.getenv("REALTIME_CACHE_TTL"),
                int(dat_s.get("cache_ttl", 600)),
                field_name="REALTIME_CACHE_TTL",
                minimum=1,
            ),
            realtime_source_priority=cls._resolve_realtime_source_priority(),
            enable_eastmoney_patch=parse_env_bool(
                os.getenv("ENABLE_EASTMONEY_PATCH"),
                default=bool(dat_s.get("eastmoney_patch", False)),
            ),
            database_path=(os.getenv("DATABASE_PATH") or dat_s.get("database_path", "./data/stock_analysis.db")).strip() or "./data/stock_analysis.db",
            save_context_snapshot=parse_env_bool(os.getenv("SAVE_CONTEXT_SNAPSHOT"), default=True),
            tushare_token=os.getenv("TUSHARE_TOKEN"),
            tickflow_api_key=os.getenv("TICKFLOW_API_KEY"),
            enable_realtime_quote=parse_env_bool(os.getenv("ENABLE_REALTIME_QUOTE"), default=True),
            enable_realtime_technical_indicators=parse_env_bool(
                os.getenv("ENABLE_REALTIME_TECHNICAL_INDICATORS"),
                default=True,
            ),
            enable_chip_distribution=parse_env_bool(os.getenv("ENABLE_CHIP_DISTRIBUTION"), default=True),
            sqlite_wal_enabled=parse_env_bool(os.getenv("SQLITE_WAL_ENABLED"), default=True),
            sqlite_busy_timeout_ms=parse_env_int(
                os.getenv("SQLITE_BUSY_TIMEOUT_MS"),
                5000,
                field_name="SQLITE_BUSY_TIMEOUT_MS",
                minimum=0,
            ),
            sqlite_write_retry_max=parse_env_int(
                os.getenv("SQLITE_WRITE_RETRY_MAX"),
                3,
                field_name="SQLITE_WRITE_RETRY_MAX",
                minimum=0,
            ),
            sqlite_write_retry_base_delay=parse_env_float(
                os.getenv("SQLITE_WRITE_RETRY_BASE_DELAY"),
                0.1,
                field_name="SQLITE_WRITE_RETRY_BASE_DELAY",
                minimum=0.0,
            ),
            searxng_base_urls=[u.strip() for u in os.getenv("SEARXNG_BASE_URLS", "").split(",") if u.strip()],
            searxng_public_instances_enabled=parse_env_bool(
                os.getenv("SEARXNG_PUBLIC_INSTANCES_ENABLED"),
                default=False,
            ),
            litellm_model=litellm_model,
            litellm_fallback_models=litellm_fallback_models,
            litellm_config_path=litellm_config_path,
            llm_channels=llm_channels,
            llm_model_list=llm_model_list,
            llm_temperature=resolve_unified_llm_temperature(litellm_model),
            llm_models_source=llm_models_source,
            agent_litellm_model=agent_litellm_model,
            agent_mode=parse_env_bool(agent_mode_env, default=False),
            _agent_mode_explicit=agent_mode_env is not None,
            agent_max_steps=parse_env_int(
                os.getenv("AGENT_MAX_STEPS"),
                10,
                field_name="AGENT_MAX_STEPS",
                minimum=1,
            ),
            agent_skills=[s.strip() for s in os.getenv("AGENT_SKILLS", "").split(",") if s.strip()],
            agent_arch=agent_arch,
            agent_orchestrator_mode=((os.getenv("AGENT_ORCHESTRATOR_MODE") or "standard").strip().lower() or "standard"),
            agent_orchestrator_timeout_s=parse_env_int(
                os.getenv("AGENT_ORCHESTRATOR_TIMEOUT_S"),
                600,
                field_name="AGENT_ORCHESTRATOR_TIMEOUT_S",
                minimum=0,
            ),
            agent_risk_override=parse_env_bool(os.getenv("AGENT_RISK_OVERRIDE"), default=True),
            agent_memory_enabled=parse_env_bool(os.getenv("AGENT_MEMORY_ENABLED"), default=False),
            agent_skill_autoweight=parse_env_bool(os.getenv("AGENT_SKILL_AUTOWEIGHT"), default=True),
            agent_skill_routing=((os.getenv("AGENT_SKILL_ROUTING") or "auto").strip().lower() or "auto"),
            gemini_api_keys=gemini_keys,
            gemini_api_key=gemini_keys[0] if gemini_keys else None,
            anthropic_api_keys=anthropic_keys,
            anthropic_api_key=anthropic_keys[0] if anthropic_keys else None,
            openai_api_keys=openai_keys,
            deepseek_api_keys=deepseek_keys,
            bocha_api_keys=_get_keys("BOCHA_API_KEYS", "BOCHA_API_KEY"),
            minimax_api_keys=_get_keys("MINIMAX_API_KEYS", "MINIMAX_API_KEY"),
            tavily_api_keys=_get_keys("TAVILY_API_KEYS", "TAVILY_API_KEY"),
            exa_api_keys=_get_keys("EXA_API_KEYS", "EXA_API_KEY"),
            serpapi_keys=_get_keys("SERPAPI_API_KEYS", "SERPAPI_API_KEY"),
            brave_api_keys=_get_keys("BRAVE_API_KEYS", "BRAVE_API_KEY"),
            feishu_app_id=os.getenv("FEISHU_APP_ID"),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET"),
            feishu_folder_token=os.getenv("FEISHU_FOLDER_TOKEN"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=os.getenv("OPENAI_BASE_URL"),
            openai_model=(os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip(),
            openai_vision_model=os.getenv("OPENAI_VISION_MODEL"),
            vision_model=(os.getenv("VISION_MODEL") or "").strip(),
            vision_provider_priority=(os.getenv("VISION_PROVIDER_PRIORITY") or "gemini,anthropic,openai").strip() or "gemini,anthropic,openai",
            anthropic_model=(os.getenv("ANTHROPIC_MODEL") or "claude-3-5-sonnet-20241022").strip(),
            report_templates_dir=(os.getenv("REPORT_TEMPLATES_DIR") or "templates").strip() or "templates",
            social_sentiment_api_key=os.getenv("SOCIAL_SENTIMENT_API_KEY"),
        )

    @classmethod
    def _parse_litellm_yaml(cls, config_path: str) -> List[Dict[str, Any]]:
        logger.info(f"正在从加载 LiteLLM 配置: {config_path}")
        return parse_litellm_yaml(config_path)

    @classmethod
    def _parse_llm_channels(cls, channels_str: str) -> List[Dict[str, Any]]:
        return parse_llm_channels(channels_str)

    @classmethod
    def _channels_to_model_list(cls, channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return channels_to_model_list(channels)

    @classmethod
    def _legacy_keys_to_model_list(
        cls,
        gemini_keys: List[str],
        anthropic_keys: List[str],
        openai_keys: List[str],
        openai_base_url: Optional[str],
        deepseek_keys: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return legacy_keys_to_model_list(
            gemini_keys,
            anthropic_keys,
            openai_keys,
            openai_base_url,
            deepseek_keys,
        )

    @classmethod
    def _get_env_file_value(cls, key: str) -> Optional[str]:
        env_file = os.getenv("ENV_FILE")
        env_path = Path(env_file) if env_file else (Path(__file__).resolve().parents[2] / ".env")
        if not env_path.exists():
            return None
        try:
            env_values = dotenv_values(env_path)
        except Exception as exc:
            logger.warning("Failed to read %s while resolving %s: %s", env_path, key, exc)
            return None
        value = env_values.get(key)
        return None if value is None else str(value)

    @classmethod
    def _resolve_report_language_env_value(cls, preexisting_env_value: Optional[str]) -> str:
        file_value = cls._get_env_file_value("REPORT_LANGUAGE")
        env_value = os.getenv("REPORT_LANGUAGE")
        if preexisting_env_value is not None:
            env_text = preexisting_env_value.strip()
            file_text = (file_value or "").strip()
            if file_text and env_text and env_text.lower() != file_text.lower():
                env_file = os.getenv("ENV_FILE") or str(Path(__file__).resolve().parents[2] / ".env")
                logger.warning(
                    "REPORT_LANGUAGE environment value '%s' overrides %s ('%s')",
                    preexisting_env_value,
                    env_file,
                    file_value,
                )
            return preexisting_env_value
        if file_value is not None:
            return file_value
        return env_value or "zh"

    @classmethod
    def _parse_report_language(cls, value: Optional[str]) -> str:
        normalized = normalize_report_language(value, default="zh")
        raw = (value or "").strip()
        if raw and not is_supported_report_language_value(raw):
            logger.warning(
                "REPORT_LANGUAGE '%s' invalid, fallback to 'zh' (valid: zh/en)",
                value,
            )
        return normalized

    @classmethod
    def _resolve_realtime_source_priority(cls) -> str:
        explicit = os.getenv("REALTIME_SOURCE_PRIORITY")
        default_priority = "tencent,akshare_sina,efinance,akshare_em"
        if explicit is not None and explicit.strip():
            return explicit.strip()
        if os.getenv("TUSHARE_TOKEN", "").strip():
            return f"tushare,{default_priority}"
        return default_priority

    @classmethod
    def _parse_stock_email_groups(cls) -> List[Tuple[List[str], List[str]]]:
        groups: Dict[int, Dict[str, List[str]]] = {}
        stock_re = re.compile(r"^STOCK_GROUP_(\d+)$", re.IGNORECASE)
        email_re = re.compile(r"^EMAIL_GROUP_(\d+)$", re.IGNORECASE)
        for key, value in os.environ.items():
            stock_match = stock_re.match(key)
            if stock_match:
                idx = int(stock_match.group(1))
                groups.setdefault(idx, {})["stocks"] = [c.strip() for c in value.split(",") if c.strip()]
                continue
            email_match = email_re.match(key)
            if email_match:
                idx = int(email_match.group(1))
                groups.setdefault(idx, {})["emails"] = [e.strip() for e in value.split(",") if e.strip()]
        result: List[Tuple[List[str], List[str]]] = []
        for idx in sorted(groups):
            group = groups[idx]
            stocks = group.get("stocks") or []
            emails = group.get("emails") or []
            if stocks and emails:
                result.append((stocks, emails))
        return result

    def refresh_stock_list(self) -> None:
        stock_list = load_stocks_from_yaml(self.stock_config_path)
        if not stock_list:
            env_file = os.getenv("ENV_FILE")
            env_path = Path(env_file) if env_file else (Path(__file__).resolve().parents[2] / ".env")
            stock_list_str = ""
            if env_path.exists():
                try:
                    stock_list_str = str(dotenv_values(env_path).get("STOCK_LIST") or "").strip()
                except Exception:
                    stock_list_str = ""
            if not stock_list_str:
                self._call_setup_env()
                stock_list_str = os.getenv("STOCK_LIST", "")
            stock_list = [s.strip().upper() for s in stock_list_str.split(",") if s.strip()]
        if stock_list:
            self.stock_list = stock_list
            logger.info("股票列表已更新，当前共 %s 只股票", len(self.stock_list))

    def validate_structured(self) -> List[ConfigIssue]:
        issues: List[ConfigIssue] = []

        def _has_any_key(keys: List[str]) -> bool:
            return any((key or "").strip() for key in (keys or []))

        def _has_valid_key(keys: List[str]) -> bool:
            return any((key or "").strip() and len((key or "").strip()) >= 8 for key in (keys or []))

        def _has_provider_key(provider: str) -> bool:
            normalized_provider = (provider or "").strip().lower()
            if normalized_provider in {"gemini", "vertex_ai"}:
                return _has_any_key(self.gemini_api_keys) or bool((self.gemini_api_key or "").strip())
            if normalized_provider == "anthropic":
                return _has_any_key(self.anthropic_api_keys) or bool((self.anthropic_api_key or "").strip())
            if normalized_provider == "openai":
                return _has_any_key(self.openai_api_keys) or bool((self.openai_api_key or "").strip())
            if normalized_provider == "deepseek":
                return _has_any_key(self.deepseek_api_keys) or bool((os.getenv("DEEPSEEK_API_KEY") or "").strip())
            return False

        def _has_valid_provider_key(provider: str) -> bool:
            normalized_provider = (provider or "").strip().lower()
            if normalized_provider in {"gemini", "vertex_ai"}:
                return _has_valid_key(self.gemini_api_keys)
            if normalized_provider == "anthropic":
                return _has_valid_key(self.anthropic_api_keys)
            if normalized_provider == "openai":
                return _has_valid_key(self.openai_api_keys)
            if normalized_provider == "deepseek":
                return _has_valid_key(self.deepseek_api_keys) or len((os.getenv("DEEPSEEK_API_KEY") or "").strip()) >= 8
            return False

        def _has_runtime_source_for_model(model: str) -> bool:
            normalized_model = (model or "").strip()
            if not normalized_model:
                return False
            if _uses_direct_env_provider(normalized_model):
                return True
            return _has_provider_key(_get_litellm_provider(normalized_model))

        available_models: set[str] = set()
        for item in self.llm_model_list or []:
            if not isinstance(item, dict):
                continue
            model_name = str(item.get("model_name") or "").strip()
            if model_name:
                available_models.add(model_name)
            litellm_params = item.get("litellm_params") or {}
            if isinstance(litellm_params, dict):
                raw_model = str(litellm_params.get("model") or "").strip()
                if raw_model:
                    available_models.add(raw_model)
        has_only_legacy_models = bool(available_models) and all(model.startswith("__legacy_") for model in available_models)

        if not self.stock_list:
            issues.append(ConfigIssue("error", "STOCK_LIST 未配置，无法执行股票分析。", field="STOCK_LIST"))

        primary_model = (self.litellm_model or "").strip()
        if not self.llm_model_list and not _has_runtime_source_for_model(primary_model):
            issues.append(ConfigIssue("error", "LLM 未配置，AI 功能不可用。", field="LITELLM_MODEL"))

        if self.llm_model_list and not primary_model:
            issues.append(ConfigIssue("info", "LITELLM_MODEL 未设置，将使用默认模型选择策略。", field="LITELLM_MODEL"))

        if (
            primary_model
            and self.llm_model_list
            and not has_only_legacy_models
            and primary_model not in available_models
            and not _has_runtime_source_for_model(primary_model)
        ):
            issues.append(
                ConfigIssue(
                    "error",
                    "LITELLM_MODEL 未在当前启用的通道模型中声明，且不存在匹配的运行时来源。",
                    field="LITELLM_MODEL",
                )
            )

        agent_model_raw = (self.agent_litellm_model or "").strip()
        agent_model = normalize_agent_litellm_model(agent_model_raw, configured_models=available_models)
        if agent_model_raw:
            if self.llm_model_list:
                if agent_model not in available_models and not _has_runtime_source_for_model(agent_model):
                    issues.append(
                        ConfigIssue(
                            "error",
                            "AGENT_LITELLM_MODEL 未在当前启用的通道模型中声明，且不存在匹配的运行时来源。",
                            field="AGENT_LITELLM_MODEL",
                        )
                    )
            elif not _has_runtime_source_for_model(agent_model):
                issues.append(
                    ConfigIssue(
                        "error",
                        "AGENT_LITELLM_MODEL 已配置，但不存在可用的运行时来源。",
                        field="AGENT_LITELLM_MODEL",
                    )
                )

        vision_model = (self.vision_model or "").strip()
        if vision_model:
            if self.llm_model_list and vision_model not in available_models and not _has_runtime_source_for_model(vision_model):
                issues.append(
                    ConfigIssue(
                        "warning",
                        "VISION_MODEL 未在当前启用的通道模型中声明，图像分析能力可能不可用。",
                        field="VISION_MODEL",
                    )
                )
            if not _has_valid_provider_key(_get_litellm_provider(vision_model)) and not any(
                str((item.get("litellm_params") or {}).get("model") or "").strip() == vision_model
                and len(str((item.get("litellm_params") or {}).get("api_key") or "").strip()) >= 8
                for item in (self.llm_model_list or [])
                if isinstance(item, dict)
            ):
                issues.append(
                    ConfigIssue(
                        "warning",
                        "VISION_MODEL 已配置，但缺少对应 provider 的有效 API Key。",
                        field="VISION_MODEL",
                    )
                )

        if os.getenv("OPENAI_VISION_MODEL"):
            issues.append(
                ConfigIssue(
                    "info",
                    "OPENAI_VISION_MODEL 已废弃，请迁移到 VISION_MODEL。",
                    field="OPENAI_VISION_MODEL",
                )
            )

        has_notification = bool(
            self.wechat_webhook_url
            or self.feishu_webhook_url
            or self.dingtalk_webhook_url
            or (self.telegram_bot_token and self.telegram_chat_id)
            or (self.email_sender and self.email_password)
            or (self.discord_bot_token and self.discord_main_channel_id)
            or self.discord_webhook_url
            or self.slack_webhook_url
            or (self.slack_bot_token and self.slack_channel_id)
            or self.pushplus_token
            or self.serverchan3_sendkey
            or (self.pushover_user_key and self.pushover_api_token)
            or self.custom_webhook_urls
            or self.astrbot_url
        )
        if not has_notification:
            issues.append(ConfigIssue("warning", "未配置通知渠道，分析结果将不会自动推送。", field="WECHAT_WEBHOOK_URL"))

        has_search = bool(
            self.bocha_api_keys
            or self.minimax_api_keys
            or self.tavily_api_keys
            or self.exa_api_keys
            or self.brave_api_keys
            or self.serpapi_keys
            or self.has_searxng_enabled()
        )
        if not has_search:
            issues.append(ConfigIssue("info", "搜索引擎未配置，新闻检索能力将受限。", field="BOCHA_API_KEYS"))

        return issues

    def validate(self) -> List[str]:
        return [str(issue) for issue in self.validate_structured()]

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def has_searxng_enabled(self) -> bool:
        return bool(self.searxng_base_urls or self.searxng_public_instances_enabled)

    def has_search_capability_enabled(self) -> bool:
        return bool(
            self.bocha_api_keys
            or self.minimax_api_keys
            or self.tavily_api_keys
            or self.exa_api_keys
            or self.brave_api_keys
            or self.serpapi_keys
            or self.has_searxng_enabled()
        )

    def is_agent_available(self) -> bool:
        if self._agent_mode_explicit:
            return self.agent_mode
        return bool(get_effective_agent_primary_model(self))

    @property
    def gemini_model(self) -> str:
        return self.litellm_model

    @property
    def gemini_model_fallback(self) -> Optional[str]:
        return self.litellm_fallback_models[0] if self.litellm_fallback_models else None

    def get_db_url(self) -> str:
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.absolute()}"


def get_config() -> Config:
    return Config.get_instance()


def get_api_keys_for_model(model: str, config: Config) -> List[str]:
    provider = _get_litellm_provider(model)
    m_lower = model.lower()

    if provider in {"gemini", "vertex_ai"} or "gemini" in m_lower:
        return [k for k in config.gemini_api_keys if len(k) >= 8]

    if provider == "deepseek" or "deepseek" in m_lower:
        val = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if val:
            logger.debug("成功获取 DeepSeek API Key (长度: %s)", len(val))
            return [val]

    return []


def extra_litellm_params(model: str, config: Config) -> Dict[str, Any]:
    return {}
