# 数据可信度与产品一致性治理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将审查发现的 7 项数据/产品问题系统性收口，第一期聚焦"分析结果可信任"，第二期聚焦"产品一致性"。

**Architecture:** 第一阶段复用同步版的维度构建逻辑消除异步版缺口，强制 freshness 过滤，硬化报告闸门。第二阶段清理文档漂移、补齐证据字段、配置能力分级。

**Tech Stack:** Python dataclasses, asyncio, pytest, YAML

---

### Task 1: 提取共享维度构建方法，异步版复用

**Files:**
- Modify: `src/search/service.py:583-647`（异步版维度构建）, `src/search/service.py:812-941`（同步版维度构建）

**Goal:** 将同步版 8 维度定义提取为 `_build_intel_dimensions()`，删除异步版中独立的 5 维度硬编码。

- [ ] **Step 1: 添加提取方法并更新同步版**

在 `src/search/service.py` 中 `search_comprehensive_intel_async` 方法前，插入共享的 `_build_intel_dimensions` 静态方法：

```python
@staticmethod
def _build_intel_dimensions(
    stock_code: str,
    stock_name: str,
    is_foreign: bool,
    is_index_etf: bool,
) -> list[dict[str, Any]]:
    """Build the unified intelligence dimension definitions.

    Used by both sync (search_comprehensive_intel) and async
    (search_comprehensive_intel_async) to keep dimensions in sync.
    """
    if is_foreign:
        return [
            {
                'name': 'latest_news',
                'query': f"{stock_name} {stock_code} latest news events",
                'desc': '最新消息',
                'tavily_topic': 'news',
                'strict_freshness': True,
            },
            {
                'name': 'market_analysis',
                'query': f"{stock_name} analyst rating target price report",
                'desc': '机构分析',
                'tavily_topic': None,
                'strict_freshness': False,
            },
            {
                'name': 'risk_check',
                'query': (
                    f"{stock_name} {stock_code} index performance outlook tracking error"
                    if is_index_etf else f"{stock_name} risk insider selling lawsuit litigation"
                ),
                'desc': '风险排查',
                'tavily_topic': None if is_index_etf else 'news',
                'strict_freshness': not is_index_etf,
            },
            {
                'name': 'macro_news',
                'query': SearchService._build_macro_news_query(stock_code, stock_name),
                'desc': '宏观新闻',
                'tavily_topic': 'news',
                'strict_freshness': True,
            },
            {
                'name': 'earnings',
                'query': (
                    f"{stock_name} {stock_code} index performance composition outlook"
                    if is_index_etf else f"{stock_name} earnings revenue profit growth forecast"
                ),
                'desc': '业绩预期',
                'tavily_topic': None,
                'strict_freshness': False,
            },
            {
                'name': 'industry',
                'query': (
                    f"{stock_name} {stock_code} index sector allocation holdings"
                    if is_index_etf else f"{stock_name} industry competitors market share outlook"
                ),
                'desc': '行业分析',
                'tavily_topic': None,
                'strict_freshness': False,
            },
        ]
    return [
        {
            'name': 'latest_news',
            'query': f"{stock_name} {stock_code} 最新 新闻 重大 事件",
            'desc': '最新消息',
            'tavily_topic': 'news',
            'strict_freshness': True,
        },
        {
            'name': 'market_analysis',
            'query': f"{stock_name} 研报 目标价 评级 深度分析",
            'desc': '机构分析',
            'tavily_topic': None,
            'strict_freshness': False,
        },
        {
            'name': 'risk_check',
            'query': (
                f"{stock_name} 指数走势 跟踪误差 净值 表现"
                if is_index_etf else f"{stock_name} 减持 处罚 违规 诉讼 利空 风险"
            ),
            'desc': '风险排查',
            'tavily_topic': None if is_index_etf else 'news',
            'strict_freshness': not is_index_etf,
        },
        {
            'name': 'announcements',
            'query': (
                f"{stock_name} {stock_code} 公告 指数调整 成分变化"
                if is_index_etf else f"{stock_name} {stock_code} 公司公告 重要公告 上交所 深交所 cninfo"
            ),
            'desc': '公司公告',
            'tavily_topic': 'news',
            'strict_freshness': True,
        },
        {
            'name': 'macro_news',
            'query': SearchService._build_macro_news_query(stock_code, stock_name),
            'desc': '宏观新闻',
            'tavily_topic': 'news',
            'strict_freshness': True,
        },
        {
            'name': 'earnings',
            'query': (
                f"{stock_name} 指数成分 净值 跟踪表现"
                if is_index_etf else f"{stock_name} 业绩预告 财报 营收 净利润 同比增长"
            ),
            'desc': '业绩预期',
            'tavily_topic': None,
            'strict_freshness': False,
        },
        {
            'name': 'industry',
            'query': (
                f"{stock_name} 指数成分股 行业配置 权重"
                if is_index_etf else f"{stock_name} 所在行业 竞争对手 市场份额 行业前景"
            ),
            'desc': '行业分析',
            'tavily_topic': None,
            'strict_freshness': False,
        },
    ]
```

- [ ] **Step 2: 替换同步版 `search_comprehensive_intel` 内的维度定义**

找到 `def search_comprehensive_intel(self, ...)` 方法中 `if is_foreign:` 开始的维度构建块（约第 824-941 行），替换为调用共享方法：

```python
def search_comprehensive_intel(
    self,
    stock_code: str,
    stock_name: str,
    max_searches: int = 3
) -> Dict[str, SearchResponse]:
    results = {}
    search_count = 0

    is_foreign = self._is_foreign_stock(stock_code)
    is_index_etf = self.is_index_or_etf(stock_code, stock_name)

    search_dimensions = self._build_intel_dimensions(
        stock_code, stock_name, is_foreign, is_index_etf
    )
    # 限制维度数量
    search_dimensions = search_dimensions[:max_searches]

    for dim in search_dimensions:
        if search_count >= max_searches:
            break
        # ... 后续搜索逻辑保持不变
```

注意：同步版后续还有 `for dim in search_dimensions:` 循环和 `_handle_dimension_search_errors` 调用，需要保留（用新的 `search_dimensions` 变量）。原代码中维度定义后的搜索循环保持不变。

- [ ] **Step 3: 替换异步版 `search_comprehensive_intel_async` 内的维度定义**

找到 `async def search_comprehensive_intel_async(self, ...)` 方法中硬编码的 5 维度列表（约第 592-623 行），替换为：

```python
async def search_comprehensive_intel_async(
    self,
    stock_code: str,
    stock_name: str,
    max_searches: int = 3
) -> Dict[str, SearchResponse]:
    """并发执行多维度的异步深度情报搜索"""
    is_index_etf = self.is_index_or_etf(stock_code, stock_name)
    is_foreign = self._is_foreign_stock(stock_code)

    search_dimensions = self._build_intel_dimensions(
        stock_code, stock_name, is_foreign, is_index_etf
    )

    # 限制维度数量
    search_dimensions = search_dimensions[:max_searches]

    # 后续 async _single_dimension_search、asyncio.gather 逻辑保持不变
```

- [ ] **Step 4: 写测试**

在 `tests/test_search_news_freshness.py` 末尾添加：

```python
class TestIntelDimensionsAlignment:
    """Verify sync and async comprehensive intel use the same dimensions."""

    def test_async_and_sync_dimensions_match(self):
        """Both paths should build the same dimension set from _build_intel_dimensions."""
        from src.search.service import SearchService

        # A-share case
        sync_dims = SearchService._build_intel_dimensions(
            "600519", "贵州茅台", is_foreign=False, is_index_etf=False
        )
        async_dims = SearchService._build_intel_dimensions(
            "600519", "贵州茅台", is_foreign=False, is_index_etf=False
        )
        sync_names = [d['name'] for d in sync_dims]
        async_names = [d['name'] for d in async_dims]
        assert sync_names == async_names
        assert 'market_analysis' in sync_names
        assert 'announcements' in sync_names
        assert 'industry' in sync_names
        assert 'macro_news' in sync_names
        assert len(sync_names) == 7  # A-share: 7 dimensions

    def test_foreign_dimensions_include_market_analysis_and_industry(self):
        """Foreign stocks must also have market_analysis and industry dimensions."""
        from src.search.service import SearchService

        dims = SearchService._build_intel_dimensions(
            "AAPL", "Apple", is_foreign=True, is_index_etf=False
        )
        names = [d['name'] for d in dims]
        assert 'market_analysis' in names
        assert 'industry' in names
        assert 'macro_news' in names
        assert len(names) == 6  # Foreign: 6 dimensions (no announcements)

    def test_index_etf_uses_adapted_queries(self):
        """Index/ETF entries should use adapted queries, not stock-specific ones."""
        from src.search.service import SearchService

        dims = SearchService._build_intel_dimensions(
            "510050", "上证50ETF", is_foreign=False, is_index_etf=True
        )
        risk_dim = next(d for d in dims if d['name'] == 'risk_check')
        assert '指数' in risk_dim['query'] or 'index' in risk_dim['query'].lower()
```

- [ ] **Step 5: 运行测试**

```bash
python3 -m pytest tests/test_search_news_freshness.py::TestIntelDimensionsAlignment -v
```

Expected: 3 tests FAIL (new test class, shared method not yet wired — or PASS if compilation succeeds first)

- [ ] **Step 6: 运行全量测试确认无回归**

```bash
python3 -m pytest tests/test_search_news_freshness.py tests/test_search_tavily_provider.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/search/service.py tests/test_search_news_freshness.py
git commit -m "refactor: extract shared _build_intel_dimensions, align async intel path"
```

---

### Task 2: Freshness 规则统一 — 异步路径传 strict=True

**Files:**
- Modify: `src/search/service.py:527-530`

**Goal:** `search_stock_news_async` 调用 `_filter_news_response` 时传 `strict=True`，与宏观新闻维度一致。

- [ ] **Step 1: 修改 `_filter_news_response` 调用**

在 `search_stock_news_async` 方法中（约第 527-530 行），给 `_filter_news_response` 加上 `strict=True`：

```python
# Before:
filtered_response = self._filter_news_response(
    response, search_days=search_days, max_results=max_results,
    log_scope=f"{stock_code}:{provider.name}:stock_news_async",
)

# After:
filtered_response = self._filter_news_response(
    response, search_days=search_days, max_results=max_results,
    log_scope=f"{stock_code}:{provider.name}:stock_news_async",
    strict=True,
)
```

- [ ] **Step 2: 写测试 — 验证 strict 模式不过滤有日期的合法新闻**

在 `tests/test_search_news_freshness.py` 末尾添加：

```python
class TestAsyncFreshnessEnforcement:
    """Verify async news path enforces strict freshness."""

    def test_async_filter_passes_strict_when_dates_within_window(self):
        """News items with published_date within window should pass strict=True."""
        from datetime import datetime, timedelta
        from src.search.types import SearchResult, SearchResponse
        from src.search.service import SearchService

        today = datetime.now().strftime('%Y-%m-%d')
        svc = SearchService(tavily_keys=[])

        response = SearchResponse(
            query="test",
            results=[
                SearchResult(
                    title="Recent news",
                    snippet="Something",
                    url="https://example.com/1",
                    source="TestSource",
                    published_date=today,
                ),
            ],
            provider="tavily",
            success=True,
        )

        filtered = svc._filter_news_response(
            response, search_days=3, max_results=5,
            log_scope="TEST:async_strict", strict=True,
        )
        assert len(filtered.results) == 1

    def test_async_filter_rejects_old_news_when_strict(self):
        """News items with dates outside window should be rejected with strict=True."""
        from datetime import datetime, timedelta
        from src.search.types import SearchResult, SearchResponse
        from src.search.service import SearchService

        old_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        svc = SearchService(tavily_keys=[])

        response = SearchResponse(
            query="test",
            results=[
                SearchResult(
                    title="Old news",
                    snippet="Something",
                    url="https://example.com/1",
                    source="TestSource",
                    published_date=old_date,
                ),
            ],
            provider="tavily",
            success=True,
        )

        filtered = svc._filter_news_response(
            response, search_days=3, max_results=5,
            log_scope="TEST:async_strict_old", strict=True,
        )
        assert len(filtered.results) == 0

    def test_async_filter_handles_missing_date_when_strict(self):
        """News items without published_date should be rejected with strict=True."""
        from src.search.types import SearchResult, SearchResponse
        from src.search.service import SearchService

        svc = SearchService(tavily_keys=[])

        response = SearchResponse(
            query="test",
            results=[
                SearchResult(
                    title="Undated news",
                    snippet="Something",
                    url="https://example.com/1",
                    source="TestSource",
                    published_date=None,
                ),
            ],
            provider="tavily",
            success=True,
        )

        filtered = svc._filter_news_response(
            response, search_days=3, max_results=5,
            log_scope="TEST:async_strict_undated", strict=True,
        )
        assert len(filtered.results) == 0
```

- [ ] **Step 3: 运行新测试**

```bash
python3 -m pytest tests/test_search_news_freshness.py::TestAsyncFreshnessEnforcement -v
```

Expected: 3 PASS

- [ ] **Step 4: 运行全量测试**

```bash
python3 -m pytest tests/test_search_news_freshness.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/search/service.py tests/test_search_news_freshness.py
git commit -m "fix: enforce strict freshness in async stock news path"
```

---

### Task 3: 报告质量闸门 — JSON 提取失败时 fail-fast

**Files:**
- Modify: `src/analyzer/core.py:704-781`
- Modify: `src/core/pipeline_executor.py:330-360`（integrity check 对齐）

**Goal:** `extract_json_from_text` 返回空 `{}` 时，`_parse_response` 返回 `success=False`，不再用 placeholder 伪装成功。

- [ ] **Step 1: 修改 `_parse_response`**

将 `src/analyzer/core.py` 第 707 行：

```python
# Before:
data = extract_json_from_text(text) or {}

# After:
data = extract_json_from_text(text)
if not data:
    return AnalysisResult(
        code=code,
        name=name,
        success=False,
        error="JSON extraction failed — model output could not be parsed",
        raw_response=text,
    )
```

注意：`AnalysisResult` dataclass 需要支持 `error` 字段。检查 `src/schemas/analysis_result.py` 是否已有 `error` 字段。如果没有，在 dataclass 中添加：

```python
error: Optional[str] = None
```

- [ ] **Step 2: 检查 `AnalysisResult` dataclass**

```bash
grep -n 'class AnalysisResult' src/schemas/analysis_result.py
```

Read the class，确认有 `success` 字段。如果缺 `error` 字段，读 `src/schemas/analysis_result.py` 并添加：

```python
error: Optional[str] = None
```

- [ ] **Step 3: 写测试**

在 `tests/` 下新建或扩展现有测试文件：

```python
def test_parse_response_fails_on_empty_json():
    """When extract_json_from_text returns {}, result should be success=False."""
    from src.analyzer.core import GeminiAnalyzer
    from unittest.mock import patch

    analyzer = GeminiAnalyzer()
    # Input that produces no JSON structure
    garbage_text = "This is not JSON at all, just some prose about the stock."

    result = analyzer._parse_response(garbage_text, "600519", "贵州茅台")
    assert result.success is False
    assert "JSON extraction failed" in (result.error or "")


def test_parse_response_succeeds_on_valid_json():
    """When extract_json_from_text returns valid data, result should be success=True."""
    from src.analyzer.core import GeminiAnalyzer

    analyzer = GeminiAnalyzer()
    valid_text = '''
    {
        "decision_type": "持有",
        "decision_dashboard": {
            "analysis_summary": "短期震荡偏强",
            "trend_prediction": "震荡上行",
            "operation_advice": "可逢低加仓",
            "confidence_level": "中"
        }
    }
    '''

    result = analyzer._parse_response(valid_text, "600519", "贵州茅台")
    assert result.success is True
    assert result.decision_type == "持有"
```

- [ ] **Step 4: 运行测试**

```bash
python3 -m pytest tests/ -k "parse_response" -v
```

Expected: 2 PASS

- [ ] **Step 5: 确认 pipeline_executor 处理 success=False**

读 `src/core/pipeline_executor.py` 中调用分析结果的地方（约 342 行），确认当 `result.success is False` 时行为正确（至少记日志、不崩溃）：

```bash
grep -n 'success\|integrity_check\|placeholder\|result\.success' src/core/pipeline_executor.py | head -10
```

如果 `pipeline_executor` 对 `success=False` 做了 integrity check 降级，无需额外改动。如果直接假设 `success=True`，加：

```python
if not result.success:
    logger.warning("分析失败 (%s): %s", result.code, result.error)
    # continue to next stock or fallback
```

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/core.py src/schemas/analysis_result.py tests/
git commit -m "fix: fail-fast on JSON extraction failure in parse_response"
```

---

### Task 4: 文档漂移清理

**Files:**
- Delete: `docs/architecture/api_spec.json`
- Modify: `src/config/manager.py:111`（删除 webui_port 字段定义）
- Modify: `src/config/manager.py:535-538`（删除 _load_from_env 中 webui_port 赋值）
- Modify: `config.example.yaml:10`（删除 webui_port 行）
- Modify: `README.md:15`（删除 API 服务模式描述）
- Modify: `docs/README_CHT.md:198-214`（删除 API 端点表格）
- 检查: `docs/README_EN.md`（无 API 端点引用则跳过）

**Goal:** 删除所有不存在的 API/WebUI 残留引用。

- [ ] **Step 1: 删除 `api_spec.json`**

```bash
rm docs/architecture/api_spec.json
```

- [ ] **Step 2: 删除 `webui_port` 字段**

在 `src/config/manager.py` 中：
- 删除第 111 行的字段定义：
```python
# 删除这行:
webui_port: int = field(default=8000, metadata={"env": "WEBUI_PORT", "yaml": "system.webui_port", "group": "system"})
```

- 删除 `_load_from_env` 中的赋值（约 535-538 行）：
```python
# 删除这几行:
webui_port=parse_env_int(
    os.getenv("WEBUI_PORT"),
    fields.get("webui_port", 8000),
    field_name="WEBUI_PORT",
),
```

- 重新生成 `.env.example` 和 `config.example.yaml`：
```bash
python3 scripts/gen_env_example.py --write
python3 scripts/gen_config_example.py --write
```

- [ ] **Step 3: 修改 README.md**

第 15 行：
```
# Before:
当前版本专注于 **高性能命令行 (CLI) 模式** 与 **API 服务模式**：

# After:
当前版本专注于 **高性能命令行 (CLI) 模式**：
```

- [ ] **Step 4: 修改 `docs/README_CHT.md`**

删除第 198-214 行的 API 端点表格（从 `| API 端點` 到 `>` 备注行）。

- [ ] **Step 5: 全局搜索残留引用**

```bash
grep -rn 'webui_port\|WEBUI_PORT\|api_spec\|api/v1' src/ docs/ README.md config.example.yaml --include='*.py' --include='*.md' --include='*.yaml' --include='*.json' 2>/dev/null | grep -v '.git/' | grep -v '__pycache__'
```

清理任何漏网引用。

- [ ] **Step 6: 验证**

```bash
python3 -m py_compile src/config/manager.py
python3 scripts/check_config_contract.py --strict
./scripts/ci_gate.sh config-contract
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add -u docs/architecture/api_spec.json src/config/manager.py config.example.yaml .env.example README.md docs/README_CHT.md
git commit -m "docs: remove stale API/WebUI references and webui_port config"
```

---

### Task 5: 证据溯源 — 补齐 url/published_date 透传

**Files:**
- Modify: `src/agent/tools/search_tools.py:170-177`（补齐返回字段）
- Modify: `src/analyzer/prompt_builder.py`（新增📎信息来源章节）
- Verify: `src/search/types.py`（SearchResult 已有 url/published_date）

**Goal:** Agent 工具和 prompt 补齐证据字段，报告末尾有信息来源章节。

- [ ] **Step 1: 补齐 Agent 工具返回字段**

修改 `src/agent/tools/search_tools.py` 第 170-177 行：

```python
# Before:
"results": [
    {
        "title": r.title,
        "snippet": r.snippet,
        "source": r.source,
    }
    for r in response.results[:3]
],

# After:
"results": [
    {
        "title": r.title,
        "snippet": r.snippet,
        "source": r.source,
        "url": r.url or "",
        "published_date": r.published_date or "",
    }
    for r in response.results[:3]
],
```

- [ ] **Step 2: Prompt 添加信息来源章节要求**

在 `src/analyzer/prompt_builder.py` 中找到个股分析输出模板（约在 `build_stock_analysis_prompt` 或类似函数），在模板末尾的「风险提示」后添加：

```python
# 在输出模板的最后一个章节后追加：
### 八、📎 信息来源
（列出关键结论的新闻来源、日期和链接，格式：
- [日期] 标题 — 来源 (URL)
至少列出 3 条支撑本次分析结论的信息来源。若某来源无 URL 可省略链接，但必须注明日期和标题。）
```

- [ ] **Step 3: 全局 prompt 调用处检查**

```bash
grep -n '信息来源\|evidence\|source.*date\|📎' src/analyzer/prompt_builder.py
```

确保新增的章节要求不与其他 template 冲突。

- [ ] **Step 4: 验证 SearchResult 数据源透传**

确认 Tavily provider 返回的 `url` 和 `published_date` 已透传至 `SearchResult`：

```bash
grep -A5 'SearchResult(' src/search/providers/tavily.py | head -20
```

检查结果中是否有 `url=` 和 `published_date=` 字段。若 Tavily 返回了但没有映射，在 provider 中补上。

- [ ] **Step 5: 写测试**

```python
def test_agent_tool_results_include_url_and_date():
    """Agent search tool result entries must include url and published_date.
    
    Verifies that _handle_search_comprehensive_intel's structured output
    carries evidence fields, not just title/snippet/source.
    """
    from unittest.mock import patch, MagicMock
    from src.search.types import SearchResult, SearchResponse

    mock_svc = MagicMock()
    mock_svc.is_available = True
    mock_svc.search_comprehensive_intel.return_value = {
        "latest_news": SearchResponse(
            query="test query",
            results=[
                SearchResult(
                    title="FOMC decision",
                    snippet="The Fed held rates steady",
                    url="https://example.com/fomc",
                    source="Reuters",
                    published_date="2026-05-09",
                ),
            ],
            provider="tavily",
            success=True,
        ),
    }
    mock_svc.format_intel_report.return_value = "# Intel Report\nDummy"

    with patch(
        "src.agent.tools.search_tools._get_search_service",
        return_value=mock_svc,
    ):
        from src.agent.tools.search_tools import _handle_search_comprehensive_intel

        result = _handle_search_comprehensive_intel("600519", "贵州茅台")

    dim = result["dimensions"]["latest_news"]
    assert dim["results_count"] == 1
    entry = dim["results"][0]
    assert entry["title"] == "FOMC decision"
    assert entry["source"] == "Reuters"
    assert entry["url"] == "https://example.com/fomc"
    assert entry["published_date"] == "2026-05-09"
```

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools/search_tools.py src/analyzer/prompt_builder.py
git commit -m "feat: add url/published_date to agent search results and evidence section to prompt"
```

---

### Task 6: 配置可用性分级 — 搜索能力 strong/weak

**Files:**
- Modify: `src/config/manager.py:1007-1013`（`validate_structured` 中的搜索能力判断）

**Goal:** 配置校验将 AkShare 纳入搜索能力评估，weak 时给出分级提示。

- [ ] **Step 1: 修改搜索能力检查逻辑**

将 `src/config/manager.py` 中 `validate_structured` 方法约 1007-1013 行：

```python
# Before:
has_search = bool(
    self.tavily_api_keys
    or self.finnhub_api_key
    or self.openbb_news_enabled
)
if not has_search:
    issues.append(ConfigIssue("info", "搜索引擎未配置，新闻检索能力将受限。", field="TAVILY_API_KEYS"))

# After:
has_strong_search = bool(
    self.tavily_api_keys
    or self.finnhub_api_key
    or self.openbb_news_enabled
)
# 即使未配置上述 provider，SearchService 也会自动启用 AkShare 东方财富新闻源
has_any_search = True  # AkShare is auto-enabled by SearchService

if not has_strong_search:
    if has_any_search:
        issues.append(ConfigIssue(
            "info",
            "搜索能力：弱（仅 AkShare 东方财富公告），建议配置 TAVILY_API_KEYS 获取完整新闻与宏观信息。",
            field="TAVILY_API_KEYS",
        ))
    else:
        issues.append(ConfigIssue(
            "info",
            "搜索引擎未配置，新闻检索能力将受限。",
            field="TAVILY_API_KEYS",
        ))
```

- [ ] **Step 2: 更新 `has_search_capability_enabled` 方法**

检查 `src/config/manager.py` 中 `has_search_capability_enabled` 方法（约 1024 行），确认其逻辑与 `validate_structured` 一致。如果有差异，也加上 AkShare 感知。

- [ ] **Step 3: 运行测试验证**

```bash
python3 -m pytest tests/ -k "config" -v
```

Expected: config 相关测试 PASS

- [ ] **Step 4: Commit**

```bash
git add src/config/manager.py
git commit -m "feat: add search capability tiering (strong/weak) with AkShare awareness"
```

---

### Task 7: 全量回归验证

- [ ] **Step 1: 运行全量离线测试**

```bash
python3 -m pytest -m "not network" -q
```

Expected: all tests PASS

- [ ] **Step 2: 运行 ci_gate**

```bash
./scripts/ci_gate.sh
```

Expected: `backend-gate: all checks passed`

- [ ] **Step 3: 运行 config contract check**

```bash
python3 scripts/check_config_contract.py --strict
```

Expected: `OK: Config contract is consistent`

---

## 验证矩阵

| 任务 | 测试文件 | ci_gate 阶段 |
|------|---------|-------------|
| 1. 维度对齐 | `test_search_news_freshness.py::TestIntelDimensionsAlignment` | `offline-tests` |
| 2. Freshness | `test_search_news_freshness.py::TestAsyncFreshnessEnforcement` | `offline-tests` |
| 3. 质量闸门 | `test_analyzer_news_prompt.py`（扩展） | `offline-tests` |
| 4. 文档漂移 | `check_config_contract.py` | `config-contract` |
| 5. 证据溯源 | `test_search_news_freshness.py`（扩展） | `offline-tests` |
| 6. 配置分级 | `test_config_contract.py` / config tests | `config-contract` |
| 7. 回归验证 | 全量 | `./scripts/ci_gate.sh` |
