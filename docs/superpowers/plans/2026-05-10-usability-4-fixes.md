# 四项可用性修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 dry-run 方向、回测日期比较+退出码、TraderAgent timeout、搜索静默降级 4 个问题

**Architecture:** 四项独立修复，每项 1-2 行代码改动 + 对应测试，可并行或顺序执行

**Tech Stack:** Python, pandas (Timestamp), pytest

---

### Task A: dry-run 修复 — 先拉数据后检查

**Files:**
- Modify: `src/core/pipeline.py:536-542`
- Test: `tests/test_pipeline_core.py`

- [ ] **Step 1: 写测试**

在 `tests/test_pipeline_core.py` 类 `TestStockAnalysisPipeline` 末尾新增：

```python
async def test_dry_run_calls_prefetch(self):
    """dry-run 模式调 prefetch_stock_data 但跳过 analyze_stock。"""
    pl = StockAnalysisPipeline(config=self.mock_config)
    pl.prefetch_stock_data = AsyncMock(return_value=(True, None))
    pl.analyze_stock = AsyncMock(return_value=AnalysisResult(
        code="600519", name="测试股票", sentiment_score=80,
        trend_prediction="看多", operation_advice="买入",
        analysis_summary="测试", success=True,
    ))

    result = await pl.process_single_stock("600519", skip_analysis=True)

    self.assertIsNone(result)
    pl.prefetch_stock_data.assert_awaited_once()
    pl.analyze_stock.assert_not_awaited()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest tests/test_pipeline_core.py::TestStockAnalysisPipeline::test_dry_run_calls_prefetch -v
```

Expected: FAIL — 当前代码在 prefetch 前就 return None

- [ ] **Step 3: 修改 pipeline.py**

把 `skip_analysis` 检查从 prefetch 之前移到 prefetch 之后：

```python
# src/core/pipeline.py:535-554
try:
    self._emit_progress(12, f"{code}：正在准备分析任务")
    # Step 1: 获取并缓存数据
    success, error = await self.prefetch_stock_data(
        code, current_time=current_time
    )

    if not success:
        logger.warning(f"[{code}] 数据获取失败: {error}")
    else:
        self._emit_progress(16, f"{code}：行情数据准备完成")

    # dry-run 检查（在 prefetch 之后、analyze 之前）
    if skip_analysis:
        logger.info("[%s] dry-run 模式：数据已缓存，跳过 AI 分析", code)
        return None

    # Step 2: AI 分析
    effective_query_id = analysis_query_id or self.query_id or uuid.uuid4().hex
    result = await self.analyze_stock(code, report_type, query_id=effective_query_id)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest tests/test_pipeline_core.py::TestStockAnalysisPipeline::test_dry_run_calls_prefetch -v
```

Expected: PASS

- [ ] **Step 5: 运行全量 pipeline 测试验证无回归**

```bash
python3 -m pytest tests/test_pipeline_core.py -v
```

Expected: 8 passed (含新增 test)

- [ ] **Step 6: Commit**

```bash
git add src/core/pipeline.py tests/test_pipeline_core.py
git commit -m "fix: dry-run should prefetch data before skipping analysis"
```

---

### Task B: 回测日期类型不匹配 + 退出码

**Files:**
- Modify: `src/services/backtest_service.py:121`
- Modify: `src/core/runner.py:53-54`
- Test: `tests/test_backtest_cli.py`

- [ ] **Step 1: 写测试**

在 `tests/test_backtest_cli.py` 末尾新增：

```python
def test_backtest_returns_nonzero_on_failure(self):
    """backtest 内部异常时 run_backtest 返回非 0。"""
    from src.core.runner import run_backtest
    from unittest.mock import patch, MagicMock

    with patch("src.core.runner.BacktestService") as mock_cls:
        mock_service = MagicMock()
        mock_service.run_backtest.side_effect = RuntimeError("backtest failed")
        mock_cls.return_value = mock_service

        result = run_backtest(backtest_code="600519")
        self.assertNotEqual(result, 0)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest tests/test_backtest_cli.py::TestBacktestCli::test_backtest_returns_nonzero_on_failure -v
```

Expected: FAIL — runner.py 无条件 return 0

- [ ] **Step 3: 修复 backtest_service.py 日期比较**

```python
# src/services/backtest_service.py:121
# 改前:
mask = df[date_col] <= analysis_date
# 改后:
import pandas as pd
mask = df[date_col] <= pd.Timestamp(analysis_date)
```

- [ ] **Step 4: 修复 runner.py 退出码**

```python
# src/core/runner.py:49-54
def run_backtest(backtest_code: Optional[str] = None) -> int:
    """回测模式。"""
    from src.services.backtest_service import BacktestService
    service = BacktestService()
    try:
        service.run_backtest(code=backtest_code)
        return 0
    except Exception as e:
        logger.error("回测失败: %s", e)
        return 1
```

需要加 import: `logger = logging.getLogger(__name__)` 已在文件顶部。

- [ ] **Step 5: 运行测试确认通过**

```bash
python3 -m pytest tests/test_backtest_cli.py -v
```

Expected: both PASS

- [ ] **Step 6: Commit**

```bash
git add src/services/backtest_service.py src/core/runner.py tests/test_backtest_cli.py
git commit -m "fix: backtest date dtype comparison and non-zero exit code"
```

---

### Task C: TraderAgent timeout 参数删除

**Files:**
- Modify: `src/agent/agents/trader_agent.py:48`
- Test: `tests/test_trader_agent.py` (新建)

- [ ] **Step 1: 写测试**

```python
# tests/test_trader_agent.py
"""Tests for TraderAgent — LLM call parameter correctness."""
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch


class TestTraderAgentCallLiteLlm(TestCase):
    """TraderAgent._call_litellm_async 不传 timeout。"""

    @patch("src.agent.agents.trader_agent.TraderAgent._post_process")
    def test_run_does_not_pass_timeout(self, mock_post_process):
        from src.agent.agents.trader_agent import TraderAgent
        from src.agent.schemas import AgentContext

        agent = TraderAgent.__new__(TraderAgent)
        agent.analyzer = MagicMock()
        agent.analyzer._call_litellm_async = AsyncMock(
            return_value=("result", "model", {})
        )
        mock_post_process.return_value = MagicMock()

        ctx = AgentContext(code="600519", name="test")
        import asyncio
        result = asyncio.run(agent.run(ctx, timeout_seconds=None))

        # Verify _call_litellm_async was called WITHOUT timeout kwarg
        call_kwargs = agent.analyzer._call_litellm_async.call_args.kwargs
        self.assertNotIn("timeout", call_kwargs)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest tests/test_trader_agent.py -v
```

Expected: FAIL — 当前传 `timeout=timeout_seconds` (即使 None)

- [ ] **Step 3: 修改 trader_agent.py**

只传三个位置参数，不传 timeout：

```python
# src/agent/agents/trader_agent.py:44-49
response_text, model_used, _ = await self.analyzer._call_litellm_async(
    user_message,
    {"max_tokens": 2048, "temperature": 0.3},
    system_prompt=system_prompt,
)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest tests/test_trader_agent.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/agents/trader_agent.py tests/test_trader_agent.py
git commit -m "fix: remove timeout kwarg from TraderAgent _call_litellm_async call"
```

---

### Task D: 搜索静默退化日志

**Files:**
- Modify: `src/search/service.py`
- Test: `tests/test_search_service.py` (追加)

- [ ] **Step 1: 写测试**

在 `tests/test_search_service.py` 末尾追加（或新建）：

```python
"""Tests for search degradation logging."""
import logging
from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestSearchDegradationLogging(TestCase):
    """搜索返回空结果时记录 warning。"""

    @patch("src.search.service.logger")
    def test_warning_when_all_providers_return_empty(self, mock_logger):
        from src.search.service import SearchService
        service = SearchService.__new__(SearchService)
        service._providers = []

        # Call a sync search path
        from src.search.types import SearchResponse
        response = SearchResponse(results=[])

        # Simulate: call search_stock_news with empty providers
        service._get_cached = MagicMock(return_value=None)
        service._cache_result = MagicMock()
        service.search_stock_news("600519", "茅台")

        # The search path with no providers should log empty
```

Wait, this test is getting complicated — `search_stock_news` is a synchronous wrapper that calls providers. Let me simplify: just verify the existing code path logs when results are empty. The right place for the warning is inside each search method after provider iteration.

Actually, let me simplify the approach. The simplest fix is to add a warning at the end of `search_stock_news_async` and `search_macro_news_async` after the provider loop, if `had_provider_success` is False.

Let me read the end of the provider loop in search_stock_news_async.

- [ ] **Step 1: Read context**

```bash
grep -n "had_provider_success\|return\|SearchResponse" src/search/service.py | head -20
```

- [ ] **Step 2: 写测试**

```python
# 追加到 tests/test_search_service.py
class TestSearchDegradation(TestCase):
    @patch("src.search.service.logger")
    def test_warning_when_no_provider_success(self, mock_logger):
        from src.search.service import SearchService
        service = SearchService.__new__(SearchService)
        service._providers = []
        service._get_cached = MagicMock(return_value=None)
        service._cache_result = MagicMock()

        from src.search.types import SearchResponse
        service.search_stock_news("000000", "测试")
        # With no providers, all searches return empty -> warning logged
```

Hmm, this is getting complicated. Let me simplify the approach significantly. Instead of trying to test at the provider level, I'll add the warning in the simplest way possible — right at the aggregation layer.

Actually, looking again at the code, `search_stock_news` is a sync wrapper that calls `search_stock_news_async` in a new event loop. The core fix should be in the async methods. Let me just add a simple warning check.

Let me simplify the plan and skip the overly complex test approach. Just add the warning after the provider loop.

- [ ] **Step 1: 写测试（简化版）**

```python
# 追加到 tests/test_search_service.py
"""Tests for search degradation logging."""
import logging
from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestSearchDegradationLogging(TestCase):
    """搜索返回空结果时记录 warning。"""

    @patch("src.search.service.logger")
    def test_degradation_logged_when_results_empty(self, mock_logger):
        from src.search.service import SearchService
        service = SearchService.__new__(SearchService)
        service._get_cached = MagicMock(return_value=None)
        service._cache_result = MagicMock()
        service._providers = []
        service._effective_news_window_days = MagicMock(return_value=7)
        service._provider_request_size = MagicMock(return_value=5)
        service._is_foreign_stock = MagicMock(return_value=False)
        service._build_stock_search_query = MagicMock(return_value="test query")
        service.search_stock_news("000000", "测试")
        # No providers -> empty results -> warning logged
```

This is still getting complex. Let me take a different approach that's simpler and more robust. I'll add a search-wide warning in a single place. The key insight from the user's report is that when all providers fail or return empty, the end result is an empty SearchResponse.

Let me just add the logging right at the end of each high-level search method. A clean approach:

```python
# In search_stock_news_async, after the provider loop, before returning:
if not had_provider_success:
    logger.warning("[%s] 所有搜索 provider 均返回空", stock_code)
```

And similarly for `search_macro_news_async`.

For the test, I'll create a proxy through a method that we can easily mock.

Actually, let me just simplify everything. The test can be straightforward:

```python
def test_search_empty_results_logs_warning():
    from src.search.service import SearchService
    service = SearchService.__new__(SearchService)
    service._get_cached = MagicMock(return_value=None)
    service._cache_result = MagicMock()
    service._providers = [MagicMock()]
    service._providers[0].search_news = MagicMock(return_value=[])  # returns nothing
    ...
```

OK this is way too deep for the plan. Let me just write a practical plan and move on. The key changes are:

1. pipeline.py: move skip_analysis check
2. backtest_service.py: pd.Timestamp
3. runner.py: try/except return 1
4. trader_agent.py: remove timeout kwarg
5. search/service.py: add warning log

Let me write the complete plan now.<｜end▁of▁thinking｜>

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="Write">
<｜｜DSML｜｜parameter name="content" string="true"># 四项可用性修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this task task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复实跑验证发现的 4 个独立可用性问题

**Architecture:** 四项独立修复，各 1-2 行代码改动，可并行。每项包含测试，改动后回归检查。

**Tech Stack:** Python, pandas (Timestamp), pytest

---

### Task A: dry-run 修复 — 先拉数据后检查跳过

**Files:**
- Modify: `src/core/pipeline.py:536-542`
- Test: `tests/test_pipeline_core.py`

- [ ] **Step 1: 写测试**

在 `tests/test_pipeline_core.py` 末尾新增：

```python
async def test_dry_run_calls_prefetch(self):
    """dry-run 模式调 prefetch_stock_data 但跳过 analyze_stock。"""
    pl = StockAnalysisPipeline(config=self.mock_config)
    pl.prefetch_stock_data = AsyncMock(return_value=(True, None))
    pl.analyze_stock = AsyncMock(return_value=AnalysisResult(
        code="600519", name="测试股票", sentiment_score=80,
        trend_prediction="看多", operation_advice="买入",
        analysis_summary="测试", success=True,
    ))

    result = await pl.process_single_stock("600519", skip_analysis=True)

    self.assertIsNone(result)
    pl.prefetch_stock_data.assert_awaited_once()
    pl.analyze_stock.assert_not_awaited()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_pipeline_core.py::TestStockAnalysisPipeline::test_dry_run_calls_prefetch -v`
Expected: FAIL — 当前在 prefetch 前就 return None

- [ ] **Step 3: 修改 pipeline.py**

把 `skip_analysis` 检查从 prefetch 之前移到 prefetch 之后、analyze 之前：

```python
# src/core/pipeline.py:535-554
try:
    self._emit_progress(12, f"{code}：正在准备分析任务")
    # Step 1: 获取并缓存数据
    success, error = await self.prefetch_stock_data(
        code, current_time=current_time
    )

    if not success:
        logger.warning(f"[{code}] 数据获取失败: {error}")
    else:
        self._emit_progress(16, f"{code}：行情数据准备完成")

    # dry-run: 数据已拉，跳过 AI 分析
    if skip_analysis:
        logger.info("[%s] dry-run 模式：数据已缓存，跳过 AI 分析", code)
        return None

    # Step 2: AI 分析
    effective_query_id = analysis_query_id or self.query_id or uuid.uuid4().hex
    result = await self.analyze_stock(code, report_type, query_id=effective_query_id)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_pipeline_core.py::TestStockAnalysisPipeline::test_dry_run_calls_prefetch -v`
Expected: PASS

- [ ] **Step 5: 运行全量 pipeline 测试**

Run: `python3 -m pytest tests/test_pipeline_core.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add src/core/pipeline.py tests/test_pipeline_core.py
git commit -m "fix: dry-run should prefetch data before skipping analysis"
```

---

### Task B: 回测日期类型不匹配 + 退出码

**Files:**
- Modify: `src/services/backtest_service.py:121`
- Modify: `src/core/runner.py:49-54`
- Test: `tests/test_backtest_cli.py`

- [ ] **Step 1: 写测试**

在 `tests/test_backtest_cli.py` 末尾新增：

```python
def test_backtest_returns_nonzero_on_failure(self):
    """backtest 内部异常时 run_backtest 返回非 0。"""
    from src.core.runner import run_backtest

    with patch("src.core.runner.BacktestService") as mock_cls:
        mock_service = MagicMock()
        mock_service.run_backtest.side_effect = RuntimeError("backtest failed")
        mock_cls.return_value = mock_service

        result = run_backtest(backtest_code="600519")
        self.assertNotEqual(result, 0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_backtest_cli.py::TestBacktestCli::test_backtest_returns_nonzero_on_failure -v`
Expected: FAIL — runner.py 无条件 return 0

- [ ] **Step 3: 修复 backtest_service.py 日期比较**

```python
# src/services/backtest_service.py:121
# 改前:
mask = df[date_col] <= analysis_date
# 改后:
import pandas as pd
mask = df[date_col] <= pd.Timestamp(analysis_date)
```

- [ ] **Step 4: 修复 runner.py 退出码**

```python
# src/core/runner.py:49-54
def run_backtest(backtest_code: Optional[str] = None) -> int:
    """回测模式。"""
    from src.services.backtest_service import BacktestService
    service = BacktestService()
    try:
        service.run_backtest(code=backtest_code)
        return 0
    except Exception as e:
        logger.error("回测失败: %s", e)
        return 1
```

`logger` 已在文件顶部定义。

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest tests/test_backtest_cli.py -v`
Expected: both PASS

- [ ] **Step 6: Commit**

```bash
git add src/services/backtest_service.py src/core/runner.py tests/test_backtest_cli.py
git commit -m "fix: backtest date dtype comparison and non-zero exit code"
```

---

### Task C: TraderAgent timeout 参数删除

**Files:**
- Modify: `src/agent/agents/trader_agent.py:44-49`
- Create: `tests/test_trader_agent.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_trader_agent.py
"""Tests for TraderAgent — LLM call parameter correctness."""
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock


class TestTraderAgentCallLiteLlm(TestCase):
    """TraderAgent._call_litellm_async 不传 timeout。"""

    def test_run_does_not_pass_timeout(self):
        from src.agent.agents.trader_agent import TraderAgent
        from src.agent.schemas import AgentContext

        agent = TraderAgent.__new__(TraderAgent)
        agent.analyzer = MagicMock()
        agent.analyzer._call_litellm_async = AsyncMock(
            return_value=("result", "model", {}),
        )
        agent._post_process = MagicMock(return_value=MagicMock())

        ctx = AgentContext(code="600519", name="test")
        import asyncio
        asyncio.run(agent.run(ctx, timeout_seconds=None))

        call_kwargs = agent.analyzer._call_litellm_async.call_args.kwargs
        self.assertNotIn("timeout", call_kwargs)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_trader_agent.py -v`
Expected: FAIL — 当前传 timeout=timeout_seconds

- [ ] **Step 3: 修改 trader_agent.py**

删掉 timeout kwarg：

```python
# src/agent/agents/trader_agent.py:44-49
response_text, model_used, _ = await self.analyzer._call_litellm_async(
    user_message,
    {"max_tokens": 2048, "temperature": 0.3},
    system_prompt=system_prompt,
)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_trader_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/agents/trader_agent.py tests/test_trader_agent.py
git commit -m "fix: remove timeout kwarg from TraderAgent _call_litellm_async call"
```

---

### Task D: 搜索静默退化日志

**Files:**
- Modify: `src/search/service.py`
- Test: `tests/test_search_service.py`

- [ ] **Step 1: 确认插入位置**

Read `src/search/service.py` 中 `search_stock_news_async` 方法的 provider 循环结尾，`had_provider_success` 变量出现在约第 518 行。搜索 `had_provider_success` 确认：

Run: `grep -n "had_provider_success" src/search/service.py`

- [ ] **Step 2: 写测试**

```python
# 追加到 tests/test_search_service.py
"""Tests for search degradation logging."""
from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestSearchDegradationLogging(TestCase):
    """搜索返回空结果时记录 warning。"""

    @patch("src.search.service.logger")
    def test_degradation_logged_when_no_provider_success(self, mock_logger):
        from src.search.service import SearchService
        service = SearchService.__new__(SearchService)
        service._get_cached = MagicMock(return_value=None)
        service._cache_result = MagicMock()
        service._providers = []
        service._effective_news_window_days = MagicMock(return_value=7)
        service._provider_request_size = MagicMock(return_value=5)
        service._is_foreign_stock = MagicMock(return_value=False)
        service._build_stock_search_query = MagicMock(return_value="test query")

        from src.search.types import SearchResponse
        result = service.search_stock_news("000000", "测试")

        mock_logger.warning.assert_called()
        args, _ = mock_logger.warning.call_args
        self.assertIn("所有", str(args))
```

Note: `_build_stock_search_query` may not exist — if not, replace with patching the `query` construction block inside `search_stock_news_async`. The goal is to create a service with zero providers that produces an empty result, triggering the warning.

- [ ] **Step 3: 运行测试确认失败**

Run: `python3 -m pytest tests/test_search_service.py -v -k test_degradation`
Expected: FAIL — warning 尚未添加

- [ ] **Step 4: 在 search_stock_news_async 末尾加 warning**

```python
# src/search/service.py — 在 search_stock_news_async 的 provider 循环结束、cache 写入后、return 前
if not had_provider_success:
    logger.warning("[%s] 所有搜索 provider 均返回空", stock_code)
```

- [ ] **Step 5: 在 search_macro_news_async 末尾加同样 pattern**

搜索 `had_provider_success` 出现位置，对 macro 搜索也加上：

```python
if not had_provider_success:
    logger.warning("[%s] 宏观新闻搜索 provider 均返回空", stock_code)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest tests/test_search_service.py -v -k test_degradation`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/search/service.py tests/test_search_service.py
git commit -m "fix: log warning when all search providers return empty"
```

---

## 执行顺序

推荐按 A → B → C → D 顺序执行。四项独立无依赖，每项可单独提交、单独回滚。
