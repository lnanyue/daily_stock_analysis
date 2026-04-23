# -*- coding: utf-8 -*-
"""
ORM 数据模型定义
"""

from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy import (
    Column,
    String,
    Float,
    Boolean,
    Date,
    DateTime,
    Integer,
    UniqueConstraint,
    Index,
    Text,
)
from sqlalchemy.orm import declarative_base

# SQLAlchemy ORM 基类
Base = declarative_base()


class StockDaily(Base):
    """
    股票日线数据模型
    """
    __tablename__ = 'stock_daily'
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    pct_chg = Column(Float)
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    volume_ratio = Column(Float)
    data_source = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('code', 'date', name='uix_code_date'),
        Index('ix_code_date', 'code', 'date'),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code, 'date': self.date, 'open': self.open, 'high': self.high,
            'low': self.low, 'close': self.close, 'volume': self.volume, 'amount': self.amount,
            'pct_chg': self.pct_chg, 'ma5': self.ma5, 'ma10': self.ma10, 'ma20': self.ma20,
            'volume_ratio': self.volume_ratio, 'data_source': self.data_source,
        }


class NewsIntel(Base):
    """
    新闻情报数据模型
    """
    __tablename__ = 'news_intel'
    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), index=True)
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(50))
    dimension = Column(String(32), index=True)
    query = Column(String(255))
    provider = Column(String(32), index=True)
    title = Column(String(300), nullable=False)
    snippet = Column(Text)
    url = Column(String(1000), nullable=False)
    source = Column(String(100))
    published_date = Column(DateTime, index=True)
    fetched_at = Column(DateTime, default=datetime.now, index=True)
    query_source = Column(String(32), index=True)
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


class FundamentalSnapshot(Base):
    """
    基本面上下文快照
    """
    __tablename__ = 'fundamental_snapshot'
    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    source_chain = Column(Text)
    coverage = Column(Text)


class AnalysisHistory(Base):
    """
    分析历史记录模型
    """
    __tablename__ = 'analysis_history'
    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(64), nullable=False, index=True, unique=True)
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(50))
    report_type = Column(String(16), index=True)
    sentiment_score = Column(Integer)
    trend_prediction = Column(String(50))
    operation_advice = Column(String(50))
    analysis_summary = Column(Text)
    raw_result = Column(Text)
    news_content = Column(Text)
    context_snapshot = Column(Text)
    ideal_buy = Column(Float)
    secondary_buy = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    created_at = Column(DateTime, default=datetime.now, index=True)
    decision_type = Column(String(10), index=True)
    confidence_level = Column(String(10))
    full_result_json = Column(Text)
    model_used = Column(String(100), index=True)
    search_performed = Column(Boolean, default=False)
    report_language = Column(String(10), default='zh')
    current_price = Column(Float)
    change_pct = Column(Float)
    analyzed_at = Column(DateTime, default=datetime.now, index=True)
    query_source = Column(String(32), index=True)

    __table_args__ = (
        Index('ix_history_code_date', 'code', 'analyzed_at'),
    )


class BacktestResult(Base):
    """回测结果"""
    __tablename__ = 'backtest_results'
    id = Column(Integer, primary_key=True, autoincrement=True)
    backtest_id = Column(String(64), index=True)
    code = Column(String(16), index=True)
    name = Column(String(64))
    start_date = Column(Date)
    end_date = Column(Date)
    total_return = Column(Float)
    max_drawdown = Column(Float)
    sharpe_ratio = Column(Float)
    win_rate = Column(Float)
    trades_count = Column(Integer)
    params_json = Column(Text)
    metrics_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class BacktestSummary(Base):
    """回测汇总"""
    __tablename__ = 'backtest_summaries'
    id = Column(Integer, primary_key=True, autoincrement=True)
    summary_id = Column(String(64), index=True)
    strategy_name = Column(String(64))
    market_condition = Column(String(64))
    overall_win_rate = Column(Float)
    avg_profit_per_trade = Column(Float)
    details_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class PortfolioAccount(Base):
    """持仓账户表"""
    __tablename__ = 'portfolio_accounts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(100))
    base_currency = Column(String(10), default='CNY')
    initial_balance = Column(Float, default=0.0)
    current_balance = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.now)


class PortfolioTrade(Base):
    """交易记录表"""
    __tablename__ = 'portfolio_trades'
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(64), index=True)
    code = Column(String(16), index=True)
    name = Column(String(100))
    trade_type = Column(String(10)) # buy/sell
    price = Column(Float)
    quantity = Column(Float)
    amount = Column(Float)
    commission = Column(Float, default=0.0)
    tax = Column(Float, default=0.0)
    trade_date = Column(Date, index=True)
    created_at = Column(DateTime, default=datetime.now)


class PortfolioCashLedger(Base):
    """现金流水账"""
    __tablename__ = 'portfolio_cash_ledger'
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(64), index=True)
    action_type = Column(String(20)) # deposit/withdraw/trade_buy/trade_sell/dividend
    amount = Column(Float)
    balance_after = Column(Float)
    description = Column(Text)
    action_date = Column(Date, index=True)
    created_at = Column(DateTime, default=datetime.now)


class PortfolioCorporateAction(Base):
    """公司行为（分红送股）"""
    __tablename__ = 'portfolio_corporate_actions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), index=True)
    action_type = Column(String(20)) # dividend/split/bonus
    ex_date = Column(Date, index=True)
    dividend_per_share = Column(Float, default=0.0)
    split_ratio = Column(Float, default=1.0)
    description = Column(Text)


class PortfolioPosition(Base):
    """当前持仓汇总"""
    __tablename__ = 'portfolio_positions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(64), index=True)
    code = Column(String(16), index=True)
    name = Column(String(100))
    quantity = Column(Float, default=0.0)
    avg_price = Column(Float, default=0.0)
    current_price = Column(Float, default=0.0)
    market_value = Column(Float, default=0.0)
    profit_loss = Column(Float, default=0.0)
    profit_loss_pct = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=datetime.now)


class PortfolioPositionLot(Base):
    """持仓明细 (Lots)"""
    __tablename__ = 'portfolio_position_lots'
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(64), index=True)
    code = Column(String(16), index=True)
    purchase_date = Column(Date)
    purchase_price = Column(Float)
    initial_quantity = Column(Float)
    current_quantity = Column(Float)


class PortfolioDailySnapshot(Base):
    """每日净值快照"""
    __tablename__ = 'portfolio_daily_snapshots'
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(64), index=True)
    snapshot_date = Column(Date, index=True)
    total_assets = Column(Float)
    cash_balance = Column(Float)
    market_value = Column(Float)
    daily_profit = Column(Float)
    cumulative_profit = Column(Float)


class PortfolioFxRate(Base):
    """汇率表"""
    __tablename__ = 'portfolio_fx_rates'
    id = Column(Integer, primary_key=True, autoincrement=True)
    from_currency = Column(String(10))
    to_currency = Column(String(10))
    rate = Column(Float)
    rate_date = Column(Date, index=True)


class ConversationMessage(Base):
    """Agent 对话历史记录表"""
    __tablename__ = 'conversation_messages'
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, index=True)


class LLMUsage(Base):
    """Token 使用审计日志"""
    __tablename__ = 'llm_usage'
    id = Column(Integer, primary_key=True, autoincrement=True)
    call_type = Column(String(32), nullable=False, index=True)
    model = Column(String(128), nullable=False)
    stock_code = Column(String(16), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    called_at = Column(DateTime, default=datetime.now, index=True)
