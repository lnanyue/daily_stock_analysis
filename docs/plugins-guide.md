# 插件开发指南

## 快速开始

### 创建自定义 Fetcher

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

在 `plugins.yaml` 中启用：

```yaml
fetchers:
  - name: my_api
    module: my_fetcher_filename   # 不带 .py 的文件名
    enabled: true
    priority: 0
    config:
      api_key: "${MY_API_KEY}"
```

### 创建分析策略

在 `plugins/strategies/` 下新建文件，继承 `AnalysisStrategy`：

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
