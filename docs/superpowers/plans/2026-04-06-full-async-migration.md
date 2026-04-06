# Full Async Migration: I/O Bound to Async-First

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple hard-coded dependencies in Pipeline and DataFetchers, then replace all sync I/O (`requests`, `ThreadPoolExecutor`, `smtplib`, per-request `httpx.Client()`) with `async/await` using `httpx.AsyncClient`.

**Architecture:** Three-phase. Phase 0 decouples Pipeline → Analyzer/Notifier and DataFetcher → global config via factory/DI injection. Phase 1 migrates notification senders and data providers to async-first with sync wrappers. Phase 2 converts callers (`main.py`, `src/core/pipeline.py`) to `async def`. Phase 3 adds email async + cleanup. All changes retain sync-compat adapters so `python main.py` continues working without modification.

**Tech Stack:** Python `asyncio`, `httpx` (async client, SOCKS proxy support), `anyio` (async-safe alternatives), `aiosmtplib` (async SMTP).

---

## File Map

### New Files
- `src/notification_sender/async_base.py` — Async notification sender base class, shared retry logic
- `data_provider/_async_client.py` — Singleton `httpx.AsyncClient` with connection pooling, proxy, timeout config
- `data_provider/_async_utils.py` — `async_timeout`, `async_sleep`, `gather_with_concurrency` utilities

### Modified Files (Phase 0)
- `src/core/pipeline.py` — Add `analyzer` and `notifier` factory support, DI via `__init__` params
- `data_provider/base.py` — Add config injection to individual fetchers, remove direct `get_config()` calls from `get_realtime_quote` and related methods

### Modified Files (Phase 1)
- `src/notification_sender/*.py` (12 files) — Convert each HTTP sender to use `httpx.AsyncClient` + async methods
- `src/notification.py` — Convert send pipeline from `ThreadPoolExecutor` to `asyncio.gather`
- `data_provider/akshare_fetcher.py` — Convert per-request `httpx.Client()` to shared async client
- `data_provider/tushare_fetcher.py` — Same as above
- `data_provider/efinance_fetcher.py` — Convert `requests` to `httpx.AsyncClient` (note: wraps efinance library)

### Modified Files (Phase 2)
- `src/core/pipeline.py` — Convert `run_pipeline`, `_process_single_stock` to async
- `main.py` — `asyncio.run(main())` entry point
- `src/stock_analyzer.py` — Indicators remain sync (CPU-bound), no change needed

### Modified Files (Phase 3, conditional)
- `src/notification_sender/email_sender.py` — Add `aiosmtplib`, wrap sync fallback

---

# Phase 0: Decoupling — DI for Pipeline and Fetchers

## Task 0.1: Pipeline → Analyzer/Notifier dependency injection

**Files:**
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: Add factory parameters to `__init__`**

Change `StockAnalysisPipeline.__init__` to accept optional factory parameters while maintaining full backward compat:

```python
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None,
        source_message: Optional[BotMessage] = None,
        query_id: Optional[str] = None,
        query_source: Optional[str] = None,
        save_context_snapshot: Optional[bool] = None,
        # ★ NEW: factory-based DI (optional, defaults to concrete instantiation)
        analyzer_factory: Optional[Any] = None,  # Callable[[Config], BaseAnalyzer]
        notifier_factory: Optional[Any] = None,  # Callable[[BotMessage], NotificationService]
    ):
```

- [ ] **Step 2: Use factories or fall back to defaults**

Replace these lines:
```python
        self.analyzer = GeminiAnalyzer(config=self.config)
        self.notifier = NotificationService(source_message=source_message)
```

With:
```python
        if analyzer_factory is not None:
            self.analyzer = analyzer_factory(self.config)
        else:
            self.analyzer = GeminiAnalyzer(config=self.config)

        if notifier_factory is not None:
            self.notifier = notifier_factory(source_message=source_message)
        else:
            self.notifier = NotificationService(source_message=source_message)
```

This is backward compatible — existing callers that don't pass factories get identical behavior.

---

## Task 0.2: DataFetcher → Config decoupling

**Files:**
- Modify: `data_provider/base.py` (BaseFetcher class)

**Design note:** There are ~30+ sites in `data_provider/base.py` and individual fetcher files that call `from src.config import get_config(); config = get_config()` individually. A full refactor to pass config into every fetcher constructor would touch too many lines at once. Instead, use a two-level approach:

1. **Add `config` parameter to `BaseFetcher.__init__`** (defaulting to `None` → lazy `get_config()`)
2. **Add a class-level accessor `_get_config()`** that returns `self._config or get_config()`
3. **In pipeline.py**, construct the `DataFetcherManager` with an explicit `config` argument that it passes to each fetcher

This approach:
- Makes fetcher construction explicitly injectable
- Preserves backward compat (existing fetchers without config param keep working)
- Gives a clear migration path: callers can gradually update to pass config

- [ ] **Step 1: Add config injection to BaseFetcher**

In `data_provider/base.py`, add to `BaseFetcher.__init__`:

```python
    def __init__(self, config: Optional[Any] = None):
        """
        Args:
            config: Config object. If None, falls back to global get_config().
        """
        self._config = config
        ...
```

- [ ] **Step 2: Add `_get_config()` helper**

```python
    def _get_config(self):
        """Get config — uses injected config if available, falls back to global."""
        if self._config is not None:
            return self._config
        from src.config import get_config
        return get_config()
```

- [ ] **Step 3: Update DataFetcherManager to accept and forward config**

In `DataFetcherManager.__init__`, add optional config parameter:
```python
    def __init__(self, fetchers: Optional[List[BaseFetcher]] = None, config: Optional[Any] = None):
        ...
```

If config is provided and fetchers is not, pass it to default fetcher construction.

- [ ] **Step 4: Update pipeline.py to pass config to DataFetcherManager**

In pipeline.py where `DataFetcherManager()` is called:
```python
self.fetcher_manager = DataFetcherManager(config=self.config)
```

---

# Phase 1: Notification Senders Async-First

## Task 1: Extract `data_provider/_async_client.py` — Shared Async HTTP Client

**Files:**
- Create: `data_provider/_async_client.py`

- [ ] **Step 1: Create the async client module**

This module provides a singleton-like `httpx.AsyncClient` that is reused across all fetchers (fixes the per-request connection pool waste identified in code review).

```python
# data_provider/_async_client.py
"""Shared async HTTP client with connection pooling and SOCKS proxy support."""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx

from src.config import get_config

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_proxy: str | None = None


def _resolve_proxy() -> str | None:
    config = get_config()
    use_proxy = getattr(config, "use_proxy", False)
    if not use_proxy:
        return None
    host = getattr(config, "proxy_host", "127.0.0.1")
    port = getattr(config, "proxy_port", "10809")
    return f"socks5://{host}:{port}"


async def get_async_client(**kwargs) -> httpx.AsyncClient:
    """
    Get (or create) a shared httpx.AsyncClient.

    Connection pooling is shared across all callers, eliminating the
    per-request connect overhead from the old `with httpx.Client()` pattern.
    """
    global _client, _proxy

    proxy = _resolve_proxy()
    recreated = proxy != _proxy

    if recreated or _client is None or _client.is_closed:
        if _client and not _client.is_closed:
            await _client.aclose()
        _proxy = proxy
        _client = httpx.AsyncClient(
            proxy=proxy,
            timeout=kwargs.pop("timeout", httpx.Timeout(30.0, connect=10.0)),
            http2=False,  # akshare / sina don't need it
            **kwargs,
        )
    return _client


@asynccontextmanager
async def managed_async_client(**kwargs) -> AsyncGenerator[httpx.AsyncClient, None]:
    """
    Context manager version for callers that want guaranteed lifecycle management.

    Use this when the caller cannot guarantee cleanup (e.g. standalone scripts).
    """
    client = await get_async_client(**kwargs)
    try:
        yield client
    finally:
        _client = None  # Force recreation next call


async def close_async_client() -> None:
    """Explicitly close the shared client (for shutdown)."""
    global _client, _proxy
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
    _proxy = None
```

- [ ] **Step 2: Verify compile**

Run: `python -m py_compile data_provider/_async_client.py`
Expected: No output (success)

---

## Task 2: Convert Sina/Tencent realtime endpoints in akshare_fetcher.py to async

**Files:**
- Modify: `data_provider/akshare_fetcher.py` (~140 lines changed — replace `with httpx.Client()` with shared async client)

- [ ] **Step 1: Convert `_fetch_realtime_sina` to async**

The Sina realtime endpoint currently does:
```python
with httpx.Client() as client:
    response = client.get(url, headers=headers, timeout=10.0)
```

Change to:
```python
client = await get_async_client()
response = await client.get(url, headers=headers, timeout=10.0)
content = response.content.decode('gbk')
```

Import `get_async_client` from `._async_client` and `async def` the method. The full `_fetch_realtime_sina` signature changes from `def _fetch_realtime_sina(self, stock_code: str) -> Optional[dict]` to `async def _fetch_realtime_sina(self, stock_code: str) -> Optional[dict]`.

The exception block also updates — `classify_http_error(e)` still works on the caught exception.

- [ ] **Step 2: Convert `_fetch_realtime_tencent` to async**

Same pattern as Step 1. Replace `with httpx.Client()` with `await get_async_client()` and make the method `async def`.

- [ ] **Step 3: Verify compile**

Run: `python -m py_compile data_provider/akshare_fetcher.py`
Expected: No output (success)

---

## Task 3: Convert tushare_fetcher.py to async

**Files:**
- Modify: `data_provider/tushare_fetcher.py` (~5 lines changed)

- [ ] **Step 1: Convert `_api_post` to async**

The Tushare `_api_post` method currently does:
```python
with httpx.Client() as client:
    res = client.post(TUSHARE_API_URL, json=req_params, timeout=_timeout)
```

Change to:
```python
from ._async_client import get_async_client

async def _api_post(self, func_name: str, fields: str, **kwargs) -> dict:
    _timeout = kwargs.pop("timeout", 30)
    req_params = {"api_name": func_name, "params": kwargs, "fields": fields}
    client = await get_async_client()
    res = await client.post(TUSHARE_API_URL, json=req_params, timeout=_timeout)
    ...
```

- [ ] **Step 2: Verify compile**

Run: `python -m py_compile data_provider/tushare_fetcher.py`
Expected: No output (success)

---

## Task 4: Convert all HTTP notification senders to async

**Files:**
- Create: `src/notification_sender/async_base.py`
- Modify: `src/notification_sender/astrbot_sender.py`
- Modify: `src/notification_sender/custom_webhook_sender.py`
- Modify: `src/notification_sender/discord_sender.py`
- Modify: `src/notification_sender/feishu_sender.py`
- Modify: `src/notification_sender/pushover_sender.py`
- Modify: `src/notification_sender/pushplus_sender.py`
- Modify: `src/notification_sender/serverchan3_sender.py`
- Modify: `src/notification_sender/slack_sender.py`
- Modify: `src/notification_sender/telegram_sender.py`
- Modify: `src/notification_sender/wechat_sender.py`

- [ ] **Step 1: Create `async_base.py` — Shared async notification retry + client**

```python
# src/notification_sender/async_base.py
"""Async retry and HTTP client utilities for notification senders."""
import asyncio
import logging
from typing import AsyncContextManager, Any

import httpx

from src.config import get_config
from src.notification import NOTIFICATION_DEFAULT_TIMEOUT_SEC, NOTIFICATION_DEFAULT_MAX_RETRIES

logger = logging.getLogger(__name__)

# Module-level shared client to avoid per-call connect overhead
_async_http_client: httpx.AsyncClient | None = None


async def get_sender_http_client() -> httpx.AsyncClient:
    """Get a shared httpx.AsyncClient for notification HTTP requests."""
    global _async_http_client
    if _async_http_client is None or _async_http_client.is_closed:
        config = get_config()
        proxy = None
        if getattr(config, "use_proxy", False):
            host = getattr(config, "proxy_host", "127.0.0.1")
            port = getattr(config, "proxy_port", "10809")
            proxy = f"socks5://{host}:{port}"
        _async_http_client = httpx.AsyncClient(
            proxy=proxy,
            verify=getattr(config, "webhook_verify_ssl", True),
            timeout=httpx.Timeout(
                getattr(config, "notification_timeout_sec", NOTIFICATION_DEFAULT_TIMEOUT_SEC),
                connect=10.0,
            ),
        )
    return _async_http_client


async def close_sender_http_client() -> None:
    """Close the shared HTTP client."""
    global _async_http_client
    if _async_http_client and not _async_http_client.is_closed:
        await _async_http_client.aclose()
    _async_http_client = None


async def send_with_retry(
    send_fn,
    channel_name: str,
    max_retries: int = NOTIFICATION_DEFAULT_MAX_RETRIES,
) -> bool:
    """Send with exponential backoff retry, async version."""
    attempt = 0
    while True:
        try:
            result = await send_fn()
            if result:
                return True
        except Exception as e:
            if attempt < max_retries:
                delay = min(0.5 * (2 ** attempt), 30.0)
                logger.warning(
                    f"{channel_name} 发送失败，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries}): {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"{channel_name} 发送失败，已重试 {max_retries} 次: {e}")
        attempt += 1
        if attempt > max_retries:
            return False
```

- [ ] **Step 2: Convert each HTTP sender to async**

For each sender file, apply this transformation pattern:

**Before:**
```python
def send_to_xxx(self, content: str) -> bool:
    response = requests.post(url, json=payload, timeout=self._timeout)
    if response.status_code == 200:
        return True
    return False
```

**After:**
```python
async def send_to_xxx(self, content: str) -> bool:
    from .async_base import get_sender_http_client
    client = await get_sender_http_client()
    response = await client.post(url, json=payload)
    if response.status_code == 200:
        return True
    return False
```

Key points per sender:
- **astrbot_sender.py**: `send_to_astrbot` → `async def`, remove `requests.post(..., timeout=...)`
- **custom_webhook_sender.py**: `send_to_custom` and `_send_custom_webhook_image` → `async def`
- **discord_sender.py**: `send_to_discord` → `async def`, both webhook and bot paths
- **feishu_sender.py**: `send_to_feishu` → `async def`
- **pushover_sender.py**: `send_to_pushover` → `async def`
- **pushplus_sender.py**: `send_to_pushplus` → `async def`
- **serverchan3_sender.py**: `send_to_serverchan3` → `async def`
- **slack_sender.py**: `send_to_slack`, `_send_slack_image` → `async def`
- **telegram_sender.py**: `send_to_telegram`, `_send_telegram_photo` → `async def`. **IMPORTANT**: Telegram sender has its own retry loop internally. Remove the internal retry and let the caller (`_send_channel_with_retry` → async version) handle retries via `send_with_retry()`.
- **wechat_sender.py**: `send_to_wechat`, `_send_wechat_image` → `async def`. Keep `_compress_image` as sync (CPU-bound PIL work).

- [ ] **Step 3: Verify compile for all sender files**

Run: `python -m py_compile src/notification_sender/$sender.py` for each file
Expected: No output (success)

---

## Task 5: Migrate NotificationService pipeline from ThreadPoolExecutor to asyncio.gather

**Files:**
- Modify: `src/notification.py`

- [ ] **Step 1: Convert `send()` method to async**

Change `def send(...)` to `async def send(...)`. Replace:

```python
# OLD: ThreadPoolExecutor
max_workers = min(4, len(channel_tasks)) if channel_tasks else 1
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    futures = {
        executor.submit(self._send_channel_with_retry, ch, cnt, img): ch
        ...
    }
```

With:
```python
# NEW: asyncio.gather
coros = [
    self._send_channel_async(ch, cnt, img)
    for ch, cnt, img, _ in channel_tasks
]
results = await asyncio.gather(*coros, return_exceptions=True)

success_count = 0
fail_count = 0
for i, res in enumerate(results):
    channel = channel_tasks[i][0]
    channel_name = ChannelDetector.get_channel_name(channel)
    if isinstance(res, Exception):
        logger.error(f"{channel_name} 发送异常: {res}")
        fail_count += 1
    elif res:
        success_count += 1
    else:
        fail_count += 1
```

- [ ] **Step 2: Convert helper methods to async**

`_send_channel_with_retry` and `_send_single_channel` become `async def`. Replace `time.sleep()` with `asyncio.sleep()`.

```python
async def _send_channel_with_retry(self, channel, content, image_bytes=None) -> bool:
    max_retries = self._notification_max_retries
    for attempt in range(max_retries + 1):
        try:
            result = await self._send_single_channel(channel, content, image_bytes)
            if result:
                return True
        except Exception as e:
            channel_name = ChannelDetector.get_channel_name(channel)
            if attempt < max_retries:
                delay = min(0.5 * (2 ** attempt), 30.0)
                logger.warning(f"{channel_name} 发送失败，{delay:.1f}s 后重试: {e}")
                await asyncio.sleep(delay)
            else:
                logger.error(f"{channel_name} 发送失败，已重试 {max_retries} 次: {e}")
    return False

async def _send_single_channel(self, channel, content, image_bytes=None) -> bool:
    # Same dispatch as before, but all targets are now async def
    ...
```

- [ ] **Step 3: Add sync wrapper for back-compat (main.py still calls sync)**

```python
def send_sync(self, content, email_stock_codes=None, email_send_to_all=False) -> bool:
    """Sync wrapper for callers that haven't migrated to async yet."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # Already in async context — shouldn't happen for main.py callers
        return loop.run_until_complete(
            self.send(content, email_stock_codes, email_send_to_all)
        )
    except RuntimeError:
        # No running loop — create one (sync caller)
        return asyncio.run(
            self.send(content, email_stock_codes, email_send_to_all)
        )
```

- [ ] **Step 4: Remove unused imports**

Remove `from concurrent.futures import ThreadPoolExecutor, as_completed` since it's no longer used.

- [ ] **Step 5: Verify compile**

Run: `python -m py_compile src/notification.py`
Expected: No output (success)

---

# Phase 2: Caller Migration — Full Async Pipeline

## Task 6: Add `async_timeout` and `anyio` to requirements, add `NOTIFICATION_DEFAULT_MAX_RETRIES`

**Files:**
- Modify: `requirements.txt`
- Modify: `src/notification.py`

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:
```
aiosmtplib>=3.0.0        # Async SMTP for email notifications
anyio>=4.0.0             # Async-safe utilities
trio>=0.24.0             # Optional: alternative async backend
```

- [ ] **Step 2: Add shared constant for retries**

In `src/notification.py` (next to `NOTIFICATION_DEFAULT_TIMEOUT_SEC`):
```python
NOTIFICATION_DEFAULT_MAX_RETRIES = 2
```

---

## Task 7: Convert pipeline.py to async

**Files:**
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: Convert `run_pipeline()` to async**

The pipeline's `run_pipeline()` method calls `notification.send()`. Change it to `async def run_pipeline(...)` and `await` the notification call.

Find the notification call site in `pipeline.py`:
```python
# OLD
from src.notification import send_daily_report
send_daily_report(results)

# NEW
from src.notification import send_daily_report
await send_daily_report(results)
```

If the pipeline uses `concurrent.futures` for batch processing, convert to `asyncio.gather` or `asyncio.TaskGroup` with `asyncio.Semaphore` for concurrency limiting.

- [ ] **Step 2: Verify compile**

Run: `python -m py_compile src/core/pipeline.py`
Expected: No output (success)

---

## Task 8: Convert main.py to async entry point

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Wrap main() in asyncio.run()**

```python
async def async_main():
    """Main async entry point."""
    # ... existing main() body, but change sync calls to await
    pass

def main():
    """Sync wrapper for argparse CLI."""
    asyncio.run(async_main())

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify compile and run**

Run: `python -m py_compile main.py`
Run: `python main.py --stocks 600519` (quick smoke test)
Expected: Runs pipeline end-to-end

---

## Task 9: Add async close/shutdown hooks

**Files:**
- Modify: `data_provider/_async_client.py`
- Modify: `src/notification_sender/async_base.py`
- Modify: `main.py`

- [ ] **Step 1: Add cleanup at shutdown**

In the `finally` block of `async_main()`:
```python
finally:
    await close_async_client()       # data_provider
    await close_sender_http_client() # notification senders
```

Import and call both cleanup functions to prevent "unclosed client" warnings.

- [ ] **Step 2: Verify**

Run: `python main.py --stocks 600519`
Expected: No "Unclosed client session" warnings in output

---

# Phase 3: Email Async + FastAPI Integration

## Task 10: Migrate email sender to aiosmtplib

**Files:**
- Modify: `src/notification_sender/email_sender.py`

- [ ] **Step 1: Add async email method**

```python
async def send_to_email_async(self, content: str, receivers: list | None = None) -> bool:
    """Async email sending using aiosmtplib."""
    import aiosmtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    msg = self._build_email_message(content, receivers)
    config = self._email_config
    use_ssl = "ssl" in config.get("smtp_security", "ssl")

    try:
        if use_ssl:
            await aiosmtplib.send(
                msg,
                hostname=config["smtp_server"],
                port=config.get("smtp_port", 465),
                use_tls=True,
                username=config["sender"],
                password=config["password"],
                timeout=self._timeout,
            )
        else:
            await aiosmtplib.send(
                msg,
                hostname=config["smtp_server"],
                port=config.get("smtp_port", 587),
                use_tls=False,
                start_tls=True,
                username=config["sender"],
                password=config["password"],
                timeout=self._timeout,
            )
        logger.info("邮件发送成功")
        return True
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False
```

Keep the sync `send_to_email` as a fallback. Update `_send_single_channel` in notification.py to call the async variant when available.

- [ ] **Step 2: Verify compile**

Run: `python -m py_compile src/notification_sender/email_sender.py`
Expected: No output (success)

---

## Task 11: Fix EMAIL receiver routing bug (critical, blocking merge)

**Files:**
- Modify: `src/notification.py`

This bug was introduced in the current uncommitted diff — the EMAIL branch in `_send_single_channel` hardcodes `receivers=None`, losing the `email_send_to_all` and `email_stock_codes` logic from the old code.

- [ ] **Step 1: Fix `_send_single_channel` EMAIL branch**

Add `email_stock_codes` and `email_send_to_all` as parameters to `_send_single_channel`:

```python
async def _send_single_channel(
    self,
    channel: NotificationChannel,
    content: str,
    image_bytes: Optional[bytes] = None,
    email_stock_codes: Optional[List[str]] = None,
    email_send_to_all: bool = False,
) -> bool:
    ...
    elif channel == NotificationChannel.EMAIL:
        receivers = None
        if email_send_to_all and self._stock_email_groups:
            receivers = self.get_all_email_receivers()
        elif email_stock_codes and self._stock_email_groups:
            receivers = self.get_receivers_for_stocks(email_stock_codes)
        if image_bytes:
            return await self._send_email_with_inline_image(image_bytes, receivers=receivers)
        return await self.send_to_email(content, receivers=receivers)
```

Also fix the `channel_tasks` construction to pass through these parameters.

---

# Verification

## Verification Commands

After each phase:
```bash
# Python syntax check
python -m py_compile data_provider/_async_client.py
python -m py_compile src/notification_sender/async_base.py
python -m py_compile src/notification.py
python -m py_compile src/core/pipeline.py
python -m py_compile main.py

# Import test (no network)
python -c "from data_provider._async_client import get_async_client; print('OK')"
python -c "from src.notification_sender.async_base import get_sender_http_client; print('OK')"

# Smoke test (requires real config)
python main.py --stocks 600519 --dry-run
```

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `httpx` SOCKS proxy not compatible with existing proxy config | Low | High — proxy users break | Tested in `_async_client.py` with same `socks5://` URL format |
| Async migration breaks `python main.py` in Docker | Medium | High — nightly job failure | Sync wrapper `send_sync()` ensures `main.py` callers work without change in Phase 1 |
| `aiosmtplib` incompatible with existing `email_sender.py` logic | Low | Medium — email channel breaks | Sync fallback kept as `.send_to_email()` when aiosmtplib unavailable |
| `asyncio.run()` cannot be called from within existing event loop | Low | Medium — breaks if called from FastAPI sync route | `_send_sync` uses `get_running_loop()` check to handle nesting |

## Rollback

Each phase is independently revertible:
- Phase 1: Revert sender changes, `send()` keeps sync version
- Phase 2: Revert pipeline/main changes back to sync callers
- Phase 3: Remove `aiosmtplib` dep, email falls back to sync

Full rollback: `git checkout HEAD -- data_provider/ src/notification_sender/ src/notification.py src/core/pipeline.py main.py`
