# 配置契约：单真源 Schema 设计

## 问题

当前配置定义分散在三处，存在约 30% 的不一致率：

| 来源 | 数量 | 说明 |
|------|------|------|
| `Config` dataclass fields | 116 | 运行时实际读取的字段，带类型和默认值 |
| `config_registry.py` | 107 | WebUI 使用的字段清单，28 个已不在 Config 中 |
| `.env.example` | ~60 | 文档性变量清单，缺少 50+ 个 Config 实际读取的变量 |
| `config.example.yaml` | ~40 | 示例配置，部分 key 映射到错误的 env var |

根因：没有单一真源，每次新增字段都需要人工同步三到四处，遗漏是常态。

## 方案：Config dataclass metadata 作为真源

在现有的 `Config` dataclass field 上通过 `metadata` dict 标注 env 映射、yaml 路径、分组，然后从 Config 字段反向生成所有衍生文件。

### Metadata 结构

```python
field(metadata={
    "env": "ENV_VAR_NAME",      # 环境变量名（必填）
    "yaml": "section.key",      # YAML 配置路径（可选，扁平配置可省略）
    "group": "notification",    # 分组（可选，用于生成时归类）
})
```

### 示例

```python
@dataclass(init=False)
class Config:
    stock_list: List[str] = field(default_factory=list, metadata={
        "env": "STOCK_LIST",
        "group": "core",
    })
    wechat_webhook_url: Optional[str] = field(default=None, metadata={
        "env": "WECHAT_WEBHOOK_URL",
        "yaml": "notification.wechat_webhook_url",
        "group": "notification",
    })
    report_dir: str = field(default="./report", metadata={
        "env": "REPORT_DIR",
        "yaml": "system.report_dir",
        "group": "system",
    })
```

- metadata dict 是 Python dataclass 原生支持的，不影响 hash/eq/order
- 不强制一次性全量标注——可以先加最关键的 30-40 个字段，增量补全
- 废弃字段直接删除 field，不留 `deprecated` 标记

## 产出物

### P0：CI 配置一致性检查

在 `scripts/ci_gate.sh` 和 `ci.yml` 中新增检查步骤：

1. 遍历 Config 所有带 metadata 的字段，提取 `env` 值
2. **检查 `.env.example`**：每个 `env` 对应的变量是否出现在 `.env.example` 中；`.env.example` 中是否有已不在 Config 中的变量
3. **检查 `config.example.yaml`**：每个 `yaml` 路径是否在 `config.example.yaml` 中有对应值；文件中是否有无元数据匹配的 key
4. 不匹配时输出警告（`config_validate_mode=strict` 时升为错误）

### P1：环境变量示例自动生成

实现一个 `scripts/gen_env_example.py`：

1. 遍历 Config field，按 group 分组
2. 输出格式：`# === {group} ===` → `ENV_VAR=default      # description`
3. 保留现有注释结构，只覆盖变量区

输出直接替换 `.env.example`。

### P2：YAML 配置示例自动生成

实现 `scripts/gen_config_example.py`：

1. 遍历 Config field，按 yaml path 的 section 分组
2. 输出结构化 YAML，注释标注 env 映射
3. 输出直接替换 `config.example.yaml`

### P3：config_registry 退场

1. 新增 metadata 的字段不再写入 `config_registry.py`
2. WebUI 配置页面改用 `Config` 字段 + metadata 作为数据源
3. 旧 registry 条目逐步迁移，迁移完删除 `config_registry.py`

## 完成标准

- [ ] `scripts/ci_gate.sh` 包含配置一致性检查步骤
- [ ] CI 上配置不一致会报警告
- [ ] `scripts/gen_env_example.py` 可运行输出完整的 `.env.example`
- [ ] `scripts/gen_config_example.py` 可运行输出完整的 `config.example.yaml`
- [ ] Config 所有非内部字段（排除 `_instance`、`_agent_mode_explicit` 等前缀带 `_` 的）均标注 `env` metadata（可不等同于一次性 PR）
- [ ] `config_registry.py` 不再新增条目

## 不在此设计中的内容

- 不改 Config 运行时加载逻辑（`_load_from_env`）
- 不改 `get_config()` 返回结构
- 不改配置验证模式（`config_validate_mode`）
- 不引入新依赖（python-dataclasses 是 stdlib）
- 不自动迁移已有配置——只提供检查和生成工具
