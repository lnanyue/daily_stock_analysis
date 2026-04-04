# CLI-Only Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all web frontend, desktop, API server, and Docker infrastructure, leaving a clean CLI-only tool with bot notification support.

**Architecture:** Delete user-facing web layers (frontend, API, desktop, Docker) while preserving core analysis engine, data providers, bot notifications, and CLI entry point. The `main.py` CLI remains the sole user interface.

**Tech Stack:** Python 3.11, pytest, GitHub Actions CLI

---

### Task 1: Delete web frontend and desktop apps

**Files:**
- Delete: `apps/dsa-web/` (entire directory)
- Delete: `apps/dsa-desktop/` (entire directory)
- Delete: `static/` (entire directory — web static assets)
- Delete: `templates/` (entire directory — web HTML templates)
- Delete: `scripts/build-desktop-macos.sh`
- Delete: `scripts/build-desktop.ps1`
- Delete: `scripts/run-desktop.ps1`
- Delete: `scripts/build-all-macos.sh` (references desktop)
- Delete: `scripts/build-all.ps1` (references desktop)

- [ ] **Step 1: Delete frontend and desktop directories**

```bash
# Delete web frontend
rm -rf apps/dsa-web/

# Delete Electron desktop
rm -rf apps/dsa-desktop/

# Delete web static assets and templates
rm -rf static/
rm -rf templates/

# Delete desktop build scripts
rm -f scripts/build-desktop-macos.sh scripts/build-desktop.ps1
rm -f scripts/run-desktop.ps1
rm -f scripts/build-all-macos.sh scripts/build-all.ps1
```

- [ ] **Step 2: Remove the apps/ directory if now empty**

```bash
rmdir apps/ 2>/dev/null || true
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: remove web frontend, desktop, and web static assets"
```

### Task 2: Delete API server and web UI modules

**Files:**
- Delete: `server.py`
- Delete: `webui.py`
- Delete: `src/webui_frontend.py`
- Delete: `api/` (entire directory)

- [ ] **Step 1: Delete API server and web UI files**

```bash
rm -f server.py
rm -f webui.py
rm -f src/webui_frontend.py
rm -rf api/
```

- [ ] **Step 2: Remove `prepare_webui_frontend_assets` import from main.py**

Open `main.py` line 49 and remove the import:

```python
# REMOVE line 49:
from src.webui_frontend import prepare_webui_frontend_assets
```

- [ ] **Step 3: Remove `start_api_server` function from main.py**

Delete lines 445-469 (the entire `start_api_server` function):

```python
# DELETE this entire function:
def start_api_server(host: str, port: int, config: Config) -> None:
    """..."""
    import threading
    import uvicorn
    ...
    logger.info(f"FastAPI 服务已启动: http://{host}:{port}")
```

- [ ] **Step 4: Remove web/serve-related CLI arguments from `parse_arguments()` in main.py**

Delete these argument groups from `parse_arguments()`:

```python
# DELETE these 5 add_argument calls:
    parser.add_argument(
        '--webui',
        action='store_true',
        help='启动 Web 管理界面'
    )

    parser.add_argument(
        '--webui-only',
        action='store_true',
        help='仅启动 Web 服务，不执行自动分析'
    )

    parser.add_argument(
        '--serve',
        action='store_true',
        help='启动 FastAPI 后端服务（同时执行分析任务）'
    )

    parser.add_argument(
        '--serve-only',
        action='store_true',
        help='仅启动 FastAPI 后端服务，不自动执行分析'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=8000,
        help='FastAPI 服务端口（默认 8000）'
    )

    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='FastAPI 服务监听地址（默认 0.0.0.0）'
    )
```

- [ ] **Step 5: Remove the web/serve startup block from `main()` in main.py**

Replace the web startup block (lines 551-596) with nothing — delete entirely:

```python
# DELETE lines 551-596 (=== 处理 --webui ... === through === 仅 Web 服务模式 ===)
# The entire block from:
#     # === 处理 --webui / --webui-only 参数，映射到 --serve / --serve-only ===
# through:
#         return 0
```

Also delete the `bot_clients_started` variable and its uses since it's only set by the service block. Remove line 571:
```python
# DELETE: bot_clients_started = False
```

And the `if bot_clients_started:` block (lines 581-582):
```python
# DELETE:
# if bot_clients_started:
#     start_bot_stream_clients(config)
```

Replace with direct bot startup (bot should start regardless of web):
```python
    # Start bot clients independently of web server
    start_bot_stream_clients(config)
```

- [ ] **Step 6: Remove `keep_running` / service loop from `main()`**

Replace lines 710-718:

```python
# REPLACE this block:
        # 如果启用了服务且是非定时任务模式，保持程序运行
        keep_running = start_serve and not (args.schedule or config.schedule_enabled)
        if keep_running:
            logger.info("API 服务运行中 (按 Ctrl+C 退出)...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

# WITH nothing (delete the entire block)
```

- [ ] **Step 7: Remove `time` import if no longer needed**

Check if `time` is still used elsewhere in main.py. It IS used in `run_full_analysis` (line 329: `time.sleep(analysis_delay)`), so keep it.

- [ ] **Step 8: Verify no remaining references to deleted modules**

```bash
grep -r "prepare_webui_frontend_assets\|start_api_server\|from api\.\|import api\.\|server\.py\|webui\.py\|webui_frontend" main.py || echo "No remaining references"
```

Expected: "No remaining references"

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: remove API server, FastAPI web UI, and serve flags from CLI"
```

### Task 3: Delete API-related test files

**Files:**
- Delete: `tests/test_system_config_api.py`
- Delete: `tests/test_analysis_integration.py`
- Delete: `tests/test_analysis_api_contract.py`
- Delete: `tests/test_portfolio_pr2.py`
- Delete: `tests/test_portfolio_api.py`
- Delete: `tests/test_autocomplete_pr0.py`
- Delete: `tests/test_auth_status_setup_state.py`
- Delete: `tests/test_auth_api.py`
- Delete: `tests/test_api_app_cors.py`
- Delete: `tests/test_agent_models_api.py`

Note: `tests/test_analysis_history.py` and `tests/test_portfolio_pr2.py` may have mixed tests. We delete only the fully API-dependent files.

- [ ] **Step 1: Delete the 10 API test files**

```bash
rm -f tests/test_system_config_api.py
rm -f tests/test_analysis_integration.py
rm -f tests/test_analysis_api_contract.py
rm -f tests/test_portfolio_pr2.py
rm -f tests/test_portfolio_api.py
rm -f tests/test_autocomplete_pr0.py
rm -f tests/test_auth_status_setup_state.py
rm -f tests/test_auth_api.py
rm -f tests/test_api_app_cors.py
rm -f tests/test_agent_models_api.py
```

- [ ] **Step 2: Check test_analysis_history.py for API-dependent code**

```bash
grep -n "from api\.\|import api\." tests/test_analysis_history.py || echo "No API imports — keep file"
```

If there ARE API imports, we need to check line 620+ for the API test method and decide. The spec only called out `test_delete_history_api_deletes_selected_records` on line 620. If the file has `from api.` imports at the top but only within specific test methods, the file can be kept as the import is lazy (inside a function). If the import is at top level, we need to remove that test method.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: remove API-dependent test files"
```

### Task 4: Delete Docker files and workflows

**Files:**
- Delete: `docker/` (entire directory)
- Delete: `.dockerignore`
- Delete: `.github/workflows/docker-publish.yml`
- Delete: `.github/workflows/ghcr-dockerhub.yml`
- Delete: `.github/workflows/desktop-release.yml`
- Delete: `docs/docker/` (entire directory)

- [ ] **Step 1: Delete Docker and desktop-related files**

```bash
rm -rf docker/
rm -f .dockerignore
rm -f .github/workflows/docker-publish.yml
rm -f .github/workflows/ghcr-dockerhub.yml
rm -f .github/workflows/desktop-release.yml
rm -rf docs/docker/
```

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "refactor: remove Docker infrastructure and desktop release workflows"
```

### Task 5: Update CI workflow for backend-only

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Remove the `changes` job and `web-gate` job, and `docker-build` job from ci.yml**

Replace the entire `.github/workflows/ci.yml` with:

```yaml
name: CI

on:
  pull_request:
    branches: [main]

concurrency:
  group: ci-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  ai-governance:
    name: ai-governance
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v5
      - name: Setup Python
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'
      - name: Check AI governance assets
        run: python scripts/check_ai_assets.py

  backend-gate:
    name: backend-gate
    runs-on: ubuntu-latest
    needs: [ai-governance]
    steps:
      - name: Checkout
        uses: actions/checkout@v5
      - name: Setup Python
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'
          cache: 'pip'
          cache-dependency-path: |
            requirements.txt
            requirements-ci.txt
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          for attempt in 1 2 3; do
            if python -m pip install -r requirements-ci.txt; then
              break
            fi
            if [ "$attempt" -eq 3 ]; then
              echo "Dependency install failed after ${attempt} attempts." >&2
              exit 1
            fi
            echo "Dependency install attempt ${attempt} failed, retrying in 15s..." >&2
            sleep 15
          done
      - name: Python syntax check
        run: ./scripts/ci_gate.sh syntax
      - name: Flake8 critical checks
        run: ./scripts/ci_gate.sh flake8
      - name: Local deterministic checks
        run: ./scripts/ci_gate.sh deterministic
      - name: Offline test suite
        run: ./scripts/ci_gate.sh offline-tests
```

This removes:
- The `changes` job (no longer needed — frontend path filtering is gone)
- The `docker-build` job (Docker removed)
- The `web-gate` job (web frontend removed)

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "ci: remove web-gate and docker-build jobs from CI workflow"
```

### Task 6: Clean up .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Remove WebUI and ADMIN_AUTH sections from `.env.example`**

Delete the two sections at lines 407-428:

```ini
# ===================================
# WebUI 配置（可选）
# ===================================
# 是否默认启动 WebUI（true/false，默认 false）
WEBUI_ENABLED=false
# WebUI 监听地址（默认 127.0.0.1；Docker/云服务器需设为 0.0.0.0 才能外网访问，详见 docs/deploy-webui-cloud.md）
WEBUI_HOST=127.0.0.1
# WebUI 监听端口（默认 8000）
WEBUI_PORT=8000
# 启动 Web 服务前是否自动构建前端（npm install && npm run build，默认 true）
WEBUI_AUTO_BUILD=true
# 反向代理下信任 X-Forwarded-For 获取真实 IP（Nginx/Cloudflare 前置时设为 true，直连公网时保持 false 防伪造）
# TRUST_X_FORWARDED_FOR=false

# ===================================
# Web 登录认证（可选）
# ===================================
# 设为 true 启用密码保护；首次访问时在网页设置初始密码，可在「系统设置 > 修改密码」中修改
# 忘记密码可在服务器执行: python -m src.auth reset_password
ADMIN_AUTH_ENABLED=false
# ADMIN_SESSION_MAX_AGE_HOURS=24  # Session 有效期（小时）
```

Also update line 37 — remove the phrase "或在 Web 设置页可视化管理":

```diff
- # 【进阶】需要多模型 / 多平台 fallback → 配置下方「多渠道」或在 Web 设置页可视化管理。
+ # 【进阶】需要多模型 / 多平台 fallback → 配置下方「多渠道」。
```

Also update line 18 comment from "定时任务配置（本地/Docker运行）" to "定时任务配置":

```diff
- # 定时任务配置（本地/Docker运行）
+ # 定时任务配置
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove web/desktop/Docker env vars from .env.example"
```

### Task 7: Clean up requirements

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements-ci.txt`

- [ ] **Step 1: Remove web/API-only dependencies from requirements.txt**

Packages to consider removing: `uvicorn`, `fastapi`, `starlette`, `pydantic` (only if not used elsewhere — check grep), and any frontend-related packages.

First verify which packages are still imported by remaining code:

```bash
grep -rh "^from fastapi\|^import fastapi\|^from uvicorn\|^import uvicorn" src/ bot/ data_provider/ main.py || echo "No FastAPI/uvicorn imports remain"
```

If no imports remain, remove these packages from `requirements.txt`:

```
# Remove lines containing:
# uvicorn
# fastapi
```

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "chore: remove FastAPI/uvicorn dependencies from requirements"
```

### Task 8: Run verification

- [ ] **Step 1: Compile-check all remaining Python files**

```bash
find . -name '*.py' -not -path '*/node_modules/*' -not -path '*/.git/*' | xargs python -m py_compile
```

Expected: No output (all files compile cleanly)

- [ ] **Step 2: Run offline tests**

```bash
python -m pytest -m "not network" -v
```

Expected: All tests pass

- [ ] **Step 3: Run CI gate script**

```bash
bash scripts/ci_gate.sh
```

Expected: All checks pass

- [ ] **Step 4: Verify CLI help works**

```bash
python main.py --help
```

Expected: Shows CLI help without web/serve flags, without errors

- [ ] **Step 5: If all verifications pass, commit**

No code change commit needed if all pass — verification only.

### Task 9: Update README.md

**Files:**
- Modify: `README.md`
- Modify: `docs/README_EN.md` (or `README_EN.md` at root if exists)

- [ ] **Step 1: Update README.md**

Remove all references to:
- Web UI (`--webui`, `--webui-only`, `--serve`, WebUI screenshots, web deployment)
- Desktop app (`apps/dsa-desktop`, Electron, desktop package)
- Docker (docker-compose, Dockerfile, Zeabur)
- API routes (`/api/v1/...`, `/docs`)

Update the running examples to CLI only:

```markdown
## Usage

```bash
# Basic analysis
python main.py

# Debug mode
python main.py --debug

# Dry run (fetch data only)
python main.py --dry-run

# Specific stocks
python main.py --stocks 600519,000001,AAPL

# Market review only
python main.py --market-review

# Scheduled mode
python main.py --schedule

# Backtest
python main.py --backtest
```
```

The bot notification setup section should remain (Telegram, Feishu, Discord, etc.).

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "docs: update README for CLI-only usage"
```

### Task 10: Clean up remaining documentation

**Files:**
Modify or remove any docs referencing deleted features.

- [ ] **Step 1: Find docs referencing deleted features**

```bash
grep -rl "webui\|WebUI\|web-ui\|dsa-web\|dsa-desktop\|desktop.*app\|docker.*compose\|Dockerfile\|--serve\|--webui" docs/ README*.md || echo "No remaining references"
```

- [ ] **Step 2: Delete these docs entirely**

```bash
rm -f docs/deploy-webui-cloud.md
rm -f docs/desktop-package.md
```

- [ ] **Step 3: Update DEPLOY.md and DEPLOY_EN.md**

Remove Docker/docker-compose/Dockerfile/Zeabur references. Check if other deployment methods (bare-metal, systemd, etc.) remain — if yes, keep and update. If Docker was the only method documented, simplify to cover a basic `python main.py` server run.

```bash
grep -n -i "docker\|compose\|zeabur" docs/DEPLOY.md docs/DEPLOY_EN.md || echo "No Docker references to remove"
```

- [ ] **Step 4: Check doc index files**

```bash
grep -n "deploy-webui\|desktop-package\|docker\|zeabur\|webui\|desktop" docs/INDEX_EN.md docs/README_CHT.md 2>/dev/null || echo "No index references to deleted docs"
```

Remove any entries pointing to deleted pages.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: remove web/desktop/Docker documentation references"
```

### Task 11: Verify AGENTS.md / governance assets

- [ ] **Step 1: Run AI governance check**

```bash
python scripts/check_ai_assets.py
```

If this script references `apps/` or `docker/` or the deleted workflows, update it.

- [ ] **Step 2: Final verification**

```bash
python main.py --help
python -m pytest -m "not network" -v
```

- [ ] **Step 3: Commit any governance fixes**

```bash
git add -A
git commit -m "chore: update AI governance assets for CLI-only refactor"
```

---

## Summary of Commits

| # | Commit Message | What |
|---|---|---|
| 1 | `refactor: remove web frontend, desktop, and web static assets` | apps/, static/, templates/, desktop scripts |
| 2 | `refactor: remove API server, FastAPI web UI, and serve flags from CLI` | server.py, webui.py, api/, src/webui_frontend.py, main.py cleanup |
| 3 | `refactor: remove API-dependent test files` | 10 API test files |
| 4 | `refactor: remove Docker infrastructure and desktop release workflows` | docker/, workflows, docs/docker |
| 5 | `ci: remove web-gate and docker-build jobs from CI workflow` | .github/workflows/ci.yml |
| 6 | `chore: remove web/desktop/Docker env vars from .env.example` | .env.example |
| 7 | `chore: remove FastAPI/uvicorn dependencies from requirements` | requirements.txt |
| 8 | _verification only, no commit_ | compile check, pytest, CLI help |
| 9 | `docs: update README for CLI-only usage` | README.md, README_EN.md |
| 10 | `docs: remove web/desktop/Docker documentation references` | docs/ cleanup |
| 11 | `chore: update AI governance assets for CLI-only refactor` | governance script check |
