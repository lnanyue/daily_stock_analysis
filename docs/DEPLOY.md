# 🚀 部署指南

本文档介绍如何将 A股自选股智能分析系统部署到服务器。

## 📋 部署方案对比

| 方案 | 优点 | 缺点 | 推荐场景 |
|------|------|------|----------|
| **GitHub Actions** ⭐ | 完全免费、免服务器、自动定时 | 无状态、无法提供 API | **推荐**：个人用户 |
| **直接部署** | 简单直接、无额外依赖 | 环境依赖、迁移麻烦 | 临时测试 |
| **Systemd 服务** | 系统级管理、开机自启 | 配置繁琐 | 长期稳定运行 |

**结论：个人用户推荐 GitHub Actions，服务器用户推荐直接部署或 Systemd 服务！**

---

## ☁️ 方案一：GitHub Actions 部署（免服务器）

**最简单的方案！** 无需服务器，利用 GitHub 免费计算资源。

### 优势
- ✅ **完全免费**（每月 2000 分钟）
- ✅ **无需服务器**
- ✅ **自动定时执行**
- ✅ **零维护成本**

### 限制
- ⚠️ 无状态（每次运行是新环境）
- ⚠️ 定时可能有几分钟延迟
- ⚠️ 无法提供 HTTP API

### 部署步骤

#### 1. 创建 GitHub 仓库

```bash
# 初始化 git（如果还没有）
cd /path/to/daily_stock_analysis
git init
git add .
git commit -m "Initial commit"

# 创建 GitHub 仓库并推送
# 在 GitHub 网页上创建新仓库后：
git remote add origin https://github.com/你的用户名/daily_stock_analysis.git
git branch -M main
git push -u origin main
```

#### 2. 配置 Secrets（重要！）

打开仓库页面 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

添加以下 Secrets：

| Secret 名称 | 说明 | 必填 |
|------------|------|------|
| `GEMINI_API_KEY` | Gemini AI API Key | ✅ |
| `WECHAT_WEBHOOK_URL` | 企业微信机器人 Webhook | 可选* |
| `FEISHU_WEBHOOK_URL` | 飞书机器人 Webhook | 可选* |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 可选* |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | 可选* |
| `EMAIL_SENDER` | 发件人邮箱 | 可选* |
| `EMAIL_PASSWORD` | 邮箱授权码 | 可选* |
| `STOCK_LIST` | 自选股列表，如 `600519,300750` | ✅ |
| `TAVILY_API_KEYS` | Tavily 搜索 API Key | 推荐 |

> *注：通知渠道至少配置一个，支持多渠道同时推送

#### 3. 验证 Workflow 文件

确保 `.github/workflows/daily_analysis.yml` 文件存在且已提交：

```bash
git add .github/workflows/daily_analysis.yml
git commit -m "Add GitHub Actions workflow"
git push
```

#### 4. 手动测试运行

1. 打开仓库页面 → **Actions** 标签
2. 选择 **"每日股票分析"** workflow
3. 点击 **"Run workflow"** 按钮

### 定时说明

默认配置：**周一到周五，北京时间 18:00** 自动执行

修改时间：编辑 `.github/workflows/daily_analysis.yml` 中的 cron 表达式：

```yaml
schedule:
  - cron: '0 10 * * 1-5'  # UTC 时间，+8 = 北京时间
```

### 常见问题

**Q: 为什么定时任务没有执行？**
A: GitHub Actions 定时任务可能有 5-15 分钟延迟，且仅在仓库有活动时才触发。

**Q: 免费额度够用吗？**
A: 每次运行约 2-5 分钟，一个月 22 个工作日 = 44-110 分钟，远低于 2000 分钟限制。

---

## 🖥️ 方案二：直接部署

### 1. 安装 Python 环境

```bash
# 安装 Python 3.10+
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip

# 创建虚拟环境
python3.10 -m venv /opt/stock-analyzer/venv
source /opt/stock-analyzer/venv/bin/activate
```

### 2. 安装依赖

```bash
cd /opt/stock-analyzer
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 配置环境变量

```bash
cp .env.example .env
vim .env  # 填入配置
```

### 4. 运行

```bash
# 单次运行
python main.py

# 定时任务模式（前台运行）
python main.py --schedule

# 后台运行（使用 nohup）
nohup python main.py --schedule > /dev/null 2>&1 &
```

---

## 🔧 方案三：Systemd 服务

创建 systemd 服务文件实现开机自启和自动重启：

### 1. 创建服务文件

```bash
sudo vim /etc/systemd/system/stock-analyzer.service
```

内容：
```ini
[Unit]
Description=A股自选股智能分析系统
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/stock-analyzer
Environment="PATH=/opt/stock-analyzer/venv/bin"
ExecStart=/opt/stock-analyzer/venv/bin/python main.py --schedule
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

### 2. 启动服务

```bash
# 重载配置
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start stock-analyzer

# 开机自启
sudo systemctl enable stock-analyzer

# 查看状态
sudo systemctl status stock-analyzer

# 查看日志
journalctl -u stock-analyzer -f
```

---

## ⚙️ 配置说明

### 必须配置项

| 配置项 | 说明 | 获取方式 |
|--------|------|----------|
| `GEMINI_API_KEY` | AI 分析必需 | [Google AI Studio](https://aistudio.google.com/) |
| `STOCK_LIST` | 自选股列表 | 逗号分隔的股票代码 |
| `WECHAT_WEBHOOK_URL` | 微信推送 | 企业微信群机器人 |

### 可选配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `SCHEDULE_ENABLED` | `false` | 是否启用定时任务 |
| `SCHEDULE_TIME` | `18:00` | 每日执行时间 |
| `MARKET_REVIEW_ENABLED` | `true` | 是否启用大盘复盘 |
| `TAVILY_API_KEYS` | - | 新闻搜索（可选） |

---

## 🌐 代理配置

如果服务器在国内，访问 Gemini API 需要代理：

```bash
# 在 .env 中配置
USE_PROXY=true
PROXY_HOST=127.0.0.1
PROXY_PORT=10809
```

---

## 📊 监控与维护

### 日志查看

```bash
tail -f /opt/stock-analyzer/logs/stock_analysis_*.log
```

### 健康检查

```bash
# 检查进程
ps aux | grep main.py

# 检查最近的报告
ls -la /opt/stock-analyzer/reports/
```

### 定期维护

```bash
# 清理旧日志（保留7天）
find /opt/stock-analyzer/logs -mtime +7 -delete

# 清理旧报告（保留30天）
find /opt/stock-analyzer/reports -mtime +30 -delete
```

---

## ❓ 常见问题

### 1. API 访问超时

检查代理配置，确保服务器能访问 Gemini API。

### 2. 数据库锁定

```bash
# 停止服务后删除 lock 文件
rm /opt/stock-analyzer/data/*.lock
```

### 3. 内存不足

增加服务器内存或减少并发线程数（`MAX_WORKERS`）。

---

## 🔄 快速迁移

从一台服务器迁移到另一台：

```bash
# 源服务器：打包
cd /opt/stock-analyzer
tar -czvf stock-analyzer-backup.tar.gz .env data/ logs/ reports/

# 目标服务器：部署
mkdir -p /opt/stock-analyzer
cd /opt/stock-analyzer
git clone <your-repo-url> .
tar -xzvf stock-analyzer-backup.tar.gz
pip install -r requirements.txt
```

---

**祝部署顺利！**
