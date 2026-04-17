# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 配置管理核心
===================================

职责：
1. 维护全局配置单例
2. 聚合 YAML 文件与环境变量
3. 确保所有业务模块所需的字段完整性
"""

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Literal
from dataclasses import dataclass, field
from dotenv import load_dotenv

from src.report_language import (
    is_supported_report_language_value,
    normalize_report_language,
)
from .models import ConfigIssue, LLMChannelConfig
from .utils import (
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
    load_settings_from_yaml,
    SUPPORTED_LLM_CHANNEL_PROTOCOLS,
)

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """
    系统配置类 - 单例模式
    
    保持字段完整性，确保主流程各模块正常运行。
    """
    # === 1. 基础自选股配置 ===
    stock_list: List[str] = field(default_factory=list)
    stock_config_path: str = "stocks.yaml"
    
    # === 2. 核心分析参数 ===
    report_language: str = 'zh'
    news_max_age_days: int = 3
    news_strategy_profile: str = "short"
    bias_threshold: float = 5.0
    gemini_request_delay: float = 2.0
    report_integrity_enabled: bool = True
    report_integrity_retry: int = 1
    analysis_mode: str = 'simple'
    
    # === 3. 系统并发与性能 ===
    max_workers: int = 2
    log_level: str = 'INFO'
    log_dir: str = './logs'
    webui_enabled: bool = False
    webui_port: int = 8000
    debug: bool = False
    config_validate_mode: str = 'warn'
    
    # === 4. 通知偏好 ===
    report_type: str = 'simple'
    report_summary_only: bool = False
    merge_email_notification: bool = False
    single_stock_notify: bool = False
    feishu_webhook_url: Optional[str] = None
    wechat_webhook_url: Optional[str] = None
    dingtalk_webhook_url: Optional[str] = None
    dingtalk_stream_enabled: bool = False
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    email_sender: Optional[str] = None
    email_sender_name: str = '股票分析助手'
    email_password: Optional[str] = None
    email_receivers: List[str] = field(default_factory=list)
    pushover_user_key: Optional[str] = None
    pushover_api_token: Optional[str] = None
    pushplus_token: Optional[str] = None
    serverchan3_sendkey: Optional[str] = None
    discord_bot_token: Optional[str] = None
    discord_main_channel_id: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    slack_bot_token: Optional[str] = None
    slack_channel_id: Optional[str] = None
    custom_webhook_urls: List[str] = field(default_factory=list)
    astrbot_url: Optional[str] = None
    astrbot_token: Optional[str] = None
    
    # === 5. 调度与自动化 ===
    schedule_enabled: bool = False
    schedule_time: str = '18:00'
    schedule_run_immediately: bool = True
    run_immediately: bool = True
    market_review_enabled: bool = True
    market_review_region: str = 'cn'
    trading_day_check_enabled: bool = True
    
    # === 6. 数据源与存储 ===
    prefetch_realtime_quotes: bool = True
    realtime_cache_ttl: int = 600
    enable_eastmoney_patch: bool = False
    database_path: str = './data/stock_analysis.db'
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
    
    # === 7. AI 模型 (来自 litellm_config.yaml) ===
    litellm_model: Optional[str] = None
    litellm_fallback_models: List[str] = field(default_factory=list)
    llm_model_list: List[Dict[str, Any]] = field(default_factory=list)
    llm_temperature: float = 0.7
    llm_models_source: str = "env"
    
    # === 8. 敏感 API Keys ===
    gemini_api_keys: List[str] = field(default_factory=list)
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
    social_sentiment_api_key: Optional[str] = None
    social_sentiment_api_url: str = 'https://api.adanos.org'
    
    # (内部单例)
    _instance: Optional['Config'] = None

    @classmethod
    def get_instance(cls) -> 'Config':
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance

    @classmethod
    def _load_from_env(cls) -> 'Config':
        setup_env()
        
        # A. 加载 YAML 配置
        settings = load_settings_from_yaml('settings.yaml')
        ana_s = settings.get('analysis', {})
        sys_s = settings.get('system', {})
        not_s = settings.get('notification', {})
        dat_s = settings.get('data', {})
        sch_s = settings.get('schedule', {})

        # B. 辅助函数：环境变量覆盖
        def _get_val(env_name, yaml_val, parser=lambda x: x):
            env_val = os.getenv(env_name)
            if env_val is not None:
                try: return parser(env_val)
                except: return yaml_val
            return yaml_val

        # C. 股票列表
        stock_config_path = _get_val('STOCK_CONFIG_PATH', 'stocks.yaml')
        stock_list = load_stocks_from_yaml(stock_config_path)
        if not stock_list:
            stock_list = [s.strip().upper() for s in os.getenv('STOCK_LIST', '').split(',') if s.strip()]
        if not stock_list:
            stock_list = ['600519', '000001', '300750']

        # D. LLM 列表
        llm_model_list = []
        litellm_config_path = os.getenv('LITELLM_CONFIG', 'litellm_config.yaml')
        if Path(litellm_config_path).exists():
            llm_model_list = parse_litellm_yaml(litellm_config_path)
        
        gemini_keys = [k.strip() for k in os.getenv('GEMINI_API_KEYS', '').split(',') if k.strip()]
        if not gemini_keys and os.getenv('GEMINI_API_KEY'): gemini_keys = [os.getenv('GEMINI_API_KEY')]
        
        if not llm_model_list:
            llm_model_list = legacy_keys_to_model_list(gemini_keys, [], [], None)

        # E. 搜索引擎 Keys
        def _get_keys(plural, singular):
            val = os.getenv(plural, '')
            if val: return [k.strip() for k in val.split(',') if k.strip()]
            s = os.getenv(singular, '').strip()
            return [s] if s else []

        return cls(
            stock_list=stock_list,
            # 分析设置
            report_language=_get_val('REPORT_LANGUAGE', ana_s.get('language', 'zh')),
            news_max_age_days=int(_get_val('NEWS_MAX_AGE_DAYS', ana_s.get('news_window_days', 3))),
            news_strategy_profile=ana_s.get('strategy_profile', 'short'),
            bias_threshold=float(_get_val('BIAS_THRESHOLD', ana_s.get('bias_threshold', 5.0))),
            gemini_request_delay=float(_get_val('ANALYSIS_REQUEST_DELAY', ana_s.get('request_delay', 2.0))),
            report_integrity_retry=int(_get_val('REPORT_INTEGRITY_RETRY', ana_s.get('integrity_retry', 1))),
            analysis_mode=_get_val('ANALYSIS_MODE', ana_s.get('mode', 'simple')),
            
            # 系统设置
            max_workers=int(_get_val('MAX_WORKERS', sys_s.get('max_workers', 2))),
            log_level=_get_val('LOG_LEVEL', sys_s.get('log_level', 'INFO')),
            log_dir=_get_val('LOG_DIR', sys_s.get('log_dir', './logs')),
            webui_enabled=parse_env_bool(_get_val('WEBUI_ENABLED', sys_s.get('webui_enabled', False))),
            webui_port=int(_get_val('WEBUI_PORT', sys_s.get('webui_port', 8000))),
            debug=parse_env_bool(_get_val('DEBUG', sys_s.get('debug', False))),
            
            # 通知设置
            report_type=_get_val('REPORT_TYPE', not_s.get('report_type', 'simple')),
            report_summary_only=parse_env_bool(_get_val('REPORT_SUMMARY_ONLY', not_s.get('summary_only', False))),
            merge_email_notification=parse_env_bool(_get_val('MERGE_EMAIL_NOTIFICATION', not_s.get('merge_email', False))),
            single_stock_notify=parse_env_bool(_get_val('SINGLE_STOCK_NOTIFY', not_s.get('single_stock', False))),
            feishu_webhook_url=os.getenv('FEISHU_WEBHOOK_URL'),
            wechat_webhook_url=os.getenv('WECHAT_WEBHOOK_URL'),
            dingtalk_stream_enabled=parse_env_bool(os.getenv('DINGTALK_STREAM_ENABLED'), False),
            
            # 调度设置
            schedule_enabled=parse_env_bool(_get_val('SCHEDULE_ENABLED', sch_s.get('enabled', False))),
            schedule_time=_get_val('SCHEDULE_TIME', sch_s.get('time', '18:00')),
            schedule_run_immediately=parse_env_bool(_get_val('SCHEDULE_RUN_IMMEDIATELY', sch_s.get('run_immediately', True))),
            run_immediately=parse_env_bool(_get_val('RUN_IMMEDIATELY', True)),
            market_review_enabled=parse_env_bool(_get_val('MARKET_REVIEW_ENABLED', True)),
            
            # 数据设置
            prefetch_realtime_quotes=parse_env_bool(_get_val('PREFETCH_REALTIME_QUOTES', dat_s.get('prefetch_quotes', True))),
            realtime_cache_ttl=int(_get_val('REALTIME_CACHE_TTL', dat_s.get('cache_ttl', 600))),
            enable_eastmoney_patch=parse_env_bool(_get_val('ENABLE_EASTMONEY_PATCH', dat_s.get('eastmoney_patch', False))),
            database_path=_get_val('DATABASE_PATH', dat_s.get('database_path', './data/stock_analysis.db')),
            
            # Keys
            tushare_token=os.getenv('TUSHARE_TOKEN'),
            tickflow_api_key=os.getenv('TICKFLOW_API_KEY'),
            litellm_model=os.getenv('LITELLM_MODEL'),
            llm_model_list=llm_model_list,
            gemini_api_keys=gemini_keys,
            bocha_api_keys=_get_keys('BOCHA_API_KEYS', 'BOCHA_API_KEY'),
            tavily_api_keys=_get_keys('TAVILY_API_KEYS', 'TAVILY_API_KEY'),
            exa_api_keys=_get_keys('EXA_API_KEYS', 'EXA_API_KEY'),
            serpapi_keys=_get_keys('SERPAPI_API_KEYS', 'SERPAPI_API_KEY'),
            minimax_api_keys=_get_keys('MINIMAX_API_KEYS', 'MINIMAX_API_KEY'),
            brave_api_keys=_get_keys('BRAVE_API_KEYS', 'BRAVE_API_KEY'),
            feishu_app_id=os.getenv('FEISHU_APP_ID'),
            feishu_app_secret=os.getenv('FEISHU_APP_SECRET'),
            email_sender=os.getenv('EMAIL_SENDER'),
            email_sender_name=os.getenv('EMAIL_SENDER_NAME', '股票分析助手'),
            email_password=os.getenv('EMAIL_PASSWORD'),
            email_receivers=[r.strip() for r in os.getenv('EMAIL_RECEIVERS', '').split(',') if r.strip()],
            pushover_user_key=os.getenv('PUSHOVER_USER_KEY'),
            pushover_api_token=os.getenv('PUSHOVER_API_TOKEN'),
            pushplus_token=os.getenv('PUSHPLUS_TOKEN'),
            serverchan3_sendkey=os.getenv('SERVERCHAN3_SENDKEY'),
            discord_bot_token=os.getenv('DISCORD_BOT_TOKEN'),
            discord_main_channel_id=os.getenv('DISCORD_MAIN_CHANNEL_ID'),
            discord_webhook_url=os.getenv('DISCORD_WEBHOOK_URL'),
            slack_webhook_url=os.getenv('SLACK_WEBHOOK_URL'),
            slack_bot_token=os.getenv('SLACK_BOT_TOKEN'),
            slack_channel_id=os.getenv('SLACK_CHANNEL_ID'),
            custom_webhook_urls=[u.strip() for u in os.getenv('CUSTOM_WEBHOOK_URLS', '').split(',') if u.strip()],
            astrbot_url=os.getenv('ASTRBOT_URL'),
            astrbot_token=os.getenv('ASTRBOT_TOKEN'),
            openai_api_key=os.getenv('OPENAI_API_KEY'),
            openai_base_url=os.getenv('OPENAI_BASE_URL'),
            social_sentiment_api_key=os.getenv('SOCIAL_SENTIMENT_API_KEY'),
        )

    def refresh_stock_list(self) -> None:
        stock_list = load_stocks_from_yaml(self.stock_config_path)
        if not stock_list:
            setup_env()
            stock_list = [s.strip().upper() for s in os.getenv('STOCK_LIST', '').split(',') if s.strip()]
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
    from .utils import _get_litellm_provider
    provider = _get_litellm_provider(model)
    m_lower = model.lower()
    
    # 1. 识别 Gemini
    if provider in {"gemini", "vertex_ai"} or "gemini" in m_lower:
        return [k for k in config.gemini_api_keys if len(k) >= 8]
        
    # 2. 识别 DeepSeek (强制从环境变量提取)
    if provider == "deepseek" or "deepseek" in m_lower:
        val = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if val:
            logger.debug(f"成功获取 DeepSeek API Key (长度: {len(val)})")
            return [val]
        
    return []

def extra_litellm_params(model: str, config: Config) -> Dict[str, Any]:
    return {}
