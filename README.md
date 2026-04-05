<div align="center">

# 📈 股票智能分析系统

[![GitHub stars](https://img.shields.io/github/stars/ZhuLinsen/daily_stock_analysis?style=social)](https://github.com/ZhuLinsen/daily_stock_analysis/stargazers)
[![CI](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Ready-2088FF?logo=github-actions&logoColor=white)](https://github.com/features/actions)

<p>
  <a href="https://trendshift.io/repositories/18527" target="_blank"><img src="https://trendshift.io/api/badge/repositories/18527" alt="ZhuLinsen%2Fdaily_stock_analysis | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
  <a href="https://hellogithub.com/repository/ZhuLinsen/daily_stock_analysis" target="_blank"><img src="https://api.hellogithub.com/v1/widgets/recommend.svg?rid=6daa16e405ce46ed97b4a57706aeb29f&claim_uid=pfiJMqhR9uvDGlT&theme=neutral" alt="Featured｜HelloGitHub" style="width: 250px; height: 54px;" width="250" height="54" /></a>
</p>

> 🤖 基于 AI 大模型的 A股/港股/美股自选股智能分析系统，每日自动分析并推送「决策仪表盘」到企业微信/飞书/Telegram/Discord/Slack/邮箱

[**功能特性**](#-功能特性) · [**快速开始**](#-快速开始) · [**推送效果**](#-推送效果) · [**完整指南**](docs/full-guide.md) · [**常见问题**](docs/FAQ.md) · [**更新日志**](docs/CHANGELOG.md)

简体中文 | [English](docs/README_EN.md) | [繁體中文](docs/README_CHT.md)

</div>

## 💖 赞助商 (Sponsors)
<div align="center">
  <a href="https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis" target="_blank">
    <img src="./sources/serpapi_banner_zh.png" alt="轻松抓取搜索引擎上的实时金融新闻数据 - SerpApi" height="160">
  </a>
</div>
<br>


## ✨ 功能特性

| 模块 | 功能 | 说明 |
|------|------|------|
| AI | 决策仪表盘 | 一句话核心结论 + 精确买卖点位 + 操作检查清单 |
| 分析 | 多维度分析 | 技术面（盘中实时 MA/多头排列）+ 筹码分布 + 舆情情报 + 实时行情 |
| 市场 | 全球市场 | 支持 A股、港股、美股及美股指数（SPX、DJI、IXIC 等） |
| 基本面 | 结构化聚合 | 新增 `fundamental_context`（valuation/growth/earnings/institution/capital_flow/dragon_tiger/boards，其中 `earnings.data` 新增 `financial_report` 与 `dividend`，`boards` 表示板块涨跌榜），主链路 fail-open 降级 |
| 策略 | 市场策略系统 | 内置 A股「三段式复盘策略」与美股「Regime Strategy」，输出进攻/均衡/防守或 risk-on/neutral/risk-off 计划，并附“仅供参考，不构成投资建议”提示 |
| 复盘 | 大盘复盘 | 每日市场概览、板块涨跌；支持 cn(A股)/us(美股)/both(两者) 切换 |
| 补全 | 智能补全 (MVP) | **[测试阶段]** 首页搜索框支持代码/名称/拼音/别名联想；索引已覆盖 A股、港股、美股三个市场，支持通过 Tushare 或 AkShare 数据源更新 |
| 智能导入 | 多源导入 | 支持图片、CSV/Excel 文件、剪贴板粘贴；Vision LLM 提取代码+名称；置信度分层确认；名称→代码解析（本地+拼音+AkShare） |
| 历史记录 | 批量管理 | 支持多选、全选及批量删除历史分析记录，优化管理效率与 UI/UX 体验 |
| 回测 | AI 回测验证 | 自动评估历史分析准确率，支持按股票与分析日期查看”AI 预测 vs 次日实际（1 日窗口）”和准确率 |
| 资讯 | 公司公告 + 资金流 | IntelAgent 新增公告抓取与主力资金流维度（上交所/深交所/cninfo + A 股主力资金流）用于补强舆情链路 |
| **Agent 问股** | **策略对话** | **多轮策略问答，支持均线金叉/缠论/波浪等 11 种内置策略，Bot 全链路** |
| 推送 | 多渠道通知 | 企业微信、飞书、Telegram、Discord、Slack、钉钉、邮件、Pushover |
| 自动化 | 定时运行 | GitHub Actions 定时执行，无需服务器 |

> 历史报告详情会优先展示 AI 返回的原始「狙击点位」文本，避免区间价、条件说明等复杂内容在历史回看时被压缩成单个数字。


</parameter_replace_all>

### 技术栈与数据来源

| 类型 | 支持 |
|------|------|
| AI 模型 | [AIHubMix](https://aihubmix.com/?aff=CfMq)、Gemini、OpenAI 兼容、DeepSeek、通义千问、Claude、Ollama 本地模型 等（统一通过 [LiteLLM](https://github.com/BerriAI/litellm) 调用，支持多 Key 负载均衡）|
| 行情数据 | AkShare、Tushare、Pytdx、Baostock、YFinance |
| 新闻搜索 | Tavily、SerpAPI、Bocha、Brave、MiniMax |
| 社交舆情 | [Stock Sentiment API](https://api.adanos.org/docs)（Reddit / X / Polymarket，仅美股，可选） |

> 注：美股历史数据与实时行情统一使用 YFinance，确保复权一致性

### 内置交易纪律

| 规则 | 说明 |
|------|------|
| 严禁追高 | 乖离率超阈值（默认 5%，可配置）自动提示风险；强势趋势股自动放宽 |
| 趋势交易 | MA5 > MA10 > MA20 多头排列 |
| 精确点位 | 买入价、止损价、目标价 |
| 检查清单 | 每项条件以「满足 / 注意 / 不满足」标记 |
| 新闻时效 | 可配置新闻最大时效（默认 3 天），避免使用过时信息 |

## 🚀 快速开始

### 方式一：GitHub Actions（推荐）

> 5 分钟完成部署，零成本，无需服务器。


#### 1. Fork 本仓库

点击右上角 `Fork` 按钮（顺便点个 Star⭐ 支持一下）

#### 2. 配置 Secrets

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

**AI 模型配置（至少配置一个）**

> 详细配置说明见 [LLM 配置指南](docs/LLM_CONFIG_GUIDE.md)（三层配置、渠道模式、YAML高级配置、Vision、Agent、排错），GitHub Actions用户也可以实现YAML高级配置。进阶用户可配置 `LITELLM_MODEL`、`LITELLM_FALLBACK_MODELS` 或 `LLM_CHANNELS` 多渠道模式。

> 现在推荐把多模型配置统一写成 `LLM_CHANNELS + LLM_<NAME>_PROTOCOL/BASE_URL/API_KEY/MODELS/ENABLED`。`.env` 使用同一套字段，便于相互切换。

> 💡 **推荐 [AIHubMix](https://aihubmix.com/?aff=CfMq)**：一个 Key 即可使用 Gemini、GPT、Claude、DeepSeek 等全球主流模型，无需科学上网，含免费模型（glm-5、gpt-4o-free 等），付费模型高稳定性无限并发。本项目可享 **10% 充值优惠**。

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `AIHUBMIX_KEY` | [AIHubMix](https://aihubmix.com/?aff=CfMq) API Key，一 Key 切换使用全系模型，免费模型可用 | 可选 |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/) 获取免费 Key（需科学上网） | 可选 |
| `ANTHROPIC_API_KEY` | [Anthropic Claude](https://console.anthropic.com/) API Key | 可选 |
| `ANTHROPIC_MODEL` | Claude 模型（如 `claude-3-5-sonnet-20241022`） | 可选 |
| `OPENAI_API_KEY` | OpenAI 兼容 API Key（支持 DeepSeek、通义千问等） | 可选 |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址（如 `https://api.deepseek.com/v1`） | 可选 |
| `OPENAI_MODEL` | 模型名称（如 `gemini-3.1-pro-preview`、`gemini-3-flash-preview`、`gpt-5.2`） | 可选 |
| `OPENAI_VISION_MODEL` | 图片识别专用模型（部分第三方模型不支持图像；不填则用 `OPENAI_MODEL`） | 可选 |
| `OLLAMA_API_BASE` | Ollama 本地服务地址（如 `http://localhost:11434`），**不要**用 `OPENAI_BASE_URL` 配置 Ollama，详见 [LLM 配置指南 - Ollama](docs/LLM_CONFIG_GUIDE.md#示例-4使用-ollama-本地模型) | 可选 |

> 注：AI 优先级 Gemini > Anthropic > OpenAI（含 AIHubmix）> Ollama，至少配置一个。`AIHUBMIX_KEY` 无需配置 `OPENAI_BASE_URL`，系统自动适配。图片识别需 Vision 能力模型。DeepSeek 思考模式（deepseek-reasoner、deepseek-r1、qwq、deepseek-chat）按模型名自动识别，无需额外配置。**Ollama 本地模型**（无需 API Key）必须使用 `OLLAMA_API_BASE`，误用 `OPENAI_BASE_URL` 会导致 404。

<details>
<summary><b>通知渠道配置</b>（点击展开，至少配置一个）</summary>


| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `WECHAT_WEBHOOK_URL` | 企业微信 Webhook URL | 可选 |
| `FEISHU_WEBHOOK_URL` | 飞书 Webhook URL | 可选 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（@BotFather 获取） | 可选 |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | 可选 |
| `TELEGRAM_MESSAGE_THREAD_ID` | Telegram Topic ID (用于发送到子话题) | 可选 |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL | 可选 |
| `DISCORD_BOT_TOKEN` | Discord Bot Token（与 Webhook 二选一） | 可选 |
| `DISCORD_MAIN_CHANNEL_ID` | Discord Channel ID（使用 Bot 时需要） | 可选 |
| `SLACK_BOT_TOKEN` | Slack Bot Token（推荐，支持图片上传；同时配置时优先于 Webhook） | 可选 |
| `SLACK_CHANNEL_ID` | Slack Channel ID（使用 Bot 时需要） | 可选 |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL（仅文本，不支持图片） | 可选 |
| `EMAIL_SENDER` | 发件人邮箱（如 `xxx@qq.com`） | 可选 |
| `EMAIL_PASSWORD` | 邮箱授权码（非登录密码） | 可选 |
| `EMAIL_RECEIVERS` | 收件人邮箱（多个用逗号分隔，留空则发给自己） | 可选 |
| `EMAIL_SENDER_NAME` | 邮件发件人显示名称（默认：daily_stock_analysis股票分析助手，支持中文并自动编码邮件头） | 可选 |
| `STOCK_GROUP_N` / `EMAIL_GROUP_N` | 股票分组发往不同邮箱（如 `STOCK_GROUP_1=600519,300750` `EMAIL_GROUP_1=user1@example.com`） | 可选 |
| `PUSHPLUS_TOKEN` | PushPlus Token（[获取地址](https://www.pushplus.plus)，国内推送服务） | 可选 |
| `PUSHPLUS_TOPIC` | PushPlus 群组编码（一对多推送，配置后消息推送给群组所有订阅用户） | 可选 |
| `SERVERCHAN3_SENDKEY` | Server酱³ Sendkey（[获取地址](https://sc3.ft07.com/)，手机APP推送服务） | 可选 |
| `CUSTOM_WEBHOOK_URLS` | 自定义 Webhook（支持钉钉等，多个用逗号分隔） | 可选 |
| `CUSTOM_WEBHOOK_BEARER_TOKEN` | 自定义 Webhook 的 Bearer Token（用于需要认证的 Webhook） | 可选 |
| `WEBHOOK_VERIFY_SSL` | Webhook HTTPS 证书校验（默认 true）。设为 false 可支持自签名证书。警告：关闭有严重安全风险，仅限可信内网 | 可选 |
| `SCHEDULE_RUN_IMMEDIATELY` | 定时模式启动时是否立即执行一次分析 | 可选 |
| `RUN_IMMEDIATELY` | 非定时模式启动时是否立即执行一次分析 | 可选 |
| `SINGLE_STOCK_NOTIFY` | 单股推送模式：设为 `true` 则每分析完一只股票立即推送 | 可选 |
| `REPORT_TYPE` | 报告类型：`simple`(精简)、`full`(完整)、`brief`(3-5句概括)，云服务器部署推荐设为 `full` | 可选 |
| `REPORT_LANGUAGE` | 报告输出语言：`zh`(默认中文) / `en`(英文)；会同步影响 Prompt、Markdown 模板、通知 fallback 与 Web 报告页固定文案 | 可选 |
| `REPORT_SUMMARY_ONLY` | 仅分析结果摘要：设为 `true` 时只推送汇总，不含个股详情 | 可选 |
| `REPORT_TEMPLATES_DIR` | Jinja2 模板目录（相对项目根，默认 `templates`） | 可选 |
| `REPORT_RENDERER_ENABLED` | 启用 Jinja2 模板渲染（默认 `false`，保证零回归） | 可选 |
| `REPORT_INTEGRITY_ENABLED` | 启用报告完整性校验，缺失必填字段时重试或占位补全（默认 `true`） | 可选 |
| `REPORT_INTEGRITY_RETRY` | 完整性校验重试次数（默认 `1`，`0` 表示仅占位不重试） | 可选 |
| `REPORT_HISTORY_COMPARE_N` | 历史信号对比条数，`0` 关闭（默认），`>0` 启用 | 可选 |
| `ANALYSIS_DELAY` | 个股分析和大盘分析之间的延迟（秒），避免API限流，如 `10` | 可选 |
| `MAX_WORKERS` | 异步分析任务队列并发线程数（默认 `3`）；保存后队列空闲时自动应用，繁忙时延后生效 | 可选 |
| `MERGE_EMAIL_NOTIFICATION` | 个股与大盘复盘合并推送（默认 false），减少邮件数量 | 可选 |
| `MARKDOWN_TO_IMAGE_CHANNELS` | 将 Markdown 转为图片发送的渠道（逗号分隔）：`telegram,wechat,custom,email,slack` | 可选 |
| `MARKDOWN_TO_IMAGE_MAX_CHARS` | 超过此长度不转图片，避免超大图片（默认 `15000`） | 可选 |
| `MD2IMG_ENGINE` | 转图引擎：`wkhtmltoimage`（默认）或 `markdown-to-file`（emoji 更好） | 可选 |

> 至少配置一个渠道，配置多个则同时推送。图片发送与引擎安装细节请参考 [完整指南](docs/full-guide.md)

</details>

**其他配置**

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `STOCK_LIST` | 自选股代码，如 `600519,hk00700,AAPL,TSLA` | ✅ |
| `TAVILY_API_KEYS` | [Tavily](https://tavily.com/) 搜索 API（新闻搜索） | 推荐 |
| `MINIMAX_API_KEYS` | [MiniMax](https://platform.minimaxi.com/) Coding Plan Web Search（结构化搜索结果） | 可选 |
| `SERPAPI_API_KEYS` | [SerpAPI](https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis) 全渠道搜索 | 可选 |
| `BOCHA_API_KEYS` | [博查搜索](https://open.bocha.cn/) Web Search API（中文搜索优化，支持AI摘要，多个key用逗号分隔） | 可选 |
| `BRAVE_API_KEYS` | [Brave Search](https://brave.com/search/api/) API（隐私优先，美股优化，多个key用逗号分隔） | 可选 |
| `SEARXNG_BASE_URLS` | SearXNG 自建实例（无配额兜底，需在 settings.yml 启用 format: json）；留空时默认自动发现公共实例 | 可选 |
| `SEARXNG_PUBLIC_INSTANCES_ENABLED` | 是否在 `SEARXNG_BASE_URLS` 为空时自动从 `searx.space` 获取公共实例（默认 `true`） | 可选 |
| `SOCIAL_SENTIMENT_API_KEY` | [Stock Sentiment API](https://api.adanos.org/docs)（Reddit/X/Polymarket 社交舆情，仅美股） | 可选 |
| `SOCIAL_SENTIMENT_API_URL` | 自定义社交舆情 API 地址（默认 `https://api.adanos.org`） | 可选 |
| `TUSHARE_TOKEN` | [Tushare Pro](https://tushare.pro/weborder/#/login?reg=834638 ) Token | 可选 |
| `TICKFLOW_API_KEY` | [TickFlow](https://tickflow.org) API Key（增强 A 股大盘复盘指数；若套餐支持标的池查询，也可增强市场统计） | 可选 |
| `PREFETCH_REALTIME_QUOTES` | 实时行情预取开关：设为 `false` 可禁用全市场预取（默认 `true`） | 可选 |
| `WECHAT_MSG_TYPE` | 企微消息类型，默认 markdown，支持配置 text 类型，发送纯 markdown 文本 | 可选 |
| `NEWS_STRATEGY_PROFILE` | 新闻策略窗口档位：`ultra_short`(1天) / `short`(3天) / `medium`(7天) / `long`(30天)，默认 `short` | 可选 |
| `NEWS_MAX_AGE_DAYS` | 新闻最大时效上限（天），默认 3；实际窗口 `effective_days = min(profile_days, NEWS_MAX_AGE_DAYS)`，例如 `ultra_short(1)` + `7` 仍为 `1` 天 | 可选 |
| `BIAS_THRESHOLD` | 乖离率阈值（%），默认 5.0，超过提示不追高；强势趋势股自动放宽 | 可选 |
| `AGENT_MODE` | 开启 Agent 策略问股模式（内部统一命名为 skill，`true`/`false`，默认 false） | 可选 |
| `AGENT_LITELLM_MODEL` | Agent 主模型（可选）；留空继承 `LITELLM_MODEL`，无前缀会按 `openai/<model>` 解析 | 可选 |
| `AGENT_SKILLS` | 激活的策略技能 id（逗号分隔），`all` 启用全部策略技能；留空时使用主默认策略 skill（内置默认是 `bull_trend`），详见 `.env.example` | 可选 |
| `AGENT_MAX_STEPS` | Agent 最大推理步数（默认 10） | 可选 |
| `AGENT_SKILL_DIR` | 自定义策略技能目录（默认沿用内置 `strategies/` 兼容路径） | 可选 |
| `TRADING_DAY_CHECK_ENABLED` | 交易日检查（默认 `true`）：非交易日跳过执行；设为 `false` 或使用 `--force-run` 强制执行 | 可选 |
| `ENABLE_CHIP_DISTRIBUTION` | 启用筹码分布（Actions 默认 false；需筹码数据时在 Variables 中设为 true，接口可能不稳定） | 可选 |
| `ENABLE_FUNDAMENTAL_PIPELINE` | 基本面聚合总开关；关闭时保持主流程不变 | 可选 |
| `FUNDAMENTAL_STAGE_TIMEOUT_SECONDS` | 基本面阶段总预算（秒） | 可选 |
| `FUNDAMENTAL_FETCH_TIMEOUT_SECONDS` | 单能力源调用超时（秒） | 可选 |
| `FUNDAMENTAL_RETRY_MAX` | 基本面能力重试次数（包含首次） | 可选 |
| `FUNDAMENTAL_CACHE_TTL_SECONDS` | 基本面缓存 TTL（秒） | 可选 |
| `FUNDAMENTAL_CACHE_MAX_ENTRIES` | 基本面缓存最大条目数（避免长时间运行内存增长） | 可选 |

> 基本面超时语义（P0）：
> - 当前采用 `best-effort` 软超时（fail-open），超时会立即降级并继续主流程；
> - 不承诺严格硬中断第三方调用线程，因此 `P95 <= 1.5s` 是阶段目标而非硬 SLA；
> - 若业务需要硬 SLA，可在后续阶段升级为“子进程隔离 + kill”的硬超时方案。
> - 字段契约：
>   - `fundamental_context.boards.data` = `sector_rankings`（板块涨跌榜，结构 `{top, bottom}`）；
>   - `fundamental_context.earnings.data.financial_report` = 财报摘要（报告期、营收、归母净利润、经营现金流、ROE）；
>   - `fundamental_context.earnings.data.dividend` = 分红指标（仅现金分红税前口径，含 `events`、`ttm_cash_dividend_per_share`、`ttm_dividend_yield_pct`）；
>   - `get_stock_info.belong_boards` = 个股所属板块列表；
>   - `get_stock_info.boards` 为兼容别名，值与 `belong_boards` 相同（未来仅在大版本考虑移除）；
>   - `get_stock_info.sector_rankings` 与 `fundamental_context.boards.data` 保持一致。
> - 配置 `TICKFLOW_API_KEY` 后，A 股大盘复盘的主要指数行情会优先尝试 TickFlow；若当前套餐支持标的池查询，市场涨跌统计也会优先尝试 TickFlow。失败或权限不足时会回退到现有数据源。
> - 该行为按能力分层而非仅按“是否有 Key”判断：有限权限套餐仍可获得 TickFlow 指数增强；支持 `CN_Equity_A` 标的池查询的套餐会额外获得 TickFlow 市场统计增强。
> - TickFlow 官方 quickstart 展示了 `quotes.get(universes=["CN_Equity_A"])` 的正式用法，但真实线上验证确认：不同套餐权限不同，且 `quotes.get(symbols=[...])` 单次有标的数量限制。
> - TickFlow 返回的 `change_pct` / `amplitude` 在实际接口中为比例值，本项目已统一转换为内部使用的百分比口径，保证与 AkShare / Tushare / efinance 一致。
> - 板块涨跌榜采用固定回退顺序：`AkShare(EM->Sina) -> Tushare -> efinance`。

#### 3. 启用 Actions

`Actions` 标签 → `I understand my workflows, go ahead and enable them`

#### 4. 手动测试

`Actions` → `每日股票分析` → `Run workflow` → `Run workflow`

#### 完成

默认每个**工作日 18:00（北京时间）**自动执行，也可手动触发。默认非交易日（含 A/H/US 节假日）不执行。

> 💡 **关于跳过交易日检查的两种机制：**
> | 机制 | 配置方式 | 生效范围 | 适用场景 |
> |------|----------|----------|----------|
> | `TRADING_DAY_CHECK_ENABLED=false` | 环境变量/Secrets | 全局、长期有效 | 测试环境、长期关闭检查 |
> | `force_run` (UI 勾选) | Actions 手动触发时选择 | 单次运行 | 临时在非交易日执行一次 |
>
> - **环境变量方式**：在 `.env` 或 GitHub Secrets 中设置，影响所有运行方式（定时触发、手动触发、本地运行）
> - **UI 勾选方式**：仅在 GitHub Actions 手动触发时可见，不影响定时任务，适合临时需求

### 方式二：本地运行

```bash
# 克隆项目
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git && cd daily_stock_analysis

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env && vim .env

# 运行分析
python main.py
```

> 定期任务配置、部署等请参考 [完整指南](docs/full-guide.md)

## 📱 推送效果

### 决策仪表盘
```
🎯 2026-02-08 决策仪表盘
共分析3只股票 | 🟢买入:0 🟡观望:2 🔴卖出:1

📊 分析结果摘要
⚪ 中钨高新(000657): 观望 | 评分 65 | 看多
⚪ 永鼎股份(600105): 观望 | 评分 48 | 震荡
🟡 新莱应材(300260): 卖出 | 评分 35 | 看空

⚪ 中钨高新 (000657)
📰 重要信息速览
💭 舆情情绪: 市场关注其AI属性与业绩高增长，情绪偏积极，但需消化短期获利盘和主力流出压力。
📊 业绩预期: 基于舆情信息，公司2025年前三季度业绩同比大幅增长，基本面强劲，为股价提供支撑。

🚨 风险警报:

风险点1：2月5日主力资金大幅净卖出3.63亿元，需警惕短期抛压。
风险点2：筹码集中度高达35.15%，表明筹码分散，拉升阻力可能较大。
风险点3：舆情中提及公司历史违规记录及重组相关风险提示，需保持关注。
✨ 利好催化:

利好1：公司被市场定位为AI服务器HDI核心供应商，受益于AI产业发展。
利好2：2025年前三季度扣非净利润同比暴涨407.52%，业绩表现强劲。
📢 最新动态: 【最新消息】舆情显示公司是AI PCB微钻领域龙头，深度绑定全球头部PCB/载板厂。2月5日主力资金净卖出3.63亿元，需关注后续资金流向。

---
生成时间: 18:00
```

### 大盘复盘
```
🎯 2026-01-10 大盘复盘

📊 主要指数
- 上证指数: 3250.12 (🟢+0.85%)
- 深证成指: 10521.36 (🟢+1.02%)
- 创业板指: 2156.78 (🟢+1.35%)

📈 市场概况
上涨: 3920 | 下跌: 1349 | 涨停: 155 | 跌停: 3

🔥 板块表现
领涨: 互联网服务、文化传媒、小金属
领跌: 保险、航空机场、光伏设备
```
## ⚙️ 配置说明

> 📖 完整环境变量、定时任务配置请参考 [完整配置指南](docs/full-guide.md)
> 邮件通知当前基于 SMTP 授权码/基础认证；若 Outlook / Exchange 账号或租户强制 OAuth2，当前版本暂不支持。


### 🤖 Bot 策略问股

通过 Bot 命令 `/ask` 进行策略分析（支持多股对比）、`/chat` 自由对话。

> 对用户侧文案，本项目仍以”策略”为主称呼；代码、配置统一使用 `skill`，可理解为”可复用的策略能力包”。

- **选择策略**：均线金叉、缠论、波浪理论、多头趋势等 11 种内置策略
- **自然语言提问**：如「用缠论分析 600519」，Agent 自动调用实时行情、K线、技术指标、新闻等工具
- **自定义策略（Skill）**：在 `strategies/` 目录下新建 YAML 文件或在自定义 skill 目录中放入 `SKILL.md` bundle，即可添加新的交易策略，无需写代码
- **Intel 增强字段兼容说明**：`capital_flow_signal` 为新增扩展字段（`inflow/outflow/neutral/not_available`），用于描述 A 股资金流方向，未返回时不会影响后续阶段；下游解析建议以 `risk_alerts` 与 `positive_catalysts` 为主，新增字段可忽略，兼容历史客户端。

> **注意**：配置了任意 AI API Key 后，Agent 对话功能自动可用。每次对话会产生 LLM API 调用费用。若你手动修改了 `.env` 中的模型主备配置（如 `LITELLM_MODEL` / `AGENT_LITELLM_MODEL` / `LITELLM_FALLBACK_MODELS` / `LLM_CHANNELS`），修改后重新运行生效。

## 🗺️ Roadmap

查看已支持的功能和未来规划：[更新日志](docs/CHANGELOG.md)

> 有建议？欢迎 [提交 Issue](https://github.com/ZhuLinsen/daily_stock_analysis/issues)

> ⚠️ **UI 调整提示**：项目当前正在持续进行 Web UI 调整与升级，部分页面在过渡阶段可能仍存在样式、交互或兼容性问题。欢迎通过 [Issue](https://github.com/ZhuLinsen/daily_stock_analysis/issues) 反馈问题，或直接提交 [Pull Request](https://github.com/ZhuLinsen/daily_stock_analysis/pulls) 一起完善。
---

## ☕ 支持项目

如果本项目对你有帮助，欢迎支持项目的持续维护与迭代，感谢支持 🙏  
赞赏可备注联系方式，祝股市长虹

| 支付宝 (Alipay) | 微信支付 (WeChat) | Ko-fi |
| :---: | :---: | :---: |
| <img src="./sources/alipay.jpg" width="200" alt="Alipay"> | <img src="./sources/wechatpay.jpg" width="200" alt="WeChat Pay"> | <a href="https://ko-fi.com/mumu157" target="_blank"><img src="./sources/ko-fi.png" width="200" alt="Ko-fi"></a> |

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

详见 [贡献指南](docs/CONTRIBUTING.md)

### 本地门禁（建议先跑）

```bash
pip install -r requirements.txt
pip install flake8 pytest
./scripts/ci_gate.sh
```

如修改前端（`apps/dsa-web`）：

```bash
cd apps/dsa-web
npm ci
npm run lint
npm run build
```

## 📄 License
[MIT License](LICENSE) © 2026 ZhuLinsen

如果你在项目中使用或基于本项目进行二次开发，
非常欢迎在 README 或文档中注明来源并附上本仓库链接。
这将有助于项目的持续维护和社区发展。

## 📬 联系与合作
- GitHub Issues：[提交 Issue](https://github.com/ZhuLinsen/daily_stock_analysis/issues)
- 合作邮箱：zhuls345@gmail.com

## ⭐ Star History
**如果觉得有用，请给个 ⭐ Star 支持一下！**

<a href="https://star-history.com/#ZhuLinsen/daily_stock_analysis&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=ZhuLinsen/daily_stock_analysis&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=ZhuLinsen/daily_stock_analysis&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=ZhuLinsen/daily_stock_analysis&type=Date" />
 </picture>
</a>

## ⚠️ 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。作者不对使用本项目产生的任何损失负责。

---
