# -*- coding: utf-8 -*-
"""
AI 分析提示词构建逻辑
"""

import logging
from typing import Dict, Any, Optional, List

from src.data.stock_mapping import STOCK_NAME_MAP
from src.report_language import (
    get_no_data_text,
    get_unknown_text,
    normalize_report_language,
)
from src.config import resolve_news_window_days
from src.schemas.analysis_result import DASHBOARD_OUTPUT_SCHEMA, DASHBOARD_SCHEMA_INTRO

logger = logging.getLogger(__name__)

def _format_volume(volume: Optional[float]) -> str:
    """格式化成交量显示"""
    if volume is None:
        return 'N/A'
    if volume >= 1e8:
        return f"{volume / 1e8:.2f} 亿股"
    elif volume >= 1e4:
        return f"{volume / 1e4:.2f} 万股"
    else:
        return f"{volume:.0f} 股"

def _format_amount(amount: Optional[float]) -> str:
    """格式化成交额显示"""
    if amount is None:
        return 'N/A'
    if amount >= 1e8:
        return f"{amount / 1e8:.2f} 亿元"
    elif amount >= 1e4:
        return f"{amount / 1e4:.2f} 万元"
    else:
        return f"{amount:.0f} 元"


def _resolve_prompt_news_window_days(news_window_days_config: Optional[int]) -> int:
    """为 prompt 解析最终使用的新闻窗口天数。"""
    if news_window_days_config is not None:
        try:
            return max(1, int(news_window_days_config))
        except (TypeError, ValueError):
            logger.warning("Prompt news_window_days_config 非法，回退到默认窗口: %r", news_window_days_config)
    return resolve_news_window_days(3, "short")


def _build_analysis_guardrails(report_language: str, no_data_text: str) -> str:
    """构建与具体数据载荷分离的稳定分析规则。"""
    if report_language == "en":
        return f"""
---

## 🧭 Analysis Rules
- Start from the structured market, technical, chip, and fundamental data above. Use news to validate or challenge that base case, not replace it.
- If technical, fundamental, and news signals conflict, call out the source of the conflict explicitly instead of forcing a single neat story.
- Only conclude from the data provided in this prompt. When a field is missing, state "{no_data_text}, unable to judge" instead of inventing details.
- Keep the final dashboard decision-oriented. Do not simply restate the tables without turning them into a trading conclusion.
"""

    return f"""
---

## 🧭 分析规则
- 优先使用上方结构化的行情、趋势、筹码、财报数据做判断；新闻主要用于验证、补充催化与风险。
- 如果技术面、基本面、消息面出现冲突，必须明确指出冲突来源，不要强行拼成单一结论。
- 只能根据当前 prompt 中提供的数据下结论；字段缺失时直接写“{no_data_text}，无法判断”，禁止编造。
- 最终输出要服务于交易决策，不要只是机械复述上面的表格。
"""


def _build_focus_questions(use_legacy_default_prompt: bool) -> str:
    """构建稳定的关注问题块。"""
    if use_legacy_default_prompt:
        return """
### 重点关注（必须明确回答）：
1. ❓ 大盘环境与行业板块是否支持做多？
2. ❓ 是否满足 MA5>MA10>MA20 多头排列？
3. ❓ 当前乖离率是否在安全范围内（<5%）？—— 超过5%必须标注"严禁追高"
4. ❓ 量能是否配合（缩量回调/放量突破）？
5. ❓ 筹码结构是否健康？
6. ❓ 消息面有无重大利空？（减持、处罚、业绩变变脸等）
"""

    return """
### 重点关注（必须明确回答）：
1. ❓ 当前大盘趋势与行业共振情况如何？是否属于强势赛道？
2. ❓ 当前结构是否满足激活技能的关键触发条件？
3. ❓ 当前入场位置与风险回报是否合理？若偏离过大，请明确说明等待条件
4. ❓ 量能、波动与筹码结构是否支持当前结论？
5. ❓ 消息面有无重大利空或与技能结论冲突的信息？
6. ❓ 若结论成立，具体触发条件、止损位、观察点分别是什么？
"""


def _build_output_language_requirements(report_language: str, no_data_text: str) -> str:
    """构建输出语言要求。"""
    if report_language == "en":
        return """

### Output language requirements (highest priority)
- Keep every JSON key exactly as defined above; do not translate keys.
- `decision_type` must remain `buy`, `hold`, or `sell`.
- All human-readable JSON values must be in English.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, all nested dashboard text, checklist items, and every summary field.
- Use the common English company name when you are confident. If not, keep the listed company name rather than inventing one.
- When data is missing, explain it in English instead of Chinese.
"""

    return f"""

### 输出语言要求（最高优先级）
- 所有 JSON 键名必须保持不变，不要翻译键名。
- `decision_type` 必须保持为 `buy`、`hold`、`sell`。
- 所有面向用户的人类可读文本值必须使用中文。
- 当数据缺失时，请使用中文直接说明“{no_data_text}，无法判断”。
"""

def get_persona_system_prompt(persona: str, report_language: str = "zh") -> str:
    """获取特定角色的系统提示词。"""
    if persona == "technical":
        if report_language == "en":
            return "You are a Senior Technical Analyst. Your goal is to analyze price action, moving averages, volume, and chip distribution. Be objective and data-driven."
        return "你是一位资深技术面分析师。你的职责是深入分析股价走势、均线系统、成交量变化以及筹码分布结构。请保持客观、严谨，一切以数据说话。"
    
    if persona == "risk":
        if report_language == "en":
            return "You are a Chief Risk Officer. Your goal is to identify potential pitfalls, news-driven risks, and fundamental red flags. Be skeptical and cautious."
        return "你是一位首席风控官。你的职责是识别潜在的投资陷阱、舆情风险、基本面红线以及技术指标背离。请保持审慎、怀疑的态度，重点关注风险。 "
    
    if persona == "chief":
        if report_language == "en":
            return "You are a Chief Strategist. Your goal is to synthesize findings from Technical and Risk experts into a final, actionable trading decision. Resolve any conflicts logically."
        return "你是一位首席策略师。你的职责是汇总技术专家和风控专家的分析意见，结合市场全局，给出最终的、可落地的交易决策。请逻辑严密地化解专家间的观点冲突。"
    
    return ""

def format_expert_instruction(persona: str, name: str, code: str, report_language: str = "zh") -> str:
    """格式化专家的具体指令。"""
    if persona == "technical":
        if report_language == "en":
            return f"Analyze the technical structure of {name} ({code}). Focus on trend status, MA alignment, and chip health. Provide a concise bulleted summary."
        return f"请对 {name} ({code}) 的技术面结构进行深度分析。重点关注趋势状态、均线排列、以及筹码健康度。给出精炼的要点总结。"
    
    if persona == "risk":
        if report_language == "en":
            return f"Evaluate all risk factors for {name} ({code}). Scan the news context for alerts, catalysts, and fundamental weaknesses. List critical red flags."
        return f"请评估 {name} ({code}) 的所有风险因素。扫描新闻上下文，识别风险警报、利好催化及其可持续性，并指出基本面薄弱点。列出关键的负面信号。"
    
    return ""

def build_chief_synthesizer_prompt(
    context: Dict[str, Any],
    expert_outputs: Dict[str, str],
    name: str,
    report_language: str = "zh"
) -> str:
    """构建首席策略师的综合提示词。"""
    base_prompt = format_analysis_prompt(context, name, news_context=None, report_language=report_language, output_format="dashboard")
    
    expert_section = "\n---\n\n## 🧑‍🔬 专家组分析报告 (Expert Reports)\n"
    if "technical" in expert_outputs:
        expert_section += f"\n### 📊 技术专家意见：\n{expert_outputs['technical']}\n"
    if "risk" in expert_outputs:
        expert_section += f"\n### 🛡️ 风控专家意见：\n{expert_outputs['risk']}\n"
        
    if report_language == "en":
        instruction = "\n\n### 🎯 Final Instruction: Synthesize the expert reports above with the structured data. Finalize the Decision Dashboard JSON."
    else:
        instruction = "\n\n### 🎯 最终指令：请汇总上方专家意见与结构化数据，化解可能的观点冲突，输出最终的【决策仪表盘】JSON。"
        
    return base_prompt + expert_section + instruction

def format_analysis_prompt(
    context: Dict[str, Any],
    name: str,
    news_context: Optional[str] = None,
    report_language: str = "zh",
    use_legacy_default_prompt: bool = False,
    news_window_days_config: Optional[int] = None,
    output_format: str = "standard",
) -> str:
    """
    格式化分析提示词（决策仪表盘 v2.0）
    """
    code = context.get('code', 'Unknown')
    report_language = normalize_report_language(report_language)
    
    # 优先使用上下文中的股票名称
    stock_name = context.get('stock_name', name)
    if not stock_name or stock_name == f'股票{code}':
        stock_name = STOCK_NAME_MAP.get(code, f'股票{code}')
        
    today = context.get('today', {})
    unknown_text = get_unknown_text(report_language)
    no_data_text = get_no_data_text(report_language)
    news_window_days = _resolve_prompt_news_window_days(news_window_days_config)
    
    # ========== 构建决策仪表盘格式的输入 ==========
    prompt = f"""# 决策仪表盘分析请求

## 📊 股票基础信息
| 项目 | 数据 |
|------|------|
| 股票代码 | **{code}** |
| 股票名称 | **{stock_name}** |
| 分析日期 | {context.get('date', unknown_text)} |

---

## 🌍 大盘与行业背景
"""
    if 'market_overview' in context:
        mo = context['market_overview']
        indices = mo.get('indices', [])
        stats = mo.get('stats', {})
        sectors = mo.get('sector_rankings', {})
        
        if indices:
            prompt += "\n### 指数表现\n| 指数 | 现价 | 涨跌幅 |\n|------|------|--------|\n"
            for idx in indices[:4]:
                prompt += f"| {idx.get('name')} | {idx.get('current')} | {idx.get('change_pct')}% |\n"
        
        if stats:
            prompt += f"\n### 市场广度\n- 上涨家数: {stats.get('rise_count', 'N/A')} | 下跌家数: {stats.get('fall_count', 'N/A')}\n"
            if 'limit_up_count' in stats:
                prompt += f"- 涨停家数: {stats.get('limit_up_count', 'N/A')} | 跌停家数: {stats.get('limit_down_count', 'N/A')}\n"
        
        if sectors:
            top_sectors = sectors.get('top', [])
            if top_sectors:
                prompt += "\n### 强势行业板块\n" + " | ".join([f"{s['name']}({s['change_pct']}%)" for s in top_sectors[:3]]) + "\n"

    prompt += f"""
---

## 📈 技术面数据

### 今日行情
| 指标 | 数值 |
|------|------|
| 收盘价 | {today.get('close', 'N/A')} 元 |
| 开盘价 | {today.get('open', 'N/A')} 元 |
| 最高价 | {today.get('high', 'N/A')} 元 |
| 最低价 | {today.get('low', 'N/A')} 元 |
| 涨跌幅 | {today.get('pct_chg', 'N/A')}% |
| 成交量 | {_format_volume(today.get('volume'))} |
| 成交额 | {_format_amount(today.get('amount'))} |

### 均线系统（关键判断指标）
| 均线 | 数值 | 说明 |
|------|------|------|
| MA5 | {today.get('ma5', 'N/A')} | 短期趋势线 |
| MA10 | {today.get('ma10', 'N/A')} | 中短期趋势线 |
| MA20 | {today.get('ma20', 'N/A')} | 中期趋势线 |
| 均线形态 | {context.get('ma_status', unknown_text)} | 多头/空头/缠绕 |
"""
    
    # 添加实时行情数据
    if 'realtime' in context:
        rt = context['realtime']
        prompt += f"""
### 实时行情增强数据
| 指标 | 数值 | 解读 |
|------|------|------|
| 当前价格 | {rt.get('price', 'N/A')} 元 | |
| **量比** | **{rt.get('volume_ratio', 'N/A')}** | {rt.get('volume_ratio_desc', '')} |
| **换手率** | **{rt.get('turnover_rate', 'N/A')}%** | |
| 市盈率(动态) | {rt.get('pe_ratio', 'N/A')} | |
| 市净率 | {rt.get('pb_ratio', 'N/A')} | |
| 总市值 | {_format_amount(rt.get('total_mv'))} | |
| 流通市值 | {_format_amount(rt.get('circ_mv'))} | |
| 60日涨跌幅 | {rt.get('change_60d', 'N/A')}% | 中期表现 |
"""

    # 添加财报与分红
    fundamental_context = context.get("fundamental_context")
    earnings_block = fundamental_context.get("earnings", {}) if fundamental_context else {}
    earnings_data = earnings_block.get("data", {}) if isinstance(earnings_block, dict) else {}
    financial_report = earnings_data.get("financial_report", {}) if isinstance(earnings_data, dict) else {}
    dividend_metrics = earnings_data.get("dividend", {}) if isinstance(earnings_data, dict) else {}
    
    if financial_report or dividend_metrics:
        ttm_yield = dividend_metrics.get("ttm_dividend_yield_pct", "N/A")
        ttm_cash = dividend_metrics.get("ttm_cash_dividend_per_share", "N/A")
        ttm_count = dividend_metrics.get("ttm_event_count", "N/A")
        report_date = financial_report.get("report_date", "N/A")
        prompt += f"""
### 财报与分红（价值投资口径）
| 指标 | 数值 | 说明 |
|------|------|------|
| 最近报告期 | {report_date} | 来自结构化财报字段 |
| 营业收入 | {financial_report.get('revenue', 'N/A')} | |
| 归母净利润 | {financial_report.get('net_profit_parent', 'N/A')} | |
| 经营现金流 | {financial_report.get('operating_cash_flow', 'N/A')} | |
| ROE | {financial_report.get('roe', 'N/A')} | |
| 近12个月每股现金分红 | {ttm_cash} | 仅现金分红、税前口径 |
| TTM 股息率 | {ttm_yield} | 公式：近12个月每股现金分红 / 当前价格 × 100% |
| TTM 分红事件数 | {ttm_count} | |

> 若上述字段为 N/A 或缺失，请明确写“数据缺失，无法判断”，禁止编造。
"""

    # 添加筹码分布数据
    if 'chip' in context:
        chip = context['chip']
        profit_ratio = chip.get('profit_ratio', 0)
        prompt += f"""
### 筹码分布数据（效率指标）
| 指标 | 数值 | 健康标准 |
|------|------|----------|
| **获利比例** | **{profit_ratio:.1%}** | 70-90%时警惕 |
| 平均成本 | {chip.get('avg_cost', 'N/A')} 元 | 现价应高于5-15% |
| 90%筹码集中度 | {chip.get('concentration_90', 0):.2%} | <15%为集中 |
| 70%筹码集中度 | {chip.get('concentration_70', 0):.2%} | |
| 筹码状态 | {chip.get('chip_status', unknown_text)} | |
"""
    
    # 添加趋势分析结果
    if 'trend_analysis' in context:
        trend = context['trend_analysis']
        if use_legacy_default_prompt:
            bias_warning = "🚨 超过5%，严禁追高！" if trend.get('bias_ma5', 0) > 5 else "✅ 安全范围"
            prompt += f"""
### 趋势分析预判（基于交易理念）
| 指标 | 数值 | 判定 |
|------|------|------|
| 趋势状态 | {trend.get('trend_status', unknown_text)} | |
| 均线排列 | {trend.get('ma_alignment', unknown_text)} | MA5>MA10>MA20为多头 |
| 趋势强度 | {trend.get('trend_strength', 0)}/100 | |
| **乖离率(MA5)** | **{trend.get('bias_ma5', 0):+.2f}%** | {bias_warning} |
| 乖离率(MA10) | {trend.get('bias_ma10', 0):+.2f}% | |
| 量能状态 | {trend.get('volume_status', unknown_text)} | {trend.get('volume_trend', '')} |
| 系统信号 | {trend.get('buy_signal', unknown_text)} | |
| 系统评分 | {trend.get('signal_score', 0)}/100 | |

#### 系统分析理由
**买入理由**：
{chr(10).join('- ' + r for r in trend.get('signal_reasons', ['无'])) if trend.get('signal_reasons') else '- 无'}

**风险因素**：
{chr(10).join('- ' + r for r in trend.get('risk_factors', ['无'])) if trend.get('risk_factors') else '- 无'}
"""
        else:
            bias_warning = (
                "🚨 偏离较大，需谨慎评估追高风险"
                if trend.get('bias_ma5', 0) > 5
                else "✅ 位置相对可控"
            )
            prompt += f"""
### 技术与结构分析（供激活技能判断参考）
| 指标 | 数值 | 说明 |
|------|------|------|
| 趋势状态 | {trend.get('trend_status', unknown_text)} | |
| 均线排列 | {trend.get('ma_alignment', unknown_text)} | 结合激活技能判断结构强弱 |
| 趋势强度 | {trend.get('trend_strength', 0)}/100 | |
| **价格位置(MA5)** | **{trend.get('bias_ma5', 0):+.2f}%** | {bias_warning} |
| 价格位置(MA10) | {trend.get('bias_ma10', 0):+.2f}% | |
| 量能状态 | {trend.get('volume_status', unknown_text)} | {trend.get('volume_trend', '')} |
| 系统信号 | {trend.get('buy_signal', unknown_text)} | |
| 系统评分 | {trend.get('signal_score', 0)}/100 | |

#### 系统分析理由
**支持因素**：
{chr(10).join('- ' + r for r in trend.get('signal_reasons', ['无'])) if trend.get('signal_reasons') else '- 无'}

**风险因素**：
{chr(10).join('- ' + r for r in trend.get('risk_factors', ['无'])) if trend.get('risk_factors') else '- 无'}
"""
    
    # 添加昨日对比数据
    if 'yesterday' in context:
        volume_change = context.get('volume_change_ratio', 'N/A')
        prompt += f"""
### 量价变化
- 成交量较昨日变化：{volume_change}倍
- 价格较昨日变化：{context.get('price_change_ratio', 'N/A')}%
"""

    # 添加历史胜率/表现 (Report Engine P1)
    if 'historical_performance' in context:
        perf = context['historical_performance']
        stock_perf = perf.get('stock')
        overall_perf = perf.get('overall')
        
        prompt += "\n---\n\n## 📊 历史分析准确率 (AI 自我复盘参考)\n"
        
        if stock_perf:
            prompt += f"""
### 本股历史表现 ({stock_name})
- **胜率 (Win Rate)**: {stock_perf.get('win_rate_pct', 'N/A')}%
- **方向准确率**: {stock_perf.get('direction_accuracy_pct', 'N/A')}%
- **总评估样本**: {stock_perf.get('total_evaluations', 0)} 次
"""
        
        if overall_perf:
            prompt += f"""
### 全局历史表现 (系统整体)
- **平均胜率**: {overall_perf.get('win_rate_pct', 'N/A')}%
- **平均方向准确率**: {overall_perf.get('direction_accuracy_pct', 'N/A')}%
- **总分析次数**: {overall_perf.get('total_evaluations', 0)} 次分析
"""
        
        prompt += """
> **分析建议**：请结合历史表现校准你的信心。如果历史胜率较低，请更加谨慎地评估当前的信号，并在风险提示中增加对应说明。
"""

    # 添加新闻搜索结果
    prompt += """
---

## 📰 舆情情报
"""
    if news_context:
        prompt += f"""
以下是 **{stock_name}({code})** 近{news_window_days}日的新闻搜索结果，请重点提取：
1. 🚨 **风险警报**：减持、处罚、利空
2. 🎯 **利好催化**：业绩、合同、政策
3. 📊 **业绩预期**：年报预告、业绩快报
4. 🕒 **时间规则（强制）**：
   - 输出到 `risk_alerts` / `positive_catalysts` / `latest_news` 的每一条都必须带具体日期（YYYY-MM-DD）
   - 超出近{news_window_days}日窗口的新闻一律忽略
   - 时间未知、无法确定发布日期的新闻一律忽略

```
{news_context}
```
"""
    else:
        prompt += """
未搜索到该股票近期的相关新闻。请主要依据技术面数据进行分析。
"""

    # 注入缺失数据警告
    if context.get('data_missing'):
        prompt += """
⚠️ **数据缺失警告**
由于接口限制，当前无法获取完整的实时行情和技术指标数据。
请 **忽略上述表格中的 N/A 数据**，重点依据 **【📰 舆情情报】** 中的新闻进行基本面和情绪面分析。
在回答技术面问题（如均线、乖离率）时，请直接说明“数据缺失，无法判断”，**严禁编造数据**。
"""

    prompt += _build_analysis_guardrails(report_language, no_data_text)

    # 明确的输出要求
    prompt += f"""
---

## ✅ 分析任务

请为 **{stock_name}({code})** 生成【决策仪表盘】，严格按照 JSON 格式输出。
"""
    if context.get('is_index_etf'):
        prompt += """
> ⚠️ **指数/ETF 分析约束**：该标的为指数跟踪型 ETF 或市场指数。
> - 风险分析仅关注：**指数走势、跟踪误差、市场流动性**
> - 严禁将基金公司的诉讼、声誉、高管变动纳入风险警报
> - 业绩预期基于**指数成分股整体表现**，而非基金公司财报
> - `risk_alerts` 中不得出现基金管理人相关的公司经营风险

"""
    prompt += f"""
### ⚠️ 重要：输出正确的股票名称格式
正确的股票名称格式为“股票名称（股票代码）”，例如“贵州茅台（600519）”。
如果上方显示的股票名称为"股票{code}"或不正确，请在分析开头**明确输出该股票的正确中文全称**。
"""
    prompt += _build_focus_questions(use_legacy_default_prompt)
    prompt += f"""

### 决策仪表盘要求：
- **股票名称**：必须输出正确的中文全称（如"贵州茅台"而非"股票600519"）
- **核心结论**：一句话说清该买/该卖/该等
- **持仓分类建议**：空仓者怎么做 vs 持仓者怎么做
- **具体狙击点位**：买入价、止损价、目标价（精确到分）
- **检查清单**：每项用 ✅/⚠️/❌ 标记
- **消息面时间合规**：`latest_news`、`risk_alerts`、`positive_catalysts` 不得包含超出近{news_window_days}日或时间未知的信息

请输出完整的 JSON 格式决策仪表盘。"""
    prompt += _build_output_language_requirements(report_language, no_data_text)

    # Hybrid mode: inject explicit dashboard JSON schema
    if output_format == "dashboard":
        prompt += DASHBOARD_SCHEMA_INTRO + DASHBOARD_OUTPUT_SCHEMA + """

### 输出规则（优先级最高）
1. 所有 JSON 键名必须与上面 Schema 完全一致，不要翻译键名，不要添加额外顶层字段
2. `decision_type` 必须为 `buy`、`hold`、`sell` 之一
3. `sentiment_score` 必须是 0-100 的整数
4. 当数据缺失时，字段值写 `"数据缺失，无法判断"`，不要编造
5. 不要在 JSON 外额外输出解释文字
6. 必须输出完整的 JSON 对象（包含所有顶层字段），不要省略任何字段
"""

    return prompt

def build_integrity_complement_prompt(missing_fields: List[str], report_language: str = "zh") -> str:
    """构建补全建议"""
    report_language = normalize_report_language(report_language)
    if report_language == "en":
        lines = ["### Completion requirements: fill the missing mandatory fields below and output the full JSON again:"]
        for f in missing_fields:
            if f == "sentiment_score":
                lines.append("- sentiment_score: integer score from 0 to 100")
            elif f == "operation_advice":
                lines.append("- operation_advice: localized action advice")
            elif f == "analysis_summary":
                lines.append("- analysis_summary: concise analysis summary")
            elif f == "dashboard.core_conclusion.one_sentence":
                lines.append("- dashboard.core_conclusion.one_sentence: one-line decision")
            elif f == "dashboard.intelligence.risk_alerts":
                lines.append("- dashboard.intelligence.risk_alerts: risk alert list (can be empty)")
            elif f == "dashboard.battle_plan.sniper_points.stop_loss":
                lines.append("- dashboard.battle_plan.sniper_points.stop_loss: stop-loss level")
        return "\n".join(lines)

    lines = ["### 补全要求：请在上方分析基础上补充以下必填内容，并输出完整 JSON："]
    for f in missing_fields:
        if f == "sentiment_score":
            lines.append("- sentiment_score: 0-100 综合评分")
        elif f == "operation_advice":
            lines.append("- operation_advice: 买入/加仓/持有/减仓/卖出/观望")
        elif f == "analysis_summary":
            lines.append("- analysis_summary: 综合分析摘要")
        elif f == "dashboard.core_conclusion.one_sentence":
            lines.append("- dashboard.core_conclusion.one_sentence: 一句话决策")
        elif f == "dashboard.intelligence.risk_alerts":
            lines.append("- dashboard.intelligence.risk_alerts: 风险警报列表（可为空数组）")
        elif f == "dashboard.battle_plan.sniper_points.stop_loss":
            lines.append("- dashboard.battle_plan.sniper_points.stop_loss: 止损价")
    return "\n".join(lines)


def build_integrity_retry_prompt(
    base_prompt: str,
    previous_response: str,
    missing_fields: List[str],
    report_language: str = "zh",
) -> str:
    """构建重试 Prompt"""
    complement = build_integrity_complement_prompt(missing_fields, report_language=report_language)
    previous_output = previous_response.strip()
    if normalize_report_language(report_language) == "en":
        prefix = "### The previous output is below. Complete the missing fields based on that output and return the full JSON again. Do not omit existing fields:"
    else:
        prefix = "### 上一次输出如下，请在该输出基础上补齐缺失字段，并重新输出完整 JSON。不要省略已有字段："
    return "\n\n".join([
        base_prompt,
        prefix,
        previous_output,
        complement,
    ])
