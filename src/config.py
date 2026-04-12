# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 配置管理模块
===================================

职责：
1. 使用单例模式管理全局配置
2. 从 .env 文件加载配置
3. 提供类型安全的配置访问接口
"""

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Literal
from dataclasses import dataclass, field
from dotenv import load_dotenv, dotenv_values

from src.report_language import (
    is_supported_report_language_value,
    normalize_report_language,
)
from src.config.models import ConfigIssue, LLMChannelConfig
from src.config.utils import (
    parse_env_bool,
    parse_env_int,
    parse_env_float,
    normalize_news_strategy_profile,
    resolve_news_window_days,
    resolve_llm_channel_protocol,
    channel_allows_empty_api_key,
    normalize_llm_channel_model,
    get_configured_llm_models,
    get_effective_agent_primary_model,
    resolve_unified_llm_temperature,
    _get_litellm_provider,
    _uses_direct_env_provider,
    parse_llm_channels,
    channels_to_model_list,
    legacy_keys_to_model_list,
    parse_litellm_yaml,
    setup_env,
    load_stocks_from_yaml,
    SUPPORTED_LLM_CHANNEL_PROTOCOLS,
)

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """
    系统配置类 - 单例模式
    """
    # === 自选股配置 ===
    stock_list: List[str] = field(default_factory=list)
    stock_config_path: str = "stocks.yaml"
    
    # ... rest of the fields ...
    
    # === 飞书配置 ===
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_folder_token: Optional[str] = None
    
    # === 数据源 Token ===
    tushare_token: Optional[str] = None
    tickflow_api_key: Optional[str] = None
    
    # === LiteLLM (Unified LLM Router) ===
    litellm_model: Optional[str] = None
    litellm_fallback_models: List[str] = field(default_factory=list)
    llm_temperature: float = 0.7
    litellm_config_path: Optional[str] = None
    llm_models_source: str = "env"
    llm_channels: List[Dict[str, Any]] = field(default_factory=list)
    llm_model_list: List[Dict[str, Any]] = field(default_factory=list)
    
    # === 模型 API Keys (Managed) ===
    gemini_api_keys: List[str] = field(default_factory=list)
    anthropic_api_keys: List[str] = field(default_factory=list)
    openai_api_keys: List[str] = field(default_factory=list)
    deepseek_api_keys: List[str] = field(default_factory=list)
    
    # === 基础分析模型配置 (Legacy) ===
    gemini_api_key: Optional[str] = None
    gemini_model: str = 'gemini-2.0-flash'
    gemini_model_fallback: str = 'gemini-1.5-flash'
    gemini_temperature: float = 0.7
    gemini_request_delay: float = 2.0
    gemini_max_retries: int = 5
    gemini_retry_delay: float = 5.0
    
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = 'claude-3-5-sonnet-20241022'
    anthropic_temperature: float = 0.7
    anthropic_max_tokens: int = 8192
    
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: str = 'gpt-4o-mini'
    openai_vision_model: Optional[str] = None
    openai_temperature: float = 0.7
    
    vision_model: str = ""
    vision_provider_priority: str = 'gemini,anthropic,openai'
    
    # === 搜索引擎 API Keys ===
    bocha_api_keys: List[str] = field(default_factory=list)
    minimax_api_keys: List[str] = field(default_factory=list)
    tavily_api_keys: List[str] = field(default_factory=list)
    exa_api_keys: List[str] = field(default_factory=list)
    serpapi_keys: List[str] = field(default_factory=list)
    brave_api_keys: List[str] = field(default_factory=list)
    searxng_base_urls: List[str] = field(default_factory=list)
    searxng_public_instances_enabled: bool = True
    
    social_sentiment_api_key: Optional[str] = None
    social_sentiment_api_url: str = 'https://api.adanos.org'
    
    # === 分析策略配置 ===
    news_max_age_days: int = 3
    news_strategy_profile: str = "short"
    bias_threshold: float = 5.0
    
    # === Agent 模式配置 (Issue #418) ===
    agent_litellm_model: Optional[str] = None
    agent_mode: bool = False
    _agent_mode_explicit: bool = False
    agent_max_steps: int = 10
    agent_skills: List[str] = field(default_factory=list)
    agent_skill_dir: Optional[str] = None
    agent_nl_routing: bool = False
    agent_arch: str = "single"
    agent_orchestrator_mode: str = "standard"
    agent_orchestrator_timeout_s: int = 600
    agent_risk_override: bool = True
    agent_deep_research_budget: int = 30000
    agent_deep_research_timeout: int = 180
    agent_memory_enabled: bool = False
    agent_skill_autoweight: bool = True
    agent_skill_routing: str = "auto"
    
    # === 事件监听配置 ===
    agent_event_monitor_enabled: bool = False
    agent_event_monitor_interval_minutes: int = 5
    agent_event_alert_rules_json: str = ''
    
    # === 通知渠道配置 ===
    wechat_webhook_url: Optional[str] = None
    feishu_webhook_url: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_message_thread_id: Optional[str] = None
    
    email_sender: Optional[str] = None
    email_sender_name: str = '股票分析助手'
    email_password: Optional[str] = None
    email_receivers: List[str] = field(default_factory=list)
    stock_email_groups: List[Tuple[List[str], List[str]]] = field(default_factory=list)
    
    pushover_user_key: Optional[str] = None
    pushover_api_token: Optional[str] = None
    pushplus_token: Optional[str] = None
    pushplus_topic: Optional[str] = None
    serverchan3_sendkey: Optional[str] = None
    custom_webhook_urls: List[str] = field(default_factory=list)
    custom_webhook_bearer_token: Optional[str] = None
    webhook_verify_ssl: bool = True
    
    discord_bot_token: Optional[str] = None
    discord_main_channel_id: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    discord_bot_status: str = 'A股智能分析 | /help'
    slack_webhook_url: Optional[str] = None
    slack_bot_token: Optional[str] = None
    slack_channel_id: Optional[str] = None
    astrbot_url: Optional[str] = None
    astrbot_token: Optional[str] = None
    
    single_stock_notify: bool = False
    report_type: str = 'simple'
    report_language: str = 'zh'
    report_summary_only: bool = False
    report_templates_dir: str = 'templates'
    report_renderer_enabled: bool = False
    report_integrity_enabled: bool = True
    report_integrity_retry: int = 1
    report_history_compare_n: int = 0
    
    analysis_delay: float = 0.0
    merge_email_notification: bool = False
    feishu_max_bytes: int = 20000
    wechat_max_bytes: int = 4000
    wechat_msg_type: str = 'markdown'
    discord_max_words: int = 2000
    
    markdown_to_image_channels: List[str] = field(default_factory=list)
    markdown_to_image_max_chars: int = 15000
    md2img_engine: str = 'wkhtmltoimage'
    
    prefetch_realtime_quotes: bool = True
    database_path: str = './data/stock_analysis.db'
    sqlite_wal_enabled: bool = True
    sqlite_busy_timeout_ms: int = 5000
    sqlite_write_retry_max: int = 3
    sqlite_write_retry_base_delay: float = 0.1
    save_context_snapshot: bool = True
    
    backtest_enabled: bool = True
    backtest_eval_window_days: int = 10
    backtest_min_age_days: int = 14
    backtest_engine_version: str = 'v1'
    backtest_neutral_band_pct: float = 2.0
    
    log_dir: str = './logs'
    log_level: str = 'INFO'
    max_workers: int = 3
    debug: bool = False
    config_validate_mode: str = 'warn'
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    
    schedule_enabled: bool = False
    schedule_time: str = '18:00'
    schedule_run_immediately: bool = True
    run_immediately: bool = True
    market_review_enabled: bool = True
    market_review_region: str = 'cn'
    trading_day_check_enabled: bool = True
    
    webui_enabled: bool = False
    webui_host: str = '127.0.0.1'
    webui_port: int = 8000
    
    bot_enabled: bool = True
    bot_command_prefix: str = '/'
    bot_rate_limit_requests: int = 10
    bot_rate_limit_window: int = 60
    bot_admin_users: List[str] = field(default_factory=list)
    
    feishu_verification_token: Optional[str] = None
    feishu_encrypt_key: Optional[str] = None
    feishu_stream_enabled: bool = False
    dingtalk_app_key: Optional[str] = None
    dingtalk_app_secret: Optional[str] = None
    dingtalk_stream_enabled: bool = False
    
    wecom_corpid: Optional[str] = None
    wecom_token: Optional[str] = None
    wecom_encoding_aes_key: Optional[str] = None
    wecom_agent_id: Optional[str] = None
    telegram_webhook_secret: Optional[str] = None
    
    enable_realtime_quote: bool = True
    enable_realtime_technical_indicators: bool = True
    enable_chip_distribution: bool = True
    enable_eastmoney_patch: bool = False
    realtime_source_priority: str = 'tencent,akshare_sina,efinance,akshare_em'
    realtime_cache_ttl: int = 600
    circuit_breaker_cooldown: int = 300
    
    enable_fundamental_pipeline: bool = True
    fundamental_stage_timeout_seconds: float = 1.5
    fundamental_fetch_timeout_seconds: float = 0.8
    fundamental_retry_max: int = 1
    fundamental_cache_ttl_seconds: int = 120
    fundamental_cache_max_entries: int = 256
    
    portfolio_risk_concentration_alert_pct: float = 35.0
    portfolio_risk_drawdown_alert_pct: float = 15.0
    portfolio_risk_stop_loss_alert_pct: float = 10.0
    portfolio_risk_stop_loss_near_ratio: float = 0.8
    portfolio_risk_lookback_days: int = 180
    portfolio_fx_update_enabled: bool = True

    _instance: Optional['Config'] = None

    def __post_init__(self) -> None:
        if self.http_proxy:
            os.environ['HTTP_PROXY'] = self.http_proxy
            os.environ['http_proxy'] = self.http_proxy
        if self.https_proxy:
            os.environ['HTTPS_PROXY'] = self.https_proxy
            os.environ['https_proxy'] = self.https_proxy

    @classmethod
    def get_instance(cls) -> 'Config':
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance

    @classmethod
    def _load_from_env(cls) -> 'Config':
        setup_env()
        
        # 1. 尝试从 YAML 文件加载股票列表 (优先)
        stock_config_path = os.getenv('STOCK_CONFIG_PATH', 'stocks.yaml')
        stock_list = load_stocks_from_yaml(stock_config_path)
        
        # 2. 如果 YAML 未配置，尝试从环境变量读取
        if not stock_list:
            stock_list_str = os.getenv('STOCK_LIST', '')
            stock_list = [s.strip().upper() for s in stock_list_str.split(',') if s.strip()]
        
        # 3. 兜底默认值
        if not stock_list:
            stock_list = ['600519', '000001', '300750']
        
        # ... rest of the loading logic ...
        llm_channels_str = os.getenv('LLM_CHANNELS', '')
        llm_channels = parse_llm_channels(llm_channels_str)
        
        # Priority for model list: LITELLM_CONFIG YAML > LLM_CHANNELS > Legacy Keys
        llm_model_list = []
        litellm_config_path = os.getenv('LITELLM_CONFIG', 'litellm_config.yaml')
        if litellm_config_path and Path(litellm_config_path).exists():
            llm_model_list = parse_litellm_yaml(litellm_config_path)
            if llm_model_list:
                logger.info(f"已从 {litellm_config_path} 加载 {len(llm_model_list)} 个模型部署")
        
        if not llm_model_list and llm_channels:
            llm_model_list = channels_to_model_list(llm_channels)
            
        # Legacy fallback keys
        gemini_keys = [k.strip() for k in os.getenv('GEMINI_API_KEYS', '').split(',') if k.strip()]
        if not gemini_keys and os.getenv('GEMINI_API_KEY'): gemini_keys = [os.getenv('GEMINI_API_KEY')]
        
        if not llm_model_list:
            llm_model_list = legacy_keys_to_model_list(
                gemini_keys, [], [], None
            )

        return cls(
            stock_list=stock_list,
            tushare_token=os.getenv('TUSHARE_TOKEN'),
            litellm_model=litellm_model,
            llm_channels=llm_channels,
            llm_model_list=llm_model_list,
            gemini_api_keys=gemini_keys,
            database_path=os.getenv('DATABASE_PATH', './data/stock_analysis.db'),
            # ... many more fields would be loaded here in a full implementation
        )

    def refresh_stock_list(self) -> None:
        """
        热读取股票列表并更新配置
        优先级：YAML 文件 > 环境变量 > 默认值
        """
        # 1. 尝试从 YAML 文件读取
        stock_list = load_stocks_from_yaml(self.stock_config_path)
        
        # 2. 如果 YAML 未配置，尝试从 .env 文件/环境变量读取
        if not stock_list:
            # 重新加载 .env 以防有变动
            setup_env()
            stock_list_str = os.getenv('STOCK_LIST', '')
            stock_list = [s.strip().upper() for s in stock_list_str.split(',') if s.strip()]

        if stock_list:
            self.stock_list = stock_list
            logger.info(f"股票列表已更新，当前共 {len(self.stock_list)} 只股票")

    def validate(self) -> List[str]:
        return []

    def get_db_url(self) -> str:
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.absolute()}"

def get_config() -> Config:
    return Config.get_instance()

def get_api_keys_for_model(model: str, config: Config) -> List[str]:
    provider = _get_litellm_provider(model)
    if provider in {"gemini", "vertex_ai"}:
        return [k for k in config.gemini_api_keys if len(k) >= 8]
    return []

def extra_litellm_params(model: str, config: Config) -> Dict[str, Any]:
    return {}
