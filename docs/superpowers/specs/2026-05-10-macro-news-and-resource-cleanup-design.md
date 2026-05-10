# 宏观新闻稳定化 & ResourceWarning 清理设计

## 概述

两个独立问题，各 1-3 行代码改动：

1. **宏观新闻 path**：`search_macro_news_async` 硬编码只允许 Tavily，其他 provider 被跳过；改为和股票新闻路径一致的 fallback 模式。
2. **ResourceWarning**：httpx SSL transport 在 `asyncio.run()` 关闭循环时残留；加 event loop 滴答 + 显式 loop close。

---

### 修复 1：宏观新闻 provider 白名单拓宽

**根因**：`src/search/service.py` `search_macro_news_async` 第 561 行：
```python
if not isinstance(provider, TavilySearchProvider):
    continue
```
Tavily 未配置或不可用时，宏观新闻必然空。股票新闻路径（`search_stock_news_async`）遍历所有 provider，fallback 充分。

**修复**：将白名单改为和股票新闻路径一致的模式：

```python
search_kwargs: Dict[str, Any] = {}
if isinstance(provider, TavilySearchProvider):
    search_kwargs["topic"] = "news"
if hasattr(provider, "search_async"):
    response = await provider.search_async(query, ...)
```

同步版 `search_macro_news`（行 ~867）同样放宽。

**文件**：`src/search/service.py`（异步 + 同步两处）

**测试**：mock 一个非 Tavily provider 返回成功结果 → 验证宏观新闻调用走通了。

---

### 修复 2：ResourceWarning 清理

**根因**：httpx `AsyncClient` 内部使用 SSL transport，`aclose()` 返回后 transport 的最终化回调还在 pending。`asyncio.run()` 立即关闭事件循环，触发了 `ResourceWarning: unclosed transport`。当前 `lifecycle.py` 已有 `warnings.filterwarnings("ignore")`，但实跑仍可能冒出来。

**修复**：

A. `lifecycle.py` `cleanup()` 中 `aclose()` 后加 event loop 滴答：
```python
await AsyncHttpClientManager().close()
await asyncio.sleep(0)  # 让 SSL transport 完成最终化
```

B. `main.py` `asyncio.run()` 返回后加显式清理：
```python
loop = asyncio.new_event_loop()
try:
    return loop.run_until_complete(run_with_cleanup(...))
finally:
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
```

等效但更彻底的写法是直接用 `asyncio.run()` 的 close 后处理。实际采用模式为在 `main.py` 将 `asyncio.run(run_with_cleanup(...))` 替换为等效的显式生命期管理。

**测试**：不新增专门测试（难以断言事件循环上无 warning），通过 pytest `-W error::ResourceWarning` 运行确认不再触发。

---

## 涉及文件

**修改：**
- `src/search/service.py` — 宏观新闻 provider 白名单拓宽（异步 + 同步两处）
- `src/core/lifecycle.py` — cleanup 中加 `await asyncio.sleep(0)`
- `main.py` — 显式事件循环管理替代 `asyncio.run()`

**测试：**
- `tests/test_search_service.py` — 新增宏观新闻非 Tavily provider 路径测试
