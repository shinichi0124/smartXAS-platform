"""smartLarch Platform GUI and Fit 92 validation entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))


def _make_splash_pixmap(assets: Path, name: str, version: str, copyright_: str):
    """Render logo + centered title; version/copyright stay in the main UI."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

    pm = QPixmap(460, 280)
    pm.fill(QColor("#f8f5f2"))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    logo = QPixmap(str(assets / "app_icon_g.png"))
    if not logo.isNull():
        scaled = logo.scaled(112, 112, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((pm.width() - scaled.width()) // 2, 24, scaled)
    painter.setPen(QColor("#242424"))
    font = QFont()
    font.setPointSize(24)
    font.setBold(True)
    painter.setFont(font)
    title_rect = pm.rect().adjusted(0, 142, 0, -62)
    painter.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, name)
    painter.end()
    return pm


def _splash_loading_text(language: str, stage: str = "loading") -> str:
    zh = str(language or "").lower().startswith("zh")
    if stage == "building":
        return "正在构建主界面…" if zh else "Building main window…"
    return "正在加载 xraylarch 和界面组件…" if zh else "Loading xraylarch and UI components…"


def _run_gui(eng: bool = False) -> int:
    """Show the title-only splash while importing the heavy UI module."""
    import threading
    import traceback

    from app._boot import ensure_qt_plugin_env
    from app.version import APP_NAME, APP_VERSION, COPYRIGHT, app_settings

    ensure_qt_plugin_env()

    try:
        from PySide6.QtCore import QEventLoop, Qt
        from PySide6.QtGui import QColor, QIcon
        from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen
    except ModuleNotFoundError as exc:
        sys.stderr.write(
            "Missing package: {0}\n"
            "Use Launch-smartLarch-Platform.bat so it can install "
            "xraylarch + PySide6 (a generic Anaconda is not enough).\n".format(exc)
        )
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    language = str(app_settings().value("lang", "zh"))

    assets = Path(__file__).resolve().parent / "assets"
    ico = assets / "app_icon.ico"
    if ico.is_file():
        app.setWindowIcon(QIcon(str(ico)))

    # Single instance: a second launch only raises the running window, then exits.
    from app.single_instance import ensure_single_instance

    single_server, should_exit = ensure_single_instance(language=language)
    if should_exit:
        return 0

    splash = QSplashScreen(_make_splash_pixmap(assets, APP_NAME, APP_VERSION, COPYRIGHT))
    splash.show()
    splash.showMessage(
        _splash_loading_text(language),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
        QColor("#6b625d"),
    )
    app.processEvents()

    box: list = []

    def _import_heavy():
        try:
            import app.main_window as mw
            box.append(mw)
        except BaseException as exc:
            box.append(exc)

    thread = threading.Thread(target=_import_heavy, daemon=True)
    thread.start()
    while thread.is_alive():
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        thread.join(0.05)

    module = box[0] if box else RuntimeError("import thread finished without a result")
    if isinstance(module, BaseException):
        trace = "".join(traceback.format_exception(module))
        QMessageBox.critical(None, APP_NAME, f"smartLarch Platform failed to start:\n\n{module}\n\n{trace}")
        return 1

    splash.showMessage(
        _splash_loading_text(language, "building"),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
        QColor("#6b625d"),
    )
    app.processEvents()
    return int(module.run_app(app=app, splash=splash, eng=eng, single_instance=single_server) or 0)


def main(argv: list[str] | None = None) -> int:
    import os

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        try:
            if stream is None:
                setattr(sys, stream_name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="smartLarch Platform - Larch EXAFS fitting")
    parser.add_argument("--validate", nargs="?", const="__demo__", default=None)
    parser.add_argument("--cli-fit", action="store_true")
    parser.add_argument(
        "--eng",
        action="store_true",
        help="Start with engineering mode on (layout capture/export; also log-bar 🔧)",
    )
    args = parser.parse_args(argv)

    if args.validate is not None or args.cli_fit:
        from app.fit_engine import project_paths, run_cli_validate

        data = None if args.validate in (None, "__demo__") else args.validate
        if data is None and args.validate == "__demo__":
            data = str(project_paths()["demo_xdi"])
        return run_cli_validate(data)
    return _run_gui(eng=bool(args.eng))


if __name__ == "__main__":
    raise SystemExit(main())
