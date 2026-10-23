#!/bin/bash
# Launch smartLarch Platform on macOS.
# Prefer managed runtime; else well-known envs; else first-run Install-Runtime.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/source${PYTHONPATH:+:$PYTHONPATH}"
LOGDIR="$ROOT/output"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/launch_log.txt"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

MANAGED="${HOME}/Library/Application Support/smartLarch"
MANAGED_PY="$MANAGED/miniconda3/envs/smartlarch/bin/python"

candidates=(
  "$MANAGED_PY"
  "$HOME/xraylarch/bin/python"
  "$HOME/miniconda3/envs/larch/bin/python"
  "$HOME/anaconda3/envs/larch/bin/python"
  "$HOME/mambaforge/envs/larch/bin/python"
  "$HOME/miniforge3/envs/larch/bin/python"
)

pick() {
  for c in "${candidates[@]}"; do
    if [ -x "$c" ] 2>/dev/null; then
      if "$c" -c "import larch, PySide6" 2>/dev/null; then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}

PY="$(pick || true)"
if [ -z "${PY:-}" ]; then
  log "No runtime; offering Install-Runtime.sh"
  if [ -f "$ROOT/Install-Runtime.sh" ]; then
    chmod +x "$ROOT/Install-Runtime.sh" || true
    bash "$ROOT/Install-Runtime.sh" || {
      log "Install-Runtime failed"
      exit 1
    }
    PY="$(pick || true)"
  fi
fi

if [ -z "${PY:-}" ]; then
  if command -v osascript >/dev/null 2>&1; then
    osascript -e 'display dialog "Cannot find or install xraylarch + PySide6.\nSee output/launch_log.txt and runtime_install.log" buttons {"OK"} with icon stop'
  else
    echo "Cannot find Python with larch+PySide6" >&2
  fi
  exit 1
fi

# Qt plugins for conda-like env
ENV_ROOT="$(dirname "$(dirname "$PY")")"
if [ -d "$ENV_ROOT/lib" ]; then
  for q in \
    "$ENV_ROOT/lib/qt6/plugins" \
    "$ENV_ROOT/plugins" \
    "$ENV_ROOT/lib/python3.12/site-packages/PySide6/Qt/plugins"
  do
    if [ -d "$q" ]; then
      export QT_PLUGIN_PATH="$q"
      break
    fi
  done
fi
export QT_API=pyside6
log "Using $PY"
exec "$PY" -m app.main "$@"
