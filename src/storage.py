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
    MetaData,
    Table,
    text,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError

from src.config import get_config
from src.schemas.storage_models import (
    Base,
    AnalysisHistory,
    BacktestResult,
    BacktestSummary,
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
        
        self._SessionLocal = sessionmaker(
            bind=self._engine,
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
            for obj in list(session.dirty):
                if isinstance(obj, AnalysisHistory) and isinstance(getattr(obj, "raw_result", None), dict):
                    obj.raw_result = json.dumps(obj.raw_result, ensure_ascii=False)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def session_scope(self):
        with self.get_session() as session:
            yield session

    def _run_write_transaction(self, name: str, operation: Callable[[Session], T]) -> T:
        with self.get_session() as session:
            if self._is_sqlite_engine:
                session.execute(text("BEGIN IMMEDIATE"))
            
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
        snapshot = FundamentalSnapshot(
            query_id=query_id,
            code=code,
            payload=json.dumps(payload or {}, ensure_ascii=False),
            source_chain=json.dumps(source_chain or [], ensure_ascii=False) if source_chain is not None else None,
            coverage=json.dumps(coverage or {}, ensure_ascii=False) if coverage is not None else None,
        )

        def _write(session: Session) -> int:
            session.add(snapshot)
            return 1

        return self._run_write_transaction(f"save_fundamental_snapshot[{code}]", _write)

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
