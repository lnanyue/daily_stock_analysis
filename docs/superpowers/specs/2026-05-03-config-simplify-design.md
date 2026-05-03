# 配置简化设计文档

**日期：** 2026-05-03
**分支：** feature/config-simplify
**设计人：** Claude Code + 用户协作

---

## 1. 背景与问题

现有配置体系存在四层分散：

| 文件 | 职责 | 问题 |
|------|------|------|
| `.env` | 混合敏感 Key + 业务参数 | 220 行 `.env.example` 臃肿，业务参数与敏感 Key 混杂 |
| `settings.yaml` | 部分业务参数 | 与 `.env` 字段重叠（如 `report_type`） |
| `litellm_config.yaml` | LLM 路由 | 与 `.env` 中 `LITELLM_CONFIG`/`LLM_CHANNELS` 重叠 |
| `config_registry.py` | 字段元数据 | 缺少统一校验，UI 元数据未参与运行时校验 |

**核心问题：** 配置分散、职责不清、缺少统一 strict 校验、新用户上手路径复杂。

---

## 2. 设计目标

1. **双文件分工** — `.env` 只保留敏感 Key/Token，`config.yaml` 存放全部业务参数和 LLM 路由
2. **统一校验** — 基于 `config_registry.py` 字段元数据，启动时 strict 校验，失败直接阻断
3. **一步到位** — 直接重构，更新 `.env.example` 和 `config.yaml` 模板，同步修改文档
4. **不破坏消费方接口** — `src/config/__init__.py` 导出接口不变，`data_provider/`、`bot/`、`src/services/` 等消费方无感知

---

## 3. 架构概览

### 3.1 双文件分工

| 文件 | 职责 | 示例字段 |
|------|------|----------|
| `.env` | 仅敏感 Key/Token | `GEMINI_API_KEY`, `FEISHU_WEBHOOK_URL`, `TUSHARE_TOKEN` |
| `config.yaml` | 全部业务参数 + LLM 路由 | `analysis.mode`, `system.max_workers`, `llm.primary_model`, `notification.channels` |

### 3.2 核心新增组件

```
src/config/
├── __init__.py          # 保持不变，新增导出 ConfigValidator, UnifiedConfigLoader
├── manager.py            # 现有 Config 类，改为组合 UnifiedConfigLoader
├── utils.py              # 现有工具函数，保持不变
├── validator.py          # 新增：ConfigValidator，strict 校验逻辑
├── loader.py             # 新增：UnifiedConfigLoader，统一加载 .env + config.yaml
├── models.py             # 保持不变
```

### 3.3 数据流

```
.env → dotenv → os.environ
                     ↓
config.yaml → YAML解析 → config dict
                     ↓
        UnifiedConfigLoader.merge() → ConfigValidator.validate() → 阻断或继续
```

---

## 4. ConfigValidator 设计

**定位：** 纯校验器，不负责加载，只接收已加载的配置字典，对照 `config_registry.py` 做 strict 校验。

### 4.1 校验项

1. **必填字段检查** — `config_registry` 中 `is_required=True` 的字段，若值为 `None`/空字符串，抛出 `ConfigValidationError` 并列出缺失字段
2. **类型校验** — 根据 `data_type`（string/integer/number/boolean/array）校验，类型不匹配则抛异常
3. **枚举值校验** — `validation.enum` 中定义的字段，值不在列表中则抛异常
4. **数值范围校验** — `validation.min` / `validation.max`，越界则抛异常
5. **敏感 Key 有效性** — 对 `is_sensitive=True` 的字段，检查长度（如 API Key ≥ 8 字符），无效则抛异常

### 4.2 校验入口

```python
def validate_all(env_dict: dict, config_dict: dict) -> None:
    """校验全部配置，失败直接抛出 ConfigValidationError"""
```

### 4.3 错误信息格式

```
Config validation failed:
  [REQUIRED] GEMINI_API_KEY is required but missing
  [TYPE] MAX_WORKERS=abc is not a valid integer
  [ENUM] REPORT_TYPE=brief is not in ['simple', 'full']
  [RANGE] BIAS_THRESHOLD=60.0 exceeds max 50.0
```

---

## 5. UnifiedConfigLoader 设计

**定位：** 统一加载器，按优先级合并 `.env` + `config.yaml` + 环境变量 fallback，供 `manager.py` 中的 `Config` 类使用。

### 5.1 加载优先级（从低到高）

1. `config.yaml` 默认值（YAML 文件中的 `default_value`）
2. `.env` 文件（敏感 Key，通过 `dotenv` 加载）
3. `config.yaml` 业务参数（YAML 中实际配置的值）
4. 环境变量（运行期覆盖，最高优先级）

### 5.2 核心方法

```python
class UnifiedConfigLoader:
    def load(self) -> dict:
        """返回合并后的完整配置字典"""
        env_dict = self._load_env()          # .env → dict
        yaml_dict = self._load_yaml()        # config.yaml → dict
        merged = self._merge(env_dict, yaml_dict)
        ConfigValidator.validate_all(env_dict, merged)
        return merged

    def _load_env(self) -> dict:
        """加载 .env，只提取敏感 Key（参考 config_registry 中 is_sensitive=True）"""

    def _load_yaml(self) -> dict:
        """加载 config.yaml，按分类组织（analysis/system/notification/llm 等）"""

    def _merge(self, env_dict, yaml_dict) -> dict:
        """按优先级合并，环境变量可覆盖 YAML 值"""
```

### 5.3 config.yaml 新结构（示意）

```yaml
# 业务参数 + LLM 路由，统一在此配置
analysis:
  mode: simple
  language: zh
  bias_threshold: 5.0
  news_window_days: 3
  request_delay: 2.0
  integrity_retry: 1

system:
  max_workers: 2
  log_level: INFO
  report_dir: "./report"
  schedule_enabled: false
  schedule_time: "18:00"
  trading_day_check_enabled: true
  market_review_enabled: true
  market_review_region: "cn"
  debug: false

llm:
  primary_model: "gemini/gemini-3-flash-preview"
  fallback_models: []
  temperature: 0.7
  channels: []

notification:
  report_type: simple
  summary_only: false
  markdown_to_image: []
  merge_email: false
  feishu_webhook_url: null    # 实际值放 .env
  email_enabled: false
  discord_enabled: false

data:
  prefetch_quotes: true
  cache_ttl: 600
  eastmoney_patch: false
  realtime_source_priority: "akshare_em,tencent,akshare_sina,efinance"
  enable_realtime_quote: true
  enable_realtime_technical_indicators: true
  enable_chip_distribution: true
```

---

## 6. 现有代码修改清单

### 6.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/config/validator.py` | ConfigValidator 实现 |
| `src/config/loader.py` | UnifiedConfigLoader 实现 |

### 6.2 修改文件

| 文件 | 改动内容 |
|------|----------|
| `src/config/__init__.py` | 导出 `ConfigValidator`, `UnifiedConfigLoader` |
| `src/config/manager.py` | `Config` 类改为组合 `UnifiedConfigLoader`，初始化时自动校验 |
| `src/core/config_manager.py` | 标记为 deprecated，迁移到新加载器后移除 |
| `src/core/config_registry.py` | 保持不变，作为字段元数据真源 |
| `.env.example` | 只保留敏感 Key（移除 `MAX_WORKERS`, `LOG_LEVEL`, `SCHEDULE_TIME` 等业务参数） |
| `config.yaml` | 新建，包含全部业务参数模板（从 `.env.example` 和 `settings.yaml` 迁移） |
| `settings.yaml` | 标记为 deprecated，合并到 `config.yaml` 后移除 |
| `litellm_config.yaml` | 废弃，LLM 路由配置合并到 `config.yaml` 的 `llm` 段 |
| `AGENTS.md` | 更新"2. 常用命令"和"5. 默认工作流"中关于配置文件的说明 |

### 6.3 不改动的文件

- `src/config/utils.py` — 工具函数保持不变
- `src/config/models.py` — 数据模型保持不变
- `data_provider/`, `bot/`, `src/services/` 等 — 配置消费方通过 `src/config/__init__.py` 获取配置，接口不变

---

## 7. 验证矩阵

| 改动面 | 验证方法 | 是否阻断 |
|--------|----------|----------|
| 配置加载 | `python -c "from src.config import get_config; get_config()"` | 是 |
| 校验逻辑 | 故意制造错误配置 → 确认抛出 `ConfigValidationError` | 是 |
| 双文件分工 | `.env` 只含敏感 Key + `config.yaml` 含业务参数 → 启动成功 | 是 |
| 旧配置兼容 | 确认 `settings.yaml` 和旧 `.env` 业务参数不再被读取 | 否 |
| 消费方接口 | `from src.config import get_config` 接口不变 | 是 |

---

## 8. 风险点与回滚

### 8.1 风险点

1. **迁移断层** — 用户从旧版本升级，`.env` 中仍有业务参数，需要清晰的迁移指南
2. **config_registry 元数据不完整** — 部分字段缺少 `validation` 定义，校验可能遗漏
3. **YAML 格式敏感度** — `config.yaml` 缩进/格式错误会导致加载失败，需要友好报错

### 8.2 回滚方式

- 保留 `.env.example` 和 `settings.yaml` 的备份副本
- 若新配置加载失败，可回退到旧版 `manager.py`（通过 git revert）
- 旧 `src/core/config_manager.py` 标记为 deprecated 但暂不删除，留一个版本作为回退参考

---

## 9. 未验证项

1. **Docker 环境** — Docker 镜像中配置加载路径是否受影响
2. **GitHub Actions** — CI 流程中配置环境变量注入方式是否需要调整
3. **插件配置** — `plugins.yaml` 与新的 `config.yaml` 是否需要进一步整合（本次不改）

---

## 10. 交付结构

- **改了什么：** `.env` 只保留敏感 Key，`config.yaml` 接管业务参数，新增 `ConfigValidator` + `UnifiedConfigLoader`，`config_registry.py` 元数据参与运行时校验
- **为什么这么改：** 双文件职责清晰，统一校验防错，一步到位不欠技术债
- **验证情况：** 待实现后执行验证矩阵
- **未验证项：** Docker、GitHub Actions、插件配置（本次不改）
- **风险点：** 迁移断层、元数据不完整、YAML 格式敏感度
- **回滚方式：** git revert + 旧配置备份
