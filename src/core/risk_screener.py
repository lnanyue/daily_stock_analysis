# -*- coding: utf-8 -*-
"""
排雷筛选 —— 独立运行流程，基于硬编码规则，LLM 仅用于证据总结。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    RED = "red"         # 一票否决：ST、退市风险、监管立案、审计保留意见
    YELLOW = "yellow"   # 预警：ROE为负、资产负债率>80%、营收下滑、PE异常
    GREEN = "green"     # 通过：无显著风险


@dataclass
class RiskFlag:
    """单个排雷检查结果。"""
    rule_name: str
    level: RiskLevel
    evidence: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskScreenResult:
    """单只股票的完整排雷结果。"""
    code: str
    name: str
    overall_level: RiskLevel = RiskLevel.GREEN
    flags: List[RiskFlag] = field(default_factory=list)
    summary: str = ""
    timestamp: str = ""

    @property
    def is_red(self) -> bool:
        return self.overall_level == RiskLevel.RED

    @property
    def is_yellow(self) -> bool:
        return self.overall_level == RiskLevel.YELLOW

    @property
    def red_flags(self) -> List[RiskFlag]:
        return [f for f in self.flags if f.level == RiskLevel.RED]

    @property
    def yellow_flags(self) -> List[RiskFlag]:
        return [f for f in self.flags if f.level == RiskLevel.YELLOW]


# ── 监管高敏关键词（命中即 RED）─────────────────────────────────
_HIGH_RISK_PATTERNS = [
    "立案调查",
    "行政处罚",
    "监管措施",
    "退市风险",
    "风险警示",
    "审计保留意见",
    "无法表示意见",
    "否定意见",
    "证监会调查",
    "investigation",
    "regulatory action",
    "delisting warning",
    "audit qualification",
    "going concern",
]


class RiskScreener:
    """排雷筛选器 —— 基于硬编码规则的股票风险筛查引擎。

    接收预获取的结构化数据（基本面、行情、财务指标、ST名单），
    输出结构化的 RiskScreenResult。
    LLM 仅用于证据总结，不参与风险等级判定。
    """

    def __init__(
        self,
        config: Optional[Any] = None,
        search_service: Optional[Any] = None,
    ):
        from src.config import get_config

        self.config = config or get_config()
        self.search_service = search_service

        # 从配置读取阈值
        self.debt_threshold = getattr(
            self.config, "risk_screen_debt_threshold", 80.0
        )
        self.pe_max_threshold = getattr(
            self.config, "risk_screen_pe_max", 100.0
        )
        self.pe_negative_is_yellow = getattr(
            self.config, "risk_screen_pe_negative_warn", True
        )

    # ── 外部入口 ──────────────────────────────────────────────────

    async def screen(
        self,
        code: str,
        stock_name: str,
        fundamental_context: Optional[Dict[str, Any]] = None,
        realtime_quote: Optional[Any] = None,
        value_metrics: Optional[Dict[str, Any]] = None,
        st_list: Optional[List[Dict[str, str]]] = None,
    ) -> RiskScreenResult:
        """对单只股票执行全套排雷检查。

        参数均为预获取的数据；排雷器不主动调用外部 API。
        （ST 名单可在所有股票排雷前批量获取一次。）
        """
        flags: List[RiskFlag] = []
        ts = datetime.now().isoformat()

        # 1. ST 检查
        flags.append(self._check_st_status(code, stock_name, st_list))

        # 2. 财务健康检查
        flags.append(self._check_financial_health(fundamental_context))

        # 3. 债务风险检查
        flags.append(self._check_debt_risk(value_metrics))

        # 4. 估值风险检查
        flags.append(self._check_valuation_risk(realtime_quote))

        # 5. 监管风险检查（联网搜索）
        reg_flag = await self._check_regulatory_risk(code, stock_name)
        flags.append(reg_flag)

        # 计算整体等级：任何 RED 则整体 RED；否则有 YELLOW 则 YELLOW；否则 GREEN
        overall = RiskLevel.GREEN
        for f in flags:
            if f.level == RiskLevel.RED:
                overall = RiskLevel.RED
                break
            if f.level == RiskLevel.YELLOW:
                overall = RiskLevel.YELLOW

        return RiskScreenResult(
            code=code,
            name=stock_name,
            overall_level=overall,
            flags=[f for f in flags if f.level != RiskLevel.GREEN],
            timestamp=ts,
        )

    # ── 各项检查 ──────────────────────────────────────────────────

    def _check_st_status(
        self,
        code: str,
        stock_name: str,
        st_list: Optional[List[Dict[str, str]]],
    ) -> RiskFlag:
        """检查股票是否在 ST/*ST 名单中。

        优先使用名称判断（is_st_stock），再交叉验证 st_list。
        """
        from data_provider import is_st_stock

        is_st_by_name = is_st_stock(stock_name)
        is_in_st_list = False
        matched_record = None
        if st_list:
            for record in st_list:
                rcode = record.get("code") or record.get("代码") or ""
                if rcode.strip() == code:
                    is_in_st_list = True
                    matched_record = record
                    break

        if is_st_by_name or is_in_st_list:
            parts = []
            if is_st_by_name:
                parts.append(f"名称含 ST 标记: {stock_name}")
            if matched_record:
                name_from_list = (
                    matched_record.get("name")
                    or matched_record.get("名称", "")
                )
                parts.append(f"在 ST 列表中: {name_from_list}")
            return RiskFlag(
                rule_name="ST/*ST 状态",
                level=RiskLevel.RED,
                evidence="；".join(parts),
                detail={
                    "is_st": True,
                    "st_list_hit": is_in_st_list,
                    "name_hit": is_st_by_name,
                },
            )
        return RiskFlag(
            rule_name="ST/*ST 状态",
            level=RiskLevel.GREEN,
            evidence="非 ST 股",
            detail={"is_st": False},
        )

    def _check_financial_health(
        self,
        fundamental_context: Optional[Dict[str, Any]],
    ) -> RiskFlag:
        """检查 ROE 是否为负、营收/净利润是否同比下滑。"""
        if not fundamental_context:
            return RiskFlag(
                rule_name="财务健康",
                level=RiskLevel.GREEN,
                evidence="无基本面数据",
                detail={"data_available": False},
            )

        issues: List[str] = []
        detail: Dict[str, Any] = {"data_available": True}

        growth = fundamental_context.get("growth", {})
        growth_data = (
            growth.get("data", {}) if isinstance(growth, dict) else {}
        )
        revenue_yoy = growth_data.get("revenue_yoy")
        detail["revenue_yoy"] = revenue_yoy

        earnings = fundamental_context.get("earnings", {})
        earnings_data = (
            earnings.get("data", {}) if isinstance(earnings, dict) else {}
        )
        roe = earnings_data.get("roe")
        net_profit_yoy = earnings_data.get("net_profit_yoy")
        detail["roe"] = roe
        detail["net_profit_yoy"] = net_profit_yoy

        if roe is not None and isinstance(roe, (int, float)) and roe < 0:
            issues.append(f"ROE 为负 ({roe:.1f}%)")

        if (
            revenue_yoy is not None
            and isinstance(revenue_yoy, (int, float))
            and revenue_yoy < 0
        ):
            issues.append(f"营收同比下滑 ({revenue_yoy:.1f}%)")

        if (
            net_profit_yoy is not None
            and isinstance(net_profit_yoy, (int, float))
            and net_profit_yoy < 0
        ):
            issues.append(f"净利润同比下滑 ({net_profit_yoy:.1f}%)")

        if not issues:
            return RiskFlag(
                rule_name="财务健康",
                level=RiskLevel.GREEN,
                evidence="财务指标正常",
                detail=detail,
            )

        return RiskFlag(
            rule_name="财务健康",
            level=RiskLevel.YELLOW,
            evidence="；".join(issues),
            detail={**detail, "flags": issues},
        )

    def _check_debt_risk(
        self,
        value_metrics: Optional[Dict[str, Any]],
    ) -> RiskFlag:
        """检查资产负债率是否超过阈值。"""
        if not value_metrics:
            return RiskFlag(
                rule_name="债务风险",
                level=RiskLevel.GREEN,
                evidence="无财务指标数据",
                detail={"data_available": False},
            )

        debt_ratio = value_metrics.get("debt_ratio")
        if debt_ratio is None:
            return RiskFlag(
                rule_name="债务风险",
                level=RiskLevel.GREEN,
                evidence="无资产负债率数据",
                detail={"data_available": False},
            )

        if (
            isinstance(debt_ratio, (int, float))
            and debt_ratio > self.debt_threshold
        ):
            return RiskFlag(
                rule_name="债务风险",
                level=RiskLevel.YELLOW,
                evidence=(
                    f"资产负债率 {debt_ratio:.1f}%"
                    f" 超过阈值 {self.debt_threshold:.0f}%"
                ),
                detail={
                    "debt_ratio": debt_ratio,
                    "threshold": self.debt_threshold,
                },
            )

        return RiskFlag(
            rule_name="债务风险",
            level=RiskLevel.GREEN,
            evidence=f"资产负债率 {debt_ratio:.1f}% 在安全范围内",
            detail={"debt_ratio": debt_ratio},
        )

    def _check_valuation_risk(
        self,
        realtime_quote: Optional[Any],
    ) -> RiskFlag:
        """检查 PE 是否过高或为负。"""
        if realtime_quote is None:
            return RiskFlag(
                rule_name="估值风险",
                level=RiskLevel.GREEN,
                evidence="无行情数据",
                detail={"data_available": False},
            )

        pe = self._extract_pe(realtime_quote)
        if pe is None:
            return RiskFlag(
                rule_name="估值风险",
                level=RiskLevel.GREEN,
                evidence="无 PE 数据",
                detail={"data_available": False},
            )

        if isinstance(pe, (int, float)):
            issues: List[str] = []
            if self.pe_negative_is_yellow and pe < 0:
                issues.append(
                    f"PE 为负 ({pe:.1f})，公司处于亏损状态"
                )
            elif pe > self.pe_max_threshold:
                issues.append(
                    f"PE ({pe:.1f}) 超过阈值 {self.pe_max_threshold:.0f}"
                )

            if issues:
                return RiskFlag(
                    rule_name="估值风险",
                    level=RiskLevel.YELLOW,
                    evidence="；".join(issues),
                    detail={
                        "pe_ratio": pe,
                        "threshold": self.pe_max_threshold,
                    },
                )

        return RiskFlag(
            rule_name="估值风险",
            level=RiskLevel.GREEN,
            evidence=(
                f"PE {pe:.1f} 正常"
                if isinstance(pe, (int, float))
                else "PE 正常"
            ),
            detail={"pe_ratio": pe},
        )

    async def _check_regulatory_risk(
        self,
        code: str,
        stock_name: str,
    ) -> RiskFlag:
        """通过搜索服务检查监管风险。

        搜索命中高敏词（立案调查、审计保留意见等）→ RED。
        搜索命中一般风险词（extract_risk_keywords）→ YELLOW。
        """
        if not self._has_search_capability():
            return RiskFlag(
                rule_name="监管风险",
                level=RiskLevel.GREEN,
                evidence="搜索引擎未配置",
                detail={"search_skipped": True},
            )

        try:
            intel = await self.search_service.search_comprehensive_intel_async(
                stock_code=code,
                stock_name=stock_name,
                max_searches=2,
            )
        except Exception as e:
            logger.warning(
                "监管风险搜索失败 [%s %s]: %s", code, stock_name, e
            )
            return RiskFlag(
                rule_name="监管风险",
                level=RiskLevel.GREEN,
                evidence="监管搜索异常",
                detail={"error": str(e)},
            )

        # 收集所有搜索结果的文本
        all_text = ""
        if isinstance(intel, dict):
            for response in intel.values():
                if response and getattr(response, "success", False):
                    results = getattr(response, "results", []) or []
                    for r in results:
                        all_text += " "
                        all_text += getattr(r, "title", "") or ""
                        all_text += " "
                        all_text += getattr(r, "content", "") or ""
                        all_text += " "
                        all_text += getattr(r, "snippet", "") or ""
                        if hasattr(r, "raw") and isinstance(
                            r.raw, dict
                        ):
                            all_text += " "
                            all_text += r.raw.get("content", "") or ""

        # 高敏词匹配 → RED
        high_risk_matches = []
        for pattern in _HIGH_RISK_PATTERNS:
            if re.search(re.escape(pattern), all_text, re.IGNORECASE):
                high_risk_matches.append(pattern)

        if high_risk_matches:
            return RiskFlag(
                rule_name="监管风险",
                level=RiskLevel.RED,
                evidence=(
                    "检测到高风险关键词: "
                    + ", ".join(high_risk_matches[:5])
                ),
                detail={"high_risk_matches": high_risk_matches},
            )

        # 一般风险词匹配 → YELLOW
        from src.core.pipeline_helpers import extract_risk_keywords

        matched = extract_risk_keywords(all_text)
        if matched:
            return RiskFlag(
                rule_name="监管风险",
                level=RiskLevel.YELLOW,
                evidence=(
                    "检测到一般风险关键词: "
                    + ", ".join(matched[:5])
                ),
                detail={"matched_keywords": matched},
            )

        return RiskFlag(
            rule_name="监管风险",
            level=RiskLevel.GREEN,
            evidence="未检测到监管风险信号",
            detail={"matched_keywords": []},
        )

    # ── LLM 总结（可选，仅总结证据，不改变判定）────────────────

    async def summarize_with_llm(
        self, result: RiskScreenResult
    ) -> str:
        """使用 LLM 总结排雷证据（不改变风险等级判定）。

        如果 LLM 不可用，返回空字符串。
        这是排雷器中 LLM 的唯一使用场景。
        """
        if not result.flags:
            return ""

        evidence_lines = [
            f"- [{f.level.upper()}] {f.rule_name}: {f.evidence}"
            for f in result.flags
        ]

        prompt = (
            f"请根据以下排雷证据撰写一段简洁的中文总结（3-5句话）。\n"
            f"只总结事实，不要改变风险等级，不要给出买卖建议。\n\n"
            f"股票: {result.name}({result.code})\n"
            f"总体风险: {result.overall_level.value}\n\n"
            + "\n".join(evidence_lines)
        )

        try:
            from src.analyzer import GeminiAnalyzer

            analyzer = GeminiAnalyzer()
            if not analyzer.is_available():
                return ""
            summary = await analyzer.analyze(prompt)
            return summary or ""
        except Exception as e:
            logger.warning("LLM 排雷总结失败: %s", e)
            return ""

    # ── 辅助方法 ──────────────────────────────────────────────────

    @staticmethod
    def _extract_pe(quote: Any) -> Optional[float]:
        """从行情对象或字典中提取 PE。"""
        if isinstance(quote, dict):
            raw = quote.get("pe_ratio") or quote.get("pe")
        elif hasattr(quote, "pe_ratio"):
            raw = quote.pe_ratio
        else:
            return None
        if raw is not None and isinstance(raw, (int, float)):
            return float(raw)
        return None

    def _has_search_capability(self) -> bool:
        return bool(
            self.search_service
            and hasattr(self.search_service, "search_comprehensive_intel_async")
        )
