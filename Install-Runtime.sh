#!/usr/bin/env bash
# Install private Miniconda + conda-forge env (xraylarch + PySide6) for smartLarch Platform.
# Target: ~/Library/Application Support/smartLarch/miniconda3/envs/smartlarch
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# Log sits next to this script (same folder as the extracted zip).
LOG="$ROOT/smartLarch-runtime-install.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

MANAGED="${HOME}/Library/Application Support/smartLarch"
MINICONDA="$MANAGED/miniconda3"
ENV_NAME="smartlarch"
ENV_PREFIX="$MINICONDA/envs/$ENV_NAME"
CONDA="$MINICONDA/bin/conda"
PY="$ENV_PREFIX/bin/python"
MARKER="$MANAGED/runtime.json"
URL="https://repo.anaconda.com/miniconda/Miniconda3-py312_25.7.0-2-MacOSX-x86_64.sh"
MINICONDA_SHA256="e8f6aed58d708cc544ba6bacbebad86787cb8df56667ff4729ad2fe36af32846"
# Apple Silicon uses the matching immutable arm64 artifact.
if [ "$(uname -m)" = "arm64" ]; then
  URL="https://repo.anaconda.com/miniconda/Miniconda3-py312_25.7.0-2-MacOSX-arm64.sh"
  MINICONDA_SHA256="8d67e7824088d7aa3bde938a4fc4365bb39ba1f710104cfe7bd9cfb9a99bd8d2"
fi
INSTALLER="${TMPDIR:-/tmp}/Miniconda3-smartLarch-setup.sh"

log "=== smartLarch runtime install (macOS) ==="
log "ENV_PREFIX=$ENV_PREFIX"

healthy() {
  [ -x "$1" ] || return 1
  if [ "${2:-}" = "pymcr" ]; then
    "$1" -c "import larch, PySide6, pymcr; print('OK')" 2>/dev/null | grep -q OK
  elif [ "${2:-}" = "openpyxl" ]; then
    "$1" -c "import larch, PySide6, openpyxl; print('OK')" 2>/dev/null | grep -q OK
  else
    "$1" -c "import larch, PySide6; print('OK')" 2>/dev/null | grep -q OK
  fi
}

install_pymcr() {
  log "Ensuring pymcr (MCR-ALS) via pip"
  "$1" -m pip install "pymcr==0.5.1" >>"$LOG" 2>&1 || true
  healthy "$1" pymcr
}

install_openpyxl() {
  log "Ensuring openpyxl (Excel export) via pip"
  "$1" -m pip install openpyxl >>"$LOG" 2>&1 || true
  healthy "$1" openpyxl
}

if healthy "$PY"; then
  if ! healthy "$PY" pymcr; then
    log "Core runtime OK but pymcr missing — installing for MCR-ALS"
    install_pymcr "$PY" || log "WARN: pymcr install failed"
  fi
  if ! healthy "$PY" openpyxl; then
    log "Core runtime OK but openpyxl missing — installing for Excel export"
    install_openpyxl "$PY" || log "WARN: openpyxl install failed"
  fi
  log "Already healthy: $PY"
  python3 - <<PY
import json
from pathlib import Path
Path(r"""$MARKER""").parent.mkdir(parents=True, exist_ok=True)
Path(r"""$MARKER""").write_text(json.dumps({
  "schema": "smartlarch-runtime/v1",
  "python": r"""$PY""",
  "env_prefix": r"""$ENV_PREFIX""",
  "miniconda": r"""$MINICONDA""",
  "source": "existing",
  "packages": ["xraylarch", "pyside6", "matplotlib", "numpy", "scipy", "pymcr", "openpyxl"],
}, indent=2), encoding="utf-8")
print("marker ok")
PY
  exit 0
fi

if [ "${1:-}" != "-y" ] && [ "${1:-}" != "--yes" ]; then
  if command -v osascript >/dev/null 2>&1; then
    if ! osascript -e 'display dialog "Install private xraylarch runtime (~1-2 GB, needs internet, 10-30 min) into Application Support/smartLarch?" buttons {"Cancel","Install"} default button "Install"' >/dev/null 2>&1; then
      log "User cancelled"
      exit 2
    fi
  else
    read -r -p "Install runtime to $ENV_PREFIX ? [y/N] " a
    case "$a" in y|Y|yes|YES) ;; *) exit 2 ;; esac
  fi
fi

mkdir -p "$MANAGED"
if [ ! -x "$CONDA" ]; then
  log "Downloading $URL"
  curl --fail --location --output "$INSTALLER" "$URL"
  printf '%s  %s\n' "$MINICONDA_SHA256" "$INSTALLER" | shasum -a 256 -c -
  log "Installing Miniconda silently"
  bash "$INSTALLER" -b -p "$MINICONDA"
fi

log "conda create env $ENV_NAME"
"$CONDA" create -y -n "$ENV_NAME" -c conda-forge \
  python=3.12 xraylarch=2026.2.2 pyside6=6.11.1 matplotlib=3.10.9 numpy=2.4.6 scipy=1.17.1 openpyxl

if ! healthy "$PY"; then
  log "FAILED health check"
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display dialog \"Runtime install failed. See $LOG\" buttons {\"OK\"} with icon stop"
  fi
  exit 1
fi

# MCR-ALS dependency (Analysis after PCA)
install_pymcr "$PY" || log "WARN: pymcr install failed; MCR-ALS may be unavailable"

# Excel .xlsx export (Tools -> Export) — conda list covers fresh installs;
# this pip ensure covers envs created before openpyxl was added.
if ! healthy "$PY" openpyxl; then
  install_openpyxl "$PY" || log "WARN: openpyxl install failed; Excel export may be unavailable"
fi

python3 - <<PY
import json
from pathlib import Path
from datetime import datetime, timezone
Path(r"""$MARKER""").write_text(json.dumps({
  "schema": "smartlarch-runtime/v1",
  "python": r"""$PY""",
  "env_prefix": r"""$ENV_PREFIX""",
  "miniconda": r"""$MINICONDA""",
  "installed_at": datetime.now(timezone.utc).isoformat(),
  "source": "miniconda+conda-forge",
  "packages": ["python=3.12", "xraylarch", "pyside6", "matplotlib", "numpy", "scipy", "pymcr", "openpyxl"],
}, indent=2), encoding="utf-8")
print("SUCCESS")
PY
log "SUCCESS $PY"
exit 0
