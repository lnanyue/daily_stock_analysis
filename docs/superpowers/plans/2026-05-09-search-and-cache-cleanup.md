# SQLite Cache Removal + Search Enhancement + E2E Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove SQLite caching layer for renewable network data (k-line, news, fundamentals), enhance Agent search with full-text extraction + LLM structured analysis, and add VCR-based end-to-end regression tests.

**Architecture:** Three independent phases: (1) strip out SQLite models/methods and redirect all k-line consumers to `DataFetcherManager.get_daily_data`; (2) add `trafilatura` full-text extraction and LLM-powered structured summarization to the existing `search_stock_news` tool handler; (3) write VCR-recorded e2e tests covering the full pipeline and new search tool.

**Tech Stack:** Python, asyncio, trafilatura, DataFetcherManager, SearchService, pytest-vcr

---

## Phase 1: SQLite Cache Removal (A组)

Remove `StockDaily`, `NewsIntel`, `FundamentalSnapshot` models + all associated methods from `DatabaseManager` and redirect all callers to use network fetches via `DataFetcherManager` / `SearchService` directly.

### Task 1: Clean up storage.py — remove A组 models and methods

**Files:**
- Modify: `src/storage.py`

**Changes:**

*Delete these SQLAlchemy model classes entirely:*
- `StockDaily` (lines ~122-190)
- `NewsIntel` (lines ~193-239)
- `FundamentalSnapshot` (lines ~241-264)

*Delete these `DatabaseManager` methods entirely:*
- `has_today_data` (~912-919)
- `get_latest_data` (~921-926)
- `get_global_latest_date` (~928-934)
- `save_daily_data` (~1030-1112, incl inner `_write`)
- `save_news_intel` (~1114-1186, incl inner helpers)
- `save_fundamental_snapshot` (~1392-1414)
- `get_data_range` (~1481-1489)
- `get_data_range_async` (~1491-1493)
- `save_daily_data_async` (~1495-1497)
- `save_analysis_history_async` (~1544) **NOTE: keep the sync `save_analysis_history` since AnalysisHistory is B组**
- `get_news_intel_by_query_id` (~1371-1379)
- `get_recent_news` (~1381-1390)
- `_analyze_ma_status` (~1416-1442)
- `_normalize_daily_date` (~1645)
- `_normalize_sql_value` (~1650)
- `_build_fallback_url_key` (~1663-1665)

*Keep (used by B组 code):*
- `_find_sniper_in_dashboard` (~1445-1479) — pure dict-parsing utility, used by `history_service.py`
- `_parse_sniper_value` (~1499 onwards) — pure string parsing
- `save_analysis_history` (sync version)
- `get_analysis_history` / `get_analysis_history_by_id` / `get_latest_analysis_by_query_id` / `delete_analysis_history_records`
- `save_prediction_eval`, `get_pending_evaluations`, `update_prediction_verdict`, `get_evaluation_stats`
- `save_conversation_message`, `get_conversation_history`, `get_chat_sessions`
- `record_llm_usage`, `get_llm_usage_summary`
- `save_analysis_history_async` — only if B组 code calls it asynchronously
- All `Portfolio*` related methods

*Also remove imports* that are now unused at the top of `storage.py`:
- `pandas` import (if only used by A组 methods)
- `re` import (check if only used by A组 methods)
- `json` import (check if still needed by B组)

- [ ] **Step 1: Remove model classes and method bodies**

Edit `storage.py` to delete the classes and methods listed above. Leave the `DatabaseManager` class with B组 methods intact.

- [ ] **Step 2: Run imports cleanup**

```bash
python -c "from src.storage import DatabaseManager, get_db; print('import OK')"
```

- [ ] **Step 3: Run py_compile check**

```bash
python -m py_compile src/storage.py
```

- [ ] **Step 4: Commit**

```bash
git add src/storage.py
git commit -m "perf: remove StockDaily, NewsIntel, FundamentalSnapshot models and methods (A组 cache layer)"
```

---

### Task 2: Rewrite pipeline_data_collector.py — replace get_data_range with fetcher_manager

**Files:**
- Modify: `src/core/pipeline_data_collector.py`

**Change:** In `_collect_trend_and_kline` (line 261), replace the SQLite read with a direct network call.

```python
# OLD (line 261):
hist = await self.db.get_data_range_async(code, end_date - timedelta(days=90), end_date)

# NEW:
hist_df, hist_source = await self.fetcher_manager.get_daily_data(
    code, days=90,
    end_date=end_date.strftime('%Y-%m-%d') if isinstance(end_date, date) else None,
)
if hist_df is not None and not hist_df.empty:
    # Convert to the same format expected downstream
    # _enrich_quote_from_history and sorted_df below expect a DataFrame
    df = hist_df
else:
    df = pd.DataFrame()
```

Also remove the `self.db` parameter usage for data range in the constructor — the collector no longer needs `db` for k-line. Remove the `db` parameter from `__init__` if it's only used for `get_data_range_async`. Check if `db` is used for anything else in the collector (like `_collect_news` which calls `search_service`, not db).

**Check if `self.db` is used elsewhere in `pipeline_data_collector.py`:** If `db` was only passed for `get_data_range_async`, remove the parameter entirely. If `db` has no other uses in `collector`, all references to `self.db` can be dropped.

- [ ] **Step 1: Modify _collect_trend_and_kline**

Edit `pipeline_data_collector.py` to replace `get_data_range_async` with `fetcher_manager.get_daily_data`. The downstream code at lines 266-284 works with a `pd.DataFrame` named `df`, so the new code feeds that same variable.

```python
async def _collect_trend_and_kline(self, code: str, result: StockDataCollectionResult) -> None:
    end_date = result.analysis_date
    hist_df, _ = await self.fetcher_manager.get_daily_data(
        code, days=90,
        end_date=end_date.isoformat() if isinstance(end_date, date) else None,
    )
    if hist_df is None or hist_df.empty:
        return
    df = hist_df

    if self.config.enable_realtime_quote and result.realtime_quote and self._augment_fn is not None:
        df = self._augment_fn(df, result.realtime_quote, code)

    trend_result = await asyncio.to_thread(self.trend_analyzer.analyze, df, code)
    result.trend_result = trend_result
    result.visual_description = f"\n### 视觉形态描述\n- 趋势: {trend_result.trend_status.value}\n"

    if result.realtime_quote is not None:
        self._enrich_quote_from_history(result.realtime_quote, df)

    sorted_df = df.sort_values('date', ascending=False)
    if len(sorted_df) > 0:
        result.today_k = sorted_df.iloc[0].to_dict()
        if isinstance(result.today_k.get('date'), (datetime, date)):
            result.today_k['date'] = result.today_k['date'].isoformat()
    if len(sorted_df) > 1:
        result.yesterday_k = sorted_df.iloc[1].to_dict()
        if isinstance(result.yesterday_k.get('date'), (datetime, date)):
            result.yesterday_k['date'] = result.yesterday_k['date'].isoformat()
```

- [ ] **Step 2: Remove `self.db` usage from collector if no longer needed**

Check if `self.db` is used anywhere in `pipeline_data_collector.py` besides `_collect_trend_and_kline`. If not, remove it from `__init__` and the class.

- [ ] **Step 3: Verify compilation**

```bash
python -m py_compile src/core/pipeline_data_collector.py
```

- [ ] **Step 4: Commit**

```bash
git add src/core/pipeline_data_collector.py
git commit -m "perf: replace SQLite k-line read with network fetch in _collect_trend_and_kline"
```

---

### Task 3: Update pipeline.py — remove cache checks and cache writes

**Files:**
- Modify: `src/core/pipeline.py`

**Changes:**

1. Remove `has_today_data` check (lines 205-208) — the `force_refresh` param can stay for future use but the SQLite cache check goes:
```python
# DELETE these lines (205-208):
if not force_refresh and await asyncio.to_thread(self.db.has_today_data, code, target_date):
    logger.info(f"{stock_name}({code}) {target_date} 数据已存在，跳过获取（断点续传）")
    return True, None
```

2. Remove `save_daily_data_async` call (line 216) after `get_daily_data` succeeds:
```python
# DELETE this line (216):
await self.db.save_daily_data_async(df, code, source_name)
```

The `get_daily_data` call and empty check stay:
```python
res = await self.fetcher_manager.get_daily_data(code, days=45)
df, source_name = res
if df is None or df.empty:
    return False, "获取数据为空"
# save_daily_data_async removed — always fetch from network
return True, None
```

3. Remove `prefetch_stock_names` (lines 235-243) — the stock name is already fetched per-stock at line 198:
```python
# DELETE these lines (235-243):
if not dry_run and hasattr(self.fetcher_manager, "prefetch_stock_names"):
    try:
        await asyncio.to_thread(
            self.fetcher_manager.prefetch_stock_names,
            list(stock_codes),
            use_bulk=False,
        )
    except Exception as exc:
        logger.warning("股票名称预取失败，继续主流程: %s", exc)
```

Also remove import `asyncio.to_thread` if it's no longer needed in this file (check if any other `to_thread` calls remain).

- [ ] **Step 1: Remove has_today_data check + save_daily_data from process_single_stock**

- [ ] **Step 2: Remove prefetch_stock_names from run()**

- [ ] **Step 3: Verify compilation**

```bash
python -m py_compile src/core/pipeline.py
```

- [ ] **Step 4: Commit**

```bash
git add src/core/pipeline.py
git commit -m "perf: remove SQLite cache checks and writes from pipeline.py"
```

---

### Task 4: Remove stock_repo.py

**Files:**
- Delete: `src/repositories/stock_repo.py`

This entire file depends on `StockDaily` and `DatabaseManager` for stock k-line data access. Since we're removing StockDaily, delete it.

Check if any other file imports `stock_repo`:
```bash
grep -rn "stock_repo\|from src.repositories.stock_repo\|import.*stock_repo" /Users/ming/Desktop/daily_stock_analysis/src/ --include="*.py"
```

- [ ] **Step 1: Find and remove all imports of stock_repo.py**

```bash
grep -rn "stock_repo" /Users/ming/Desktop/daily_stock_analysis/src/ --include="*.py" | grep -v "__pycache__"
```
For each import found, remove the import line. If the imported symbols are unused after removal, remove those too.

- [ ] **Step 2: Delete stock_repo.py**

```bash
rm /Users/ming/Desktop/daily_stock_analysis/src/repositories/stock_repo.py
```

- [ ] **Step 3: Verify compilation**

```bash
python -m py_compile src/core/pipeline.py
# plus any other files that imported stock_repo
```

- [ ] **Step 4: Commit**

```bash
git add src/repositories/stock_repo.py  # (deletion)
git commit -m "perf: remove stock_repo.py (depends on deleted StockDaily model)"
```

---

### Task 5: Update fact_checker.py — remove StockDaily dependency

**Files:**
- Modify: `src/services/fact_checker.py`

**Change:** Replace the `StockDaily` ORM query with `DataFetcherManager.get_daily_data_sync` (or async).

```python
# OLD (lines 18, 127-139):
from sqlalchemy import select
from src.storage import DatabaseManager, StockDaily

# ... in _get_close_price:
with self.db.get_session() as session:
    row = session.execute(
        select(StockDaily.close)
        .where(StockDaily.code == code)
        .where(StockDaily.date == eval_date)
    ).scalar()
    return float(row) if row is not None else None

# NEW:
from data_provider import DataFetcherManager

# ... in _get_close_price:
try:
    df, _ = DataFetcherManager().get_daily_data_sync(
        code,
        start_date=(eval_date - timedelta(days=5)).isoformat(),
        end_date=(eval_date + timedelta(days=1)).isoformat(),
    )
    if df is not None and not df.empty:
        # Find row matching eval_date
        match = df[df['date'] == eval_date]
        if not match.empty:
            return float(match.iloc[0]['close'])
    return None
except Exception as exc:
    logger.debug("[%s] get_daily_data_sync failed for %s: %s", code, eval_date, exc)
    return None
```

Also remove the unused `from sqlalchemy import select` import.

- [ ] **Step 1: Rewrite _get_close_price to use DataFetcherManager**

Edit `fact_checker.py` — replace the StockDaily query with `DataFetcherManager().get_daily_data_sync`. Remove `StockDaily` and `select` imports.

- [ ] **Step 2: Verify compilation**

```bash
python -m py_compile src/services/fact_checker.py
```

- [ ] **Step 3: Commit**

```bash
git add src/services/fact_checker.py
git commit -m "perf: remove StockDaily dependency from fact_checker.py, use network fetch"
```

---

### Task 6: Remove _persist_news_response from search_tools.py

**Files:**
- Modify: `src/agent/tools/search_tools.py`

**Change:** Remove the `_persist_news_response` function (lines 35-68) and all calls to it. The `_persist_news_response` function saves to `NewsIntel` in SQLite, which we're removing.

Remove:
- `_persist_news_response` function definition (lines 35-68)
- Call to `_persist_news_response` in `_handle_search_stock_news` (lines 87-92)
- Calls to `_persist_news_response` in `_handle_search_comprehensive_intel` (lines 162-166)
- Import of `get_db` via `_get_db` helper if `_get_db` is no longer needed

Keep `_get_db` if it's imported but used elsewhere. Actually `_get_db()` is only called through `_persist_news_response` in this file, so the entire `_get_db` function can be removed.

- [ ] **Step 1: Remove _persist_news_response function and _get_db helper**

Delete the function body and all calls to it. Remove `_get_db()` function definition.

- [ ] **Step 2: Verify compilation**

```bash
python -m py_compile src/agent/tools/search_tools.py
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/tools/search_tools.py
git commit -m "perf: remove _persist_news_response (writes to deleted NewsIntel table)"
```

---

### Task 7: Update analysis_tools.py — remove SQLite fallback in _fetch_trend_data

**Files:**
- Modify: `src/agent/tools/analysis_tools.py`

**Change:** In `_fetch_trend_data`, remove the SQLite lookup path (lines 31-43) that tries `db.get_data_range` first. Always go directly to `DataFetcherManager`.

```python
# OLD function (~lines 17-59):
def _fetch_trend_data(stock_code: str):
    from datetime import date, timedelta
    import pandas as pd
    from data_provider import canonical_stock_code, DataFetchError
    from data_provider import DataFetcherManager
    from src.storage import get_db

    code = canonical_stock_code(stock_code)
    if not code:
        return None
    end_date = date.today()
    start_date = end_date - timedelta(days=89)

    # 1. Try DB — DELETE THIS ENTIRE BLOCK
    try:
        db = get_db()
        bars = db.get_data_range(code, start_date, end_date)
        ...
    except ...

    # 2. Fallback to DataFetcherManager — KEEP this, remove "Fallback" label
    try:
        manager = DataFetcherManager()
        df, _ = manager.get_daily_data_sync(code, days=90)
        ...

# NEW:
def _fetch_trend_data(stock_code: str):
    from datetime import date, timedelta
    import pandas as pd
    from data_provider import canonical_stock_code, DataFetchError
    from data_provider import DataFetcherManager

    code = canonical_stock_code(stock_code)
    if not code:
        return None

    try:
        manager = DataFetcherManager()
        df, _ = manager.get_daily_data_sync(code, days=90)
        if df is not None and not df.empty:
            logger.debug("analyze_trend(%s): loaded %d rows from DataFetcherManager", stock_code, len(df))
            return df
    except DataFetchError as e:
        logger.warning("analyze_trend(%s): DataFetcherManager failed: %s", stock_code, e)
    except Exception as e:
        logger.warning("analyze_trend(%s): DataFetcherManager unexpected error: %s", stock_code, e)

    return None
```

- [ ] **Step 1: Simplify _fetch_trend_data to direct network fetch**

Remove the SQLite lookup block and its `from src.storage import get_db` import. Keep only the `DataFetcherManager` path.

- [ ] **Step 2: Verify compilation**

```bash
python -m py_compile src/agent/tools/analysis_tools.py
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/tools/analysis_tools.py
git commit -m "perf: remove SQLite fallback from analysis_tools _fetch_trend_data"
```

---

### Task 8: Update data_tools.py — remove SQLite save_daily_data references

**Files:**
- Modify: `src/agent/tools/data_tools.py`

**Change:** Remove the `_get_db()` function and any calls to `_get_db().save_daily_data(...)` or `_get_db().get_*()` that operate on StockDaily.

Check lines around 338 and 454 where `_get_db()` is called. If these are saving to StockDaily, remove them. If they're for AnalysisHistory or other B组 tables, keep them.

- [ ] **Step 1: Audit and remove StockDaily-related get_db calls**

```bash
# First check what _get_db() is used for at those lines
```

For each call that references StockDaily operations (save_daily_data, get_data_range, etc.), remove the call. If the function itself becomes unused, remove the `_get_db` helper.

- [ ] **Step 2: Verify compilation**

```bash
python -m py_compile src/agent/tools/data_tools.py
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/tools/data_tools.py
git commit -m "perf: remove StockDaily-dependent get_db calls from data_tools.py"
```

---

### Task 9: Update history_service.py — remove NewsIntel-dependent methods

**Files:**
- Modify: `src/services/history_service.py`

**Change:** Remove `get_news_intel()` (lines 315-347), `get_news_intel_by_record_id()` (lines 349-374), `_fallback_news_by_analysis_context()` (lines 376-427), and `resolve_and_get_news()` (lines 186-205). These all query the `NewsIntel` table.

Keep `get_history_list()`, `resolve_and_get_detail()`, `_record_to_detail_dict()`, `get_markdown_report()`, etc. — these use `AnalysisHistory` which is B组.

- [ ] **Step 1: Remove news intel methods**

Delete `get_news_intel`, `get_news_intel_by_record_id`, `_fallback_news_by_analysis_context`, and `resolve_and_get_news` method bodies.

- [ ] **Step 2: Verify compilation**

```bash
python -m py_compile src/services/history_service.py
```

- [ ] **Step 3: Commit**

```bash
git add src/services/history_service.py
git commit -m "perf: remove NewsIntel-dependent methods from history_service.py"
```

---

### Task 10: Update history_loader.py — remove SQLite lookup path

**Files:**
- Modify: `src/services/history_loader.py`

**Change:** Remove the "DB lookup" code block (lines ~149-158) that queries `get_db().get_data_range()`. Keep only the DataFetcherManager network fallback.

The function `load_history_df` currently has two paths:
1. DB lookup (to be removed)
2. Network fallback via `DataFetcherManager` (to keep)

Remove imports of `get_db` and any DB-related helpers.

- [ ] **Step 1: Remove DB lookup path from load_history_df**

Edit `history_loader.py` — remove the DB section and the `from src.storage import get_db` import. Change the function to always use the network path.

- [ ] **Step 2: Verify compilation**

```bash
python -m py_compile src/services/history_loader.py
```

- [ ] **Step 3: Commit**

```bash
git add src/services/history_loader.py
git commit -m "perf: remove SQLite lookup path from history_loader.py"
```

---

### Task 11: Simplify pipeline_executor.py — remove ensure_agent_history cache logic

**Files:**
- Modify: `src/core/pipeline_executor.py`

**Change:** The `ensure_agent_history` method (lines 563-582) reads from SQLite and writes to SQLite. Since we're removing the cache, simplify it to just fetch from network. Or if nothing calls it, remove it entirely.

```bash
grep -rn "ensure_agent_history" /Users/ming/Desktop/daily_stock_analysis/src/ --include="*.py"
```
If no callers, delete the method. If callers exist, change to:
```python
async def ensure_agent_history(self, code: str, min_days: int = 240) -> None:
    """Ensure at least min_days of K-line history available. Always fetches from network."""
    try:
        df, source = await self.fetcher_manager.get_daily_data(code, days=min_days)
        if df is not None and not df.empty:
            logger.info("[%s] Fetched %d rows of history (source: %s)", code, len(df), source)
        else:
            logger.warning("[%s] History fetch returned empty", code)
    except Exception as e:
        logger.warning("[%s] History fetch failed: %s", code, e)
```

Also check `pipeline_executor.py` for any other `save_daily_data_async` or `get_data_range_async` calls.

- [ ] **Step 1: Simplify ensure_agent_history**

Remove SQLite read/write, keep only network fetch.

- [ ] **Step 2: Verify compilation**

```bash
python -m py_compile src/core/pipeline_executor.py
```

- [ ] **Step 3: Commit**

```bash
git add src/core/pipeline_executor.py
git commit -m "perf: simplify ensure_agent_history to direct network fetch"
```

---

### Task 12: Update market_analyzer.py — remove get_db/StockDaily refs

**Files:**
- Modify: `src/market_analyzer.py`

**Change:** Remove imports and calls to `get_db()` / `StockDaily` for k-line data. Replace with `DataFetcherManager.get_daily_data` if needed.

- [ ] **Step 1: Audit and fix market_analyzer.py references to get_db/StockDaily**

Search for `get_db`, `StockDaily`, `get_data_range` in market_analyzer.py and replace k-line queries with `DataFetcherManager`.

- [ ] **Step 2: Verify compilation**

```bash
python -m py_compile src/market_analyzer.py
```

- [ ] **Step 3: Commit**

```bash
git add src/market_analyzer.py
git commit -m "perf: remove SQLite k-line refs from market_analyzer.py"
```

---

## Phase 2: Agent Search Tool Enhancement

### Task 13: Add trafilatura dependency and full-text extraction to search_tools.py

**Files:**
- Modify: `src/agent/tools/search_tools.py`
- Modify: `requirements.txt`, `requirements-ci.txt`

**Step 1: Add trafilatura to requirements files:**

```
# Add to requirements.txt and requirements-ci.txt:
trafilatura>=1.6.0
```

- [ ] **Step 1.1: Add trafilatura to requirements.txt**

- [ ] **Step 1.2: Add trafilatura to requirements-ci.txt**

- [ ] **Step 1.3: Commit requirements changes**

```bash
git add requirements.txt requirements-ci.txt
git commit -m "deps: add trafilatura for full-text extraction"
```

**Step 2: Add full-text extraction helper to search_tools.py:**

Add a new helper function that takes a URL and returns extracted text (or None). Also add `MAX_EXTRACT_URLS = 3` constant.

```python
import trafilatura

_MAX_EXTRACT_URLS = 3

def _extract_full_text(url: str, timeout: int = 10) -> Optional[str]:
    """Fetch and extract readable text from a URL using trafilatura.
    
    Returns None on timeout, network error, or paywalled content.
    """
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded, include_links=False, include_images=False)
        return text.strip() if text else None
    except Exception as exc:
        logger.debug("Full-text extraction failed for %s: %s", url, exc)
        return None
```

- [ ] **Step 2.1: Add `_extract_full_text` helper** and `_MAX_EXTRACT_URLS` constant

**Step 3: Integrate extraction into the search results:**

Modify `_handle_search_stock_news` to call `_extract_full_text` on up to 3 results and include the extracted text in the response:

```python
def _handle_search_stock_news(stock_code: str, stock_name: str) -> dict:
    """Search latest news for a stock with full-text extraction."""
    service = _get_search_service()
    if not service.is_available:
        return {"error": "No search engine available (no API keys configured)"}

    response = service.search_stock_news(stock_code, stock_name, max_results=5)

    if not response.success:
        return {
            "query": response.query,
            "success": False,
            "error": response.error_message,
        }

    results = []
    for i, r in enumerate(response.results):
        item = {
            "title": r.title,
            "url": r.url,
            "source": r.source,
            "published_date": r.published_date,
        }
        # Extract full text for first _MAX_EXTRACT_URLS results
        if i < _MAX_EXTRACT_URLS:
            full_text = _extract_full_text(r.url)
            item["extracted"] = full_text is not None
            item["full_text_snippet"] = full_text[:500] if full_text else ""
            item["full_text"] = full_text or ""
        else:
            item["extracted"] = False
            item["full_text_snippet"] = r.snippet or ""
            item["full_text"] = ""
        results.append(item)

    return {
        "query": response.query,
        "provider": response.provider,
        "success": True,
        "results_count": len(results),
        "results": results,
    }
```

- [ ] **Step 3.1: Modify _handle_search_stock_news** to include full-text extraction

- [ ] **Step 4: Verify compilation**

```bash
python -m py_compile src/agent/tools/search_tools.py
```

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools/search_tools.py
git commit -m "feat: add trafilatura full-text extraction to search_stock_news tool"
```

---

### Task 14: Add LLM structured summary to search_tools.py

**Files:**
- Modify: `src/agent/tools/search_tools.py`

**Change:** Add LLM-powered structured analysis after full-text extraction. The summary extracts key points, key data, and ticker impact from the combined extracted texts.

**Step 1: Add LLM analysis helper:**

```python
def _analyze_articles_with_llm(
    stock_code: str,
    stock_name: str,
    articles: List[dict],
) -> Optional[dict]:
    """Feed extracted article texts to LLM for structured analysis.
    
    Returns dict with key_points, key_data, and ticker_impact.
    Returns None if LLM call fails or no full text is available.
    """
    texts_with_content = [a for a in articles if a.get("full_text")]
    if not texts_with_content:
        return None

    # Build prompt
    combined = "\n\n---\n\n".join(
        f"Title: {a['title']}\n{a['full_text'][:2000]}"
        for a in texts_with_content[:_MAX_EXTRACT_URLS]
    )

    prompt = f"""Analyze the following news articles about {stock_name} ({stock_code}) 
and produce a structured analysis in JSON format:

{combined}

Respond with ONLY a JSON object with these fields:
{{
  "key_points": ["point 1", "point 2", ...],
  "key_data": {{"metric_name": "value", ...}},
  "ticker_impact": [
    {{"ticker": "{stock_code}", "sentiment": "bullish/bearish/neutral", "confidence": 0.0-1.0, "reason": "..."}}
  ]
}}
"""
    try:
        from src.analyzer import Analyzer
        from src.config import get_config
        analyzer = Analyzer(config=get_config())
        raw = analyzer.generate_text(prompt, max_tokens=800, temperature=0.1)
        # Expect JSON, try to parse
        import json
        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0]
        result = json.loads(cleaned.strip())
        return {
            "key_points": result.get("key_points", []),
            "key_data": result.get("key_data", {}),
            "ticker_impact": result.get("ticker_impact", []),
        }
    except Exception as exc:
        logger.debug("LLM article analysis failed: %s", exc)
        return None
```

- [ ] **Step 1: Add `_analyze_articles_with_llm` function**

**Step 2: Integrate LLM analysis into search results:**

Add the LLM analysis to the results returned by `_handle_search_stock_news`:

```python
# After building results list:
llm_analysis = _analyze_articles_with_llm(stock_code, stock_name, results)

return {
    "query": response.query,
    "provider": response.provider,
    "success": True,
    "results_count": len(results),
    "results": results,
    "llm_analysis": llm_analysis,
}
```

- [ ] **Step 2.1: Add `llm_analysis` to the returned dict**

- [ ] **Step 3: Verify compilation**

```bash
python -m py_compile src/agent/tools/search_tools.py
```

- [ ] **Step 4: Commit**

```bash
git add src/agent/tools/search_tools.py
git commit -m "feat: add LLM structured summary to search_stock_news tool"
```

---

## Phase 3: End-to-End Regression Test

### Task 15: Write VCR-based e2e test

**Files:**
- Create: `tests/test_e2e_pipeline.py`
- Create: `tests/fixtures/stocks_test.yaml`
- Modify: `pytest.ini` or `pyproject.toml` (if needed for markers)
- Modify: `requirements-ci.txt` (add `pytest-vcr`)

**Step 0: Add pytest-vcr to requirements:**

```
# Add to requirements-ci.txt:
pytest-vcr>=1.0.2
```

- [ ] **Step 0.1: Add pytest-vcr**

```bash
git add requirements-ci.txt
git commit -m "deps: add pytest-vcr for e2e test recording"
```

**Step 1: Create test fixture config:**

`tests/fixtures/stocks_test.yaml`:
```yaml
stocks:
  - code: "600519"
    name: "贵州茅台"
    market: "cn"
```

- [ ] **Step 1.1: Create stocks_test.yaml**

**Step 2: Create e2e test with VCR recording:**

`tests/test_e2e_pipeline.py`:
```python
"""End-to-end pipeline tests with VCR-recorded HTTP interactions."""

import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.network


@pytest.mark.vcr
class TestFullPipeline:
    """Test full analysis pipeline with recorded HTTP."""

    @pytest.fixture
    def stocks_config(self):
        """Load test stock config."""
        import yaml
        with open(FIXTURES_DIR / "stocks_test.yaml") as f:
            return yaml.safe_load(f)

    @pytest.fixture
    def fetcher_manager(self):
        from data_provider import DataFetcherManager
        return DataFetcherManager()

    @pytest.mark.asyncio
    async def test_kline_fetch(self, fetcher_manager):
        """Verify k-line data can be fetched from network."""
        df, source = await fetcher_manager.get_daily_data("600519", days=30)
        assert df is not None
        assert not df.empty
        assert len(df) > 0
        assert "close" in df.columns or "收盘" in str(df.columns)
        logger.info("K-line fetched from %s: %d rows", source, len(df))

    @pytest.mark.asyncio
    async def test_full_analysis_flow(self, stocks_config, fetcher_manager):
        """Verify full collection pipeline produces structured output."""
        from src.core.pipeline_data_collector import StockDataCollector
        from src.search_service import SearchService
        from src.stock_analyzer import StockTrendAnalyzer
        from src.config import get_config
        from src.storage import get_db

        config = get_config()
        db = get_db()
        search = SearchService()
        analyzer = StockTrendAnalyzer(config)

        collector = StockDataCollector(
            config=config,
            fetcher_manager=fetcher_manager,
            db=db,
            search_service=search,
            analyzer=analyzer,
            trend_analyzer=analyzer,
        )

        code = stocks_config["stocks"][0]["code"]
        result = await collector.collect(code)

        # Verify all data fields are populated
        assert result.stock_name == "贵州茅台" or "茅台" in result.stock_name
        assert result.realtime_quote is not None
        assert result.trend_result is not None
        assert result.final_news is not None
        assert len(result.final_news) > 0

    def test_search_stock_news_with_extraction(self):
        """Verify search tool returns full-text extraction results."""
        from src.agent.tools.search_tools import _handle_search_stock_news

        result = _handle_search_stock_news("600519", "贵州茅台")
        assert result.get("success", False)
        assert len(result.get("results", [])) > 0

        # Verify extraction attempted
        first_result = result["results"][0]
        assert "extracted" in first_result
        assert "full_text_snippet" in first_result

    @pytest.mark.asyncio
    async def test_market_review(self):
        """Verify market review report contains expected sections."""
        from src.market_analyzer import MarketAnalyzer
        from src.config import get_config

        config = get_config()
        analyzer = MarketAnalyzer(config)
        report = await analyzer.run()
        assert report is not None
        assert len(report) > 0
```

- [ ] **Step 2.1: Create test_e2e_pipeline.py**

**Step 3: Initial VCR recording run:**

```bash
cd /Users/ming/Desktop/daily_stock_analysis
pip install trafilatura pytest-vcr
pytest tests/test_e2e_pipeline.py --record-mode=once -v
```
Expected: tests run against real APIs, VCR records cassettes to `tests/cassettes/`.

- [ ] **Step 3.1: Run with recording**

**Step 4: Verify replay works (no network):**

```bash
pytest tests/test_e2e_pipeline.py -v
```
Expected: tests run against cassettes, no real HTTP calls.

- [ ] **Step 4.1: Run replay test**

**Step 5: Strip API keys from cassettes:**

Check `tests/cassettes/*.yaml` for any headers containing API keys. If found, add a `before_record_request` callback to `conftest.py` or edit the cassette YAML to replace sensitive values with placeholders.

- [ ] **Step 5.1: Verify cassettes are safe to commit**

- [ ] **Step 6: Commit**

```bash
git add tests/test_e2e_pipeline.py tests/fixtures/stocks_test.yaml tests/cassettes/
git commit -m "test: add VCR-based e2e tests for full pipeline and search enhancement"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** Does "SQLite cache removal (A组)" cover StockDaily, NewsIntel, FundamentalSnapshot? → Tasks 1-12.
- [ ] **Spec coverage:** Does "search enhancement" include trafilatura extraction + LLM analysis? → Tasks 13-14.
- [ ] **Spec coverage:** Does "e2e test" cover pipeline + search tool + market review? → Task 15.
- [ ] **Placeholder scan:** No TBD, TODO, "implement later", or empty steps in this plan.
- [ ] **Type consistency:** All method signatures referenced in later tasks match earlier task definitions.
