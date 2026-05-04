# Multi-Agent 系统重构设计

日期：2026-05-04 | 状态：草稿

## 解决的问题

1. **技能聚合未落实**：`_aggregate_skill_opinions` 是 pass，`SkillAggregator` 已实现但未接入
2. **串行调度瓶颈**：Agent 链串行执行，不支持并行
3. **RiskAgent 幻觉风险**：系提示要求输出 VaR / 压力测试，但无计算工具，靠 LLM 编造
4. **重复数据拉取**：各 Agent 各自调用数源 API，无共享缓存
5. **工程债务**：循环依赖（局部导入补丁）、魔法数字、Skill/Strategy 概念模糊

## 整体架构

旧流程（串行 Agent 链）：

```
TechnicalAgent → IntelAgent → RiskAgent → DecisionAgent
各 Agent 独立拉取数据      Skill 输出未聚合       RiskAgent 编数字
```

新流程（预采集 → 并行 → 聚合）：

```
┌─ 1. 预采集 ───────────────────────────────┐
│  DataCollector                              │
│  ├─ 行情（realtime_quote, daily_history）   │
│  ├─ 技术指标（MACD, RSI, KDJ — 计算型）    │
│  ├─ 新闻/情绪                               │
│  ├─ 基本面                                  │
│  └─ 风险指标（volatility, VaR — 计算型）   │
└─────────────────────────────────────────────┘
                     │
                     ▼
┌─ 2. 并行分析 ───────────────────────────────┐
│  asyncio.gather(                             │
│    TechnicalAgent, IntelAgent, RiskAgent,    │
│    Skills...,                                │
│  )                                           │
│  各 Agent 消费 CollectedData，不再自行拉取   │
└─────────────────────────────────────────────┘
                     │
                     ▼
┌─ 3. 聚合 ──────────────────────────────────┐
│  OpinionMerger.merge()                      │
│  SkillAggregator.aggregate()                │
│  DashboardBuilder.build()                   │
│  → MergedDecision                           │
└─────────────────────────────────────────────┘
```

## 1. 共享数据层

### CollectedData

新增 `src/agent/quantitative/collected_data.py`，类型化预采集数据：

```
realtime_quote     → Optional[Dict]
daily_history      → Optional[pd.DataFrame] (日 K 序列)
today_k            → Optional[Dict]
yesterday_k        → Optional[Dict]
trend_result       → Optional[Dict]
chip_distribution  → Optional[Dict]
technical_indicators → Optional[Dict[str, float]] (MACD, RSI, KDJ)
fundamentals       → Optional[Dict]
news_context       → str
sentiment_score    → Optional[float]
risk_metrics       → Optional[Dict[str, float]] (volatility, max_drawdown, sharpe, VaR, beta)
```

### DataCollector

新增 `src/agent/collector.py`，独立采集器：

- 职责单一：到各数据源 fetch，填充 CollectedData
- 失败不阻塞，数据缺失由 Agent 通过 `(数据不可用)` 标记处理
- 可配置按需采集（`collect_fundamentals`, `collect_news`, `collect_risk`）

## 2. 并行 Agent 调度

### Orchestrator 改造

旧 `orchestrator.py`（串行 `_execute_pipeline`）改为三段式调度：

```
1. DataCollector.collect(code=stock_code)
2. asyncio.gather(agent_runs..., return_exceptions=True)
3. OpinionMerger.merge() → SkillAggregator → DashboardBuilder
```

### pipeline_agent.py 调整

保留 `run_agent_analysis()` 作为外部入口，职责如下：

- 上游：上下文拼接、enhance_context、emit_progress
- 中游：**委托 Orchestrator** 做 agent 调度（不再 inline 创建 Agent）
- 下游：结果解析、FactChecker、DB 持久化

改动控制在 `src/agent/` 内部，不波及 `src/core/` 的上层调用链。

### 废弃配置

- 移除 `config.yaml` 中 `arch: single/multi` 开关
- 统一走新调度模式
- `factory.py` 简化：`build_agent_executor()` 去掉 multi-arch 分支

## 3. 聚合层

### OpinionMerger

新增 `src/agent/quantitative/opinion_merger.py`：

- 收集所有 AgentOpinion + Skill 输出
- 按加权计算最终评分（权重可配置）
- 输出 MergedDecision（signal, score, confidence, direction, breakdown, risk_override）

### SkillAggregator 接入

- 从 `portfolio_optimizer.py` 抽离 Skill 聚合逻辑到独立模块
- 定义 `SkillResult` 标准化接口
- Orchestrator 聚合阶段主动调用

### DashboardBuilder

- DecisionAgent 退化为最终润色角色
- dashboard 核心数据由聚合层产出

## 4. RiskAgent 治本

### 预计算风险指标

`DataCollector._compute_risk_metrics()` 基于日 K 纯计算：

- 年化波动率
- 最大回撤
- Sharpe Ratio
- 95% VaR（历史法）
- Beta
- 上涨天数比例

### Prompt 调整

- 量化指标已在 `collected.risk_metrics` 中，Agent 直接引用
- RiskAgent 职责切换为：解读风险指标 + 定性风险分析（宏观、行业、事件）
- 移除"估算量化指标"要求

## 5. 工程清理

### 循环依赖 — Lazy Agent Registry

新增 `src/agent/registry.py`：

```python
@dataclass
class AgentRegistry:
    _agents: Dict[str, Type[BaseAgent]] = field(default_factory=dict)

    def register(self, role: str):
        def decorator(cls):
            self._agents[role] = cls
            return cls
        return decorator

    def get(self, role: str) -> Type[BaseAgent]: ...
```

Agent 类通过装饰器注册，`factory.py` 按角色名查 registry，消除局部导入。

### 魔法数字

新增 `src/agent/quantitative/scoring_config.py`，统一管理：

- 信号评分阈值
- 置信度因子
- 聚合权重默认值
- 超时阈值

对 `config.yaml` 暴露可选覆盖入口。

### Skill vs Strategy

- `strategies/`：用户自定义策略文件目录（外部输入）
- `skills/`：系统内置分析技能（系统代码）
- 文档 / 注释明确区分，不改已有目录结构

## 变更文件清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `src/agent/collector.py` | 新增 | DataCollector |
| `src/agent/registry.py` | 新增 | Lazy Agent Registry |
| `src/agent/quantitative/collected_data.py` | 新增 | CollectedData 类型 |
| `src/agent/quantitative/opinion_merger.py` | 新增 | OpinionMerger |
| `src/agent/quantitative/scoring_config.py` | 新增 | 评分阈值配置 |
| `src/agent/quantitative/portfolio_optimizer.py` | 修改 | 抽离 SkillAggregator 逻辑 |
| `src/agent/orchestrator.py` | 大幅改写 | 三段式调度 |
| `src/agent/factory.py` | 调整 | 简化，去掉 multi-arch 分支 |
| `src/agent/protocols.py` | 调整 | 可选：追加 MergedDecision |
| `src/core/pipeline_agent.py` | 调整 | 委托调度给 Orchestrator |
| `src/agent/agents/risk_agent.py` | 调整 | Prompt 和工具装配 |
| `config.yaml` | 清理 | 移除 arch/mode/timeout 等旧配置 |

## 不做的范围

- 不废弃 `pipeline_agent.py`（保留外部入口，只翻新内部调度）
- 不引入新的第三方依赖
- 不改 `strategies/` 目录结构（向后兼容）
- 不引入进程级 Agent 并行（asyncio 协程级并行足够）
