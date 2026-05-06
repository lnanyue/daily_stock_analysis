# 逻辑回溯：强制 AI 解释判断方向变化

## 摘要

在 LLM 分析 prompt 中注入上一次分析的结论摘要，当本次判断方向（buy/hold/sell）与上次不同时，强制 LLM 在分析文本中说明逻辑转折点，避免"今天看多、明天看空"却不给理由的碎片化决策。

## 改动范围

仅涉及 prompt 构建层，不改动数据模型、DB schema、API 结构或配置文件。

## 需求

### 功能

- 读取当前股票最近一次分析结论（`decision_type` + `one_sentence`）
- 如果本次预期方向与上次不同，在 prompt 尾部追加指令要求说明转折原因
- 转折点说明格式：`「逻辑回溯：上次看[X]，本次看[Y]，原因：[...]」`

### 非功能

- 没有上次分析记录时，不追加任何指令（静默跳过）
- 方向相同时，也不追加（不引入冗余指令）
- 不新增 JSON schema 字段、不新增数据库表或列
- 兼容 `agent_mode=False` 和 `agent_mode=True` 两条路径（当前已收敛为统一路径）

### 判定规则

- `decision_type` 值：`buy` / `hold` / `sell`
- 方向变化定义为跨越中线：
  - `buy → sell` 或 `sell → buy`：强变化，必须说明
  - `buy → hold` 或 `hold → sell`：弱变化，也说明
  - `hold → hold` 或 `buy → buy`：不变，不追加

## 设计

### prompt 注入位置

`src/analyzer/prompt_builder.py` → `format_analysis_prompt()`

接收 `context` 字典中的新 key：

```python
"logic_turnover": {
    "previous_decision": "buy",
    "current_direction": "sell",  # "看空"等方向提示词
    "previous_summary": "K线多头排列，均线支撑有效，建议逢低买入"
}
```

### prompt 追加内容

当 `logic_turnover` 存在时，在 prompt 最后追加：

```
## 逻辑回溯要求

你上次（{date}）对此股票的判断是「{previous_decision}」，理由是：{previous_summary}
本次你的判断方向发生了变化，你**必须**在分析文本中说明你的逻辑转折点，
格式为：「逻辑回溯：上次看{prev_label}，本次看{curr_label}，原因：...」
```

### 触发时机

`src/core/pipeline.py` → `_analyze_with_agent()`：

在 `enhanced_context["previous_analyses"]` 组装完成后（当前已有），比对最后一条记录的 `decision` 与当前趋势计算结果：

- 取 `previous_analyses[0]`（最近一次）
- 如果 `prev["decision"]` 和当前预期方向不同，写入 `enhanced_context["logic_turnover"]`
- 当前预期方向：从 `trend_result.signal_score` 推导（>60=看多, <40=看空, 其余=中性）

### 涉及的函数

- `format_analysis_prompt()` — 新增 `context` 读取 `logic_turnover`
- `_analyze_with_agent()` — 组装 `logic_turnover` 字典

### 测试

- 已有方向变化时，prompt 包含「逻辑回溯」字样
- 方向无变化时，prompt 不包含「逻辑回溯」
- 无历史记录时，静默跳过

## 边界场景

| 场景 | 行为 |
|------|------|
| 无历史记录 | 不追加指令 |
| 方向不变（buy→buy） | 不追加指令 |
| 方向变化（buy→sell） | 追加，提示 LLM 说明转折 |
| 方向弱化（buy→hold） | 追加 |
| 历史记录没有 decision 字段 | 视为无变化，跳过 |
| trend_result 无法推导方向 | 默认 neutral，不触发转折 |
