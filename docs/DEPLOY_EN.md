# Deployment Guide

This document explains how to deploy the AI Stock Analysis System to a server.

## Deployment Options Comparison

| Option | Pros | Cons | Recommended For |
|------|------|------|----------|
| **GitHub Actions** ⭐ | Completely free, no server, auto-scheduled | Stateless, no HTTP API | **Recommended**: Personal use |
| **Direct Deployment** | Simple, no extra dependencies | Environment dependencies, migration | Temporary testing |
| **Systemd Service** | System-level management, auto-start on boot | Complex configuration | Long-term stable operation |

**Conclusion: Personal users should use GitHub Actions; server users should use Direct Deployment or Systemd!**

---

## Option 1: GitHub Actions Deployment (Serverless)

**The simplest option!** No server needed, leverages GitHub's free compute resources.

### Advantages
- ✅ **Completely free** (2000 minutes/month)
- ✅ **No server needed**
- ✅ **Auto-scheduled execution**
- ✅ **Zero maintenance cost**

### Limitations
- ⚠️ Stateless (fresh environment each run)
- ⚠️ Scheduled timing may have few minutes delay
- ⚠️ Cannot provide HTTP API

### Deployment Steps

#### 1. Create GitHub Repository

```bash
cd /path/to/daily_stock_analysis
git init
git add .
git commit -m "Initial commit"

# After creating new repo on GitHub web:
git remote add origin https://github.com/your-username/daily_stock_analysis.git
git branch -M main
git push -u origin main
```

#### 2. Configure Secrets

Go to repo page → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret Name | Description | Required |
|------------|------|------|
| `GEMINI_API_KEY` | Gemini AI API Key | ✅ |
| `WECHAT_WEBHOOK_URL` | WeChat Work Bot Webhook | Optional* |
| `FEISHU_WEBHOOK_URL` | Feishu Bot Webhook | Optional* |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | Optional* |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | Optional* |
| `EMAIL_SENDER` | Sender email | Optional* |
| `EMAIL_PASSWORD` | Email authorization code | Optional* |
| `STOCK_LIST` | Watchlist, e.g., `600519,300750` | ✅ |
| `TAVILY_API_KEYS` | Tavily Search API Key | Recommended |

> *Note: Configure at least one notification channel

#### 3. Verify Workflow File

Ensure `.github/workflows/daily_analysis.yml` file exists and is committed.

#### 4. Manual Test Run

1. Go to repo page → **Actions** tab
2. Select **"Daily Stock Analysis"** workflow
3. Click **"Run workflow"** button

### Schedule Details

Default: **Monday to Friday, 18:00 Beijing Time**

Modify: Edit cron in `.github/workflows/daily_analysis.yml`:

```yaml
schedule:
  - cron: '0 10 * * 1-5'  # UTC time, +8 = Beijing time
```

---

## Option 2: Direct Deployment

### 1. Install Python Environment

```bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip

python3.10 -m venv /opt/stock-analyzer/venv
source /opt/stock-analyzer/venv/bin/activate
```

### 2. Install Dependencies

```bash
cd /opt/stock-analyzer
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. Configure Environment Variables

```bash
cp .env.example .env
vim .env
```

### 4. Run

```bash
# Single run
python main.py

# Scheduled task mode
python main.py --schedule

# Background run
nohup python main.py --schedule > /dev/null 2>&1 &
```

---

## Option 3: Systemd Service

### 1. Create Service File

```bash
sudo vim /etc/systemd/system/stock-analyzer.service
```

Contents:
```ini
[Unit]
Description=AI Stock Analysis System
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

### 2. Start Service

```bash
sudo systemctl daemon-reload
sudo systemctl start stock-analyzer
sudo systemctl enable stock-analyzer
sudo systemctl status stock-analyzer
journalctl -u stock-analyzer -f
```

---

## Monitoring & Maintenance

### View Logs

```bash
tail -f /opt/stock-analyzer/logs/stock_analysis_*.log
```

### Routine Maintenance

```bash
find /opt/stock-analyzer/logs -mtime +7 -delete
find /opt/stock-analyzer/reports -mtime +30 -delete
```

---

## FAQ

### API access timeout

Check proxy configuration, ensure server can access Gemini API.

### Database locked

```bash
rm /opt/stock-analyzer/data/*.lock
```

### Insufficient memory

Increase server memory or reduce `MAX_WORKERS`.

---

## Quick Migration

```bash
# Source server: Package
cd /opt/stock-analyzer
tar -czvf stock-analyzer-backup.tar.gz .env data/ logs/ reports/

# Target server: Deploy
mkdir -p /opt/stock-analyzer
cd /opt/stock-analyzer
git clone <your-repo-url> .
tar -xzvf stock-analyzer-backup.tar.gz
pip install -r requirements.txt
```

---

**Wishing you a smooth deployment!**
