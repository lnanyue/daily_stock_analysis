# LLM 分析事实核查与评估闭环

**Date:** 2026-05-02
**Status:** Draft

## Problem

当前 hybrid 分析用单次 LLM 调用生成决策仪表盘，但没有人验证 LLM 说的数据对不对。涨跌预测需要 5 天后才能验证，周期长且受大盘/板块噪音干扰。需要一个更即时、更本质的评估维度：**事实准确率（Fact Accuracy Score）**。

具体问题：
1. **延迟验证** — 方向预测需等 N 天，期间噪音干扰大
2. **无归因检查** — LLM 说"PE 低"，但实际 PE 可能偏高
3. **无学习循环** — 评估结果不回流到 prompt，模型不会变好
4. **无模型分层** — 不同模型混在一起评估，无法指导选型

## Solution

每次分析时要求 LLM 输出 3-5 条可验证的事实断言；代码在分析完成后**当场验证**数据类断言，**持续跟踪**事件类断言。结果合入 `AnalysisResult` 并汇总为评估报告。

## Design

### 数据流

```
LLM response (含 verifiable_assertions)
  │
  ▼
_parse_response → AnalysisResult.fact_assertions
  │
  ▼
FactChecker.check(assertions, realtime_quote, trend_result, chip_data, fundamental_context)
  ├─ data 断言 → 即时比对 → passed/deviation
  ├─ event 断言 → 跳过（留 EventTracker）
  └─ 不可验证断言 → 不计入分母
  │
  ▼
AnalysisResult.fact_accuracy_score = sum(passed) / sum(verifiable)
AnalysisResult.fact_assertions = [AssertionResult, ...]
  │
  ▼
EventTracker.save(assertions, query_id, code)
  │
  ▼
(下次分析时) EventTracker.scan_pending_events(search_service)
  └─ 匹配新闻/公告 → 确认 or 超时
```

### 新增 / 修改文件

| 文件 | 改动 |
|------|------|
| `src/schemas/analysis_result.py` | `AnalysisResult` 加 `fact_accuracy_score: float = 0.0`、`fact_assertions: List[Dict]` 字段；`DASHBOARD_OUTPUT_SCHEMA` 加 `verifiable_assertions` + `fact_assertions` 结构定义 |
| `src/analysis/fact_checker.py` | 新增模块：`FactChecker` 类，接收断言 + 股票数据，返回 `List[AssertionResult]` |
| `src/analysis/event_tracker.py` | 新增模块：事件断言记录、扫描确认、超时失效 |
| `src/analyzer/prompt_builder.py` | dashboard 输出规则追加第 7-9 条 |
| `src/core/pipeline.py` | `_analyze_with_agent()` 末尾集成 FactChecker 验证 + EventTracker hook |
| `src/storage.py` | 加 `save_event_assertion()` 、 `scan_pending_events()` 方法 |
| `tests/test_fact_checker.py` | 新增测试文件 |
| `tests/test_event_tracker.py` | 新增测试文件 |

### 新增数据结构

```python
@dataclass
class AssertionResult:
    claim: str              # 原始断言文本
    type: str               # "data" / "event" / "other"
    verifiable: bool        # 是否可验证
    passed: Optional[bool]  # True / False / None(不可验证)
    actual_value: Optional[str]  # 实际数据
    deviation: Optional[float]   # 偏差比例
    matched_rule: Optional[str]   # 匹配到的验证规则名
```

### FactChecker 验证规则

| 关键词 | 验证方法 | 容差 |
|--------|---------|------|
| PE / 市盈率 / 估值 | 从 `fundamental_context` 取 PE，比对断言值 | ±15% |
| MACD 金叉/死叉 | 用日线数据计算 MACD，验证 DIFF/DEA 关系 | 精确 |
| 均线多头/空头排列 | 计算 MA5/MA10/MA20，验证位置关系 | 精确 |
| 北向资金 / 资金流向 | 从 `chip_data` 取北向净流入 | ±20% |
| 支撑位 / 压力位 | 用近期价格区间验证 | ±2% |
| 量能 / 成交量 | 对比近日均量 | ±20% |
| 板块 / 行业 | 从 `sector` 数据验证板块表现 | ±15% |
| K 线形态 | 简化验证（如"长阳"= 涨幅 > 3%） | 精确 |

### Prompt 追加规则

```text
7. `verifiable_assertions` 至少输出 3 条，至多 5 条
8. `type` 为 `data` 的断言必须基于当前已有的行情/财务/技术指标数据（必须有数据源可验证），不得凭空编造
9. `type` 为 `event` 的断言必须与该公司直接相关的事件预期（财报/分红/解禁/公告等），不得泛泛而谈
```

### Dashboard Schema 追加

```json
{
  "verifiable_assertions": [
    {"claim": "贵州茅台当前 PE 约 25 倍，处于近 3 年中等偏低水平", "type": "data"},
    {"claim": "MACD 日线金叉，DIFF 上穿 DEA 线", "type": "data"}
  ]
}
```

### EventTracker 表结构（SQLite）

```sql
CREATE TABLE IF NOT EXISTS event_assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    claim TEXT NOT NULL,
    created_at TEXT NOT NULL,
    target_window_days INTEGER DEFAULT 30,
    confirmed INTEGER,  -- 1=confirmed, 0=failed, NULL=pending
    confirmed_at TEXT,
    match_detail TEXT
);
```

### 评估报告输出

每月 / 按需输出：

```
本月总断言: 342 条
可验证: 286 条 (83.6%)
  通过: 263 条 (92.0%)
  未通过: 23 条 (8.0%)
不可验证: 56 条 (16.4%)

事件跟踪:
  断言: 48 条
  已确认: 18 条 (37.5%)
  待确认: 25 条 (52.1%)
  已超时: 5 条 (10.4%)

按模型分桶:
  gemini-2.0-flash: 92.3% (192/208)
  gpt-4o-mini: 88.5% (54/61)
```

## 性能影响

- FactChecker 验证在内存中完成，单次 < 10ms
- EventTracker 扫描在每次分析末尾同步执行，依赖 `SearchService` 已有缓存
- 新增断言解析不影响 LLM 调用延迟（在 LLM 返回后执行）

## 风险与回退

- **风险：LLM 编造断言** — prompt 规则第 8 条明确禁止，且 FactChecker 对不可验证断言不计分，激励 LLM 输出可验证断言
- **风险：事件确认不准确** — 关键词匹配有误报可能，初始阶段保持宽松匹配，用户可手动核查
- **回退：** `fact_accuracy_score` 默认 0.0，`fact_assertions` 默认空列表，不影响现有分析流程；可随时通过 config 开关关闭
