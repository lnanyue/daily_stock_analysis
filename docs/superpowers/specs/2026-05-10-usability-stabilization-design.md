# 深层可用性问题修复设计

## 概述

修复 SQLite 移除后暴露的 5 个可用性断链，让 `--backtest`、`--dry-run`、断点续传、AGENT_AUTO_ROUTE、退出码行为恢复可工作状态。

## 范围

P0 × 3 + P1 × 2，五块独立修复，可分步合入。

---

### 块 A：`--backtest` CLI 参数断链

**根因**：`runner.py:49` → `service.run_backtest(backtest_code)` 传了位置参数，但 `BacktestService.run_backtest()` 签名是 `(*, code=None, ...)` keyword-only。

**修复**：`runner.py:49` 改为 `service.run_backtest(code=backtest_code)`。

**文件**：`src/core/runner.py`

**测试**：新增 `tests/test_backtest_cli.py`，mock BacktestService，验证 `run_backtest(code="600519")` 被调用。

---

### 块 B：`fetch_and_save_stock_data` 重命名 + parquet 文件缓存

**背景**：SQLite A组 移除后，数据只拉不存。`fetch_and_save_stock_data` 名不副实，`--dry-run` 和断点续传逻辑失效。

#### 文件缓存设计

```
~/.cache/stock-data/{code}.parquet
```

- 写入：`fetch_and_save_stock_data` 网络拉取成功 → 写入 parquet（含 date/close/volume 等核心字段 + 抓取时间戳）
- 读取：检查今天是否已抓 → 是则跳过网络；网络失败 → fallback 读缓存（不限新旧）
- 存储量：每只股票 45 行 × N 只股票，parquet 压缩后 < 1MB

#### 改动清单

| 文件 | 改动 |
|------|------|
| `src/core/pipeline.py:186` | `fetch_and_save_stock_data` → `prefetch_stock_data`，加入缓存检查+写入 |
| `src/core/pipeline.py:470-509` | `process_single_stock` docstring 去掉"保存数据"，dry-run 跳过 prefetch |
| 新增 `src/core/stock_cache.py` | parquet 缓存读写：`read_cache(code)`, `write_cache(code, df)`, `is_fresh(code)` |

#### 缓存接口

```python
# src/core/stock_cache.py
CACHE_DIR = Path.home() / ".cache" / "stock-data"

def is_fresh(code: str) -> bool:
    """今天已经缓存过该股票的日线数据？"""
    path = CACHE_DIR / f"{code}.parquet"
    if not path.exists():
        return False
    meta = _read_metadata(path)
    return meta.get("fetch_date") == date.today().isoformat()

def read_cache(code: str) -> Optional[pd.DataFrame]:
    """从 parquet 读缓存。"""
    ...

def write_cache(code: str, df: pd.DataFrame) -> None:
    """写入 parquet，附加抓取日期元数据。"""
    ...
```

---

### 块 C：`analysis_mode` 从 Collector 传到 Executor

**根因**：`pipeline.py:217` → `executor.analyze(code, report_type, query_id, collected)`，没有传 `analysis_mode`，Executor 默认 `"simple"`。`collected.analysis_mode` 已正确定义但不被使用。

**修复**：

```python
# pipeline.py:217
await self.executor.analyze(
    code, report_type, query_id, collected,
    analysis_mode=collected.analysis_mode,
)
```

**测试修复**：`test_debate_analysis_mode` 当前只断言 `result.success`，需改为断言 `DebateAnalyzer.analyze` 被调用。使用 `with patch.object(DebateAnalyzer, "analyze") as mock_analyze`。

---

### 块 D：全失败时返回非 0 退出码

**根因**：`runner.py:run_full_analysis` 最外层 `except` 只打日志不 propagate；`lifecycle.py:run_with_cleanup` 协程不抛出则永远 `return 0`。

**修复**：

```python
# runner.py: 在 run_full_analysis 末尾增加失败判定
if all_failed:
    raise RuntimeError("所有个股分析均失败")
```

```python
# lifecycle.py:93
async def run_with_cleanup(coro) -> int:
    try:
        await coro
        return 0
    except Exception:
        return 1
    finally:
        await cleanup()
```

**不做**：数据不足以分析时仍生成 partial report（当前设计，需要更大的设计讨论）。

---

### 块 E：AGENT_AUTO_ROUTE_ANALYSIS 配置孤岛处理

**根因**：自动分流逻辑在 `pipeline.py:277`，但生产走 `analyze_stock` → `executor.analyze` 绕过该路径。配置、文档、测试引用已存在但逻辑断线。

**处理方式**：
1. 标记 `agent_auto_route_analysis` 为 deprecated
2. 从用户可见文档中移除"复杂场景自动升级到 Agent"的承诺
3. 保留定义和读取（不破坏现有配置），加 logging.warning 提示已弃用

**不做**：不接回自动分流。当前路线图是稳定化，不在 scope 内。

---

## 测试计划

| 测试 | 类型 | 验证点 |
|------|------|--------|
| `test_backtest_cli` | 新增 | mock service，验证 CLI → `run_backtest(code=...)` |
| `test_parquet_cache_basic` | 新增 | write → read → 数据一致 |
| `test_parquet_cache_freshness` | 新增 | 今天写入 → is_fresh=True；昨天写入 → is_fresh=False |
| `test_parquet_cache_fallback` | 新增 | 网络失败 → 读缓存 |
| `test_debate_analysis_mode` | 改 | 断言 `DebateAnalyzer.analyze` 被 mock 调用 |
| `test_full_failure_exit_code` | 新增 | `run_with_cleanup` 抛异常 → return 1 |

## 涉及文件清单

**修改：**
- `src/core/runner.py` — backtest keyword + 全失败检查
- `src/core/pipeline.py` — fetch_and_save_stock_data 改名+缓存+analysis_mode传参
- `src/core/pipeline_executor.py` — 无改动（签名已有 `analysis_mode` 参数）
- `src/core/lifecycle.py` — run_with_cleanup 返回 1
- `src/config/manager.py` — agent_auto_route_analysis 标记 deprecated

**新增：**
- `src/core/stock_cache.py` — parquet 缓存读写

**测试：**
- `tests/test_backtest_cli.py` — 新增
- `tests/test_stock_cache.py` — 新增
- `tests/test_pipeline_split_integration.py` — 改 debate 测试
