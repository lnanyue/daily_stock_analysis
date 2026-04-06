# 异步改造 - 关键改动点对照表

本文件快速列出需要修改的关键文件和改动要点。

---

## 📋 文件改动清单

### Phase 1 - 异步基础框架

| 文件路径 | 类型 | 改动 | 优先级 |
|---------|------|------ |--------|
| `src/utils/async_http.py` | ✨ 新文件 | 创建 AsyncHttpClientManager | P0 |
| `data_provider/base_async.py` | ✨ 新文件 | 创建 BaseFetcherAsync 基类 | P0 |
| `data_provider/efinance_fetcher_async.py` | ✨ 新文件 | 异步 EfinanceFetcher | P0 |
| `data_provider/tushare_fetcher_async.py` | ✨ 新文件 | 异步 TushareFetcher | P0 |
| `requirements.txt` | 📝 修改 | 添加 `httpx>=0.25.0` | P0 |

**工作量**: 3-4 天

---

### Phase 2 - 核心流程异步化

| 文件路径 | 类型 | 改动 | 优先级 |
|---------|------|------ |--------|
| `src/core/pipeline_async.py` | ✨ 新文件 | 创建 AsyncStockAnalysisPipeline | P0 |
| `src/core/pipeline.py` | 📝 修改 | 添加同步包装器，调用异步版本 | P1 |
| `main.py` | 📝 修改 | 改造主入口为异步（可选兼容同步） | P1 |
| `src/analyzer_async.py` | ✨ 新文件 | 异步 LLM 调用封装 | P1 |

**工作量**: 4-5 天

---

### Phase 3 - 推送和存储异步化

| 文件路径 | 类型 | 改动 | 优先级 |
|---------|------|------ |--------|
| `src/notification_sender_async.py` | ✨ 新文件 | 异步通知推送 | P2 |
| `src/storage_async.py` | ✨ 新文件 | 异步数据库操作（可选） | P2 |
| `server.py` | 📝 修改 | FastAPI 异步路由优化 | P2 |

**工作量**: 3-4 天

---

## 🔄 具体改动要点

### 1. requirements.txt 修改

**改前**:
```txt
requests==2.31.0
```

**改后**:
```txt
requests==2.31.0
httpx[http2]==0.25.0   # 添加异步 HTTP 客户端
```

---

### 2. data_provider 改动框架

**现状**（同步）:
```python
# data_provider/efinance_fetcher.py
class EfinanceFetcher(BaseFetcher):
    def get_daily_data(self, code):
        response = requests.get(url)  # ← 同步阻塞
        return pd.DataFrame(response.json())
```

**改后**（异步）:
```python
# data_provider/efinance_fetcher_async.py
class EfinanceFetcherAsync(BaseFetcherAsync):
    async def fetch_daily_data_async(self, code):
        response = await client.get(url)  # ← 异步等待
        return pd.DataFrame(response.json())

# 同步包装器（兼容旧代码）
class EfinanceFetcher(BaseFetcher):
    def get_daily_data(self, code):
        return asyncio.run(
            EfinanceFetcherAsync().fetch_daily_data_async(code)
        )
```

---

### 3. Pipeline 改动框架

**现状**（同步）:
```python
# src/core/pipeline.py
class StockAnalysisPipeline:
    def analyze_batch(self, codes):
        with ThreadPoolExecutor(max_workers=10) as executor:
            # 所有 I/O 在 executor 中串行执行
            futures = [
                executor.submit(self.analyze_single_stock, code)
                for code in codes
            ]
            return [f.result() for f in futures]
    
    def analyze_single_stock(self, code):
        # 同步执行，阻塞线程
        df = self.fetcher.get_daily_data(code)  # I/O 阻塞
        result = self.analyzer.analyze(df)       # CPU 计算
        return result
```

**改后**（异步）:
```python
# src/core/pipeline_async.py
class AsyncStockAnalysisPipeline:
    def __init__(self, max_concurrent=10):
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def analyze_batch_async(self, codes):
        # 所有 I/O 真正并发执行
        tasks = [
            self.analyze_single_stock_async(code)
            for code in codes
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    async def analyze_single_stock_async(self, code):
        async with self.semaphore:  # 背压控制
            # 并发获取多个数据源
            df, quote = await asyncio.gather(
                self.fetcher.fetch_daily_data_async(code),
                self.fetcher.fetch_realtime_quote_async(code),
                return_exceptions=True
            )
            
            # 本地计算在线程池中运行（不阻塞事件循环）
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self.analyzer.analyze,
                df
            )
            return result
```

---

### 4. main.py 改动框架

**现状**:
```python
# main.py（老版本）
if __name__ == "__main__":
    pipeline = StockAnalysisPipeline()
    results = pipeline.analyze_batch(codes)  # 同步调用
```

**改后**（选项 A：完全异步）:
```python
# main.py（新版本）
async def main():
    pipeline = AsyncStockAnalysisPipeline()
    results = await pipeline.analyze_batch_async(codes)
    await notify_async(results)

if __name__ == "__main__":
    asyncio.run(main())
```

**改后**（选项 B：向后兼容）:
```python
# main.py（兼容版本）
if __name__ == "__main__":
    # 仍然使用同步 API，内部调用异步版本
    results = analyze_stocks_sync(codes, max_workers=10)
```

---

### 5. server.py FastAPI 改动

**现状**:
```python
# server.py（可能的问题）
from fastapi import FastAPI

@app.post("/analyze")
def analyze_endpoint(codes: List[str]):
    # 这里是阻塞调用！
    results = pipeline.analyze_batch(codes)
    return results
```

**改后**:
```python
# server.py（优化后）
@app.post("/analyze")
async def analyze_endpoint(codes: List[str]):
    # 异步端点，不阻塞 FastAPI 事件循环
    results = await pipeline_async.analyze_batch_async(codes)
    return results
```

---

## 🧪 验证清单

### Phase 1 验证

- [ ] 创建 `src/utils/async_http.py`，单测验证：
  ```python
  async def test_async_http_client():
      manager = AsyncHttpClientManager()
      client = await manager.get_client()
      # 模拟 HTTP 请求
      assert client is not None
  ```

- [ ] 创建 `data_provider/base_async.py`，验证：
  ```python
  async def test_fetcher_concurrent():
      fetchers = [EfinanceFetcherAsync(), TushareFetcherAsync()]
      manager = AsyncDataFetcherManager(fetchers)
      
      # 并发获取 10 个股票 < 2 秒
      results = await manager.fetch_batch(10_stocks)
      assert len(results) == 10
  ```

- [ ] 改 `requirements.txt`，验证：
  ```bash
  pip install httpx
  python -c "import httpx; print(httpx.__version__)"
  ```

### Phase 2 验证

- [ ] 创建 `src/core/pipeline_async.py`，E2E 测试：
  ```python
  async def test_pipeline_async():
      pipeline = AsyncStockAnalysisPipeline()
      
      # 100 个股票 < 5 秒
      start = time.time()
      results = await pipeline.analyze_batch_async(100_stocks)
      elapsed = time.time() - start
      
      assert elapsed < 5.0
      assert len(results) > 90  # 允许 10% 失败
  ```

- [ ] 性能基准测试：
  ```bash
  # 同步版本（前 10 个股票）
  time python test_sync_pipeline.py
  
  # 异步版本（相同 10 个股票）
  time python test_async_pipeline.py
  
  # 预期：async 版本快 3-4 倍
  ```

- [ ] 兼容性验证：
  ```python
  # 现有代码仍然可用
  pipeline = StockAnalysisPipeline()  # 旧 API
  results = pipeline.analyze_batch(codes)  # 仍工作
  ```

### Phase 3 验证

- [ ] 异步通知推送验证：
  ```python
  async def test_async_notification():
      notifier = AsyncNotificationService()
      
      # 并发推送 20 条消息
      start = time.time()
      await notifier.notify_batch_async(20_messages)
      elapsed = time.time() - start
      
      assert elapsed < 5.0  # 超时设置 20s，应快速完成
  ```

---

## 🐞 常见 Async Bug 及其预防

| Bug 类型 | 症状 | 预防方案 |
|---------|------|--------|
| **忘记 await** | `coroutine was never awaited` 警告 | 启用 linter（pylint, flake8） |
| **事件循环关闭错误** | `RuntimeError: Event loop closed` | 使用 `finally` 块or context manager |
| **资源泄漏** | 内存持续增长 | 确保所有客户端都正确关闭 |
| **死锁** | 程序卡住 | 不要在异步代码中调用 `time.sleep()`，用 `await asyncio.sleep()` |
| **异常丢失** | 错误被吞掉 | 使用 `return_exceptions=True` 后检查结果 |

---

## 📊 改动风险矩阵

| 改动 | 风险等级 | 影响范围 | 缓解措施 |
|------|--------|--------|--------|
| requests → httpx | 🟡 中 | 数据获取 | 充分测试 API 兼容性 |
| ThreadPool → asyncio | 🟡 中 | 并发模式 | 性能基准测试 |
| 同步 Pipeline | 🟢 低 | 兼容层 | 提供同步包装 |
| 全量变更 | 🔴 高 | 整个系统 | **分阶段改造** |

**推荐风险管理**: 分阶段改造，每阶段完成后充分测试，再进入下一阶段。

---

## ⏱️ 行动计划

### Week 1（Phase 1）- Mon to Fri
- **Mon-Tue**: 实现 AsyncHttpClientManager + BaseFetcherAsync
- **Wed-Thu**: 改造 EfinanceFetcher + TushareFetcher 为异步
- **Fri**: 集成测试 + 性能验证

### Week 2（Phase 2）- Mon to Fri
- **Mon-Tue**: 实现 AsyncStockAnalysisPipeline
- **Wed**: 改造 main.py
- **Thu-Fri**: E2E 测试 + 性能对标

### Week 3（Phase 3）- Mon to Fri
- **Mon-Tue**: 异步通知服务
- **Wed-Thu**: 集成 FastAPI
- **Fri**: 完整系统验证

---

## 📚 参考资源

- [Python asyncio 官方文档](https://docs.python.org/3/library/asyncio.html)
- [httpx 异步客户端文档](https://www.python-httpx.org/async/)
- [FastAPI 异步支持](https://fastapi.tiangolo.com/async-sql-databases/)
- [Real Python - Async IO](https://realpython.com/async-io-python/)

---

**最后提醒**: 异步改造是性能优化的关键，但一定要：
1. ✅ 分阶段进行
2. ✅ 充分测试（特别是压力测试）
3. ✅ 保留同步 API（向后兼容）
4. ✅ 完整文档记录
5. ✅ 团队培训（异步编程有学习曲线）
