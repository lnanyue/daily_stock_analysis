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
import hashlib
import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Tuple, Callable, TypeVar

import pandas as pd
from sqlalchemy import (
    create_engine,
    Column,
    String,
    Float,
    Boolean,
    Date,
    DateTime,
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
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError

from src.config import get_config
from src.schemas.storage_models import (
    Base,
    StockDaily,
    NewsIntel,
    FundamentalSnapshot,
    AnalysisHistory,
    LLMUsage,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


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

        engine_kwargs = {"echo": False, "pool_pre_ping": True}
        if str(db_url).startswith("sqlite:") and self._sqlite_busy_timeout_ms > 0:
            engine_kwargs["connect_args"] = {"timeout": self._sqlite_busy_timeout_ms / 1000}

        self._engine = create_engine(db_url, **engine_kwargs)
        self._is_sqlite_engine = self._engine.url.get_backend_name() == 'sqlite'
        self._install_sqlite_pragma_handler()
        
        self._SessionLocal = sessionmaker(bind=self._engine, autocommit=False, autoflush=False)
        
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

    @staticmethod
    def _cleanup_engine(engine):
        try:
            engine.dispose()
        except Exception:
            pass

    def _install_sqlite_pragma_handler(self):
        if not self._is_sqlite_engine: return
        @event.listens_for(self._engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            if self._sqlite_wal_enabled:
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                except Exception: pass
            if self._sqlite_busy_timeout_ms > 0:
                try: cursor.execute(f"PRAGMA busy_timeout={self._sqlite_busy_timeout_ms}")
                except Exception: pass
            cursor.close()

    @contextmanager
    def get_session(self) -> Session:
        session = self._SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def session_scope(self):
        return self.get_session()

    def _run_write_transaction(self, name: str, operation: Callable[[Session], T]) -> T:
        with self.get_session() as session:
            if self._is_sqlite_engine:
                session.execute(Text("BEGIN IMMEDIATE"))
            
            retry_count = 0
            while retry_count <= self._sqlite_write_retry_max:
                try:
                    return operation(session)
                except OperationalError as e:
                    if "database is locked" in str(e) and retry_count < self._sqlite_write_retry_max:
                        retry_count += 1
                        time.sleep(self._sqlite_write_retry_base_delay * (2 ** (retry_count - 1)))
                        continue
                    raise
                except Exception: raise

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

    def save_news_intel(self, news_items: List[Dict[str, Any]]) -> int:
        if not news_items: return 0
        now = datetime.now()
        
        def _write(session: Session) -> int:
            new_count = 0
            for item in news_items:
                pub_date = self._parse_published_date(item.get('published_date'))
                url = item.get('url') or self._build_fallback_url_key(item.get('code',''), item.get('title',''), item.get('source',''), pub_date)
                
                record = {
                    'query_id': item.get('query_id'), 'code': item.get('code'), 'name': item.get('name'),
                    'dimension': item.get('dimension'), 'query': item.get('query'), 'provider': item.get('provider'),
                    'title': item.get('title'), 'snippet': item.get('snippet'), 'url': url,
                    'source': item.get('source'), 'published_date': pub_date, 'fetched_at': now,
                    'query_source': item.get('query_source', 'system'),
                }
                
                if self._is_sqlite_engine:
                    stmt = sqlite_insert(NewsIntel).values(record).on_conflict_do_nothing(index_elements=['url'])
                    if session.execute(stmt).rowcount > 0: new_count += 1
                else:
                    if not session.execute(select(NewsIntel.id).where(NewsIntel.url == url)).scalar():
                        session.add(NewsIntel(**record))
                        new_count += 1
            return new_count
        return self._run_write_transaction("save_news_intel", _write)

    def save_analysis_history(self, result: Any, query_id: str, query_source: str = "cli") -> int:
        def _write(session: Session) -> int:
            session.add(AnalysisHistory(
                query_id=query_id, code=result.code, name=result.name,
                sentiment_score=result.sentiment_score, trend_prediction=result.trend_prediction,
                operation_advice=result.operation_advice, decision_type=result.decision_type or 'hold',
                confidence_level=result.confidence_level, full_result_json=json.dumps(result.to_dict(), ensure_ascii=False),
                model_used=result.model_used, search_performed=result.search_performed,
                report_language=result.report_language, current_price=result.current_price,
                change_pct=result.change_pct, query_source=query_source, analyzed_at=datetime.now(),
            ))
            return 1
        return self._run_write_transaction(f"save_analysis_history[{result.code}]", _write)

    def save_daily_data(self, df: pd.DataFrame, code: str, data_source: str = "Unknown") -> int:
        if df is None or df.empty: return 0
        now = datetime.now()
        def _write(session: Session) -> int:
            new_count = 0
            for row in df.to_dict(orient='records'):
                row_date = self._normalize_daily_date(row.get('date'))
                record = {
                    'code': code, 'date': row_date, 'open': self._normalize_sql_value(row.get('open')),
                    'high': self._normalize_sql_value(row.get('high')), 'low': self._normalize_sql_value(row.get('low')),
                    'close': self._normalize_sql_value(row.get('close')), 'volume': self._normalize_sql_value(row.get('volume')),
                    'amount': self._normalize_sql_value(row.get('amount')), 'pct_chg': self._normalize_sql_value(row.get('pct_chg')),
                    'ma5': self._normalize_sql_value(row.get('ma5')), 'ma10': self._normalize_sql_value(row.get('ma10')),
                    'ma20': self._normalize_sql_value(row.get('ma20')), 'volume_ratio': self._normalize_sql_value(row.get('volume_ratio')),
                    'data_source': data_source, 'created_at': now, 'updated_at': now,
                }
                stmt = sqlite_insert(StockDaily).values(record).on_conflict_do_update(
                    index_elements=['code', 'date'],
                    set_={k: v for k, v in record.items() if k not in ('code', 'date', 'created_at')}
                )
                if session.execute(stmt).rowcount > 0: new_count += 1
            return new_count
        return self._run_write_transaction(f"save_daily_data[{code}]", _write)

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

    # --- Internal Helpers ---
    def _normalize_daily_date(self, val: Any) -> date:
        if isinstance(val, date): return val
        if isinstance(val, datetime): return val.date()
        return datetime.strptime(str(val), "%Y-%m-%d").date()

    def _normalize_sql_value(self, val: Any) -> Optional[float]:
        try: return float(val) if val is not None else None
        except: return None

    def _parse_published_date(self, value: Optional[str]) -> Optional[datetime]:
        if not value: return None
        try: return datetime.fromisoformat(str(value))
        except: return None

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
