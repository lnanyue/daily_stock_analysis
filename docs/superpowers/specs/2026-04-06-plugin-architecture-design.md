# 插件化架构设计

**日期**: 2026-04-06
**状态**: Draft
**范围**: 数据源和分析策略的可插拔插件化

## 1. 目标与约束

### 目标
- 添加新数据源只需创建 Python 文件 + 配置 `plugins.yaml`，无需修改核心代码
- 添加新分析策略同理，可插拔启用/禁用
- 保持现有 `BaseFetcher` 和 `DataFetcherManager` 基础设施的兼容性
- 插件失败不影响主流程（单个数据源失败自动降级，单个策略失败记录日志并跳过）

### 约束
- 插件在同一个 Python 进程中运行，直接 import，共享内存
- 使用独立的 `plugins.yaml` 配置文件管理启用/禁用/优先级/参数
- 采用轻量注册表模式，不引入复杂 hook 框架
- 现有六大 Fetcher（Akshare/Efinance/Tushare/Pytdx/Baostock/Yfinance）作为内置数据源保留

## 2. 目录结构

```
project-root/
├── plugins.yaml                        # 插件总配置
├── plugins/                            # 插件目录（Git 跟踪）
│   ├── fetchers/                       # 数据源插件
│   │   ├── __init__.py                # 自动发现入口
│   │   └── my_custom_api.py           # 用户自定义 Fetcher 示例
│   └── strategies/                     # 分析策略插件
│       ├── __init__.py                # 自动发现入口
│       └── fundamentals.py            # 基本面分析示例
└── plugins.local/                      # 用户本地插件（.gitignore）
    ├── fetchers/
    └── strategies/
```

用户自定义插件可放在 `plugins.local/`，不会被 Git 跟踪，避免提交密钥。

## 3. 配置格式

### `plugins.yaml`

```yaml
fetchers:
  - name: custom_api
    module: my_custom_api
    enabled: true
    priority: 0
    config:
      base_url: "https://api.example.com/v1"
      api_key: "${CUSTOM_API_KEY}"    # 支持环境变量引用
      timeout: 10

strategies:
  - name: fundamentals
    module: fundamentals
    enabled: true
    config:
      include_pe_ratio: true
      include_pb_ratio: true
  - name: sentiment
    module: sentiment
    enabled: false
    config: {}
```

- `enabled`: 是否启用此插件
- `priority`: 仅 fetchers 使用，数字越小越优先
- `config`: 传递给插件 `register()` 方法的参数字典
- `config` 中的 `${ENV_VAR}` 语法在加载时替换为环境变量值

## 4. 核心接口

### 4.1 数据源插件

继承 `BaseFetcher`，提供 `register(config: dict) -> BaseFetcher` 工厂函数：

```python
from data_provider.base import BaseFetcher

class MyCustomFetcher(BaseFetcher):
    name = "my_custom_api"
    
    def __init__(self, base_url: str, api_key: str, timeout: int = 10):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
    
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        ...
    
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        ...

def register(config: dict) -> BaseFetcher:
    return MyCustomFetcher(**config)
```

### 4.2 分析策略插件

实现 `AnalysisStrategy` 抽象基类：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class AnalysisContext:
    """传递给策略插件的上下文"""
    stock_code: str
    price_data: pd.DataFrame
    indicators: Dict[str, Any]
    search_results: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass
class StrategyResult:
    """策略返回结果"""
    name: str                       # 策略名称，用于报告渲染
    title: str                      # 报告中的标题（支持多语言）
    content: Dict[str, Any]         # 结构化数据
    summary: str                    # 人类可读摘要
    raw_data: Optional[Any] = None  # 原始数据（可选，供调试）

class AnalysisStrategy(ABC):
    name: str = "base_strategy"
    
    def __init__(self, config: Dict[str, Any], context: "PluginContext"):
        self.config = config
        self.ctx = context  # 可访问配置/数据库/搜索服务等
    
    @abstractmethod
    def execute(self, analysis_ctx: AnalysisContext) -> StrategyResult:
        """
        执行分析。
        异常应向上抛出，由 Pipeline 统一捕获并记录，不阻塞其他策略。
        """
        ...

def register(config: dict, plugin_ctx: "PluginContext") -> AnalysisStrategy:
    return MyStrategy(config=config, plugin_ctx=plugin_ctx)
```

### 4.3 `PluginContext`

提供给策略插件的内部服务访问：

```python
@dataclass
class PluginContext:
    """策略插件可访问的内部服务引用"""
    config: Config              # 系统配置
    db: Database                # 数据库连接
    search_service: SearchService
    fetcher_manager: DataFetcherManager
```

策略可通过 `self.ctx` 获取已有服务，无需重复初始化。

## 5. `PluginRegistry` 类

```python
class PluginRegistry:
    """统一插件注册表

    职责：
    1. 扫描 plugins/ 和 plugins.local/ 目录
    2. 解析 plugins.yaml 配置
    3. 实例化并注册启用的插件
    4. 提供按类型查询插件的接口
    """

    SEARCH_PATHS = ["plugins/", "plugins.local/"]

    def __init__(self, config_path: str = "plugins.yaml"):
        self.config_path = config_path
        self.fetchers: List[Tuple[str, callable]] = []   # (name, factory)
        self.strategies: List[Tuple[str, callable]] = [] # (name, factory)
        self._plugin_config: Dict = {}

    def load(self, plugin_ctx: PluginContext) -> None:
        """完整加载流程：扫描 + 解析配置 + 注册"""
        self._load_config()
        self._scan_directories()
        self._instantiate_fetchers()
        self._instantiate_strategies(plugin_ctx)

    def get_enabled_fetchers(self) -> List[BaseFetcher]:
        """返回已实例化的 Fetcher 列表，按 priority 排序"""

    def get_enabled_strategies(self) -> List[AnalysisStrategy]:
        """返回已实例化的 Strategy 列表"""

    def execute_strategies(self, analysis_ctx: AnalysisContext) -> List[StrategyResult]:
        """批量执行已启用的策略，单个失败不阻塞其他"""
```

### 加载流程

```
Pipeline 初始化
    ↓
PluginRegistry.load()
    ↓
1. 解析 plugins.yaml → _plugin_config
   - 替换 ${ENV_VAR} 引用
    ↓
2. 扫描 plugins/fetchers/ 和 plugins/strategies/
   - 对每个 .py 文件（跳过 __init__.py）：import 模块
   - 检查是否定义了 register() 函数
   - 注册到对应注册表
    ↓
3. 遍历 _plugin_config["fetchers"]
   - 如果 enabled: true，调用 factory(**config) 实例化
   - 按 priority 排序
    ↓
4. 遍历 _plugin_config["strategies"]
   - 如果 enabled: true，调用 factory(config, plugin_ctx) 实例化
```

## 6. Pipeline 集成

### 6.1 数据源集成

```python
class StockAnalysisPipeline:
    def __init__(self, ...):
        self.plugins = PluginRegistry()
        plugin_ctx = PluginContext(
            config=self.config,
            db=self.db,
            search_service=self.search_service,
            fetcher_manager=None,  # 稍后赋值
        )
        self.plugins.load(plugin_ctx)

        # 插件 fetchers 优先，内置 fetchers 作为 fallback
        plugin_fetchers = self.plugins.get_enabled_fetchers()
        self.fetcher_manager = DataFetcherManager(fetchers=plugin_fetchers)
        plugin_ctx.fetcher_manager = self.fetcher_manager
```

### 6.2 分析策略集成

在单个股票的分析流程中（约在 `analyze_single_stock` 方法内），数据获取和技术分析完成后：

```python
# 构建策略上下文
analysis_ctx = AnalysisContext(
    stock_code=canonical_code,
    price_data=df,
    indicators=trend_result,
    search_results=search_data,
)

# 执行策略插件
strategy_results = self.plugins.execute_strategies(analysis_ctx)

# 合并到最终传递给 LLM 的 report_context
report_context["plugin_results"] = strategy_results
```

### 6.3 报告模板集成

报告生成时，在 `plugin_results` 部分遍历渲染：

```
## 附加分析

### {strategy_result.title}
{strategy_result.summary}

（可选：根据 content 渲染表格/图表）
```

`title` 和 `summary` 使用插件返回的原始文本，不进行自动翻译。多语言由插件自身负责（如读取 `config.language` 返回对应语言文本）。

## 7. 内置数据源的兼容处理

现有 DataFetcherManager 在 `fetchers=None` 时自动创建内置 Fetchers（efinance、akshare、tushare 等），该行为默认保留。插件 fetchers 通过 `priority` 参数与内置 Fetchers 竞争优先级，无需修改内置 Fetchers 的初始化逻辑即可共存。

## 9. 错误处理与容错

| 场景 | 行为 |
|------|------|
| 插件模块 import 失败 | 记录 warning 日志，跳过该插件 |
| `register()` 执行失败 | 记录 error 日志，跳过该插件 |
| Fetcher 获取数据失败 | 按 DataFetcherManager 现有机制自动降级到下一个 |
| Strategy 执行失败 | 记录 error 日志，标记该策略结果为错误，继续执行下一个策略 |
| plugins.yaml 格式错误 | 记录 error 日志，回退到仅使用内置 fetchers |
| 插件引用未配置的环境变量 | 抛出加载错误，明确提示缺少的变量名 |

## 10. 安全考量

- 插件代码在同一个 Python 进程中执行，无沙箱隔离 —— 需信任插件代码
- 敏感配置通过 `${ENV_VAR}` 引用，不直接写入 `plugins.yaml`
- 用户自定义插件放在 `plugins.local/`（gitignored），避免密钥入库
- 不自动执行插件中的任何系统命令或网络请求（除 Fetcher 明确的数据获取外）

## 11. 迁移路径

### Phase 1: 基础设施（插件注册表 + 配置解析）

- 实现 `PluginRegistry`、`plugins.yaml` 解析、`${ENV_VAR}` 替换
- 扫描 `plugins/` 和 `plugins.local/`
- 不动现有 Fetcher/Strategy 代码

### Phase 2: 数据源插件化

- 将现有 `plugins/fetchers/__init__.py` 自动发现机制接入
- 提供示例 Fetcher 模板
- `DataFetcherManager.from_plugin_registry()` 新方法
- 内置 fetchers 通过默认配置自动生成

### Phase 3: 分析策略插件化

- 实现 `AnalysisStrategy` 基类 + `PluginContext`
- Pipeline 中新增策略执行入口
- 报告模板新增 `plugin_results` 区块
- 提供示例 Strategy 模板

### Phase 4: 文档与清理

- 更新 `README.md` 新增"自定义插件"章节
- 更新 `docs/` 新增插件开发指南
- 清理冗余的硬编码注册逻辑（可选）
