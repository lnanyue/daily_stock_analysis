#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() {
  echo "$1"
}

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    PYTHON_BIN="python"
  fi
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python not found. Please install Python 3.10+ and retry."
  exit 1
fi

log "Building backend executable (CLI Pro)..."
if ! "${PYTHON_BIN}" -m PyInstaller --version >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m pip install pyinstaller
fi

log "Installing backend dependencies..."
"${PYTHON_BIN}" -m pip install -r "${ROOT_DIR}/requirements.txt"

if [[ -d "${ROOT_DIR}/dist/stock_analysis" ]]; then
  rm -rf "${ROOT_DIR}/dist/stock_analysis"
fi

if [[ -d "${ROOT_DIR}/build/stock_analysis" ]]; then
  rm -rf "${ROOT_DIR}/build/stock_analysis"
fi

# Hidden imports for core modules now modularized
hidden_imports=(
  "json_repair"
  "tiktoken"
  "tiktoken_ext"
  "tiktoken_ext.openai_public"
  "src.config"
  "src.config.manager"
  "src.config.utils"
  "src.config.models"
  "src.analyzer"
  "src.analyzer.core"
  "src.analyzer.utils"
  "src.analyzer.prompt_builder"
  "src.notification"
  "src.notification.service"
  "src.notification.renderer"
  "src.notification.utils"
  "src.schemas"
  "src.schemas.analysis_result"
  "src.schemas.storage_models"
)

hidden_import_args=()
for module in "${hidden_imports[@]}"; do
  hidden_import_args+=("--hidden-import=${module}")
done

pushd "${ROOT_DIR}" >/dev/null
# CLI version: --console instead of --noconsole, no --add-data "static:static"
cmd=("${PYTHON_BIN}" -m PyInstaller --name stock_analysis --onedir --noconfirm --console --collect-data litellm --collect-data tiktoken)
cmd+=("${hidden_import_args[@]}" "main.py")

echo "Running: ${cmd[*]}"
"${cmd[@]}"
popd >/dev/null

log "CLI Backend build completed."
