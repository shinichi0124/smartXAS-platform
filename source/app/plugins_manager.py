"""Companion web plugins: directory, manifest, open, auto-update overwrite.

Active slot files (fixed names, overwritten on update):
  xas-merged.html, easyxanes-xafs.html, xrd-xas.html, mex1.html
Optional versioned siblings in the same directory or subfolder updates/:
  xas-merged_v4.28.7.html, …  (used as the update feed)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .version import APP_VERSION

VER_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)", re.I)

PLUGIN_SLOTS: Tuple[dict, ...] = (
    {
        "id": "xas-merged",
        "active": "xas-merged.html",
        "title_zh": "样品/晶体预检",
        "title_en": "Sample / Crystal Precheck",
        "prefix": "xas-merged",
    },
    {
        "id": "easyxanes-xafs",
        "active": "easyxanes-xafs.html",
        "title_zh": "LabXAS data processing",
        "title_en": "LabXAS data processing",
        "prefix": "easyxanes-xafs",
    },
    {
        "id": "xrd-xas",
        "active": "xrd-xas.html",
        "title_zh": "XRD-XAS data processing",
        "title_en": "XRD-XAS data processing",
        "prefix": "xrd-xas",
    },
    {
        "id": "mex1",
        "active": "mex1.html",
        "title_zh": "MEX data convertor",
        "title_en": "MEX data convertor",
        "prefix": "mex1",
    },
)


def project_root_from_paths(paths: dict) -> Path:
    root = paths.get("root") if isinstance(paths, dict) else None
    if root:
        return Path(root)
    # source/app/plugins_manager.py → parents[2] = project root
    return Path(__file__).resolve().parents[2]


def default_plugins_dir(paths: dict) -> Path:
    return project_root_from_paths(paths) / "plugins"


def version_tuple(s: str) -> Tuple[int, int, int]:
    m = re.search(r"(?:^|[^0-9])v?(\d+)\.(\d+)\.(\d+)(?:$|[^0-9])", str(s or ""), re.I)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def version_str_from_name(name: str) -> str:
    m = VER_RE.search(name)
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}" if m else "0.0.0"


def _read_manifest(dir_path: Path) -> dict:
    mf = dir_path / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _write_manifest(dir_path: Path, plugins_meta: list) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "smartlarch-plugins/v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "plugins_root_note": (
            "Default plugin directory; active files use fixed names and are "
            "overwritten on auto-update."
        ),
        "plugins": plugins_meta,
        "platform_app_version": APP_VERSION,
    }
    (dir_path / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def active_plugin_path(plugins_dir: Path, slot_id: str) -> Optional[Path]:
    for slot in PLUGIN_SLOTS:
        if slot["id"] == slot_id:
            p = plugins_dir / slot["active"]
            return p if p.is_file() else None
    return None


def list_slot_status(plugins_dir: Path) -> List[dict]:
    """Return per-slot status: active version + newest candidate version."""
    plugins_dir = Path(plugins_dir)
    man = _read_manifest(plugins_dir)
    man_by_id = {
        str(p.get("id")): p
        for p in (man.get("plugins") or [])
        if isinstance(p, dict) and p.get("id")
    }
    out = []
    for slot in PLUGIN_SLOTS:
        active = plugins_dir / slot["active"]
        active_ver = "0.0.0"
        if active.is_file():
            # Prefer manifest version for this id; else try HTML APP_VERSION / footer
            meta = man_by_id.get(slot["id"]) or {}
            active_ver = str(meta.get("version") or "") or _version_from_html(active)
        cand = find_newer_candidate(plugins_dir, slot["prefix"], active_ver)
        out.append(
            {
                "id": slot["id"],
                "title_zh": slot["title_zh"],
                "title_en": slot["title_en"],
                "active_file": slot["active"],
                "active_path": str(active) if active.is_file() else "",
                "active_version": active_ver if active.is_file() else "",
                "present": active.is_file(),
                "update": cand,  # None or {version, path}
            }
        )
    return out


def _version_from_html(path: Path) -> str:
    try:
        # Only scan head of file for speed
        head = path.read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError:
        return "0.0.0"
    m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', head)
    if m:
        return m.group(1).strip()
    m = VER_RE.search(path.name)
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}" if m else "0.0.0"


def find_newer_candidate(
    plugins_dir: Path, prefix: str, current_ver: str
) -> Optional[dict]:
    """Look for prefix_vX.Y.Z.html (or in updates/) newer than current_ver."""
    plugins_dir = Path(plugins_dir)
    current = version_tuple(current_ver)
    best: Optional[dict] = None
    search_dirs = [plugins_dir, plugins_dir / "updates"]
    for d in search_dirs:
        if not d.is_dir():
            continue
        manifest = _read_manifest(d)
        entries = [item for item in (manifest.get("plugins") or []) if isinstance(item, dict)]
        for item in entries:
            if str(item.get("id") or "") != prefix:
                continue
            filename = str(item.get("update_file") or item.get("file") or "").strip()
            digest = str(item.get("update_sha256") or item.get("sha256") or "").strip().lower()
            p = (d / filename).resolve()
            try:
                p.relative_to(d.resolve())
            except ValueError:
                continue
            if not re.fullmatch(rf"{re.escape(prefix)}_v\d+\.\d+\.\d+\.html", p.name, re.I):
                continue
            if not p.is_file() or not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            if not hmac.compare_digest(_sha256(p), digest):
                continue
            ver = version_str_from_name(p.name)
            vt = version_tuple(ver)
            if vt <= current:
                continue
            if best is None or vt > version_tuple(best["version"]):
                best = {"version": ver, "path": str(p), "sha256": digest}
    return best


def auto_update_plugins(plugins_dir: Path) -> List[dict]:
    """Overwrite active slots with newer versioned files. Returns list of applied updates."""
    plugins_dir = Path(plugins_dir)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    applied: List[dict] = []
    statuses = list_slot_status(plugins_dir)
    meta_for_manifest = []
    for st in statuses:
        slot = next(s for s in PLUGIN_SLOTS if s["id"] == st["id"])
        active = plugins_dir / slot["active"]
        upd = st.get("update")
        if upd and Path(upd["path"]).is_file():
            src = Path(upd["path"])
            if not hmac.compare_digest(_sha256(src), str(upd.get("sha256") or "")):
                upd = None
        if upd and Path(upd["path"]).is_file():
            src = Path(upd["path"])
            # Keep one bak only
            if active.is_file():
                bak = active.with_suffix(active.suffix + ".bak")
                try:
                    shutil.copy2(active, bak)
                except OSError:
                    pass
            shutil.copy2(src, active)
            applied.append(
                {
                    "id": slot["id"],
                    "version": upd["version"],
                    "from": str(src),
                    "to": str(active),
                }
            )
            ver = upd["version"]
        else:
            ver = st.get("active_version") or (
                _version_from_html(active) if active.is_file() else ""
            )
        if active.is_file():
            meta_for_manifest.append(
                {
                    "id": slot["id"],
                    "title_zh": slot["title_zh"],
                    "title_en": slot["title_en"],
                    "version": ver or "0.0.0",
                    "file": slot["active"],
                    "sha256": _sha256(active),
                    "updated_at": datetime.now().astimezone().isoformat(),
                }
            )
    if meta_for_manifest:
        _write_manifest(plugins_dir, meta_for_manifest)
    return applied


def ensure_bundled_plugins_seed(paths: dict, plugins_dir: Path) -> None:
    """If active files missing, copy from project plugins/ next to source tree."""
    plugins_dir = Path(plugins_dir)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    bundled = default_plugins_dir(paths)
    if bundled.resolve() == plugins_dir.resolve():
        return
    for slot in PLUGIN_SLOTS:
        dest = plugins_dir / slot["active"]
        src = bundled / slot["active"]
        if not dest.is_file() and src.is_file():
            shutil.copy2(src, dest)
    man_src = bundled / "manifest.json"
    man_dst = plugins_dir / "manifest.json"
    if man_src.is_file() and not man_dst.is_file():
        shutil.copy2(man_src, man_dst)
