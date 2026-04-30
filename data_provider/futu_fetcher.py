# -*- coding: utf-8 -*-
"""
===================================
FutuFetcher - 富途牛牛数据源 (Priority 3)
===================================

数据来源：富途牛牛 OpenAPI
文档：https://openapi.futunn.com/futu-api-doc/

特点：
1. 港股/美股实时行情质量最佳
2. WebSocket 推送 + REST API 双模式
3. 需富途账户资产 >= 1万港币开通 API 权限

环境要求：
    pip install futu-api

配置 (.env):
    FUTU_API_HOST=127.0.0.1       # OpenD 本地地址
    FUTU_API_PORT=11111           # OpenD 默认端口
    FUTU_UNLOCK_PASSWORD=xxx      # 解锁密码（如需要）
"""

import logging
import os
from datetime import datetime
from typing import Optional, Any, List
from dataclasses import dataclass

import pandas as pd

from .base import BaseFetcher, DataFetchError
from .utils import normalize_stock_code
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource

logger = logging.getLogger(__name__)

# 可选依赖处理
try:
    from futu import (
        OpenQuoteContext,
        SubType,
        KLType,
        RET_OK,
    )
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False
    logger.info("futu-api not installed, FutuFetcher disabled")


@dataclass
class FutuConfig:
    """富途 API 配置"""
    host: str = "127.0.0.1"
    port: int = 11111
    unlock_password: Optional[str] = None


class FutuFetcher(BaseFetcher):
    """
    富途牛牛数据源 Fetcher

    适用市场：港股、美股
    推荐场景：港股实时行情、美股盘前盘后数据
    """

    name: str = "FutuFetcher"
    priority: int = 30  # Priority 3 (介于 Tushare 和 OpenBB/YFinance 之间)

    # 市场代码映射
    MARKET_MAP = {
        "HK": "hk",      # 港股
        "US": "us",      # 美股
        "SH": "sh",      # 沪股 (有限支持)
        "SZ": "sz",      # 深股 (有限支持)
    }

    def __init__(self, config: Optional[Any] = None):
        super().__init__(config)
        self._futu_config = self._load_config()
        self._quote_ctx: Optional[Any] = None
        self._initialized = False

        if not FUTU_AVAILABLE:
            logger.warning("[%s] futu-api not installed, fetcher unavailable", self.name)
            return

    def _load_config(self) -> FutuConfig:
        """加载配置"""
        cfg = self._get_config()
        return FutuConfig(
            host=getattr(cfg, "futu_api_host", os.getenv("FUTU_API_HOST", "127.0.0.1")),
            port=int(getattr(cfg, "futu_api_port", os.getenv("FUTU_API_PORT", "11111"))),
            unlock_password=getattr(cfg, "futu_unlock_password", os.getenv("FUTU_UNLOCK_PASSWORD")),
        )

    def _ensure_connection(self) -> bool:
        """确保 OpenD 连接可用"""
        if not FUTU_AVAILABLE:
            return False

        if self._initialized and self._quote_ctx is not None:
            return True

        try:
            self._quote_ctx = OpenQuoteContext(
                host=self._futu_config.host,
                port=self._futu_config.port,
            )

            # 如果需要解锁
            if self._futu_config.unlock_password:
                ret, data = self._quote_ctx.unlock_trade(self._futu_config.unlock_password)
                if ret != RET_OK:
                    logger.warning("[%s] 解锁失败: %s", self.name, data)

            self._initialized = True
            logger.info("[%s] 已连接到 OpenD %s:%s", self.name, self._futu_config.host, self._futu_config.port)
            return True

        except Exception as e:
            logger.error("[%s] 连接 OpenD 失败: %s", self.name, e)
            return False

    def _convert_code_to_futu(self, stock_code: str) -> Optional[str]:
        """
        转换为富途代码格式

        Examples:
            00700 -> HK.00700 (腾讯)
            AAPL -> US.AAPL (苹果)
            600519 -> SH.600519 (茅台)
        """
        code = normalize_stock_code(stock_code)

        # 港股代码格式：纯数字5位
        if code.isdigit() and len(code) <= 5:
            return f"HK.{code.zfill(5)}"

        # A股代码
        if code[:2].upper() in ("SH", "SZ"):
            market = code[:2].upper()
            return f"{market}.{code[2:]}"

        # 美股代码（假设纯字母）
        if code.isalpha():
            return f"US.{code.upper()}"

        return None

    def _is_supported(self, stock_code: str) -> bool:
        """检查是否支持该股票代码"""
        futu_code = self._convert_code_to_futu(stock_code)
        if not futu_code:
            return False

        # 主要支持港股和美股
        return futu_code.startswith(("HK.", "US."))

    def _fetch_raw_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        获取历史K线数据

        Note: 富途 API 限制，建议单次不超过 1000 根K线
        """
        if not self._ensure_connection():
            raise DataFetchError(f"[{self.name}] OpenD 连接不可用，请检查：\n"
                                f"1. 富途牛牛 OpenD 是否已启动\n"
                                f"2. 配置 FUTU_API_HOST/FUTU_API_PORT 是否正确")

        futu_code = self._convert_code_to_futu(stock_code)
        if not futu_code:
            raise DataFetchError(f"[{self.name}] 无法识别股票代码格式: {stock_code}")

        try:
            # 转换日期格式
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            # 计算需要获取的K线数量（粗略估计）
            days = (end_dt - start_dt).days
            kline_count = min(days + 10, 1000)  # 富途限制

            ret, data, page_req_key = self._quote_ctx.request_history_kline(
                code=futu_code,
                ktype=KLType.K_DAY,
                start=start_date,
                end=end_date,
                max_count=kline_count,
            )

            if ret != RET_OK:
                raise DataFetchError(f"[{self.name}] 获取历史数据失败: {data}")

            if data is None or data.empty:
                raise DataFetchError(f"[{self.name}] 未获取到 {stock_code} 的历史数据")

            logger.info(f"[{self.name}] 获取 {stock_code} 历史数据: {len(data)} 条")
            return data

        except Exception as e:
            if "连接" in str(e) or "Connection" in str(e):
                self._initialized = False  # 标记需要重连
            raise DataFetchError(f"[{self.name}] 获取历史数据异常: {e}") from e

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """标准化数据格式"""
        # 富途返回的字段名映射到标准格式
        # key=标准列名, value=富途API返回的列名
        column_mapping = {
            "date": "time_key",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "amount": "turnover",
            "pct_chg": "change_rate",
        }

        result = pd.DataFrame()

        for std_col, futu_col in column_mapping.items():
            if futu_col in df.columns:
                result[std_col] = df[futu_col]

        # 处理日期格式
        if "date" in result.columns:
            result["date"] = pd.to_datetime(result["date"]).dt.date

        # 添加股票代码
        result["code"] = normalize_stock_code(stock_code)

        # 数据清洗
        result = result.dropna(subset=["close", "volume"])
        result = result.sort_values("date", ascending=True).reset_index(drop=True)

        return result

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        获取实时行情（富途强项）

        返回 UnifiedRealtimeQuote，统一格式
        """
        if not self._ensure_connection():
            logger.warning("[%s] OpenD 未连接，无法获取实时行情", self.name)
            return None

        futu_code = self._convert_code_to_futu(stock_code)
        if not futu_code:
            return None

        try:
            # 订阅实时行情
            ret_sub, err_message = self._quote_ctx.subscribe(
                [futu_code], [SubType.QUOTE]
            )
            if ret_sub != RET_OK:
                logger.warning("[%s] 订阅 %s 失败: %s", self.name, stock_code, err_message)
                return None

            # 获取快照
            ret, data = self._quote_ctx.get_stock_quote([futu_code])
            if ret != RET_OK or data is None or data.empty:
                return None

            # 提取第一条数据
            row = data.iloc[0]

            quote = UnifiedRealtimeQuote(
                code=normalize_stock_code(stock_code),
                name=str(row.get("stock_name", "")),
                price=float(row.get("last_price", 0)),
                change_pct=float(row.get("change_rate", 0)),
                change_amount=float(row.get("change_val", 0)),
                volume=int(row.get("volume", 0)),
                amount=float(row.get("turnover", 0)),
                turnover_rate=float(row.get("turnover_rate", 0)),
                amplitude=float(row.get("amplitude", 0)),
                high=float(row.get("high_price", 0)),
                low=float(row.get("low_price", 0)),
                open_price=float(row.get("open_price", 0)),
                pre_close=float(row.get("prev_close_price", 0)),
                bid_price=float(row.get("bid_price", 0)),
                ask_price=float(row.get("ask_price", 0)),
                bid_volume=int(row.get("bid_volume", 0)),
                ask_volume=int(row.get("ask_volume", 0)),
                timestamp=datetime.now(),
                source=RealtimeSource.FUTU,
                raw_data=row.to_dict(),
            )

            return quote

        except Exception as e:
            logger.error("[%s] 获取实时行情失败: %s", self.name, e)
            return None

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """获取股票名称"""
        quote = self.get_realtime_quote(stock_code)
        if quote and quote.name:
            return quote.name
        return None

    def close(self):
        """关闭连接"""
        if self._quote_ctx:
            try:
                self._quote_ctx.close()
                logger.info("[%s] 已关闭 OpenD 连接", self.name)
            except Exception as e:
                logger.warning("[%s] 关闭连接失败: %s", self.name, e)
            finally:
                self._quote_ctx = None
                self._initialized = False

    def __del__(self):
        """析构时关闭连接"""
        self.close()


class FutuRealtimeProvider:
    """
    富途实时行情推送提供者（高级功能）

    适用于需要高频实时数据的场景，如：
    - 实时盯盘
    - 触发器告警
    - 实时策略

    使用 WebSocket 长连接，需配合 OpenD 运行
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        self.host = host
        self.port = port
        self._ctx: Optional[Any] = None
        self._subscribed: set = set()

    def start(self):
        """启动实时行情接收"""
        if not FUTU_AVAILABLE:
            raise DataFetchError("futu-api not installed")

        from futu import OpenQuoteContext

        self._ctx = OpenQuoteContext(host=self.host, port=self.port)

        # 设置回调
        self._ctx.set_handler(self._create_handler())

        logger.info("[FutuRealtime] 启动实时行情接收")

    def _create_handler(self):
        """创建行情处理器"""
        from futu import StockQuoteHandlerBase

        class QuoteHandler(StockQuoteHandlerBase):
            def __init__(self, provider):
                self.provider = provider

            def on_recv_rsp(self, rsp_pb):
                ret_code, data = super().on_recv_rsp(rsp_pb)
                if ret_code == RET_OK:
                    self.provider._on_quote_update(data)
                return ret_code, data

        return QuoteHandler(self)

    def _on_quote_update(self, data):
        """行情更新回调"""
        # 可扩展为消息队列或事件总线
        logger.debug("[FutuRealtime] 收到行情更新: %s", data.head().to_dict())

    def subscribe(self, codes: List[str]):
        """订阅股票列表"""
        if not self._ctx:
            raise DataFetchError("Provider not started")

        futu_codes = []
        for code in codes:
            fetcher = FutuFetcher()
            futu_code = fetcher._convert_code_to_futu(code)
            if futu_code:
                futu_codes.append(futu_code)
                self._subscribed.add(code)

        if futu_codes:
            ret, msg = self._ctx.subscribe(futu_codes, [SubType.QUOTE])
            if ret == RET_OK:
                logger.info("[FutuRealtime] 订阅成功: %d 只股票", len(futu_codes))
            else:
                logger.warning("[FutuRealtime] 订阅失败: %s", msg)

    def stop(self):
        """停止接收"""
        if self._ctx:
            self._ctx.close()
            self._ctx = None
            self._subscribed.clear()
            logger.info("[FutuRealtime] 已停止")
