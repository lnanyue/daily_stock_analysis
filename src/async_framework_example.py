"""
异步框架示例代码 - Phase 1&2 核心模块

这是一个完整的异步改造框架，包含：
1. 异步 HTTP 客户端管理
2. 异步数据获取的 Fetcher 基类
3. 改造后的 StockAnalysisPipeline
4. 同步包装器（兼容现有代码）

用途：参考实施，不直接上线，需要充分测试后集成
"""

import asyncio
import httpx
import logging
import time
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd

logger = logging.getLogger(__name__)


# ==================== Phase 1: 异步 HTTP 框架 ====================

class AsyncHttpClientManager:
    """单例管理全局异步 HTTP 客户端"""
    
    _instance: Optional['AsyncHttpClientManager'] = None
    _client: Optional[httpx.AsyncClient] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_client(self, 
                        timeout: int = 10,
                        max_connections: int = 100,
                        max_keepalive: int = 20) -> httpx.AsyncClient:
        """获取或创建异步客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive,
                ),
                headers={
                    'User-Agent': 'DailyStockAnalysis/1.0'
                }
            )
            logger.info("AsyncHttpClient initialized")
        return self._client
    
    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("AsyncHttpClient closed")
    
    async def get_with_retry(self,
                            url: str,
                            max_retries: int = 3,
                            backoff: float = 1.0,
                            **kwargs) -> httpx.Response:
        """带重试的异步 GET 请求"""
        client = await self.get_client()
        
        for attempt in range(max_retries):
            try:
                response = await client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if attempt == max_retries - 1:
                    logger.error(f"Final attempt failed for {url}: {e}")
                    raise
                wait_time = backoff ** attempt
                logger.warning(f"Attempt {attempt+1}/{max_retries} failed, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
            except httpx.TimeoutException:
                if attempt == max_retries - 1:
                    logger.error(f"Timeout on {url}")
                    raise
                await asyncio.sleep(backoff ** attempt)


# ==================== Phase 1: 异步 Fetcher 基类 ====================

@dataclass
class FetchResult:
    """数据获取结果"""
    success: bool
    data: Optional[pd.DataFrame] = None
    source: str = ""
    error: Optional[str] = None
    duration: float = 0.0


class BaseFetcherAsync(ABC):
    """异步数据源基类"""
    
    def __init__(self, timeout: int = 10, priority: int = 999):
        self.timeout = timeout
        self.priority = priority
        self.name = self.__class__.__name__
    
    @abstractmethod
    async def fetch_daily_data_async(self,
                                     stock_code: str,
                                     days: int = 30,
                                     client: Optional[httpx.AsyncClient] = None) -> FetchResult:
        """异步获取日线数据"""
        pass
    
    @abstractmethod
    async def fetch_realtime_quote_async(self,
                                        stock_code: str,
                                        client: Optional[httpx.AsyncClient] = None) -> Optional[Dict]:
        """异步获取实时行情"""
        pass
    
    async def _safe_request(self,
                           url: str,
                           method: str = 'GET',
                           client: Optional[httpx.AsyncClient] = None,
                           **kwargs) -> Optional[httpx.Response]:
        """安全的异步请求包装"""
        if client is None:
            manager = AsyncHttpClientManager()
            client = await manager.get_client()
        
        try:
            if method.upper() == 'GET':
                response = await client.get(url, **kwargs)
            elif method.upper() == 'POST':
                response = await client.post(url, **kwargs)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response
        except Exception as e:
            logger.debug(f"{self.name} request error: {e}")
            return None


# ==================== Phase 1: 具体 Fetcher 实现示例 ====================

class EFinanceFetcherAsync(BaseFetcherAsync):
    """异步东财数据源（示例）"""
    
    def __init__(self):
        super().__init__(timeout=10, priority=0)
    
    async def fetch_daily_data_async(self,
                                     stock_code: str,
                                     days: int = 30,
                                     client: Optional[httpx.AsyncClient] = None) -> FetchResult:
        """异步获取东财日线数据"""
        start_time = time.time()
        
        try:
            # 这里是示例，实际需要根据 efinance API 调整
            # response = await self._safe_request(
            #     f"https://api-efinance.example.com/history/{stock_code}",
            #     params={"days": days},
            #     client=client
            # )
            
            # 为了演示，这里模拟一个小延迟
            await asyncio.sleep(0.1)
            
            # 模拟返回数据
            df = pd.DataFrame({
                'date': pd.date_range('2024-01-01', periods=days),
                'close': [100 + i for i in range(days)],
                'volume': [1000000 + i*10000 for i in range(days)]
            })
            
            duration = time.time() - start_time
            return FetchResult(
                success=True,
                data=df,
                source=self.name,
                duration=duration
            )
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"EFinance fetch error: {e}")
            return FetchResult(
                success=False,
                source=self.name,
                error=str(e),
                duration=duration
            )
    
    async def fetch_realtime_quote_async(self,
                                        stock_code: str,
                                        client: Optional[httpx.AsyncClient] = None) -> Optional[Dict]:
        """异步获取实时行情"""
        await asyncio.sleep(0.05)  # 模拟网络延迟
        return {
            'code': stock_code,
            'current_price': 100.0,
            'change': 1.5,
            'volume': 10000000
        }


# ==================== Phase 2: 异步数据获取管理器 ====================

class AsyncDataFetcherManager:
    """异步数据获取管理器，支持多源 fallback"""
    
    def __init__(self, fetchers: Optional[List[BaseFetcherAsync]] = None):
        self.fetchers = fetchers or []
        # 按优先级排序
        self.fetchers.sort(key=lambda f: f.priority)
    
    def register_fetcher(self, fetcher: BaseFetcherAsync):
        """注册数据源"""
        self.fetchers.append(fetcher)
        self.fetchers.sort(key=lambda f: f.priority)
    
    async def fetch_daily_data_async(self,
                                     stock_code: str,
                                     days: int = 30,
                                     max_concurrent: int = 3) -> Tuple[Optional[pd.DataFrame], str]:
        """并发获取日线数据，直到成功为止"""
        
        # 限制并发数（不是所有源都并发，只并发前 N 个）
        fetchers_to_try = self.fetchers[:max_concurrent]
        
        # 创建任务
        tasks = [
            fetcher.fetch_daily_data_async(stock_code, days)
            for fetcher in fetchers_to_try
        ]
        
        logger.debug(f"Fetching {stock_code} from {len(fetchers_to_try)} sources concurrently")
        
        # 使用 asyncio.as_completed 返回最快的结果
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result.success and result.data is not None:
                logger.info(f"Fetched {stock_code} from {result.source} in {result.duration:.2f}s")
                return result.data, result.source
        
        logger.error(f"Failed to fetch {stock_code} from all sources")
        return None, "NONE"
    
    async def fetch_realtime_quote_async(self,
                                        stock_code: str) -> Optional[Dict]:
        """获取实时行情（优先级 fallback）"""
        
        for fetcher in self.fetchers:
            try:
                quote = await fetcher.fetch_realtime_quote_async(stock_code)
                if quote:
                    return quote
            except Exception as e:
                logger.debug(f"Error fetching quote from {fetcher.name}: {e}")
                continue
        
        return None


# ==================== Phase 2: 异步 Stock 分析 Pipeline ====================

class AsyncStockAnalysisPipeline:
    """异步股票分析流水线"""
    
    def __init__(self,
                 fetcher_manager: Optional[AsyncDataFetcherManager] = None,
                 max_concurrent: int = 10):
        self.fetcher_manager = fetcher_manager or AsyncDataFetcherManager()
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def analyze_single_stock_async(self,
                                        stock_code: str,
                                        days: int = 30) -> Dict[str, Any]:
        """异步分析单只股票"""
        
        async with self.semaphore:  # 背压控制
            start = time.time()
            
            try:
                logger.info(f"Starting analysis for {stock_code}")
                
                # Step 1: 并发获取多个数据
                daily_df, source, realtime_quote = await asyncio.gather(
                    self.fetcher_manager.fetch_daily_data_async(stock_code, days),
                    asyncio.sleep(0),  # 占位符
                    self.fetcher_manager.fetch_realtime_quote_async(stock_code),
                    return_exceptions=True
                )
                
                # 解包结果
                if isinstance(daily_df, Exception):
                    logger.error(f"Error fetching data: {daily_df}")
                    return {'code': stock_code, 'success': False, 'error': str(daily_df)}
                
                daily_data, source = daily_df
                
                if daily_data is None:
                    return {'code': stock_code, 'success': False, 'error': 'No data'}
                
                # Step 2: 本地分析（计算 MA 等）
                analysis_result = await self._analyze_locally_async(
                    daily_data, realtime_quote
                )
                
                duration = time.time() - start
                
                return {
                    'code': stock_code,
                    'success': True,
                    'data': daily_data.tail(5).to_dict(),
                    'analysis': analysis_result,
                    'source': source,
                    'duration': duration
                }
            
            except Exception as e:
                duration = time.time() - start
                logger.error(f"Error analyzing {stock_code}: {e}")
                return {
                    'code': stock_code,
                    'success': False,
                    'error': str(e),
                    'duration': duration
                }
    
    async def _analyze_locally_async(self,
                                     daily_data: pd.DataFrame,
                                     realtime_quote: Optional[Dict]) -> Dict:
        """本地异步分析（计算技术指标）"""
        
        # 这可以在线程池中执行（CPU 密集型）
        loop = asyncio.get_event_loop()
        
        analysis = await loop.run_in_executor(
            None,
            self._compute_technical_indicators,
            daily_data
        )
        
        return analysis
    
    @staticmethod
    def _compute_technical_indicators(df: pd.DataFrame) -> Dict:
        """计算技术指标（CPU 密集）"""
        # 这个方法在线程池中运行，不会阻塞事件循环
        ma5 = df['close'].rolling(5).mean().iloc[-1]
        ma20 = df['close'].rolling(20).mean().iloc[-1]
        
        return {
            'ma5': float(ma5) if pd.notna(ma5) else None,
            'ma20': float(ma20) if pd.notna(ma20) else None,
            'current_price': float(df['close'].iloc[-1])
        }
    
    async def analyze_batch_async(self,
                                 stock_codes: List[str],
                                 days: int = 30) -> List[Dict[str, Any]]:
        """异步批量分析"""
        
        logger.info(f"Batch analyzing {len(stock_codes)} stocks")
        start = time.time()
        
        # 创建任务
        tasks = [
            self.analyze_single_stock_async(code, days)
            for code in stock_codes
        ]
        
        # 并发执行
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常
        valid_results = [
            r for r in results
            if not isinstance(r, Exception)
        ]
        
        duration = time.time() - start
        
        # 统计
        success_count = sum(1 for r in valid_results if r.get('success'))
        failed_count = len(valid_results) - success_count
        
        logger.info(
            f"Batch analysis completed: "
            f"{success_count} success, {failed_count} failed, "
            f"{duration:.2f}s total ({duration/len(stock_codes):.2f}s avg)"
        )
        
        return valid_results


# ==================== 同步包装器（兼容现有代码）====================

def analyze_stocks_sync(stock_codes: List[str],
                       days: int = 30,
                       max_workers: int = 10) -> List[Dict[str, Any]]:
    """同步接口包装（供现有代码使用）"""
    
    # 初始化异步组件
    fetchers = [EFinanceFetcherAsync()]
    fetcher_manager = AsyncDataFetcherManager(fetchers)
    pipeline = AsyncStockAnalysisPipeline(
        fetcher_manager=fetcher_manager,
        max_concurrent=max_workers
    )
    
    # 运行异步任务
    results = asyncio.run(
        pipeline.analyze_batch_async(stock_codes, days)
    )
    
    # 清理资源
    manager = AsyncHttpClientManager()
    asyncio.run(manager.close())
    
    return results


# ==================== 异步主函数示例 ====================

async def main_async_example():
    """异步主函数示例"""
    
    # 初始化
    fetchers = [
        EFinanceFetcherAsync(),
        # TusharesFetcherAsync(),  # 可扩展更多源
    ]
    
    fetcher_manager = AsyncDataFetcherManager(fetchers)
    pipeline = AsyncStockAnalysisPipeline(
        fetcher_manager=fetcher_manager,
        max_concurrent=10
    )
    
    try:
        # 要分析的股票
        stock_codes = ['600519', '000001', '000858', '600036', '601398']
        
        # 异步分析
        results = await pipeline.analyze_batch_async(stock_codes, days=30)
        
        # 处理结果
        for result in results:
            if result['success']:
                print(f"✓ {result['code']}: {result['analysis']} ({result['duration']:.2f}s)")
            else:
                print(f"✗ {result['code']}: {result['error']}")
        
        return results
    
    finally:
        # 清理资源
        manager = AsyncHttpClientManager()
        await manager.close()


# ==================== 测试对比脚本 ====================

if __name__ == "__main__":
    """演示异步 vs 同步性能对比"""
    
    test_stocks = ['600519', '000001', '000858', '600036', '601398',
                   '601988', '601398', '600000', '601166', '600519']
    
    print("=" * 60)
    print("异步分析框架演示")
    print("=" * 60)
    
    # 异步版本
    print("\n[异步版本]")
    start_async = time.time()
    results_async = asyncio.run(main_async_example())
    time_async = time.time() - start_async
    print(f"总耗时: {time_async:.2f}s")
    
    # 同步版本（模拟）
    print("\n[同步版本（估算）]")
    # 如果串行执行，每个股票平均 2s，10 个 = 20s
    estimated_sync_time = 2.0 * len(test_stocks)
    print(f"估计耗时: {estimated_sync_time:.2f}s")
    
    # 性能提升
    improvement = (estimated_sync_time - time_async) / estimated_sync_time * 100
    print(f"\n性能提升: {improvement:.1f}%")
    print("=" * 60)
