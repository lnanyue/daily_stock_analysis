# 事实核查：T+5 预测准确率评价

## 摘要

每次 AI 分析结果入库后，系统在查询时自动比对 T+5 天的实际行情，判定预测方向是否正确，并统计各模型和各股票的准确率，为模型优胜劣汰提供数据依据。

## 改动范围

新增 `prediction_eval` 数据库表，新增一个评价服务类，在现有查询路径中触发评价。

## 判定规则

| `decision_type` | 5日涨跌幅 | 判定 |
|-----------------|-----------|------|
| buy             | ≥+1%      | correct |
| buy             | <-1%      | wrong |
| buy             | [-1%, +1%) | wrong (看多但横盘) |
| sell            | ≤-1%      | correct |
| sell            | >-1%      | wrong |
| hold            | [-1%, +1%) | correct |
| hold            | ≥+1% 或 ≤-1% | wrong (说横盘但实际有方向) |

## 数据表

### `prediction_eval`

| 列名 | 类型 | 说明 |
|------|------|------|
| query_id | str (PK) | 关联 analysis_history |
| code | str | 股票代码 |
| analysis_date | date | 分析日期 |
| eval_date | date | 评价日期 (analysis_date + 5 交易日) |
| decision_type | str | buy/hold/sell |
| sentiment_score | int | 当时评分 |
| model_used | str | 模型名 |
| change_pct_5d | float | 5日涨跌幅 (%) |
| close_at_analysis | float | 分析日收盘价 |
| close_at_eval | float | 评价日收盘价 |
| verdict | str | correct/wrong/null (pending) |
| evaluated_at | datetime | 评价时间 |

### DDL

```sql
CREATE TABLE IF NOT EXISTS prediction_eval (
    query_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    analysis_date DATE NOT NULL,
    eval_date DATE,
    decision_type TEXT,
    sentiment_score INTEGER,
    model_used TEXT,
    change_pct_5d REAL,
    close_at_analysis REAL,
    close_at_eval REAL,
    verdict TEXT,
    evaluated_at TIMESTAMP
);
CREATE INDEX idx_prediction_eval_verdict ON prediction_eval(verdict);
CREATE INDEX idx_prediction_eval_code ON prediction_eval(code);
CREATE INDEX idx_prediction_eval_model ON prediction_eval(model_used);
```

## 架构

```
src/
  services/
    fact_checker.py      ← 新增：评价服务，包含 evaluate() + stats()
  storage.py             ← 修改：管理 prediction_eval 表
```

### `FactChecker` 服务

```python
class FactChecker:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def evaluate_pending(self, limit: int = 50) -> int:
        """扫描 verdict IS NULL 的记录，T+5 评价，返回评价数量。"""

    def get_stats(self, model: str = None, code: str = None) -> Dict:
        """聚合统计：准确率、样本量、按模型/股票分组。"""

    def get_model_ranking(self) -> List[Dict]:
        """按准确率降序排列各模型。"""
```

### `evaluate_pending()` 流程

1. `SELECT * FROM prediction_eval WHERE verdict IS NULL AND eval_date <= today LIMIT 50`
2. 对每条记录：查 `stock_daily` 表 `code + eval_date` 的 `close`
3. 计算 `change_pct_5d = (close_eval - close_analysis) / close_analysis * 100`
4. 按判定规则得出 `verdict`
5. `UPDATE prediction_eval SET verdict=..., evaluated_at=now WHERE query_id=...`

### 触发时机

`src/services/history_service.py` 中的历史查询方法（`get_analysis_history_paginated` 或类似），每次查询前调用 `evaluate_pending()`。

现有的 `analysis_history` 数据入库时，**同步写入** `prediction_eval` 记录（verdict=null, eval_date=analysis_date+5d）。

## 涉及文件

| 文件 | 改动 |
|------|------|
| `src/services/fact_checker.py` | 新增：评价服务 |
| `src/storage.py` | 新增 `prediction_eval` 表管理方法 |
| `src/core/pipeline.py` | `save_analysis_history_async` 调用后，同步写入 prediction_eval 记录 |
| `src/services/history_service.py` | 查询历史时触发 `evaluate_pending()` |

## 测试

- 写 prediction_eval 记录 → 读取验证
- evaluate_pending 对可评价记录正确判定 verdict
- 无对应 stock_daily 数据的记录保持 verdict=null
- get_stats 返回正确的聚合数据
