# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 配置管理模块 (Refactored)
===================================

职责：
1. 使用单例模式管理全局配置
2. 支持 stocks.yaml, litellm_config.yaml, settings.yaml 结构化配置
3. 保持环境变量 ( .env ) 的最高优先级覆盖能力
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
    """
    # === 1. 基础自选股配置 ===
    stock_list: List[str] = field(default_factory=list)
    stock_config_path: str = "stocks.yaml"
    
    # === 2. 核心分析参数 (来自 settings.yaml / .env) ===
    report_language: str = 'zh'
    news_max_age_days: int = 3
    bias_threshold: float = 5.0
    gemini_request_delay: float = 2.0
    report_integrity_retry: int = 1
    
    # === 3. 系统并发与性能 ===
    max_workers: int = 2
    log_level: str = 'INFO'
    webui_enabled: bool = False
    webui_port: int = 8000
    
    # === 4. 通知偏好 ===
    report_type: str = 'simple'
    report_summary_only: bool = False
    merge_email_notification: bool = False
    
    # === 5. 数据源设置 ===
    prefetch_realtime_quotes: bool = True
    realtime_cache_ttl: int = 600
    enable_eastmoney_patch: bool = False
    database_path: str = './data/stock_analysis.db'
    realtime_source_priority: str = 'tencent,akshare_sina,efinance,akshare_em'
    
    # === 6. AI 模型列表 (来自 litellm_config.yaml) ===
    litellm_model: Optional[str] = None
    llm_model_list: List[Dict[str, Any]] = field(default_factory=list)
    llm_temperature: float = 0.7
    
    # === 7. 敏感 Token (来自 .env) ===
    tushare_token: Optional[str] = None
    gemini_api_keys: List[str] = field(default_factory=list)
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_webhook_url: Optional[str] = None
    feishu_verification_token: Optional[str] = None
    feishu_encrypt_key: Optional[str] = None
    feishu_stream_enabled: bool = False
    
    # (保持 dataclass 的字段完整性以兼容其他模块引用)
    # ... 
    
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

        # B. 辅助函数：环境变量覆盖
        def _get_val(env_name, yaml_val, parser=lambda x: x):
            env_val = os.getenv(env_name)
            if env_val is not None:
                return parser(env_val)
            return yaml_val

        # C. 加载股票列表
        stock_config_path = _get_val('STOCK_CONFIG_PATH', 'stocks.yaml')
        stock_list = load_stocks_from_yaml(stock_config_path)
        if not stock_list:
            stock_list = [s.strip().upper() for s in os.getenv('STOCK_LIST', '').split(',') if s.strip()]
        if not stock_list:
            stock_list = ['600519', '000001', '300750']

        # D. 加载 LLM 配置
        llm_model_list = []
        litellm_config_path = os.getenv('LITELLM_CONFIG', 'litellm_config.yaml')
        if Path(litellm_config_path).exists():
            llm_model_list = parse_litellm_yaml(litellm_config_path)
        
        gemini_keys = [k.strip() for k in os.getenv('GEMINI_API_KEYS', '').split(',') if k.strip()]
        if not gemini_keys and os.getenv('GEMINI_API_KEY'): gemini_keys = [os.getenv('GEMINI_API_KEY')]
        
        if not llm_model_list:
            llm_model_list = legacy_keys_to_model_list(gemini_keys, [], [], None)

        return cls(
            stock_list=stock_list,
            report_language=_get_val('REPORT_LANGUAGE', ana_s.get('language', 'zh')),
            news_max_age_days=int(_get_val('NEWS_MAX_AGE_DAYS', ana_s.get('news_window_days', 3))),
            bias_threshold=float(_get_val('BIAS_THRESHOLD', ana_s.get('bias_threshold', 5.0))),
            gemini_request_delay=float(_get_val('ANALYSIS_REQUEST_DELAY', ana_s.get('request_delay', 2.0))),
            report_integrity_retry=int(_get_val('REPORT_INTEGRITY_RETRY', ana_s.get('integrity_retry', 1))),
            max_workers=int(_get_val('MAX_WORKERS', sys_s.get('max_workers', 2))),
            log_level=_get_val('LOG_LEVEL', sys_s.get('log_level', 'INFO')),
            webui_enabled=parse_env_bool(_get_val('WEBUI_ENABLED', sys_s.get('webui_enabled', False))),
            webui_port=int(_get_val('WEBUI_PORT', sys_s.get('webui_port', 8000))),
            report_type=_get_val('REPORT_TYPE', not_s.get('report_type', 'simple')),
            report_summary_only=parse_env_bool(_get_val('REPORT_SUMMARY_ONLY', not_s.get('summary_only', False))),
            merge_email_notification=parse_env_bool(_get_val('MERGE_EMAIL_NOTIFICATION', not_s.get('merge_email', False))),
            prefetch_realtime_quotes=parse_env_bool(_get_val('PREFETCH_REALTIME_QUOTES', dat_s.get('prefetch_quotes', True))),
            realtime_cache_ttl=int(_get_val('REALTIME_CACHE_TTL', dat_s.get('cache_ttl', 600))),
            enable_eastmoney_patch=parse_env_bool(_get_val('ENABLE_EASTMONEY_PATCH', dat_s.get('eastmoney_patch', False))),
            database_path=os.getenv('DATABASE_PATH', './data/stock_analysis.db'),
            tushare_token=os.getenv('TUSHARE_TOKEN'),
            litellm_model=os.getenv('LITELLM_MODEL'),
            llm_model_list=llm_model_list,
            gemini_api_keys=gemini_keys,
            feishu_app_id=os.getenv('FEISHU_APP_ID'),
            feishu_app_secret=os.getenv('FEISHU_APP_SECRET'),
            feishu_webhook_url=os.getenv('FEISHU_WEBHOOK_URL'),
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
    from src.config.utils import _get_litellm_provider
    provider = _get_litellm_provider(model)
    if provider in {"gemini", "vertex_ai"}:
        return [k for k in config.gemini_api_keys if len(k) >= 8]
    return []

def extra_litellm_params(model: str, config: Config) -> Dict[str, Any]:
    return {}
