# Config Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify the four-layer configuration system into a two-file structure (.env for secrets, config.yaml for business params + LLM routing) with unified strict validation.

**Architecture:** Add `ConfigValidator` (validates against `config_registry.py` metadata) and `UnifiedConfigLoader` (loads and merges .env + config.yaml). Modify `manager.py` to integrate the new loader. Deprecate `settings.yaml` and `litellm_config.yaml`.

**Tech Stack:** Python 3, PyYAML, python-dotenv, existing `config_registry.py` metadata

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/config/validator.py` | `ConfigValidationError` exception + `ConfigValidator` class with strict validation logic |
| `src/config/loader.py` | `UnifiedConfigLoader` class that loads .env and config.yaml, merges with priority |
| `config.yaml` | New business configuration template (migrated from .env.example + settings.yaml + litellm_config.yaml) |
| `docs/migration-guide.md` | Step-by-step migration guide for users upgrading from old config |

### Modified Files
| File | Change |
|------|-------|
| `src/config/__init__.py` | Add exports for `ConfigValidator`, `UnifiedConfigLoader`, `ConfigValidationError` |
| `src/config/manager.py` | Refactor `Config` class to use `UnifiedConfigLoader`, call validation on init |
| `.env.example` | Remove business parameters (MAX_WORKERS, LOG_LEVEL, etc.), keep only sensitive keys |
| `settings.yaml` | Mark as deprecated with deprecation notice |
| `litellm_config.yaml` | Mark as deprecated with deprecation notice |
| `AGENTS.md` | Update section 2 (common commands) and section 5 (default workflow) for new config structure |

### Test Files
| File | Tests |
|------|-------|
| `tests/test_config_validator.py` | Unit tests for `ConfigValidator` |
| `tests/test_config_loader.py` | Unit tests for `UnifiedConfigLoader` |

---

### Task 1: Create ConfigValidationError Exception

**Files:**
- Create: `src/config/validator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_validator.py
import pytest
from src.config.validator import ConfigValidationError

def test_validation_error_is_exception():
    """ConfigValidationError should be a subclass of Exception"""
    assert issubclass(ConfigValidationError, Exception)

def test_validation_error_stores_messages():
    """ConfigValidationError should store validation messages"""
    error = ConfigValidationError(["[REQUIRED] API_KEY is missing"])
    assert len(error.messages) == 1
    assert "[REQUIRED] API_KEY is missing" in error.messages

def test_validation_error_str_format():
    """ConfigValidationError __str__ should format messages"""
    error = ConfigValidationError([
        "[REQUIRED] GEMINI_API_KEY is missing",
        "[TYPE] MAX_WORKERS=abc is not valid integer"
    ])
    str_repr = str(error)
    assert "Config validation failed:" in str_repr
    assert "[REQUIRED] GEMINI_API_KEY is missing" in str_repr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_validator.py::test_validation_error_is_exception -v`
Expected: FAIL with `ImportError` or `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/config/validator.py
"""Configuration validation module."""

class ConfigValidationError(Exception):
    """Raised when configuration validation fails (strict mode)."""

    def __init__(self, messages: list):
        self.messages = messages
        super().__init__(self._format_messages())

    def _format_messages(self) -> str:
        if not self.messages:
            return "Config validation failed."
        return "Config validation failed:\n  " + "\n  ".join(self.messages)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_validator.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/config/validator.py tests/test_config_validator.py
git commit -m "feat(config): add ConfigValidationError exception class"
```

---

### Task 2: Implement ConfigValidator - Required Field Check

**Files:**
- Modify: `src/config/validator.py`
- Modify: `tests/test_config_validator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_validator.py (append)
from src.config.validator import ConfigValidator, ConfigValidationError

def test_validate_required_field_missing():
    """Should raise ConfigValidationError when required field is missing"""
    from src.core.config_registry import get_field_definition

    # Simulate env_dict with missing required field
    env_dict = {}
    config_dict = {}

    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)

    assert exc_info.value.messages  # Should have at least one message

def test_validate_no_error_when_valid():
    """Should not raise when all required fields are present"""
    env_dict = {"GEMINI_API_KEY": "valid_key_12345678"}
    config_dict = {}
    # Should NOT raise
    ConfigValidator.validate_all(env_dict, config_dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_validator.py::test_validate_required_field_missing -v`
Expected: FAIL with `AttributeError` (ConfigValidator not yet implemented)

- [ ] **Step 3: Write minimal implementation**

```python
# src/config/validator.py (append)
from typing import Any, Dict

from src.core.config_registry import (
    get_registered_field_keys,
    get_field_definition,
)


class ConfigValidator:
    """Strict validator for configuration, based on config_registry metadata."""

    @classmethod
    def validate_all(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> None:
        """Validate all configuration. Raises ConfigValidationError on failure."""
        messages = []

        # Check required fields
        messages.extend(cls._check_required_fields(env_dict, config_dict))

        if messages:
            raise ConfigValidationError(messages)

    @classmethod
    def _check_required_fields(
        cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]
    ) -> list:
        """Check that required fields are present and not empty."""
        messages = []
        # Only check fields marked as required in registry
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            if not field.get("is_required", False):
                continue
            # Check in env_dict first, then config_dict
            value = env_dict.get(key) or config_dict.get(key)
            if not value:
                messages.append(f"[REQUIRED] {key} is required but missing")
        return messages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_validator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/validator.py tests/test_config_validator.py
git commit -m "feat(config): implement ConfigValidator required field check"
```

---

### Task 3: Implement ConfigValidator - Type and Enum Validation

**Files:**
- Modify: `src/config/validator.py`
- Modify: `tests/test_config_validator.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_validator.py (append)
def test_validate_type_integer_valid():
    """Integer type fields should accept valid integers"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "MAX_WORKERS": 3}
    config_dict = {}
    ConfigValidator.validate_all(env_dict, config_dict)  # Should not raise

def test_validate_type_integer_invalid():
    """Integer type fields should reject non-integer values"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "MAX_WORKERS": "abc"}
    config_dict = {}
    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)
    assert any("MAX_WORKERS" in m and "integer" in m.lower() for m in exc_info.value.messages)

def test_validate_enum_valid():
    """Enum fields should accept values in the enum list"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "REPORT_TYPE": "simple"}
    config_dict = {}
    ConfigValidator.validate_all(env_dict, config_dict)  # Should not raise

def test_validate_enum_invalid():
    """Enum fields should reject values not in the enum list"""
    env_dict = {"GEMINI_API_KEY": "key12345678", "REPORT_TYPE": "invalid_type"}
    config_dict = {}
    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator.validate_all(env_dict, config_dict)
    assert any("REPORT_TYPE" in m and "invalid_type" in m for m in exc_info.value.messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_validator.py::test_validate_type_integer_invalid -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

```python
# src/config/validator.py - replace _check_required_fields and add new methods
    @classmethod
    def validate_all(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> None:
        """Validate all configuration. Raises ConfigValidationError on failure."""
        messages = []

        # Check required fields
        messages.extend(cls._check_required_fields(env_dict, config_dict))
        # Check types
        messages.extend(cls._check_types(env_dict, config_dict))
        # Check enums
        messages.extend(cls._check_enums(env_dict, config_dict))
        # Check ranges
        messages.extend(cls._check_ranges(env_dict, config_dict))
        # Check sensitive keys
        messages.extend(cls._check_sensitive_keys(env_dict, config_dict))

        if messages:
            raise ConfigValidationError(messages)

    @classmethod
    def _check_required_fields(
        cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]
    ) -> list:
        """Check that required fields are present and not empty."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            if not field.get("is_required", False):
                continue
            value = env_dict.get(key) or config_dict.get(key)
            if not value:
                messages.append(f"[REQUIRED] {key} is required but missing")
        return messages

    @classmethod
    def _check_types(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> list:
        """Check data types match field definitions."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            data_type = field.get("data_type", "string")
            value = env_dict.get(key) or config_dict.get(key)
            if value is None:
                continue
            if data_type == "integer":
                if not isinstance(value, int) and not (isinstance(value, str) and value.isdigit()):
                    messages.append(f"[TYPE] {key}={value} is not a valid integer")
            elif data_type == "number":
                try:
                    float(str(value))
                except (ValueError, TypeError):
                    messages.append(f"[TYPE] {key}={value} is not a valid number")
            elif data_type == "boolean":
                if str(value).lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                    messages.append(f"[TYPE] {key}={value} is not a valid boolean")
        return messages

    @classmethod
    def _check_enums(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> list:
        """Check enum fields have valid values."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            validation = field.get("validation", {})
            enum_values = validation.get("enum", [])
            if not enum_values:
                continue
            value = env_dict.get(key) or config_dict.get(key)
            if value is None:
                continue
            if str(value) not in [str(v) for v in enum_values]:
                messages.append(f"[ENUM] {key}={value} is not in {enum_values}")
        return messages

    @classmethod
    def _check_ranges(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> list:
        """Check numeric fields are within valid ranges."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            validation = field.get("validation", {})
            min_val = validation.get("min")
            max_val = validation.get("max")
            if min_val is None and max_val is None:
                continue
            value = env_dict.get(key) or config_dict.get(key)
            if value is None:
                continue
            try:
                num_value = float(str(value))
            except (ValueError, TypeError):
                continue
            if min_val is not None and num_value < min_val:
                messages.append(f"[RANGE] {key}={value} is below minimum {min_val}")
            if max_val is not None and num_value > max_val:
                messages.append(f"[RANGE] {key}={value} exceeds maximum {max_val}")
        return messages

    @classmethod
    def _check_sensitive_keys(cls, env_dict: Dict[str, Any], config_dict: Dict[str, Any]) -> list:
        """Check sensitive keys have valid format (e.g., API key length >= 8)."""
        messages = []
        for key in get_registered_field_keys():
            field = get_field_definition(key)
            if not field.get("is_sensitive", False):
                continue
            value = env_dict.get(key) or config_dict.get(key)
            if value is None:
                continue
            str_value = str(value)
            # API keys should be at least 8 characters
            if field.get("data_type") == "string" and len(str_value) < 8:
                messages.append(f"[SENSITIVE] {key} is too short (min 8 chars)")
        return messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_validator.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/config/validator.py tests/test_config_validator.py
git commit -m "feat(config): add type, enum, range, and sensitive key validation"
```

---

### Task 4: Implement UnifiedConfigLoader - Core Structure

**Files:**
- Create: `src/config/loader.py`
- Create: `tests/test_config_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_loader.py
import pytest
import os
from pathlib import Path
from unittest.mock import patch

from src.config.loader import UnifiedConfigLoader


@pytest.fixture
def loader():
    return UnifiedConfigLoader()


def test_loader_initialization(loader):
    """UnifiedConfigLoader should initialize without error"""
    assert loader is not None
    assert hasattr(loader, 'load')
    assert hasattr(loader, '_load_env')
    assert hasattr(loader, '_load_yaml')
    assert hasattr(loader, '_merge')


def test_load_env_returns_dict(loader):
    """_load_env should return a dictionary"""
    with patch('src.config.loader.os.path.exists', return_value=True):
        with patch('src.config.loader.dotenv_values', return_value={"GEMINI_API_KEY": "test_key"}):
            result = loader._load_env()
            assert isinstance(result, dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_loader.py::test_loader_initialization -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/config/loader.py
"""Unified configuration loader."""

import os
from pathlib import Path
from typing import Any, Dict

from dotenv import dotenv_values


class UnifiedConfigLoader:
    """Loads and merges .env and config.yaml with unified validation."""

    def __init__(self, env_path: str = None, config_path: str = None):
        self._env_path = Path(env_path) if env_path else Path(os.getcwd()) / ".env"
        self._config_path = Path(config_path) if config_path else Path(os.getcwd()) / "config.yaml"

    def load(self) -> Dict[str, Any]:
        """Load, merge, validate, and return the full configuration."""
        env_dict = self._load_env()
        yaml_dict = self._load_yaml()
        merged = self._merge(env_dict, yaml_dict)
        from src.config.validator import ConfigValidator
        ConfigValidator.validate_all(env_dict, merged)
        return merged

    def _load_env(self) -> Dict[str, Any]:
        """Load .env file into a dictionary."""
        if not self._env_path.exists():
            return {}
        values = dotenv_values(self._env_path)
        return {str(k): ("" if v is None else str(v)) for k, v in values.items() if k is not None}

    def _load_yaml(self) -> Dict[str, Any]:
        """Load config.yaml into a dictionary."""
        if not self._config_path.exists():
            return {}
        try:
            import yaml
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data if isinstance(data, dict) else {}
        except ImportError:
            return {}
        except Exception:
            return {}

    def _merge(self, env_dict: Dict[str, Any], yaml_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Merge yaml_dict and env_dict with env taking priority."""
        merged = dict(yaml_dict)  # Start with yaml defaults
        # Override with env values
        for key, value in env_dict.items():
            merged[key] = value
        # Override with environment variables (highest priority)
        for key in os.environ:
            if key in merged or key in env_dict:
                merged[key] = os.environ[key]
        return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_loader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/loader.py tests/test_config_loader.py
git commit -m "feat(config): implement UnifiedConfigLoader with load, merge, and validate"
```

---

### Task 5: Create config.yaml Template

**Files:**
- Create: `config.yaml`

- [ ] **Step 1: Create the config.yaml file**

```yaml
# A股自选股智能分析系统 - 业务配置
# 本文件存放全部业务参数和 LLM 路由配置
# 敏感 Key/Token 请放在 .env 文件中

# 1. 分析策略设置
analysis:
  mode: simple                  # simple: 单模型分析, debate: 红蓝对垒辩论模式
  language: zh                  # zh: 中文, en: 英文
  bias_threshold: 5.0            # 乖离率警告阈值 (%)
  news_window_days: 3            # 新闻搜索的时间窗口 (天)
  request_delay: 2.0             # LLM 请求间的强制延迟 (秒)
  integrity_retry: 1              # AI 结果缺失时的重试次数

# 2. 系统性能与并发
system:
  max_workers: 2                 # 最大并行分析股票数 (建议 1-3)
  log_level: INFO                # DEBUG, INFO, WARNING, ERROR
  report_dir: "./report"         # 报告保存目录
  schedule_enabled: false        # 是否启用定时分析
  schedule_time: "18:00"         # 定时分析时间 (24h)
  schedule_run_immediately: true  # 定时模式下启动时是否立即运行
  trading_day_check_enabled: true # 是否校验交易日
  market_review_enabled: true    # 是否启用大盘复盘
  market_review_region: "cn"      # 大盘复盘市场：cn / hk / us / both
  debug: false                   # 调试模式
  analysis_delay: 0              # 单股分析间隔延迟（秒）

# 3. LLM 模型配置
llm:
  primary_model: ""              # 主模型，格式 provider/model（留空则自动推断）
  fallback_models: []             # 备用模型列表（逗号分隔或 YAML 列表）
  temperature: 0.7              # 统一采样温度 [0.0, 2.0]
  channels: []                    # LLM 通道配置（高级用法）

# 4. 通知配置
notification:
  report_type: simple            # simple: 简报, full: 详细报告, brief: 3-5句
  summary_only: false            # 是否仅发送汇总表
  markdown_to_image: []          # 哪些渠道启用图片模式
  merge_email: false            # 是否合并多股分析到一封邮件
  report_language: zh            # 报告语言：zh / en
  report_templates_dir: "templates"
  report_integrity_enabled: true
  report_integrity_retry: 1
  report_history_compare_n: 0
  merge_email_notification: false
  single_stock_notify: false

# 5. 数据源配置
data:
  prefetch_quotes: true          # 是否预取实时行情
  cache_ttl: 600                 # 实时数据缓存时长 (秒)
  eastmoney_patch: false         # 是否启用东财接口兼容补丁
  realtime_source_priority: "akshare_em,tencent,akshare_sina,efinance"
  enable_realtime_quote: true
  enable_realtime_technical_indicators: true
  enable_chip_distribution: true
  news_max_age_days: 3
  news_strategy_profile: "short"  # ultra_short(1d) / short(3d) / medium(7d) / long(30d)

# 6. Agent 配置
agent:
  mode: false                    # 是否启用 Agent 模式
  auto_route_analysis: false     # 条件式路由到 Agent
  max_steps: 10                  # Agent 最大执行步数
  skills: ""                      # Agent 技能列表（逗号分隔）
  skill_dir: "strategies"         # 技能目录
  nl_routing: false              # 自然语言路由
  arch: "single"                 # single / multi
  orchestrator_mode: "standard"  # quick / standard / full / specialist
  orchestrator_timeout_s: 600
  risk_override: true
  memory_enabled: false
  skill_autoweight: true
  skill_routing: "auto"          # auto / manual
  event_monitor_enabled: false
  event_monitor_interval_minutes: 5
  event_alert_rules_json: ""

# 7. 回测配置
backtest:
  enabled: true
  eval_window_days: 10
  min_age_days: 14
  engine_version: "v1"
  neutral_band_pct: 2.0
```

- [ ] **Step 2: Verify YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`
Expected: No error, returns dict

- [ ] **Step 3: Commit**

```bash
git add config.yaml
git commit -m "feat(config): add config.yaml business configuration template"
```

---

### Task 6: Update .env.example - Keep Only Sensitive Keys

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Rewrite .env.example with only sensitive keys**

```bash
# Backup old .env.example
cp .env.example .env.example.bak
```

Replace `.env.example` content:

```env
# =================================================================
# A股自选股智能分析系统 - 环境变量配置
# =================================================================
# 配置指引：
# 1. 敏感 Key/Token ➜ 本文件（.env）
# 2. 业务参数（并发数、语言、调度等） ➜ 见 config.yaml
# 3. 股票列表 ➜ 见 stocks.yaml
#
# 验证：python -c "from src.config import get_config; get_config()"
# =================================================================

# === 1. 核心敏感 Token (必填项，至少一个 LLM Key) ===
GEMINI_API_KEY=your_gemini_key_here
DEEPSEEK_API_KEY=your_deepseek_key_here
TUSHARE_TOKEN=your_tushare_token_here

# === 2. 搜索引擎 API Keys (选填) ===
TAVILY_API_KEY=
OPENBB_NEWS_ENABLED=false
OPENBB_NEWS_PROVIDER=yfinance

# === 3. LLM 模型配置（选填，详见 config.yaml llm 段）===
LITELLM_MODEL=
LITELLM_FALLBACK_MODELS=
LITELLM_CONFIG=
LLM_CHANNELS=

# === 4. Agent 配置（选填）===
AGENT_MODE=false
AGENT_AUTO_ROUTE_ANALYSIS=false
AGENT_LITELLM_MODEL=
AGENT_ARCH=single
AGENT_ORCHESTRATOR_MODE=standard
AGENT_ORCHESTRATOR_TIMEOUT_S=600
AGENT_MAX_STEPS=10
AGENT_SKILLS=
AGENT_SKILL_ROUTING=auto
AGENT_SKILL_AUTOWEIGHT=true
AGENT_MEMORY_ENABLED=false
AGENT_RISK_OVERRIDE=true

# === 5. 通知渠道密钥 (选填) ===
# 5a. 飞书机器人
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_WEBHOOK_URL=
FEISHU_STREAM_ENABLED=false
FEISHU_FOLDER_TOKEN=
FEISHU_MAX_BYTES=20000

# 5b. 企业微信
WECHAT_WEBHOOK_URL=
WECHAT_MSG_TYPE=markdown
WECHAT_MAX_BYTES=4000

# 5c. 邮件 SMTP
EMAIL_SENDER=
EMAIL_PASSWORD=
EMAIL_RECEIVERS=
EMAIL_SENDER_NAME=股票分析助手

# 5d. Discord 机器人
DISCORD_BOT_TOKEN=
DISCORD_MAIN_CHANNEL_ID=
DISCORD_WEBHOOK_URL=
DISCORD_MAX_WORDS=2000

# 5e. Pushover
PUSHOVER_USER_KEY=
PUSHOVER_API_TOKEN=

# 5f. PushPlus
PUSHPLUS_TOKEN=
PUSHPLUS_TOPIC=

# 5g. ServerChan
SERVERCHAN3_SENDKEY=

# 5h. 钉钉机器人
DINGTALK_WEBHOOK_URL=
DINGTALK_STREAM_ENABLED=false

# 5i. 自定义 Webhook
CUSTOM_WEBHOOK_URLS=
CUSTOM_WEBHOOK_BEARER_TOKEN=
WEBHOOK_VERIFY_SSL=true

# === 6. 数据源配置 ===
# 6a. 富途牛牛 OpenAPI
FUTU_API_HOST=127.0.0.1
FUTU_API_PORT=11111
FUTU_UNLOCK_PASSWORD=

# 6b. TickFlow
TICKFLOW_API_KEY=

# 6c. 实时行情
REALTIME_SOURCE_PRIORITY=tencent,akshare_sina,efinance,akshare_em
PREFETCH_REALTIME_QUOTES=true
REALTIME_CACHE_TTL=600
ENABLE_REALTIME_QUOTE=true
ENABLE_REALTIME_TECHNICAL_INDICATORS=true
ENABLE_CHIP_DISTRIBUTION=true

# === 7. 大盘与调度 ===
MARKET_REVIEW_ENABLED=true
MARKET_REVIEW_REGION=cn
TRADING_DAY_CHECK_ENABLED=true
SCHEDULE_ENABLED=false
SCHEDULE_TIME=18:00
RUN_IMMEDIATELY=true
SCHEDULE_RUN_IMMEDIATELY=true

# === 8. 数据库 ===
DATABASE_PATH=./data/stock_analysis.db
SQLITE_WAL_ENABLED=true
SQLITE_BUSY_TIMEOUT_MS=5000
SQLITE_WRITE_RETRY_MAX=3
SQLITE_WRITE_RETRY_BASE_DELAY=0.1

# === 9. 其他 LLM Key (选填) ===
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-4o-mini
OPENAI_VISION_MODEL=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
AIHUBMIX_KEY=

# === 10. 代理 (选填) ===
HTTP_PROXY=
HTTPS_PROXY=

# === 11. 本地环境设置 (通常保持默认) ===
LOG_DIR=./logs
REPORT_DIR=./report
ENV_FILE=.env
DEBUG=false
LOG_LEVEL=INFO
MAX_WORKERS=2
ANALYSIS_MODE=simple
TRADER_AGENT_ENABLED=true
DEBATE_ROUNDS=2
DEBATE_JUDGE_SCORING=true
NEWS_MAX_AGE_DAYS=7
NEWS_STRATEGY_PROFILE=medium
BIAS_THRESHOLD=5.0
ANALYSIS_REQUEST_DELAY=2.0
REPORT_INTEGRITY_ENABLED=true
REPORT_INTEGRITY_RETRY=1
SAVE_CONTEXT_SNAPSHOT=true
CONFIG_VALIDATE_MODE=strict
NOTIFICATION_TIMEOUT_SEC=15
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "refactor(config): simplify .env.example to focus on sensitive keys only"
```

---

### Task 7: Mark Old Config Files as Deprecated

**Files:**
- Modify: `settings.yaml`
- Modify: `litellm_config.yaml`

- [ ] **Step 1: Add deprecation notice to settings.yaml**

Add to top of `settings.yaml`:

```yaml
# DEPRECATED: This file is deprecated.
# All business configuration has moved to config.yaml.
# This file will be removed in a future version.
# Please migrate your settings to config.yaml.
#
# Migration: Copy your custom values from this file to config.yaml
# and then remove this file.

# ... rest of existing content ...
```

- [ ] **Step 2: Add deprecation notice to litellm_config.yaml**

Add to top of `litellm_config.yaml`:

```yaml
# DEPRECATED: This file is deprecated.
# LLM routing configuration has moved to config.yaml (llm section).
# This file will be removed in a future version.
# 
# Migration: Move your model_list to config.yaml llm section,
# or set LITELLM_CONFIG env var to point to a new YAML file.

# ... rest of existing content ...
```

- [ ] **Step 3: Commit**

```bash
git add settings.yaml litellm_config.yaml
git commit -m "deprecation(config): mark settings.yaml and litellm_config.yaml as deprecated"
```

---

### Task 8: Update src/config/__init__.py Exports

**Files:**
- Modify: `src/config/__init__.py`

- [ ] **Step 1: Write test for new exports**

```python
# tests/test_config_init.py
def test_validator_exported():
    from src.config import ConfigValidator, ConfigValidationError
    assert ConfigValidator is not None
    assert ConfigValidationError is not None

def test_loader_exported():
    from src.config import UnifiedConfigLoader
    assert UnifiedConfigLoader is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_init.py -v`
Expected: FAIL

- [ ] **Step 3: Update __init__.py**

```python
# src/config/__init__.py - add these imports
from .validator import ConfigValidator, ConfigValidationError
from .loader import UnifiedConfigLoader

__all__ = [
    "Config",
    "get_config",
    "get_api_keys_for_model",
    "extra_litellm_params",
    "ConfigIssue",
    "LLMChannelConfig",
    "AGENT_MAX_STEPS_DEFAULT",
    "SUPPORTED_LLM_CHANNEL_PROTOCOLS",
    "_get_litellm_provider",
    "_uses_direct_env_provider",
    "canonicalize_llm_channel_protocol",
    "channel_allows_empty_api_key",
    "channels_to_model_list",
    "load_dotenv",
    "os",
    "parse_env_bool",
    "parse_env_int",
    "parse_env_float",
    "parse_llm_channels",
    "setup_env",
    "load_stocks_from_yaml",
    "load_settings_from_yaml",
    "get_configured_llm_models",
    "get_effective_agent_primary_model",
    "get_effective_agent_models_to_try",
    "legacy_keys_to_model_list",
    "normalize_agent_litellm_model",
    "normalize_llm_channel_model",
    "normalize_litellm_temperature",
    "resolve_unified_llm_temperature",
    "resolve_news_window_days",
    "resolve_llm_channel_protocol",
    "parse_litellm_yaml",
    "NEWS_STRATEGY_WINDOWS",
    "normalize_news_strategy_profile",
    # New exports
    "ConfigValidator",
    "ConfigValidationError",
    "UnifiedConfigLoader",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_init.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/__init__.py tests/test_config_init.py
git commit -m "feat(config): export ConfigValidator and UnifiedConfigLoader from __init__"
```

---

### Task 9: Update AGENTS.md Documentation

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update section 2 (Common Commands) - config related**

In `AGENTS.md` section 2, add new commands and update existing:

```markdown
### 配置验证

```bash
# 验证配置（strict 模式，失败会抛出异常）
python -c "from src.config import get_config; get_config()"

# 查看当前配置加载结果
python -c "from src.config import get_config; import json; print(json.dumps(get_config(), indent=2, default=str))"
```

### 后端验证

```bash
pip install -r requirements.txt
pip install flake8 pytest pyyaml python-dotenv
./scripts/ci_gate.sh
python -m pytest -m "not network"
python -m py_compile <changed_python_files>
```

- [ ] **Step 2: Update section 5 (Default Workflow) - mention two-file config**

In `AGENTS.md` section 5, update the workflow to mention the new config structure:

```markdown
## 5. 默认工作流

1. 先判断任务类型：`fix / feat / refactor / docs / chore / test / review`
2. 先读现有实现、配置、测试、脚本、工作流和文档，再动手修改。
   - 配置读取顺序：`.env`（敏感 Key）→ `config.yaml`（业务参数）→ 环境变量覆盖
   - `settings.yaml` 和 `litellm_config.yaml` 已废弃，请迁移到 `config.yaml`
3. 识别改动边界：后端 / API / Workflow / Docs / AI 协作资产。
...
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md for new two-file config structure"
```

---

### Task 10: Create Migration Guide

**Files:**
- Create: `docs/migration-guide.md`

- [ ] **Step 1: Write migration guide**

```markdown
# Configuration Migration Guide

## Overview

The configuration system has been simplified from a four-layer structure (.env + settings.yaml + litellm_config.yaml + config_registry.py) to a two-file structure:

- `.env` — Sensitive keys and tokens only
- `config.yaml` — All business parameters and LLM routing

## Migration Steps

### Step 1: Backup Your Current Configuration

```bash
cp .env .env.backup
cp settings.yaml settings.yaml.backup
cp litellm_config.yaml litellm_config.yaml.backup
```

### Step 2: Update .env File

Remove all business parameters from `.env`:

**Remove these types of parameters:**
- `MAX_WORKERS`, `LOG_LEVEL`, `DEBUG`
- `SCHEDULE_ENABLED`, `SCHEDULE_TIME`, `MARKET_REVIEW_ENABLED`
- `REPORT_TYPE`, `REPORT_LANGUAGE`, `REPORT_SUMMARY_ONLY`
- `BIAS_THRESHOLD`, `NEWS_MAX_AGE_DAYS`, `ANALYSIS_MODE`
- All `LLM_*` and `LITELLM_*` variables (move to config.yaml llm section)

**Keep in .env:**
- All API keys and tokens (GEMINI_API_KEY, DEEPSEEK_API_KEY, etc.)
- All notification webhook URLs and credentials
- All data source credentials (TUSHARE_TOKEN, FUTU_*, etc.)

### Step 3: Create config.yaml

Copy the `config.yaml` template from the repository root into your working directory, then customize:

```bash
# The template is already in the repo root
# Edit it with your preferred values
vim config.yaml
```

### Step 4: Migrate settings.yaml Parameters

If you had custom values in `settings.yaml`, move them to `config.yaml`:

| settings.yaml path | config.yaml path |
|-------------------|-------------------|
| `analysis.mode` | `analysis.mode` |
| `analysis.language` | `analysis.language` |
| `analysis.bias_threshold` | `analysis.bias_threshold` |
| `system.max_workers` | `system.max_workers` |
| `system.log_level` | `system.log_level` |
| `system.report_dir` | `system.report_dir` |
| `notification.report_type` | `notification.report_type` |
| `notification.summary_only` | `notification.summary_only` |
| `data.prefetch_quotes` | `data.prefetch_quotes` |
| `data.cache_ttl` | `data.cache_ttl` |

### Step 5: Migrate litellm_config.yaml Parameters

If you used `litellm_config.yaml`, move the configuration to `config.yaml`:

```yaml
# Old litellm_config.yaml
model_list:
  - model_name: deepseek-v4-flash
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: "os.environ/DEEPSEEK_API_KEY"

# New config.yaml
llm:
  primary_model: "deepseek/deepseek-v4-flash"
  fallback_models: []
  temperature: 0.7
  channels: []
```

Or set `LITELLM_CONFIG` in `.env` to point to your existing YAML file.

### Step 6: Validate Configuration

```bash
python -c "from src.config import get_config; get_config()"
```

If validation fails, check the error messages and fix the configuration.

### Step 7: Remove Old Files (Optional)

After confirming everything works:

```bash
rm settings.yaml.backup
rm litellm_config.yaml.backup
# Keep .backup files until you're sure the migration is successful
```

## FAQ

**Q: What if I still have business parameters in .env?**
A: The new `UnifiedConfigLoader` will still read them, but a deprecation warning will be logged. Please migrate to `config.yaml`.

**Q: Can I keep using settings.yaml?**
A: It's marked as deprecated. The new loader doesn't read it. Please migrate to `config.yaml`.

**Q: Where did my LLM routing config go?**
A: Move it to the `llm` section in `config.yaml`, or set `LITELLM_CONFIG` env var.

**Q: Validation fails with "required field missing"**
A: Check that you have at least one LLM API key set in `.env` (e.g., `GEMINI_API_KEY`).
```

- [ ] **Step 2: Commit**

```bash
git add docs/migration-guide.md
git commit -m "docs: add configuration migration guide for users"
```

---

### Task 11: Final Integration Test

**Files:**
- None (testing only)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v -m "not network"`
Expected: PASS (all non-network tests)

- [ ] **Step 2: Run config validation**

Run: `python -c "from src.config import get_config; config = get_config(); print('Config loaded successfully')"`
Expected: Success message (or clear error if misconfigured)

- [ ] **Step 3: Verify .env.example works**

Run: `cp .env.example .env.test && python -c "from src.config import get_config; get_config()" && rm .env.test`
Expected: Should fail with validation error (no real API keys), but shows validation is working

- [ ] **Step 4: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "feat(config): complete config simplification - validate integration"
```

---

## Summary

This plan implements a two-file configuration system (.env + config.yaml) with strict validation. Each task is 2-5 minutes and follows TDD (test first, implement, commit). The plan covers:

1. `ConfigValidationError` exception
2. `ConfigValidator` with required, type, enum, range, and sensitive key checks
3. `UnifiedConfigLoader` for loading and merging config
4. New `config.yaml` template
5. Simplified `.env.example`
6. Deprecation notices for old files
7. Updated exports and documentation
8. Migration guide for users
9. Integration testing
