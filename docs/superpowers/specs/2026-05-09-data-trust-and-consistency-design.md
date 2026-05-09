# 数据可信度与产品一致性治理设计

> 基于 2026-05-09 代码审查 7 项发现，分两期执行。第一期聚焦"分析结果可信任"，第二期聚焦"产品一致性"。

## 第一期：数据可信度

### 1. 情报维度对齐

**问题**：`search_comprehensive_intel_async`（主流程异步版）只建 5 个维度（latest_news / risk_check / bearish_check / earnings / macro_news），而同步版 `search_comprehensive_intel` 建 8 个维度（含 announcements / market_analysis / industry）。主报告缺公告、研报、行业信息。

**方案**：
- 删除异步版中独立的维度构建代码
- 异步版复用同步版的 `_build_search_dimensions()` 方法，8 个维度走 `asyncio.gather` 并发执行
- Agent 工具后续也引用同一份维度注册表

**文件**：`src/search/service.py`

---

### 2. Freshness 规则统一

**问题**：异步路径 `search_stock_news_async` 调用 `_filter_news_response` 时未传 `strict=True`，旧新闻/无日期新闻可能混入。宏观新闻维度已传 `strict=True`，其他维度漏了。

**方案**：
- `search_stock_news_async` 所有调用统一传 `strict=True`
- 过滤后某维度结果为空 → prompt 中显示"该维度未找到近期新闻"
- 不伪造数据，不降级

**文件**：`src/search/service.py`

---

### 3. 报告质量闸门

**问题**：`_parse_response` 在 `extract_json_from_text` 返回 `{}` 时，仍用 placeholder 填充并返回 `AnalysisResult(success=True)`。模型输出格式异常被伪装成成功分析。

**方案**：
- JSON 提取失败（返回空 `{}`）→ 直接返回 `AnalysisResult(success=False, error="JSON extraction failed")`
- 删除 `data = extract_json_from_text(text) or {}` 中的 `or {}` 兜底
- `pipeline_executor` 对 `success=False` 标记重试或降级

**文件**：`src/analyzer/core.py`、`src/core/pipeline_executor.py`

---

### 4. DeepSeek tool-calling

**方案**：暂不接入。保持当前搜索 → 喂数据 → LLM 分析流水线。

---

## 第二期：产品一致性

### 5. 文档漂移清理

**问题**：README 声称有 API 服务模式但仓库无 `api/` 实现；`config.example.yaml` 保留 `webui_port`；`docs/architecture/api_spec.json` 描述不存在的 `/api/v1/*` 端点；繁中/英文文档包含 API 端点引用。

**方案**：
- 删除 `docs/architecture/api_spec.json`
- 删除 `config.example.yaml` 中 `webui_port` 字段及对应的 Config metadata
- README 删除 API 服务模式描述，保留纯 CLI 工具定位
- `docs/README_CHT.md`、`docs/README_EN.md` 删除 `/api/v1/*` 端点引用
- 全局搜索残留 API/WebUI 引用并清理

**文件**：`README.md`、`docs/README_CHT.md`、`docs/README_EN.md`、`docs/architecture/api_spec.json`、`config.example.yaml`、`src/config/manager.py`

---

### 6. 证据溯源

**问题**：Agent 工具返回只含 `title/snippet/source`，无 `url/published_date/relevance`。报告结论无法追溯到原始来源。

**方案**：
- 新增 `Evidence` dataclass：`url`、`published_date`、`source`、`title`、`snippet`、`relevance`
- `NewsItem` 透传数据源返回的 `url` 和 `published_date`
- Prompt 要求：引用事实时注明 `<来源> (<日期>)`
- 报告模板新增 "📎 信息来源" 章节，要求模型在末尾列出关键结论依据
- Agent 工具 `search_tools.py` 补齐返回结构
- 数据库 migration 可选（后续按需添加 `evidence_json` 列）

**文件**：`src/schemas/`（新增或扩展）、`src/analyzer/prompt_builder.py`、`src/agent/tools/search_tools.py`、`src/search/service.py`

---

### 7. 配置可用性分级

**问题**：`SearchService` 自动启用 AkShare，但配置校验只把 Tavily/Finnhub/OpenBB 算作搜索能力。用户实际有弱搜索，但系统提示"未配置搜索"。

**方案**：
- 配置校验增加两级判断：`strong`（Tavily/Finnhub/OpenBB 至少一个）和 `weak`（仅 AkShare）
- `weak` 时打印："搜索能力：弱（仅 AkShare 东方财富公告），建议配置 Tavily 获取完整新闻与宏观信息"
- 不改变 AkShare 自动启用行为

**文件**：`src/config/manager.py`

---

## 执行顺序

```
第一期（独立）：1 → 2 → 3（DeepSeek 不动）
第二期（独立，可在第一期完成后执行）：5 → 6 → 7
```

两期无代码依赖，可分别 spec → plan → 实施。
