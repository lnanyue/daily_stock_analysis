# 集成 TechnicalAgent 和 IntelAgent 到 Pipeline

**日期**: 2026-05-03  
**状态**: 已批准  
**作者**: Claude

## 1. 设计概述

将已实现的 `TechnicalAgent` 和 `IntelAgent` 集成到 `run_agent_analysis` 流程中，替代当前的"多专家"模式（直接 LLM 调用）。

通过 `BaseAgent` 架构，智能体可以使用工具调用（如 `get_realtime_quote`、`get_daily_history`、`search_stock_news` 等）来获取实时数据，生成更精准的分析意见。

## 2. 修改文件

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/core/pipeline_agent.py` | 修改 | 修改 `run_agent_analysis` 函数，使用智能体获取专家意见 |
| `src/agent/runner.py` | 可能修改 | 添加异步包装器（如需） |

## 3. 关键代码变更

### 3.1 在 `run_agent_analysis` 中获取依赖

```python
from src.agent.factory import get_tool_registry
from src.agent.llm_adapter import LLMToolAdapter
from src.agent.agents.technical_agent import TechnicalAgent
from src.agent.agents.intel_agent import IntelAgent
from src.agent.protocols import AgentContext
import asyncio

# 在 run_agent_analysis 函数内
registry = get_tool_registry()
llm_adapter = LLMToolAdapter(config)
```

### 3.2 创建智能体实例

```python
tech_agent = TechnicalAgent(
    tool_registry=registry,
    llm_adapter=llm_adapter,
    skill_instructions="",
    technical_skill_policy="",
)
intel_agent = IntelAgent(
    tool_registry=registry,
    llm_adapter=llm_adapter,
)
```

### 3.3 创建 AgentContext 并运行智能体

```python
async def _run_agent_async(agent, ctx):
    """包装同步的 BaseAgent.run() 为异步调用"""
    return await asyncio.to_thread(agent.run, ctx)

ctx = AgentContext(
    stock_code=code,
    stock_name=stock_name or code,
    query=f"Analysis for {stock_name or code}",
    meta={"report_language": report_language},
)

# 并行运行两个智能体
tech_result, intel_result = await asyncio.gather(
    _run_agent_async(tech_agent, ctx),
    _run_agent_async(intel_agent, ctx),
)
```

### 3.4 提取 AgentOpinion 并传递给首席策略师

```python
expert_map = {}

if tech_result and tech_result.opinion:
    expert_map["technical"] = tech_result.opinion.raw_data
    # 也将意见添加到 ctx 供后续智能体使用
    ctx.add_opinion(tech_result.opinion)

if intel_result and intel_result.opinion:
    expert_map["intel"] = intel_result.opinion.raw_data
    ctx.add_opinion(intel_result.opinion)

# 继续现有的首席策略师汇总流程
# 修改 _run_expert 函数，使其可以使用 expert_map 中的数据
```

### 3.5 修改 `_run_expert` 函数（可选）

当前 `_run_expert` 是并行调用 LLM 的。可以修改为：
- 如果 `expert_map` 中有数据，直接使用
- 否则，回退到原有的 LLM 调用方式

## 4. 数据流

```
run_agent_analysis
    │
    ├─ 获取 ToolRegistry 和 LLMToolAdapter
    │
    ├─ 创建 TechnicalAgent 和 IntelAgent
    │
    ├─ 创建 AgentContext
    │
    ├─ 并行运行两个智能体（asyncio.gather + asyncio.to_thread）
    │   │
    │   ├─ TechnicalAgent.run(ctx)
    │   │   ├─ 调用工具：get_realtime_quote, get_daily_history, analyze_trend, ...
    │   │   └─ 返回 StageResult（包含 AgentOpinion）
    │   │
    │   └─ IntelAgent.run(ctx)
    │       ├─ 调用工具：search_stock_news, search_comprehensive_intel, ...
    │       └─ 返回 StageResult（包含 AgentOpinion）
    │
    ├─ 提取 AgentOpinion，构建 expert_map
    │
    ├─ 首席策略师汇总（使用 expert_map）
    │
    └─ 生成最终 AnalysisResult
```

## 5. 测试计划

1. **单元测试**：测试 `run_agent_analysis` 中的智能体创建和调用逻辑
2. **集成测试**：验证 TechnicalAgent 和 IntelAgent 能正确调用工具并返回意见
3. **端到端测试**：运行完整的股票分析流程，验证输出正确

## 6. 风险点

1. **同步/异步桥接**：`BaseAgent.run()` 是同步的，需要用 `asyncio.to_thread` 包装
2. **工具调用超时**：工具调用可能需要较长时间，需要设置合理的超时
3. **ToolRegistry 线程安全**：`get_tool_registry()` 返回全局缓存的注册表，需要确认其在多线程环境中的安全性
4. **LLMToolAdapter 线程安全**：需要确认 `LLMToolAdapter` 是否线程安全

## 7. 回滚方式

保留当前的"多专家"模式作为 fallback：
- 在 `run_agent_analysis` 中添加 try-except
- 如果新的智能体调用失败，回退到原有的 `_run_expert` 方式
- 或者通过配置项 `agent_use_base_agent` 来切换模式

## 8. 后续优化

1. 将 `BaseAgent.run()` 改为原生异步方法，避免 `asyncio.to_thread` 开销
2. 考虑将 RiskAgent 也集成到流程中
3. 优化工具调用的并发性，减少总分析时间
