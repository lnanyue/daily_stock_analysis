# -*- coding: utf-8 -*-
"""
基本面分析流水线 - 负责聚合估值、成长、盈利、机构、资金流、龙虎榜等数据。
"""

import logging
import time
import asyncio
from datetime import datetime, date, timedelta
from threading import BoundedSemaphore, Thread
from typing import Any, Dict, List, Optional, Tuple, Callable

import pandas as pd
import numpy as np

try:
    import akshare as ak
except ImportError:
    ak = None  # 惰性处理：使用 ak 的方法会自行处理缺失

from .utils import normalize_stock_code, _market_tag, _is_etf_code, summarize_exception, run_async_sync
from .exceptions import DataFetchError
from .fundamental_adapter import AkshareFundamentalAdapter
from .normalizers import normalize_source_chain, normalize_belong_boards
from src.schemas.fundamental_context import FundamentalContext, PeerComparisonBlock, PeerComparisonData, PeerComparisonEntry

logger = logging.getLogger(__name__)

class FundamentalPipeline:
    """
    基本面数据聚合流水线。
    支持 Fail-open 语义、多级缓存、并发控制及耗时分析。
    """

    def __init__(self, manager: Any):
        from src.config import get_config
        self.config = get_config()
        self.manager = manager
        self.adapter = AkshareFundamentalAdapter()
        self._timeout_slots = BoundedSemaphore(8)

    async def get_fundamental_context(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        聚合基本面区块，返回符合 FundamentalContext 模型的数据。
        """
        if not getattr(self.config, "enable_fundamental_pipeline", True):
            return self._build_failed_context(stock_code, "fundamental pipeline disabled")

        stock_code = normalize_stock_code(stock_code)
        market = _market_tag(stock_code)
        is_etf = _is_etf_code(stock_code)
        
        if market not in {"cn"}:
            return self.adapter.get_fundamental_context(stock_code)

        start_ts = time.time()
        timeout = float(budget_seconds or getattr(self.config, "fundamental_stage_timeout_seconds", 1.5))
        remaining_seconds = timeout
        fetch_timeout = float(getattr(self.config, "fundamental_fetch_timeout_seconds", 1.0))

        # 初始化模型数据
        ctx = FundamentalContext(market=market)

        def _consume_budget(consumed_ms: int) -> None:
            nonlocal remaining_seconds
            remaining_seconds = max(0.0, remaining_seconds - consumed_ms / 1000.0)

        # 1. Valuation
        valuation_timeout = min(fetch_timeout, remaining_seconds)
        quote_payload = None
        valuation_err = None
        valuation_ms = 0
        if valuation_timeout > 0:
            try:
                quote_payload = await self.manager.get_realtime_quote(stock_code)
                valuation_ms = int((time.time() - start_ts) * 1000)
            except Exception as e:
                valuation_err = str(e)
            _consume_budget(valuation_ms)

        valuation_data = {
            "price": getattr(quote_payload, "price", None) if quote_payload else None,
            "pe_ratio": getattr(quote_payload, "pe_ratio", None) if quote_payload else None,
            "pb_ratio": getattr(quote_payload, "pb_ratio", None) if quote_payload else None,
            "total_mv": getattr(quote_payload, "total_mv", None) if quote_payload else None,
            "circ_mv": getattr(quote_payload, "circ_mv", None) if quote_payload else None,
        }
        val_status = self._infer_block_status(valuation_data, "partial" if quote_payload else "not_supported")
        ctx.valuation = self._build_fundamental_block(val_status, valuation_data, [{"provider": "realtime_quote", "result": val_status}], [valuation_err] if valuation_err else [])

        # 2. Bundle (Growth, Earnings, Institution, Valuation Fallback)
        if remaining_seconds > 0:
            bundle_payload, bundle_err, bundle_ms = await self._run_with_timeout_async(lambda: self.adapter.get_fundamental_bundle(stock_code), min(fetch_timeout, remaining_seconds), "fundamental_bundle")
            _consume_budget(bundle_ms)
            if isinstance(bundle_payload, dict):
                bundle_status = str(bundle_payload.get("status", "partial"))
                bundle_chain = bundle_payload.get("source_chain", [])
                bundle_errors = list(bundle_payload.get("errors", []))
                if bundle_err: bundle_errors.append(bundle_err)
                
                for block_name in ["valuation", "growth", "earnings", "institution"]:
                    data = bundle_payload.get(block_name, {})
                    if not data: continue
                    
                    block_errors = list(bundle_errors)
                    if block_name == "earnings" and "dividend" in data:
                        block_errors.extend(self._inject_dividend_yield(data["dividend"], valuation_data.get("price")))
                    
                    # 特殊处理：如果是 valuation，执行合并逻辑
                    if block_name == "valuation":
                        existing_val = getattr(ctx, "valuation", None)
                        if existing_val and hasattr(existing_val, "data"):
                            merged_data = dict(existing_val.data)
                            # 仅补全缺失值
                            for k, v in data.items():
                                if merged_data.get(k) is None:
                                    merged_data[k] = v
                            
                            status = self._infer_block_status(merged_data, bundle_status)
                            ctx.valuation = self._build_fundamental_block(status, merged_data, bundle_chain, block_errors)
                        continue

                    status = self._infer_block_status(data, bundle_status)
                    block_obj = self._build_fundamental_block(status, data, bundle_chain, block_errors)
                    setattr(ctx, block_name, block_obj)

        # 3. Capital Flow / Dragon Tiger
        if not is_etf and remaining_seconds > 0:
            cf_budget = min(fetch_timeout, remaining_seconds)
            # 支持测试/插件通过 manager mock 覆盖
            cf_override = getattr(self.manager, "__dict__", {}).get("get_capital_flow_context")
            if cf_override is not None:
                ctx.capital_flow = cf_override(stock_code, cf_budget)
            else:
                ctx.capital_flow = await self.get_capital_flow_context_async(stock_code, cf_budget)

            dt_budget = min(fetch_timeout, remaining_seconds)
            dt_override = getattr(self.manager, "__dict__", {}).get("get_dragon_tiger_context")
            if dt_override is not None:
                ctx.dragon_tiger = dt_override(stock_code, dt_budget)
            else:
                ctx.dragon_tiger = await self.get_dragon_tiger_context_async(stock_code, dt_budget)
            # 行业对标
            ctx.peer_comparison = await self.get_peer_comparison_context(stock_code)
        else:
            not_supported = self._build_fundamental_block("not_supported", {}, [], ["etf not supported"])
            ctx.capital_flow = not_supported
            ctx.dragon_tiger = not_supported

        if is_etf:
            ctx.boards = self._build_fundamental_block("not_supported", {}, [], ["etf not supported"])
        else:
            board_budget = min(fetch_timeout, remaining_seconds)
            board_override = getattr(self.manager, "__dict__", {}).get("get_board_context")
            if board_override is not None:
                ctx.boards = board_override(stock_code, board_budget)
            else:
                ctx.boards = self.get_board_context(stock_code, board_budget)
        
        # 4. Status and Coverage
        ctx.coverage = {}
        for k in ["valuation", "growth", "earnings", "institution", "capital_flow", "dragon_tiger", "boards"]:
            if hasattr(ctx, k):
                obj = getattr(ctx, k)
                ctx.coverage[k] = obj.get("status") if isinstance(obj, dict) else obj.status
        ctx.status = "ok" if all(v == "ok" for v in ctx.coverage.values()) else "partial"
        ctx.elapsed_ms = int((time.time() - start_ts) * 1000)
        
        return ctx.model_dump(warnings=False)

    def _inject_dividend_yield(self, dividend_payload: Dict, price: Optional[float]) -> List[str]:
        ttm_cash = dividend_payload.get("ttm_cash_dividend_per_share")
        if ttm_cash is None: return []
        if not price or price <= 0:
            dividend_payload["ttm_dividend_yield_pct"] = None
            return ["invalid_price_for_ttm_dividend_yield"]
        try:
            dividend_payload["ttm_dividend_yield_pct"] = round(float(ttm_cash) / float(price) * 100, 4)
            dividend_payload["yield_formula"] = "ttm_cash_dividend_per_share / latest_price * 100"
        except (TypeError, ValueError):
            return ["invalid_ttm_cash_dividend_for_yield"]
        return []

    def _infer_block_status(self, payload: Any, fallback: str) -> str:
        if not payload: return fallback
        if isinstance(payload, dict) and not any(v not in (None, "", [], {}) for v in payload.values()): return fallback
        return "ok"

    def _build_fundamental_block(self, status: str, data: Any, chain: List, errors: List) -> Dict[str, Any]:
        return {"status": status, "data": data, "source_chain": normalize_source_chain(chain), "errors": errors}

    def _build_failed_context(self, stock_code: str, reason: str) -> Dict[str, Any]:
        return {"market": _market_tag(stock_code), "status": "failed", "errors": [reason]}

    def _run_with_timeout(self, func: Callable, timeout: float, label: str, slots=None) -> Tuple[Any, Any, int]:
        """同步版：保留给非异步调用方使用（如 manager._run_with_timeout）。"""
        _slots = slots if slots is not None else self._timeout_slots
        start = time.time()
        if _slots is not None and not _slots.acquire(blocking=False):
             return None, "worker pool exhausted", 0
        outcome = {}
        def _target():
            try: outcome["res"] = func()
            except Exception as e: outcome["err"] = str(e)
            finally:
                if _slots is not None: _slots.release()
        t = Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout)
        elapsed_ms = int((time.time() - start) * 1000)
        if t.is_alive(): return None, f"{label} timeout", elapsed_ms
        return outcome.get("res"), outcome.get("err"), elapsed_ms

    async def _run_with_timeout_async(self, func: Callable, timeout: float, label: str, slots=None) -> Tuple[Any, Any, int]:
        """异步版：使用 asyncio.wait_for + to_thread 实现可取消的超时控制。"""
        _slots = slots if slots is not None else self._timeout_slots
        if _slots is not None and not _slots.acquire(blocking=False):
            return None, "worker pool exhausted", 0
        start = time.time()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(func), timeout=timeout)
            return result, None, int((time.time() - start) * 1000)
        except asyncio.TimeoutError:
            return None, f"{label} timeout", int((time.time() - start) * 1000)
        except Exception as e:
            return None, str(e), int((time.time() - start) * 1000)
        finally:
            if _slots is not None:
                _slots.release()

    async def get_capital_flow_context_async(self, stock_code: str, budget_seconds: float = 1.0) -> Dict[str, Any]:
        res, err, ms = await self._run_with_timeout_async(lambda: self.adapter.get_capital_flow(stock_code), budget_seconds, "capital_flow")
        if isinstance(res, dict): return self._build_fundamental_block(res.get("status", "ok"), {"stock_flow": res.get("stock_flow", {}), "sector_rankings": res.get("sector_rankings", {})}, res.get("source_chain", []), res.get("errors", []))
        return self._build_fundamental_block("failed", {}, [], [err or "failed"])

    async def get_dragon_tiger_context_async(self, stock_code: str, budget_seconds: float = 1.0) -> Dict[str, Any]:
        res, err, ms = await self._run_with_timeout_async(lambda: self.adapter.get_dragon_tiger_flag(stock_code), budget_seconds, "dragon_tiger")
        if isinstance(res, dict): return self._build_fundamental_block(res.get("status", "ok"), {"is_on_list": res.get("is_on_list", False), "recent_count": res.get("recent_count", 0), "latest_date": res.get("latest_date")}, res.get("source_chain", []), res.get("errors", []))
        return self._build_fundamental_block("failed", {}, [], [err or "failed"])

    def get_capital_flow_context(self, stock_code: str, budget_seconds: float = 1.0) -> Dict[str, Any]:
        return run_async_sync(self.get_capital_flow_context_async, stock_code, budget_seconds)

    def get_dragon_tiger_context(self, stock_code: str, budget_seconds: float = 1.0) -> Dict[str, Any]:
        return run_async_sync(self.get_dragon_tiger_context_async, stock_code, budget_seconds)

    def get_board_context(self, stock_code: str, budget_seconds: float = 1.0) -> Dict[str, Any]:
        top, bottom, chain, err = self._get_sector_rankings_with_meta(5)
        status = "ok" if top or bottom else "failed"
        data = {"top": top, "bottom": bottom} if status == "ok" else {}
        return self._build_fundamental_block(status, data, chain, [err] if err else [])

    async def get_peer_comparison_context(self, stock_code: str) -> Dict[str, Any]:
        """行业横向对比区块（含深度财务指标）。"""
        stock_code = normalize_stock_code(stock_code)
        target_industry = None
        all_stocks = None
        
        fetchers = self.manager.fetchers
        for fetcher in fetchers:
            if fetcher.name == "TushareFetcher" and hasattr(fetcher, "get_stock_list"):
                all_stocks = await asyncio.to_thread(fetcher.get_stock_list)
                if all_stocks is not None and not all_stocks.empty:
                    row = all_stocks[all_stocks['code'] == stock_code]
                    if not row.empty:
                        target_industry = row.iloc[0].get('industry')
                        break
        
        if not target_industry:
            return self._build_fundamental_block("not_supported", {}, [], ["Industry information not found"])

        if ak is None:
            logger.warning("akshare 未安装，跳过同行对比 AI 行情")
            return self._build_fundamental_block("failed", {}, [], ["akshare not available"])

        ak_spot = None
        for fetcher in fetchers:
            if fetcher.name == "AkshareFetcher":
                try:
                    ak_spot = await asyncio.to_thread(ak.stock_zh_a_spot_em)
                    break
                except Exception: continue
        
        if ak_spot is None or ak_spot.empty:
            return self._build_fundamental_block("failed", {}, [], ["Market spot data unavailable"])

        try:
            ak_spot['code'] = ak_spot['代码'].astype(str)
            industry_peers_codes = all_stocks[all_stocks['industry'] == target_industry]['code'].tolist()
            peers_spot = ak_spot[ak_spot['code'].isin(industry_peers_codes)].copy()
            
            mv_col = '总市值' if '总市值' in peers_spot.columns else 'total_mv'
            peers_spot = peers_spot.sort_values(mv_col, ascending=False)
            
            target_row = peers_spot[peers_spot['code'] == stock_code]
            top_peers_df = peers_spot[peers_spot['code'] != stock_code].head(3)
            final_peers_df = pd.concat([target_row, top_peers_df])
            peer_codes = final_peers_df['code'].tolist()

            async def _fetch_peer_financials(code: str) -> Dict[str, Any]:
                try:
                    bundle = await asyncio.to_thread(self.adapter.get_fundamental_bundle, code)
                    growth = bundle.get("growth", {})
                    earnings = bundle.get("earnings", {}).get("financial_report", {})
                    return {
                        "code": code,
                        "roe": growth.get("roe") or earnings.get("roe"),
                        "revenue_yoy": growth.get("revenue_yoy"),
                        "net_profit_yoy": growth.get("net_profit_yoy"),
                        "gross_margin": growth.get("gross_margin")
                    }
                except Exception: return {"code": code}

            fin_tasks = [_fetch_peer_financials(c) for c in peer_codes]
            fin_results = await asyncio.gather(*fin_tasks, return_exceptions=True)
            fin_map: Dict[str, Any] = {}
            for res in fin_results:
                if isinstance(res, Exception):
                    logger.warning("同行财务数据获取失败: %s", res)
                    continue
                if isinstance(res, dict) and "code" in res:
                    fin_map[res["code"]] = res

            comparison_list = []
            for _, row in final_peers_df.iterrows():
                code = row['code']
                fin = fin_map.get(code, {})
                comparison_list.append({
                    "code": code,
                    "name": row['名称'],
                    "price": row['最新价'],
                    "change_pct": row['涨跌幅'],
                    "pe_ttm": row.get('动态市盈率', 'N/A'),
                    "pb": row.get('市净率', 'N/A'),
                    "market_cap": round(row[mv_col] / 1e8, 2) if mv_col in row else 'N/A',
                    "roe": fin.get("roe", "N/A"),
                    "revenue_yoy": fin.get("revenue_yoy", "N/A"),
                    "net_profit_yoy": fin.get("net_profit_yoy", "N/A"),
                    "gross_margin": fin.get("gross_margin", "N/A"),
                    "is_target": code == stock_code
                })

            return self._build_fundamental_block("ok", {
                "industry": target_industry,
                "comparison": comparison_list,
                "peer_count": len(peers_spot)
            }, ["tushare:industry", "akshare:spot_em", "akshare:financials"], [])
            
        except Exception as e:
            logger.error(f"[Peer Comparison] 失败: {e}", exc_info=True)
            return self._build_fundamental_block("failed", {}, [], [str(e)])

    def _get_sector_rankings_with_meta(self, n: int = 5):
        source_chain: List[Dict[str, Any]] = []
        last_error = ""
        for fetcher in self.manager.fetchers:
            if not hasattr(fetcher, 'get_sector_rankings'): continue
            start = time.time()
            try:
                data = fetcher.get_sector_rankings(n)
                duration_ms = int((time.time() - start) * 1000)
                if data and data[0] is not None:
                    source_chain.append({"provider": fetcher.name, "result": "ok", "duration_ms": duration_ms})
                    return data[0], data[1], source_chain, ""
                source_chain.append({"provider": fetcher.name, "result": "empty", "duration_ms": duration_ms})
            except Exception as e:
                last_error = str(e)
                source_chain.append({"provider": fetcher.name, "result": "failed", "duration_ms": int((time.time() - start) * 1000), "error": str(e)})
        return [], [], source_chain, last_error
