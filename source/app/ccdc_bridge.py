"""Bridge to the CSD (Cambridge Structural Database) — Qt-free (v4.10.0).

The ccdc package must not enter the smartLarch process: it is version-locked to
the CCDC release, ships its own local data and can clash with PySide6 / numpy.
So we start the CSD's own python.exe on ccdc_helper.py and read one JSON line.

Nothing here stores a CCDC account or password, and nothing logs in or scrapes
a website: only the path of a Python that has the CSD Python API is remembered.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HELPER = Path(__file__).resolve().parent / "ccdc_helper.py"

_VERIFY_TIMEOUT = 60
_SEARCH_TIMEOUT = 120
_EXPORT_TIMEOUT = 60

_STRIP_ENV = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP")
_STRIP_PREFIX = ("QT_", "CONDA_")
_PATH_BLOCK = ("xraylarch", "smartlarch", "miniconda", "anaconda")


def _candidate_roots() -> List[Path]:
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "CCDC",
        Path(os.environ.get("LOCALAPPDATA", "")) / "CCDC",
        Path.home() / "CCDC",  # CSD Portfolio 2025+ per-user default
    ]
    return [r for r in roots if str(r) and r.is_dir()]


def _find_pythons_under(root: Path, depth: int = 4) -> List[str]:
    """python.exe below *root*, at most *depth* directory levels deep."""
    out: List[str] = []
    root = Path(root)
    base_depth = len(root.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if len(here.parts) - base_depth >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.lower() == "python.exe":
                    out.append(str(here / name))
    except OSError:
        pass
    return out


def _conda_pythons_with_ccdc() -> List[str]:
    """Conda environments that actually have the ccdc package installed."""
    out: List[str] = []
    listfile = Path.home() / ".conda" / "environments.txt"
    if not listfile.is_file():
        return out
    try:
        lines = listfile.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        env = Path(line.strip())
        if not str(env) or not env.is_dir():
            continue
        site = env / "Lib" / "site-packages" / "ccdc"
        py = env / "python.exe"
        if site.is_dir() and py.is_file():
            out.append(str(py))
    return out


def _has_ccdc(python: str) -> bool:
    return (Path(python).parent / "Lib" / "site-packages" / "ccdc").is_dir()


def find_ccdc_pythons() -> List[str]:
    """Every plausible CSD Python, de-duplicated, existing files only.

    Pythons that actually carry the ccdc package come first (a CSD install may
    also hold conda pkgs copies of python.exe without it).
    """
    found: List[str] = []
    for root in _candidate_roots():
        found.extend(_find_pythons_under(root))
    found.extend(_conda_pythons_with_ccdc())
    out: List[str] = []
    seen = set()
    for item in found:
        try:
            path = Path(item)
            if not path.is_file():
                continue
            key = str(path.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(str(path))
    out.sort(key=lambda p: not _has_ccdc(p))  # stable: discovery order otherwise
    return out


def clean_env(python: str) -> Dict[str, str]:
    """A small environment for the CSD Python: no Qt, no conda, no larch."""
    env: Dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _STRIP_ENV or any(upper.startswith(p) for p in _STRIP_PREFIX):
            continue
        env[key] = value
    py = Path(python)
    parts = [str(py.parent), str(py.parent / "Library" / "bin"), str(py.parent / "Scripts")]
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    parts.extend([str(Path(system_root) / "System32"), system_root])
    for item in str(os.environ.get("PATH", "")).split(os.pathsep):
        if not item:
            continue
        low = item.lower()
        if any(block in low for block in _PATH_BLOCK):
            continue
        if item in parts:
            continue
        parts.append(item)
    env["PATH"] = os.pathsep.join(parts)
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:  # pragma: no cover
            proc.kill()
    except Exception:
        pass
    try:
        if proc.poll() is None:  # taskkill missing or refused
            proc.kill()
    except Exception:
        pass
    try:
        proc.communicate(timeout=5)  # drain and close the pipes
    except Exception:
        pass


def _last_json(text: str) -> Optional[dict]:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def run_helper(
    python: str,
    cmd: str,
    payload: Optional[dict] = None,
    *,
    timeout: float = _VERIFY_TIMEOUT,
    extra_path: Optional[str] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """Start ccdc_helper.py with the CSD Python and return its JSON answer.

    *cancel* is polled about four times a second; True ends the process tree
    and returns kind "cancelled".
    """
    if not python or not Path(python).is_file():
        # "notfound" = the CSD Python itself is missing (i18n ccdc_fail_notfound)
        return {"ok": False, "kind": "notfound", "message": "CSD Python not found"}
    args = [str(python), "-I", str(HELPER), str(cmd), json.dumps(payload or {})]
    if extra_path:
        args.extend(["--extra-path", str(extra_path)])
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_env(python),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return {"ok": False, "kind": "other", "message": f"could not start: {exc}"}
    deadline = time.monotonic() + float(timeout)
    while True:
        remaining = deadline - time.monotonic()
        step = min(0.25, remaining) if cancel is not None else remaining
        try:
            # retrying communicate after TimeoutExpired loses no output
            out, err = proc.communicate(timeout=max(0.0, step))
            break
        except subprocess.TimeoutExpired:
            if cancel is not None:
                try:
                    stop = bool(cancel())
                except Exception:
                    stop = False
                if stop:
                    _kill_tree(proc)
                    return {"ok": False, "kind": "cancelled", "message": "cancelled"}
            if time.monotonic() >= deadline:
                _kill_tree(proc)
                return {
                    "ok": False,
                    "kind": "other",
                    "message": f"timed out after {timeout:g} s",
                }
    text = (out or b"").decode("utf-8", errors="replace")
    errtext = (err or b"").decode("utf-8", errors="replace")
    data = _last_json(text)
    if data is None:
        tail = "\n".join(errtext.splitlines()[-12:])
        return {
            "ok": False,
            "kind": "other",
            "message": ("no JSON from the CSD helper" + (f"; stderr: {tail}" if tail else ""))[:800],
        }
    return data


def verify(python: str, *, extra_path: Optional[str] = None, cancel=None) -> dict:
    return run_helper(
        python, "verify", {}, timeout=_VERIFY_TIMEOUT, extra_path=extra_path, cancel=cancel
    )


def search(python: str, **query) -> dict:
    payload = {
        "text": str(query.get("text", "")),
        "field": str(query.get("field", "name")),
        "must_have": list(query.get("must_have") or []),
        "no_disorder": bool(query.get("no_disorder", True)),
        "max_r": float(query.get("max_r", 10) or 10),
        "max_hits": int(query.get("max_hits", 200) or 200),
    }
    return run_helper(
        python,
        "search",
        payload,
        timeout=_SEARCH_TIMEOUT,
        extra_path=query.get("extra_path"),
        cancel=query.get("cancel"),
    )


def export_cif(
    python: str, refcode: str, out: str | Path, *, extra_path: Optional[str] = None, cancel=None
) -> dict:
    payload = {"refcode": str(refcode), "out": str(out)}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    return run_helper(
        python, "export", payload, timeout=_EXPORT_TIMEOUT, extra_path=extra_path, cancel=cancel
    )
