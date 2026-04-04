# CLI-Only Project Refactor Design

## Goal

Remove all web frontend, desktop, API server, and Docker infrastructure from the project, leaving a clean CLI-only tool with bot notification support.

## Motivation

The current codebase carries significant surface area from the web frontend (`apps/dsa-web/`), Electron desktop wrapper (`apps/dsa-desktop/`), FastAPI server (`server.py`, `api/`), and Docker publishing workflows. These were user-facing layers, but the goal is to simplify the project to a CLI-only tool.

## Design Decisions

### What to Delete

| Path | Reason |
|------|--------|
| `apps/dsa-web/` | Entire React/TypeScript web UI |
| `apps/dsa-desktop/` | Electron desktop wrapper |
| `server.py` | FastAPI server entry point (for web) |
| `api/` | FastAPI routes layer (web-facing) |
| `webui.py` | Web UI entry point |
| `src/webui_frontend.py` | Web rendering logic |
| `static/` | Static assets for web |
| `templates/` | HTML templates for web |
| `docker/` | Docker configuration |
| `.dockerignore` | Docker ignore rules |
| `.github/workflows/docker-publish.yml` | Docker publish |
| `.github/workflows/ghcr-dockerhub.yml` | GHCR/DockerHub publish |
| `.github/workflows/desktop-release.yml` | Desktop release |
| `docs/docker/` | Docker documentation |
| `tests/test_system_config_api.py` | API test — imports `api.v1.endpoints` |
| `tests/test_analysis_integration.py` | API test — uses `api.app.create_app` |
| `tests/test_analysis_history.py` (lines 620+) | API test — tests `api.v1.endpoints.history` |
| `tests/test_analysis_api_contract.py` | API test — uses `api.app.create_app` |
| `tests/test_portfolio_pr2.py` | API test — uses `api.app.create_app` |
| `tests/test_portfolio_api.py` | API test — uses `api.app.create_app` |
| `tests/test_autocomplete_pr0.py` | API test — imports `api.v1.schemas.analysis` |
| `tests/test_auth_status_setup_state.py` | API test — imports `api.v1.endpoints.auth` |
| `tests/test_auth_api.py` | API test — imports `api.middlewares.auth` |
| `tests/test_api_app_cors.py` | API test — uses `api.app.create_app` |
| `tests/test_agent_models_api.py` | API test — imports `api.v1.endpoints.agent` |

### What to Modify

| Path | Change |
|------|--------|
| `main.py` | Remove `--serve` and `--serve-only` flags |
| `.env.example` | Remove WEBUI-only / AUTH_ (API-related) variables |
| `.github/workflows/ci.yml` | Remove `web-gate` and `docker-build` jobs |
| `README.md` | Simplify to CLI-only usage |
| `README_EN.md` | Sync English translation |
| All docs/ referencing web/desktop/Docker | Update or remove |
| `scripts/build-*.sh` | Remove if Docker-only |

### What to Keep

| Path | Reason |
|------|--------|
| `main.py` | CLI entry point |
| `src/` | Core analysis, agents, reports, notifications |
| `data_provider/` | Data source abstraction |
| `bot/` | Bot integrations (Telegram, Feishu, Discord, etc.) |
| `scripts/` | Utility scripts (non-Docker) |
| `strategies/` | Trading strategies |
| `sources/` | Data sources |
| `tests/` | Non-API tests |
| `.github/workflows/ci.yml` | Core CI (backend only) |
| `.github/workflows/daily_analysis.yml` | Daily scheduled analysis |
| `.github/workflows/auto-tag.yml` | Auto versioning |
| `.github/workflows/create-release.yml` | GitHub release |
| `.github/workflows/pr-review.yml` | PR review |
| `.github/workflows/network-smoke.yml` | Network smoke test |
| `.github/workflows/stale.yml` | Stale issue/PR management |

## Verification Strategy

1. After deletion, run `python -m py_compile` on all remaining Python files to catch broken imports
2. Run `pytest -m "not network"` to ensure non-API tests pass
3. Run `bash scripts/ci_gate.sh` if still relevant
4. Verify `python main.py --help` works without errors

## Rollback

- Git checkout before the refactor merge — single commit or PR
- No destructive changes to core logic, so rollback is straightforward
