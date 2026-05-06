# 全景化：大盘温度与板块排行注入

## 摘要

在个股分析 prompt 顶部注入当日大盘主要指数表现和行业板块涨跌榜，让 LLM 在分析个股时能感知宏观环境，避免"覆巢之下无完卵"时给出盲目的买入建议。

## 改动范围

涉及 pipeline 层（数据获取 + 缓存）和 prompt 构建层（渲染），不涉及 DB schema、配置或 API。

## 数据来源

`DataFetcherManager.get_main_indices(region="cn")` 和 `DataFetcherManager.get_sector_rankings(n=5)`。

### 返回格式

**get_main_indices** → `List[Dict]`:

```python
[
    {"name": "上证指数", "current": 3100.0, "change_pct": 0.5, "volume": ..., "amount": ...},
    {"name": "深证成指", ...},
    {"name": "创业板指", ...},
    {"name": "科创50", ...},
    {"name": "上证50", ...},
    {"name": "沪深300", ...},
]
```

**get_sector_rankings** → `Tuple[List[Dict], List[Dict]]`:

```python
(
    [{"name": "半导体", "change_pct": 3.2}, {"name": "通信", "change_pct": 2.8}, ...],  # 领涨前5
    [{"name": "房地产", "change_pct": -2.1}, ...],  # 领跌前5
)
```

## 设计

### 缓存层

`pipeline.py` 新增 `_cached_market_overview: Optional[Dict]` 实例变量（init 中初始化为 None）。

新增 `_fetch_market_overview(region: str = "cn")` 方法：
- 检查 `self._cached_market_overview`
- 未缓存时：调用 `self.fetcher_manager.get_main_indices(region)` 和 `self.fetcher_manager.get_sector_rankings()`
- 组装为 `{"indices": [...], "sectors": {"top": [...], "bottom": [...]}}`
- 写入缓存后返回
- 任一接口失败时，返回仅包含成功部分的字典（不阻断流程）

### 注入位置

`_analyze_with_agent()` 在调用 `_enhance_context()` 之前调用 `_fetch_market_overview()`：

```python
market_overview = await self._fetch_market_overview()
enhanced_context = self._enhance_context(
    base_context, ..., market_overview=market_overview,
)
```

`_enhance_context()` → `enhance_analysis_context()` 将 `market_overview` 写入 `context["market_overview"]`。

### Prompt 渲染

`format_analysis_prompt()` 在信号摘要表之后（或顶部区域）增加：

```
## 📊 市场全景

### 主要指数
| 指数 | 最新价 | 涨跌幅 |
|------|--------|--------|
| 上证指数 | 3100.00 | +0.50% |
| 深证成指 | ... | ... |
| ... | ... | ... |

### 板块热点
领涨：半导体(+3.2%) 通信(+2.8%) ...
领跌：房地产(-2.1%) ...
```

### 错误处理

- `get_main_indices` 失败 → `indices` 展示 "获取失败"
- `get_sector_rankings` 失败 → 跳过板块排行段落
- 两者都失败时，不渲染市场全景段落
- 缓存确保多只股票分析时只请求一次

## 涉及文件

| 文件 | 改动 |
|------|------|
| `src/core/pipeline.py` | 新增 `_cached_market_overview`、`_fetch_market_overview()`，修改 `_analyze_with_agent()` |
| `src/analyzer/prompt_builder.py` | `format_analysis_prompt()` 新增市场全景渲染段 |

## 测试

- market_overview 存在时，prompt 包含「市场全景」标题
- market_overview 为 None 时，跳过
- 缓存逻辑：两次调用返回同一对象
