# 搜索工具链增强与 SQLite 缓存层清理设计

## 概述

三个独立但相关的工作包：拆除 SQLite 缓存层（A 组）、增强 Agent 搜索工具链（全文提取 + LLM 结构化摘要）、编写端到端回归测试。

## 1. SQLite 缓存层拆除（A 组）

### 删除范围

**删除的表（Model + 所有 CRUD）：**

- **StockDaily** — K 线日线缓存。改用 `DataFetcherManager.get_daily_data`（已有 async 版本，支持 LongbridgeFetcher → AkshareFetcher / YfinanceFetcher fallback 链）直接网络获取。
- **NewsIntel** — 新闻搜索结果持久化。Agent 搜新闻后不再存历史，结果实时返回。
- **FundamentalSnapshot** — 基本面数据缓存。直接删除，每次重新拉取。

**删除的方法（`DatabaseManager` 中）：**

- `has_today_data`、`get_data_range`、`get_data_range_async`、`save_daily_data`、`save_daily_data_async`
- `save_news_intel`、`get_news_intel_by_query_id`、`get_recent_news`
- `save_fundamental_snapshot`
- `get_latest_data`、`get_global_latest_date`
- `_analyze_ma_status`、`_find_sniper_in_dashboard`
- `_normalize_daily_date`、`_normalize_sql_value`、`_build_fallback_url_key`

**删除的文件：**

- `src/repositories/stock_repo.py`（整个文件，全部依赖 StockDaily）

**改造的调用方：**

| 文件 | 改动 |
|------|------|
| `src/core/pipeline.py` | 移除 `has_today_data` 缓存检查、移除 `prefetch_stock_names`（缓存到 SQLite 的逻辑） |
| `src/core/pipeline_data_collector.py` | `_collect_trend_and_kline` 中 `get_data_range_async` → `fetcher_manager.get_daily_data` |
| `src/services/fact_checker.py` | `StockDaily` 读取 → 改用 `fetcher_manager.get_daily_data` |
| `src/services/history_loader.py` | `get_db` / `get_data_range` → `fetcher_manager.get_daily_data` |
| `src/market_analyzer.py` | `get_db` / `StockDaily` 引用 → `fetcher_manager.get_daily_data` |
| `src/agent/tools/search_tools.py` | 删除 `_persist_news_response`（写入 NewsIntel） |
| `src/agent/tools/data_tools.py` | 删除 `get_db` 引用 |
| `src/agent/tools/analysis_tools.py` | 删除 `get_db` 引用 |
| `src/services/history_service.py` | 删除 `DatabaseManager` 中 stock 相关引用 |

**不动（B 组）：**

BacktestResult、BacktestSummary、PortfolioAccount、PortfolioTrade、PortfolioCashLedger、PortfolioCorporateAction、PortfolioPosition、PortfolioPositionLot、PortfolioDailySnapshot、PortfolioFxRate、ConversationMessage、LLMUsage、AnalysisHistory、PredictionEval

### 风险

- 网络故障时分析中断（之前有 SQLite 降级保护）
- `_enrich_quote_from_history` 需要历史 df，`get_daily_data` 返回格式与 `get_data_range_async` 返回的 bar list 不同，需适配
- `fact_checker.py` 依赖历史 k 线做验证断言，改成实时拉语义可能有细微差别

## 2. Agent 搜索工具链增强（全文提取 + LLM 结构化摘要）

### 改造内容

在 `src/agent/tools/search_tools.py` 中增强 `search_stock_news` 工具：

```python
# 改后返回结构
{
    "title": "...",
    "url": "...",
    "source": "Reuters",
    "published_date": "...",
    "extracted": True/False,
    "full_text_snippet": "前500字预览...",
    "llm_analysis": {
        "key_points": ["...", "..."],
        "key_data": {"LME铜价": "$9,850", ...},
        "ticker_impact": [
            {"ticker": "600362", "sentiment": "bullish", "confidence": 0.85, "reason": "..."},
        ]
    }
}
```

### 实现方式

1. **全文提取**：用 `trafilatura`（已在依赖中）对搜索结果中的 URL 做 HTML→Markdown 提取
2. **LLM 摘要**：在工具 handler 内调用 `Analyzer.generate_text(prompt)` 做结构化分析
3. **性能控制**：
   - 最多处理 3 条 URL / 次搜索
   - 全文提取超时 10s，跳过失败项
   - LLM 摘要超时 15s，降级返回原始 snippet
   - 用短模型（现有 analyzer 即可），不需要深度思考
4. **不做的**：站点定制解析、全文存储（NewsIntel 即将删除）、RSS 订阅/定时抓取

## 3. 端到端回归测试

### 测试文件

新增 `tests/test_e2e_pipeline.py`：

- `test_full_analysis_flow` — 从测试配置文件 → 长桥 API 获取 → 技术分析 → 新闻搜索 → markdown 报告
- `test_agent_search_stock_news` — 搜新闻 → 验证全文提取 + LLM 摘要 → 网络失败降级
- `test_market_review` — 执行 market-review → 验证报告覆盖所有配置板块

### 技术方案

- **VCR 录制**：`pytest-vcr` 录制真实 HTTP 交互，后续回放零网络依赖
- **不 Mock 逻辑层**：不 mock DataFetcherManager、SearchService，只录放 HTTP
- **数据隔离**：`tests/fixtures/stocks_test.yaml` 用 1-2 只测试股票
- **CI 标记**：`@pytest.mark.network`，日常 CI 跳过，network-smoke 工作流可手动触发
- **验证维度**：数据完整性、报告结构、降级路径、新搜索工具链
