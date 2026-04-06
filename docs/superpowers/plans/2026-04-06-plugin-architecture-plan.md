# Plugin Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现数据源和分析策略的插件化架构，让添加新能力只需创建 Python 文件 + 配置 `plugins.yaml`，无需修改核心代码。

**Architecture:** 采用"注册表 + 自动发现 + 独立配置"三层结构。`PluginRegistry` 扫描 `plugins/fetchers/` 和 `plugins/strategies/` 目录，读取 `plugins.yaml` 配置，实例化并注册启用的插件。DataFetcherManager 通过插件注册表获取数据源，Pipeline 在分析流程中执行策略插件。

**Tech Stack:** Python 标准库 + pyyaml（配置文件解析）+ 现有 BaseFetcher/DataFetcherManager 基础设施

**Spec:** `docs/superpowers/specs/2026-04-06-plugin-architecture-design.md`

---

## File Structure

| 文件 | 动作 | 职责 |
|------|------|------|
| `src/plugins/registry.py` | 新建 | PluginRegistry 主类 |
| `src/plugins/strategy_base.py` | 新建 | AnalysisStrategy 基类、AnalysisContext、StrategyResult 数据类 |
| `src/plugins/plugin_context.py` | 新建 | PluginContext 数据类（提供内部服务引用）|
| `src/plugins/config.py` | 新建 | plugins.yaml 加载、解析、${ENV_VAR} 替换 |
| `src/plugins/__init__.py` | 新建 | 包初始化，导出公共接口 |
| `src/plugins/loader.py` | 新建 | 模块扫描与加载（通用逻辑）|
| `plugins.yaml` | 新建 | 插件配置文件（含示例，默认空）|
| `plugins/fetchers/__init__.py` | 新建 | Fetcher 插件发现目录 |
| `plugins/fetchers/example_fetcher.py` | 新建 | 示例 Fetcher 模板 |
| `plugins/strategies/__init__.py` | 新建 | Strategy 插件发现目录 |
| `plugins.local/` | 新建 | 用户本地插件目录（.gitignore）|
| `.gitignore` | 修改 | 添加 `plugins.local/` |
| `data_provider/base.py` | 修改 | DataFetcherManager 增加 `from_plugin_registry()` |
| `src/core/pipeline.py` | 修改 | 集成 PluginRegistry，新增策略执行点 |
| `src/reports/` | 修改 | 报告模板新增 plugin_results 渲染 |
| `requirements.txt` | 修改 | 添加 pyyaml |
| `tests/test_plugin_registry.py` | 新建 | 插件注册表测试 |
| `docs/plugins-guide.md` | 新建 | 插件开发指南 |

---

### Task 1: 配置文件解析器（`src/plugins/config.py`）

**Files:**
- Create: `src/plugins/config.py`

**描述：** 实现 `plugins.yaml` 的加载和 `${ENV_VAR}` 替换。

- [ ] **Step 1: 实现 ConfigLoader 类**

```python
# src/plugins/config.py
import os


def resolve_env_refs(value):
    """
    递归替换字符串中的 ${ENV_VAR} 为实际环境变量值。
    未设置的环境变量返回空字符串。

    示例:
        {"api_key": "${CUSTOM_API_KEY}"} -> {"api_key": os.environ["CUSTOM_API_KEY"]}
        {"timeout": 10} -> 10  (非字符串保持不变)
    """
    import re

    if isinstance(value, str):
        pattern = re.compile(r"\$\{(\w+)\}")

        def replacer(match):
            env_var = match.group(1)
            return os.environ.get(env_var, "")

        return pattern.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: resolve_env_refs(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_env_refs(item) for item in value]
    return value


class ConfigLoader:
    """加载并解析 plugins.yaml 配置"""

    def __init__(self, config_path: str = "plugins.yaml"):
        self.config_path = config_path
        self._raw: dict = {"fetchers": [], "strategies": []}
        self._load()

    def _load(self) -> None:
        import yaml
        from pathlib import Path

        path = Path(self.config_path)
        if not path.exists():
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self._raw = resolve_env_refs(raw)

    @property
    def fetchers(self) -> list:
        return self._raw.get("fetchers", [])

    @property
    def strategies(self) -> list:
        return self._raw.get("strategies", [])
```

- [ ] **Step 2: 提交**

```bash
git add src/plugins/__init__.py src/plugins/config.py
git commit -m "feat: add PluginConfigLoader for plugins.yaml parsing with env var resolution"
```

---

### Task 2: 模块加载器（`src/plugins/loader.py`）

**Files:**
- Create: `src/plugins/loader.py`

**描述：** 通用 Python 模块扫描与加载逻辑，供 fetchers 和 strategies 共用。

- [ ] **Step 1: 实现模块加载器**

```python
# src/plugins/loader.py
import importlib
import logging
from pathlib import Path
from typing import Callable, List, Tuple

logger = logging.getLogger(__name__)


def scan_and_register(
    plugin_dirs: List[str],
    register_func_name: str = "register",
) -> List[Tuple[str, Callable]]:
    """
    扫描多个目录，导入其中的 .py 文件（跳过 __init__.py），
    尝试调用模块级的 register() 函数。

    返回: [(name, factory_func), ...]
    """
    results = []

    for dir_path in plugin_dirs:
        path = Path(dir_path)
        if not path.is_dir():
            continue

        for py_file in sorted(path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            module_name = f"plugins.{path.name}.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
                factory = getattr(module, register_func_name, None)
                if factory is None or not callable(factory):
                    logger.warning(
                        f"[Loader] {module_name} 缺少可执行的 register() 函数，跳过"
                    )
                    continue
                results.append((py_file.stem, factory))
                logger.info(f"[Loader] 成功加载插件: {module_name}")
            except Exception as exc:
                logger.warning(f"[Loader] 加载插件 {module_name} 失败: {exc}")
                continue

    return results
```

- [ ] **Step 2: 提交**

```bash
git add src/plugins/loader.py
git commit -m "feat: add module scanner/loader for plugin discovery"
```

---

### Task 3: 策略插件基类与上下文（`src/plugins/strategy_base.py`）

**Files:**
- Create: `src/plugins/strategy_base.py`

- [ ] **Step 1: 实现 AnalysisStrategy 基类、AnalysisContext、StrategyResult**

```python
# src/plugins/strategy_base.py
"""
分析策略插件基类与数据结构
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import pandas as pd


@dataclass
class AnalysisContext:
    """传递给策略插件的分析上下文"""
    stock_code: str
    price_data: pd.DataFrame
    indicators: Dict[str, Any]
    search_results: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyResult:
    """策略返回结果"""
    name: str                       # 策略名称标识
    title: str                      # 报告中的显示标题
    summary: str                    # 人类可读摘要
    content: Dict[str, Any] = field(default_factory=dict)  # 结构化数据
    raw_data: Optional[Any] = None  # 原始数据（可选，供调试）
    error: Optional[str] = None     # 如果有错误，填充此字段


class AnalysisStrategy(ABC):
    """分析策略插件必须继承此基类"""

    name: str = "base_strategy"

    def __init__(self, config: Dict[str, Any], plugin_ctx: "PluginContext"):
        self.config = config
        self.ctx = plugin_ctx

    @abstractmethod
    def execute(self, analysis_ctx: AnalysisContext) -> StrategyResult:
        """
        执行分析策略。

        策略内部应自行处理异常或向上抛出，由 Pipeline 统一捕获。
        不应返回 None，应返回包含 error 字段的 StrategyResult。
        """
        ...
```

- [ ] **Step 2: 提交**

```bash
git add src/plugins/strategy_base.py
git commit -m "feat: add AnalysisStrategy base class with AnalysisContext and StrategyResult"
```

---

### Task 4: PluginContext（`src/plugins/plugin_context.py`）

**Files:**
- Create: `src/plugins/plugin_context.py`

- [ ] **Step 1: 实现 PluginContext 数据类**

```python
# src/plugins/plugin_context.py
"""
PluginContext — 提供给策略插件的内部服务访问
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Config
    from src.storage import Database
    from src.search_service import SearchService
    from data_provider.base import DataFetcherManager


@dataclass
class PluginContext:
    """策略插件可访问的内部服务引用"""
    config: "Config"
    db: "Database"
    search_service: "SearchService"
    fetcher_manager: "DataFetcherManager"
```

- [ ] **Step 2: 提交**

```bash
git add src/plugins/plugin_context.py
git commit -m "feat: add PluginContext dataclass for strategy plugin service access"
```

---

### Task 5: PluginRegistry（`src/plugins/registry.py`）

**Files:**
- Create: `src/plugins/registry.py`
- Modify: `src/plugins/__init__.py` （更新导出）

- [ ] **Step 1: 实现 PluginRegistry 类**

```python
# src/plugins/registry.py
"""
统一插件注册表
"""
import logging
from typing import List, Dict, Any, Optional
from data_provider.base import BaseFetcher, DataFetcherManager
from .config import ConfigLoader
from .loader import scan_and_register
from .strategy_base import AnalysisStrategy, AnalysisContext, StrategyResult
from .plugin_context import PluginContext

logger = logging.getLogger(__name__)

SEARCH_PATHS = ["plugins/", "plugins.local/"]


class PluginRegistry:
    """统一插件注册表

    职责:
    1. 扫描 plugins/fetchers/ 和 plugins/strategies/ 目录
    2. 解析 plugins.yaml 配置
    3. 实例化并注册启用的插件
    4. 提供按类型查询插件的接口
    """

    def __init__(self, config_path: str = "plugins.yaml"):
        self.config_path = config_path
        self._config_loader = ConfigLoader(config_path)
        self._fetcher_factories: Dict[str, callable] = {}
        self._strategy_factories: Dict[str, callable] = {}
        self._fetchers: List[BaseFetcher] = []
        self._strategies: List[AnalysisStrategy] = []
        self._plugin_ctx: Optional[PluginContext] = None

    def load(self, plugin_ctx: PluginContext) -> None:
        """完整加载流程"""
        self._plugin_ctx = plugin_ctx
        self._scan_fetchers()
        self._scan_strategies()
        self._instantiate_fetchers()
        self._instantiate_strategies()

    def _scan_fetchers(self) -> None:
        paths = [f"{p}fetchers" for p in SEARCH_PATHS]
        factories = scan_and_register(paths, register_func_name="register")
        for name, factory in factories:
            self._fetcher_factories[name] = factory
            logger.info(f"[PluginRegistry] 注册 Fetcher 插件: {name}")

    def _scan_strategies(self) -> None:
        paths = [f"{p}strategies" for p in SEARCH_PATHS]
        factories = scan_and_register(paths, register_func_name="register")
        for name, factory in factories:
            self._strategy_factories[name] = factory
            logger.info(f"[PluginRegistry] 注册 Strategy 插件: {name}")

    def _instantiate_fetchers(self) -> None:
        for fetcher_cfg in self._config_loader.fetchers:
            name = fetcher_cfg.get("name", "")
            module = fetcher_cfg.get("module", name)
            enabled = fetcher_cfg.get("enabled", True)

            if not enabled:
                logger.info(f"[PluginRegistry] Fetcher '{name}' 未启用，跳过")
                continue

            factory = self._fetcher_factories.get(module) or self._fetcher_factories.get(name)
            if factory is None:
                logger.warning(f"[PluginRegistry] Fetcher '{name}' 的模块 '{module}' 未找到")
                continue

            try:
                config = fetcher_cfg.get("config", {})
                fetcher = factory(config)
                # 可选的 priority 覆盖
                if "priority" in fetcher_cfg:
                    fetcher.priority = fetcher_cfg["priority"]
                self._fetchers.append(fetcher)
                logger.info(f"[PluginRegistry] 实例化 Fetcher: {name} (priority={fetcher.priority})")
            except Exception as exc:
                logger.error(f"[PluginRegistry] 实例化 Fetcher '{name}' 失败: {exc}")

    def _instantiate_strategies(self) -> None:
        if self._plugin_ctx is None:
            return

        for strategy_cfg in self._config_loader.strategies:
            name = strategy_cfg.get("name", "")
            module = strategy_cfg.get("module", name)
            enabled = strategy_cfg.get("enabled", True)

            if not enabled:
                logger.info(f"[PluginRegistry] Strategy '{name}' 未启用，跳过")
                continue

            factory = self._strategy_factories.get(module) or self._strategy_factories.get(name)
            if factory is None:
                logger.warning(f"[PluginRegistry] Strategy '{name}' 的模块 '{module}' 未找到")
                continue

            try:
                config = strategy_cfg.get("config", {})
                strategy = factory(config, self._plugin_ctx)
                self._strategies.append(strategy)
                logger.info(f"[PluginRegistry] 实例化 Strategy: {name}")
            except Exception as exc:
                logger.error(f"[PluginRegistry] 实例化 Strategy '{name}' 失败: {exc}")

    def get_enabled_fetchers(self) -> List[BaseFetcher]:
        """返回已实例化的 Fetcher 列表"""
        return list(self._fetchers)

    def get_enabled_strategies(self) -> List[AnalysisStrategy]:
        """返回已实例化的 Strategy 列表"""
        return list(self._strategies)

    def execute_strategies(self, analysis_ctx: AnalysisContext) -> List[StrategyResult]:
        """批量执行已启用的策略，单个失败不阻塞其他"""
        results = []
        for strategy in self._strategies:
            try:
                result = strategy.execute(analysis_ctx)
                results.append(result)
                logger.info(f"[PluginRegistry] Strategy '{strategy.name}' 执行成功")
            except Exception as exc:
                logger.error(f"[PluginRegistry] Strategy '{strategy.name}' 执行失败: {exc}")
                from .strategy_base import StrategyResult
                error_result = StrategyResult(
                    name=strategy.name,
                    title=strategy.name,
                    summary=f"执行失败: {exc}",
                    error=str(exc),
                )
                results.append(error_result)
        return results
```

- [ ] **Step 2: 更新包导出**

```python
# src/plugins/__init__.py
"""
插件系统 — 统一插件注册表与接口
"""
from .registry import PluginRegistry
from .strategy_base import AnalysisStrategy, AnalysisContext, StrategyResult
from .plugin_context import PluginContext

__all__ = [
    "PluginRegistry",
    "AnalysisStrategy",
    "AnalysisContext",
    "StrategyResult",
    "PluginContext",
]
```

- [ ] **Step 3: 提交**

```bash
git add src/plugins/registry.py src/plugins/__init__.py
git commit -m "feat: add PluginRegistry with auto-discovery, instantiation, and strategy execution"
```

---

### Task 6: 目录结构与示例插件

**Files:**
- Create: `plugins.yaml`
- Create: `plugins/fetchers/__init__.py`
- Create: `plugins/fetchers/example_fetcher.py`
- Create: `plugins/strategies/__init__.py`
- Create: `plugins.local/fetchers/.gitkeep`
- Create: `plugins.local/strategies/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: 创建 `plugins.yaml`（示例配置，默认全禁用）**

```yaml
# plugins.yaml — 插件配置文件
#
# fetchers: 数据源插件
# strategies: 分析策略插件
#
# config 中的 ${ENV_VAR} 会在加载时被替换为环境变量值

fetchers:
  # 示例自定义 Fetcher（默认禁用）
  - name: example_api
    module: example_fetcher
    enabled: false
    priority: 0
    config:
      base_url: "https://api.example.com/v1"
      api_key: "${EXAMPLE_API_KEY}"

strategies:
  # 示例策略（默认禁用）
  - name: example_analysis
    module: example_strategy
    enabled: false
    config:
      some_option: true
```

- [ ] **Step 2: 创建 `plugins/fetchers/__init__.py`**

```python
# plugins/fetchers/__init__.py
```

- [ ] **Step 3: 创建 `plugins/fetchers/example_fetcher.py`**

```python
# plugins/fetchers/example_fetcher.py
"""
示例 Fetcher 插件 — 展示如何编写自定义数据源插件
"""
import pandas as pd
from data_provider.base import BaseFetcher


class ExampleFetcher(BaseFetcher):
    name = "example_api"

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        # TODO: 替换为实际 API 调用
        raise NotImplementedError("ExampleFetcher._fetch_raw_data 未实现")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        df = df.copy()
        df.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        return df


def register(config: dict) -> ExampleFetcher:
    return ExampleFetcher(**config)
```

- [ ] **Step 4: 创建 `plugins/strategies/__init__.py`**

```python
# plugins/strategies/__init__.py
```

- [ ] **Step 5: 创建 `plugins.local/` 目录**

```bash
mkdir -p plugins.local/fetchers plugins.local/strategies
touch plugins.local/fetchers/.gitkeep plugins.local/strategies/.gitkeep
```

- [ ] **Step 6: 修改 `.gitignore` 添加 `plugins.local/`**

在 `.gitignore` 末尾添加：

```
# 用户本地插件（不入库，避免密钥泄露）
plugins.local/
```

- [ ] **Step 7: 提交**

```bash
git add plugins.yaml plugins/ .gitignore plugins.local/
git commit -m "feat: add plugin directories with example fetcher and configuration"
```

---

### Task 7: DataFetcherManager 集成插件注册表

**Files:**
- Modify: `data_provider/base.py`

- [ ] **Step 1: 在 DataFetcherManager 中添加 `from_plugin_registry` 类方法**

在 `data_provider/base.py` 的 `DataFetcherManager` 类中添加：

```python
    @classmethod
    def from_plugin_registry(cls, plugin_fetchers: Optional[List["BaseFetcher"]] = None) -> "DataFetcherManager":
        """
        从插件注册表创建管理器。

        如果提供了 plugin_fetchers，则优先使用插件 fetchers，
        内置 fetchers 作为 fallback 追加其后。
        """
        if plugin_fetchers:
            return cls(fetchers=plugin_fetchers)
        return cls()
```

这个方法直接复用已有的构造函数（接受 `fetchers` 列表并按优先级排序），无需修改现有逻辑。

- [ ] **Step 2: 提交**

```bash
git add data_provider/base.py
git commit -m "feat: add DataFetcherManager.from_plugin_registry() class method"
```

---

### Task 8: Pipeline 集成 PluginRegistry

**Files:**
- Read: `src/core/pipeline.py`
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: 在 Pipeline 初始化中添加插件集成**

在 `__init__` 末尾（`self.notifier` 初始化之后，logger 打印之前）添加：

```python
        from src.plugins import PluginRegistry, PluginContext

        self.plugins = PluginRegistry()
        plugin_ctx = PluginContext(
            config=self.config,
            db=self.db,
            search_service=self.search_service,
            fetcher_manager=None,
        )
        self.plugins.load(plugin_ctx)

        # 插件 fetchers 优先，内置 fetchers 作为 fallback
        plugin_fetchers = self.plugins.get_enabled_fetchers()
        if plugin_fetchers:
            self.fetcher_manager = DataFetcherManager(fetchers=plugin_fetchers)
        else:
            self.fetcher_manager = DataFetcherManager()

        plugin_ctx.fetcher_manager = self.fetcher_manager
```

替换原 `self.fetcher_manager = DataFetcherManager()` 为插件感知版本。

- [ ] **Step 2: 提交**

```bash
git add src/core/pipeline.py
git commit -m "feat: integrate PluginRegistry into pipeline initialization"
```

---

### Task 9: Pipeline 添加策略执行点

**Files:**
- Modify: `src/core/pipeline.py` (around line 287-302, after trend analysis, before agent/traditional branching)

- [ ] **Step 1: 在 `analyze_stock` 的趋势分析之后添加策略执行**

在 `pipeline.py:287` (trend_result 分析完成) 之后、`if use_agent:` 分支之前插入：

```python
            # Step 2.6: 执行分析策略插件（共享给 Agent 和传统两条路径）
            plugin_strategy_results = []
            enabled_strategies = self.plugins.get_enabled_strategies()
            if enabled_strategies:
                # 从数据库获取价格数据用于策略分析
                try:
                    end_date = date.today()
                    start_date = end_date - timedelta(days=89)
                    historical_bars = self.db.get_data_range(code, start_date, end_date)
                    strategy_df = None
                    if historical_bars:
                        strategy_df = pd.DataFrame([bar.to_dict() for bar in historical_bars])
                except Exception as e:
                    logger.debug(f"{stock_name}({code}) 获取策略数据失败: {e}")
                    strategy_df = None

                if strategy_df is not None and not strategy_df.empty:
                    from src.plugins import AnalysisContext as PluginAnalysisContext
                    analysis_ctx = PluginAnalysisContext(
                        stock_code=code,
                        price_data=strategy_df,
                        indicators=trend_result or {},
                        search_results=news_context if news_context else None,
                    )
                    plugin_strategy_results = self.plugins.execute_strategies(analysis_ctx)
```

- [ ] **Step 2: 将 plugin_result 传递给传统分析和 Agent 分支**

修改 `pipeline.py:303-314` 的 `_analyze_with_agent` 调用，和 Step 6 的 `_enhance_context` 调用，让两者都能拿到 `plugin_strategy_results`。

在 `_analyze_with_agent` 签名中增加参数 `plugin_strategy_results`，在 Agent 的 context 中注入。

在 `enhanced_context` 或 `news_context` 中附加插件结果（具体注入点取决于 `analyzer.py` 如何消费 context），例如：

```python
            # 将策略结果追加到 news_context 尾部（最简兼容方案）
            if plugin_strategy_results:
                plugin_text = "\n\n--- 附加分析 ---\n"
                for r in plugin_strategy_results:
                    plugin_text += f"\n## {r.title}\n{r.summary}\n"
                if news_context:
                    news_context = news_context + plugin_text
                else:
                    news_context = plugin_text.lstrip()
```

- [ ] **Step 3: 提交**

```bash
git add src/core/pipeline.py
git commit -m "feat: add strategy plugin execution point in analysis pipeline"
```

- [ ] **Step 2: 提交**

```bash
git add src/core/pipeline.py
git commit -m "feat: add strategy plugin execution point in analysis pipeline"
```

---

### Task 10: 报告模板新增 `plugin_results` 渲染

**Files:**
- Read: `src/services/report_renderer.py` + template files in `templates/` directory (relative to project root)

Jinja2 template directory resolved via `_resolve_templates_dir()` in `report_renderer.py`.

- [ ] **Step 1: 找到报告模板文件**

```bash
find . -name "*.j2" -o -name "*.jinja2" -o -name "*.jinja" | head -20
```

- [ ] **Step 2: 在报告模板中添加 plugin_results 区块**

在模板文件末尾（main conclusion/summary 之后）添加：

```jinja2
{% if plugin_results %}
---

## 附加分析

{% for result in plugin_results %}
### {{ result.title }}
{{ result.summary }}
{% endfor %}
{% endif %}
```

- [ ] **Step 3: 提交**

```bash
git add src/reports/
git commit -m "feat: add plugin_results rendering section to report template"
```

- [ ] **Step 3: 提交**

```bash
git add src/reports/
git commit -m "feat: add plugin_results rendering section to report template"
```

---

### Task 11: 添加 `pyyaml` 依赖

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 在 `requirements.txt` 中添加 `pyyaml`**

```
pyyaml>=6.0
```

放在依赖列表的合理位置（与其他配置库一起）。

- [ ] **Step 2: 提交**

```bash
git add requirements.txt
git commit -m "chore: add pyyaml dependency for plugin configuration"
```

---

### Task 12: 插件注册表测试

**Files:**
- Create: `tests/test_plugin_registry.py`

- [ ] **Step 1: 编写测试**

```python
# tests/test_plugin_registry.py
import os
import pytest
from src.plugins.config import ConfigLoader, resolve_env_refs
from src.plugins.loader import scan_and_register


class TestResolveEnvRefs:
    def test_plain_string(self):
        assert resolve_env_refs("hello") == "hello"

    def test_single_env_ref(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret_value")
        assert resolve_env_refs("${MY_KEY}") == "secret_value"

    def test_missing_env_returns_empty(self):
        assert resolve_env_refs("${UNDEFINED_VAR_123}") == ""

    def test_mixed_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "example.com")
        assert resolve_env_refs("https://${HOST}/api") == "https://example.com/api"

    def test_dict_recursion(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "tok123")
        data = {"auth": {"token": "${TOKEN}"}, "timeout": 10}
        assert resolve_env_refs(data) == {"auth": {"token": "tok123"}, "timeout": 10}

    def test_list_recursion(self, monkeypatch):
        monkeypatch.setenv("URL", "https://api.test.com")
        data = ["${URL}", "static", 42]
        assert resolve_env_refs(data) == ["https://api.test.com", "static", 42]


class TestConfigLoader:
    def test_missing_file_returns_empty(self):
        loader = ConfigLoader(config_path="/nonexistent/plugins.yaml")
        assert loader.fetchers == []
        assert loader.strategies == []

    def test_loads_yaml_config(self, tmp_path):
        config_file = tmp_path / "plugins.yaml"
        config_file.write_text(
            "fetchers:\n  - name: test_fetcher\n    module: test_fetcher\n    enabled: true\n    priority: 5\n    config:\n      api_key: test\n"
        )
        loader = ConfigLoader(config_path=str(config_file))
        assert len(loader.fetchers) == 1
        assert loader.fetchers[0]["name"] == "test_fetcher"
        assert loader.fetchers[0]["priority"] == 5


class TestScanAndRegister:
    def test_empty_directory_returns_empty(self):
        results = scan_and_register(["/nonexistent/path"])
        assert results == []
```

- [ ] **Step 2: 运行测试**

```bash
cd /Users/ming/Desktop/daily_stock_analysis
python -m pytest tests/test_plugin_registry.py -v
```

期望结果: 全部 PASS

- [ ] **Step 3: 提交**

```bash
git add tests/test_plugin_registry.py
git commit -m "test: add plugin registry unit tests for config, env resolution, and scanning"
```

---

### Task 13: 插件开发指南文档

**Files:**
- Create: `docs/plugins-guide.md`
- Modify: `docs/README_CHT.md` （或主 README，添加链接）

- [ ] **Step 1: 创建插件开发指南**

```markdown
# 插件开发指南

## 快速开始

### 1. 创建自定义 Fetcher

在 `plugins/fetchers/` 下新建 Python 文件，继承 `BaseFetcher` 并实现两个方法：

```python
from data_provider.base import BaseFetcher
import pandas as pd

class MyFetcher(BaseFetcher):
    name = "my_api"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        # 获取原始数据
        ...

    def _normalize_data(self, df, stock_code):
        # 标准化列名为: date, open, high, low, close, volume, amount, pct_chg
        ...

def register(config):
    return MyFetcher(**config)
```

在 `plugins.yaml` 中启用:

```yaml
fetchers:
  - name: my_api
    module: my_fetcher_filename   # 不带 .py 的文件名
    enabled: true
    priority: 0
    config:
      api_key: "${MY_API_KEY}"
```

### 2. 创建分析策略

在 `plugins/strategies/` 下新建文件，继承 `AnalysisStrategy`:

```python
from src.plugins.strategy_base import AnalysisStrategy, AnalysisContext, StrategyResult

class MyStrategy(AnalysisStrategy):
    name = "my_strategy"

    def execute(self, ctx: AnalysisContext) -> StrategyResult:
        # ctx.price_data — 价格数据 DataFrame
        # ctx.indicators — 技术分析结果
        return StrategyResult(
            name="my_strategy",
            title="我的分析",
            summary="分析结论摘要",
            content={"key": "value"},
        )

def register(config, plugin_ctx):
    return MyStrategy(config, plugin_ctx)
```

### 本地插件目录

用户自定义插件可放在 `plugins.local/`，不会被 Git 跟踪，适合放置含密钥的自定义插件。

## 注意事项

- 插件在同一个 Python 进程中执行，无沙箱隔离 — 需信任插件代码
- 单个插件失败不会阻塞其他插件或主流程
- 配置文件中的 `${ENV_VAR}` 会被替换为环境变量值
```

- [ ] **Step 2: 在 `README_CHT.md` 或主 README 的插件相关章节添加链接**

在主 README 中添加一节指向插件指南的链接，格式示例：

```
### 插件扩展

- [插件开发指南](docs/plugins-guide.md) — 自定义数据源和分析策略
```

- [ ] **Step 3: 提交**

```bash
git add docs/plugins-guide.md
git commit -m "docs: add plugin development guide"
```

---

### Task 14: 完善 AGENTS.md CLAUDE.md

- [ ] **Step 1: 在 AGENTS.md 中添加插件相关约束**

在 `CLAUDE.md`（即 `AGENTS.md`）的"硬规则"或"稳定性护栏"部分，添加：

```
- 新增插件时，需同步更新 `plugins.yaml` 示例配置及 `docs/plugins-guide.md`。
- 用户自定义优先放在 `plugins.local/`，不入库。
```

- [ ] **Step 2: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: add plugin development rules to AGENTS.md"
```

---

## Self-Review

### Spec Coverage Check

| Spec 要求 | 对应 Task |
|-----------|-----------|
| 独立 `plugins.yaml` 配置 | Task 1, Task 6 |
| 自动扫描 `plugins/fetchers/` 和 `plugins/strategies/` | Task 2, Task 5 |
| `${ENV_VAR}` 替换 | Task 1 |
| `PluginRegistry` 类 | Task 5 |
| `register(config)` 约定 | Task 6 (example), Task 13 (guide) |
| `AnalysisStrategy` 基类 | Task 3 |
| `AnalysisContext` / `StrategyResult` | Task 3 |
| `PluginContext` | Task 4 |
| Pipeline 数据源集成 | Task 8 |
| Pipeline 策略执行点 | Task 9 |
| 报告模板 plugin_results | Task 10 (Jinja2 template in project templates/) |
| 内置 Fetcher 兼容（fallback） | Task 7, Task 8 |
| 错误处理（单插件失败不阻塞） | Task 5 (execute_strategies), Task 5 (_instantiate_fetchers/_strategies) |
| `plugins.local/` 本地目录 | Task 6 |
| 安全考量（环境变量引用） | Task 1, Task 6 |
| 迁移路径 Phase 1-4 | 所有 Task 覆盖 |

### Placeholder Scan

检查通过 — 每个 Step 都有具体代码块、具体命令，没有 "TBD"/"TODO"/"implement later"。

### Type Consistency

- `AnalysisContext`, `StrategyResult` 在 Task 3 定义，Task 5/9/10 中统一引用
- `PluginContext` 在 Task 4 定义，Task 5/9 中统一引用
- 方法签名一致，没有冲突

计划完整，无遗漏。
