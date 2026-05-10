# 四项可用性修复设计

## 概述

修复实跑验证发现的 4 个独立问题：dry-run 方向颠倒、回测日期比较型别错误+退出码、TraderAgent timeout 参数不匹配、新闻/宏观搜索静默退化。每个修复 1-2 行代码改动，测试覆盖。

---

### 修复 A：dry-run 执行顺序

**根因**：`src/core/pipeline.py:533-538` 把 `skip_analysis` 检查移到了 `prefetch_stock_data` 之前，导致 dry-run 模式不触发数据拉取。

**修复**：恢复原来的执行顺序——先拉数据（`prefetch_stock_data`），再检查 `skip_analysis` 决定是否跳过 AI 分析。

**文件**：`src/core/pipeline.py:533-538`

```python
# Step 1: 获取并缓存数据
success, error = await self.prefetch_stock_data(...)
if not success:
    logger.warning(...)
else:
    self._emit_progress(...)

# Step 2: dry-run 检查
if skip_analysis:
    logger.info("[%s] dry-run 模式：数据已缓存，跳过 AI 分析", code)
    return None

# Step 3: AI 分析
result = await self.analyze_stock(...)
```

**测试**：在 `tests/test_pipeline_core.py` 新增 `test_dry_run_calls_prefetch`，mock `prefetch_stock_data` + `analyze_stock`，验证 dry-run 时前者被调用、后者未被调用。

---

### 修复 B：回测日期类型不匹配 + 退出码

**根因**：`src/services/backtest_service.py:121` 将 parquet 读出的 `datetime64[us]` 列与 Python `datetime.date` 对象直接比较，pandas 报 `Invalid comparison`。同时 `src/core/runner.py:53` `run_backtest` 在 `service.run_backtest()` 后无条件 `return 0`。

**修复**：

B1. `backtest_service.py:121` 将 `start_date` 转 `pd.Timestamp` 再比较：
```python
# 改前
mask = df["trade_date"] >= start_date
# 改后
mask = df["trade_date"] >= pd.Timestamp(start_date)
```

B2. `runner.py:53` 改为 `service.run_backtest(code=backtest_code)` 不 catch，由 `run_with_cleanup` 统一处理退出码：
```python
# 改前
service.run_backtest(code=backtest_code)
return 0
# 改后
return service.run_backtest(code=backtest_code) # 0/1
```
同时检查 `service.run_backtest` 是否有失败时可捕获的异常路径。

**文件**：`src/services/backtest_service.py:121`, `src/core/runner.py:53`

**测试**：`tests/test_backtest_cli.py` 增加 `test_backtest_returns_nonzero_on_failure`，mock `BacktestService.run_backtest` 抛出异常 → 验证返回非 0。

---

### 修复 C：TraderAgent timeout 参数不匹配

**根因**：`src/agent/agents/trader_agent.py:44` 调用 `_call_litellm_async` 时传入 `timeout=30`，但 `AnalyzerCore._call_litellm_async` 签名（`src/analyzer/core.py:173`）不接收该参数。

**修复**：删除 `trader_agent.py:44` 的 `timeout=30` 参数。TraderAgent 不是时间敏感路径，不需要单独 timeout。

**文件**：`src/agent/agents/trader_agent.py:44`

```python
# 改前
response, model_used, usage = await self.analyzer._call_litellm_async(
    session, messages, tools=tools, timeout=30,
)
# 改后
response, model_used, usage = await self.analyzer._call_litellm_async(
    session, messages, tools=tools,
)
```

**测试**：`tests/test_trader_agent.py`（或新增）mock `_call_litellm_async`，验证调用时不传 `timeout`。

---

### 修复 D：搜索静默退化日志

**根因**：搜索服务内部 Finnhub/AkShare 等 provider 静默失败，下游拿到空结果时无法区分"没新闻"和"搜索降级"。

**修复**：在 `src/search_service.py` 的 `search` 方法（或 `aggregate_search`）末尾，当有效结果数量低于预期阈值时记录 `logger.warning`，说明哪些 provider 返回空。

具体位置：`src/search_service.py` 中 `async def search` 或 `get_search_results`，约在 `query_news` / `query_news_batch` 聚合完成后。

```python
# 在搜索结果聚合完成后
if not results:
    logger.warning("搜索返回空结果：所有 provider 均已降级或不可用")
elif len(results) < 3:
    logger.warning("搜索结果不足（%d条），部分 provider 返回空", len(results))
```

**不做**：不改变报告结构，不新增"搜索质量"状态行。

**文件**：`src/search_service.py`（搜索聚合处）

**测试**：mock provider 返回空 → 断言 `logger.warning` 被调用。

---

## 测试计划

| 测试 | 类型 | 验证点 |
|------|------|--------|
| `test_dry_run_calls_prefetch` | 新增 | dry-run → prefetch 被调用、analyze 未被调用 |
| `test_backtest_returns_nonzero_on_failure` | 新增 | backtest 异常 → 返回非 0 |
| `test_trader_agent_no_timeout` | 新增 | TraderAgent 调用 _call_litellm_async 不传 timeout |
| `test_search_degradation_logging` | 新增 | 搜索空结果 → logger.warning 被调用 |

## 涉及文件

**修改：**
- `src/core/pipeline.py` — dry-run 顺序修复
- `src/services/backtest_service.py` — 日期类型转换
- `src/core/runner.py` — backtest 返回码
- `src/agent/agents/trader_agent.py` — 删除 timeout
- `src/search_service.py` — 降级日志

**测试：**
- `tests/test_pipeline_core.py` — 新增 dry-run 测试
- `tests/test_backtest_cli.py` — 新增 backtest 退出码测试
- `tests/test_trader_agent.py` — 新增 timeout 测试
- `tests/test_search_service.py` — 新增降级测试
