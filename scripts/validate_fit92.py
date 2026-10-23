#!/usr/bin/env python
"""CLI helper: validate Model A against Artemis Fit 92."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts/ → project root
sys.path.insert(0, str(ROOT / "source"))

from app.fit_engine import run_cli_validate  # noqa: E402

if __name__ == "__main__":
    data = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(run_cli_validate(data))
