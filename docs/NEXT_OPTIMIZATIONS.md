# 后续优化路线图 (Next Optimizations)

基于已完成的异步化和初步重构，以下是推荐的后续优化方向，旨在进一步提升代码可维护性、并发性能和系统稳定性。

---

## 1. 深度拆分通知层 (Notification Layer)
**目标文件**: `src/notification.py` (~83KB)
- **现状**: 虽然引入了 `ReportRenderer`，但大部分 Markdown 拼接、表格生成、Emoji 映射逻辑仍耦合在 `NotificationService` 中。
- **方案**:
    - 将 `generate_daily_report`, `generate_wechat_dashboard` 等所有渲染方法迁移至 `src/notification/renderer.py`。
    - 将格式化工具（百分比、均线描述）迁移至 `src/notification/utils.py`。
- **收益**: 实现“发送”与“表现”的彻底分离，使通知层易于测试和扩展新平台。

## 2. 模块化数据提供层 (Data Provider Layer)
**目标文件**: `data_provider/base.py` (~96KB)
- **现状**: 该文件承载了数据获取的调度逻辑、实时行情缓存、故障切换机制等，过于臃肿。
- **方案**:
    - 创建 `data_provider/manager.py`: 专门负责 `DataFetcherManager`。
    - 创建 `data_provider/cache.py`: 剥离实时行情缓存逻辑。
    - 创建 `data_provider/models.py`: 统一管理 `UnifiedRealtimeQuote` 等数据模型。
- **收益**: 降低维护难度，增加新数据源（如富途、老虎）时代码更加解耦。

## 3. 搜索 Provider 的全量异步化
**目标路径**: `src/search/providers/`
- **现状**: 除 SerpAPI 和 Tavily 外，`Bocha`, `Brave`, `Exa`, `MiniMax` 等仍在使用同步的 `requests` 库。
- **方案**: 
    - 使用 `httpx` 重写各 Provider 的 `_do_search_async` 方法。
- **收益**: 在配置了多搜索引擎自动切换时，彻底消除 I/O 阻塞，极大提升并发性能。

## 4. 彻底消除 Pipeline 中的 `asyncio.to_thread`
**目标文件**: `src/core/pipeline.py`
- **现状**: 仍有部分逻辑（如技术面分析、筹码计算）依赖线程池包装。
- **方案**:
    - 将 CPU 密集型的分析逻辑进一步抽象。
    - 考虑引入 `ProcessPoolExecutor` 处理大规模回测或复杂形态识别。
- **收益**: 释放主线程压力，提高多核 CPU 利用率。

## 5. 增强错误恢复与重试机制 (Resilience)
**目标**: 全局异步重试策略
- **方案**:
    - 引入 `tenacity` 的异步装饰器。
    - 定义统一的 `AsyncRetryPolicy`，针对不同错误类型（网络超时、速率限制、模型崩溃）设定差异化的退避算法。
- **收益**: 显著提高系统在极端网络或高负载情况下的健壮性。
