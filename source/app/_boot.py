"""Boot-time helpers shared by the CLI/GUI entry (main.py) and main_window.

Kept larch-free and PySide6-free so main.py can set up the Qt plugin
environment and show a splash BEFORE importing the heavy main_window module.
"""

from __future__ import annotations

import sys


def ensure_qt_plugin_env() -> None:
    """Conda / official xraylarch installs Qt plugins under Library/lib/qt6/plugins.

    Launching bare pythonw without ``conda activate`` (or Larix's envrunner)
    leaves QT_PLUGIN_PATH empty → QApplication aborts with 0xC06D007F on Windows.
    Safe to call even when already activated.
    """
    import os
    from pathlib import Path as _P

    prefix = _P(sys.prefix)
    # Also honour CONDA_PREFIX when sys.prefix is a nested env
    conda_prefix = os.environ.get("CONDA_PREFIX")
    roots = [prefix]
    if conda_prefix:
        roots.insert(0, _P(conda_prefix))

    plugin_roots = []
    for root in roots:
        for rel in (
            ("Library", "lib", "qt6", "plugins"),
            ("Library", "plugins"),
            ("Lib", "site-packages", "PySide6", "plugins"),
        ):
            cand = root.joinpath(*rel)
            if cand.is_dir():
                plugin_roots.append(cand)

    if not plugin_roots:
        return

    # Prefer the first existing tree; prepend if user already set a path.
    primary = str(plugin_roots[0])
    existing = os.environ.get("QT_PLUGIN_PATH", "").strip()
    if not existing:
        os.environ["QT_PLUGIN_PATH"] = primary
    elif primary not in existing.split(os.pathsep):
        os.environ["QT_PLUGIN_PATH"] = primary + os.pathsep + existing

    plat = plugin_roots[0] / "platforms"
    if plat.is_dir() and not os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH"):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plat)

    # Ensure Qt DLLs from this env are found before any other Qt on PATH.
    libbin = prefix / "Library" / "bin"
    if libbin.is_dir():
        bin_s = str(libbin)
        path = os.environ.get("PATH", "")
        if bin_s.lower() not in path.lower().split(";"):
            os.environ["PATH"] = bin_s + os.pathsep + path
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(bin_s)
        except Exception:
            pass
