#!/usr/bin/env bash

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

syntax_check() {
  echo "==> backend-gate: Python syntax check"
  "$PYTHON_BIN" -m py_compile \
    main.py \
    src/config/__init__.py src/config/manager.py src/config/models.py src/config/utils.py \
    src/auth.py \
    src/analyzer/__init__.py src/analyzer/core.py src/analyzer/prompt_builder.py src/analyzer/utils.py \
    src/notification/__init__.py src/notification/service.py src/notification/renderer.py src/notification/utils.py
  "$PYTHON_BIN" -m py_compile src/storage.py src/scheduler.py src/search_service.py
  "$PYTHON_BIN" -m py_compile src/market_analyzer.py src/stock_analyzer.py
  "$PYTHON_BIN" -m py_compile data_provider/*.py
}

flake8_checks() {
  echo "==> backend-gate: flake8 critical checks"
  flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics --exclude=.claude,.worktrees,.git,__pycache__
}

deterministic_checks() {
  echo "==> backend-gate: local deterministic checks"
  ./test.sh code
  ./test.sh yfinance

  echo "==> backend-gate: config.yaml local-path check"
  if grep -q '/Users/' config.yaml 2>/dev/null; then
    echo "WARNING: config.yaml contains absolute paths (might be local-only):"
    grep -n '/Users/' config.yaml
  fi
}

config_contract_check() {
  echo "==> backend-gate: config contract check"
  "$PYTHON_BIN" scripts/check_config_contract.py --strict
}

optional_dep_checks() {
  echo "==> backend-gate: optional-dependency import safety"

  # openbb_fetcher — openbb is optional
  "$PYTHON_BIN" -c "
try:
    import openbb
    print('  openbb: available')
except ImportError:
    print('  openbb: not installed (expected if not using OpenBB)')
"

  # finnhub — optional news provider
  "$PYTHON_BIN" -c "
try:
    import finnhub
    print('  finnhub-python: available')
except ImportError:
    print('  finnhub-python: not installed (expected if not using Finnhub)')
"
}

offline_test_suite() {
  echo "==> backend-gate: offline test suite"
  "$PYTHON_BIN" -m pytest -m "not network"
}

run_all() {
  syntax_check
  flake8_checks
  deterministic_checks
  config_contract_check
  optional_dep_checks
  offline_test_suite
  echo "==> backend-gate: all checks passed"
}

phase="${1:-all}"

case "$phase" in
  all)
    run_all
    ;;
  syntax)
    syntax_check
    ;;
  flake8)
    flake8_checks
    ;;
  deterministic)
    deterministic_checks
    ;;
  config-contract)
    config_contract_check
    ;;
  optional-dep)
    optional_dep_checks
    ;;
  offline-tests)
    offline_test_suite
    ;;
  *)
    echo "Usage: $0 [all|syntax|flake8|deterministic|config-contract|optional-dep|offline-tests]" >&2
    exit 2
    ;;
esac
