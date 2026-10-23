"""Locate managed smartLarch runtime and well-known xraylarch installs."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional


def managed_root() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "smartLarch"
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "smartLarch"
    return base


def managed_python_candidates() -> List[Path]:
    root = managed_root()
    if sys.platform == "darwin":
        return [
            root / "miniconda3" / "envs" / "smartlarch" / "bin" / "python",
            root / "Miniconda3" / "envs" / "smartlarch" / "bin" / "python",
        ]
    return [
        root / "Miniconda3" / "envs" / "smartlarch" / "pythonw.exe",
        root / "Miniconda3" / "envs" / "smartlarch" / "python.exe",
    ]


def read_runtime_marker() -> Optional[dict]:
    marker = managed_root() / "runtime.json"
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def well_known_python_candidates() -> List[Path]:
    """Paths launchers also try (in addition to managed runtime)."""
    home = Path.home()
    out: List[Path] = []
    out.extend(managed_python_candidates())
    if sys.platform.startswith("win"):
        out.extend(
            [
                Path(r"C:\ProgramData\xraylarch\pythonw.exe"),
                Path(r"C:\ProgramData\xraylarch\python.exe"),
                home / "xraylarch" / "pythonw.exe",
                home / "xraylarch" / "python.exe",
                home / "AppData" / "Local" / "xraylarch" / "pythonw.exe",
                home / "AppData" / "Local" / "xraylarch" / "python.exe",
                home / ".conda" / "envs" / "larch" / "pythonw.exe",
                home / ".conda" / "envs" / "larch" / "python.exe",
                home / "anaconda3" / "envs" / "larch" / "pythonw.exe",
                home / "miniconda3" / "envs" / "larch" / "pythonw.exe",
            ]
        )
    else:
        out.extend(
            [
                home / "xraylarch" / "bin" / "python",
                home / "miniconda3" / "envs" / "larch" / "bin" / "python",
                home / "anaconda3" / "envs" / "larch" / "bin" / "python",
                home / "mambaforge" / "envs" / "larch" / "bin" / "python",
            ]
        )
    # de-dupe preserve order
    seen = set()
    uniq: List[Path] = []
    for p in out:
        s = str(p)
        if s not in seen:
            seen.add(s)
            uniq.append(p)
    return uniq
