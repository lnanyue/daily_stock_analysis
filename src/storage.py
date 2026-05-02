# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 存储层 (Refactored)
===================================

职责：
1. 管理 SQLite 数据库连接（单例模式）
2. 提供数据存取接口
3. 实现智能更新逻辑
"""

import atexit
import asyncio
import hashlib
import json
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Tuple, Callable, TypeVar
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, TYPE_CHECKING, Tuple, Callable, TypeVar

import pandas as pd
from sqlalchemy import (
    create_engine,
    Column,
    String,
    Float,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    UniqueConstraint,
    Text,
    select,
    and_,
    or_,
    desc,
    event,
    func,
    MetaData,
    Table,
    text,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import (
    declarative_base,
    sessionmaker,
    Session,
)
from sqlalchemy.exc import IntegrityError, OperationalError

from src.config import get_config
from src.schemas.storage_models import (
    Base,
    AnalysisHistory,
    BacktestResult,
    BacktestSummary,
    ConversationMessage,
    LLMUsage,
    FundamentalSnapshot,
    NewsIntel,
    PortfolioAccount,
    PortfolioCashLedger,
    PortfolioCorporateAction,
    PortfolioDailySnapshot,
    PortfolioFxRate,
    PortfolioPosition,
    PortfolioPositionLot,
    PortfolioTrade,
    StockDaily,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


class AutoCommitSession(Session):
    """SQLAlchemy Session that commits when used as ``with db.get_session()``."""

    def _normalize_pending_objects(self) -> None:
        analysis_history_cls = globals().get("AnalysisHistory")
        if analysis_history_cls is None:
            return
        for obj in list(self.new) + list(self.dirty):
            if isinstance(obj, analysis_history_cls) and isinstance(getattr(obj, "raw_result", None), dict):
                obj.raw_result = json.dumps(obj.raw_result, ensure_ascii=False)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self._normalize_pending_objects()
                self.commit()
            else:
                self.rollback()
        except Exception:
            self.rollback()
            raise
        finally:
            self.close()
        return False


# SQLAlchemy ORM 基类
Base = declarative_base()

if TYPE_CHECKING:
    from src.search_service import SearchResponse


# === 数据模型定义 ===

class StockDaily(Base):
    """
    股票日线数据模型
    
    存储每日行情数据和计算的技术指标
    支持多股票、多日期的唯一约束
    """
    __tablename__ = 'stock_daily'
    
    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 股票代码（如 600519, 000001）
    code = Column(String(10), nullable=False, index=True)
    
    # 交易日期
    date = Column(Date, nullable=False, index=True)
    
    # OHLC 数据
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    
    # 成交数据
    volume = Column(Float)  # 成交量（股）
    amount = Column(Float)  # 成交额（元）
    pct_chg = Column(Float)  # 涨跌幅（%）
    
    # 技术指标
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    volume_ratio = Column(Float)  # 量比
    
    # 数据来源
    data_source = Column(String(50))  # 记录数据来源（如 AkshareFetcher）
    
    # 更新时间
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 唯一约束：同一股票同一日期只能有一条数据
    __table_args__ = (
        UniqueConstraint('code', 'date', name='uix_code_date'),
        Index('ix_code_date', 'code', 'date'),
    )
    
    def __repr__(self):
        return f"<StockDaily(code={self.code}, date={self.date}, close={self.close})>"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'code': self.code,
            'date': self.date,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'amount': self.amount,
            'pct_chg': self.pct_chg,
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'volume_ratio': self.volume_ratio,
            'data_source': self.data_source,
        }


class NewsIntel(Base):
    """
    新闻情报数据模型

    存储搜索到的新闻情报条目，用于后续分析与查询
    """
    __tablename__ = 'news_intel'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 关联用户查询操作
    query_id = Column(String(64), index=True)

    # 股票信息
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(50))

    # 搜索上下文
    dimension = Column(String(32), index=True)  # latest_news / risk_check / earnings / market_analysis / industry
    query = Column(String(255))
    provider = Column(String(32), index=True)

    # 新闻内容
    title = Column(String(300), nullable=False)
    snippet = Column(Text)
    url = Column(String(1000), nullable=False)
    source = Column(String(100))
    published_date = Column(DateTime, index=True)

    # 入库时间
    fetched_at = Column(DateTime, default=datetime.now, index=True)
    query_source = Column(String(32), index=True)  # bot/web/cli/system
    requester_platform = Column(String(20))
    requester_user_id = Column(String(64))
    requester_user_name = Column(String(64))
    requester_chat_id = Column(String(64))
    requester_message_id = Column(String(64))
    requester_query = Column(String(255))

    __table_args__ = (
        UniqueConstraint('url', name='uix_news_url'),
        Index('ix_news_code_pub', 'code', 'published_date'),
    )

    def __repr__(self) -> str:
        return f"<NewsIntel(code={self.code}, title={self.title[:20]}...)>"


class FundamentalSnapshot(Base):
    """
    基本面上下文快照（P0 write-only）。

    仅用于写入，主链路不依赖读取该表，便于后续回测/画像扩展。
    """
    __tablename__ = 'fundamental_snapshot'

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    source_chain = Column(Text)
    coverage = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_fundamental_snapshot_query_code', 'query_id', 'code'),
        Index('ix_fundamental_snapshot_created', 'created_at'),
    )

    def __repr__(self) -> str:
        return f"<FundamentalSnapshot(query_id={self.query_id}, code={self.code})>"


class AnalysisHistory(Base):
    """
    分析结果历史记录模型

    保存每次分析结果，支持按 query_id/股票代码检索
    """
    __tablename__ = 'analysis_history'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 关联查询链路
    query_id = Column(String(64), index=True)

    # 股票信息
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(50))
    report_type = Column(String(16), index=True)

    # 核心结论
    sentiment_score = Column(Integer)
    operation_advice = Column(String(20))
    trend_prediction = Column(String(50))
    analysis_summary = Column(Text)

    # 详细数据
    raw_result = Column(Text)
    news_content = Column(Text)
    context_snapshot = Column(Text)

    # 狙击点位（用于回测）
    ideal_buy = Column(Float)
    secondary_buy = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)

    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_analysis_code_time', 'code', 'created_at'),
    )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            'query_id': self.query_id,
            'code': self.code,
            'name': self.name,
            'report_type': self.report_type,
            'sentiment_score': self.sentiment_score,
            'operation_advice': self.operation_advice,
            'trend_prediction': self.trend_prediction,
            'analysis_summary': self.analysis_summary,
            'raw_result': self.raw_result,
            'news_content': self.news_content,
            'context_snapshot': self.context_snapshot,
            'ideal_buy': self.ideal_buy,
            'secondary_buy': self.secondary_buy,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class BacktestResult(Base):
    """单条分析记录的回测结果。"""

    __tablename__ = 'backtest_results'

    id = Column(Integer, primary_key=True, autoincrement=True)

    analysis_history_id = Column(
        Integer,
        ForeignKey('analysis_history.id'),
        nullable=False,
        index=True,
    )

    # 冗余字段，便于按股票筛选
    code = Column(String(10), nullable=False, index=True)
    analysis_date = Column(Date, index=True)

    # 回测参数
    eval_window_days = Column(Integer, nullable=False, default=10)
    engine_version = Column(String(16), nullable=False, default='v1')

    # 状态
    eval_status = Column(String(16), nullable=False, default='pending')
    evaluated_at = Column(DateTime, default=datetime.now, index=True)

    # 建议快照（避免未来分析字段变化导致回测不可解释）
    operation_advice = Column(String(20))
    position_recommendation = Column(String(8))  # long/cash

    # 价格与收益
    start_price = Column(Float)
    end_close = Column(Float)
    max_high = Column(Float)
    min_low = Column(Float)
    stock_return_pct = Column(Float)

    # 方向与结果
    direction_expected = Column(String(16))  # up/down/flat/not_down
    direction_correct = Column(Boolean, nullable=True)
    outcome = Column(String(16))  # win/loss/neutral

    # 目标价命中（仅 long 且配置了止盈/止损时有意义）
    stop_loss = Column(Float)
    take_profit = Column(Float)
    hit_stop_loss = Column(Boolean)
    hit_take_profit = Column(Boolean)
    first_hit = Column(String(16))  # take_profit/stop_loss/ambiguous/neither/not_applicable
    first_hit_date = Column(Date)
    first_hit_trading_days = Column(Integer)

    # 模拟执行（long-only）
    simulated_entry_price = Column(Float)
    simulated_exit_price = Column(Float)
    simulated_exit_reason = Column(String(24))  # stop_loss/take_profit/window_end/cash/ambiguous_stop_loss
    simulated_return_pct = Column(Float)

    __table_args__ = (
        UniqueConstraint(
            'analysis_history_id',
            'eval_window_days',
            'engine_version',
            name='uix_backtest_analysis_window_version',
        ),
        Index('ix_backtest_code_date', 'code', 'analysis_date'),
    )


class BacktestSummary(Base):
    """回测汇总指标（按股票或全局）。"""

    __tablename__ = 'backtest_summaries'

    id = Column(Integer, primary_key=True, autoincrement=True)

    scope = Column(String(16), nullable=False, index=True)  # overall/stock
    code = Column(String(16), index=True)

    eval_window_days = Column(Integer, nullable=False, default=10)
    engine_version = Column(String(16), nullable=False, default='v1')
    computed_at = Column(DateTime, default=datetime.now, index=True)

    # 计数
    total_evaluations = Column(Integer, default=0)
    completed_count = Column(Integer, default=0)
    insufficient_count = Column(Integer, default=0)
    long_count = Column(Integer, default=0)
    cash_count = Column(Integer, default=0)

    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    neutral_count = Column(Integer, default=0)

    # 准确率/胜率
    direction_accuracy_pct = Column(Float)
    win_rate_pct = Column(Float)
    neutral_rate_pct = Column(Float)

    # 收益
    avg_stock_return_pct = Column(Float)
    avg_simulated_return_pct = Column(Float)

    # 目标价触发统计（仅 long 且配置止盈/止损时统计）
    stop_loss_trigger_rate = Column(Float)
    take_profit_trigger_rate = Column(Float)
    ambiguous_rate = Column(Float)
    avg_days_to_first_hit = Column(Float)

    # 诊断字段（JSON 字符串）
    advice_breakdown_json = Column(Text)
    diagnostics_json = Column(Text)

    __table_args__ = (
        UniqueConstraint(
            'scope',
            'code',
            'eval_window_days',
            'engine_version',
            name='uix_backtest_summary_scope_code_window_version',
        ),
    )


class PortfolioAccount(Base):
    """Portfolio account metadata."""

    __tablename__ = 'portfolio_accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String(64), index=True)
    name = Column(String(64), nullable=False)
    broker = Column(String(64))
    market = Column(String(8), nullable=False, default='cn', index=True)  # cn/hk/us
    base_currency = Column(String(8), nullable=False, default='CNY')
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index('ix_portfolio_account_owner_active', 'owner_id', 'is_active'),
    )


class PortfolioTrade(Base):
    """Executed trade events used as the source of truth for replay."""

    __tablename__ = 'portfolio_trades'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    trade_uid = Column(String(128))
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    trade_date = Column(Date, nullable=False, index=True)
    side = Column(String(8), nullable=False)  # buy/sell
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fee = Column(Float, default=0.0)
    tax = Column(Float, default=0.0)
    note = Column(String(255))
    dedup_hash = Column(String(64), index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint('account_id', 'trade_uid', name='uix_portfolio_trade_uid'),
        UniqueConstraint('account_id', 'dedup_hash', name='uix_portfolio_trade_dedup_hash'),
        Index('ix_portfolio_trade_account_date', 'account_id', 'trade_date'),
    )


class PortfolioCashLedger(Base):
    """Cash in/out events."""

    __tablename__ = 'portfolio_cash_ledger'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    event_date = Column(Date, nullable=False, index=True)
    direction = Column(String(8), nullable=False)  # in/out
    amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default='CNY')
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_portfolio_cash_account_date', 'account_id', 'event_date'),
    )


class PortfolioCorporateAction(Base):
    """Corporate actions that impact cash or share quantity."""

    __tablename__ = 'portfolio_corporate_actions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    effective_date = Column(Date, nullable=False, index=True)
    action_type = Column(String(24), nullable=False)  # cash_dividend/split_adjustment
    cash_dividend_per_share = Column(Float)
    split_ratio = Column(Float)
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index('ix_portfolio_ca_account_date', 'account_id', 'effective_date'),
    )


class PortfolioPosition(Base):
    """Latest replayed position snapshot for each symbol in one account."""

    __tablename__ = 'portfolio_positions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    quantity = Column(Float, nullable=False, default=0.0)
    avg_cost = Column(Float, nullable=False, default=0.0)
    total_cost = Column(Float, nullable=False, default=0.0)
    last_price = Column(Float, nullable=False, default=0.0)
    market_value_base = Column(Float, nullable=False, default=0.0)
    unrealized_pnl_base = Column(Float, nullable=False, default=0.0)
    valuation_currency = Column(String(8), nullable=False, default='CNY')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint(
            'account_id',
            'symbol',
            'market',
            'currency',
            'cost_method',
            name='uix_portfolio_position_account_symbol_market_currency',
        ),
    )


class PortfolioPositionLot(Base):
    """Lot-level remaining quantities used by FIFO replay."""

    __tablename__ = 'portfolio_position_lots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')
    symbol = Column(String(16), nullable=False, index=True)
    market = Column(String(8), nullable=False, default='cn')
    currency = Column(String(8), nullable=False, default='CNY')
    open_date = Column(Date, nullable=False, index=True)
    remaining_quantity = Column(Float, nullable=False, default=0.0)
    unit_cost = Column(Float, nullable=False, default=0.0)
    source_trade_id = Column(Integer, ForeignKey('portfolio_trades.id'))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        Index('ix_portfolio_lot_account_symbol', 'account_id', 'symbol'),
    )


class PortfolioDailySnapshot(Base):
    """Daily account snapshot generated by read-time replay."""

    __tablename__ = 'portfolio_daily_snapshots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    cost_method = Column(String(8), nullable=False, default='fifo')  # fifo/avg
    base_currency = Column(String(8), nullable=False, default='CNY')
    total_cash = Column(Float, nullable=False, default=0.0)
    total_market_value = Column(Float, nullable=False, default=0.0)
    total_equity = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    fee_total = Column(Float, nullable=False, default=0.0)
    tax_total = Column(Float, nullable=False, default=0.0)
    fx_stale = Column(Boolean, nullable=False, default=False)
    payload = Column(Text)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            'account_id',
            'snapshot_date',
            'cost_method',
            name='uix_portfolio_snapshot_account_date_method',
        ),
    )


class PortfolioFxRate(Base):
    """Cached FX rates used for cross-currency portfolio conversion."""

    __tablename__ = 'portfolio_fx_rates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_currency = Column(String(8), nullable=False, index=True)
    to_currency = Column(String(8), nullable=False, index=True)
    rate_date = Column(Date, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    source = Column(String(32), nullable=False, default='manual')
    is_stale = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            'from_currency',
            'to_currency',
            'rate_date',
            name='uix_portfolio_fx_pair_date',
        ),
    )


class ConversationMessage(Base):
    """
    Agent 对话历史记录表
    """
    __tablename__ = 'conversation_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True, nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)


class LLMUsage(Base):
    """One row per litellm.completion() call — token-usage audit log."""

    __tablename__ = 'llm_usage'

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 'analysis' | 'agent' | 'market_review'
    call_type = Column(String(32), nullable=False, index=True)
    model = Column(String(128), nullable=False)
    stock_code = Column(String(16), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    called_at = Column(DateTime, default=datetime.now, index=True)


class DatabaseManager:
    """
    数据库管理器 - 单例模式
    """
    
    _instance: Optional['DatabaseManager'] = None
    _initialized: bool = False
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db_url: Optional[str] = None):
        if getattr(self, '_initialized', False):
            return

        config = get_config()
        if db_url is None:
            db_url = config.get_db_url()

        self._db_url = db_url
        self._sqlite_wal_enabled = config.sqlite_wal_enabled
        self._sqlite_busy_timeout_ms = config.sqlite_busy_timeout_ms
        self._sqlite_write_retry_max = config.sqlite_write_retry_max
        self._sqlite_write_retry_base_delay = config.sqlite_write_retry_base_delay

        engine_kwargs = {
            "echo": False,
            "pool_pre_ping": True,
        }
        if str(db_url).startswith("sqlite:") and self._sqlite_busy_timeout_ms > 0:
            engine_kwargs["connect_args"] = {
                "timeout": self._sqlite_busy_timeout_ms / 1000,
            }

        # 创建数据库引擎
        self._engine = create_engine(
            db_url,
            **engine_kwargs,
        )
        self._is_sqlite_engine = self._engine.url.get_backend_name() == 'sqlite'
        self._sqlite_file_db = self._is_sqlite_engine and self._is_file_sqlite_database()
        self._install_sqlite_pragma_handler()
        
        # 创建 Session 工厂
        self._SessionLocal = sessionmaker(
            bind=self._engine,
            class_=AutoCommitSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        
        # Ensure tables exist
        Base.metadata.create_all(self._engine)
        self._initialized = True
        logger.info("数据库初始化完成: %s", db_url)
        atexit.register(DatabaseManager._cleanup_engine, self._engine)

    @classmethod
    def get_instance(cls) -> 'DatabaseManager':
        if cls._instance is None:
            cls._instance = DatabaseManager()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        if cls._instance is not None and hasattr(cls._instance, "_engine"):
            try:
                cls._instance._engine.dispose()
            except Exception:
                pass
        cls._instance = None
        cls._initialized = False

    @staticmethod
    def _cleanup_engine(engine):
        """清理数据库引擎。

        Args:
            engine: SQLAlchemy 引擎对象
        """
        try:
            if engine is not None:
                engine.dispose()
                logger.debug("数据库引擎已清理")
        except Exception as e:
            logger.warning(f"清理数据库引擎时出错: {e}")

    def _install_sqlite_pragma_handler(self) -> None:
        """为 SQLite 连接安装竞争保护参数。"""
        if not self._is_sqlite_engine:
            return

        @event.listens_for(self._engine, "connect")
        def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"PRAGMA busy_timeout={int(self._sqlite_busy_timeout_ms)}")
                if self._sqlite_file_db and self._sqlite_wal_enabled:
                    cursor.execute("PRAGMA journal_mode=WAL")
            except Exception as exc:
                logger.warning("初始化 SQLite PRAGMA 失败: %s", exc)
            finally:
                cursor.close()

    def _is_file_sqlite_database(self) -> bool:
        database = (self._engine.url.database or "").strip()
        return bool(database) and database.lower() != ":memory:"

    @staticmethod
    def _is_sqlite_locked_error(exc: OperationalError) -> bool:
        err_text = str(getattr(exc, "orig", exc)).lower()
        return any(
            token in err_text
            for token in (
                "database is locked",
                "database schema is locked",
                "database table is locked",
            )
        )

    def get_session(self) -> Session:
        """
        获取数据库 Session。调用方可直接使用 ``with db.get_session()``，
        也可手动管理生命周期；写入且需要自动 commit 时使用
        :meth:`session_scope` 或 :meth:`_run_write_transaction`。
        """
        if not getattr(self, '_initialized', False) or not hasattr(self, '_SessionLocal'):
            raise RuntimeError(
                "DatabaseManager 未正确初始化。"
                "请确保通过 DatabaseManager.get_instance() 获取实例。"
            )
        return self._SessionLocal()

    @contextmanager
    def session_scope(self):
        session = self.get_session()
        try:
            yield session
            if hasattr(session, "_normalize_pending_objects"):
                session._normalize_pending_objects()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _run_write_transaction(self, name: str, operation: Callable[[Session], T]) -> T:
        max_retries = self._sqlite_write_retry_max if self._is_sqlite_engine else 0

        for attempt in range(max_retries + 1):
            session = self.get_session()
            try:
                if self._is_sqlite_engine:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                result = operation(session)
                session.commit()
                return result
            except OperationalError as exc:
                session.rollback()
                if (
                    self._is_sqlite_engine
                    and self._is_sqlite_locked_error(exc)
                    and attempt < max_retries
                ):
                    delay = self._sqlite_write_retry_base_delay * (2 ** attempt)
                    if delay > 0:
                        time.sleep(delay)
                    continue
                raise
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        raise RuntimeError(f"write transaction failed after retries: {name}")

    # --- Data Access Methods ---

    def has_today_data(self, code: str) -> bool:
        today = date.today()
        with self.get_session() as session:
            result = session.execute(
                select(StockDaily.id).where(and_(StockDaily.code == code, StockDaily.date == today)).limit(1)
            ).scalar()
            return result is not None

    def get_latest_data(self, code: str, days: int = 1) -> List[StockDaily]:
        with self.get_session() as session:
            results = session.execute(
                select(StockDaily).where(StockDaily.code == code).order_by(desc(StockDaily.date)).limit(days)
            ).scalars().all()
            return list(results)

    def get_global_latest_date(self) -> Optional[date]:
        """获取数据库中存储的最新数据日期。"""
        with self.get_session() as session:
            result = session.execute(
                select(StockDaily.date).order_by(desc(StockDaily.date)).limit(1)
            ).scalar()
            return result

    def save_conversation_message(self, session_id: str, role: str, content: str) -> int:
        """Persist a single Agent chat message."""
        def _operation(session: Session) -> int:
            record = ConversationMessage(
                session_id=str(session_id or "").strip(),
                role=str(role or "").strip() or "user",
                content=str(content or ""),
            )
            session.add(record)
            session.flush()
            return int(record.id or 0)

        return self._run_write_transaction("save_conversation_message", _operation)

    def get_conversation_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Load a session's chat history ordered from oldest to newest."""
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id:
            return []

        with self.get_session() as session:
            query = (
                select(ConversationMessage)
                .where(ConversationMessage.session_id == clean_session_id)
                .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
            )
            if limit and limit > 0:
                query = query.limit(int(limit))
            records = session.execute(query).scalars().all()
            return [
                {
                    "role": record.role,
                    "content": record.content,
                }
                for record in records
            ]

    def get_chat_sessions(
        self,
        session_prefix: str,
        *,
        extra_session_ids: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List recent chat sessions under a user/session prefix.

        Matching keeps the prefix scoped by a colon boundary so ``abc`` does not
        accidentally match ``abcd``.
        """
        normalized_prefix = str(session_prefix or "").strip()
        if not normalized_prefix:
            return []

        exact_ids = {
            session_id.strip()
            for session_id in (extra_session_ids or [])
            if isinstance(session_id, str) and session_id.strip()
        }
        if normalized_prefix.endswith(":"):
            base_prefix = normalized_prefix[:-1]
            like_pattern = normalized_prefix + "%"
        else:
            base_prefix = normalized_prefix
            like_pattern = normalized_prefix + ":%"

        conditions = [
            ConversationMessage.session_id == base_prefix,
            ConversationMessage.session_id.like(like_pattern),
        ]
        for session_id in exact_ids:
            conditions.append(ConversationMessage.session_id == session_id)

        with self.get_session() as session:
            query = (
                select(
                    ConversationMessage.session_id.label("session_id"),
                    func.max(ConversationMessage.created_at).label("last_message_at"),
                )
                .where(or_(*conditions))
                .group_by(ConversationMessage.session_id)
                .order_by(desc("last_message_at"))
            )
            if limit and limit > 0:
                query = query.limit(int(limit))

            rows = session.execute(query).all()
            return [
                {
                    "session_id": row.session_id,
                    "last_message_at": row.last_message_at,
                }
                for row in rows
            ]

    def save_daily_data(self, df: pd.DataFrame, code: str, data_source: str = "Unknown") -> int:
        """批量保存日线数据；返回本次真正新增的行数。"""
        if df is None or df.empty:
            return 0

        normalized_rows: Dict[date, Dict[str, Any]] = {}
        for _, row in df.iterrows():
            row_date = self._normalize_daily_date(row.get("date"))
            normalized_rows[row_date] = {
                "code": code,
                "date": row_date,
                "open": self._normalize_sql_value(row.get("open")),
                "high": self._normalize_sql_value(row.get("high")),
                "low": self._normalize_sql_value(row.get("low")),
                "close": self._normalize_sql_value(row.get("close")),
                "volume": self._normalize_sql_value(row.get("volume")),
                "amount": self._normalize_sql_value(row.get("amount")),
                "pct_chg": self._normalize_sql_value(row.get("pct_chg")),
                "ma5": self._normalize_sql_value(row.get("ma5")),
                "ma10": self._normalize_sql_value(row.get("ma10")),
                "ma20": self._normalize_sql_value(row.get("ma20")),
                "volume_ratio": self._normalize_sql_value(row.get("volume_ratio")),
                "data_source": data_source,
                "updated_at": datetime.now(),
            }

        rows = list(normalized_rows.values())
        target_dates = [item["date"] for item in rows]

        def _write(session: Session) -> int:
            existing_dates = set(
                session.execute(
                    select(StockDaily.date).where(
                        and_(StockDaily.code == code, StockDaily.date.in_(target_dates))
                    )
                ).scalars().all()
            )
            new_count = sum(1 for item in rows if item["date"] not in existing_dates)

            if self._is_sqlite_engine:
                stmt = sqlite_insert(StockDaily).values(rows)
                update_columns = {
                    key: getattr(stmt.excluded, key)
                    for key in (
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "amount",
                        "pct_chg",
                        "ma5",
                        "ma10",
                        "ma20",
                        "volume_ratio",
                        "data_source",
                        "updated_at",
                    )
                }
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["code", "date"],
                        set_=update_columns,
                    )
                )
            else:
                for item in rows:
                    existing = session.execute(
                        select(StockDaily).where(
                            and_(StockDaily.code == item["code"], StockDaily.date == item["date"])
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        session.add(StockDaily(**item))
                        continue
                    for key, value in item.items():
                        if key in ("code", "date"):
                            continue
                        setattr(existing, key, value)

            return new_count

        return self._run_write_transaction(f"save_daily_data[{code}]", _write)

    def save_news_intel(
        self,
        news_items: Optional[List[Any]] = None,
        *,
        code: Optional[str] = None,
        name: Optional[str] = None,
        dimension: Optional[str] = None,
        query: Optional[str] = None,
        response: Optional['SearchResponse'] = None,
        query_context: Optional[Dict[str, str]] = None,
        query_id: Optional[str] = None,
    ) -> int:
        if response is not None:
            news_items = list(getattr(response, "results", []) or [])
        if not news_items:
            return 0
        now = datetime.now()
        query_ctx = dict(query_context or {})
        current_query_id = query_id or query_ctx.get("query_id")

        def _item_get(item: Any, key: str, default: Any = None) -> Any:
            if isinstance(item, dict):
                return item.get(key, default)
            return getattr(item, key, default)

        def _write(session: Session) -> int:
            new_count = 0
            for item in news_items:
                item_code = code or _item_get(item, 'code') or ""
                title = (_item_get(item, 'title') or "").strip()
                source = (_item_get(item, 'source') or "").strip()
                pub_date = self._parse_published_date(_item_get(item, 'published_date'))
                url = (_item_get(item, 'url') or "").strip() or self._build_fallback_url_key(
                    item_code,
                    title,
                    source,
                    pub_date,
                )
                if not title and not url:
                    continue
                
                record = {
                    'query_id': current_query_id or _item_get(item, 'query_id'),
                    'code': item_code,
                    'name': name or _item_get(item, 'name'),
                    'dimension': dimension or _item_get(item, 'dimension'),
                    'query': query or getattr(response, "query", None) or _item_get(item, 'query'),
                    'provider': getattr(response, "provider", None) or _item_get(item, 'provider'),
                    'title': title,
                    'snippet': _item_get(item, 'snippet'),
                    'url': url,
                    'source': source,
                    'published_date': pub_date,
                    'fetched_at': now,
                    'query_source': query_ctx.get("query_source") or _item_get(item, 'query_source', 'system'),
                    'requester_platform': query_ctx.get("requester_platform"),
                    'requester_user_id': query_ctx.get("requester_user_id"),
                    'requester_user_name': query_ctx.get("requester_user_name"),
                    'requester_chat_id': query_ctx.get("requester_chat_id"),
                    'requester_message_id': query_ctx.get("requester_message_id"),
                    'requester_query': query_ctx.get("requester_query"),
                }
                
                if self._is_sqlite_engine:
                    stmt = sqlite_insert(NewsIntel).values(record).on_conflict_do_nothing(index_elements=['url'])
                    if session.execute(stmt).rowcount > 0:
                        new_count += 1
                else:
                    if not session.execute(select(NewsIntel.id).where(NewsIntel.url == url)).scalar():
                        session.add(NewsIntel(**record))
                        new_count += 1
            return new_count
        return self._run_write_transaction("save_news_intel", _write)

    def save_analysis_history(
        self, 
        result: Any, 
        query_id: str, 
        query_source: str = "cli",
        report_type: str = "standard",
        news_content: Optional[str] = None,
        news_intel: List[Dict] = None,
        context_snapshot: Dict = None,
        save_snapshot: bool = False
    ) -> int:
        raw_result_json = json.dumps(result.to_dict(), ensure_ascii=False)
        sniper_points = result.get_sniper_points() if hasattr(result, "get_sniper_points") else {}
        now = datetime.now()
        history_payload = {
            "query_id": query_id,
            "code": result.code,
            "name": result.name,
            "report_type": report_type,
            "sentiment_score": result.sentiment_score,
            "operation_advice": result.operation_advice,
            "trend_prediction": result.trend_prediction,
            "analysis_summary": getattr(result, "analysis_summary", None),
            "raw_result": raw_result_json,
            "news_content": news_content,
            "context_snapshot": (
                json.dumps(context_snapshot, ensure_ascii=False)
                if save_snapshot and context_snapshot is not None
                else None
            ),
            "ideal_buy": self._parse_sniper_value(sniper_points.get("ideal_buy")),
            "secondary_buy": self._parse_sniper_value(sniper_points.get("secondary_buy")),
            "stop_loss": self._parse_sniper_value(sniper_points.get("stop_loss")),
            "take_profit": self._parse_sniper_value(sniper_points.get("take_profit")),
            "created_at": now,
            "decision_type": getattr(result, "decision_type", None) or "hold",
            "confidence_level": getattr(result, "confidence_level", None),
            "full_result_json": raw_result_json,
            "model_used": getattr(result, "model_used", None),
            "search_performed": getattr(result, "search_performed", False),
            "report_language": getattr(result, "report_language", "zh"),
            "current_price": getattr(result, "current_price", None),
            "change_pct": getattr(result, "change_pct", None),
            "analyzed_at": now,
            "query_source": query_source,
        }

        def _write(session: Session) -> int:
            history_table = Table("analysis_history", MetaData(), autoload_with=session.bind)
            supported_columns = {column.name for column in history_table.columns}
            insert_values = {
                key: value for key, value in history_payload.items() if key in supported_columns
            }
            session.execute(history_table.insert().values(**insert_values))

            if save_snapshot and context_snapshot and "context_snapshot" not in supported_columns:
                snapshot = FundamentalSnapshot(
                    query_id=query_id,
                    code=result.code,
                    payload=json.dumps(context_snapshot, ensure_ascii=False)
                )
                session.add(snapshot)
            
            return 1
        return self._run_write_transaction(f"save_analysis_history[{result.code}]", _write)

    def get_analysis_history(
        self,
        code: Optional[str] = None,
        query_id: Optional[str] = None,
        days: int = 30,
        limit: int = 100,
    ) -> List[Any]:
        history_table = Table("analysis_history", MetaData(), autoload_with=self._engine)
        created_at_col = history_table.c.get("created_at")
        if created_at_col is None:
            created_at_col = history_table.c.get("analyzed_at")

        stmt = select(history_table)
        if code:
            stmt = stmt.where(history_table.c.code == code)
        if query_id:
            stmt = stmt.where(history_table.c.query_id == query_id)
        if created_at_col is not None and days is not None:
            stmt = stmt.where(created_at_col >= datetime.now() - timedelta(days=max(days, 0)))
            stmt = stmt.order_by(created_at_col.desc())
        stmt = stmt.limit(limit)

        with self.get_session() as session:
            rows = session.execute(stmt).mappings().all()
            return [type("AnalysisHistoryRow", (), dict(row))() for row in rows]

    def get_analysis_history_by_id(self, record_id: int) -> Optional[AnalysisHistory]:
        """按主键读取分析历史记录。"""
        with self.get_session() as session:
            return session.get(AnalysisHistory, int(record_id))

    def get_latest_analysis_by_query_id(self, query_id: str) -> Optional[AnalysisHistory]:
        """按 query_id 读取最新分析历史记录。"""
        with self.get_session() as session:
            return session.execute(
                select(AnalysisHistory)
                .where(AnalysisHistory.query_id == query_id)
                .order_by(desc(AnalysisHistory.created_at))
                .limit(1)
            ).scalar_one_or_none()

    def delete_analysis_history_records(self, record_ids: List[int]) -> int:
        """删除分析历史记录，并清理关联回测结果。"""
        ids = [int(item) for item in (record_ids or [])]
        if not ids:
            return 0

        def _write(session: Session) -> int:
            session.query(BacktestResult).filter(BacktestResult.analysis_history_id.in_(ids)).delete(
                synchronize_session=False
            )
            deleted = session.query(AnalysisHistory).filter(AnalysisHistory.id.in_(ids)).delete(
                synchronize_session=False
            )
            return int(deleted or 0)

        return self._run_write_transaction("delete_analysis_history_records", _write)

    def get_news_intel_by_query_id(self, query_id: str, limit: int = 20) -> List[NewsIntel]:
        with self.get_session() as session:
            rows = session.execute(
                select(NewsIntel)
                .where(NewsIntel.query_id == query_id)
                .order_by(desc(NewsIntel.published_date), desc(NewsIntel.fetched_at))
                .limit(limit)
            ).scalars().all()
            return list(rows)

    def get_recent_news(self, code: str, days: int = 7, limit: int = 20) -> List[NewsIntel]:
        cutoff = datetime.now() - timedelta(days=max(days, 0))
        with self.get_session() as session:
            rows = session.execute(
                select(NewsIntel)
                .where(and_(NewsIntel.code == code, NewsIntel.fetched_at >= cutoff))
                .order_by(desc(NewsIntel.published_date), desc(NewsIntel.fetched_at))
                .limit(limit)
            ).scalars().all()
            return list(rows)

    def save_fundamental_snapshot(
        self,
        *,
        query_id: str,
        code: str,
        payload: Dict[str, Any],
        source_chain: Optional[List[str]] = None,
        coverage: Optional[Dict[str, Any]] = None,
    ) -> int:
        def _write(session: Session) -> int:
            session.add(
                FundamentalSnapshot(
                    query_id=query_id,
                    code=code,
                    payload=json.dumps(payload or {}, ensure_ascii=False),
                    source_chain=json.dumps(source_chain or [], ensure_ascii=False) if source_chain is not None else None,
                    coverage=json.dumps(coverage or {}, ensure_ascii=False) if coverage is not None else None,
                )
            )
            return 1

        return self._run_write_transaction(f"save_fundamental_snapshot[{query_id}:{code}]", _write)
    
    
    def _analyze_ma_status(self, data: StockDaily) -> str:
        """
        分析均线形态
        
        判断条件：
        - 多头排列：close > ma5 > ma10 > ma20
        - 空头排列：close < ma5 < ma10 < ma20
        - 震荡整理：其他情况
        """
        # 注意：这里的均线形态判断基于“close/ma5/ma10/ma20”静态比较，
        # 未考虑均线拐点、斜率、或不同数据源复权口径差异。
        # 该行为目前保留（按需求不改逻辑）。
        close = data.close or 0
        ma5 = data.ma5 or 0
        ma10 = data.ma10 or 0
        ma20 = data.ma20 or 0
        
        if close > ma5 > ma10 > ma20 > 0:
            return "多头排列 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "空头排列 📉"
        elif close > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif close < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"

    @staticmethod
    def _find_sniper_in_dashboard(payload: Dict[str, Any]) -> Dict[str, Any]:
        """从 dashboard/raw_result 中查找 sniper_points 结构。"""
        if not isinstance(payload, dict):
            return {}
        candidates = [
            payload.get("sniper_points"),
            payload.get("sniper_levels"),
            payload.get("specific_targets"),
        ]
        battle_plan = payload.get("battle_plan")
        if isinstance(battle_plan, dict):
            candidates.extend([
                battle_plan.get("sniper_points"),
                battle_plan.get("sniper_levels"),
                battle_plan.get("specific_targets"),
            ])
        decision_dashboard = payload.get("decision_dashboard")
        if isinstance(decision_dashboard, dict):
            candidates.append(DatabaseManager._find_sniper_in_dashboard(decision_dashboard))
        dashboard = payload.get("dashboard")
        if isinstance(dashboard, dict):
            candidates.append(DatabaseManager._find_sniper_in_dashboard(dashboard))

        for candidate in candidates:
            if isinstance(candidate, dict) and any(
                candidate.get(key) is not None
                for key in ("ideal_buy", "secondary_buy", "stop_loss", "take_profit", "buy_price", "stop_loss_price", "target_price")
            ):
                return {
                    "ideal_buy": candidate.get("ideal_buy", candidate.get("buy_price")),
                    "secondary_buy": candidate.get("secondary_buy"),
                    "stop_loss": candidate.get("stop_loss", candidate.get("stop_loss_price")),
                    "take_profit": candidate.get("take_profit", candidate.get("target_price")),
                }
        return {}

    def get_data_range(self, code: str, start_date: date, end_date: date) -> List[StockDaily]:
        """获取指定日期范围内的数据"""
        with self.get_session() as session:
            results = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date >= start_date, StockDaily.date <= end_date))
                .order_by(StockDaily.date.asc())
            ).scalars().all()
            return list(results)

    async def get_data_range_async(self, code: str, start_date: date, end_date: date) -> List[StockDaily]:
        """异步获取指定日期范围内的数据"""
        return await asyncio.to_thread(self.get_data_range, code, start_date, end_date)

    async def save_daily_data_async(self, df: pd.DataFrame, code: str, data_source: str = "Unknown") -> int:
        """异步保存日线数据"""
        return await asyncio.to_thread(self.save_daily_data, df, code, data_source)

    @staticmethod
    def _parse_sniper_value(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)

        text_value = str(value).strip()
        if not text_value:
            return None

        yuan_matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*元", text_value)
        if yuan_matches:
            try:
                return float(yuan_matches[-1])
            except (TypeError, ValueError):
                return None

        sanitized = re.sub(r"MA\d+(?:/M?\d+)*", " ", text_value, flags=re.IGNORECASE)
        number_matches: List[float] = []
        for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)", sanitized):
            end = match.end()
            if end < len(sanitized) and sanitized[end] == "%":
                continue
            try:
                number_matches.append(float(match.group(1)))
            except (TypeError, ValueError):
                continue

        if not number_matches:
            return None
        return number_matches[-1]

    async def save_analysis_history_async(
        self, 
        result: Any, 
        query_id: str, 
        report_type: str = "standard",
        news_content: Optional[str] = None,
        news_intel: List[Dict] = None,
        context_snapshot: Dict = None,
        save_snapshot: bool = False
    ) -> int:
        """异步保存分析历史"""
        query_source = result.query_source if hasattr(result, "query_source") else "cli"
        return await asyncio.to_thread(
            self.save_analysis_history, 
            result, query_id, query_source, report_type, news_content, news_intel, context_snapshot, save_snapshot
        )

    def get_analysis_context(self, code: str, target_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        recent = self.get_latest_data(code, days=2)
        if not recent: return None
        today_data = recent[0]
        context = {'code': code, 'date': today_data.date.isoformat(), 'today': today_data.to_dict()}
        if len(recent) > 1:
            yest = recent[1]
            context['yesterday'] = yest.to_dict()
            if yest.volume: context['volume_change_ratio'] = round(today_data.volume / yest.volume, 2)
            if yest.close: context['price_change_ratio'] = round((today_data.close - yest.close) / yest.close * 100, 2)
        return context

    def record_llm_usage(
        self,
        *,
        call_type: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: Optional[int] = None,
        stock_code: Optional[str] = None,
    ) -> int:
        total = (
            int(total_tokens)
            if total_tokens is not None
            else int(prompt_tokens or 0) + int(completion_tokens or 0)
        )

        def _write(session: Session) -> int:
            row = LLMUsage(
                call_type=str(call_type or "unknown"),
                model=str(model or "unknown"),
                stock_code=stock_code,
                prompt_tokens=int(prompt_tokens or 0),
                completion_tokens=int(completion_tokens or 0),
                total_tokens=total,
                called_at=datetime.now(),
            )
            session.add(row)
            session.flush()
            return int(row.id or 0)

        return self._run_write_transaction("record_llm_usage", _write)

    def get_llm_usage_summary(self, from_dt: datetime, to_dt: datetime) -> Dict[str, Any]:
        with self.get_session() as session:
            conditions = and_(LLMUsage.called_at >= from_dt, LLMUsage.called_at <= to_dt)
            total_calls = session.execute(
                select(func.count()).select_from(LLMUsage).where(conditions)
            ).scalar() or 0
            total_tokens = session.execute(
                select(func.coalesce(func.sum(LLMUsage.total_tokens), 0)).where(conditions)
            ).scalar() or 0
            by_call_type = session.execute(
                select(
                    LLMUsage.call_type,
                    func.count().label("calls"),
                    func.coalesce(func.sum(LLMUsage.total_tokens), 0).label("total_tokens"),
                )
                .where(conditions)
                .group_by(LLMUsage.call_type)
                .order_by(LLMUsage.call_type.asc())
            ).all()
            by_model = session.execute(
                select(
                    LLMUsage.model,
                    func.count().label("calls"),
                    func.coalesce(func.sum(LLMUsage.total_tokens), 0).label("total_tokens"),
                )
                .where(conditions)
                .group_by(LLMUsage.model)
                .order_by(LLMUsage.model.asc())
            ).all()

        return {
            "total_calls": int(total_calls),
            "total_tokens": int(total_tokens),
            "by_call_type": [
                {
                    "call_type": row.call_type,
                    "calls": int(row.calls or 0),
                    "total_tokens": int(row.total_tokens or 0),
                }
                for row in by_call_type
            ],
            "by_model": [
                {
                    "model": row.model,
                    "calls": int(row.calls or 0),
                    "total_tokens": int(row.total_tokens or 0),
                }
                for row in by_model
            ],
        }

    # --- Internal Helpers ---
    def _normalize_daily_date(self, val: Any) -> date:
        if isinstance(val, date): return val
        if isinstance(val, datetime): return val.date()
        return datetime.strptime(str(val), "%Y-%m-%d").date()

    def _normalize_sql_value(self, val: Any) -> Optional[float]:
        try: return float(val) if val is not None else None
        except Exception:
            logger.debug("_normalize_sql_value failed for val=%r", val)
            return None

    def _parse_published_date(self, value: Optional[str]) -> Optional[datetime]:
        if not value: return None
        try: return datetime.fromisoformat(str(value))
        except Exception:
            logger.debug("_parse_published_date failed for value=%r", value)
            return None

    def _build_fallback_url_key(self, code: str, title: str, source: str, pub_date: Optional[datetime]) -> str:
        raw = f"{code}|{title}|{source}|{pub_date}"
        return f"no-url:{code}:{hashlib.md5(raw.encode()).hexdigest()}"


class StorageManager(DatabaseManager):
    """Alias for backward compatibility"""
    @classmethod
    def get_instance(cls) -> 'StorageManager':
        return super().get_instance()

def get_db() -> DatabaseManager:
    return DatabaseManager.get_instance()

def persist_llm_usage(usage: Dict[str, Any], model: str, call_type: str, stock_code: Optional[str] = None) -> None:
    try:
        db = DatabaseManager.get_instance()
        row = LLMUsage(
            call_type=call_type, model=model, stock_code=stock_code,
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
            total_tokens=usage.get("total_tokens", 0) or 0
        )
        with db.session_scope() as session:
            session.add(row)
    except Exception as e:
        logger.warning("Persist LLM usage failed: %s", e)