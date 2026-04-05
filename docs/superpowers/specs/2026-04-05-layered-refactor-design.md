# Phased Refactoring Design: Code Quality & Testing (Phase 1)

**Date**: 2026-04-05
**Status**: Draft
**Author**: Claude Code
**Scope**: data_provider/, src/core/, src/services/, src/repositories/, tests/

---

## 1. Motivation

Phase 1 addresses three high-impact, low-risk issues: code duplication, parameter naming inconsistency, and missing integration tests. These improvements are behaviorally neutral — they don't change how the system works, they make it easier to verify that it works.

Future phases (exception hierarchy, config validation, magic numbers, N+1 fixes) are out of scope for this spec and will be designed separately.

| # | Issue | Effort |
|---|-------|--------|
| 1 | 10-15% code duplication across data providers and services | 5-8h |
| 2 | Parameter naming inconsistency (`code` vs `stock_code`) | 4-6h |
| 3 | Missing integration tests (E2E, concurrency, fault scenarios) | 8-12h |

**Delivery timeline**:
- Week 1: Code dedup (5-8h) + parameter naming (4-6h)
- Week 2: Integration tests (8-12h)

---

## 2. Code Deduplication (10-15% → shared utilities)

### 2.1 Hotspot: Real-time quote fetch loops

**Location**: `data_provider/base.py` — `get_realtime_quote()` method

Current pattern repeats 4 times (US index, US stock, HK, ETF):
```python
for fetcher in self._fetchers:
    if _is_us_market(stock_code):
        for fetcher in self._fetchers:  # US-specific
            try:
                data = fetcher.get_realtime_quote(...)
                if data: return data
            except Exception: pass
    # ... same pattern for HK, ETF
```

**Extracted utility**:
```python
def _fetch_with_fallback(
    sources: list,
    stock_code: str,
    fetch_fn: str,
    **kwargs
) -> Optional[dict]:
    """Try each source's fetch_fn, log failures, return first success."""
    for source in sources:
        try:
            fn = getattr(source, fetch_fn)
            result = fn(stock_code, **kwargs)
            if result:
                return result
        except Exception as e:
            logger.debug("[%s] %s via %s failed: %s",
                         type(source).__name__, stock_code, fetch_fn, e)
            continue
    return None
```

**Impact**: Reduces ~60 lines of repeated try/except loops to ~4 call sites.

### 2.2 Hotspot: Notification sender wrappers

**Location**: `src/notification_sender/` — 13 sender files

Nearly every sender follows the same pattern:
```python
try:
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    logger.info("[Sender] notification sent successfully")
    return True
except Exception:
    logger.error("[Sender] failed to send notification")
    return False
```

**Extracted base class method** in `src/notification_sender/base.py` (new):
```python
class BaseNotificationSender:
    def _send_request(
        self,
        method: str = "POST",
        url: Optional[str] = None,
        payload: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> bool:
        """Generic HTTP send with logging and error handling."""
        ...
```

Individual senders inherit and call `self._send_request(...)` instead of raw `requests.post`.

### 2.3 Hotspot: DataFrame standardization

**Location**: `data_provider/base.py` — `_clean_data()` method, plus inline duplicates in individual fetchers

Common cleaning logic (column selection, type conversion, NaN removal, sorting) is called from the base class but also copy-pasted in fetcher-specific code.

**Fix**: Ensure all fetchers call `self._clean_data(df)` from the base class; remove inline duplicates.

### 2.4 New File: `data_provider/_query_utils.py`

Extracted query helpers for repeated patterns:
```python
def batch_select(conn: sqlite3.Connection, table: str,
                 stock_codes: list, where_column: str = "code") -> list:
    """Execute WHERE code IN (...) query."""

def batch_insert(conn: sqlite3.Connection, table: str,
                 columns: list, rows: list) -> int:
    """Execute executemany + single commit."""
```

This partially addresses N+1 but is scoped to deduplication only — full N+1 audit is out of Phase 1.

---

## 3. Parameter Naming: `code` → `stock_code`

### 3.1 Scope

All public function signatures where `code` represents a stock ticker identifier.

### 3.2 Rules

- **Public API**: use `stock_code`
- **Internal helpers** with scope < 10 lines: short names acceptable (`c`, `normalized`)
- **Do not rename** local variables, loop variables, or dict keys
- Variables named `code` that are NOT stock codes (e.g. `country_code`, `status_code`, `error_code`) are untouched

### 3.3 Files Affected

Based on grep analysis, ~20 files with ~80 parameter renames:

| File | Approximate renames |
|------|-------------------|
| `data_provider/base.py` | 15 |
| `data_provider/akshare_fetcher.py` | 8 |
| `data_provider/efinance_fetcher.py` | 6 |
| `data_provider/tushare_fetcher.py` | 5 |
| `data_provider/yfinance_fetcher.py` | 5 |
| `data_provider/pytdx_fetcher.py` | 4 |
| `src/stock_analyzer.py` | 5 |
| `src/analyzer.py` | 8 |
| `src/core/pipeline.py` | 6 |
| `src/core/market_review.py` | 3 |
| `src/services/history_service.py` | 4 |
| `src/services/stock_service.py` | 3 |
| Other services/repositories | ~10 |

### 3.4 Migration Approach

1. Identify all public function signatures with `code: str` parameter
2. Rename to `stock_code: str`
3. Update internal references within the function body
4. Update callers (IDE-assisted rename where possible)
5. Verify with `py_compile` on all changed files

---

## 4. Integration Test Suite

### 4.1 Test Modules

| Module | Coverage | External Deps | Mark |
|--------|----------|--------------|------|
| `tests/test_e2e_pipeline.py` | Full analysis pipeline | LLM mocked, data providers real or mocked | `@pytest.mark.network` |
| `tests/test_concurrent_access.py` | Simultaneous writes, SQLite behavior | None | `pytest.mark.no_network` |
| `tests/test_fault_scenarios.py` | All sources down, bad config, timeouts | Fully mocked | `pytest.mark.no_network` |

### 4.2 `test_e2e_pipeline.py`

Tests the full pipeline path: fetch → analyze → render → (optionally) notify.

```python
def test_full_pipeline_single_stock():
    """Complete analysis for one known stock (600519)."""
    # Real or mocked data providers, mocked LLM
    result = run_pipeline("600519")
    assert result.report is not None
    assert result.stock_code == "600519"
    assert result.error is None

def test_pipeline_handles_mixed_markets():
    """Analysis with A-share, HK, and US codes in one batch."""

def test_market_review_mode():
    """--market-review flag produces structured market summary."""
```

### 4.3 `test_concurrent_access.py`

Tests concurrent access to shared resources (SQLite, portfolio state).

```python
def test_concurrent_db_writes():
    """Multiple threads writing analysis results simultaneously."""
    # Verify no SQLITE_BUSY or SQLITE_LOCKED deadlock

def test_pipeline_read_while_writing():
    """Read analysis result while another thread is writing it."""

def test_concurrent_portfolio_updates():
    """Multiple threads adding trades to same portfolio."""
```

### 4.4 `test_fault_scenarios.py`

Tests failure recovery paths.

```python
def test_all_data_sources_unavailable():
    """When every provider fails, emit structured error, don't crash."""
    # Mock all fetchers to raise
    result = run_pipeline("600519")
    assert result.error is not None
    # Should NOT raise; should return error result

def test_partial_data_source_failure():
    """When first provider fails but fallback succeeds, pipeline completes."""
    # Mock primary to fail, fallback to succeed
    result = run_pipeline("600519")
    assert result.report is not None  # Fallback worked

def test_invalid_stock_code():
    """Invalid stock code format produces clear error."""

def test_llm_service_unavailable():
    """LLM API down produces structured error, not raw traceback."""
```

### 4.5 Test Infrastructure

- Use `unittest.mock.patch` to patch individual fetchers
- Use `pytest.mark.parametrize` for multi-stock test cases
- Use `concurrent.futures.ThreadPoolExecutor` for concurrency tests
- All tests must pass with `python -m pytest -m "not network"` offline
- Network-dependent tests use `@pytest.mark.network` for opt-in

---

## 5. Commit Structure

### Week 1: Code Quality

1. `refactor: deduplicate real-time quote fetch loops in data_provider/base.py`
2. `refactor: extract BaseNotificationSender with _send_request method`
3. `refactor: remove duplicate DataFrame cleaning in fetchers`
4. `refactor: extract batch query helpers to data_provider/_query_utils.py`
5. `refactor: rename code→stock_code in data_provider/ public APIs`
6. `refactor: rename code→stock_code in src/ public APIs`

### Week 2: Integration Tests

7. `test: add e2e pipeline tests`
8. `test: add concurrent access tests`
9. `test: add fault scenario tests`

---

## 6. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Parameter rename misses a caller | Low | Medium — caught by import + py_compile | Compile all changed files |
| Extracted base method breaks subclass behavior | Low | Medium | Each sender tested individually after extraction |
| Test flakiness on CI (timing-dependent) | Medium | Low — tests are skipped by default | Use `pytest.mark.network` + generous timeouts |

## 7. Rollback

Each commit is independent. Full branch revert restores previous state with zero data loss — no DB or schema changes.
