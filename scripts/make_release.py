"""Build Windows + macOS release packages for smartLarch Platform.

Does NOT embed the full xraylarch/Python stack (users install official xraylarch
or use first-run runtime later). Packages include:
  - source/ app
  - plugins/ (active web companions)
  - launchers (Win bat/ps1, Mac .command)
  - docs + README
  - latest.json for auto-update scanners

Usage (from project root or any cwd):
  python scripts/make_release.py
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts/ → project root
sys.path.insert(0, str(ROOT / "source"))
from app.version import APP_VERSION, APP_NAME  # noqa: E402

# User-facing zips live at **project root** (workspace convention).
# Staging only (deleted after zip). Plugin outbox: updates/plugins/
STAGE_ROOT = ROOT / "updates" / "_staging"
UPDATES_PLATFORM = ROOT / "updates" / "platform"
UPDATES_PLUGINS = ROOT / "updates" / "plugins"
RELEASE_DATA_FILES = (
    "demo_PtLa_001.xdi",
    "Fit92_ModelA_report.txt",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_tree(src: Path, dst: Path, ignore_names: set[str]) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    def _ignore(directory, names):
        return [n for n in names if n in ignore_names or n.endswith(".pyc")]
    shutil.copytree(src, dst, ignore=_ignore)


def _write_zip_file(zf: zipfile.ZipFile, path: Path, arcname: Path) -> None:
    """Write deterministic Unix permissions even when packaging on Windows."""
    stat = path.stat()
    info = zipfile.ZipInfo(str(arcname).replace("\\", "/"), datetime.fromtimestamp(stat.st_mtime).timetuple()[:6])
    info.create_system = 3
    mode = 0o755 if path.suffix.lower() in {".sh", ".command"} else 0o644
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, path.read_bytes())


def _write_mac_command(dest_dir: Path) -> None:
    content = """#!/bin/bash
# Launch smartLarch Platform on macOS (requires xraylarch / conda env with larch+PySide6).
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/source${PYTHONPATH:+:$PYTHONPATH}"

pick_python() {
  for c in \\
    "$HOME/xraylarch/bin/python" \\
    "/Applications/xraylarch.app/Contents/MacOS/python" \\
    "$HOME/miniconda3/envs/larch/bin/python" \\
    "$HOME/anaconda3/envs/larch/bin/python" \\
    "$HOME/mambaforge/envs/larch/bin/python" \\
    "python3"
  do
    if [ -x "$c" ] 2>/dev/null || command -v "$c" >/dev/null 2>&1; then
      if "$c" -c "import larch, PySide6" 2>/dev/null; then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}

PY="$(pick_python || true)"
if [ -z "$PY" ]; then
  osascript -e 'display dialog "Cannot find Python with xraylarch + PySide6.\\nInstall from https://xraylarch.github.io/ or conda create -n larch -c conda-forge xraylarch pyside6 matplotlib" buttons {"OK"} default button 1 with icon stop'
  exit 1
fi
exec "$PY" -m app.main "$@"
"""
    path = dest_dir / "Launch-smartLarch-Platform.command"
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _write_readme(dest_dir: Path, os_name: str) -> None:
    text = f"""# {APP_NAME} v{APP_VERSION} ({os_name})

Copyright © 2026 by Dr. GENG Xun. Directly copy is prohibited.

## First run (standalone)

You do **not** need to pre-install xraylarch.

### Windows
1. **Extract the zip FIRST — the whole folder.** Do NOT double-click the
   .bat from inside the zip preview: Windows then extracts only that one
   file to a temp dir, the companion `.ps1` is missing, and the terminal
   just flashes and closes.
2. Double-click `Launch-smartLarch-Platform.bat` in the extracted folder.
3. If no environment is found, choose **Yes** to install a private runtime
   (Miniconda + xraylarch + PySide6 into `%LOCALAPPDATA%\\smartLarch\\…`).
4. Needs **internet** the first time (~1–2 GB, often 10–30 minutes).
5. Later launches reuse the installed runtime (offline OK).
6. A Start Menu folder **smartLarch Platform** and a Desktop shortcut
   are created on first successful launch. If you cannot find them,
   run `Install-Shortcuts.ps1` in this folder.

No admin rights are needed. If launch fails, the console pauses and the
log is in **this same folder** as the .bat:
`smartLarch-launch.log` / `smartLarch-runtime-install.log`
(v0.35.14+). Do not look under output/.

Manual repair: run `Install-Runtime.ps1`.
If the network dropped mid-install, reconnect and run the launcher
again: it resumes (does not `conda create` over a half-finished env).

### macOS
1. Unzip.
2. `chmod +x Launch-smartLarch-Platform.command Install-Runtime.sh`
3. Double-click / run `Launch-smartLarch-Platform.command`.
4. First run may install a private runtime under
   `~/Library/Application Support/smartLarch/…` (internet required once).

## Web plugins

Folder `plugins/`:
- `xas-merged.html` — sample / crystal precheck
- `easyxanes-xafs.html` — easyXAFS XAFS-only
- `xrd-xas.html` — XRD-XAS data processing (I0/I1 + detector evaluation)
- `mex1.html` — MEX1/MEX2 treatment

In the app: **Tools → Companion tools**.  
Plugin-only updates: drop newer `*_vX.Y.Z.html` into `plugins/` (auto-replace on start).

## User guide

`docs/user-guide/USER_GUIDE.html` — double-click to read in any browser
(new-PC walkthrough, Norm/Fit/Tools pages, export layouts).

## Main-program updates

Settings → main-program update folder; put newer zip + `latest.json` there (auto on start).

## Version

`{APP_VERSION}` — `source/app/version.py`
"""
    (dest_dir / "README.txt").write_text(text, encoding="utf-8")


def build_one(os_tag: str) -> Path:
    name = f"smartLarch-Platform_{os_tag}_v{APP_VERSION}"
    stage = STAGE_ROOT / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    # Core
    _copy_tree(
        ROOT / "source",
        stage / "source",
        ignore_names={"__pycache__", ".pytest_cache", "tests"},
    )
    # Keep tests out of user package for size; optional copy:
    # shutil.copytree(ROOT / "source" / "tests", stage / "source" / "tests")

    # plugins
    plug_src = ROOT / "plugins"
    plug_dst = stage / "plugins"
    plug_dst.mkdir(parents=True)
    for name_f in (
        "xas-merged.html",
        "easyxanes-xafs.html",
        "xrd-xas.html",
        "mex1.html",
        "manifest.json",
        "README.md",
    ):
        p = plug_src / name_f
        if p.is_file():
            shutil.copy2(p, plug_dst / name_f)

    # Explicit, reviewed demo allowlist. Never copy the entire local data tree.
    data_src = ROOT / "data"
    if data_src.is_dir():
        data_dst = stage / "data"
        data_dst.mkdir(parents=True, exist_ok=True)
        for name_f in RELEASE_DATA_FILES:
            src = data_src / name_f
            if src.is_file():
                shutil.copy2(src, data_dst / name_f)
        fi = data_src / "feff_inputs"
        if fi.is_dir():
            shutil.copytree(fi, data_dst / "feff_inputs", dirs_exist_ok=True)

    # launchers + first-run runtime installers
    for f in (
        "Launch-smartLarch-Platform.bat",
        "Launch-smartLarch-Platform.ps1",
        "Launch-smartLarch-Platform.command",
        "Install-Runtime.ps1",
        "Install-Runtime.sh",
        "Install-Shortcuts.ps1",
    ):
        src = ROOT / f
        if src.is_file():
            shutil.copy2(src, stage / f)
            if f.endswith(".command") or f.endswith(".sh"):
                try:
                    (stage / f).chmod(0o755)
                except OSError:
                    pass
    _write_readme(stage, os_tag)

    # docs essentials
    for f in ("START_HERE.md", "CHANGELOG.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md", "SBOM.json"):
        src = ROOT / f
        if src.is_file():
            shutil.copy2(src, stage / f)

    # User guide (v2.16.15): the HTML + its screenshots — the images are
    # referenced relatively, so the docs/user-guide/ tree shape must survive.
    ug = ROOT / "docs" / "user-guide"
    if (ug / "USER_GUIDE.html").is_file():
        ug_dst = stage / "docs" / "user-guide"
        ug_dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ug / "USER_GUIDE.html", ug_dst / "USER_GUIDE.html")
        if (ug / "images").is_dir():
            shutil.copytree(ug / "images", ug_dst / "images", dirs_exist_ok=True)

    # package-root marker for updates
    (stage / "package-root.txt").write_text(
        f"{APP_NAME}\nversion={APP_VERSION}\nos={os_tag}\n",
        encoding="utf-8",
    )

    # zip → **project root** (workspace convention for deliverables)
    zip_path = ROOT / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in stage.rglob("*"):
            if p.is_file():
                _write_zip_file(zf, p, Path(name) / p.relative_to(stage))

    # drop staging tree (zip is the deliverable)
    shutil.rmtree(stage, ignore_errors=True)

    print(f"Built {zip_path.name} at project root ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return zip_path


def _prune_old_root_zips(keep: int = 1) -> None:
    """Keep only the latest *keep* Windows and macOS zips at project root (§B.13: 安装包只留最新一版)."""
    import re as _re

    for prefix in ("smartLarch-Platform_Windows_v", "smartLarch-Platform_macOS_v"):
        files = []
        for p in ROOT.glob(f"{prefix}*.zip"):
            m = _re.search(r"v(\d+)\.(\d+)\.(\d+)", p.name)
            if m:
                files.append((tuple(int(x) for x in m.groups()), p))
        files.sort(key=lambda x: x[0], reverse=True)
        for _, old in files[keep:]:
            old.unlink(missing_ok=True)
            print(f"Removed older zip: {old.name}")


def write_latest(win_zip: Path, mac_zip: Path) -> None:
    """Write latest.json next to root zips (update folder can be project root)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    UPDATES_PLATFORM.mkdir(parents=True, exist_ok=True)
    for os_tag, zpath in (("windows", win_zip), ("macos", mac_zip)):
        meta = {
            "name": APP_NAME,
            "version": APP_VERSION,
            "os": os_tag,
            "path": zpath.name,  # relative to project root / update folder
            "sha256": _sha256(zpath),
            "generated_at": now,
            "notes": "Zip is at project root. Plugins update separately under plugins/.",
        }
        text = json.dumps(meta, indent=2) + "\n"
        (ROOT / f"latest_{os_tag}.json").write_text(text, encoding="utf-8")
        (UPDATES_PLATFORM / f"latest_{os_tag}.json").write_text(text, encoding="utf-8")
        print(f"Wrote latest_{os_tag}.json (root + updates/platform)")
    generic = {
        "name": APP_NAME,
        "version": APP_VERSION,
        "packages": {
            "windows": {
                "os": "windows",
                "version": APP_VERSION,
                "path": win_zip.name,
                "sha256": _sha256(win_zip),
            },
            "macos": {
                "os": "macos",
                "version": APP_VERSION,
                "path": mac_zip.name,
                "sha256": _sha256(mac_zip),
            },
        },
        "generated_at": now,
    }
    gtext = json.dumps(generic, indent=2) + "\n"
    (ROOT / "latest.json").write_text(gtext, encoding="utf-8")
    (UPDATES_PLATFORM / "latest.json").write_text(gtext, encoding="utf-8")
    print("Wrote latest.json (root + updates/platform)")


def main() -> int:
    # Ensure plugins synced
    sync = ROOT / "plugins" / "sync_plugins.py"
    if sync.is_file():
        import subprocess

        subprocess.check_call([sys.executable, str(sync)])
    win = build_one("Windows")
    mac = build_one("macOS")
    write_latest(win, mac)
    _prune_old_root_zips(keep=1)
    print(
        "\nDone. Deliverables at project root:\n"
        f"  {win.name}\n  {mac.name}\n"
        "Update folder can be the project root (scans latest.json + zips)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
