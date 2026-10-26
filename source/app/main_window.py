"""
smartLarch Platform — main window.

Layout (v0.4): header band + toolbar; three columns —
  LEFT   groups list + current-group meta (Z / edge / ΔE …)
  CENTER big plot (2×2 grid or focused E/k/R/q panel) + plot toolbar
  RIGHT  parameter pages (Norm | Fit | Tools | Merge)
Bottom: collapsible log + copyright/version footer.
Bilingual zh/EN via i18n (§B.16); version bottom-right (§B.13).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from copy import deepcopy
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

# ---- Qt ----
QT_API = None
try:
    from PySide6.QtCore import Qt, QEvent, QSettings, QThread, Signal, QUrl, QTimer, QItemSelectionModel, QSize, QPointF
    from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QPixmap, QCloseEvent, QColor, QBrush, QCursor, QPainter, QPainterPath, QPen, QPolygonF
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QProgressBar,
        QProgressDialog,
        QRadioButton,
        QButtonGroup,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QStackedWidget,
        QSystemTrayIcon,
        QTableWidget,
        QTableWidgetItem,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

    QT_API = "PySide6"
except Exception as _e:  # pragma: no cover
    raise ImportError(f"PySide6 required for smartLarch Platform: {_e}")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.layout_engine import TightLayoutEngine
from matplotlib import transforms as mtransforms

from .athena_pipeline import AthenaSettings, resolve_phase_feff_file
from .analysis_dialogs import (
    LCFDialog,
    MCRDialog,
    PCADialog,
    PrepeakDialog,
    WaveletDialog,
    wt_safe_name,
    wt_stored_params,
)
from .athena_ops import (
    EXPORT_DEFAULT_KINDS,
    EXPORT_KINDS,
    EXPORT_KIND_GROUPS,
    calibrate_e0,
    deglitch_auto,
    deglitch_interp,
    derivative,
    detect_edge,
    edge_guess_cost,
    edge_table_energy,
    suggest_merge_groups,
    energy_align,
    export_groups_csv,
    export_groups_xlsx,
    apply_prj_meta_to_group,
    load_all_from_prj,
    load_energy_mu_from_file,
    load_energy_mu_with_spec,
    fluo_channel_spec,
    sniff_raw_columns,
    _safe_filename,
    _partition_groups_by_element,
    merge_spectra,
    rebin_group,
    save_project_to_prj,
    smooth_mu,
    suggest_bkg_k_range,
    suggest_ft_k_window,
    suggest_pre_norm_windows,
    FT_KMIN_DEFAULT,
    OpResult,
)
from .fit_engine import (
    DEFAULT_OUTPUT,
    FitResult,
    FitSettings,
    GDSParam,
    PathDef,
    clear_fit_history,
    list_fit_history,
    load_fit_snapshot,
    model_a_preset,
    prepare_model_a_paths,
    project_paths,
    run_feffit,
    save_fit_to_history,
)
from . import coding_registry as AGENTREG
from . import pipeline_rules
from . import plugins_manager
from . import runtime_paths
from .ai_review import (
    LEGACY_DEFAULT_MODELS,
    PROVIDERS as AI_REVIEW_PROVIDERS,
    build_fit_review_prompt,
    downsample_curves,
    fetch_models,
    ping_provider,
    provider_default_model,
    request_fit_review,
)
from .ai_suggestions import (
    AISuggestionError,
    ClaudeSignInRequired,
    KimiSignInRequired,
    claude_cli_head,
    ai_cli_install_command,
    build_ai_check_prompt,
    clear_analysis_cache,
    format_ai_check_report,
    parse_ai_check_response,
    parse_suggestion_response,
    run_claude_headless,
    run_kimi_headless,
    suggestion_schema_json,
)
from ._boot import ensure_qt_plugin_env as _ensure_qt_plugin_env
from .i18n import I18n
from .project_model import AthenaProject, GroupRecord
from .version import APP_NAME, APP_VERSION, COPYRIGHT

# Answer cap for AI Check over the Anthropic API (that API needs one): the old
# 1400 could cut a many-dataset JSON answer, which the parser then refused.
AI_CHECK_MAX_TOKENS = 8192


def _parse_semver(text: str) -> Optional[Tuple[int, int, int]]:
    """Extract first X.Y.Z from a string; return None if missing."""
    if not text:
        return None
    m = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", str(text))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _version_tuple(ver: str) -> Tuple[int, int, int]:
    t = _parse_semver(ver)
    return t if t else (0, 0, 0)


class _DirectoryPickDialog(QFileDialog):
    """Non-native directory picker whose Choose always accepts a folder.

    Stock Qt with FileMode.Directory only reliably accepts a *selected*
    sub-folder; after double-clicking INTO a folder (selection cleared — e.g.
    it has no sub-folders) Choose may refuse or return nothing, so the folder
    being viewed can never be picked. Here an empty selection means "the
    folder I'm viewing" (v0.35.18).
    """

    def __init__(self, parent, title: str, start: str):
        super().__init__(parent, title, start or str(Path.home()))
        self.setFileMode(QFileDialog.FileMode.Directory)
        self.setOption(QFileDialog.Option.ShowDirsOnly, True)
        self.setOption(QFileDialog.Option.DontUseNativeDialog, True)

    def accept(self):  # Qt override — the Choose button path
        if not self.selectedFiles():
            # Empty selection (e.g. the user entered a folder that has no
            # sub-folders): Choose means "the folder I'm viewing". Stock Qt
            # refuses or returns nothing here on some versions. Skip the line
            # edit entirely — selectFile(absolute dir path) resolves
            # inconsistently across Qt versions — unless the user typed a
            # name in the filename field.
            edit = self.findChild(QLineEdit, "fileNameEdit")
            if edit is None or not edit.text().strip():
                self.done(QDialog.DialogCode.Accepted)
                return
        super().accept()


def _pick_directory(parent, title: str, start: str = "") -> str:
    """Modal directory pick; '' when cancelled. Never loses the viewed folder."""
    dlg = _DirectoryPickDialog(parent, title, start)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    for path in dlg.selectedFiles():
        if path:
            return path
    return dlg.directory().absolutePath()


_APP_QSS = """
QMainWindow { background: #f8f5f2; }
#headerBand { background: white; border-bottom: 1px solid #eaa893; }
#toolBand { background: #fdfaf8; border-bottom: 1px solid #eaa893; }
#logBand { background: #fdfaf8; border-top: 1px solid #eaa893; }
QFrame#fitQualityCard { background: #fdfaf8; border: 1px solid #e3d5cd; border-radius: 4px; }
#footBand { background: #f8f5f2; }
QGroupBox {
    font-weight: 600;
    margin-top: 10px;
    padding: 8px 6px 6px 6px;
    border: 1px solid #e3d5cd;
    border-radius: 6px;
    background: white;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}

QPushButton {
    background: #e64626; color: white;
    /* 1px border reserved in EVERY state so :pressed never shifts geometry
       (a geometry change reads as "the font dipped", not "the button sank") */
    border: 1px solid #c93c20;
    padding: 5px 12px; border-radius: 4px;
}
QPushButton:hover { background: #d13e20; }
/* Press affordance (v0.35.3): the BUTTON sinks — deeper shade + sunken
   border shading (dark top/left, light bottom/right).  Padding stays fixed. */
QPushButton:pressed {
    background: #a83215;
    border-top-color: #6e2210;
    border-left-color: #6e2210;
    border-bottom-color: #f09078;
    border-right-color: #f09078;
}
/* Open menu: hide fat native ▾; text uses compact ▾ with tight padding */
QPushButton#openMenuBtn {
    padding: 5px 8px 5px 10px;
    text-align: center;
    font-size: 13px;
}
QPushButton#openMenuBtn::menu-indicator {
    image: none;
    width: 0;
    height: 0;
    border: none;
    subcontrol-position: right center;
}
/* Log collapse: text chevron, no native arrow chrome */
QToolButton#logToggle, QToolButton#logToggle:checked, QToolButton#logToggle:!checked {
    border: none;
    background: transparent;
    padding: 0;
    margin: 0;
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    font-size: 14px;
    font-weight: 600;
    color: #8a7a72;
}
QToolButton#logToggle:hover {
    color: #e64626;
    background: transparent;
    border: none;
}
#ghostBtn {
    background: white; color: #333; border: 1px solid #ccc;
    padding: 4px 10px; border-radius: 4px;
}
#ghostBtn:hover { border-color: #e64626; color: #e64626; }
#ghostBtn:pressed {
    background: #f6e3da; color: #a83215;
    border-top-color: #a83215; border-left-color: #a83215;
    border-bottom-color: #f0c4b4; border-right-color: #f0c4b4;
}
#iconBtn {
    background: white; color: #333; border: 1px solid #ccc;
    padding: 0; border-radius: 4px; font-size: 15px;
}
#iconBtn:hover { border-color: #e64626; color: #e64626; }
#iconBtn:pressed {
    background: #f6e3da; color: #a83215;
    border-top-color: #a83215; border-left-color: #a83215;
    border-bottom-color: #f0c4b4; border-right-color: #f0c4b4;
}
QListWidget { background: white; border: 1px solid #e3d5cd; border-radius: 4px; }
QComboBox, QLineEdit {
    padding: 2px 4px;
    min-height: 22px;
    background: white;
    border: 1px solid #d5c8c0;
    border-radius: 3px;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
    background: transparent;
}
QComboBox::down-arrow {
    width: 9px;
    height: 9px;
}
QComboBox#pathElementCombo {
    padding: 2px 20px 2px 8px;
    text-align: center;
}
QListWidget::item:selected {
    background: #2f80d0;
    color: white;
}
QListWidget::item:hover { background: #eaf3fc; }
QListWidget::item:selected:hover { background: #2f80d0; }
QDoubleSpinBox, QSpinBox {
    /* with QSS the arrow buttons live INSIDE the right padding:
       too small -> buttons overlay the number (the overlap bug).
       Reserve exactly the button width here… */
    padding-right: 18px;
    min-height: 22px;
    background: white;
    border: 1px solid #d5c8c0;
    border-radius: 3px;
}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button {
    /* …and cap the native style's oversized touch buttons */
    width: 16px;
    border: none;
    background: transparent;
}
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow,
QSpinBox::up-arrow, QSpinBox::down-arrow {
    width: 8px;
    height: 8px;
}
QPlainTextEdit { background: #1e1e1e; color: #d4d4d4; border: 1px solid #333; }
QToolButton:checked {
    background: #e64626; color: white; border: 1px solid #c93c20;
    border-radius: 3px; font-weight: 600;
}
QToolButton:!checked {
    background: #f4ede8; color: #333; border: 1px solid #ccc;
    border-radius: 3px;
}
/* Sunken press for tool buttons too (v0.35.3) — the button, not the text */
QToolButton:!checked:pressed {
    background: #e6d7cf;
    border-top-color: #a89a92; border-left-color: #a89a92;
    border-bottom-color: #ffffff; border-right-color: #ffffff;
}
QToolButton:checked:pressed {
    background: #a83215;
    border-top-color: #6e2210; border-left-color: #6e2210;
    border-bottom-color: #f09078; border-right-color: #f09078;
}
QToolButton#modeTab, QToolButton#modeTab:checked, QToolButton#modeTab:!checked {
    font-weight: 700;
}
/* Collapsible modules: title on a L–R rule; thin line frames the body (no header box/bg) */
QLabel#collapseHdr {
    font-weight: 700;
    padding: 0 6px;
    border: none;
    background: transparent;
    color: #333;
}
QFrame#collapseRule {
    border: none;
    max-height: 1px;
    min-height: 1px;
    background: #d5c8c0;
}
QFrame#collapseBody, QFrame#collapseBodyPlain {
    border: none;
    background: transparent;
}
"""

# Dark palette from UI-Theme-Library/themes/easyxafs-dark (ochre accent kept)
_DARK_QSS = """
QMainWindow { background: #19232d; }
QWidget { color: #e6edf3; }
#headerBand { background: #232f3b; border-bottom: 1px solid #3a4b5c; }
#toolBand { background: #232f3b; border-bottom: 1px solid #3a4b5c; }
#logBand { background: #232f3b; border-top: 1px solid #3a4b5c; }
QFrame#fitQualityCard { background: #2b3947; border: 1px solid #3a4b5c; border-radius: 4px; }
#footBand { background: #19232d; }
QGroupBox {
    font-weight: 600;
    margin-top: 10px;
    padding: 8px 6px 6px 6px;
    border: 1px solid #3a4b5c;
    border-radius: 6px;
    background: #232f3b;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}
QPushButton {
    background: #e64626; color: white;
    border: 1px solid #c93c20;  /* reserved so :pressed never shifts geometry */
    padding: 5px 12px; border-radius: 4px;
}
QPushButton:hover { background: #d13e20; }
QPushButton:pressed {
    background: #a83215;
    border-top-color: #571c0b; border-left-color: #571c0b;
    border-bottom-color: #ff9c73; border-right-color: #ff9c73;
}
QPushButton#openMenuBtn {
    padding: 5px 8px 5px 10px;
    text-align: center;
    font-size: 13px;
}
QPushButton#openMenuBtn::menu-indicator {
    image: none;
    width: 0;
    height: 0;
    border: none;
    subcontrol-position: right center;
}
QToolButton#logToggle, QToolButton#logToggle:checked, QToolButton#logToggle:!checked {
    border: none;
    background: transparent;
    padding: 0;
    margin: 0;
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    font-size: 14px;
    font-weight: 600;
    color: #9aa7b5;
}
QToolButton#logToggle:hover {
    color: #ff8c1a;
    background: transparent;
    border: none;
}
#ghostBtn {
    background: #2b3947; color: #e6edf3; border: 1px solid #3a4b5c;
    padding: 4px 10px; border-radius: 4px;
}
#ghostBtn:hover { border-color: #e64626; color: #ff8c1a; }
#ghostBtn:pressed {
    background: #1f2c38; color: #ff8c1a;
    border-top-color: #141c25; border-left-color: #141c25;
    border-bottom-color: #5a7186; border-right-color: #5a7186;
}
#iconBtn {
    background: #2c3a48; color: #e6edf3; border: 1px solid #3a4b5c;
    padding: 0; border-radius: 4px; font-size: 15px;
}
#iconBtn:hover { border-color: #e64626; color: #ff8c1a; }
#iconBtn:pressed {
    background: #1f2c38; color: #ff8c1a;
    border-top-color: #141c25; border-left-color: #141c25;
    border-bottom-color: #5a7186; border-right-color: #5a7186;
}
QListWidget { background: #232f3b; border: 1px solid #3a4b5c; border-radius: 4px; }
QComboBox, QLineEdit {
    padding: 2px 4px;
    min-height: 22px;
    background: #2b3947;
    border: 1px solid #3a4b5c;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
    background: transparent;
}
QComboBox::down-arrow {
    width: 9px;
    height: 9px;
}
QComboBox#pathElementCombo {
    padding: 2px 20px 2px 8px;
    text-align: center;
}
QListWidget::item:selected {
    background: #1f5f9e;
    color: white;
}
QListWidget::item:hover { background: #2b3947; }
QListWidget::item:selected:hover { background: #1f5f9e; }
QDoubleSpinBox, QSpinBox {
    padding-right: 18px;
    min-height: 22px;
    background: #2b3947;
    border: 1px solid #3a4b5c;
}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button {
    width: 16px;
    border: none;
    background: transparent;
}
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow,
QSpinBox::up-arrow, QSpinBox::down-arrow {
    width: 8px;
    height: 8px;
}
QPlainTextEdit { background: #10181f; color: #d4e2ec; border: 1px solid #3a4b5c; }
QToolButton:checked {
    background: #e64626; color: white; border: 1px solid #c93c20;
    border-radius: 3px; font-weight: 600;
}
QToolButton:!checked {
    background: #2b3947; color: #e6edf3; border: 1px solid #3a4b5c;
    border-radius: 3px;
}
QToolButton:!checked:pressed {
    background: #1f2c38;
    border-top-color: #141c25; border-left-color: #141c25;
    border-bottom-color: #5a7186; border-right-color: #5a7186;
}
QToolButton:checked:pressed {
    background: #a83215;
    border-top-color: #571c0b; border-left-color: #571c0b;
    border-bottom-color: #ff9c73; border-right-color: #ff9c73;
}
QToolButton#modeTab, QToolButton#modeTab:checked, QToolButton#modeTab:!checked {
    font-weight: 700;
}
QLabel#collapseHdr {
    font-weight: 700;
    padding: 0 6px;
    border: none;
    background: transparent;
    color: #e6edf3;
}
QFrame#collapseRule {
    border: none;
    max-height: 1px;
    min-height: 1px;
    background: #3a4b5c;
}
QFrame#collapseBody, QFrame#collapseBodyPlain {
    border: none;
    background: transparent;
}
"""


class Worker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None, *a, **k):
        super().__init__(parent)
        self.fn, self.a, self.k = fn, a, k

    def run(self):
        try:
            self.finished_ok.emit(self.fn(*self.a, **self.k))
        except Exception:
            self.failed.emit(traceback.format_exc())


class _CachedTightLayout(TightLayoutEngine):
    """tight_layout that reuses the last subplot params while nothing that
    shapes the margins changed (v2.18.0).

    A full tight_layout measures every tick label of every panel and was
    ~60% of a group switch with 40+ marked curves.  The margins only depend
    on the figure size, the axes grid, titles/axis labels and the tick-label
    strings, so a cheap signature of exactly those decides whether the
    cached ``subplots_adjust`` kwargs are still valid.  Anything unexpected
    → full layout (never a stale one on purpose).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cache_key = None
        self._cache_kwargs: Optional[dict] = None

    @staticmethod
    def _tick_sig(axis, lim) -> tuple:
        locs = [v for v in axis.get_majorticklocs() if min(lim) <= v <= max(lim)]
        fmt = axis.get_major_formatter()
        labels = fmt.format_ticks(locs) if locs else []
        offset = fmt.get_offset() if hasattr(fmt, "get_offset") else ""
        return (len(labels), max((len(s) for s in labels), default=0), len(offset or ""))

    def _signature(self, fig) -> Optional[tuple]:
        try:
            parts = [round(fig.bbox.width), round(fig.bbox.height), fig.dpi]
            for ax in fig.axes:
                ss = ax.get_subplotspec()
                parts.append((
                    ss.get_geometry() if ss is not None else None,
                    ax.get_visible(), ax.axison,
                    # titles/labels only occupy their line height: count
                    # lines, not text ("Energy (22 spectra)" → "(23 …)"
                    # must not force a full re-layout).
                    *(
                        (s.count("\n") + 1) if s else 0
                        for s in (ax.get_title(), ax.get_xlabel(), ax.get_ylabel())
                    ),
                    # x tick text only adds a fixed-height row; y tick text
                    # width (longest label + offset) sets the left margin.
                    self._tick_sig(ax.xaxis, ax.get_xlim())[0] > 0,
                    self._tick_sig(ax.yaxis, ax.get_ylim())[1:],
                ))
            return tuple(parts)
        except Exception:
            return None

    def execute(self, fig):
        key = self._signature(fig)
        if key is not None and key == self._cache_key and self._cache_kwargs:
            fig.subplots_adjust(**self._cache_kwargs)
            return
        super().execute(fig)
        sp = fig.subplotpars
        self._cache_kwargs = {
            k: getattr(sp, k) for k in ("left", "right", "bottom", "top", "wspace", "hspace")
        }
        self._cache_key = key


class PlotCanvas(FigureCanvas):
    """Multi-panel E/k/R/q plot with click-to-mark, drag-to-set edge guides."""

    def __init__(self, on_click=None, on_drag_guide=None, on_dblclick=None, parent=None):
        self.fig = Figure(figsize=(8.0, 6.0))
        self.fig.set_layout_engine(_CachedTightLayout())
        super().__init__(self.fig)
        self.setParent(parent)
        self.on_click = on_click
        self.on_drag_guide = on_drag_guide  # (panel, name, x_abs) while dragging guides
        self.on_dblclick = on_dblclick  # E0 pin armed → set E0; else reset zoom
        self.mode = "grid"  # grid | E | k | R | q
        self._axes_face = "white"  # theme-tracked axes/figure facecolor
        self.axes: dict = {}
        self._guide_targets: dict = {}  # panel -> {name -> absolute x}
        self._guide_artists: dict = {}  # (panel, name) -> [Line2D]
        self._span_artists: dict = {}   # (panel, span_key) -> Polygon
        self._span_edges: dict = {}     # (panel, span_key) -> (lo_name, hi_name)
        self._legend_artist_map: dict = {}  # clickable legend artist -> tuple[data artists]
        self._drag_name: Optional[str] = None
        self._drag_panel: Optional[str] = None
        self._drag_active = False
        self._pan_ax = None          # axis being middle-drag panned
        self._pan_press = None       # (px, py) at pan start
        self._pan_xlim = None
        self._pan_ylim = None
        self._build_axes()
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("motion_notify_event", self._on_motion)
        self.mpl_connect("button_release_event", self._on_release)
        self.mpl_connect("scroll_event", self._on_scroll)
        self.mpl_connect("pick_event", self._on_pick)

    def _build_axes(self):
        self.fig.clear()
        self._kq_twins = {}
        self.fig.set_facecolor(self._axes_face)
        if self.mode == "grid":
            self.axes = {
                "E": self.fig.add_subplot(221),
                "k": self.fig.add_subplot(222),
                "R": self.fig.add_subplot(223),
                "q": self.fig.add_subplot(224),
            }
        elif self.mode in ("fit_rk", "fit_kq"):
            self.axes = {
                "fit_top": self.fig.add_subplot(211),
                "fit_bottom": self.fig.add_subplot(212),
            }
        else:
            self.axes = {self.mode: self.fig.add_subplot(111)}
        for ax in self.axes.values():
            ax.set_facecolor(self._axes_face)

    def set_mode(self, mode: str):
        if mode not in (
            "grid", "E", "k", "R", "q", "kq", "wt",
            "fit_k123", "fit_r123", "fit_rmr", "fit_rk", "fit_kq",
        ):
            mode = "grid"
        if mode != self.mode:
            self.mode = mode
            self._build_axes()

    def set_theme(self, dark: bool):
        """Match matplotlib chrome to the app theme."""
        import matplotlib

        if dark:
            matplotlib.rcParams.update(
                {
                    "figure.facecolor": "#2b3947",
                    "axes.facecolor": "#2b3947",
                    "axes.edgecolor": "#9fb0c0",
                    "axes.labelcolor": "#e6edf3",
                    "xtick.color": "#9fb0c0",
                    "ytick.color": "#9fb0c0",
                    "text.color": "#e6edf3",
                    "grid.color": "#3a4b5c",
                }
            )
            self._axes_face = "#2b3947"
        else:
            matplotlib.rcParams.update(
                {
                    "figure.facecolor": "white",
                    "axes.facecolor": "white",
                    "axes.edgecolor": "black",
                    "axes.labelcolor": "black",
                    "xtick.color": "black",
                    "ytick.color": "black",
                    "text.color": "black",
                    "grid.color": "#b0b0b0",
                }
            )
            self._axes_face = "white"
        # facecolors are fixed per artist — apply to existing fig/axes too
        self.fig.set_facecolor(self._axes_face)
        for ax in self.axes.values():
            ax.set_facecolor(self._axes_face)
        self.draw_idle()

    def set_guide_targets(self, guides_by_panel: Optional[dict]) -> None:
        """Draggable guide positions per panel: {panel: {name: absolute x}}.
        E panel: pre1/pre2/norm1/norm2 (eV) — E0 is pin + double-click, not
        draggable (v0.35.1); k: kmin/kmax/bkmin/bkmax (Å⁻¹); R: rmin/rmax (Å)."""
        self._guide_targets = {}
        for panel, gd in (guides_by_panel or {}).items():
            self._guide_targets[panel] = {
                k: float(v)
                for k, v in (gd or {}).items()
                if v is not None and np.isfinite(float(v))
            }

    def _panel_of(self, ax) -> Optional[str]:
        for p, a in self.axes.items():
            if a is ax:
                return p
        return None

    def _hit_guide(self, ax, xdata: float) -> Optional[str]:
        """Return guide name if *xdata* is within ~10 px of a guide on this axis."""
        panel = self._panel_of(ax)
        targets = self._guide_targets.get(panel) if panel else None
        if not targets:
            return None
        try:
            x0, _ = ax.transData.transform((xdata, 0))
        except Exception:
            return None
        best_name, best_dx = None, 1e9
        for name, xv in targets.items():
            try:
                xp, _ = ax.transData.transform((xv, 0))
            except Exception:
                continue
            dx = abs(xp - x0)
            if dx < best_dx:
                best_dx, best_name = dx, name
        if best_name is not None and best_dx <= 10:  # pixels
            return best_name
        return None

    def _on_scroll(self, event):
        """Mouse-wheel zoom on the axis under the cursor (default interaction)."""
        if self.mode == "wt":
            return  # read-only heatmap: no zoom (double-click reset is disabled too)
        if event.inaxes is None:
            return
        ax = event.inaxes
        # events on a kq twin (top q-axis) retarget the main k axis — the two
        # are kept on the same scale, so they must zoom together
        main = getattr(ax, "_kq_main", None)
        if main is not None:
            ax = main
        # scroll up → zoom in
        scale = 0.8 if getattr(event, "step", 0) > 0 else 1.25
        try:
            xdata = float(event.xdata) if event.xdata is not None else None
            ydata = float(event.ydata) if event.ydata is not None else None
        except (TypeError, ValueError):
            return
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        if xdata is not None:
            ax.set_xlim(
                xdata - (xdata - xlim[0]) * scale,
                xdata + (xlim[1] - xdata) * scale,
            )
        if ydata is not None:
            ax.set_ylim(
                ydata - (ydata - ylim[0]) * scale,
                ydata + (ylim[1] - ydata) * scale,
            )
        twin = getattr(ax, "_kq_twin", None)
        if twin is not None:
            twin.set_xlim(ax.get_xlim())
        self.draw_idle()

    def _on_press(self, event):
        if self.mode == "wt":
            return  # WT panel is read-only — no pins, no guide drag, no pan (spec §4.3b)
        # Middle button: grab-and-pan the view (v0.35.0) — no need to zoom
        # out first when a wheel-zoom landed on the wrong spot.
        if event.button == 2 and event.inaxes is not None:
            ax = event.inaxes
            main = getattr(ax, "_kq_main", None)
            if main is not None:  # kq twin (top q-axis) pans the main k axis
                ax = main
            self._pan_ax = ax
            self._pan_press = (float(event.x), float(event.y))
            self._pan_xlim = tuple(ax.get_xlim())
            self._pan_ylim = tuple(ax.get_ylim())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button != 1 or event.inaxes is None or event.xdata is None:
            return
        if getattr(event, "dblclick", False):
            # double-click: with the E0 pin armed the handler sets E0 at the
            # clicked energy; otherwise it restores the full-range view
            if self.on_dblclick:
                self.on_dblclick(event)
            return
        hit = self._hit_guide(event.inaxes, float(event.xdata))
        if hit is not None:
            self._drag_name = hit
            self._drag_panel = self._panel_of(event.inaxes)
            self._drag_active = True
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            return
        # plain click — only meaningful when a pin is armed (handler decides)
        if self.on_click:
            self.on_click(event.inaxes, float(event.xdata), float(event.ydata), event)

    def _on_motion(self, event):
        if self.mode == "wt":
            return  # read-only heatmap
        if self._pan_ax is not None:
            # Middle-drag pan: the data point grabbed at press follows the
            # cursor.  Fractions are computed against the ORIGINAL limits so
            # the pan does not drift.
            if event.x is None or self._pan_press is None:
                return
            ax = self._pan_ax
            bbox = ax.bbox
            w = max(float(bbox.width), 1.0)
            h = max(float(bbox.height), 1.0)
            x0, x1 = self._pan_xlim
            y0, y1 = self._pan_ylim
            dfx = ((self._pan_press[0] - float(event.x)) / w) * (x1 - x0)
            dfy = ((self._pan_press[1] - float(event.y)) / h) * (y1 - y0)
            ax.set_xlim(x0 + dfx, x1 + dfx)
            ax.set_ylim(y0 + dfy, y1 + dfy)
            twin = getattr(ax, "_kq_twin", None)
            if twin is not None:
                twin.set_xlim(ax.get_xlim())
            self.draw_idle()
            return
        if not self._drag_active or self._drag_name is None:
            # hover cursor near guides (any panel that has guides)
            if event.inaxes is not None and event.xdata is not None:
                if self._hit_guide(event.inaxes, float(event.xdata)):
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        # only track motion over the panel where the drag started (xdata is
        # in that axes' coordinates)
        if event.inaxes is None or event.xdata is None:
            return
        if event.inaxes is not self.axes.get(self._drag_panel):
            return
        if self.on_drag_guide:
            self.on_drag_guide(self._drag_panel, self._drag_name, float(event.xdata), live=True)

    def _on_release(self, event):
        if self.mode == "wt":
            return  # read-only heatmap
        if self._pan_ax is not None and getattr(event, "button", None) == 2:
            self._pan_ax = None
            self._pan_press = None
            self._pan_xlim = None
            self._pan_ylim = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if not self._drag_active:
            return
        panel, name = self._drag_panel, self._drag_name
        self._drag_active = False
        self._drag_name = None
        self._drag_panel = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if name is not None and event.xdata is not None and self.on_drag_guide:
            self.on_drag_guide(panel, name, float(event.xdata), live=False)

    def move_guide(self, panel: str, name: str, x: float) -> None:
        """Cheap live update during drag: move only the guide line (and its
        span fill if any) — full replot happens once on release."""
        x = float(x)
        targets = self._guide_targets.get(panel)
        if targets is not None and name in targets:
            targets[name] = x
        for ln in self._guide_artists.get((panel, name), []):
            if len(np.atleast_1d(ln.get_xdata())) <= 1:
                # point marker (E0 dot) — move the single point, keep its y
                ln.set_xdata([x])
            else:
                ln.set_xdata([x, x])
        for (p, skey), (lo_name, hi_name) in self._span_edges.items():
            if p != panel or name not in (lo_name, hi_name):
                continue
            poly = self._span_artists.get((p, skey))
            t = self._guide_targets.get(p, {})
            lo, hi = t.get(lo_name), t.get(hi_name)
            if poly is None or lo is None or hi is None:
                continue
            a, b = (lo, hi) if lo <= hi else (hi, lo)
            try:
                # axvspan returns a Rectangle (x/width; y spans the axes)
                poly.set_x(a)
                poly.set_width(b - a)
            except Exception:
                pass
        self.draw_idle()

    def clear(self):
        # twin axes (kq combined view) survive ax.clear() — drop them explicitly
        for twin in getattr(self, "_kq_twins", {}).values():
            try:
                twin.remove()
            except Exception:
                pass
        self._kq_twins = {}
        self._legend_artist_map = {}
        for ax in self.axes.values():
            if hasattr(ax, "_kq_twin"):
                delattr(ax, "_kq_twin")
            ax.clear()

    def set_countdown(self, text: Optional[str]) -> None:
        """Show/hide the armed-pin countdown chip on the plot (figure level,
        so axes clearing on replot does not wipe it)."""
        old = getattr(self, "_countdown_txt", None)
        if old is not None:
            try:
                old.remove()
            except Exception:
                pass
        self._countdown_txt = None
        if text:
            self._countdown_txt = self.fig.text(
                0.995, 0.995, text,
                ha="right", va="top", fontsize=9, fontweight="bold",
                color="#a83215",
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#e64626", alpha=0.92),
            )
        self.draw_idle()

    def set_window_visibility(self, window: str, visible: bool) -> None:
        """Synchronously toggle a window's shade and both boundary lines.

        This deliberately does not wait for a full replot: a checkbox event
        can occur while the UI is batching control updates, when ``_replot``
        is intentionally deferred.
        """
        records = {
            "k": (("k", "ft"), (("k", "kmin"), ("k", "kmax"))),
            "r": (("R", "r"), (("R", "rmin"), ("R", "rmax"))),
            "k_bkg": (("k", "bkg"), (("k", "bkmin"), ("k", "bkmax"))),
        }
        record = records.get(window)
        if record is None:
            return
        span_key, line_keys = record
        artist = self._span_artists.get(span_key)
        if artist is not None:
            artist.set_visible(bool(visible))
        for key in line_keys:
            for line in self._guide_artists.get(key, []):
                line.set_visible(bool(visible))
        self.draw_idle()

    def _add_clickable_legend(self, ax, handles, labels):
        """Create a legend whose line samples and labels toggle their curves.

        Overlay only: upper-right, one vertical column, not a tight_layout
        participant. A many-entry multi-column loc='best' legend used to sit
        on top of the figure and shove the E/k/R/q axes to the right
        (reproduced on a 44-group project).
        """
        count = len(labels)
        legend = ax.legend(
            handles,
            labels,
            fontsize=7 if count <= 12 else 6,
            loc="upper right",
            framealpha=0.78 if count > 4 else 0.85,
            ncols=1,
            columnspacing=0.8,
            handletextpad=0.4,
            labelspacing=0.25 if count > 12 else 0.4,
            borderaxespad=0.3,
        )
        legend.set_in_layout(False)
        legend.set_draggable(True)

        def targets_for(original):
            gid = str(original.get_gid() or "")
            if not gid.startswith(("group:", "win:", "guide:")):
                return (original,)
            targets = []
            search_axes = list(self.axes.values())
            for base_ax in self.axes.values():
                twin = getattr(base_ax, "_kq_twin", None)
                if twin is not None:
                    search_axes.append(twin)
            for search_ax in search_axes:
                targets.extend(line for line in search_ax.lines if line.get_gid() == gid)
                if not gid.startswith("group:"):
                    # window/guide legend entries own the span fill (axvspan
                    # Rectangle) as well — toggle the whole visual group
                    targets.extend(p for p in search_ax.patches if p.get_gid() == gid)
            return tuple(dict.fromkeys(targets)) or (original,)

        for proxy, original in zip(legend.get_lines(), handles):
            proxy.set_picker(5)
            self._legend_artist_map[id(proxy)] = targets_for(original)
        for text, original in zip(legend.get_texts(), handles):
            text.set_picker(True)
            self._legend_artist_map[id(text)] = targets_for(original)
        return legend

    def _on_pick(self, event):
        if self.mode == "wt":
            return  # read-only heatmap — no legend toggling
        originals = self._legend_artist_map.get(id(getattr(event, "artist", None)))
        if not originals:
            return
        visible = not any(bool(original.get_visible()) for original in originals)
        for original in originals:
            original.set_visible(visible)
        # Keep the legend itself as the affordance: faded means hidden.
        for proxy_id, mapped in self._legend_artist_map.items():
            if mapped == originals:
                # The object itself is not retained separately, so find it
                # among current figure artists by its stable object id.
                for ax in self.axes.values():
                    leg = ax.get_legend()
                    if leg is None:
                        continue
                    for artist in [*leg.get_lines(), *leg.get_texts()]:
                        if id(artist) == proxy_id:
                            artist.set_alpha(1.0 if visible else 0.25)
        self.draw_idle()

    # ---- per-panel draw helpers ----
    # Guide-line palette (E0 / pre-edge / post-edge ranges) — high contrast on light & dark
    GUIDE_COLORS = {
        "e0": "#e64626",       # ochre-red solid
        "pre": "#1a6fb5",      # blue dashed
        "norm": "#1a8f4a",     # green dashed
        "mark": "#9b59b6",     # purple generic click marks
        "raw": "#8a817c",      # gray — before energy shift
    }

    def _draw_E(
        self,
        ax,
        g,
        a,
        show,
        emin,
        emax,
        label,
        n_groups,
        draw_shift_compare: bool = False,
        point_mode: bool = False,
        deglitch_selected_energy: Optional[float] = None,
    ):
        e, mu = a.energy, a.mu
        if e is None or mu is None:
            return
        e0 = a.e0 if np.isfinite(a.e0) else e[len(e) // 2]
        msk = (e >= e0 + emin) & (e <= e0 + emax)
        if not np.any(msk):
            msk = slice(None)
        y = mu
        if show.get("normalized") and a.norm is not None:
            y = a.flat if (show.get("flat") and a.flat is not None) else a.norm
        if show.get("deriv1"):
            y = derivative(e, y if y is not None else mu, 1)
        if show.get("deriv2"):
            y = derivative(e, y if y is not None else mu, 2)

        # Before-shift (raw energy) vs after-shift for clear visual comparison.
        # The raw overlay gets its own gid suffix so the legend can hide/show
        # it independently of the shifted curve (v0.34.11).
        shift = float(getattr(g, "energy_shift", 0.0) or 0.0)
        if (
            draw_shift_compare
            and abs(shift) > 1e-6
            and g.energy is not None
            and g.mu is not None
            and not show.get("deriv1")
            and not show.get("deriv2")
            and not show.get("normalized")
        ):
            e_raw = np.asarray(g.energy, dtype=float)
            mu_raw = np.asarray(g.mu, dtype=float)
            # Window relative to shifted e0 still; raw sits left/right by -shift
            msk_raw = (e_raw >= e0 + emin - shift) & (e_raw <= e0 + emax - shift)
            if not np.any(msk_raw):
                msk_raw = slice(None)
            raw_line = ax.plot(
                e_raw[msk_raw],
                mu_raw[msk_raw],
                ls="--",
                lw=1.3,
                color=self.GUIDE_COLORS["raw"],
                alpha=0.85,
                label=(f"{label} raw" if label else "raw (no ΔE)"),
                zorder=2,
            )[0]
            raw_line.set_gid(f"group:{g.id}:raw")

        curve = ax.plot(
            e[msk],
            np.asarray(y)[msk],
            lw=0 if point_mode else 1.25,
            ls="None" if point_mode else "-",
            marker="o" if point_mode else None,
            ms=3.2 if point_mode else None,
            label=label,
            alpha=0.95,
            zorder=3,
        )[0]
        curve.set_gid(f"group:{g.id}")
        if point_mode and deglitch_selected_energy is not None:
            try:
                selected_energy = float(deglitch_selected_energy)
                y_array = np.asarray(y)
                finite = np.isfinite(e) & np.isfinite(y_array)
                if np.any(finite):
                    finite_idx = np.flatnonzero(finite)
                    idx = int(finite_idx[np.argmin(np.abs(e[finite] - selected_energy))])
                    ax.plot(
                        [e[idx]],
                        [y_array[idx]],
                        ls="None",
                        marker="o",
                        ms=9.0,
                        mfc=self.GUIDE_COLORS["e0"],
                        mec="white",
                        mew=1.3,
                        label="_deglitch_selected_",
                        zorder=10,
                    )
            except (TypeError, ValueError, IndexError):
                pass
        if show.get("pre") and a.pre_edge is not None and n_groups <= 2:
            ax.plot(e[msk], a.pre_edge[msk], "--", lw=1.0, color="C1", zorder=2)
        if show.get("post") and a.post_edge is not None and n_groups <= 2:
            ax.plot(e[msk], a.post_edge[msk], "--", lw=1.0, color="C2", zorder=2)
        if show.get("bkg") and a.bkg is not None and n_groups <= 2:
            try:
                ax.plot(e[msk], a.bkg[msk], ":", lw=1.0, color="C3", zorder=2)
            except Exception:
                pass
        ax.set_title(f"Energy  ({n_groups} spectra)" if n_groups > 1 else "Energy")
        ax.set_xlabel("E (eV)")
        ax.grid(True, alpha=0.3)

    def _draw_E_guides(
        self,
        ax,
        guides: Optional[dict],
        marks: Optional[List[float]],
        e0_y: Optional[float] = None,
        visible: Optional[set] = None,
        baseline_arrays=None,
        draw_baseline: bool = True,
    ):
        """E-panel guides.  The E0 dot is always drawn.  The pre / norm
        windows (band + dashed edges + the fitted baseline segment inside the
        window) appear ONLY while their pin is armed (v0.35.1) — and only
        then are the edges drag targets (draw_groups registers targets for
        visible windows only)."""
        guides = guides or {}
        visible = set(visible or ())

        def _vline(x, color, name=None, ls="--", lw=2.0, label=None, gid=None):
            if x is None or not np.isfinite(x):
                return
            ln = ax.axvline(float(x), color=color, ls=ls, lw=lw, alpha=0.95, zorder=4, label=label)
            if name is not None:
                self._guide_artists.setdefault(("E", name), []).append(ln)
            if gid is not None:
                ln.set_gid(gid)

        # E0 — red dot on the curve (v0.35.0); set by pin + double-click,
        # not a drag target (v0.35.1).  Without a curve y it parks at the top.
        e0v = guides.get("e0")
        if e0v is not None and np.isfinite(e0v):
            e0_style = dict(
                ls="None",
                marker="o",
                ms=8,
                mfc=self.GUIDE_COLORS["e0"],
                mec="white",
                mew=1.2,
                alpha=0.95,
                zorder=5,
                label="E0",
            )
            if e0_y is not None and np.isfinite(e0_y):
                ln = ax.plot([float(e0v)], [float(e0_y)], **e0_style)[0]
            else:
                blended = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
                ln = ax.plot([float(e0v)], [0.94], transform=blended, **e0_style)[0]
            self._guide_artists.setdefault(("E", "e0"), []).append(ln)
            ln.set_gid("guide:E:e0")

        def _window(tag, lo_key, hi_key, color, ls, label, baseline_attr):
            lo_v, hi_v = guides.get(lo_key), guides.get(hi_key)
            if lo_v is None or hi_v is None or not np.isfinite(lo_v) or not np.isfinite(hi_v):
                return
            lo, hi = (lo_v, hi_v) if lo_v <= hi_v else (hi_v, lo_v)
            if hi - lo <= 1e-6:
                return
            poly = ax.axvspan(lo, hi, color=color, alpha=0.12, zorder=0, lw=0)
            poly.set_gid(f"guide:E:{tag}")  # legend toggles band + edges + baseline
            self._span_artists[("E", tag)] = poly
            self._span_edges[("E", tag)] = (lo_key, hi_key)
            _vline(lo_v, color, name=lo_key, ls=ls, lw=2.0, label=label, gid=f"guide:E:{tag}")
            _vline(hi_v, color, name=hi_key, ls=ls, lw=2.0, gid=f"guide:E:{tag}")
            # The fitted baseline segment inside the window — thin red line
            # for both pre and post, μ-scale views only (v0.35.8: it used to
            # leak into derivative/normalized views where it means nothing)
            base = getattr(baseline_arrays, baseline_attr, None) if baseline_arrays is not None else None
            e = getattr(baseline_arrays, "energy", None) if baseline_arrays is not None else None
            if draw_baseline and base is not None and e is not None:
                m = (np.asarray(e, dtype=float) >= lo) & (np.asarray(e, dtype=float) <= hi)
                if np.any(m):
                    ln = ax.plot(
                        np.asarray(e, dtype=float)[m], np.asarray(base, dtype=float)[m],
                        color=self.GUIDE_COLORS["e0"], lw=1.1, zorder=3,
                    )[0]
                    ln.set_gid(f"guide:E:{tag}")

        # Pre-edge window — thick blue dashed edges
        if "pre" in visible:
            _window("pre", "pre1", "pre2", self.GUIDE_COLORS["pre"], "--", "pre", "pre_edge")
        # Post-edge / norm window — thick green dash-dot edges
        if "norm" in visible:
            _window("norm", "norm1", "norm2", self.GUIDE_COLORS["norm"], "-.", "norm", "post_edge")
        # Generic click marks (not draggable)
        for m in marks or []:
            _vline(m, self.GUIDE_COLORS["mark"], ls=":", lw=1.8)

    def _e0_marker_y(self, ax, groups, current_id, guides) -> Optional[float]:
        """Y for the E0 dot: interpolate the guide-source curve at E0.

        Reads the already-drawn curve (gid ``group:<id>``) so the dot lands on
        whatever is displayed — μ / norm / flat / derivative alike.  Returns
        None when no usable curve is found (dot then parks at the panel top).
        """
        e0v = (guides or {}).get("e0")
        if e0v is None or not np.isfinite(e0v):
            return None
        gid = f"group:{current_id}" if current_id else (f"group:{groups[0].id}" if groups else None)
        if gid is None:
            return None
        for ln in ax.lines:
            if ln.get_gid() != gid:
                continue
            xd = np.asarray(ln.get_xdata(), dtype=float).ravel()
            yd = np.asarray(ln.get_ydata(), dtype=float).ravel()
            if xd.size < 2:
                return None
            order = np.argsort(xd)
            xs, ys = xd[order], yd[order]
            if xs[0] <= e0v <= xs[-1]:
                return float(np.interp(e0v, xs, ys))
            return None
        return None

    def _draw_analysis_overlay(self, ax, overlay: dict) -> None:
        """Draw larch LCF/PCA/pre-peak data + fit (+ components) on the E panel."""
        if not overlay:
            return
        x = np.asarray(overlay.get("x") if overlay.get("x") is not None else [], dtype=float).ravel()
        ydata = np.asarray(
            overlay.get("ydata") if overlay.get("ydata") is not None else [], dtype=float
        ).ravel()
        yfit = np.asarray(
            overlay.get("yfit") if overlay.get("yfit") is not None else [], dtype=float
        ).ravel()
        if x.size < 2:
            return
        title = str(overlay.get("title") or overlay.get("kind") or "analysis")
        if ydata.size >= 2:
            n = min(x.size, ydata.size)
            ax.plot(x[:n], ydata[:n], color="#202020", lw=1.5, label=f"{title} data", zorder=5)
        if yfit.size >= 2:
            n = min(x.size, yfit.size)
            ax.plot(
                x[:n],
                yfit[:n],
                color="#e64626",
                lw=1.6,
                ls="-",
                label=f"{title} fit",
                zorder=6,
            )
        ycomps = overlay.get("ycomps") or {}
        if isinstance(ycomps, dict):
            # Distinct hues for components (keep ochre for total fit)
            palette = ("#1a6fb5", "#1a8f4a", "#9b59b6", "#e67e22", "#16a085", "#c0392b")
            for i, (name, arr) in enumerate(ycomps.items()):
                yy = np.asarray(arr, dtype=float).ravel()
                if yy.size < 2:
                    continue
                n = min(x.size, yy.size)
                ax.plot(
                    x[:n],
                    yy[:n],
                    color=palette[i % len(palette)],
                    lw=1.1,
                    ls="--",
                    alpha=0.9,
                    label=str(name),
                    zorder=5,
                )
        # Labels stay on the artists; the shared clickable overlay legend
        # (upper right, out of tight_layout) is applied once at the end.

    def _draw_k(self, ax, g, a, kw, label, vr=None):
        if a.k is None or a.chi is None:
            return
        n = min(len(a.k), len(a.chi))
        curve = ax.plot(a.k[:n], a.chi[:n] * (a.k[:n] ** kw), lw=1.0, label=label, alpha=0.9)[0]
        curve.set_gid(f"group:{g.id}")
        ax.set_title("k-space" + (f" (k^{kw}·χ)" if kw else " (χ)"))
        ax.set_xlabel("k (Å⁻¹)")
        if vr and vr.get("k"):
            ax.set_xlim(*vr["k"])
        ax.grid(True, alpha=0.3)

    def _draw_R(self, ax, g, a, label, vr=None):
        if a.r is None or a.chir_mag is None:
            return
        n = min(len(a.r), len(a.chir_mag))
        curve = ax.plot(a.r[:n], a.chir_mag[:n], lw=1.0, label=label, alpha=0.9)[0]
        curve.set_gid(f"group:{g.id}")
        lo, hi = (vr or {}).get("R", (0, 6))
        ax.set_xlim(lo, hi)
        if getattr(a, "phase_corrected", False):
            ax.set_title("R-space (phase corr.)")
        else:
            ax.set_title("R-space")
        ax.set_xlabel("R (Å)")
        ax.grid(True, alpha=0.3)

    def _draw_span(self, ax, lo: float, hi: float, color: str, label: Optional[str] = None):
        """Highlight a selected data window [lo, hi] with soft fill + bold edges.
        Returns (span_poly, line_lo, line_hi) so the edges stay draggable."""
        if lo is None or hi is None:
            return None
        try:
            lo_f, hi_f = float(lo), float(hi)
        except (TypeError, ValueError):
            return None
        if not (np.isfinite(lo_f) and np.isfinite(hi_f)):
            return None
        if hi_f < lo_f:
            lo_f, hi_f = hi_f, lo_f
        if hi_f - lo_f < 1e-9:
            return None
        poly = ax.axvspan(lo_f, hi_f, color=color, alpha=0.16, zorder=0, lw=0)
        l_lo = ax.axvline(lo_f, color=color, ls="-", lw=2.0, alpha=0.9, zorder=4)
        l_hi = ax.axvline(hi_f, color=color, ls="-", lw=2.0, alpha=0.9, zorder=4, label=label)
        # Shared gid so the legend entry toggles the WHOLE window (span fill +
        # both edge lines), not just the one labelled line (v0.34.11 bug).
        if label:
            gid = f"win:{label}"
            poly.set_gid(gid)
            l_lo.set_gid(gid)
            l_hi.set_gid(gid)
        return poly, l_lo, l_hi

    def _draw_q(self, ax, g, a, label, vr=None):
        # Re χ(q) is the informative part of the back-transform — plain |χ(q)|
        # is of little use (user feedback, v0.7.0)
        if a.q is not None and a.chiq_re is not None:
            n = min(len(a.q), len(a.chiq_re))
            curve = ax.plot(a.q[:n], a.chiq_re[:n], lw=0.9, label=label, alpha=0.9)[0]
            curve.set_gid(f"group:{g.id}")
            ax.set_title("q-space (Re χ(q))")
        elif a.k is not None and a.chi is not None:
            n = min(len(a.k), len(a.chi))
            curve = ax.plot(a.k[:n], a.chi[:n], lw=0.9, label=label, alpha=0.75)[0]
            curve.set_gid(f"group:{g.id}")
            ax.set_title("q-space (χ(k) fallback)")
        else:
            return
        ax.set_xlabel("q (Å⁻¹)")
        if vr and vr.get("q"):
            ax.set_xlim(*vr["q"])
        ax.grid(True, alpha=0.3)

    def _draw_kq(self, ax, g, a, kw, label, vr=None):
        """Combined view: k^w·χ(k) on the bottom axis + Re χ(q) on the top axis."""
        if a.k is None or a.chi is None:
            return
        n = min(len(a.k), len(a.chi))
        curve = ax.plot(a.k[:n], a.chi[:n] * (a.k[:n] ** kw), lw=1.0, label=label, alpha=0.9)[0]
        curve.set_gid(f"group:{g.id}")
        ax.set_xlabel("k (Å⁻¹)")
        ax.set_title("k + Re χ(q)" + (f" (k^{kw}·χ)" if kw else " (χ)"))
        ax.grid(True, alpha=0.3)
        if vr and vr.get("k"):
            ax.set_xlim(*vr["k"])
        if a.q is not None and a.chiq_re is not None:
            twin = self._kq_twins.get(id(ax))
            if twin is None:
                twin = ax.twiny()
                ax._kq_twin = twin
                twin._kq_main = ax
                self._kq_twins[id(ax)] = twin
            nq = min(len(a.q), len(a.chiq_re))
            q_curve = twin.plot(
                a.q[:nq], a.chiq_re[:nq], lw=0.9, color="#e64626", alpha=0.85,
                label=(label + " Reχ(q)") if label else None,
            )[0]
            q_curve.set_gid(f"group:{g.id}")
            twin.set_xlabel("q (Å⁻¹)")
            # larch xftr's q IS on the k scale ("uniform array of k") — lock the
            # top axis 1:1 onto the bottom k limits so features line up (the
            # default independent autoscale implied a wrong q=2k squeeze).
            twin.set_xlim(ax.get_xlim())

    @staticmethod
    def _plot_pair(ax, x, data, fit, *, title: str, xlabel: str, ylabel: str, data_label="data", fit_label="fit"):
        """Draw an exactly comparable data/model pair on one transform grid."""
        x = np.asarray(x, dtype=float)
        data = np.asarray(data, dtype=float)
        fit = np.asarray(fit, dtype=float)
        n = min(x.size, data.size, fit.size)
        if n <= 0:
            return
        ax.plot(x[:n], data[:n], color="#202020", lw=1.35, label=data_label)
        ax.plot(x[:n], fit[:n], color="#e64626", lw=1.35, label=fit_label)
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)

    def _draw_fit_rmr(self, ax, view: dict, *, suffix: str = ""):
        """Artemis-like Rmr: magnitude and real R components plus Re residual."""
        r = np.asarray(view.get("r", []), dtype=float)
        mag_data = np.asarray(view.get("data_rmag", []), dtype=float)
        mag_fit = np.asarray(view.get("fit_rmag", []), dtype=float)
        re_data = np.asarray(view.get("data_rre", []), dtype=float)
        re_fit = np.asarray(view.get("fit_rre", []), dtype=float)
        resid = np.asarray(view.get("residual_rre", []), dtype=float)
        n = min(r.size, mag_data.size, mag_fit.size, re_data.size, re_fit.size, resid.size)
        if n <= 0:
            return
        r, mag_data, mag_fit = r[:n], mag_data[:n], mag_fit[:n]
        re_data, re_fit, resid = re_data[:n], re_fit[:n], resid[:n]
        scale = max(float(np.nanmax(np.abs(np.r_[mag_data, mag_fit, re_data, re_fit]))), 1e-12)
        offset = 1.15 * scale
        ax.plot(r, mag_data, color="#202020", lw=1.35, label="|data R|")
        ax.plot(r, mag_fit, color="#e64626", lw=1.35, label="|fit R|")
        ax.plot(r, re_data - offset, color="#285f9e", lw=1.05, label="Re data R (offset)")
        ax.plot(r, re_fit - offset, color="#bc6c25", lw=1.05, label="Re fit R (offset)")
        ax.plot(r, resid - offset, color="#198754", ls="--", lw=1.0, label="Re residual (offset)")
        ax.axhline(0, color="#888", lw=0.6, alpha=0.6)
        ax.axhline(-offset, color="#888", lw=0.6, alpha=0.5)
        ax.set_xlim(left=0)
        ax.set_title("Rmr: magnitude + real R" + suffix)
        ax.set_xlabel("R (Å)")
        ax.set_ylabel("FT amplitude")
        ax.grid(True, alpha=0.3)

    def _draw_fit_special(self, kind: str, view: dict, all_views: dict):
        """Display-only Artemis-style views from one completed fit result."""
        if not view:
            return
        if kind == "k123":
            ax = self.axes.get("fit_k123")
            if ax is None:
                return
            ordered = [w for w in (1, 2, 3) if w in all_views] or sorted(all_views)
            base = max(
                [float(np.nanmax(np.abs(np.r_[v["data_kchi"], v["fit_kchi"]]))) for v in all_views.values()] or [1.0]
            )
            for idx, w in enumerate(ordered):
                v = all_views[w]
                k = np.asarray(v["k"])
                d, f = np.asarray(v["data_kchi"]), np.asarray(v["fit_kchi"])
                n = min(k.size, d.size, f.size)
                offset = (idx - (len(ordered) - 1) / 2.0) * base * 2.3
                ax.plot(k[:n], d[:n] + offset, color="#202020", lw=1.1, label=f"data k{w}" if idx == 0 else None)
                ax.plot(k[:n], f[:n] + offset, color="#e64626", lw=1.1, label=f"fit k{w}" if idx == 0 else None)
                ax.text(0.99, 0.94 - idx * 0.08, f"k{w}", transform=ax.transAxes, ha="right", va="top", fontsize=8)
            ax.set_title("k123: all requested k-weights")
            ax.set_xlabel("k (Å⁻¹)")
            ax.set_ylabel("kᵂ χ(k), offset")
            ax.grid(True, alpha=0.3)
        elif kind == "r123":
            ax = self.axes.get("fit_r123")
            if ax is None:
                return
            ordered = [w for w in (1, 2, 3) if w in all_views] or sorted(all_views)
            base = max(
                [float(np.nanmax(np.abs(np.r_[v["data_rmag"], v["fit_rmag"]]))) for v in all_views.values()] or [1.0]
            )
            for idx, w in enumerate(ordered):
                v = all_views[w]
                r, d, f = np.asarray(v["r"]), np.asarray(v["data_rmag"]), np.asarray(v["fit_rmag"])
                n = min(r.size, d.size, f.size)
                offset = (idx - (len(ordered) - 1) / 2.0) * base * 2.3
                ax.plot(r[:n], d[:n] + offset, color="#202020", lw=1.1, label="|data R|" if idx == 0 else None)
                ax.plot(r[:n], f[:n] + offset, color="#e64626", lw=1.1, label="|fit R|" if idx == 0 else None)
                ax.text(0.99, 0.94 - idx * 0.08, f"R k{w}", transform=ax.transAxes, ha="right", va="top", fontsize=8)
            ax.set_xlim(left=0)
            ax.set_title("R123: all requested k-weights")
            ax.set_xlabel("R (Å)")
            ax.set_ylabel("|χ(R)|, offset")
            ax.grid(True, alpha=0.3)
        elif kind == "rmr":
            self._draw_fit_rmr(self.axes.get("fit_rmr"), view)
        elif kind == "rk":
            self._plot_pair(
                self.axes["fit_top"], view["k"], view["data_kchi"], view["fit_kchi"],
                title="Rk: k-space", xlabel="k (Å⁻¹)", ylabel="kᵂ χ(k)",
            )
            self._draw_fit_rmr(self.axes["fit_bottom"], view, suffix=" (R component)")
        elif kind == "kq":
            self._plot_pair(
                self.axes["fit_top"], view["k"], view["data_kchi"], view["fit_kchi"],
                title="kq: k-space", xlabel="k (Å⁻¹)", ylabel="kᵂ χ(k)",
            )
            self._plot_pair(
                self.axes["fit_bottom"], view["q"], view["data_qre"], view["fit_qre"],
                title="kq: real q-space", xlabel="q (Å⁻¹)", ylabel="Re χ(q)",
            )
        for ax in self.axes.values():
            h, lab = ax.get_legend_handles_labels()
            if h:
                self._add_clickable_legend(ax, h, lab)

    def draw_groups(
        self,
        groups: List[GroupRecord],
        mode: str,
        show: dict,
        kw: int = 2,
        emin: float = -200,
        emax: float = 800,
        marks: Optional[List[float]] = None,
        fit: Optional[FitResult] = None,
        fit_views: Optional[dict] = None,
        fit_weight: Optional[int] = None,
        guides: Optional[dict] = None,
        current_id: Optional[str] = None,
        windows: Optional[dict] = None,
        view_ranges: Optional[dict] = None,
        energy_point_mode: bool = False,
        deglitch_selected_energy: Optional[float] = None,
        analysis_overlay: Optional[dict] = None,
        visible_e_windows: Optional[set] = None,
    ):
        self.clear()
        self._guide_artists = {}
        self._span_artists = {}
        self._span_edges = {}
        marks = marks or []
        windows = windows or {}
        fit_views = fit_views or {}
        analysis_overlay = analysis_overlay or None
        if self.mode.startswith("fit_"):
            kind = self.mode.removeprefix("fit_")
            shown_views = (
                {int(fit_weight): fit_views[int(fit_weight)]}
                if fit_weight is not None and int(fit_weight) in fit_views
                else fit_views
            )
            selected = shown_views.get(int(kw)) or (next(iter(shown_views.values())) if shown_views else {})
            self._draw_fit_special(kind, selected, shown_views)
            self.set_guide_targets({})
            self.draw_idle()
            return
        n_groups = len(groups)
        for g in groups:
            sess = g.ensure_session()
            a = sess.arrays
            if a.energy is None or a.mu is None:
                if g.energy is not None:
                    g.process()
                    a = g.session.arrays
                else:
                    continue
            # Every displayed curve needs an identity.  The old >4 shortcut
            # removed all labels, leaving multi-display curves anonymous.
            label = g.name
            # Only the current curve gets raw-vs-shifted overlay (avoids clutter)
            compare = bool(current_id and g.id == current_id)
            if "E" in self.axes:
                self._draw_E(
                    self.axes["E"], g, a, show, emin, emax, label, n_groups,
                    draw_shift_compare=compare,
                    point_mode=energy_point_mode,
                    deglitch_selected_energy=(
                        deglitch_selected_energy if compare else None
                    ),
                )
            if "k" in self.axes:
                self._draw_k(self.axes["k"], g, a, kw, label, vr=view_ranges)
            if "R" in self.axes:
                self._draw_R(self.axes["R"], g, a, label, vr=view_ranges)
            if "q" in self.axes:
                self._draw_q(self.axes["q"], g, a, label, vr=view_ranges)
            if "kq" in self.axes:
                self._draw_kq(self.axes["kq"], g, a, kw, label, vr=view_ranges)

        # LCF / PCA / pre-peak: overlay on E (default) or k when space=='k'
        if analysis_overlay:
            space = str(analysis_overlay.get("space") or "E").lower()
            if space == "k" and "k" in self.axes:
                self._draw_analysis_overlay(self.axes["k"], analysis_overlay)
            elif "E" in self.axes:
                self._draw_analysis_overlay(self.axes["E"], analysis_overlay)

        # Guides / bands once on the Energy panel (not per-spectrum); every
        # panel's draggable lines feed the per-panel target registry.
        targets: dict = {}
        if "E" in self.axes:
            vis_e = set(visible_e_windows or ())
            # Window chrome is bound to the display mode (v0.35.8):
            # derivative view → no pre/norm chrome at all; normalized/flat →
            # band + edges but no baseline (the fitted lines are μ-scale);
            # raw μ(E) → everything.
            deriv = bool(show.get("deriv1") or show.get("deriv2"))
            if deriv:
                vis_e = set()
            draw_baseline = not (
                show.get("normalized") or show.get("deriv1") or show.get("deriv2")
            )
            baseline_arrays = None
            if vis_e:
                # baseline segments come from the guide-source group (current
                # when plotted, else the first plotted — mirrors _energy_guides)
                src = next(
                    (g for g in groups if current_id and g.id == current_id),
                    groups[0] if groups else None,
                )
                if src is not None:
                    try:
                        baseline_arrays = src.ensure_session().arrays
                    except Exception:
                        baseline_arrays = None
            self._draw_E_guides(
                self.axes["E"],
                guides,
                marks,
                e0_y=self._e0_marker_y(self.axes["E"], groups, current_id, guides),
                visible=vis_e,
                baseline_arrays=baseline_arrays,
                draw_baseline=draw_baseline,
            )
            # Only armed (visible) windows are drag targets; E0 never is
            keep = set()
            if "pre" in vis_e:
                keep |= {"pre1", "pre2"}
            if "norm" in vis_e:
                keep |= {"norm1", "norm2"}
            targets["E"] = {k: v for k, v in (guides or {}).items() if k in keep}

        # Selected k / R / bkg-k windows (when user ticks "span" next to ranges)
        if "k" in self.axes:
            if windows.get("k"):
                lo, hi = windows["k"]
                arts = self._draw_span(self.axes["k"], lo, hi, "#e67e22", label="k-win")
                if arts:
                    poly, l_lo, l_hi = arts
                    self._span_artists[("k", "ft")] = poly
                    self._span_edges[("k", "ft")] = ("kmin", "kmax")
                    self._guide_artists[("k", "kmin")] = [l_lo]
                    self._guide_artists[("k", "kmax")] = [l_hi]
                    targets.setdefault("k", {}).update({"kmin": float(lo), "kmax": float(hi)})
            if windows.get("k_bkg"):
                lo, hi = windows["k_bkg"]
                arts = self._draw_span(self.axes["k"], lo, hi, "#8e44ad", label="bkg-k")
                if arts:
                    poly, l_lo, l_hi = arts
                    self._span_artists[("k", "bkg")] = poly
                    self._span_edges[("k", "bkg")] = ("bkmin", "bkmax")
                    self._guide_artists[("k", "bkmin")] = [l_lo]
                    self._guide_artists[("k", "bkmax")] = [l_hi]
                    targets.setdefault("k", {}).update({"bkmin": float(lo), "bkmax": float(hi)})
        if "R" in self.axes and windows.get("r"):
            lo, hi = windows["r"]
            arts = self._draw_span(self.axes["R"], lo, hi, "#16a085", label="R-win")
            if arts:
                poly, l_lo, l_hi = arts
                self._span_artists[("R", "r")] = poly
                self._span_edges[("R", "r")] = ("rmin", "rmax")
                self._guide_artists[("R", "rmin")] = [l_lo]
                self._guide_artists[("R", "rmax")] = [l_hi]
                targets["R"] = {"rmin": float(lo), "rmax": float(hi)}
        self.set_guide_targets(targets)

        # Fit overlay once (not per group).  These arrays are transformed from
        # the identical raw data/model pair at the *display* k-weight, unlike
        # feffit output which contains only its first fitting weight.
        view = fit_views.get(int(kw)) if fit_views else None
        if view and "k" in self.axes:
            axK = self.axes["k"]
            self._plot_pair(
                axK, view["k"], view["data_kchi"], view["fit_kchi"],
                title=f"k-space (k^{kw}χ)", xlabel="k (Å⁻¹)", ylabel=f"k^{kw}χ(k)",
                data_label="fit data", fit_label="fit model",
            )
        elif fit is not None and fit.ok and fit.fit_chi is not None and "k" in self.axes:
            fk = fit.fit_k if fit.fit_k is not None else fit.k
            if fk is not None:
                axK = self.axes["k"]
                nk = min(len(fk), len(fit.fit_chi))
                axK.plot(fk[:nk], fit.fit_chi[:nk] * (fk[:nk] ** kw), "r-", lw=1.1, label="fit")

        if view and "R" in self.axes:
            axR = self.axes["R"]
            r = np.asarray(view["r"])
            md, mf, rr = (np.asarray(view[k]) for k in ("data_rmag", "fit_rmag", "residual_rre"))
            n2 = min(r.size, md.size, mf.size, rr.size)
            axR.plot(r[:n2], md[:n2], color="#202020", lw=1.3, label="fit data |R|")
            axR.plot(r[:n2], mf[:n2], color="#e64626", lw=1.3, label="fit model |R|")
            axR.plot(r[:n2], rr[:n2], color="#198754", ls="--", lw=0.95, label="Re residual")
        elif fit is not None and fit.ok and fit.fit_chir_mag is not None and "R" in self.axes:
            fr = fit.fit_r if fit.fit_r is not None else (groups[0].session.arrays.r if groups else None)
            if fr is not None:
                axR = self.axes["R"]
                n2 = min(len(fr), len(fit.fit_chir_mag))
                axR.plot(fr[:n2], fit.fit_chir_mag[:n2], "r-", lw=1.1, label="fit")

        # Every panel with labelled artists gets its own clickable legend —
        # including Marked overlays with many spectra.  The old n>4 shortcut
        # collapsed grid mode to a single E-panel legend, leaving k/R/q
        # curves anonymous and untoggleable (v0.35.0).
        for ax in self.axes.values():
                h, lab = ax.get_legend_handles_labels()
                twin = getattr(ax, "_kq_twin", None)
                if twin is not None:
                    h2, lab2 = twin.get_legend_handles_labels()
                    h, lab = h + h2, lab + lab2
                if h:
                    self._add_clickable_legend(ax, h, lab)
        self.draw_idle()


def _dspin(lo, hi, step, val, dec=3, width: int | None = None) -> QDoubleSpinBox:
    """Numeric spin sized from font metrics so the number never meets the arrows.

    The QSS caps the up/down buttons at 16px inside an 18px right padding, so
    the chrome is deterministic: text area = width − ~28. Width is therefore
    widest renderable text + 36 slack. Minimum (not Fixed) so rows may stretch.
    """
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setValue(val)
    s.setKeyboardTracking(False)
    fm = s.fontMetrics()
    lo_t, hi_t = f"{lo:.{dec}f}", f"{hi:.{dec}f}"
    sample = lo_t if len(lo_t) >= len(hi_t) else hi_t
    s.setMinimumWidth(max(width or 0, fm.horizontalAdvance(sample) + 30))
    s.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    return s


def _plain_num(s: "QDoubleSpinBox") -> "QDoubleSpinBox":
    """Arrowless number box (v3.19.6, Rebin grid): type or wheel, no up/down.

    The app QSS reserves 18 px of right padding for the arrows on every spin
    box; without arrows that padding goes, so the box can be ~18 px narrower
    and two parameter pairs fit one row of the 372 px panel.
    """
    s.setButtonSymbols(s.ButtonSymbols.NoButtons)
    s.setStyleSheet("padding-right: 3px;")
    dec = s.decimals()
    sample = max((f"{s.minimum():.{dec}f}", f"{s.maximum():.{dec}f}"), key=len)
    s.setMinimumWidth(s.fontMetrics().horizontalAdvance(sample) + 16)
    return s


def _combo(items, editable=False, width: int = 72) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    c.addItems(items)
    c.setFixedWidth(width)
    c.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return c


def _row(*items) -> QWidget:
    """One horizontal control row; stretch at end so nothing piles up."""
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(8)
    for x in items:
        if isinstance(x, str):
            lab = QLabel(x)
            lab.setMinimumWidth(16)
            h.addWidget(lab)
        else:
            h.addWidget(x)
    h.addStretch(1)
    return host


def _even_row(*items) -> QWidget:
    """One horizontal row with evenly stretched cells (no leftover gap)."""
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(6)
    for x in items:
        h.addWidget(x, 1)
    return host


def _side_toggle_icon(point: str, dark: bool = False) -> "QIcon":
    """Minimal collapse glyph: a vertical bar with a small arrow (◀| or |▶)."""
    px = QPixmap(16, 16)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#cfd8e3" if dark else "#6b625d")
    p.setPen(QPen(color, 1.8))
    p.setBrush(QBrush(color))
    if point == "left":
        p.drawLine(3, 3, 3, 13)
        p.drawPolygon(QPolygonF([QPointF(12.5, 4.0), QPointF(6.5, 8.0), QPointF(12.5, 12.0)]))
    else:
        p.drawLine(13, 3, 13, 13)
        p.drawPolygon(QPolygonF([QPointF(3.5, 4.0), QPointF(9.5, 8.0), QPointF(3.5, 12.0)]))
    p.end()
    return QIcon(px)


# Action buttons in the side panels are short (v3.19.6): they all share one
# width (the widest text in the current language, see
# MainWindow._sync_action_btn_widths) so the radios line up module after
# module; this is only the floor.
ACTION_BTN_MIN_W = 56
# Right parameter panel minimum (v3.19.6, was 340): the one-row AI Check bar
# needs 347 px in English; 372 leaves visible gaps between its radios.
RIGHT_PANEL_MIN_W = 372


def _undo_icon(dark: bool = False) -> "QIcon":
    """Undo glyph (v3.19.6): a hook arrow pointing back, drawn so it needs no
    font glyph and follows the light/dark theme like _side_toggle_icon."""
    px = QPixmap(32, 32)
    px.setDevicePixelRatio(2.0)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#cfd8e3" if dark else "#6b625d")
    pen = QPen(color, 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(5.5, 5.5)
    path.lineTo(10.0, 5.5)
    path.cubicTo(14.0, 5.5, 14.0, 12.5, 10.0, 12.5)
    path.lineTo(6.0, 12.5)
    p.drawPath(path)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(2.0, 5.5), QPointF(6.0, 2.5), QPointF(6.0, 8.5)]))
    p.end()
    return QIcon(px)


def _action_scope_row(
    run_btn: "QPushButton",
    rad_current: "QRadioButton",
    rad_marked: "QRadioButton",
    rad_all: "QRadioButton",
    undo_btn: "Optional[QPushButton]" = None,
    extra: "Optional[QWidget]" = None,
) -> QWidget:
    """
    One row:  [Action]  ○ Current  ○ Marked  ○ All  [↶]  spread evenly
    v3.19.6: the action button is short (one shared width) instead of
    filling the row; the items are spread with equal gaps across the full
    width, so the radios form columns module after module and every ↶ sits
    at the right edge.
    """
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(4)
    run_btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    run_btn.setMinimumWidth(ACTION_BTN_MIN_W)
    h.addWidget(run_btn, 0)
    if extra is not None:  # e.g. Smooth's boxcar width, right after Apply
        h.addWidget(extra, 0)
    for r in (rad_current, rad_marked, rad_all):
        r.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        h.addStretch(1)
        h.addWidget(r, 0)
    if undo_btn is not None:
        h.addStretch(1)
        h.addWidget(undo_btn, 0)
    return host


def _button_row(*buttons) -> QWidget:
    """[button]…            [↶]: text buttons on the left at their own width,
    icon buttons pushed to the right edge like every other ↶ (v3.19.6)."""
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(4)
    pushed = False
    for b in buttons:
        # icon vs text by the icon, not the text: labels are set later in
        # _retranslate, so b.text() is still empty while the row is built
        if b.icon().isNull():
            b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        elif not pushed:
            h.addStretch(1)
            pushed = True
        h.addWidget(b, 0)
    if not pushed:
        h.addStretch(1)
    return host


class CollapsibleGroupBox(QWidget):
    """Module with optional L–R title rules only (no body box). Click title to toggle.

    framed=True  → ── Title ──
    framed=False → plain title only
    """

    def __init__(
        self,
        title: str = "",
        parent=None,
        *,
        expanded: bool = True,
        framed: bool = True,
    ):
        super().__init__(parent)
        self.setObjectName("collapsibleBox")
        self._base_title = title or ""
        self._expanded = bool(expanded)
        self._framed = bool(framed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 2)
        root.setSpacing(0)

        # Title row (optional L–R rules)
        self._hdr_row = QWidget(self)
        self._hdr_row.setObjectName("collapseHdrRow")
        self._hdr_row.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(self._hdr_row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self._line_l = QFrame(self._hdr_row)
        self._line_l.setObjectName("collapseRule")
        self._line_l.setFixedHeight(1)
        self._line_l.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hdr = QLabel(self._hdr_row)
        self._hdr.setObjectName("collapseHdr")
        self._hdr.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if not self._framed
            else Qt.AlignmentFlag.AlignCenter
        )
        self._hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        self._line_r = QFrame(self._hdr_row)
        self._line_r.setObjectName("collapseRule")
        self._line_r.setFixedHeight(1)
        self._line_r.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if self._framed:
            hl.addWidget(self._line_l, 1)
            hl.addWidget(self._hdr, 0)
            hl.addWidget(self._line_r, 1)
        else:
            self._line_l.hide()
            self._line_r.hide()
            hl.addWidget(self._hdr, 1)
        self._hdr_row.mousePressEvent = self._hdr_clicked  # type: ignore[method-assign]
        self._hdr.mousePressEvent = self._hdr_clicked  # type: ignore[method-assign]
        root.addWidget(self._hdr_row)

        self._body = QFrame(self)
        self._body.setObjectName("collapseBody" if self._framed else "collapseBodyPlain")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(4, 2, 4, 4)
        self._body_lay.setSpacing(2)
        root.addWidget(self._body)

        self._apply_expanded(self._expanded)

    def body_layout(self) -> QVBoxLayout:
        """Layout for module controls (same role as QVBoxLayout(QGroupBox))."""
        return self._body_lay

    def setTitle(self, title: str) -> None:
        self._base_title = title or ""
        self._refresh_header()

    def title(self) -> str:
        return self._base_title

    def isExpanded(self) -> bool:
        return self._expanded

    def setExpanded(self, expanded: bool) -> None:
        self._apply_expanded(bool(expanded))

    def _hdr_clicked(self, event) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._toggle()
            event.accept()

    def _toggle(self) -> None:
        self._apply_expanded(not self._expanded)

    def _apply_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._body.setVisible(expanded)
        self._refresh_header()

    def _refresh_header(self) -> None:
        self._hdr.setText(self._base_title)


def _btn_grid2x2(*buttons) -> QWidget:
    """2×2 button grid so bilingual labels fit in a narrow Fit column."""
    host = QWidget()
    g = QGridLayout(host)
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(4)
    g.setVerticalSpacing(2)
    for i, b in enumerate(buttons[:4]):
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        b.setMinimumHeight(22)
        g.addWidget(b, i // 2, i % 2)
    g.setColumnStretch(0, 1)
    g.setColumnStretch(1, 1)
    return host


# Larix ATHENA_CLAMPNAMES order — combo index ↔ autobk clamp weight
ATHENA_CLAMP_VALUES = (0, 1, 5, 20, 100)


def _clamp_index(value) -> int:
    """Nearest clamp-combo index for a stored numeric clamp weight."""
    try:
        v = float(value)
    except Exception:
        return 0
    return min(range(len(ATHENA_CLAMP_VALUES)), key=lambda i: abs(ATHENA_CLAMP_VALUES[i] - v))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.paths = project_paths()
        from .version import app_settings

        self.store = app_settings()
        self.i18n = I18n(str(self.store.value("lang", "zh")))
        self.project = AthenaProject("Untitled")
        self.energy_marks: List[float] = []  # click marks on E plot
        self._deglitch_selected_energy: Optional[float] = None
        self.click_mode = "none"  # none | e0 | pre1 | pre2 | norm1 | norm2 | deglitch
        # Armed 📌 pins auto-disarm after 15 s idle (v0.35.5); a 1 s ticker
        # shows the remaining time on the plot.  Created here because
        # _build_ui() below already calls _set_click_mode.
        self._pin_deadline = 0.0
        self._pin_countdown_timer = QTimer(self)
        self._pin_countdown_timer.setInterval(1000)
        self._pin_countdown_timer.timeout.connect(self._pin_countdown_tick)
        self._deglitch_point_plot_active = False
        self.last_fit: Optional[FitResult] = None
        # LCF / PCA / pre-peak overlay on E plot (from larch analysis results)
        self._analysis_overlay: Optional[dict] = None
        self.worker: Optional[Worker] = None
        self._worker_busy = False  # True while a Worker thread runs larch (blocks auto-reprocess)
        self._ai_workers: list = []  # AI Check threads: a subprocess or HTTP call, never larch
        self._ai_check_run: Optional[dict] = None
        self._theme_dark = False  # updated by _apply_theme; drives group-list mark colors
        # Mark-state icons for the group list (plain icons — no native checkbox)
        _assets = Path(__file__).resolve().parent / "assets"
        self._icon_checked = QIcon(str(_assets / "checkbox_checked.svg"))
        self._icon_unchecked = QIcon(str(_assets / "checkbox_empty.svg"))
        self._block = False
        self._select_seq = 0  # invalidates stale deferred select work
        self._tray: Optional[QSystemTrayIcon] = None
        self._force_quit = False
        self._pending_update: Optional[dict] = None  # {version, path, kind}
        # Per-module undo: last action's group snapshots (+ optional added groups / fit UI)
        self._module_undo: dict = {}
        self._undo_buttons: dict = {}  # module key → QPushButton
        # module key → {current, marked, all: QRadioButton}
        self._scope_radios: dict = {}
        self._action_btns: list = []  # short action buttons sharing one width (v3.19.6)
        # v3.19.5 import batch pipeline: spec per imported path + the
        # "auto-process after import" request for the current open flow
        self._import_specs: dict = {}
        self._batch_requested = False
        self._batch_result_dialog = None
        # per-panel view ranges for the toolbar range spins (E lives in sp_emin/sp_emax)
        self._view_ranges = {"k": (0.0, 15.0), "R": (0.0, 6.0), "q": (0.0, 30.0)}
        # intended side-panel visibility (isVisible() lies before show())
        self._left_visible = True
        self._right_visible = True

        # No top menu bar (functions live in the panels); status line is a
        # custom band between log and footer (not Qt's bottom statusBar)
        self.menuBar().setVisible(False)
        self.statusBar().setVisible(False)
        self._build_ui()
        self._retranslate()
        # follow Windows light/dark mode, live-switch on system change
        self._apply_theme()
        try:
            QApplication.styleHints().colorSchemeChanged.connect(self._apply_theme)
        except Exception:
            pass
        self._setup_tray()
        self._status(f"smartLarch Platform v{APP_VERSION}")

        # auto reprocess timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._apply_current)

        # Auto-update plugins + main program a few seconds after start
        QTimer.singleShot(2000, self._startup_update_scan)

    # ========== i18n helpers ==========
    def _bi(self, zh: str, en: str) -> str:
        """Bilingual inline string for dynamic messages."""
        return zh if self.i18n.lang == "zh" else en

    # ========== Main layout ==========
    def _build_ui(self):
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1440, 880)
        self.setMinimumSize(1180, 700)

        # Apply the stylesheet FIRST: _dspin measures sizeHint with the styled
        # metrics (capped 16px arrow buttons) — measuring before the QSS lands
        # would size boxes for the native style's oversized touch buttons.
        self.setStyleSheet(_APP_QSS)

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ================= HEADER BAND =================
        # [accent] [Larch logo] [title]  ……  [app icon] [⚙] [?] [lang]
        header = QFrame()
        header.setObjectName("headerBand")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(12, 10, 12, 10)
        hh.setSpacing(10)
        accent = QFrame()
        accent.setFixedWidth(6)
        accent.setStyleSheet("background:#e64626; border-radius:3px;")
        hh.addWidget(accent)

        assets = Path(__file__).resolve().parent / "assets"
        # App icon beside the app title (About uses the same G icon;
        # in Settings → About)
        self._logo_header = QLabel()
        self._logo_header.setStyleSheet("background:transparent;")
        self._logo_header.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        icon_fp = assets / "app_icon_g.png"
        if icon_fp.is_file():
            pm = QPixmap(str(icon_fp))
            if not pm.isNull():
                # align with the two-line title text height on the right
                self._logo_header.setPixmap(
                    pm.scaled(
                        160,
                        58,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        hh.addWidget(self._logo_header)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.setContentsMargins(2, 0, 0, 0)
        self.lbl_app_title = QLabel()
        self.lbl_app_title.setStyleSheet("font-size:20px; font-weight:700; color:#242424;")
        self.lbl_app_sub = QLabel()
        self.lbl_app_sub.setStyleSheet("font-size:11px; color:#6b625d;")
        title_col.addWidget(self.lbl_app_title)
        title_col.addWidget(self.lbl_app_sub)
        hh.addLayout(title_col, 1)

        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("iconBtn")
        self.btn_settings.setFixedSize(32, 32)
        self.btn_settings.clicked.connect(self._open_settings)
        self.btn_user_guide = QPushButton("?")
        self.btn_user_guide.setObjectName("iconBtn")
        self.btn_user_guide.setFixedSize(32, 32)
        self.btn_user_guide.clicked.connect(self._open_user_guide)
        # File tools live right-aligned on the header row, left of ⚙ — no
        # separate toolbar band (saves a full row of vertical space).
        self.btn_open = QPushButton()
        self.btn_open.setObjectName("openMenuBtn")
        self.btn_open.setFixedWidth(84)
        open_menu = QMenu(self.btn_open)
        self.act_open_replace = open_menu.addAction("")
        self.act_open_append = open_menu.addAction("")
        self.act_open_replace.triggered.connect(lambda: self._open_files(append=False))
        self.act_open_append.triggered.connect(lambda: self._open_files(append=True))
        self.btn_open.setMenu(open_menu)
        self.cmb_quick_prj = QComboBox()
        self.cmb_quick_prj.setFixedWidth(180)
        self.cmb_quick_prj.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.cmb_quick_prj.activated.connect(self._quick_prj_activated)
        self.btn_quick_prj_dir = QToolButton()
        self.btn_quick_prj_dir.setText("📂")
        self.btn_quick_prj_dir.setFixedSize(26, 24)
        self.btn_quick_prj_dir.setAutoRaise(True)
        self.btn_quick_prj_dir.setStyleSheet(
            "QToolButton { background: #fff; border: 1px solid #ccc; border-radius: 3px; }"
            "QToolButton:hover { border-color: #e64626; }"
        )
        self.btn_quick_prj_dir.clicked.connect(self._choose_quick_prj_dir)
        # current group's folder — fills the middle of the header row
        self.lbl_dir = QLabel("")
        self.lbl_dir.setStyleSheet("color:#6b625d; font-size:11px;")
        self.lbl_dir.setMinimumWidth(0)
        self.lbl_dir.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Elide on the LEFT to the real pixel width: right end (the meaningful
        # tail) is always fully visible and never clipped against the buttons.
        # (Qt < 6.7 has no QLabel.setTextElideMode, so elide via font metrics
        # in _elide_dir_label + a resize event filter.)
        self._dir_full = ""
        self.lbl_dir.installEventFilter(self)
        self.lbl_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_save = QPushButton()
        self.btn_save.setObjectName("ghostBtn")
        self.btn_save.setFixedWidth(60)
        self.btn_save.clicked.connect(self._save_snapshot)
        hh.addWidget(self.lbl_dir, 1)
        hh.addWidget(self.btn_open)
        hh.addWidget(self.cmb_quick_prj)
        hh.addWidget(self.btn_quick_prj_dir)
        hh.addWidget(self.btn_save)
        hh.addWidget(self.btn_settings)
        hh.addWidget(self.btn_user_guide)
        v.addWidget(header)

        self.setAcceptDrops(True)

        # ================= MAIN 3-COLUMN SPLIT =================
        split = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter = split
        v.addWidget(split, 1)

        # ---------- LEFT: groups ----------
        left = QWidget()
        left.setObjectName("panelLeft")
        self._left_panel = left
        left.setMinimumWidth(260)
        left.setMaximumWidth(330)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 6, 4, 6)
        lv.setSpacing(4)

        # Keep all group controls in the same compact header row so the list
        # can use the full remaining height beside the central data display.
        self.groups_header_layout = QHBoxLayout()
        self.groups_header_layout.setContentsMargins(0, 0, 0, 0)
        self.groups_header_layout.setSpacing(4)
        self.lbl_groups = QLabel()
        self.lbl_groups.setStyleSheet("font-weight:600;")
        self.groups_header_layout.addWidget(self.lbl_groups)
        self.groups_header_layout.addStretch(1)  # everything else right-aligned
        self.cmb_curve_element = QComboBox()
        self.cmb_curve_element.setFixedWidth(46)
        self.cmb_curve_element.currentIndexChanged.connect(self._curve_element_changed)
        self.groups_header_layout.addWidget(self.cmb_curve_element)
        self.btn_all = QToolButton()
        self.btn_none = QToolButton()
        self.btn_inv = QToolButton()
        for b in (self.btn_all, self.btn_none, self.btn_inv):
            b.setFixedSize(22, 22)
            b.setAutoRaise(True)
            b.setStyleSheet(
                "QToolButton { background: #fff; border: 1px solid #ccc; "
                "border-radius: 3px; font-size: 12px; font-weight: 700; color: #555; padding: 0; }"
                "QToolButton:hover { border-color: #e64626; color: #e64626; }"
            )
        self.btn_all.clicked.connect(lambda: self._mark_all(True))
        self.btn_none.clicked.connect(lambda: self._mark_all(False))
        self.btn_inv.clicked.connect(self._invert_marks)
        for b in (self.btn_all, self.btn_none, self.btn_inv):
            self.groups_header_layout.addWidget(b)
        # Freeze is a per-group (file-level) op — it belongs with the group
        # list, not inside Energy calibration.
        self.chk_freeze = QCheckBox()
        self.chk_freeze.toggled.connect(self._on_freeze_toggled)
        self.groups_header_layout.addWidget(self.chk_freeze)
        lv.addLayout(self.groups_header_layout)

        self.list_groups = QListWidget()
        self.list_groups.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_groups.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_groups.setWordWrap(False)
        self.list_groups.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list_groups.setIconSize(QSize(18, 18))
        # drag-to-reorder: visual order is mirrored back into project.groups
        self.list_groups.setDragEnabled(True)
        self.list_groups.setDropIndicatorShown(True)
        self.list_groups.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_groups.model().rowsMoved.connect(self._sync_group_order)
        self.list_groups.itemClicked.connect(self._group_item_clicked)
        self.list_groups.currentItemChanged.connect(self._group_selected)
        self.list_groups.itemSelectionChanged.connect(self._clear_transient_selection)
        self.list_groups.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_groups.customContextMenuRequested.connect(self._show_group_context_menu)
        self.group_context_menu = QMenu(self.list_groups)
        # alphabetical by English label: Close / Remove all / Remove current /
        # Remove selected / Rename
        self.act_close_file = self.group_context_menu.addAction("")
        self.act_remove_all = self.group_context_menu.addAction("")
        self.act_remove_current = self.group_context_menu.addAction("")
        self.act_remove_marked = self.group_context_menu.addAction("")
        self.act_rename_current = self.group_context_menu.addAction("")
        self.act_close_file.triggered.connect(self._close_current_file)
        self.act_remove_all.triggered.connect(self._remove_all_groups)
        self.act_remove_current.triggered.connect(self._remove_current)
        self.act_remove_marked.triggered.connect(self._remove_marked)
        self.act_rename_current.triggered.connect(self._rename_current)
        lv.addWidget(self.list_groups, 1)
        # Left column: group list only (current-group meta moved above center plot)
        split.addWidget(left)
        self._left_panel = left

        # ---------- CENTER: single top toolbar row (right-aligned) + plot ----------
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(4, 6, 4, 6)
        cv.setSpacing(4)

        def _vsep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setStyleSheet("color:#d0c8c0;")
            return f

        # All plot tools + apply in ONE row, right-aligned
        tools_row = QHBoxLayout()
        tools_row.setContentsMargins(0, 0, 2, 0)
        tools_row.setSpacing(4)
        # panel collapse toggle at the plot's top-left corner
        self.btn_toggle_left = QToolButton()
        self.btn_toggle_left.setIcon(_side_toggle_icon("left"))
        self.btn_toggle_left.setIconSize(QSize(16, 16))
        self.btn_toggle_left.setFixedSize(22, 24)
        self.btn_toggle_left.setAutoRaise(True)
        self.btn_toggle_left.clicked.connect(lambda: self._toggle_side_panel("left"))
        tools_row.addWidget(self.btn_toggle_left)
        tools_row.addStretch(1)
        self._space_btns: dict[str, QToolButton] = {}
        self._space_group = QButtonGroup(self)
        self._space_group.setExclusive(True)
        for name in ("grid", "E", "k", "R", "q", "kq", "wt"):
            b = QToolButton()
            b.setText(
                "2×2" if name == "grid"
                else ("k+q" if name == "kq" else ("WT" if name == "wt" else name))
            )
            b.setCheckable(True)
            b.setFixedSize(34 if name in ("grid", "kq") else (30 if name == "wt" else 28), 24)
            b.clicked.connect(lambda *_a, n=name: self._focus_panel(n))
            self._space_group.addButton(b)
            self._space_btns[name] = b
            tools_row.addWidget(b)
        self._space_btns["grid"].setChecked(True)
        tools_row.addWidget(_vsep())
        # view-range spins follow the focused panel (E/k/R/q)
        self.lbl_view_range = QLabel()
        self.sp_view_min = _dspin(-2000, 5000, 10, -200, 1, width=64)
        self.sp_view_max = _dspin(-2000, 5000, 10, 800, 1, width=64)
        self.sp_view_min.valueChanged.connect(self._view_range_changed)
        self.sp_view_max.valueChanged.connect(self._view_range_changed)
        tools_row.addWidget(self.lbl_view_range)
        tools_row.addWidget(self.sp_view_min)
        tools_row.addWidget(self.sp_view_max)
        tools_row.addWidget(_vsep())
        self.rad_plot_current = QRadioButton()
        self.rad_plot_marked = QRadioButton()
        self.rad_plot_marked.setChecked(True)
        self._plot_scope_group = QButtonGroup(self)
        self._plot_scope_group.addButton(self.rad_plot_current)
        self._plot_scope_group.addButton(self.rad_plot_marked)
        # Switching the Current/Marked scope changes the plotted set — re-frame
        self.rad_plot_current.toggled.connect(lambda on: self._replot(preserve_view=False) if on else None)
        self.rad_plot_marked.toggled.connect(lambda on: self._replot(preserve_view=False) if on else None)
        tools_row.addWidget(self.rad_plot_current)
        tools_row.addWidget(self.rad_plot_marked)
        tools_row.addWidget(_vsep())
        self.chk_mu = QCheckBox("μ(E)")
        self.chk_mu.setChecked(True)
        self.chk_norm = QCheckBox()
        self.chk_bkg = QCheckBox("bkg")
        self.chk_pre = QCheckBox("pre")
        self.chk_post = QCheckBox("post")
        self.chk_d1 = QCheckBox("dμ/dE")
        self.chk_d2 = QCheckBox("d²μ")
        self._curve_updating = False
        for w in (self.chk_mu, self.chk_norm, self.chk_pre, self.chk_post, self.chk_bkg, self.chk_d1, self.chk_d2):
            w.toggled.connect(self._curve_boxes_changed)
            tools_row.addWidget(w)
        tools_row.addWidget(_vsep())
        # Only clear marks / reset on the plot toolbar (deglitch controls live in Tools)
        self.btn_clear_marks = QPushButton()
        self.btn_clear_marks.setObjectName("ghostBtn")
        self.btn_clear_marks.clicked.connect(self._clear_marks_and_reset)
        tools_row.addWidget(self.btn_clear_marks)
        tools_row.addWidget(_vsep())
        self.chk_auto = QCheckBox()
        # default on; the toggle lives in Settings (most users never touch it)
        self.chk_auto.setChecked(
            str(self.store.value("auto_reprocess", "true")).strip().lower()
            not in ("0", "false", "no", "off")
        )
        self.chk_auto.setVisible(False)
        self.chk_auto.toggled.connect(
            lambda on: self.store.setValue("auto_reprocess", bool(on))
        )
        # panel collapse toggle at the plot's top-right corner
        self.btn_toggle_right = QToolButton()
        self.btn_toggle_right.setIcon(_side_toggle_icon("right"))
        self.btn_toggle_right.setIconSize(QSize(16, 16))
        self.btn_toggle_right.setFixedSize(22, 24)
        self.btn_toggle_right.setAutoRaise(True)
        self.btn_toggle_right.clicked.connect(lambda: self._toggle_side_panel("right"))
        tools_row.addWidget(self.btn_toggle_right)
        cv.addLayout(tools_row)

        self.canvas = PlotCanvas(
            on_click=self._plot_clicked,
            on_drag_guide=self._on_drag_guide,
            on_dblclick=self._plot_double_clicked,
            parent=self,
        )
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Finished-draw timestamp feeds the rapid-switch coalescing in
        # _group_selected (v2.18.0).
        self.canvas.mpl_connect(
            "draw_event",
            lambda _evt: setattr(self, "_last_draw_done_at", time.monotonic()),
        )
        # Fit reports belong beside the data, not in the narrow Fit controls.
        # Keep this second pane hidden until a fit/history report is available.
        self.fit_display_splitter = QSplitter(Qt.Orientation.Vertical)
        self.fit_display_splitter.addWidget(self.canvas)
        self.txt_fit_result = QPlainTextEdit()
        self.txt_fit_result.setReadOnly(True)
        self.txt_fit_result.setFont(QFont("Consolas", 10))
        self.txt_fit_result.setMinimumHeight(220)
        self.txt_fit_result.setVisible(False)
        self.fit_display_splitter.addWidget(self.txt_fit_result)
        self.fit_display_splitter.setStretchFactor(0, 4)
        self.fit_display_splitter.setStretchFactor(1, 1)
        cv.addWidget(self.fit_display_splitter, 1)
        split.addWidget(center)

        # ---------- RIGHT: parameter pages ----------
        right = QWidget()
        # v3.19.6: 372 px so the AI Check bar is ONE row
        # [AI Check] ○ Current ○ Marked ○ All [Apply][↶] in English (needs 347)
        right.setMinimumWidth(RIGHT_PANEL_MIN_W)
        right.setMaximumWidth(440)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(2, 2, 4, 2)
        rv.setSpacing(2)

        # Align mode tabs roughly with the single center toolbar row
        rv.addSpacing(18)

        # ONE AI Check above the page tabs (v3.19.5): the same control
        # serves Norm / Fit / Energy, and the user ticks which categories
        # (data processing / fitting) it may touch.
        rv.addWidget(self._build_ai_check_bar())

        mode_bar = QHBoxLayout()
        mode_bar.setSpacing(2)
        self._mode_btns: dict[str, QToolButton] = {}
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for key in ("norm", "fit", "energy"):
            b = QToolButton()
            b.setObjectName("modeTab")
            b.setCheckable(True)
            b.setMinimumHeight(24)
            b.setMaximumHeight(26)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda *_a, k=key: self._set_left_mode(k))
            self._mode_group.addButton(b)
            self._mode_btns[key] = b
            mode_bar.addWidget(b)
        self._mode_btns["norm"].setChecked(True)
        rv.addLayout(mode_bar)

        self.left_stack = QStackedWidget()
        self.left_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rv.addWidget(self.left_stack, 1)

        self._build_norm_page()
        self._build_fit_page()
        self._build_energy_page()

        self._mode_index = {"norm": 0, "fit": 1, "energy": 2}
        split.addWidget(right)
        self._right_panel = right
        right.setObjectName("panelRight")
        # v3.19.6: radio/checkbox indicator-to-text gap 8 -> 4 px so
        # [Action] ○ Current ○ Marked ○ All [↶] fits the panel minimum in English
        right.setStyleSheet("QRadioButton, QCheckBox { spacing: 4px; }")

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([270, 790, 380])

        # ================= LOG (collapsible) =================
        self.log_box = QFrame()
        self.log_box.setObjectName("logBand")
        lg = QVBoxLayout(self.log_box)
        lg.setContentsMargins(12, 2, 12, 2)
        lg.setSpacing(2)
        log_head = QHBoxLayout()
        log_head.setSpacing(6)
        self.btn_log_toggle = QToolButton()
        self.btn_log_toggle.setObjectName("logToggle")
        self.btn_log_toggle.setCheckable(True)
        self.btn_log_toggle.setChecked(True)
        self.btn_log_toggle.setArrowType(Qt.ArrowType.NoArrow)
        self.btn_log_toggle.setText("▾")  # text chevron (native arrow was oversized)
        self.btn_log_toggle.setFixedSize(24, 24)
        self.btn_log_toggle.setAutoRaise(True)
        self.btn_log_toggle.toggled.connect(self._toggle_log)
        self.lbl_log = QLabel()
        self.lbl_log.setStyleSheet("font-weight:600; color:#6b625d;")
        # Keep chevron tight against "日志" (rest of header still spacing=6)
        log_title = QHBoxLayout()
        log_title.setContentsMargins(0, 0, 0, 0)
        log_title.setSpacing(2)
        log_title.addWidget(self.btn_log_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        log_title.addWidget(self.lbl_log, 0, Qt.AlignmentFlag.AlignVCenter)
        # AI access: icon-only terminal button → dropdown (left of folder icon)
        self.btn_ai_menu = QToolButton()
        self.btn_ai_menu.setText(">_")
        self.btn_ai_menu.setAutoRaise(True)
        self.btn_ai_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_ai_menu.setFixedSize(28, 22)
        self.btn_ai_menu.setStyleSheet(
            "QToolButton { border: none; font-family: Consolas, 'Cascadia Mono', monospace; "
            "font-size: 12px; font-weight: 700; color: #6b625d; padding: 0 2px; }"
            "QToolButton:hover { color: #e64626; }"
            "QToolButton::menu-indicator { image: none; width: 0; }"
        )
        self._ai_menu = QMenu(self.btn_ai_menu)
        self._ai_menu.aboutToShow.connect(self._rebuild_ai_menu)
        self.btn_ai_menu.setMenu(self._ai_menu)
        self.btn_open_output = QToolButton()
        self.btn_open_output.setText("📂")
        self.btn_open_output.setAutoRaise(True)
        self.btn_open_output.clicked.connect(self._open_output)
        # All tips/status go into the log terminal only (no extra status chrome)
        log_head.addLayout(log_title)
        log_head.addStretch(1)
        log_head.addWidget(self.btn_ai_menu)
        log_head.addWidget(self.btn_open_output)
        # Engineering mode (🔧): layout capture/export — right of Log actions (§B.23)
        # Pass log VBox so eng toolbar can insert above the log header when ON.
        from .eng_mode import attach_eng_button

        lg.addLayout(log_head)
        attach_eng_button(self, log_head, log_layout=lg)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(110)
        self.txt_log.setMinimumHeight(60)
        font = QFont("Consolas")
        font.setPointSize(9)
        self.txt_log.setFont(font)
        lg.addWidget(self.txt_log)
        v.addWidget(self.log_box)

        # ================= FOOTER =================
        foot_frame = QFrame()
        foot_frame.setObjectName("footBand")
        foot = QHBoxLayout(foot_frame)
        foot.setContentsMargins(12, 2, 12, 4)
        self.lbl_copyright = QLabel(COPYRIGHT)
        self.lbl_copyright.setStyleSheet("color:#6b625d; font-size:10px;")
        self.lbl_version = QLabel(f"v{APP_VERSION}")
        self.lbl_version.setStyleSheet("color:#6b625d; font-size:10px;")
        foot.addWidget(self.lbl_copyright, 1)
        foot.addWidget(self.lbl_version)
        v.addWidget(foot_frame)

        # ---- wire auto-reprocess ----
        for w in (
            self.sp_pre1, self.sp_pre2, self.sp_norm1, self.sp_norm2,
            self.sp_nnorm, self.sp_rbkg, self.sp_bkg_kw, self.sp_kmin, self.sp_kmax,
            self.sp_dk, self.sp_rmin, self.sp_rmax, self.sp_dr, self.sp_kw_plot,
            self.sp_eshift,
            self.sp_kmin_bkg, self.sp_kmax_bkg,
        ):
            w.valueChanged.connect(self._schedule)
        self._e0_manual_pending = False
        self.sp_e0.lineEdit().textEdited.connect(self._mark_manual_e0_pending)
        self.sp_e0.valueChanged.connect(self._mark_manual_e0_spin_change)
        self.sp_e0.editingFinished.connect(self._commit_manual_e0)
        # emin/emax are view-only (plot window) — never trigger a reprocess
        self.sp_emin.valueChanged.connect(self._replot)
        self.sp_emax.valueChanged.connect(self._replot)
        # Range spins also refresh window highlights on the plot (no full reprocess needed for show-only)
        for w in (
            self.sp_kmin, self.sp_kmax, self.sp_rmin, self.sp_rmax,
            self.sp_kmin_bkg, self.sp_kmax_bkg,
        ):
            w.valueChanged.connect(self._replot)
        self.chk_e0_auto.stateChanged.connect(self._schedule)
        self.chk_flatten.stateChanged.connect(self._schedule)
        self.cmb_kwin.currentIndexChanged.connect(self._schedule)
        self.cmb_rwin.currentIndexChanged.connect(self._schedule)
        self.cmb_clamp_lo.currentIndexChanged.connect(self._schedule)
        self.cmb_clamp_hi.currentIndexChanged.connect(self._schedule)

        self._set_left_mode("norm")

    # ========== Parameter pages (right column) ==========
    def _scroll_page(self) -> tuple:
        """Right-column page host: no permanent scrollbar rail (user request).

        Content is laid out compactly; wheel can still pan if a page overflows
        without drawing a vertical scroll bar on the right edge.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Avoid reserving space for a hidden bar on some styles
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { width: 0px; background: transparent; }"
        )
        page = QWidget()
        page.setMinimumWidth(260)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 2, 4, 4)
        lay.setSpacing(3)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(page)
        return scroll, page, lay

    def _build_norm_page(self):
        scroll, page, lf = self._scroll_page()
        self._norm_layout = lf
        self._pin_btns: dict = {}
        self._last_cal_delta: Optional[float] = None  # last calibrate ΔE applied to current
        self._last_cal_group_id: Optional[str] = None  # which group was calibrated (sync reference)

        # v3.19.6: no "Current: …" line here - the Groups list already marks
        # the current group (bold + underline) and the window title has it.

        # ---- Auto norm + AI Check (built here, added to lf after Energy calibration) ----
        self.box_auto_norm = CollapsibleGroupBox()
        an = self.box_auto_norm.body_layout()
        # [Auto norm] + ○ Current ○ Marked ○ All
        self.btn_auto_norm = QPushButton()
        self.btn_auto_norm.clicked.connect(self._on_auto_norm_clicked)
        self.btn_auto_norm_reinit = self._register_undo_btn(
            "auto_norm", on_click=self._auto_normalize_reinit
        )
        row_an, _ = self._make_scope_action_row(
            "auto_norm", self.btn_auto_norm, self.btn_auto_norm_reinit
        )
        an.addWidget(row_an)
        # One FT k window per element/edge set (rule: uniform_ft_window) is
        # asked on the Auto norm click (v3.19.6, _ask_uniform_ft) - no checkbox.
        # AI Check moved to the top bar (v3.19.5) - the Norm page keeps only
        # Auto norm, its scope, the uniform-FT option and Undo.

        # ---- Auto norm FIRST, then Energy calibration, then Normalization ----
        # (v3.19.5 order: normalize in one click, then fine-tune the energy
        #  axis; the AI Check that used to sit here is now above the tabs)
        self.box_energy_cal = CollapsibleGroupBox()
        ec = self.box_energy_cal.body_layout()
        # Row 1: element / edge / Auto-edge button — three even cells (the
        # edge-on-select toggle lives in Settings, default on)
        self.lbl_element = QLabel()
        self.lbl_edge = QLabel()
        self.cmb_element = _combo(
            ["", "Fe", "Ni", "Cu", "Co", "Mn", "Pt", "Au", "Pd", "Ce", "La", "Se", "As", "S", "P", "Ti", "V", "Cr", "Zn", "Mo", "W"],
            editable=True,
            width=64,
        )
        self.cmb_element.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cmb_edge = _combo(["K", "L3", "L2", "L1", "M5", "M"], width=56)
        self.cmb_edge.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_auto_edge = QPushButton()
        self.btn_auto_edge.setObjectName("ghostBtn")
        self.btn_auto_edge.clicked.connect(self._auto_edge_current)
        self.chk_auto_edge_on_select = QCheckBox()
        self.chk_auto_edge_on_select.setChecked(
            str(self.store.value("auto_edge_on_select", "true")).strip().lower()
            not in ("0", "false", "no", "off")
        )
        self.chk_auto_edge_on_select.setVisible(False)  # toggle lives in Settings
        self.chk_auto_edge_on_select.toggled.connect(
            lambda on: self.store.setValue("auto_edge_on_select", bool(on))
        )
        ec.addWidget(_even_row(self.cmb_element, self.cmb_edge, self.btn_auto_edge))
        self.lbl_e0 = QLabel()
        self.sp_e0 = _dspin(0, 120000, 0.1, 0.0, 2, width=80)
        self.chk_e0_auto = QCheckBox()
        self.chk_e0_auto.setChecked(True)
        self.sp_importance = _dspin(0, 10, 0.1, 1.0, 1, width=40)
        self.lbl_imp = QLabel()
        # E0 row carries the pin and a compact Imp (importance) cell
        ec.addWidget(
            _row(
                self.lbl_e0, self.sp_e0, self.chk_e0_auto, self._pin("e0"),
                self.lbl_imp, self.sp_importance,
            )
        )
        # Standard (reference) edge energy — the calibrate target. Auto-filled
        # from xraydb for the selected element/edge; always editable.
        self.lbl_std_e = QLabel()
        self.sp_std_e = _dspin(0, 120000, 0.1, 0.0, 2, width=80)
        # Auto-filled theory values follow the element/edge on every group
        # switch (v0.35.17); a manually typed value is kept as-is.
        self._std_e_user_set = False
        self.sp_std_e.valueChanged.connect(self._on_std_e_user_edited)
        self.sp_eshift = _dspin(-10000, 10000, 0.01, 0.0, 2, width=64)
        self.lbl_dE = QLabel("ΔE")
        # Std E0 (reference) and ΔE (this group's shift) share one row
        ec.addWidget(_row(self.lbl_std_e, self.sp_std_e, self.lbl_dE, self.sp_eshift))

        self.btn_calibrate = QPushButton()
        self.btn_calibrate.clicked.connect(self._op_calibrate)
        ec.addWidget(self.btn_calibrate)
        # Apply ΔE with the standard Current / Marked / All scope row
        # (default Marked — the batch case; "current" is the same as editing ΔE)
        self.btn_apply_shift_marked = QPushButton()
        self.btn_apply_shift_marked.setObjectName("ghostBtn")
        self.btn_apply_shift_marked.clicked.connect(
            lambda: self._op_apply_shift_delta(self._scope_from_module("energy"))
        )
        self.btn_undo_energy = self._register_undo_btn("energy")
        row_es, _ = self._make_scope_action_row(
            "energy", self.btn_apply_shift_marked, self.btn_undo_energy
        )
        self._scope_radios["energy"]["marked"].setChecked(True)
        ec.addWidget(row_es)
        # Tips only go to the log terminal (no status labels here)
        # v3.19.5: Auto norm sits ABOVE Energy calibration (plan T5) - it is
        # the first thing run after import, and the calibration buttons are
        # re-run on the normalized curves.
        lf.addWidget(self.box_auto_norm)
        lf.addWidget(self.box_energy_cal)

        # ---- Normalization (E0 lives in Energy calibration above) ----
        self.box_norm = CollapsibleGroupBox()
        nv = self.box_norm.body_layout()
        self.sp_nnorm = QSpinBox()
        self.sp_nnorm.setRange(0, 3)  # larch degree; 0 = Athena nnorm 1 (constant), v4.4.2
        self.sp_nnorm.setValue(3)
        self.sp_nnorm.setMinimumWidth(72)
        self.chk_flatten = QCheckBox()
        self.chk_flatten.setChecked(True)
        self.sp_pre1 = _dspin(-500, 0, 1, -150, 2, width=76)
        self.sp_pre2 = _dspin(-200, 50, 1, -30, 2, width=76)
        self.sp_norm1 = _dspin(0, 500, 1, 50, 2, width=76)
        self.sp_norm2 = _dspin(50, 2000, 1, 400, 2, width=76)
        self.lbl_ord = QLabel()
        self.lbl_pre = QLabel()
        self.lbl_normr = QLabel()
        nv.addWidget(_row(self.lbl_ord, self.sp_nnorm, self.chk_flatten))
        # Merged window toggles (v0.35.8): ONE eye button per window at the
        # LEFT of the range row — shows/hides band + dashed edges + baseline;
        # edges draggable only while shown.  Replaces the per-edge pins.
        self.btn_win_pre = self._win_toggle()
        self.btn_win_pre.setChecked(False)
        self.btn_win_pre.toggled.connect(lambda *_: self._e_window_toggled())
        self.btn_win_norm = self._win_toggle()
        self.btn_win_norm.setChecked(False)
        self.btn_win_norm.toggled.connect(lambda *_: self._e_window_toggled())
        nv.addWidget(_row(self.btn_win_pre, self.lbl_pre, self.sp_pre1, "→", self.sp_pre2))
        nv.addWidget(_row(self.btn_win_norm, self.lbl_normr, self.sp_norm1, "→", self.sp_norm2))
        self.btn_apply_norm = QPushButton()
        self.btn_apply_norm.clicked.connect(lambda: self._section_apply("norm"))
        self.btn_undo_norm = self._register_undo_btn("norm")
        row_n, _ = self._make_scope_action_row("norm", self.btn_apply_norm, self.btn_undo_norm)
        nv.addWidget(row_n)
        lf.addWidget(self.box_norm)

        self.box_bkg = CollapsibleGroupBox()
        bv = self.box_bkg.body_layout()
        self.sp_rbkg = _dspin(0.2, 3, 0.05, 1.0, 2, width=64)
        self.sp_bkg_kw = QSpinBox()
        self.sp_bkg_kw.setRange(1, 3)
        self.sp_bkg_kw.setValue(2)  # default kw = 2
        self.sp_bkg_kw.setMinimumWidth(56)
        self.sp_kmin_bkg = _dspin(0, 10, 0.1, 0.0, 2, width=64)
        self.sp_kmax_bkg = _dspin(0, 20, 0.1, 15.0, 2, width=64)
        self.cmb_clamp_lo = _combo(["None", "Slight", "Weak", "Medium", "Strong"], width=72)
        self.cmb_clamp_hi = _combo(["None", "Slight", "Weak", "Medium", "Strong"], width=72)
        # default = larch autobk's own clamp_hi=1 ("Slight") — the clamps used to
        # be display-only, so processing effectively ran with larch defaults 0/1
        self.cmb_clamp_hi.setCurrentText("Slight")
        self.lbl_kw1 = QLabel("kw")
        self.lbl_clamp_lo = QLabel()
        self.lbl_clamp_hi = QLabel()
        # Two-param k range has room for the window toggle on the same row
        self.btn_win_bkg = self._win_toggle()
        self.btn_win_bkg.setChecked(False)
        self.btn_win_bkg.toggled.connect(
            lambda shown: self._window_visibility_changed("k_bkg", shown)
        )
        bv.addWidget(_row("Rbkg", self.sp_rbkg, self.lbl_kw1, self.sp_bkg_kw))
        bv.addWidget(_row(self.btn_win_bkg, "k", self.sp_kmin_bkg, "→", self.sp_kmax_bkg))
        bv.addWidget(_row(self.lbl_clamp_lo, self.cmb_clamp_lo, self.lbl_clamp_hi, self.cmb_clamp_hi))
        self.btn_apply_bkg = QPushButton()
        self.btn_apply_bkg.clicked.connect(lambda: self._section_apply("bkg"))
        self.btn_undo_bkg = self._register_undo_btn("bkg")
        row_b, _ = self._make_scope_action_row("bkg", self.btn_apply_bkg, self.btn_undo_bkg)
        bv.addWidget(row_b)
        lf.addWidget(self.box_bkg)

        self.box_ft = CollapsibleGroupBox()
        fv = self.box_ft.body_layout()
        self.sp_kmin = _dspin(0, 15, 0.1, 3.0, 2, width=64)
        self.sp_kmax = _dspin(0, 20, 0.1, 10.0, 2, width=64)
        self.sp_dk = _dspin(0, 5, 0.1, 1.0, 2, width=56)
        self.cmb_kwin = _combo(["Hanning", "Kaiser-Bessel", "Gaussian", "Sine", "Parzen"], width=96)
        self.chk_phase = QCheckBox()
        self.chk_phase.stateChanged.connect(self._schedule)
        self.chk_phase.setToolTip("")
        self.sp_rmin = _dspin(0, 8, 0.1, 1.0, 2, width=56)
        self.sp_rmax = _dspin(0, 10, 0.1, 3.0, 2, width=56)
        self.sp_dr = _dspin(0, 2, 0.1, 0.0, 2, width=56)
        self.cmb_rwin = _combo(["Hanning", "Kaiser-Bessel", "Gaussian", "Sine", "Parzen"], width=96)
        self.sp_kw_plot = QSpinBox()
        self.sp_kw_plot.setRange(0, 3)
        self.sp_kw_plot.setValue(2)
        self.sp_kw_plot.setMinimumWidth(56)
        self.lbl_win = QLabel()
        self.lbl_rwin = QLabel("Rwin")
        self.lbl_pkw = QLabel()
        # Eye toggles at the LEFT of each range row (v0.35.8): show/hide the
        # selected k/R span on the plot; edges draggable while shown.
        self.btn_win_k = self._win_toggle()
        self.btn_win_k.setChecked(True)
        self.btn_win_k.toggled.connect(
            lambda shown: self._window_visibility_changed("k", shown)
        )
        self.btn_win_r = self._win_toggle()
        self.btn_win_r.setChecked(True)
        self.btn_win_r.toggled.connect(
            lambda shown: self._window_visibility_changed("r", shown)
        )
        # Compact: k range + span | dk + win fn | R range + span | dR + Rwin | k-weight + phase
        for sp in (self.sp_kmin, self.sp_kmax, self.sp_rmin, self.sp_rmax):
            sp.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        fv.addWidget(_row(self.btn_win_k, "k", self.sp_kmin, "→", self.sp_kmax))
        fv.addWidget(_row("dk", self.sp_dk, self.lbl_win, self.cmb_kwin))
        fv.addWidget(_row(self.btn_win_r, "R", self.sp_rmin, "→", self.sp_rmax))
        fv.addWidget(_row("dR", self.sp_dr, self.lbl_rwin, self.cmb_rwin))
        fv.addWidget(_row(self.lbl_pkw, self.sp_kw_plot, self.chk_phase))
        self.btn_apply_ft = QPushButton()
        self.btn_apply_ft.clicked.connect(lambda: self._section_apply("ft"))
        self.btn_undo_ft = self._register_undo_btn("ft")
        row_f, _ = self._make_scope_action_row("ft", self.btn_apply_ft, self.btn_undo_ft)
        fv.addWidget(row_f)
        lf.addWidget(self.box_ft)

        # Plot view module removed from the Norm page (v0.19.x): the E window
        # lives in the center toolbar as space-aware range spins; autoscale
        # lives in Settings. The widgets stay (hidden) as storage/back-compat.
        self.sp_emin = _dspin(-2000, 0, 10, -200, 1, width=72)
        self.sp_emax = _dspin(0, 5000, 10, 800, 1, width=72)
        self.chk_autoscale_plot = QCheckBox()
        # always on (double-click resets the view; no user-facing switch needed)
        self.chk_autoscale_plot.setChecked(True)
        self.chk_autoscale_plot.setVisible(False)
        self.left_stack.addWidget(scroll)  # index 0

    def _build_fit_page(self):
        scroll, page, ff = self._scroll_page()
        self.fit_scroll = scroll
        # Spacing already tight from _scroll_page (no right scrollbar rail).
        self.lbl_fit_mode = QLabel()
        self.lbl_fit_mode.setWordWrap(True)
        self.lbl_fit_mode.setStyleSheet("font-weight:600;")
        ff.addWidget(self.lbl_fit_mode)
        # (no per-group data label — group selection is visible in the left list)

        # ---- Auto fit + AI Assist (first collapsible session, same style as Norm modules) ----
        self.box_auto_fit = CollapsibleGroupBox()
        af = self.box_auto_fit.body_layout()
        # [Auto fit] + ○ Current ○ Marked ○ All; Undo below
        self.btn_auto_fit = QPushButton()
        self.btn_auto_fit.clicked.connect(
            lambda: self._auto_fit(self._scope_from_module("auto_fit"))
        )
        self.btn_auto_fit_reinit = self._register_undo_btn(
            "auto_fit", on_click=self._auto_fit_reinit
        )
        row_af, _ = self._make_scope_action_row(
            "auto_fit", self.btn_auto_fit, self.btn_auto_fit_reinit
        )
        af.addWidget(row_af)

        # The Fit-page AI Assist block moved to the top ai_check bar (v3.19.5):
        # one entry point above the page tabs, user-chosen categories.
        ff.addWidget(self.box_auto_fit)

        # Read-only fit summary: the editable k/R windows remain in Norm → FT.
        self.box_fit_win = CollapsibleGroupBox()
        fw = self.box_fit_win.body_layout()
        self.lbl_fit_kweights = QLabel()
        self.lbl_fit_kweights.setStyleSheet("color:#6b625d; font-size:11px;")
        self.lbl_fit_win = QLabel()
        self.lbl_fit_win.setWordWrap(True)
        fw.addWidget(self.lbl_fit_kweights)
        fw.addWidget(self.lbl_fit_win)
        ff.addWidget(self.box_fit_win)

        # Display controls sit immediately above the fit setup tables.
        self.cmb_fit_plot = QComboBox()
        for label, code in (
            ("E / k / R / q", "standard"),
            ("k123", "k123"),
            ("R123", "r123"),
            ("Rmr", "rmr"),
            ("Rk", "rk"),
            ("kq", "kq"),
        ):
            self.cmb_fit_plot.addItem(label, code)
        self.cmb_fit_plot.setMinimumWidth(108)
        self.cmb_fit_plot_kw = QComboBox()
        self.cmb_fit_plot_kw.addItem("All (k1–k3)", None)
        for weight in (1, 2, 3):
            self.cmb_fit_plot_kw.addItem(f"k{weight}", weight)
        self.cmb_fit_plot_kw.setCurrentIndex(0)
        self.cmb_fit_plot_kw.setMinimumWidth(86)
        self.lbl_fit_plot = QLabel()
        self.lbl_fit_plot_kw = QLabel()
        self.cmb_fit_plot.currentIndexChanged.connect(self._fit_plot_changed)
        self.cmb_fit_plot_kw.currentIndexChanged.connect(self._fit_plot_weight_changed)
        self.fit_plot_host = _row(self.lbl_fit_plot, self.cmb_fit_plot, self.lbl_fit_plot_kw, self.cmb_fit_plot_kw)
        ff.addWidget(self.fit_plot_host)
        self._fit_layout = ff

        # ---- GDS parameter table (same working height as FEFF Paths) ----
        self.box_gds = CollapsibleGroupBox(expanded=True)
        self.box_gds.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        bg = self.box_gds.body_layout()
        self.tbl_gds = QTableWidget(0, 6)
        self.tbl_gds.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_gds.verticalHeader().setVisible(False)
        self.tbl_gds.setMinimumHeight(160)
        self.tbl_gds.setMaximumHeight(160)
        self.tbl_gds.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tbl_gds.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        bg.addWidget(self.tbl_gds)
        self.btn_model_a = QPushButton()
        self.btn_model_a.setObjectName("ghostBtn")
        self.btn_model_a.clicked.connect(self._preset_gds)
        self.btn_gds_add = QPushButton()
        self.btn_gds_add.setObjectName("ghostBtn")
        self.btn_gds_add.clicked.connect(lambda: self._gds_add_row(GDSParam(f"p{self.tbl_gds.rowCount() + 1}", 1.0, "vary")))
        self.btn_gds_del = QPushButton()
        self.btn_gds_del.setObjectName("ghostBtn")
        self.btn_gds_del.clicked.connect(lambda: self._del_selected_rows(self.tbl_gds))
        self.btn_gds_clear = QPushButton()
        self.btn_gds_clear.setObjectName("ghostBtn")
        self.btn_gds_clear.clicked.connect(lambda: self.tbl_gds.setRowCount(0))
        # 2×2 so long bilingual labels are not clipped in the narrow Fit column
        bg.addWidget(
            _btn_grid2x2(
                self.btn_model_a,
                self.btn_gds_add,
                self.btn_gds_del,
                self.btn_gds_clear,
            )
        )
        ff.addWidget(self.box_gds)

        # ---- feff path table ----
        self.box_fit_paths = CollapsibleGroupBox(expanded=True)
        self.box_fit_paths.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        fp = self.box_fit_paths.body_layout()
        self.tbl_paths = QTableWidget(0, 9)
        self.tbl_paths.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # The element selector must retain enough in-cell margin for its frame
        # and arrow; ResizeToContents can otherwise shrink the editable widget
        # until its first character sits under the table grid line.
        self.tbl_paths.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tbl_paths.setColumnWidth(1, 76)
        self.tbl_paths.verticalHeader().setVisible(False)
        # Match the GDS working area so the two fit-input tables form one
        # consistent vertical rhythm; longer models scroll inside the table.
        self.tbl_paths.setMinimumHeight(160)
        self.tbl_paths.setMaximumHeight(160)
        self.tbl_paths.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tbl_paths.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        fp.addWidget(self.tbl_paths)
        self.btn_model_a_paths = QPushButton()
        self.btn_model_a_paths.setObjectName("ghostBtn")
        self.btn_model_a_paths.clicked.connect(self._preset_paths)
        self.btn_feff_ui = QPushButton()
        self.btn_feff_ui.setObjectName("ghostBtn")
        self.btn_feff_ui.clicked.connect(lambda: self._run_feff())
        self.btn_path_add = QPushButton()
        self.btn_path_add.setObjectName("ghostBtn")
        self.btn_path_add.clicked.connect(self._add_feff_files)
        self.btn_path_del = QPushButton()
        self.btn_path_del.setObjectName("ghostBtn")
        self.btn_path_del.clicked.connect(lambda: self._del_selected_rows(self.tbl_paths))
        self.path_actions_host = _even_row(
            self.btn_model_a_paths,
            self.btn_feff_ui,
            self.btn_path_add,
            self.btn_path_del,
        )
        fp.addWidget(self.path_actions_host)
        ff.addWidget(self.box_fit_paths)

        # ---- Run Fit → quality → history → demo+export row ----
        self.btn_fit = QPushButton()
        self.btn_fit.clicked.connect(self._run_fit)
        ff.addWidget(self.btn_fit)

        self.fit_quality_card = QFrame()
        self.fit_quality_card.setObjectName("fitQualityCard")
        quality_v = QVBoxLayout(self.fit_quality_card)
        quality_v.setContentsMargins(8, 4, 8, 4)
        quality_v.setSpacing(0)
        self.lbl_fit_grade = QLabel()
        self.lbl_fit_grade.setStyleSheet("font-weight:700; font-size:11px;")
        self.lbl_fit_quality = QLabel()
        self.lbl_fit_quality.setWordWrap(True)
        self.lbl_fit_quality.setStyleSheet("font-size:11px; color:#4b4742;")
        self.lbl_fit_warning = QLabel()
        self.lbl_fit_warning.setWordWrap(True)
        self.lbl_fit_warning.setStyleSheet("font-size:11px; color:#8a5a00;")
        quality_v.addWidget(self.lbl_fit_grade)
        quality_v.addWidget(self.lbl_fit_quality)
        quality_v.addWidget(self.lbl_fit_warning)
        ff.addWidget(self.fit_quality_card)

        # History: select previews; Restore refills GDS/paths/windows
        hist_host = QWidget()
        hist_v = QVBoxLayout(hist_host)
        hist_v.setContentsMargins(0, 2, 0, 2)
        hist_v.setSpacing(4)
        self.lbl_fit_history = QLabel()
        self.cmb_fit_history = QComboBox()
        self.cmb_fit_history.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cmb_fit_history.setMinimumWidth(0)
        self.cmb_fit_history.currentIndexChanged.connect(lambda *_: self._history_selected())
        hist_top = QHBoxLayout()
        hist_top.setContentsMargins(0, 0, 0, 0)
        hist_top.setSpacing(8)
        hist_top.addWidget(self.lbl_fit_history)
        hist_top.addWidget(self.cmb_fit_history, 1)
        self.btn_hist_clear = QPushButton()
        self.btn_hist_clear.setObjectName("ghostBtn")
        self.btn_hist_clear.clicked.connect(self._clear_fit_history)
        self.btn_hist_clear.setMinimumHeight(26)
        hist_top.addWidget(self.btn_hist_clear)
        hist_v.addLayout(hist_top)
        self.btn_hist_restore = QPushButton()
        self.btn_hist_restore.setObjectName("ghostBtn")
        self.btn_hist_restore.clicked.connect(self._restore_fit_history)
        self.btn_hist_restore.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_hist_restore.setMinimumHeight(26)
        hist_btns = QHBoxLayout()
        hist_btns.setContentsMargins(0, 0, 0, 0)
        hist_btns.setSpacing(6)
        hist_btns.addWidget(self.btn_hist_restore, 1)
        hist_v.addLayout(hist_btns)
        ff.addWidget(hist_host)

        # Bottom row: [📂 folder] [demo check] [Export report + data]
        self.btn_demo_folder = QPushButton()
        self.btn_demo_folder.setObjectName("ghostBtn")
        self.btn_demo_folder.setFixedWidth(32)
        self.btn_demo_folder.setToolTip(self.i18n.t("demo_folder_tip"))
        self.btn_demo_folder.clicked.connect(self._choose_demo_folder)
        self.btn_validate_ui = QPushButton()
        self.btn_validate_ui.setObjectName("ghostBtn")
        self.btn_validate_ui.clicked.connect(self._run_validate)
        self.btn_fit_export_all = QPushButton()
        self.btn_fit_export_all.setObjectName("ghostBtn")
        self.btn_fit_export_all.clicked.connect(self._export_fit_all)
        # Keep old widgets as aliases for any leftover references
        self.btn_fit_export_report = self.btn_fit_export_all
        self.btn_fit_export_data = self.btn_fit_export_all
        self.btn_ai_review = QPushButton()  # retained for retranslate safety; not shown
        self.btn_ai_review.setVisible(False)
        demo_export_row = QHBoxLayout()
        demo_export_row.setContentsMargins(0, 0, 0, 0)
        demo_export_row.setSpacing(6)
        demo_export_row.addWidget(self.btn_demo_folder)
        demo_export_row.addWidget(self.btn_validate_ui, 1)
        demo_export_row.addWidget(self.btn_fit_export_all, 1)
        ff.addLayout(demo_export_row)

        self.left_stack.addWidget(scroll)  # index 1

    def _build_energy_page(self):
        """Tools page: grouped sub-panels with method selectors + Apply."""
        scroll, page, ev = self._scroll_page()
        self._tool_btns: list = []  # (button, i18n key) for analysis / merge retranslate
        self._sec_labels: list = []  # legacy; section titles now on CollapsibleGroupBox
        self._plugin_btns: list = []  # (button, slot_id) — companion web tools

        # ---- Companion tools: web plugins + merge (top of Tools page) ----
        self.box_tools_plugins = CollapsibleGroupBox()
        pl = self.box_tools_plugins.body_layout()
        self.lbl_plugins_hint = QLabel()
        self.lbl_plugins_hint.setWordWrap(True)
        self.lbl_plugins_hint.setStyleSheet("color:#6b625d; font-size:11px;")
        pl.addWidget(self.lbl_plugins_hint)
        for slot in plugins_manager.PLUGIN_SLOTS:
            btn = QPushButton()
            btn.setObjectName("ghostBtn")
            sid = slot["id"]
            btn.clicked.connect(lambda *_a, s=sid: self._open_plugin(s))
            self._plugin_btns.append((btn, sid))
            pl.addWidget(btn)
        # Merge marked groups (same box — keeps Tools header tidy)
        self.btn_merge = QPushButton()
        self.btn_merge.setObjectName("ghostBtn")
        self.btn_merge.clicked.connect(self._op_merge)
        self._tool_btns.append((self.btn_merge, "op_merge"))
        self.btn_undo_merge = self._register_undo_btn("merge", on_click=self._undo_merge)
        merge_row = QWidget()
        mh = QHBoxLayout(merge_row)
        mh.setContentsMargins(0, 2, 0, 2)
        mh.setSpacing(4)
        self.btn_merge.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        mh.addWidget(self.btn_merge, 1)  # fills the row up to its ↶ (v3.19.6)
        mh.addWidget(self.btn_undo_merge, 0)
        pl.addWidget(merge_row)
        # Align peak: pick the reference curve, shift marked groups onto it
        align_host = QWidget()
        al = QHBoxLayout(align_host)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(6)
        self.cmb_align_ref = QComboBox()
        self.cmb_align_ref.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_align = QPushButton()
        self.btn_align.setObjectName("ghostBtn")
        self.btn_align.clicked.connect(self._op_align)
        self._tool_btns.append((self.btn_align, "op_align"))
        al.addWidget(self.cmb_align_ref, 1)
        al.addWidget(self.btn_align)
        pl.addWidget(align_host)
        # hint sits UNDER the align row it explains (v2.18.1, user request)
        self.lbl_align_hint = QLabel()
        self.lbl_align_hint.setWordWrap(True)
        self.lbl_align_hint.setStyleSheet("color:#6b625d; font-size:11px;")
        pl.addWidget(self.lbl_align_hint)
        # Plugin folder is opened from Settings only (no duplicate button here).
        ev.addWidget(self.box_tools_plugins)

        # ---- Smooth / Rebin (sub-options + params + Apply current/marked) ----
        self.box_tools_smooth = CollapsibleGroupBox()
        sm = self.box_tools_smooth.body_layout()
        self.rad_tool_smooth = QRadioButton()
        self.rad_tool_rebin = QRadioButton()
        self.rad_tool_smooth.setChecked(True)
        self._smooth_rebin_group = QButtonGroup(self)
        self._smooth_rebin_group.addButton(self.rad_tool_smooth)
        self._smooth_rebin_group.addButton(self.rad_tool_rebin)
        self.chk_rebin_auto = QCheckBox()  # one switch for pre1 / xanes_step / exafs2
        self.chk_rebin_auto.setChecked(True)
        sm.addWidget(_row(self.rad_tool_smooth, self.rad_tool_rebin, self.chk_rebin_auto))
        # Smooth params: a short boxcar width that rides on the Apply row
        # (v3.19.6): [Apply] 宽度[5] ○ ○ ○ [↶]; hidden in Rebin mode
        self.smooth_params = QWidget()
        sp_lay = QHBoxLayout(self.smooth_params)
        sp_lay.setContentsMargins(0, 0, 0, 0)
        sp_lay.setSpacing(3)
        self.lbl_boxcar = QLabel()
        self.sp_boxcar = QSpinBox()
        self.sp_boxcar.setRange(3, 51)
        self.sp_boxcar.setSingleStep(2)
        self.sp_boxcar.setValue(5)
        # arrowless like the Rebin boxes (wheel / arrow keys still step by 2):
        # the 18 px arrow padding goes, which makes room for a label in English
        self.sp_boxcar.setButtonSymbols(self.sp_boxcar.ButtonSymbols.NoButtons)
        self.sp_boxcar.setStyleSheet("padding-right: 3px;")
        self.sp_boxcar.setFixedWidth(self.sp_boxcar.fontMetrics().horizontalAdvance("51") + 18)
        sp_lay.addWidget(self.lbl_boxcar)
        sp_lay.addWidget(self.sp_boxcar)
        # Rebin params (Athena/Larix 3-region: pre / XANES / EXAFS)
        self.rebin_params = QWidget()
        rb = QVBoxLayout(self.rebin_params)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(3)
        # v3.19.6: ONE auto switch (next to the Rebin radio) = automatic rebin:
        # every parameter comes from rebin_group's own defaults (pre1 / exafs2
        # = first / last data point, xanes step automatic, pre2 -30, pre_step 2,
        # exafs1 15, kstep 0.05, boxcar) and all eight fields grey out. Off =
        # the numbers in the fields. Below, four fields per row, each label
        # ABOVE its box, all fields one width, columns spread with a gap:
        #   pre1     pre2     pre_step  xanes
        #   exafs1   exafs2   kstep     method
        self.lbl_rebin_pre1 = QLabel("pre1")
        self.lbl_rebin_pre2 = QLabel("pre2")
        self.lbl_rebin_pre_step = QLabel("pre_step")
        self.sp_rebin_pre1 = _dspin(-2000, 0, 1, -200, 1)
        self.sp_rebin_pre2 = _dspin(-200, 50, 1, -30, 1)
        self.sp_rebin_pre_step = _dspin(0.05, 20, 0.05, 2.0, 2)
        self.lbl_rebin_xanes = QLabel("xanes")
        self.lbl_rebin_xanes.setToolTip("xanes_step")
        self.sp_rebin_xanes = _dspin(0.01, 5, 0.01, 0.05, 3)
        self.lbl_rebin_exafs1 = QLabel("exafs1")
        self.lbl_rebin_exafs2 = QLabel("exafs2")
        self.lbl_rebin_kstep = QLabel("kstep")
        self.sp_rebin_exafs1 = _dspin(0, 100, 0.5, 15.0, 1)
        self.sp_rebin_exafs2 = _dspin(0, 2000, 1, 800, 1)
        self.sp_rebin_kstep = _dspin(0.01, 0.5, 0.01, 0.05, 3)
        self.lbl_rebin_method = QLabel("method")
        self.cmb_rebin_method = QComboBox()
        self.cmb_rebin_method.addItems(["boxcar", "spline", "centroid"])
        fields = (self.sp_rebin_pre1, self.sp_rebin_pre2, self.sp_rebin_pre_step,
                  self.sp_rebin_xanes, self.sp_rebin_exafs1, self.sp_rebin_exafs2,
                  self.sp_rebin_kstep)
        for sp in fields:
            _plain_num(sp)
        # one width for every field (the widest need); the combo is sized from
        # its longest item - its sizeHint() before the QSS is applied is ~93 px
        combo_need = self.cmb_rebin_method.fontMetrics().horizontalAdvance("centroid") + 28
        field_w = max([sp.minimumWidth() for sp in fields] + [combo_need])
        for f in fields + (self.cmb_rebin_method,):
            f.setFixedWidth(field_w)  # extra room goes to the gaps, not the boxes
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(12)  # air between the boxes (grows with the panel)
        grid.setVerticalSpacing(1)
        layout_rows = (
            ((self.lbl_rebin_pre1, self.sp_rebin_pre1), (self.lbl_rebin_pre2, self.sp_rebin_pre2),
             (self.lbl_rebin_pre_step, self.sp_rebin_pre_step), (self.lbl_rebin_xanes, self.sp_rebin_xanes)),
            ((self.lbl_rebin_exafs1, self.sp_rebin_exafs1), (self.lbl_rebin_exafs2, self.sp_rebin_exafs2),
             (self.lbl_rebin_kstep, self.sp_rebin_kstep), (self.lbl_rebin_method, self.cmb_rebin_method)),
        )
        for r, row in enumerate(layout_rows):
            for c, (lab, field) in enumerate(row):
                grid.addWidget(lab, 2 * r, c, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
                grid.addWidget(field, 2 * r + 1, c)
        grid.setRowMinimumHeight(2, 20)  # a little air above the second label row
        for c in range(4):  # four equal columns across the row
            grid.setColumnStretch(c, 1)
            grid.setColumnMinimumWidth(c, field_w)
        rb.addWidget(grid_host)
        sm.addWidget(self.rebin_params)
        self.rad_tool_smooth.toggled.connect(self._update_smooth_rebin_ui)

        self.chk_rebin_auto.toggled.connect(self._rebin_auto_changed)
        self._rebin_manual: dict = {}
        self._rebin_auto_changed(self.chk_rebin_auto.isChecked())
        self.btn_apply_smooth = QPushButton()
        self.btn_apply_smooth.clicked.connect(self._on_apply_smooth_rebin_clicked)
        self.btn_undo_smooth = self._register_undo_btn("smooth")
        row_sm, _ = self._make_scope_action_row(
            "smooth", self.btn_apply_smooth, self.btn_undo_smooth, extra=self.smooth_params
        )
        sm.addWidget(row_sm)
        ev.addWidget(self.box_tools_smooth)

        # ---- Deglitch (auto / click + pin energy / range spins + Apply + clear) ----
        self.box_tools_deglitch = CollapsibleGroupBox()
        dg = self.box_tools_deglitch.body_layout()
        self.rad_deglitch_auto = QRadioButton()
        self.rad_deglitch_click = QRadioButton()
        self.rad_deglitch_auto.setChecked(True)
        self._deglitch_mode_group = QButtonGroup(self)
        self._deglitch_mode_group.addButton(self.rad_deglitch_auto)
        self._deglitch_mode_group.addButton(self.rad_deglitch_click)
        self.sp_dg_thresh = _dspin(1.0, 10.0, 0.5, 3.0, 1, width=64)
        self.sp_dg_thresh.setToolTip(self.i18n.t("dg_thresh_tip"))
        dg.addWidget(_row(self.rad_deglitch_auto, self.sp_dg_thresh, self.rad_deglitch_click))

        self.click_deglitch_opts = QWidget()
        cdo = QVBoxLayout(self.click_deglitch_opts)
        cdo.setContentsMargins(0, 0, 0, 0)
        cdo.setSpacing(2)
        self.rad_dg_point = QRadioButton()
        self.rad_dg_before = QRadioButton()
        self.rad_dg_after = QRadioButton()
        self.rad_dg_range = QRadioButton()
        self.rad_dg_point.setChecked(True)
        self._dg_click_style_group = QButtonGroup(self)
        for b in (self.rad_dg_point, self.rad_dg_before, self.rad_dg_after, self.rad_dg_range):
            self._dg_click_style_group.addButton(b)
        # Shared E picker (reparented under the active point/before/after option)
        self.lbl_dg_e = QLabel("E")
        self.sp_dg_e = _dspin(0, 120000, 0.01, 0.0, 3, width=100)
        self.pin_dg_e = self._pin("dg_e")
        self.dg_e_host = QWidget()
        e_host_lay = QHBoxLayout(self.dg_e_host)
        e_host_lay.setContentsMargins(16, 0, 0, 4)
        e_host_lay.setSpacing(6)
        e_host_lay.addWidget(self.lbl_dg_e)
        e_host_lay.addWidget(self.sp_dg_e)
        e_host_lay.addWidget(self.pin_dg_e)
        e_host_lay.addStretch(1)
        # Slots under each radio so E appears directly below the selected style
        self.dg_slot_point = QVBoxLayout()
        self.dg_slot_point.setContentsMargins(0, 0, 0, 0)
        self.dg_slot_point.setSpacing(0)
        self.dg_slot_before = QVBoxLayout()
        self.dg_slot_before.setContentsMargins(0, 0, 0, 0)
        self.dg_slot_before.setSpacing(0)
        self.dg_slot_after = QVBoxLayout()
        self.dg_slot_after.setContentsMargins(0, 0, 0, 0)
        self.dg_slot_after.setSpacing(0)
        cdo.addWidget(self.rad_dg_point)
        cdo.addLayout(self.dg_slot_point)
        cdo.addWidget(self.rad_dg_before)
        cdo.addLayout(self.dg_slot_before)
        cdo.addWidget(self.rad_dg_after)
        cdo.addLayout(self.dg_slot_after)
        cdo.addWidget(self.rad_dg_range)
        # Range E1/E2 sits under the range option only
        self.dg_range_host = QWidget()
        rg_lay = QHBoxLayout(self.dg_range_host)
        rg_lay.setContentsMargins(16, 0, 0, 4)
        rg_lay.setSpacing(6)
        self.lbl_dg_e1 = QLabel("E1")
        self.lbl_dg_e2 = QLabel("E2")
        self.sp_dg_e1 = _dspin(0, 120000, 0.01, 0.0, 3, width=90)
        self.sp_dg_e2 = _dspin(0, 120000, 0.01, 0.0, 3, width=90)
        self.pin_dg_e1 = self._pin("dg_e1")
        self.pin_dg_e2 = self._pin("dg_e2")
        for w in (
            self.lbl_dg_e1,
            self.sp_dg_e1,
            self.pin_dg_e1,
            self.lbl_dg_e2,
            self.sp_dg_e2,
            self.pin_dg_e2,
        ):
            rg_lay.addWidget(w)
        rg_lay.addStretch(1)
        cdo.addWidget(self.dg_range_host)
        dg.addWidget(self.click_deglitch_opts)
        self.lbl_deglitch_hint = QLabel()
        self.lbl_deglitch_hint.setWordWrap(True)
        self.lbl_deglitch_hint.setStyleSheet("color:#6b625d; font-size:11px;")
        dg.addWidget(self.lbl_deglitch_hint)
        self.rad_deglitch_auto.toggled.connect(self._update_deglitch_ui)
        for b in (self.rad_dg_point, self.rad_dg_before, self.rad_dg_after, self.rad_dg_range):
            b.toggled.connect(self._update_deglitch_ui)
        self.btn_apply_deglitch = QPushButton()
        self.btn_apply_deglitch.clicked.connect(self._on_apply_deglitch_clicked)
        self.btn_undo_deglitch = self._register_undo_btn("deglitch")
        row_dg, _ = self._make_scope_action_row("deglitch", self.btn_apply_deglitch, self.btn_undo_deglitch)
        dg.addWidget(row_dg)
        ev.addWidget(self.box_tools_deglitch)

        # ---- Analysis (same collapsible title style as Smooth / Deglitch / Export) ----
        self.box_tools_analysis = CollapsibleGroupBox()
        an = self.box_tools_analysis.body_layout()
        for key, slot in (
            ("op_lcf", self._op_lcf),
            ("op_pca", self._op_pca),
            ("op_mcr", self._op_mcr),
            ("op_peaks", self._op_peaks),
            ("op_wavelet", self._op_wavelet),
        ):
            b = QPushButton()
            b.setObjectName("ghostBtn")
            b.clicked.connect(slot)
            self._tool_btns.append((b, key))
            an.addWidget(b)
        ev.addWidget(self.box_tools_analysis)

        # ---- Export (below Analysis): format + kinds by E/k/R/q ----
        # Folder / name prefix / suffix controls live directly above the Export button (v0.34.9).
        self.box_tools_export = CollapsibleGroupBox()
        ex = self.box_tools_export.body_layout()
        # Format: short radio + word-wrapped note (QRadioButton alone clips long text)
        self.lbl_export_fmt = QLabel()
        self.lbl_export_fmt.setWordWrap(True)
        self.rad_export_csv = QRadioButton()
        self.lbl_export_csv_note = QLabel()
        self.lbl_export_csv_note.setWordWrap(True)
        self.lbl_export_csv_note.setStyleSheet("color:#6b625d; font-size:11px; margin-left:22px; margin-bottom:4px;")
        self.rad_export_xlsx = QRadioButton()
        self.lbl_export_xlsx_note = QLabel()
        self.lbl_export_xlsx_note.setWordWrap(True)
        self.lbl_export_xlsx_note.setStyleSheet("color:#6b625d; font-size:11px; margin-left:22px; margin-bottom:4px;")
        self.rad_export_xlsx.setChecked(True)
        self._export_fmt_group = QButtonGroup(self)
        self._export_fmt_group.addButton(self.rad_export_csv)
        self._export_fmt_group.addButton(self.rad_export_xlsx)
        ex.addWidget(self.lbl_export_fmt)
        ex.addWidget(self.rad_export_csv)
        ex.addWidget(self.lbl_export_csv_note)
        ex.addWidget(self.rad_export_xlsx)
        ex.addWidget(self.lbl_export_xlsx_note)
        self.lbl_export_src = QLabel()
        self.rad_export_marked = QRadioButton()
        self.rad_export_current = QRadioButton()
        self.rad_export_marked.setChecked(True)
        self._export_src_group = QButtonGroup(self)
        self._export_src_group.addButton(self.rad_export_marked)
        self._export_src_group.addButton(self.rad_export_current)
        ex.addWidget(_row(self.lbl_export_src, self.rad_export_marked, self.rad_export_current))
        self.lbl_export_kinds = QLabel()
        self.lbl_export_kinds.setWordWrap(True)
        ex.addWidget(self.lbl_export_kinds)
        # Rows: E(μ)×3, norE×3, then k/R/q ×4 — short labels so text is not clipped
        self._export_kind_checks: dict[str, QCheckBox] = {}
        self._export_space_labels: dict[str, QLabel] = {}
        kinds_host = QWidget()
        kinds_grid = QGridLayout(kinds_host)
        kinds_grid.setContentsMargins(0, 0, 0, 0)
        kinds_grid.setHorizontalSpacing(4)
        kinds_grid.setVerticalSpacing(3)
        _default_on = set(EXPORT_DEFAULT_KINDS)
        row_i = 0
        for space, keys in EXPORT_KIND_GROUPS:
            lab = QLabel(space)
            lab.setStyleSheet("font-weight:600; min-width:36px; font-size:11px;")
            self._export_space_labels[space] = lab
            kinds_grid.addWidget(lab, row_i, 0)
            for col, key in enumerate(keys):
                chk = QCheckBox()
                chk.setChecked(key in _default_on)
                chk.setMinimumWidth(0)
                chk.setStyleSheet("QCheckBox { font-size: 11px; spacing: 3px; }")
                self._export_kind_checks[key] = chk
                kinds_grid.addWidget(chk, row_i, col + 1)
            row_i += 1
        for c in range(1, 5):
            kinds_grid.setColumnStretch(c, 1)
        self.btn_export_kinds_all = QPushButton()
        self.btn_export_kinds_all.setObjectName("ghostBtn")
        self.btn_export_kinds_none = QPushButton()
        self.btn_export_kinds_none.setObjectName("ghostBtn")
        self.btn_export_kinds_all.clicked.connect(lambda: self._export_kinds_set_all(True))
        self.btn_export_kinds_none.clicked.connect(lambda: self._export_kinds_set_all(False))
        ex.addWidget(kinds_host)
        ex.addWidget(_row(self.btn_export_kinds_all, self.btn_export_kinds_none))
        # Wavelet side-car export (spec §4.3c) — two independent opt-ins (data
        # .txt / heatmap .png) appended below the kind rows; no existing control
        # is moved or resized. Stacked vertically: the sidebar is too narrow for
        # a bilingual side-by-side pair (§B.21).
        self.chk_export_wavelet = QCheckBox()
        self.chk_export_wavelet.setChecked(
            str(self.store.value("export/include_wavelet", "false")).strip().lower()
            in ("1", "true", "yes", "on")
        )
        self.chk_export_wavelet.setStyleSheet("QCheckBox { font-size: 11px; spacing: 3px; }")
        self.chk_export_wavelet.toggled.connect(
            lambda on: self.store.setValue("export/include_wavelet", bool(on))
        )
        self.chk_export_wavelet_png = QCheckBox()
        self.chk_export_wavelet_png.setChecked(
            str(self.store.value("export/include_wavelet_png", "false")).strip().lower()
            in ("1", "true", "yes", "on")
        )
        self.chk_export_wavelet_png.setStyleSheet("QCheckBox { font-size: 11px; spacing: 3px; }")
        self.chk_export_wavelet_png.toggled.connect(
            lambda on: self.store.setValue("export/include_wavelet_png", bool(on))
        )
        # v3.19.6: the two opt-ins share one row
        ex.addWidget(_row(self.chk_export_wavelet, self.chk_export_wavelet_png))
        self.lbl_export_wt_note = QLabel()
        self.lbl_export_wt_note.setWordWrap(True)
        self.lbl_export_wt_note.setStyleSheet(
            "color:#6b625d; font-size:11px; margin-left:22px; margin-bottom:4px;"
        )
        ex.addWidget(self.lbl_export_wt_note)
        self.lbl_export_hint = QLabel()
        self.lbl_export_hint.setWordWrap(True)
        self.lbl_export_hint.setStyleSheet("color:#6b625d; font-size:11px;")
        ex.addWidget(self.lbl_export_hint)
        # Export folder (moved out of Settings, v0.34.9) + file-name prefix/suffix,
        # all directly above the Export button.
        self.lbl_export_folder = QLabel()
        self.lbl_export_folder.setStyleSheet("font-size:11px; font-weight:600;")
        ex.addWidget(self.lbl_export_folder)
        self.ed_export_dir = QLineEdit(str(self._data_export_dir()))
        self.btn_export_dir_browse = QPushButton()
        self.btn_export_dir_browse.setObjectName("ghostBtn")
        self.btn_export_dir_open = QPushButton()
        self.btn_export_dir_open.setObjectName("ghostBtn")
        self.ed_export_dir.editingFinished.connect(self._export_dir_edited)
        self.btn_export_dir_browse.clicked.connect(self._browse_export_dir)
        self.btn_export_dir_open.clicked.connect(self._open_export_dir)
        dir_host = QWidget()
        dir_lay = QHBoxLayout(dir_host)
        dir_lay.setContentsMargins(0, 2, 0, 2)
        dir_lay.setSpacing(4)
        dir_lay.addWidget(self.ed_export_dir, 1)
        dir_lay.addWidget(self.btn_export_dir_browse)
        dir_lay.addWidget(self.btn_export_dir_open)
        ex.addWidget(dir_host)
        self.lbl_export_prefix = QLabel()
        self.lbl_export_suffix = QLabel()
        self.ed_export_prefix = QLineEdit(str(self.store.value("export_name_prefix", "") or ""))
        self.ed_export_suffix = QLineEdit(str(self.store.value("export_name_suffix", "") or ""))
        self.ed_export_prefix.editingFinished.connect(self._export_name_affix_edited)
        self.ed_export_suffix.editingFinished.connect(self._export_name_affix_edited)
        affix_host = QWidget()
        affix_lay = QHBoxLayout(affix_host)
        affix_lay.setContentsMargins(0, 2, 0, 2)
        affix_lay.setSpacing(4)
        affix_lay.addWidget(self.lbl_export_prefix)
        affix_lay.addWidget(self.ed_export_prefix, 1)
        affix_lay.addWidget(self.lbl_export_suffix)
        affix_lay.addWidget(self.ed_export_suffix, 1)
        ex.addWidget(affix_host)
        self.btn_export_go = QPushButton()
        self.btn_export_go.clicked.connect(self._op_export)
        ex.addWidget(self.btn_export_go)
        ev.addWidget(self.box_tools_export)

        ev.addStretch(1)
        self.left_stack.addWidget(scroll)  # index 2
        self._update_smooth_rebin_ui()
        self._update_deglitch_ui()

    def _apply_theme(self, scheme=None):
        """Follow Windows light/dark mode (UI-Theme-Library palettes)."""
        if scheme is None:
            scheme = QApplication.styleHints().colorScheme()
        dark = str(scheme).endswith("Dark")
        self._theme_dark = dark
        self.setStyleSheet(_DARK_QSS if dark else _APP_QSS)
        muted = "#9fb0c0" if dark else "#6b625d"
        ink = "#e6edf3" if dark else "#242424"
        self.lbl_app_title.setStyleSheet(f"font-size:20px; font-weight:700; color:{ink};")
        self.lbl_app_sub.setStyleSheet(f"font-size:11px; color:{muted};")
        self.lbl_dir.setStyleSheet(f"color:{muted};font-size:11px;")
        self.lbl_copyright.setStyleSheet(f"color:{muted}; font-size:10px;")
        self.lbl_version.setStyleSheet(f"color:{muted}; font-size:10px;")
        for lab, _key in getattr(self, "_sec_labels", []):
            lab.setStyleSheet(f"color:{muted}; margin-top:8px; font-weight:600;")
        self.canvas.set_theme(dark)
        self._refresh_visible_rows()
        self._side_toggle_icons_refresh()
        self._sync_win_toggle_heights()
        self._replot()

    def _sync_win_toggle_heights(self) -> None:
        """Pin each window toggle's height to its row's spin box (v0.35.10).

        Must run after the stylesheet is applied — at _build_ui time the
        unpolished sizeHint is smaller than the themed, laid-out field."""
        pairs = (
            ("btn_win_pre", "sp_pre1"),
            ("btn_win_norm", "sp_norm1"),
            ("btn_win_bkg", "sp_kmin_bkg"),
            ("btn_win_k", "sp_kmin"),
            ("btn_win_r", "sp_rmin"),
        )
        for btn_name, ref_name in pairs:
            btn, ref = getattr(self, btn_name, None), getattr(self, ref_name, None)
            if btn is None or ref is None:
                continue
            ref.ensurePolished()
            h = ref.sizeHint().height()
            btn.setFixedHeight(h if h > 4 else 24)

    # ========== retranslation ==========
    def _retranslate(self):
        t = self.i18n.t
        self.setWindowTitle(f"{t('app_title')}  v{APP_VERSION}")
        self.lbl_app_title.setText(APP_NAME)
        self.lbl_app_sub.setText(t("app_subtitle"))
        self.btn_settings.setToolTip(t("settings_tip"))
        self.btn_settings.setText("⚙")
        self.btn_user_guide.setToolTip(t("user_guide_tip"))
        self.btn_user_guide.setAccessibleName(t("user_guide_tip"))
        if self._tray is not None:
            self._tray.setToolTip(f"{APP_NAME} v{APP_VERSION}")
            self._refresh_tray_menu()
        # toolbar
        # Compact ▾ tight against label; native menu-indicator hidden in QSS
        self.btn_open.setText(f"{t('open_data')}▾")
        self.btn_open.setToolTip(t("open_data_tip"))
        self.act_open_replace.setText(t("open_replace"))
        self.act_open_append.setText(t("open_append"))
        self.btn_quick_prj_dir.setToolTip(t("quick_prj_dir_tip"))
        self.btn_save.setText(t("save"))
        self.btn_save.setToolTip(t("save_tip"))
        self._rebuild_quick_prj_buttons()
        # groups panel
        self.lbl_groups.setText(t("groups"))
        self.cmb_curve_element.setToolTip(t("curve_element_tip"))
        self.btn_all.setText("A")
        self.btn_all.setToolTip(t("mark_all_detail"))
        self.btn_none.setText("U")
        self.btn_none.setToolTip(t("mark_none_detail"))
        self.btn_inv.setText("I")
        self.btn_inv.setToolTip(t("mark_invert_detail"))
        self.rad_plot_current.setText(t("plot_current"))
        self.rad_plot_current.setToolTip(t("plot_current_tip"))
        self.rad_plot_marked.setText(t("plot_marked"))
        self.rad_plot_marked.setToolTip(t("plot_marked_tip"))
        self.act_remove_current.setText(t("remove_current"))
        self.act_remove_current.setToolTip(t("remove_current_tip"))
        self.act_close_file.setText(t("close_file"))
        self.act_close_file.setToolTip(t("close_file_tip"))
        self.act_remove_marked.setText(t("remove_marked"))
        self.act_remove_marked.setToolTip(t("remove_marked_tip"))
        self.act_remove_all.setText(t("remove_all_groups"))
        self.act_remove_all.setToolTip(t("remove_all_groups_tip"))
        self.act_rename_current.setText(t("rename"))
        self.act_rename_current.setToolTip(t("rename_tip"))
        # current-group strip (above center plot)
        self.chk_freeze.setText(t("freeze"))
        self.btn_auto_edge.setText(t("auto_edge"))
        self.btn_auto_edge.setToolTip(t("auto_edge_tip"))
        self.chk_auto_edge_on_select.setText(t("auto_edge_on_select"))
        # mode tabs
        for key, tkey in (("norm", "tab_norm"), ("fit", "tab_fit"), ("energy", "tab_tools")):
            self._mode_btns[key].setText(t(tkey))
            self._mode_btns[key].setToolTip(t(tkey + "_tip"))
        # norm page — energy calibration first
        self.box_energy_cal.setTitle(t("box_energy_cal"))
        self.lbl_element.setText(t("z"))
        self.lbl_edge.setText(t("edge"))
        self.cmb_element.setToolTip(t("z"))
        self.cmb_edge.setToolTip(t("edge"))
        self.lbl_dE.setText(t("dE"))
        self.lbl_imp.setText(t("imp"))
        self.sp_eshift.setToolTip(t("dE"))
        self.sp_importance.setToolTip(t("imp_tip"))
        self.lbl_e0.setText(t("e0"))
        self.lbl_std_e.setText(t("std_e"))
        self.sp_std_e.setToolTip(t("std_e_tip"))
        self.chk_e0_auto.setText(t("auto"))
        self.chk_e0_auto.setToolTip(t("auto_e0_tip"))
        self.btn_calibrate.setText(t("op_calibrate"))
        self.btn_align.setToolTip(t("op_align_tip"))  # text via _tool_btns (Tools page)
        self.btn_apply_shift_marked.setText(t("btn_apply_shift_marked"))
        self.btn_apply_shift_marked.setToolTip(t("btn_apply_shift_marked_tip"))
        self.box_norm.setTitle(t("box_norm"))
        self.lbl_ord.setText(t("ord"))
        self.sp_nnorm.setToolTip(t("ord_tip"))
        self.chk_flatten.setText(t("flatten"))
        self.lbl_pre.setText(t("pre_range"))
        self.lbl_normr.setText(t("norm_range"))
        self.box_bkg.setTitle(t("box_bkg"))
        if hasattr(self, "lbl_clamp_lo"):
            self.lbl_clamp_lo.setText(t("clamp_lo"))
            self.lbl_clamp_hi.setText(t("clamp_hi"))
        self.box_ft.setTitle(t("box_ft"))
        self.lbl_win.setText(t("win"))
        if hasattr(self, "lbl_rwin"):
            self.lbl_rwin.setText(t("rwin"))
        self.chk_phase.setText(t("phase"))
        self.chk_phase.setToolTip(t("phase_tip"))
        self.lbl_pkw.setText(t("pkw"))
        self.btn_win_k.setToolTip(t("show_span_k_tip"))
        self.btn_win_r.setToolTip(t("show_span_r_tip"))
        self.btn_win_bkg.setToolTip(t("show_span_bkg_tip"))
        self.btn_win_pre.setToolTip(t("show_span_pre_tip"))
        self.btn_win_norm.setToolTip(t("show_span_norm_tip"))
        self.chk_autoscale_plot.setText(t("autoscale_e"))
        self.lbl_view_range.setText(t("view_range"))
        self.btn_toggle_left.setToolTip(t("toggle_left_panel"))
        self.btn_toggle_right.setToolTip(t("toggle_right_panel"))
        if hasattr(self, "box_auto_norm"):
            self.box_auto_norm.setTitle(t("box_auto_norm"))
        if hasattr(self, "btn_auto_norm"):
            self.btn_auto_norm.setText(t("auto_norm"))
            self.btn_auto_norm.setToolTip(t("auto_norm_tip"))
        if hasattr(self, "btn_auto_norm_reinit"):
            self.btn_auto_norm_reinit.setToolTip(t("auto_norm_reinit_tip"))
        if hasattr(self, "btn_ai_check"):
            self.btn_ai_check.setText(t("ai_check"))
            self.btn_ai_check.setToolTip(t("ai_check_tip"))
            self.chk_ai_cat_process.setText(t("ai_check_cat_process"))
            self.chk_ai_cat_process.setToolTip(t("ai_check_cat_process_tip"))
            self.chk_ai_cat_fit.setText(t("ai_check_cat_fit"))
            self.chk_ai_cat_fit.setToolTip(t("ai_check_cat_fit_tip"))
            self.btn_ai_check_apply.setText(t("ai_check_apply"))
            self.btn_ai_check_apply.setToolTip(t("ai_check_apply_tip"))
            self.btn_ai_check_undo.setToolTip(t("undo_tip"))
        if hasattr(self, "btn_auto_fit"):
            self.btn_auto_fit.setText(t("auto_fit"))
            self.btn_auto_fit.setToolTip(t("auto_fit_tip"))
        if hasattr(self, "btn_auto_fit_reinit"):
            self.btn_auto_fit_reinit.setToolTip(t("auto_fit_reinit_tip"))
        # Scope radios (Current / Marked / All) on every action row
        for mod, rads in (getattr(self, "_scope_radios", None) or {}).items():
            for scope, key in (
                ("current", "auto_scope_current"),
                ("marked", "auto_scope_marked"),
                ("all", "auto_scope_all"),
            ):
                r = rads.get(scope)
                if r is not None:
                    r.setText(t(key))
                    r.setToolTip(t("auto_scope_combo_tip"))
        # Apply buttons (single action + radios)
        for btn in (
            getattr(self, "btn_apply_norm", None),
            getattr(self, "btn_apply_bkg", None),
            getattr(self, "btn_apply_ft", None),
            getattr(self, "btn_apply_plot", None),
        ):
            if btn is not None:
                btn.setText(t("apply_action"))
                btn.setToolTip(t("apply_tip"))
        # Per-module Undo buttons
        for mod, btn in (getattr(self, "_undo_buttons", None) or {}).items():
            btn.setAccessibleName(t("undo"))
            if mod in ("auto_norm", "auto_fit", "ai_check"):
                continue
            if mod == "smooth":
                btn.setToolTip(t("undo_smooth_tip"))
            elif mod == "merge":
                btn.setToolTip(t("undo_merge_tip"))
            else:
                btn.setToolTip(t("undo_tip"))
        # plot tools on center data window
        self._space_btns["grid"].setToolTip(t("space_grid_tip"))
        for name in ("E", "k", "R", "q"):
            self._space_btns[name].setToolTip(t("space_tip", s=name))
        self._space_btns["kq"].setToolTip(t("space_kq_tip"))
        self._space_btns["wt"].setToolTip(t("space_wt_tip"))
        self.chk_mu.setText(t("s_mu"))
        self.chk_norm.setText(t("s_norm"))
        self.chk_bkg.setText(t("s_bkg"))
        self.chk_pre.setText(t("s_pre"))
        self.chk_post.setText(t("s_post"))
        self.chk_d1.setText(t("s_d1"))
        self.chk_d2.setText(t("s_d2"))
        self.btn_clear_marks.setText(t("btn_clear_reset"))
        self.btn_clear_marks.setToolTip(t("btn_clear_reset_tip"))
        for mode, b in getattr(self, "_pin_btns", {}).items():
            b.setToolTip(t(f"pin_{mode}_tip"))
        self._set_click_mode(self.click_mode)
        self.chk_auto.setText(t("auto_reprocess"))
        self.chk_auto.setToolTip(t("auto_reprocess_tip"))
        # fit page
        self.lbl_fit_mode.setText(t("fit_mode"))
        self.lbl_fit_kweights.setText(t("fit_kweights_fixed"))
        self.lbl_fit_plot.setText(t("fit_plot"))
        self.lbl_fit_plot_kw.setText(t("fit_plot_kw"))
        if hasattr(self, "box_auto_fit"):
            self.box_auto_fit.setTitle(t("box_auto_fit"))
        # (the Fit-page AI Assist row was merged into the top ai_check bar)
        self.box_fit_win.setTitle(t("box_fit_win"))
        self.box_gds.setTitle(t("box_gds"))
        self.tbl_gds.setHorizontalHeaderLabels(
            [t("gds_col_name"), t("gds_col_value"), t("gds_col_kind"), t("gds_col_expr"), "min", "max"]
        )
        self.btn_model_a.setText(t("btn_model_a"))
        self.btn_gds_add.setText(t("btn_gds_add"))
        self.btn_gds_del.setText(t("btn_del_sel"))
        self.btn_gds_clear.setText(t("btn_gds_clear"))
        self.box_fit_paths.setTitle(t("box_paths"))
        self.tbl_paths.setHorizontalHeaderLabels(
            [t("path_col_use"), t("path_col_elem"), t("path_col_label"), t("path_col_file"), "s02", "e0", "ΔR", "σ²", t("path_col_degen")]
        )
        self.btn_model_a_paths.setText(t("btn_model_a_paths_short"))
        self.btn_model_a_paths.setToolTip(t("btn_model_a_paths_tip"))
        self.btn_feff_ui.setText("FEFF")
        self.btn_feff_ui.setToolTip(t("prepare_feff_tip"))
        self.btn_path_add.setText(t("btn_path_add_short"))
        self.btn_path_add.setToolTip(t("btn_path_add_tip"))
        self.btn_path_del.setText(t("btn_path_remove_short"))
        self.btn_path_del.setToolTip(t("btn_path_remove_tip"))
        self.lbl_fit_history.setText(t("fit_history"))
        self.cmb_fit_history.setToolTip(t("fit_history_tip"))
        self.btn_hist_restore.setText(t("btn_hist_restore"))
        self.btn_hist_restore.setToolTip(t("btn_hist_restore_tip"))
        self.btn_hist_clear.setText(t("btn_hist_clear"))
        self.btn_hist_clear.setToolTip(t("btn_hist_clear_tip"))
        self.btn_fit.setText(t("run_fit"))
        self.btn_validate_ui.setText(t("run_validate"))
        self.btn_validate_ui.setToolTip(t("run_validate_tip"))
        self.btn_demo_folder.setText("📂")
        self.btn_demo_folder.setToolTip(t("demo_folder_tip"))
        self.lbl_fit_grade.setText(t("fit_quality_ph"))
        self.lbl_fit_quality.setText("")
        self.lbl_fit_warning.setText("")
        if hasattr(self, "btn_fit_export_all"):
            self.btn_fit_export_all.setText(t("fit_export_all"))
            self.btn_fit_export_all.setToolTip(t("fit_export_all_tip"))
        self.txt_fit_result.setPlaceholderText(t("fit_result_ph"))
        # tools page
        if hasattr(self, "box_tools_smooth"):
            self.box_tools_smooth.setTitle(t("box_tools_smooth"))
            self.rad_tool_smooth.setText(t("op_smooth_mode"))
            self.rad_tool_rebin.setText(t("op_rebin_mode"))
            self.lbl_boxcar.setText(t("boxcar_width_short"))
            self.sp_boxcar.setToolTip(t("boxcar_width"))
            self.btn_apply_smooth.setText(t("apply_action"))
            self.btn_apply_smooth.setToolTip(t("apply_tip"))
            self.chk_rebin_auto.setText(t("rebin_auto"))
            self.chk_rebin_auto.setToolTip(t("rebin_auto_tip"))
            if self.chk_rebin_auto.isChecked():
                for w in (self.sp_rebin_pre1, self.sp_rebin_xanes, self.sp_rebin_exafs2):
                    w.setSpecialValueText(t("rebin_auto"))
            self.lbl_rebin_method.setText(t("rebin_method"))
            self.box_tools_deglitch.setTitle(t("box_tools_deglitch"))
            if hasattr(self, "box_tools_analysis"):
                self.box_tools_analysis.setTitle(t("sec_analysis"))
            self.rad_deglitch_auto.setText(t("op_deglitch_auto_mode"))
            self.rad_deglitch_click.setText(t("op_deglitch_click_mode"))
            self.rad_dg_point.setText(t("dg_style_point"))
            self.rad_dg_before.setText(t("dg_style_before"))
            self.rad_dg_after.setText(t("dg_style_after"))
            self.rad_dg_range.setText(t("dg_style_range"))
            self.lbl_dg_e.setText("E")
            self.lbl_dg_e1.setText("E1")
            self.lbl_dg_e2.setText("E2")
            self.pin_dg_e.setToolTip(t("pin_dg_e_tip"))
            self.pin_dg_e1.setToolTip(t("pin_dg_e1_tip"))
            self.pin_dg_e2.setToolTip(t("pin_dg_e2_tip"))
            self.btn_apply_deglitch.setText(t("apply_action"))
            self.btn_apply_deglitch.setToolTip(t("apply_tip"))
            self._update_smooth_rebin_ui()
            self._update_deglitch_ui()
        if hasattr(self, "box_tools_export"):
            self.box_tools_export.setTitle(t("box_tools_export"))
        if hasattr(self, "box_tools_plugins"):
            self.box_tools_plugins.setTitle(t("box_tools_plugins"))
        if hasattr(self, "cmb_align_ref"):
            self.cmb_align_ref.setToolTip(t("align_ref_tip"))
            self.lbl_align_hint.setText(t("align_hint_label"))
        if hasattr(self, "lbl_plugins_hint"):
            self.lbl_plugins_hint.setText(t("plugins_hint"))
        if hasattr(self, "_plugin_btns"):
            zh = self.i18n.lang != "en"
            for btn, sid in self._plugin_btns:
                slot = next(
                    (s for s in plugins_manager.PLUGIN_SLOTS if s["id"] == sid), None
                )
                if not slot:
                    continue
                title = slot["title_zh"] if zh else slot["title_en"]
                st = next(
                    (
                        s
                        for s in plugins_manager.list_slot_status(self._plugins_dir())
                        if s["id"] == sid
                    ),
                    None,
                )
                # Button labels: product name only (no version number on the chrome)
                btn.setText(str(title))
                btn.setEnabled(bool((st or {}).get("present")))
                btn.setToolTip(str(title))
            if hasattr(self, "lbl_export_folder"):
                self.lbl_export_folder.setText(t("export_folder_lbl"))
                folder_tip = t("settings_data_export_folder")
                self.lbl_export_folder.setToolTip(folder_tip)
                self.ed_export_dir.setToolTip(folder_tip)
                self.btn_export_dir_browse.setText(t("browse"))
                self.btn_export_dir_open.setText(t("open_folder"))
                self.lbl_export_prefix.setText(t("export_name_prefix_lbl"))
                self.lbl_export_suffix.setText(t("export_name_suffix_lbl"))
                affix_tip = t("export_name_affix_tip")
                self.ed_export_prefix.setToolTip(affix_tip)
                self.ed_export_suffix.setToolTip(affix_tip)
                if not self.ed_export_dir.hasFocus():
                    self.ed_export_dir.setText(str(self._data_export_dir()))
            self.lbl_export_fmt.setText(t("export_format"))
            self.rad_export_csv.setText(t("export_fmt_csv_name"))
            self.lbl_export_csv_note.setText(t("export_fmt_csv"))
            self.rad_export_xlsx.setText(t("export_fmt_xlsx_name"))
            self.lbl_export_xlsx_note.setText(t("export_fmt_xlsx"))
            self.lbl_export_src.setText(t("export_source"))
            self.rad_export_marked.setText(t("export_src_marked"))
            self.rad_export_current.setText(t("export_src_current"))
            self.lbl_export_kinds.setText(t("export_kinds"))
            # per-kind checkbox labels (Athena set)
            for key, chk in getattr(self, "_export_kind_checks", {}).items():
                chk.setText(t(f"export_kind_{key}"))
                chk.setToolTip(t(f"export_kind_{key}_tip") if t(f"export_kind_{key}_tip") != f"export_kind_{key}_tip" else t(f"export_kind_{key}"))
            if hasattr(self, "btn_export_kinds_all"):
                self.btn_export_kinds_all.setText(t("export_kinds_all"))
                self.btn_export_kinds_none.setText(t("export_kinds_none"))
            self.lbl_export_hint.setText(t("export_hint"))
            if hasattr(self, "chk_export_wavelet"):
                self.chk_export_wavelet.setText(t("export_include_wt"))
                self.chk_export_wavelet.setToolTip(t("export_include_wt_tip"))
                self.chk_export_wavelet_png.setText(t("export_include_wt_png"))
                self.chk_export_wavelet_png.setToolTip(t("export_include_wt_png_tip"))
                self.lbl_export_wt_note.setText(t("export_include_wt_note"))
            self.btn_export_go.setText(t("export_go"))
        for lab, key in self._sec_labels:
            lab.setText(t(key))
        for b, key in self._tool_btns:
            b.setText(t(key))
            if key == "op_wavelet":
                b.setToolTip(t("op_wavelet_tip"))
        # log
        self.lbl_log.setText(t("log"))
        self.btn_log_toggle.setToolTip(t("log_toggle_tip"))
        if hasattr(self, "btn_ai_menu"):
            self.btn_ai_menu.setToolTip(t("ai_menu_tip"))
            self.btn_ai_menu.setText(">_")
        if hasattr(self, "btn_open_output"):
            self.btn_open_output.setToolTip(t("open_output_tip"))
        if hasattr(self, "_eng_mode"):
            self._eng_mode.retranslate()
        self._sync_action_btn_widths()
        # dynamic labels
        self._update_meta()
        self._refresh_fit_panel()

    def _toggle_log(self, checked: bool):
        self.txt_log.setVisible(checked)
        # Compact chevrons (▾ open / ▸ collapsed) — avoid Qt native ArrowType chrome
        self.btn_log_toggle.setText("▾" if checked else "▸")

    def _set_left_mode(self, mode: str):
        """Switch right parameter page: norm | fit | energy | merge."""
        if not hasattr(self, "left_stack"):
            return
        if mode not in getattr(self, "_mode_index", {}):
            mode = "norm"
        self.left_stack.setCurrentIndex(self._mode_index[mode])
        btn = self._mode_btns.get(mode)
        if btn is not None and not btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)
        if mode == "fit":
            self._refresh_fit_panel()
        self._status(f"Panel: {mode}", 4000)

    def _refresh_fit_panel(self):
        if hasattr(self, "lbl_fit_win"):
            try:
                s = self._settings_from_controls()
                self.lbl_fit_win.setText(
                    f"k = {s.kmin:.2f} – {s.kmax:.2f}; dk = {s.dk:.2f}; {s.kwindow}\n"
                    f"R = {s.rmin:.2f} – {s.rmax:.2f}; dR = {s.dr:.2f}; {s.rwindow}"
                )
            except Exception:
                pass
        self._refresh_fit_history()

    # ========== Fit page: GDS / path tables + history ==========
    def _refresh_fit_history(self):
        if not hasattr(self, "cmb_fit_history"):
            return
        cur_file = self.cmb_fit_history.currentData()
        self.cmb_fit_history.blockSignals(True)
        self.cmb_fit_history.clear()
        rows = list_fit_history(self._history_cache_dir())
        if not rows:
            self.cmb_fit_history.addItem(self.i18n.t("hist_empty"), None)
        for row in rows:
            rf = row.get("r_factor")
            rf_s = f"R={rf:.4f}" if isinstance(rf, (int, float)) and np.isfinite(rf) else "R=?"
            curve_state = self.i18n.t(
                "history_curves_saved" if row.get("has_curves") else "history_report_only"
            )
            self.cmb_fit_history.addItem(
                f"{row.get('stamp', '')}  {rf_s}  [{curve_state}]  {row.get('data', '')}", row.get("file")
            )
        if cur_file:
            idx = self.cmb_fit_history.findData(cur_file)
            if idx >= 0:
                self.cmb_fit_history.setCurrentIndex(idx)
        self.cmb_fit_history.blockSignals(False)

    def _set_fit_result_text(self, text: str) -> None:
        """Show a fit report in the central data area without shrinking the plot controls."""
        self.txt_fit_result.setPlainText(str(text or ""))
        self.txt_fit_result.setVisible(True)
        self.fit_display_splitter.setSizes([620, 280])

    def _history_selected(self, *_args) -> None:
        """Preview the stored report and redraw its saved curves when present."""
        if self.cmb_fit_history.currentData():
            self._preview_history_snapshot()

    def _restore_fit_history(self):
        f = self.cmb_fit_history.currentData()
        if not f:
            self._log(self.i18n.t("hist_empty"))
            return
        try:
            snap = load_fit_snapshot(f)
        except Exception as exc:
            self._log(f"history load failed: {exc}")
            return
        self.tbl_gds.setRowCount(0)
        for d in snap.get("gds", []):
            self._gds_add_row(GDSParam.from_dict(d))
        self.tbl_paths.setRowCount(0)
        for d in snap.get("paths", []):
            self._paths_add_row(PathDef.from_dict(d))
        st = snap.get("settings", {}) or {}
        self._block = True
        try:
            for attr, spin in (
                ("kmin", self.sp_kmin), ("kmax", self.sp_kmax), ("dk", self.sp_dk),
                ("rmin", self.sp_rmin), ("rmax", self.sp_rmax), ("dr", self.sp_dr),
                ("rbkg", self.sp_rbkg),
            ):
                if attr in st and st[attr] is not None:
                    spin.setValue(float(st[attr]))
            self._set_win_combo(self.cmb_kwin, st.get("kwindow"))
            self._set_win_combo(self.cmb_rwin, st.get("rwindow"))
        finally:
            self._block = False
        self._log(self._bi(f"已恢复历史拟合: {snap.get('stamp', '')}", f"Restored fit snapshot: {snap.get('stamp', '')}"))
        self._preview_history_snapshot()
        self._refresh_fit_panel()

    def _clear_fit_history(self):
        """Remove saved snapshots after explicit confirmation, not fit exports."""
        if not list_fit_history(self._history_cache_dir()):
            self._log(self.i18n.t("hist_empty"))
            return
        answer = QMessageBox.question(
            self,
            self.i18n.t("hist_clear_title"),
            self.i18n.t("hist_clear_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        n = clear_fit_history(self._history_cache_dir())
        self._refresh_fit_history()
        self._log(self.i18n.t("hist_cleared", n=n))

    def _preview_history_snapshot(self):
        f = self.cmb_fit_history.currentData()
        if not f:
            self._log(self.i18n.t("hist_empty"))
            return
        try:
            snap = load_fit_snapshot(f)
            self._set_fit_result_text(snap.get("report_text", "") or self._bi("(无报告)", "(no report)"))
            raw_views = snap.get("plot_views") or {}
            views = {}
            for weight, view in raw_views.items():
                try:
                    views[int(weight)] = {
                        str(name): np.asarray(values, dtype=float)
                        for name, values in (view or {}).items()
                    }
                except (TypeError, ValueError):
                    continue
            if views:
                historic = FitResult(
                    ok=True,
                    message="history preview",
                    params=dict(snap.get("params") or {}),
                    statistics=dict(snap.get("statistics") or {}),
                    paths=list(snap.get("path_results") or []),
                    correlations=[tuple(c) for c in (snap.get("correlations") or [])],
                    report_text=str(snap.get("report_text") or ""),
                    plot_views=views,
                )
                self.last_fit = historic
                self._update_fit_quality(historic)
                idx = self.cmb_fit_plot.findData("rmr")
                if idx >= 0:
                    # ``setCurrentIndex`` emits nothing when Rmr is already
                    # selected.  Redraw explicitly so a newly selected
                    # history snapshot never leaves the old/blank curves up.
                    self.cmb_fit_plot.blockSignals(True)
                    self.cmb_fit_plot.setCurrentIndex(idx)
                    self.cmb_fit_plot.blockSignals(False)
                    self.canvas.set_mode("fit_rmr")
                    self._replot()
                else:
                    self._replot()
            else:
                # Older v0.10.3-and-earlier snapshots have reports only;
                # never leave a misleading blank fit view or curve from another
                # history entry.  Their original data/model arrays were not
                # persisted, so an exact historical plot cannot be recreated.
                self.last_fit = None
                self._set_fit_result_text(
                    f"{self.i18n.t('history_report_only_note')}\n\n"
                    f"{snap.get('report_text', '') or self._bi('(无报告)', '(no report)')}"
                )
                self.canvas.set_mode("grid")
                idx = self.cmb_fit_plot.findData("standard")
                if idx >= 0:
                    self.cmb_fit_plot.blockSignals(True)
                    self.cmb_fit_plot.setCurrentIndex(idx)
                    self.cmb_fit_plot.blockSignals(False)
                self._replot()
                self._log(self._bi("该旧历史未保存曲线，仅显示报告", "This older history has no saved curves; report only"))
        except Exception as exc:
            self._log(f"history load failed: {exc}")

    def _gds_add_row(self, p: GDSParam):
        r = self.tbl_gds.rowCount()
        self.tbl_gds.insertRow(r)
        self.tbl_gds.setItem(r, 0, QTableWidgetItem(p.name))
        self.tbl_gds.setItem(r, 1, QTableWidgetItem("" if p.value is None else f"{p.value:g}"))
        cmb = QComboBox()
        cmb.addItems(["vary", "fix", "expr", "skip"])
        cmb.setCurrentText(p.kind if p.kind in ("vary", "fix", "expr", "skip") else "vary")
        self.tbl_gds.setCellWidget(r, 2, cmb)
        self.tbl_gds.setItem(r, 3, QTableWidgetItem(p.expr or ""))
        self.tbl_gds.setItem(r, 4, QTableWidgetItem("" if p.min is None else f"{p.min:g}"))
        self.tbl_gds.setItem(r, 5, QTableWidgetItem("" if p.max is None else f"{p.max:g}"))

    def _gds_from_table(self) -> List[GDSParam]:
        out: List[GDSParam] = []
        for r in range(self.tbl_gds.rowCount()):
            def _txt(col: int) -> str:
                it = self.tbl_gds.item(r, col)
                return it.text().strip() if it else ""

            def _num(col: int):
                t = _txt(col)
                if not t:
                    return None
                try:
                    return float(t)
                except ValueError:
                    return None

            name = _txt(0)
            if not name:
                continue
            w = self.tbl_gds.cellWidget(r, 2)
            kind = w.currentText() if isinstance(w, QComboBox) else "vary"
            out.append(GDSParam(name, _num(1) or 0.0, kind, _txt(3), _num(4), _num(5)))
        return out

    # common absorber elements for the elem-column filename guess (2-letter first)
    _ELEMENT_SYMBOLS = (
        "Pt Fe Ni Cu Co Mn Au Pd Ce La Se As Mo Zn Ti Cr W Ag Sn Sb In Ga Ge "
        "Pb Bi Rb Sr Zr Nb Ru Rh Ir Os Hf Ta Re Cd Hg Tl Ba Cs Ca Na Mg Al Si "
        "Sc Y U Th K V P S O N C H".split()
    )

    @classmethod
    def _guess_elem_from_name(cls, name: str) -> str:
        """Best-effort absorber-element guess from a feff filename (editable after)."""
        if not name:
            return ""
        stem = Path(name).stem
        tokens = [t for t in re.split(r"[^A-Za-z]+", stem) if t]
        syms = set(cls._ELEMENT_SYMBOLS)
        for tok in tokens:
            if tok in syms:
                return tok
        for tok in tokens:
            for sym in cls._ELEMENT_SYMBOLS:
                if len(sym) == 2 and tok.startswith(sym):
                    return sym
        return ""

    def _paths_add_row(self, pd: PathDef):
        r = self.tbl_paths.rowCount()
        self.tbl_paths.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Checked if pd.use else Qt.CheckState.Unchecked)
        # full PathDef rides along so table-only columns (third/ei) survive round-trips
        chk.setData(Qt.ItemDataRole.UserRole, pd)
        self.tbl_paths.setItem(r, 0, chk)
        # Element is an explicit selectable assignment and sits before the
        # human-facing label, so a multi-element path table reads naturally.
        elem = (pd.elem or "").strip() or self._guess_elem_from_name(pd.feff_file)
        elem_box = QComboBox()
        elem_box.setObjectName("pathElementCombo")
        # An absorber is one periodic-table symbol.  A non-editable selector
        # keeps Qt from overlaying a QLineEdit border on the table grid line.
        elem_box.setEditable(False)
        elem_box.setMinimumWidth(72)
        elem_box.addItem("")
        elem_box.addItems(self._ELEMENT_SYMBOLS)
        if elem and elem not in self._ELEMENT_SYMBOLS:
            # Preserve an imported legacy/custom tag rather than silently
            # clearing it when the user only opens the path table.
            elem_box.addItem(elem)
        elem_box.setCurrentText(elem)
        self.tbl_paths.setCellWidget(r, 1, elem_box)
        self.tbl_paths.setItem(r, 2, QTableWidgetItem(pd.label))
        f = QTableWidgetItem(Path(pd.feff_file).name if pd.feff_file else "")
        f.setFlags(f.flags() & ~Qt.ItemFlag.ItemIsEditable)
        f.setToolTip(pd.feff_file)
        f.setData(Qt.ItemDataRole.UserRole, pd.feff_file)
        self.tbl_paths.setItem(r, 3, f)
        self.tbl_paths.setItem(r, 4, QTableWidgetItem(pd.s02))
        self.tbl_paths.setItem(r, 5, QTableWidgetItem(pd.e0))
        self.tbl_paths.setItem(r, 6, QTableWidgetItem(pd.deltar))
        self.tbl_paths.setItem(r, 7, QTableWidgetItem(pd.sigma2))
        self.tbl_paths.setItem(r, 8, QTableWidgetItem("" if pd.degen is None else f"{pd.degen:g}"))

    def _paths_from_table(self) -> List[PathDef]:
        out: List[PathDef] = []
        for r in range(self.tbl_paths.rowCount()):
            def _txt(col: int) -> str:
                it = self.tbl_paths.item(r, col)
                return it.text().strip() if it else ""

            use_it = self.tbl_paths.item(r, 0)
            use = bool(use_it and use_it.checkState() == Qt.CheckState.Checked)
            f_it = self.tbl_paths.item(r, 3)
            feff_file = str(f_it.data(Qt.ItemDataRole.UserRole) or "") if f_it else ""
            degen = None
            try:
                degen = float(_txt(8)) if _txt(8) else None
            except ValueError:
                degen = None
            elem_box = self.tbl_paths.cellWidget(r, 1)
            elem = elem_box.currentText().strip() if isinstance(elem_box, QComboBox) else _txt(1)
            pd = PathDef(
                label=_txt(2),
                feff_file=feff_file,
                use=use,
                s02=_txt(4) or "1.0",
                e0=_txt(5) or "0.0",
                deltar=_txt(6) or "0.0",
                sigma2=_txt(7) or "0.0",
                degen=degen,
                elem=elem,
            )
            # third/ei have no table columns — recover them from the stored PathDef
            stored = use_it.data(Qt.ItemDataRole.UserRole) if use_it else None
            if isinstance(stored, PathDef):
                pd.third = stored.third
                pd.ei = stored.ei
            out.append(pd)
        return out

    def _paths_for_group(self, g, paths):
        """Element-matched path subset for group *g* (v0.9.0 assignment).

        Rows tagged in the elem column must match the group's element; untagged
        rows are the fallback when nothing is tagged for that element. Returns
        (used_paths, kind): no-element | matched | fallback-untagged | mismatch.
        """
        used_all = [p for p in paths if p.use and p.feff_file]
        ge = (getattr(g, "element", "") or "").strip()
        if not ge:
            return used_all, "no-element"
        tagged = [p for p in used_all if (p.elem or "").strip().lower() == ge.lower()]
        if tagged:
            return tagged, "matched"
        untagged = [p for p in used_all if not (p.elem or "").strip()]
        if untagged:
            return untagged, "fallback-untagged"
        return [], "mismatch"

    def _del_selected_rows(self, tbl: QTableWidget):
        rows = sorted({i.row() for i in tbl.selectedIndexes()}, reverse=True)
        for r in rows:
            tbl.removeRow(r)
        if not rows:
            self._log(self._bi("先在表格里选中要删的行", "Select table row(s) to remove first"))

    def _preset_gds(self):
        gds, _ = model_a_preset()
        self.tbl_gds.setRowCount(0)
        for p in gds:
            self._gds_add_row(p)
        self._log(self._bi("已填入预设 GDS 参数", "Preset GDS parameters loaded"))

    def _preset_paths(self):
        _, plist = model_a_preset()
        self.tbl_paths.setRowCount(0)
        for pd in plist:
            self._paths_add_row(pd)
        self._log(self._bi("已填入预设路径", "Preset paths loaded"))

    def _load_model_a_preset(self):
        self._preset_gds()
        self._preset_paths()

    def _add_feff_files(self):
        start = self.paths["feff_out"]
        start_dir = str(start) if start.is_dir() else str(self.paths["data"])
        dlg = QFileDialog(self, self._bi("选择 feffNNNN.dat", "Select feffNNNN.dat"), start_dir)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dlg.setNameFilters(["FEFF paths (feff*.dat *.dat)", "All files (*.*)"])
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if not dlg.exec():
            return
        for fpath in dlg.selectedFiles():
            self._paths_add_row(PathDef(label=Path(fpath).stem, feff_file=fpath, use=True))

    def _status(self, msg: str, msec: int = 8000):
        """All transient tips go into the log terminal only."""
        if msg:
            self._log(msg)

    def _log(self, msg: str):
        if hasattr(self, "txt_log") and self.txt_log is not None:
            self.txt_log.appendPlainText(str(msg))

    def _schedule(self, *_):
        if self._block or not self.chk_auto.isChecked():
            return
        if getattr(self, "_worker_busy", False):
            return  # larch already running on a Worker thread — no GUI-thread reprocess
        self._timer.start()

    def _mark_manual_e0_pending(self, *_args) -> None:
        if not self._block:
            self._e0_manual_pending = True

    def _mark_manual_e0_spin_change(self, *_args) -> None:
        if not self._block and self.sp_e0.hasFocus():
            self._e0_manual_pending = True

    def _commit_manual_e0(self) -> None:
        """Accept a typed E0 once, disable Auto E0, then run one update."""
        if self._block or not getattr(self, "_e0_manual_pending", False):
            return
        self._e0_manual_pending = False
        self._block = True
        try:
            self.chk_e0_auto.setChecked(False)
        finally:
            self._block = False
        if not self.chk_auto.isChecked() or getattr(self, "_worker_busy", False):
            return
        self._timer.stop()
        self._timer.start(80)

    # ========== Group list ==========
    def _curve_element_filter(self) -> str:
        if not hasattr(self, "cmb_curve_element"):
            return ""
        return str(self.cmb_curve_element.currentData() or "").strip()

    def _group_matches_element_filter(self, g) -> bool:
        wanted = self._curve_element_filter().lower()
        if not wanted:
            return True
        return str(getattr(g, "element", "") or "").strip().lower() == wanted

    def _group_row_text(self, g) -> str:
        edge_tag = " ".join(
            x for x in (str(g.element or "").strip(), str(g.edge or "").strip()) if x
        )
        return f"{g.name}" + (f"  [{edge_tag}]" if edge_tag else "")

    def _visible_groups_for_filter(self) -> List["GroupRecord"]:
        """Groups shown in the left list (element filter, strict)."""
        return self._groups_matching_element_filter()

    def _groups_matching_element_filter(self) -> List["GroupRecord"]:
        """Working set for batch ops: the left-list element filter, no current exception."""
        return [g for g in self.project.groups if self._group_matches_element_filter(g)]

    def _marked_groups_for_scope(self) -> List["GroupRecord"]:
        """Marked ∩ element filter. Filter All → every marked group."""
        return [
            g for g in self.project.marked_groups()
            if self._group_matches_element_filter(g)
        ]

    def _refresh_curve_element_choices(self) -> None:
        """Keep the curve-element selector in sync with imported groups."""
        if not hasattr(self, "cmb_curve_element"):
            return
        current = self._curve_element_filter()
        elements = sorted(
            {
                str(g.element or "").strip()
                for g in self.project.groups
                if str(g.element or "").strip()
            },
            key=str.lower,
        )
        self.cmb_curve_element.blockSignals(True)
        self.cmb_curve_element.clear()
        self.cmb_curve_element.addItem(self.i18n.t("curve_element_all"), "")
        for elem in elements:
            self.cmb_curve_element.addItem(elem, elem)
        idx = self.cmb_curve_element.findData(current)
        self.cmb_curve_element.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_curve_element.blockSignals(False)

    def _curve_element_changed(self, *_args) -> None:
        if self._block:
            return
        wanted = self._curve_element_filter().lower()
        cur = self.project.current
        if wanted and (
            cur is None
            or str(cur.element or "").strip().lower() != wanted
        ):
            matching = [
                g for g in self.project.groups
                if str(g.element or "").strip().lower() == wanted
            ]
            # First row in the filtered list, not first *marked* curve.
            # Marked overlay used to promote Ni file 2 and then draw the
            # pre-edge window from the previous element's E0 still in the
            # spin (guides ran before the new group was processed) (v1.15.29).
            target = matching[0] if matching else None
            if target is not None:
                self.project.set_current(target.id)
                self.last_fit = None
                if target.energy is not None and target.mu is not None:
                    target.process()
                self._load_group_to_controls(target)
                self._update_meta()
        self._refresh_list()
        self._replot(preserve_view=False)  # filter change = new working set, re-frame

    def _reapply_element_filter(self) -> None:
        """Rebuild the list after a delayed element change (auto-edge on select).

        Must run *after* ``itemClicked`` so the first mark-icon click is not eaten.
        If the current group left the filter, promote a matching group.
        """
        if self._block:
            return
        promoted = False
        wanted = self._curve_element_filter().lower()
        if wanted:
            cur = self.project.current
            if cur is None or not self._group_matches_element_filter(cur):
                matching = [
                    g
                    for g in self.project.groups
                    if str(g.element or "").strip().lower() == wanted
                ]
                if matching:
                    self.project.set_current(matching[0].id)
                    self.last_fit = None
                    self._load_group_to_controls(matching[0])
                    promoted = True
        self._refresh_list()
        # re-frame only when the plotted element actually changed
        self._replot(preserve_view=not promoted)

    def _refresh_list(self):
        self._block = True
        self._refresh_curve_element_choices()
        self.list_groups.blockSignals(True)
        self.list_groups.clear()
        cur = self.project.current_id
        wanted_element = self._curve_element_filter().lower()
        for g in self.project.groups:
            if wanted_element and str(g.element or "").strip().lower() != wanted_element:
                continue
            item = QListWidgetItem(self._group_row_text(g))
            # No ItemIsUserCheckable / checkState: on Windows the native checkbox
            # primitive is always painted for checkable items and bleeds out from
            # under any QSS indicator styling (the "white box" artifact). The
            # mark glyph is a plain icon instead; clicking the icon zone toggles.
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(Qt.ItemDataRole.UserRole, g.id)
            self._paint_group_item(item, g)
            self.list_groups.addItem(item)
            if g.id == cur:
                # set current WITHOUT selecting it: blue is reserved for marked
                # groups; any transient click-selection is cleared below.
                self.list_groups.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.list_groups.clearSelection()
        self.list_groups.blockSignals(False)
        self._refresh_align_ref_choices()
        self._block = False
        self._update_meta()

    def _repaint_group_list_items(self) -> None:
        """Update mark/current/frozen styling in place. Do not rebuild.

        ``_group_selected`` used to call ``_refresh_list()``, which ``clear()``s
        the widget before ``itemClicked`` runs. Clicking the mark icon on a
        *non-current* row then did nothing — first click only made it current,
        second click (same row, no rebuild) finally toggled the mark.
        """
        by_id = {g.id: g for g in self.project.groups}
        for i in range(self.list_groups.count()):
            item = self.list_groups.item(i)
            if item is None:
                continue
            g = by_id.get(item.data(Qt.ItemDataRole.UserRole))
            if g is not None:
                item.setText(self._group_row_text(g))
                self._paint_group_item(item, g)

    def _refresh_visible_rows(self) -> None:
        """Repaint existing rows; do not re-apply the element filter.

        ``_refresh_list`` drops rows whose element no longer matches the
        left filter. Freeze / Align / ΔE / Auto-norm / 自动寻边 must not
        hide a file the user already has on screen (v1.15.20). Filter
        combo changes still call ``_refresh_list``.
        """
        self._refresh_curve_element_choices()
        self._repaint_group_list_items()
        self._refresh_align_ref_choices()

    def _refresh_align_ref_choices(self):
        """Keep the Align-peak reference combo in sync with the group list.

        Selection policy: keep the user's explicit pick while its group
        exists; otherwise fall back to the current group.
        """
        cmb = getattr(self, "cmb_align_ref", None)
        if cmb is None:
            return
        prev = cmb.currentData()
        cur_id = self.project.current_id
        cmb.blockSignals(True)
        cmb.clear()
        for g in self.project.groups:
            cmb.addItem(g.name, g.id)
        idx = cmb.findData(prev)
        if idx < 0 and cur_id:
            idx = cmb.findData(cur_id)
        cmb.setCurrentIndex(max(0, idx))
        cmb.blockSignals(False)

    def _paint_mark_list_item(
        self,
        item: QListWidgetItem,
        *,
        marked: bool,
        is_current: bool,
        frozen: bool = False,
    ) -> None:
        """Shared list-row paint (main Groups list + import batch list).

        Contract (do not diverge — see AGENTS.md UI notes):
        - Mark = **icon only** (checkbox_checked / checkbox_empty SVG). Never
          ``ItemIsUserCheckable`` / ``checkState`` (Windows paints a native
          checkbox that bleeds under QSS).
        - Current = **bold + underline** text.
        - Frozen = italic + muted foreground.
        - No persistent Qt selection blue (callers clearSelection).
        """
        f = item.font()
        f.setBold(bool(is_current))
        f.setUnderline(bool(is_current))
        f.setItalic(bool(frozen))
        item.setFont(f)
        item.setIcon(self._icon_checked if marked else self._icon_unchecked)
        item.setBackground(QBrush())
        if frozen:
            item.setForeground(
                QColor("#9fb0c0" if getattr(self, "_theme_dark", False) else "#6b625d")
            )
        else:
            item.setForeground(QBrush())

    def _paint_group_item(self, item: QListWidgetItem, g) -> None:
        """Marked = ✓ icon; current = bold+underline; frozen = italic+muted."""
        self._paint_mark_list_item(
            item,
            marked=bool(g.marked),
            is_current=(g.id == self.project.current_id),
            frozen=bool(getattr(g, "frozen", False)),
        )

    def _mark_list_icon_zone_hit(self, list_widget: QListWidget, item: QListWidgetItem) -> bool:
        """True if the cursor is over the left ~28px mark-icon zone of *item*."""
        rect = list_widget.visualItemRect(item)
        pos = list_widget.viewport().mapFromGlobal(QCursor.pos())
        return rect.left() - 2 <= pos.x() <= rect.left() + 28

    def _make_scope_action_row(
        self, module: str, run_btn: "QPushButton", undo_btn=None, extra=None
    ) -> Tuple["QWidget", "QPushButton"]:
        """
        Build [run_btn] ○ Current ○ Marked ○ All [↶] for a module.
        Radios are exclusive; default = Current.
        """
        rad_cur, rad_mk, rad_all = self._make_scope_radios(module)
        host = _action_scope_row(run_btn, rad_cur, rad_mk, rad_all, undo_btn, extra)
        if extra is None:
            # rows with an extra field keep the button at its own width so
            # the row still fits; the others share one width
            self._action_btns.append(run_btn)
        return host, run_btn

    def _sync_action_btn_widths(self) -> None:
        """Give every short action button the same width (v3.19.6).

        Width = the widest text among them in the current language, so the
        ○ Current ○ Marked ○ All columns and the ↶ icons line up.
        """
        btns = [b for b in getattr(self, "_action_btns", []) if b is not None]
        if not btns:
            return
        for b in btns:
            b.setMinimumWidth(0)
        width = max(ACTION_BTN_MIN_W, max(b.sizeHint().width() for b in btns))
        for b in btns:
            b.setMinimumWidth(width)

    def _make_scope_radios(self, module: str) -> tuple:
        """○ Current ○ Marked ○ All for *module*, registered in _scope_radios."""
        rad_cur = QRadioButton()
        rad_mk = QRadioButton()
        rad_all = QRadioButton()
        rad_cur.setChecked(True)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for r, scope in ((rad_cur, "current"), (rad_mk, "marked"), (rad_all, "all")):
            r.setProperty("scope", scope)
            grp.addButton(r)
        self._scope_radios[module] = {
            "current": rad_cur,
            "marked": rad_mk,
            "all": rad_all,
            "group": grp,
        }
        return rad_cur, rad_mk, rad_all

    def _scope_from_module(self, module: str) -> str:
        rads = (getattr(self, "_scope_radios", None) or {}).get(module) or {}
        for scope in ("current", "marked", "all"):
            r = rads.get(scope)
            if r is not None and r.isChecked():
                return scope
        return "current"

    def _on_apply_smooth_rebin_clicked(self) -> None:
        scope = self._scope_from_module("smooth")
        self._op_apply_smooth_rebin(
            marked=(scope == "marked"),
            all_groups=(scope == "all"),
        )

    def _on_apply_deglitch_clicked(self) -> None:
        scope = self._scope_from_module("deglitch")
        self._op_apply_deglitch(
            marked=(scope == "marked"),
            all_groups=(scope == "all"),
        )

    def _register_undo_btn(self, module: str, on_click=None) -> "QPushButton":
        """Create a disabled Undo button for *module*; enabled after a push.

        v3.19.6: every Undo is an icon button (↶, tooltip says what it
        undoes) placed at the right end of its module's action row.
        """
        btn = QPushButton()
        btn.setObjectName("ghostBtn")
        btn.setIcon(_undo_icon(getattr(self, "_theme_dark", False)))
        btn.setIconSize(QSize(16, 16))
        btn.setFixedWidth(30)
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn.setAccessibleName(self.i18n.t("undo"))
        btn.setToolTip(self.i18n.t("undo_tip"))
        btn.setEnabled(False)
        if on_click is not None:
            btn.clicked.connect(on_click)
        else:
            btn.clicked.connect(lambda *_a, m=module: self._undo_module(m))
        self._undo_buttons[module] = btn
        return btn

    def _snapshot_group_params(self, g: "GroupRecord") -> dict:
        return {
            "settings": deepcopy(g.settings),
            "element": g.element,
            "edge": g.edge,
            "energy_shift": float(g.energy_shift),
            "importance": float(g.importance),
            "mu": np.array(g.mu, copy=True) if g.mu is not None else None,
            "energy": np.array(g.energy, copy=True) if g.energy is not None else None,
            "name": g.name,
            # v3.19.5: check state + note are part of the pre-action state,
            # so the import batch pipeline can be undone in ONE step
            "marked": bool(getattr(g, "marked", False)),
            "note": str(getattr(g, "note", "") or ""),
        }

    def _push_module_undo(
        self,
        module: str,
        groups,
        *,
        added_ids: Optional[List[str]] = None,
        fit_ui: Optional[dict] = None,
    ) -> None:
        """Store pre-action snapshots on this module's undo STACK (multi-step,
        Office-style — each undo pops one level back towards the initial state)."""
        snaps = {}
        for g in groups or []:
            if g is None or getattr(g, "frozen", False):
                continue
            snaps[g.id] = self._snapshot_group_params(g)
        added = list(added_ids or [])
        if not (snaps or added or fit_ui):
            return
        stack = self._module_undo.setdefault(module, [])
        stack.append({"groups": snaps, "added_ids": added, "fit_ui": fit_ui})
        if len(stack) > 100:  # bound memory (snapshots hold mu/energy arrays)
            del stack[:-100]
        btn = self._undo_buttons.get(module)
        if btn is not None:
            btn.setEnabled(True)

    def _undo_module(self, module: str) -> None:
        """Restore the most recent snapshot on *module*'s undo stack (one level
        per click; keeps going until the stack is empty)."""
        stack = (getattr(self, "_module_undo", None) or {}).get(module) or []
        label = {
            "energy": self.i18n.t("box_energy_cal"),
            "norm": self.i18n.t("box_norm"),
            "bkg": self.i18n.t("box_bkg"),
            "ft": self.i18n.t("box_ft"),
            "plot": self.i18n.t("box_plot"),
            "smooth": self.i18n.t("box_tools_smooth"),
            "deglitch": self.i18n.t("box_tools_deglitch"),
            "auto_norm": self.i18n.t("auto_norm"),
            "auto_fit": self.i18n.t("auto_fit"),
            "ai_check": self.i18n.t("ai_check"),
            "batch": self.i18n.t("batch_result_title"),
        }.get(module, module)
        if not stack:
            self._log(self.i18n.t("undo_none", name=label))
            return
        entry = stack[-1]

        # Smooth/Rebin: undo = delete newly created curves only (sources unchanged)
        added = set(entry.get("added_ids") or [])
        if module == "smooth":
            n_del = 0
            if added:
                before = len(self.project.groups)
                self.project.groups = [g for g in self.project.groups if g.id not in added]
                n_del = before - len(self.project.groups)
                if self.project.current_id in added:
                    self.project.current_id = (
                        self.project.groups[0].id if self.project.groups else None
                    )
            stack.pop()
            if not stack:
                self._module_undo.pop(module, None)
            btn = self._undo_buttons.get(module)
            if btn is not None:
                btn.setEnabled(bool(stack))
            cur = self.project.current
            if cur is not None:
                self._load_group_to_controls(cur)
                self._update_meta()
            self._refresh_list()
            self._replot()
            self._log(self.i18n.t("undo_smooth_done", n=n_del))
            return

        # Other modules: restore group parameter snapshots (+ optional added cleanup)
        if added:
            self.project.groups = [g for g in self.project.groups if g.id not in added]
            if self.project.current_id in added:
                self.project.current_id = (
                    self.project.groups[0].id if self.project.groups else None
                )
        by_id = {g.id: g for g in self.project.groups}
        n = 0
        for gid, snap in (entry.get("groups") or {}).items():
            g = by_id.get(gid)
            if g is None:
                continue
            g.settings = deepcopy(snap.get("settings") or AthenaSettings())
            g.element = str(snap.get("element") or "")
            g.edge = str(snap.get("edge") or "K")
            g.energy_shift = float(snap.get("energy_shift", 0.0))
            g.importance = float(snap.get("importance", 1.0))
            if snap.get("mu") is not None:
                g.mu = np.array(snap["mu"], copy=True)
            if snap.get("energy") is not None:
                g.energy = np.array(snap["energy"], copy=True)
            if snap.get("name"):
                g.name = str(snap["name"])
            if "marked" in snap:
                g.marked = bool(snap.get("marked"))
            if "note" in snap:
                g.note = str(snap.get("note") or "")
            try:
                g.process()
            except Exception as exc:
                self._log(f"undo process [{g.name}]: {exc}")
            n += 1
        if entry.get("fit_ui"):
            self._restore_fit_ui_snapshot(entry["fit_ui"])
            self.last_fit = None
        stack.pop()
        if not stack:
            self._module_undo.pop(module, None)
        btn = self._undo_buttons.get(module)
        if btn is not None:
            btn.setEnabled(bool(stack))
        cur = self.project.current
        if cur is not None:
            self._load_group_to_controls(cur)
            self._update_meta()
        self._refresh_list()
        self._replot()
        self._log(self.i18n.t("undo_done", name=label, n=n))

    def _section_apply(self, section: str) -> None:
        """Apply panel parameters using the module's Current/Marked/All radio."""
        titles = {
            "norm": self.i18n.t("box_norm"),
            "bkg": self.i18n.t("box_bkg"),
            "ft": self.i18n.t("box_ft"),
            "plot": self.i18n.t("box_plot"),
        }
        label = titles.get(section, section)
        scope = self._scope_from_module(section)
        targets = self._targets_for_scope(scope)
        if not targets:
            return
        if scope == "all":
            self._push_module_undo(section, targets)
            self._copy_section_settings_to_groups(section, targets)
            self._log(self.i18n.t("op_ok_section_all", name=label))
            return
        if scope == "marked":
            self._push_module_undo(section, targets)
            self._copy_section_settings_to_groups(section, targets)
            self._log(self.i18n.t("op_ok_section_marked", name=label))
            return
        # current
        g = targets[0]
        self._push_module_undo(section, [g])
        # ensure current is the target
        if self.project.current_id != g.id:
            self.project.set_current(g.id)
            self._load_group_to_controls(g)
        code = 0 if self._copy_section_settings_to_groups(section, [g]) else 1
        if code == 0:
            self._log(self.i18n.t("op_ok_section", name=label))
        else:
            # apply failed — drop only the snapshot pushed above, older
            # undo levels on this module stay intact
            stack = (getattr(self, "_module_undo", None) or {}).get(section) or []
            if stack:
                stack.pop()
                if not stack:
                    self._module_undo.pop(section, None)
            btn = self._undo_buttons.get(section)
            if btn is not None:
                btn.setEnabled(bool(stack))

    def _clear_transient_selection(self):
        """Blue is reserved for marked rows — never let a Qt selection persist
        (e.g. clicking the already-current row selects it without firing
        currentItemChanged)."""
        if self._block:
            return
        self._block = True
        self.list_groups.clearSelection()
        self._block = False

    def _sync_group_order(self, *_):
        """After a drag-drop reorder, mirror the visual order into project.groups."""
        if self._block:
            return
        order = []
        for i in range(self.list_groups.count()):
            it = self.list_groups.item(i)
            gid = it.data(Qt.ItemDataRole.UserRole) if it else None
            if gid:
                order.append(gid)
        by_id = {g.id: g for g in self.project.groups}
        new = [by_id[i] for i in order if i in by_id]
        if not new:
            return
        if len(new) == len(self.project.groups):
            self.project.groups = new
        else:
            # Filtered list: reorder only the visible subset in place.
            wanted = {g.id for g in new}
            it = iter(new)
            self.project.groups = [
                next(it) if g.id in wanted else g for g in self.project.groups
            ]
        self._replot()

    def _group_item_clicked(self, item: QListWidgetItem):
        """Click on the mark-icon zone toggles the group's mark; clicks anywhere
        else just select the row (handled via currentItemChanged)."""
        if self._block:
            return
        if not self._mark_list_icon_zone_hit(self.list_groups, item):
            return
        gid = item.data(Qt.ItemDataRole.UserRole)
        for g in self.project.groups:
            if g.id == gid:
                g.marked = not g.marked
                self._paint_group_item(item, g)
                break
        # Replot after this click returns so the checkmark can paint first.
        # Inline _replot of Marked overlay used to stall 1–2 s (v1.15.22).
        QTimer.singleShot(0, self._replot)

    def _group_selected(self, cur: QListWidgetItem, _prev):
        if self._block or cur is None:
            return
        gid = cur.data(Qt.ItemDataRole.UserRole)
        g = self.project.set_current(gid)
        if g:
            self.last_fit = None  # fit overlay belongs to the previous group's data
            self._repaint_group_list_items()
            self.list_groups.clearSelection()
            self._load_group_to_controls(g)
            self._update_meta()
            # Auto-edge + auto-reprocess + replot stay off this click's stack
            # so itemClicked can paint the mark first (v1.15.22). Settings
            # auto_reprocess / auto_edge_on_select default on and used to
            # stall 1–2 s before the checkmark appeared.
            self._select_seq += 1
            seq = self._select_seq
            # Rapid switching (held arrow key / fast clicks) coalesces: only
            # the row the user stops on gets processed + redrawn (v2.18.0).
            # "Rapid" = within 0.3 s of the previous selection OR of the last
            # finished canvas draw — a held key queues its repeats while a
            # redraw blocks, so they arrive right after it.  A lone click
            # still runs on the next event-loop turn.
            now = time.monotonic()
            last = max(
                getattr(self, "_last_select_at", 0.0),
                getattr(self, "_last_draw_done_at", 0.0),
            )
            rapid = (now - last) < 0.3
            self._last_select_at = now
            QTimer.singleShot(
                150 if rapid else 0, lambda: self._after_group_selected(gid, seq)
            )

    def _after_group_selected(self, gid: str, seq: int) -> None:
        if seq != self._select_seq or self._block:
            return
        g = self._find_group_by_id(gid)
        if g is None or self.project.current_id != gid:
            return
        if self.chk_auto_edge_on_select.isChecked() and not g.frozen:
            self._auto_edge_for_group(g, reprocess=False, freeze_e0=False)
            self._load_group_to_controls(g)
            self._update_meta()
        if self.chk_auto.isChecked():
            self._apply_current(preserve_view=False)
        else:
            self._replot(preserve_view=False)

    def _update_meta(self):
        g = self.project.current
        if not g:
            self._dir_full = ""
            self.lbl_dir.setText("")
            self.lbl_dir.setToolTip("")
            return
        # current group's folder goes to the toolbar (right of project title);
        # keep the full path and elide on the left to the real pixel width
        self._dir_full = str(Path(g.path).parent) if g.path else ""
        self.lbl_dir.setToolTip(self._dir_full)
        self._elide_dir_label()

    def _elide_dir_label(self) -> None:
        """Left-elide the toolbar folder path to the label's real pixel width."""
        fm = self.lbl_dir.fontMetrics()
        width = max(24, self.lbl_dir.width() - 6)
        self.lbl_dir.setText(fm.elidedText(self._dir_full, Qt.TextElideMode.ElideLeft, width))

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is getattr(self, "lbl_dir", None) and event.type() == QEvent.Type.Resize:
            self._elide_dir_label()
        return super().eventFilter(obj, event)

    def _auto_edge_current(self):
        g = self.project.current
        if not g:
            self._log(self.i18n.t("no_current_group"))
            return
        self._auto_edge_for_group(g, reprocess=True)
        self._load_group_to_controls(g)
        self._update_meta()
        self._refresh_visible_rows()

    def _auto_detect_loaded_group_elements(self, groups: List[GroupRecord]) -> int:
        """Record absorber element/edge for every newly imported curve.

        Detection is deliberately separated from processing: importing a large
        Athena project should populate the element filter at once, without
        running the whole normalisation/EXAFS pipeline for every curve.
        The progress dialog is pumped per curve so the bar never freezes.
        """
        found = 0
        tagged: List[Tuple[GroupRecord, float]] = []
        n = len(groups)
        dlg = getattr(self, "_load_progress", None)
        base = 0
        if dlg is not None and n and not bool(dlg.property("sliding")):
            base = dlg.value()
            dlg.setMaximum(max(dlg.maximum(), base + n))
        for i, g in enumerate(groups):
            if dlg is not None:
                if not self._progress_update(
                    base + i,
                    self._bi(
                        f"识别元素/边 {i + 1}/{n}：{g.name}",
                        f"Detecting element/edge {i + 1}/{n}: {g.name}",
                    ),
                ):
                    break  # user cancelled
            if g.energy is None or g.mu is None:
                continue
            if str(g.element or "").strip():
                continue
            try:
                res = detect_edge(
                    np.asarray(g.energy, dtype=float) + float(g.energy_shift),
                    np.asarray(g.mu, dtype=float),
                )
            except Exception as exc:
                self._log(f"Auto edge failed [{g.name}]: {exc}")
                continue
            if not res.ok or not isinstance(res.data, dict):
                continue
            data = res.data
            element = str(data.get("element") or "").strip()
            edge = str(data.get("edge") or "").strip()
            if not element:
                continue
            g.element = element
            if edge:
                g.edge = edge
            # Deliberately do NOT write settings.e0 here (v0.34.11): import-time
            # detection is passive. Leaving e0=None keeps Auto E0 checked so
            # each process re-finds the edge; the value still shows in the spin
            # after processing. The user freezes a value by unchecking Auto or
            # typing one — never by our batch detection.
            found += 1
            e0v = data.get("e0")
            if isinstance(e0v, (int, float)) and np.isfinite(e0v):
                tagged.append((g, float(e0v)))
        self._batch_edge_consensus(tagged)
        if found:
            self._log(
                self._bi(
                    f"{found}/{len(groups)} 条曲线已自动识别元素/边",
                    f"Auto-detected element/edge for {found}/{len(groups)} curves",
                )
            )
        return found

    def _batch_edge_consensus(self, tagged: List[Tuple[GroupRecord, float]]) -> int:
        """Align odd-one-out tags with the batch majority (v2.18.0).

        One import batch is usually one element/edge.  When ≥ 3 curves were
        just tagged and ≥ 60 % agree, a curve tagged differently is moved to
        the majority edge — but only if its own e0 is physically plausible
        for that edge (inside the chemical-shift window of
        ``edge_guess_cost``).  A genuinely different element (Co K next to
        Fe K) is far outside that window and keeps its own tag.
        """
        if len(tagged) < 3:
            return 0
        from collections import Counter

        counts = Counter((g.element, g.edge) for g, _ in tagged)
        (maj_el, maj_edge), n_maj = counts.most_common(1)[0]
        if n_maj < 0.6 * len(tagged):
            return 0
        moved = 0
        for g, e0 in tagged:
            if (g.element, g.edge) == (maj_el, maj_edge):
                continue
            if edge_guess_cost(e0, maj_el, maj_edge) is None:
                continue
            self._log(self._bi(
                f"同批多数参照：{g.name} {g.element} {g.edge} → {maj_el} {maj_edge}（E0≈{e0:.1f}）",
                f"Batch consensus: {g.name} {g.element} {g.edge} → {maj_el} {maj_edge} (E0≈{e0:.1f})",
            ))
            g.element, g.edge = maj_el, maj_edge
            moved += 1
        return moved

    def _auto_edge_for_group(self, g: GroupRecord, reprocess: bool = True, freeze_e0: bool = True) -> bool:
        """Detect E0 + element + edge from this group's μ(E); optionally reprocess & autoscale plot.

        freeze_e0 pins the detected value into settings (auto checkbox off).
        Selection-time detection passes freeze_e0=False (v0.34.11): passively
        switching groups must not kill the user's Auto-E0 choice — only the
        explicit 自动寻边 button pins the result.
        freeze_e0=False also must not overwrite an existing element/edge
        (v1.15.19): guess_edge is often a neighbour, and rewriting the
        list-filter key hid the clicked row.
        """
        if g.energy is None or g.mu is None:
            return False
        e = np.asarray(g.energy, dtype=float) + float(g.energy_shift)
        mu = np.asarray(g.mu, dtype=float)
        res = detect_edge(e, mu)
        if not res.ok:
            self._log(res.message)
            return False
        d = res.data
        if freeze_e0:
            # Explicit 自动寻边: retag + pin e0.
            if d.get("element"):
                g.element = str(d["element"])
            if d.get("edge"):
                g.edge = str(d["edge"])
            g.settings.e0 = float(d["e0"])
        else:
            # Select-time: fill empty tags only; never rewrite the filter key.
            if not str(g.element or "").strip() and d.get("element"):
                g.element = str(d["element"])
            if not str(g.edge or "").strip() and d.get("edge"):
                g.edge = str(d["edge"])
        # Autoscale energy plot window to this spectrum (rel E0)
        if getattr(self, "chk_autoscale_plot", None) is None or self.chk_autoscale_plot.isChecked():
            emin = float(d.get("emin_rel", -200))
            emax = float(d.get("emax_rel", 800))
            # pad slightly, clamp to spin ranges
            emin = max(-2000.0, min(-20.0, emin - 10))
            emax = min(5000.0, max(50.0, emax + 10))
            self._block = True
            self.sp_emin.setValue(emin)
            self.sp_emax.setValue(emax)
            self._block = False
        self._log(res.message)
        if reprocess:
            r = g.process()
            if not r.ok:
                self._log(f"process after auto-edge: {r.message[:200]}")
        return True

    def _commit_controls_to_previous_group(self, new_g: GroupRecord) -> None:
        """Write the panel's un-Applied state back to the group it belongs to.

        Switching groups reloads the panel from the new group's settings; without
        this, edits the user typed but never Applied (or that never auto-fired)
        would vanish. Settings fields only — no reprocess, no signals. Frozen
        groups are left untouched.
        """
        if getattr(self, "_panel_writeback_suspended", False):
            # Import batch pipeline running (v3.19.5 acceptance fix): the
            # panel still shows the pre-batch values of the last-loaded file.
            # Writing them back on a group switch (merge / auto norm reload)
            # zeroed that file's fresh ΔE calibration.
            return
        prev_id = getattr(self, "_controls_group_id", None)
        if not prev_id or prev_id == new_g.id:
            return
        prev = self._find_group_by_id(prev_id)
        if prev is None or prev.frozen:
            return
        prev.settings = self._settings_from_controls()
        prev.energy_shift = float(self.sp_eshift.value())
        prev.importance = float(self.sp_importance.value())
        el = self.cmb_element.currentText().strip()
        if el:
            prev.element = el
        edge = self.cmb_edge.currentText().strip()
        if edge:
            prev.edge = edge

    def _load_group_to_controls(self, g: GroupRecord):
        self._commit_controls_to_previous_group(g)
        self._block = True
        s = g.settings
        # Element / edge — short symbols
        el = (g.element or "").split(":")[0].strip()
        # strip "26 Iron" style
        if " " in el and len(el) > 2:
            # take last token if looks like "26 Iron"
            parts = el.replace(":", " ").split()
            el = parts[-1] if parts[-1].isalpha() else parts[0]
        self.cmb_element.setEditText(el[:4] if el else "")
        idx = self.cmb_edge.findText(g.edge or "K")
        if idx >= 0:
            self.cmb_edge.setCurrentIndex(idx)
        elif g.edge:
            for i in range(self.cmb_edge.count()):
                if g.edge in self.cmb_edge.itemText(i):
                    self.cmb_edge.setCurrentIndex(i)
                    break
        self._prefill_std_e()  # theory edge for empty Std E0 (never overwrites)

        if s.e0 is None:
            self.chk_e0_auto.setChecked(True)
        else:
            # explicit E0 (manual or auto-detected) → show it with auto off
            self.chk_e0_auto.setChecked(False)
            self.sp_e0.setValue(float(s.e0))
        self.sp_pre1.setValue(s.pre1)
        self.sp_pre2.setValue(s.pre2)
        self.sp_norm1.setValue(s.norm1)
        self.sp_norm2.setValue(s.norm2)
        self.sp_nnorm.setValue(s.nnorm if s.nnorm is not None else 2)
        self.chk_flatten.setChecked(s.make_flat)
        self.sp_rbkg.setValue(s.rbkg)
        self.sp_bkg_kw.setValue(s.bkg_kweight)
        self.sp_kmin_bkg.setValue(float(getattr(s, "bkg_kmin", 0.0)))
        self.sp_kmax_bkg.setValue(float(getattr(s, "bkg_kmax", 15.0)))
        self.cmb_clamp_lo.setCurrentIndex(_clamp_index(getattr(s, "bkg_clamp_lo", 0)))
        self.cmb_clamp_hi.setCurrentIndex(_clamp_index(getattr(s, "bkg_clamp_hi", 1)))
        self.sp_kmin.setValue(s.kmin)
        self.sp_kmax.setValue(s.kmax)
        self.sp_dk.setValue(s.dk)
        self.sp_rmin.setValue(s.rmin)
        self.sp_rmax.setValue(s.rmax)
        self.sp_dr.setValue(getattr(s, "dr", 0.0))
        self._set_win_combo(self.cmb_kwin, s.kwindow)
        self._set_win_combo(self.cmb_rwin, s.rwindow)
        self.sp_kw_plot.setValue(s.kweight_plot)
        if hasattr(self, "chk_phase"):
            self.chk_phase.setChecked(bool(getattr(s, "with_phase", False)))
        self.sp_eshift.setValue(g.energy_shift)
        self.sp_importance.setValue(g.importance)
        self.chk_freeze.setChecked(g.frozen)
        # E0 from last process (display only) — auto mode only.  Never let a
        # stale session value override an explicitly pinned settings.e0
        # (v0.35.12: that put the auto-detected point back after calibrate).
        if s.e0 is None and g.session and g.session.arrays:
            if np.isfinite(g.session.arrays.e0):
                self.sp_e0.setValue(float(g.session.arrays.e0))
        self._controls_group_id = g.id  # panel now shows this group (switch write-back)
        # Victoreen exponent has no panel control: carry the group's value so a
        # switch/Apply write-back keeps what Auto norm chose (v2.19.5).
        self._panel_nvict = int(getattr(s, "nvict", 0) or 0)
        self._block = False

    @staticmethod
    def _set_win_combo(cmb: QComboBox, stored) -> None:
        """Select combo item matching a stored window name ('kaiser' ↔ 'Kaiser-Bessel')."""
        if not stored:
            return
        norm = str(stored).strip().lower()
        for i in range(cmb.count()):
            if cmb.itemText(i).lower().replace("kaiser-bessel", "kaiser") == norm:
                cmb.setCurrentIndex(i)
                return

    def _settings_from_controls(self) -> AthenaSettings:
        s = AthenaSettings()
        # auto E0: pass None so pre_edge finds it; still show value in spin
        if self.chk_e0_auto.isChecked():
            s.e0 = None
        else:
            s.e0 = float(self.sp_e0.value())
        s.pre1 = self.sp_pre1.value()
        s.pre2 = self.sp_pre2.value()
        s.norm1 = self.sp_norm1.value()
        s.norm2 = self.sp_norm2.value()
        s.nnorm = self.sp_nnorm.value()
        s.nvict = int(getattr(self, "_panel_nvict", 0) or 0)
        s.make_flat = self.chk_flatten.isChecked()
        s.rbkg = self.sp_rbkg.value()
        s.bkg_kweight = self.sp_bkg_kw.value()
        s.bkg_kmin = self.sp_kmin_bkg.value()
        s.bkg_kmax = self.sp_kmax_bkg.value()
        i_lo = self.cmb_clamp_lo.currentIndex()
        i_hi = self.cmb_clamp_hi.currentIndex()
        s.bkg_clamp_lo = ATHENA_CLAMP_VALUES[i_lo] if 0 <= i_lo < len(ATHENA_CLAMP_VALUES) else 0
        s.bkg_clamp_hi = ATHENA_CLAMP_VALUES[i_hi] if 0 <= i_hi < len(ATHENA_CLAMP_VALUES) else 1
        s.kmin = self.sp_kmin.value()
        s.kmax = self.sp_kmax.value()
        s.dk = self.sp_dk.value()
        s.kwindow = self.cmb_kwin.currentText().lower().replace("kaiser-bessel", "kaiser")
        s.rmin = self.sp_rmin.value()
        s.rmax = self.sp_rmax.value()
        s.dr = self.sp_dr.value()
        s.rwindow = self.cmb_rwin.currentText().lower().replace("kaiser-bessel", "kaiser")
        s.kweight_plot = self.sp_kw_plot.value()
        if hasattr(self, "chk_phase"):
            s.with_phase = bool(self.chk_phase.isChecked())
            if s.with_phase:
                s.phase_feff = self._resolve_phase_feff()
            else:
                s.phase_feff = ""
        return s

    def _resolve_phase_feff(self) -> str:
        extra: List[str] = []
        if hasattr(self, "tbl_paths"):
            try:
                extra = [
                    p.feff_file
                    for p in self._paths_from_table()
                    if p.use and p.feff_file
                ]
            except Exception:
                extra = []
        absorber = ""
        if hasattr(self, "cmb_element"):
            absorber = str(self.cmb_element.currentText() or "").strip()
        found = resolve_phase_feff_file(
            absorber=absorber,
            extra=extra,
        )
        return str(found) if found else ""

    def _on_freeze_toggled(self, checked: bool = False) -> None:
        """Freeze checkbox locks the current group immediately (no reprocess until unlocked)."""
        if self._block:
            return
        g = self.project.current
        if not g:
            return
        g.frozen = bool(checked)
        self._refresh_visible_rows()
        if checked:
            self._log(self._bi(f"已冻结组：{g.name}", f"Frozen group: {g.name}"))
        else:
            self._log(self._bi(f"已解冻组：{g.name}", f"Unfrozen group: {g.name}"))

    def _apply_current(self, quiet: bool = False, preserve_view: bool = True) -> int:
        """Apply current controls to the selected group. Returns 0 on success.

        *quiet*: section Apply buttons log a short 「已成功」 themselves.
        Frozen groups refuse reprocess (toggle Freeze off first).
        *preserve_view*: keep the user's zoom/pan window (default); switching
        the current group or the element filter passes False so the freshly
        selected spectrum is auto-framed (v0.35.8).
        """
        g = self.project.current
        if not g:
            self._log(self.i18n.t("no_current_group"))
            return 1
        # Keep group.frozen in sync with the checkbox (toggle may have set it already)
        g.frozen = bool(self.chk_freeze.isChecked())
        if g.frozen:
            self._log(self._bi("组已冻结 — 取消冻结后再 Apply", "Group is frozen — uncheck Freeze, then Apply"))
            return 1
        g.settings = self._settings_from_controls()
        g.energy_shift = self.sp_eshift.value()
        g.importance = self.sp_importance.value()
        g.element = self.cmb_element.currentText()
        g.edge = self.cmb_edge.currentText()
        res = g.process()
        if not res.ok:
            self._log(f"Process error: {res.message[:300]}")
            return 1
        if res.info:
            e0v = res.info.get("e0")
            if self.chk_e0_auto.isChecked() and isinstance(e0v, (int, float)) and np.isfinite(e0v):
                self._block = True
                self.sp_e0.setValue(float(e0v))
                self._block = False
        if not quiet:
            e0v = res.info.get("e0") if res.info else None
            e0s = f"{e0v:.3f}" if isinstance(e0v, (int, float)) and np.isfinite(e0v) else "?"
            self._log(f"Processed {g.name}: E0={e0s}  n_k={(res.info or {}).get('n_k')}")
        if g.settings.with_phase:
            if (res.info or {}).get("phase_corrected"):
                src = Path(str((res.info or {}).get("phase_feff") or "")).name
                self._log(self.i18n.t("phase_applied").format(path=src or "?"))
            else:
                note = str((res.info or {}).get("phase_note") or "")
                self._log(self.i18n.t("phase_need_feff") + (f" ({note})" if note else ""))
        self.last_fit = None  # processed data changed — stale fit overlay must go
        self._replot(preserve_view=preserve_view)
        return 0

    def _mark_all(self, marked: bool):
        for g in self._visible_groups_for_filter():
            if not g.frozen:
                g.marked = bool(marked)
        self._refresh_visible_rows()
        self._replot()

    def _invert_marks(self):
        for g in self._visible_groups_for_filter():
            if not g.frozen:
                g.marked = not g.marked
        self._refresh_visible_rows()
        self._replot()

    def _show_group_context_menu(self, pos) -> None:
        """Offer destructive/rename actions at the list item, not below the list."""
        item = self.list_groups.itemAt(pos)
        if item is not None:
            self.list_groups.setCurrentItem(item)
        self.act_remove_current.setEnabled(self.project.current is not None)
        self.act_close_file.setEnabled(self.project.current is not None)
        self.act_remove_marked.setEnabled(bool(self._marked_groups_for_scope()))
        self.act_remove_all.setEnabled(bool(self.project.groups))
        self.act_rename_current.setEnabled(self.project.current is not None)
        self.group_context_menu.popup(self.list_groups.mapToGlobal(pos))

    def _remove_current(self):
        g = self.project.current
        if g is None:
            return
        self.project.groups = [x for x in self.project.groups if x.id != g.id]
        if self.project.current_id == g.id:
            self.project.current_id = self.project.groups[0].id if self.project.groups else None
        self.last_fit = None
        self._refresh_list()
        cur = self.project.current
        if cur is not None:
            self._load_group_to_controls(cur)
        self._update_meta()
        self._replot()
        self._log(self._bi(f"已删除当前组：{g.name}", f"Removed current group: {g.name}"))

    def _close_current_file(self):
        """Remove every group that came from the same file as the current group."""
        g = self.project.current
        if g is None:
            return
        path = g.path
        if path:
            before = len(self.project.groups)
            self.project.groups = [x for x in self.project.groups if x.path != path]
            n = before - len(self.project.groups)
        else:
            self.project.groups = [x for x in self.project.groups if x.id != g.id]
            n = 1
        if self.project.current is None:
            self.project.current_id = self.project.groups[0].id if self.project.groups else None
        self.last_fit = None
        self._refresh_list()
        cur = self.project.current
        if cur is not None:
            self._load_group_to_controls(cur)
        self._update_meta()
        self._replot()
        self._log(
            self._bi(
                f"已关闭文件 {Path(path).name if path else g.name}（{n} 个组）",
                f"Closed file {Path(path).name if path else g.name} ({n} groups)",
            )
        )

    def _remove_all_groups(self):
        n = len(self.project.groups)
        if not n:
            return
        self.project.groups = []
        self.project.current_id = None
        self.last_fit = None
        self._refresh_list()
        self._update_meta()
        self._replot(preserve_view=False)
        self._log(self._bi(f"已删除全部 {n} 个组", f"Removed all {n} groups"))

    def _remove_marked(self):
        doomed = {g.id for g in self._marked_groups_for_scope()}
        if not doomed:
            return
        self.project.groups = [g for g in self.project.groups if g.id not in doomed]
        if self.project.current is None:
            self.project.current_id = self.project.groups[0].id if self.project.groups else None
        n = len(doomed)
        self._refresh_list()
        cur = self.project.current
        if cur is not None:
            self._load_group_to_controls(cur)
        self._update_meta()
        self._replot()
        self._log(self._bi(f"已删除 {n} 个组", f"Removed {n} groups"))

    def _rename_current(self):
        g = self.project.current
        if not g:
            return
        name, ok = QInputDialog.getText(self, self.i18n.t("rename"), self.i18n.t("rename_prompt"), text=g.name)
        if ok and name.strip():
            g.name = name.strip()
            self._refresh_visible_rows()

    def _copy_section_settings_to_groups(self, section: str, groups) -> int:
        """Apply only one panel's fields, preserving all unrelated settings."""
        panel = self._settings_from_controls()
        fields = {
            "norm": ("pre1", "pre2", "norm1", "norm2", "nnorm", "nvict", "make_flat"),
            "bkg": (
                "rbkg", "bkg_kweight", "bkg_kmin", "bkg_kmax",
                "bkg_clamp_lo", "bkg_clamp_hi",
            ),
            "ft": (
                "kmin", "kmax", "dk", "kwindow", "rmin", "rmax", "dr",
                "rwindow", "kweight_plot", "with_phase", "phase_feff",
            ),
        }.get(section, ())
        n_ok = 0
        for group in groups:
            if group.frozen:
                continue
            updated = deepcopy(group.settings)
            for field_name in fields:
                setattr(updated, field_name, deepcopy(getattr(panel, field_name)))
            group.settings = updated
            result = group.process()
            if result.ok:
                n_ok += 1
            else:
                self._log(f"Process error [{group.name}]: {result.message[:300]}")
        self.last_fit = None
        current = self.project.current
        if current is not None:
            self._load_group_to_controls(current)
        self._replot()
        return n_ok

    def _copy_settings_to_groups(self, groups, quiet: bool = False) -> int:
        """Copy panel settings onto each unfrozen group in *groups* and reprocess."""
        s = self._settings_from_controls()
        n = 0
        for g in groups:
            if g.frozen:
                continue
            g.settings = deepcopy(s)
            g.energy_shift = self.sp_eshift.value()
            r = g.process()
            n += 1 if r.ok else 0
        if not quiet:
            self._log(
                self._bi(
                    f"已把参数应用到 {n} 个组",
                    f"Applied settings to {n} groups",
                )
            )
        self.last_fit = None  # reprocessed data — drop stale fit overlay
        self._replot()
        return n

    def _suggest_norm_settings(
        self, energy: np.ndarray, mu: np.ndarray, base: Optional[AthenaSettings] = None
    ) -> Tuple[Optional[AthenaSettings], dict]:
        """
        Auto edge + per-curve pre/norm windows from μ(E) shape (not a copied
        Athena default). Keeps background/FT knobs from *base* when provided.
        Caller (Auto norm) then overwrites bkg/FT **k** windows; R/Rbkg stay.
        """
        e = np.asarray(energy, dtype=float)
        m = np.asarray(mu, dtype=float)
        pinned = None
        if base is not None and getattr(base, "e0", None) is not None:
            try:
                pe = float(base.e0)
                if np.isfinite(pe) and pe > 0:
                    pinned = pe
            except (TypeError, ValueError):
                pinned = None
        res = detect_edge(e, m)
        d: dict = {}
        if res.ok and res.data:
            d = dict(res.data)
        if pinned is not None:
            # Calibrated / typed E0 (Auto E0 off). Auto-norm still picks
            # pre/post/k windows; it must not move the foil E0 (v1.15.28).
            e0 = pinned
            d["e0"] = e0
        else:
            if not d:
                return None, {}
            e0 = float(d["e0"])
        win = suggest_pre_norm_windows(e, m, e0)
        d["pre_norm_reason"] = win.get("reason")

        s = deepcopy(base) if base is not None else AthenaSettings()
        s.e0 = e0
        s.pre1 = float(win["pre1"])
        s.pre2 = float(win["pre2"])
        s.norm1 = float(win["norm1"])
        s.norm2 = float(win["norm2"])
        s.nnorm = int(win["nnorm"])
        s.nvict = int(win.get("nvict", 0))
        s.make_flat = True
        return s, d

    def _targets_for_scope(self, scope: str) -> List["GroupRecord"]:
        """Resolve Current / Marked / All (unfrozen only).

        Marked and All obey the left element filter (v0.34.19): Fe filter
        + Marked must not rewrite hidden Ni rows that still carry a mark.
        Filter All leaves the previous whole-project behaviour.
        """
        scope = (scope or "current").strip().lower()
        if scope in ("all", "all_groups"):
            gs = [g for g in self._groups_matching_element_filter() if not g.frozen]
            if not gs:
                self._log(self.i18n.t("no_current_group"))
            return gs
        if scope in ("marked", "mark"):
            gs = [g for g in self._marked_groups_for_scope() if not g.frozen]
            if not gs:
                self._log(self.i18n.t("mark_groups_first"))
            return gs
        g = self.project.current
        if not g:
            self._log(self.i18n.t("no_current_group"))
            return []
        if g.frozen:
            self._log(self._bi("当前组已冻结", "Current group is frozen"))
            return []
        return [g]

    @staticmethod
    def _format_noise_ratio_pct(ratio) -> str:
        """Safe residual text for auto-norm logs; ratio may be None after rebin."""
        if ratio is None:
            return "n/a"
        try:
            return f"{float(ratio) * 100:.1f}%"
        except (TypeError, ValueError):
            return "n/a"

    def _preedge_noise_ratio(self, e, mu, g):
        """Pre-edge baseline noise as a fraction of the edge step.

        Returns (ratio, edge_step) or (None, None) when there are too few
        points. ratio = std(linearly detrended pre-edge μ) / edge_step;
        >~2% means the baseline is too noisy for a clean normalization.
        """
        try:
            e = np.asarray(e, dtype=float)
            mu = np.asarray(mu, dtype=float)
            e0 = getattr(g.settings, "e0", None)
            if not (isinstance(e0, (int, float)) and np.isfinite(e0) and e0 > 0):
                de = np.gradient(mu, e)
                e0 = float(e[int(np.argmax(np.abs(de)))])
            pre = (e >= e0 - 150.0) & (e <= e0 - 20.0)
            post = (e >= e0 + 30.0) & (e <= e0 + 150.0)
            if pre.sum() < 8 or post.sum() < 5:
                return None, None
            coef = np.polyfit(e[pre], mu[pre], 1)
            noise = float(np.std(mu[pre] - np.polyval(coef, e[pre])))
            step = float(abs(np.median(mu[post]) - np.median(mu[pre])))
            if step <= 0:
                return None, None
            return noise / step, step
        except Exception:
            return None, None

    def _auto_normalize(self, scope: str = "current", *, targets=None) -> None:
        """
        Batch auto-normalization for Current / Marked / All.
        Snapshots previous state for Undo (all groups touched in this run).

        *targets* overrides the scope lookup (the import batch pipeline already
        resolved its own working set).  When a sliding dialog is already up
        (import path) this must not open or close its own — same pattern as
        _merge_by_plan, so the whole batch is ONE progress dialog.
        """
        if targets is None:
            targets = self._targets_for_scope(scope)
        targets = [g for g in (targets or []) if g is not None]
        if not targets:
            return

        # One undo step = this run’s targets (pre-auto params)
        self._push_module_undo("auto_norm", targets)

        n_total = len(targets)
        own_progress = getattr(self, "_load_progress", None) is None
        if own_progress:
            self._progress_begin(
                self.i18n.t("progress_auto_norm_working", n=n_total),
                maximum=n_total,
                window_title=self.i18n.t("progress_auto_norm_title"),
            )
        n_ok = 0
        cancelled = False
        try:
            for i, g in enumerate(targets):
                if not self._progress_update(
                    i,
                    self.i18n.t(
                        "progress_auto_norm_group", i=i + 1, n=n_total, name=g.name
                    ),
                ):
                    cancelled = True
                    self._log(
                        self.i18n.t(
                            "progress_auto_norm_cancelled", ok=n_ok, n=n_total
                        )
                    )
                    break
                if g.energy is None or g.mu is None:
                    self._log(f"auto-norm skip {g.name}: no μ(E)")
                    continue
                e = np.asarray(g.energy, dtype=float) + float(g.energy_shift)
                mu = np.asarray(g.mu, dtype=float)
                # SNR pre-check (v0.9.x): escalate smooth → smooth+rebin →
                # manual. Never rebin-then-smooth (worse on noisy data).
                # Auto steps are covered by the auto_norm undo snapshot.
                ratio, _step = self._preedge_noise_ratio(e, mu, g)
                if ratio is not None and ratio > 0.02:
                    sm = smooth_mu(e, mu, boxcar=3)
                    if sm.ok:
                        g.mu = np.asarray(sm.data, dtype=float)
                        mu = g.mu
                        ratio, _ = self._preedge_noise_ratio(e, mu, g)
                        # After smooth/rebin the SNR estimate can become None
                        # (too few pre/post points on the new grid) — never
                        # format ratio*100 without a guard (batch Auto norm crash).
                        self._log(
                            self._bi(
                                f"auto-norm: {g.name} 基线噪声偏大，已自动 boxcar(3) 平滑（残余 {self._format_noise_ratio_pct(ratio)}）",
                                f"auto-norm: {g.name} noisy baseline — auto boxcar(3) applied (residual {self._format_noise_ratio_pct(ratio)})",
                            )
                        )
                    if ratio is not None and ratio > 0.04:
                        kw = self._rebin_kwargs_for_group(g)
                        rb = rebin_group(e, mu, **kw)
                        if rb.ok:
                            e2, m2 = rb.data
                            shift = float(g.energy_shift or 0.0)
                            g.energy = np.asarray(e2, dtype=float) - shift
                            g.mu = np.asarray(m2, dtype=float)
                            e, mu = np.asarray(g.energy, dtype=float) + shift, g.mu
                            ratio, _ = self._preedge_noise_ratio(e, mu, g)
                            self._log(
                                self._bi(
                                    f"auto-norm: {g.name} 平滑后仍偏噪，已在平滑基础上 rebin（残余 {self._format_noise_ratio_pct(ratio)}）",
                                    f"auto-norm: {g.name} still noisy after smoothing — rebinned on top (residual {self._format_noise_ratio_pct(ratio)})",
                                )
                            )
                    if ratio is not None and ratio > 0.04:
                        self._log(
                            self._bi(
                                f"⚠ {g.name} 基线波动仍过大——建议先在 Tools 手动做 Smooth/Rebin/Deglitch 再做 normalization",
                                f"⚠ {g.name} baseline still too noisy — please run Smooth/Rebin/Deglitch manually in Tools before normalization",
                            )
                        )
                s, d = self._suggest_norm_settings(e, mu, base=g.settings)
                if s is None:
                    self._log(f"auto-norm failed [{g.name}]: edge not found")
                    continue
                # Element/edge are the list-filter key. The working set was
                # already chosen (filter ∩ Marked/All). guess_edge often lands
                # on a neighbour; overwriting then _refresh_list hid the row.
                # Fill only when empty; never retag a labelled group.
                if not str(g.element or "").strip() and d.get("element"):
                    g.element = str(d["element"])
                if not str(g.edge or "").strip() and d.get("edge"):
                    g.edge = str(d["edge"])
                e0 = float(s.e0) if s.e0 is not None else float("nan")
                bkg = suggest_bkg_k_range(e, e0)
                s.bkg_kmin = float(bkg["bkg_kmin"])
                s.bkg_kmax = float(bkg["bkg_kmax"])
                s.kmin = float(FT_KMIN_DEFAULT)
                s.kmax = min(10.0, max(float(bkg["bkg_kmax"]), 3.0))
                g.settings = s
                r = g.process()
                if r.ok:
                    a = g.session.arrays if g.session is not None else None
                    if a is not None and a.k is not None and a.chi is not None:
                        win = suggest_ft_k_window(a.k, a.chi)
                        s.kmin = float(win["kmin"])
                        s.kmax = min(float(win["kmax"]), float(s.bkg_kmax))
                        if s.kmax <= s.kmin:
                            s.kmax = float(s.bkg_kmax)
                        g.settings = s
                        r2 = g.process()
                        if r2.ok:
                            r = r2
                        else:
                            self._log(
                                f"auto-norm FT window process error [{g.name}]: {r2.message[:200]}"
                            )
                    ok_check, check_text = self._ensure_normalization_ok(g, s, e, mu)
                    n_ok += 1
                    e0s = f"{s.e0:.2f}" if s.e0 is not None else "?"
                    vict = f" nvict={s.nvict}" if s.nvict else ""
                    self._log(
                        f"auto-norm {g.name}: {g.element} {g.edge} E0={e0s}  "
                        f"pre=[{s.pre1:.0f},{s.pre2:.0f}]{vict} norm=[{s.norm1:.0f},{s.norm2:.0f}] "
                        f"k_bkg={s.bkg_kmin:.1f}-{s.bkg_kmax:.1f}  FT k={s.kmin:.1f}-{s.kmax:.1f}  "
                        f"check={check_text}"
                    )
                    if not ok_check:
                        self._log(self._bi(
                            f"⚠ {g.name} 归一化自检未通过（{check_text}）——预边/后边窗口仍按 Auto norm 写入，请人工确认",
                            f"⚠ {g.name} normalization self-check failed ({check_text}) - Auto norm windows kept; please review",
                        ))
                else:
                    self._log(
                        f"auto-norm process error [{g.name}]: {r.message[:200]}"
                    )
                self._progress_update(i + 1)
            if not cancelled:
                self._progress_update(n_total, self.i18n.t("progress_done"))
        finally:
            if own_progress:
                self._progress_end()

        self._apply_uniform_ft_windows(targets)

        cur = self.project.current
        if cur is not None:
            self._load_group_to_controls(cur)
            self._update_meta()
        self._refresh_visible_rows()
        self._replot()
        self._log(self.i18n.t("auto_norm_done", ok=n_ok, n=n_total))

    # ---- shared rules wrapper (v3.19.5): one rule set for every entry ----
    def _ensure_normalization_ok(self, g, settings, e, mu) -> tuple:
        """Self-check the normalization and walk the retry ladder if it fails.

        The thresholds and the ladder come from pipeline_rules (the ONLY rules
        source), so Auto norm, the import batch pipeline and an applied AI
        Check suggestion all accept/reject a curve the same way.
        """
        e0 = settings.e0 if settings.e0 is not None else None
        if e0 is None:
            arrs = getattr(getattr(g, "session", None), "arrays", None)
            e0 = getattr(arrs, "e0", None) if arrs is not None else None
        chk = pipeline_rules.normalization_check(g)
        if chk.get("ok"):
            return True, "ok"
        for cand in pipeline_rules.retry_windows(e, mu, e0):
            settings.pre1 = float(cand["pre1"])
            settings.pre2 = float(cand["pre2"])
            settings.nvict = int(cand.get("nvict", settings.nvict))
            if cand.get("nnorm"):
                settings.nnorm = int(cand["nnorm"])
            g.settings = settings
            res = g.process()
            if not res.ok:
                continue
            chk = pipeline_rules.normalization_check(g)
            self._log(
                f"auto-norm {g.name}: {cand['label']} -> check="
                + ("ok" if chk.get("ok") else str(chk.get("reason_en") or "")[:120])
            )
            if chk.get("ok"):
                return True, str(cand.get("reason_en") or cand["label"])
        return False, str(chk.get("reason_en") or "check failed")

    def _on_auto_norm_clicked(self) -> None:
        """Auto norm button: ask about the common FT window when it matters.

        The question only comes up when this run holds two or more curves of
        one element/edge (a single curve has nothing to share a window with).
        The answer is remembered and is the default next time; the import
        batch never asks and uses the remembered value.
        """
        scope = self._scope_from_module("auto_norm")
        sets: dict = {}
        for g in self._targets_for_scope(scope):
            key = (str(g.element or ""), str(g.edge or ""))
            sets[key] = sets.get(key, 0) + 1
        biggest = max(sets.values()) if sets else 0
        if biggest >= 2:
            answer = self._ask_uniform_ft(biggest)
            if answer is None:
                return
            self.store.setValue("auto_norm/uniform_ft", "1" if answer else "0")
        self._auto_normalize(scope)

    def _ask_uniform_ft(self, n_curves: int) -> Optional[bool]:
        """Yes = one FT k window per element/edge set; None = cancelled."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.i18n.t("auto_norm_uniform_ft"))
        box.setText(self.i18n.t("auto_norm_uniform_ft_ask", n=n_curves))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel
        )
        box.button(QMessageBox.StandardButton.Yes).setText(self.i18n.t("auto_norm_uniform_ft_yes"))
        box.button(QMessageBox.StandardButton.No).setText(self.i18n.t("auto_norm_uniform_ft_no"))
        box.button(QMessageBox.StandardButton.Cancel).setText(self.i18n.t("cancel"))
        box.setDefaultButton(
            QMessageBox.StandardButton.Yes if self._uniform_ft_enabled() else QMessageBox.StandardButton.No
        )
        ans = box.exec()
        if ans == QMessageBox.StandardButton.Yes:
            return True
        if ans == QMessageBox.StandardButton.No:
            return False
        return None

    def _uniform_ft_enabled(self) -> bool:
        try:
            return str(self.store.value("auto_norm/uniform_ft", "1")).strip().lower() not in (
                "0", "false", "no", "off",
            )
        except Exception:
            return True

    def _apply_uniform_ft_windows(self, targets) -> int:
        """One FT k window per element/edge set (rule: uniform_ft_window).

        Comparing |chi(R)| peak heights across samples only works when every
        curve uses the same k range and weighting, so a multi-curve Auto norm
        unifies the window unless the user turned the option off.
        """
        gs = [g for g in (targets or []) if getattr(g, "energy", None) is not None]
        if len(gs) < 2 or not self._uniform_ft_enabled():
            return 0
        sets: dict = {}
        for g in gs:
            key = (str(g.element or ""), str(g.edge or ""))
            sets.setdefault(key, []).append(g)
        n = 0
        for (el, edge), members in sets.items():
            if len(members) < 2:
                continue
            win = pipeline_rules.uniform_ft_window(members)
            if not win.get("ok") or not win.get("value"):
                continue
            kmin, kmax = win["value"]
            changed = False
            for g in members:
                if abs(float(g.settings.kmin) - kmin) < 1e-9 and abs(float(g.settings.kmax) - kmax) < 1e-9:
                    continue
                g.settings.kmin = float(kmin)
                g.settings.kmax = float(kmax)
                g.process()
                changed = True
                n += 1
            if changed:
                self._log(
                    f"auto-norm: FT k window unified for {el} {edge} -> {kmin:.1f}-{kmax:.1f} A^-1"
                    f" ({len(members)} curves; tightest {win.get('tightest', '')})"
                )
        return n

    def _auto_normalize_reinit(self) -> None:
        """Undo last Auto normalization via shared module undo stack."""
        if "auto_norm" not in (getattr(self, "_module_undo", None) or {}):
            self._log(self.i18n.t("auto_norm_reinit_none"))
            return
        self._undo_module("auto_norm")

    def _ai_check_scope(self) -> str:
        """Scope of the single top-level AI Check (Current / Marked / All)."""
        return self._scope_from_module("ai_check")

    def _normalization_ai_contexts(self, scope: Optional[str] = None) -> tuple[str, list[dict]]:
        """Build bounded raw-data evidence for the data-processing category.

        Per dataset the AI gets: current normalization settings, an
        edge-dense adaptive raw μ(E) sample, program-computed diagnostics
        (derivative E0 candidate, pre-edge line fit, edge-step estimate,
        current post-edge norm mean/slope), and samples of the *current*
        normalized spectrum — so it can judge whether the current
        normalization is actually reasonable, not just guess parameters.
        """
        scope = str(scope or self._ai_check_scope())
        targets = self._targets_for_scope(scope)
        contexts: list[dict] = []
        for group in targets:
            energy = np.asarray(group.energy if group.energy is not None else [], dtype=float)
            mu = np.asarray(group.mu if group.mu is not None else [], dtype=float)
            count = min(energy.size, mu.size)
            settings = group.settings.to_dict()
            ctx: dict = {
                "dataset": {
                    "name": group.name, "element": group.element, "edge": group.edge,
                    "points": int(count), "energy_shift": float(group.energy_shift),
                },
                "normalization": {
                    "e0_auto": settings.get("e0") is None,
                    "e0": settings.get("e0"), "pre1": settings.get("pre1"),
                    "pre2": settings.get("pre2"), "norm1": settings.get("norm1"),
                    "norm2": settings.get("norm2"), "nnorm": settings.get("nnorm"),
                    "flatten": settings.get("make_flat"),
                },
                "sample_columns": ["energy_eV", "mu"],
                "samples": [],
            }
            if count >= 8:
                e = energy.astype(float) + float(group.energy_shift)
                order = np.argsort(e)
                e, m = e[order], mu[order]
                s = group.settings
                dmude = np.gradient(m, e)
                e0_deriv = float(e[int(np.argmax(dmude))])
                e0_ref = float(s.e0) if s.e0 is not None else e0_deriv
                # adaptive sampling: dense near the edge (±60 eV), sparse outside
                dense_idx = np.where((e >= e0_ref - 60.0) & (e <= e0_ref + 60.0))[0]
                sparse_idx = np.where((e < e0_ref - 60.0) | (e > e0_ref + 60.0))[0]
                if dense_idx.size:
                    dense_idx = dense_idx[np.unique(np.linspace(0, dense_idx.size - 1, min(32, dense_idx.size), dtype=int))]
                if sparse_idx.size:
                    sparse_idx = sparse_idx[np.unique(np.linspace(0, sparse_idx.size - 1, min(16, sparse_idx.size), dtype=int))]
                idx = np.sort(np.concatenate([dense_idx, sparse_idx])) if (dense_idx.size or sparse_idx.size) else np.array([], dtype=int)
                ctx["samples"] = [[float(e[i]), float(m[i])] for i in idx]
                derived: dict = {"e0_by_derivative": e0_deriv}
                pre_mask = (e >= e0_ref + float(s.pre1)) & (e <= e0_ref + float(s.pre2))
                if int(pre_mask.sum()) >= 3:
                    slope, intercept = np.polyfit(e[pre_mask], m[pre_mask], 1)
                    derived["pre_edge_line"] = {
                        "slope": float(slope), "intercept": float(intercept),
                        "points": int(pre_mask.sum()),
                    }
                lo = (e >= e0_ref - 150.0) & (e <= e0_ref - 30.0)
                hi = (e >= e0_ref + 30.0) & (e <= e0_ref + 150.0)
                if lo.any() and hi.any():
                    derived["edge_step_estimate"] = float(np.median(m[hi]) - np.median(m[lo]))
                sess = group.session
                arrs = getattr(sess, "arrays", None) if sess is not None else None
                if arrs is not None and arrs.energy is not None and arrs.norm is not None:
                    # processed arrays already carry the energy shift
                    ne = np.asarray(arrs.energy, dtype=float)
                    nm = np.asarray(arrs.norm, dtype=float)
                    ncount = min(ne.size, nm.size)
                    if ncount >= 8:
                        ne, nm = ne[:ncount], nm[:ncount]
                        norm_mask = (ne >= e0_ref + float(s.norm1)) & (ne <= e0_ref + float(s.norm2))
                        if int(norm_mask.sum()) >= 3:
                            nslope, _nint = np.polyfit(ne[norm_mask], nm[norm_mask], 1)
                            derived["current_norm_post"] = {
                                "mean": float(np.mean(nm[norm_mask])),
                                "slope": float(nslope),
                                "note": "ideal: mean≈1.0, slope≈0",
                            }
                        ni = np.unique(np.linspace(0, ncount - 1, min(16, ncount), dtype=int))
                        ctx["samples_norm_current"] = [[float(ne[i]), float(nm[i])] for i in ni]
                ctx["derived"] = derived
            # High-k noise of this curve: the number the batch pipeline uses to
            # pick a channel and an FT window, so the AI judges the same figure.
            ctx["k_noise"] = self._group_k_noise(group)
            contexts.append(ctx)
        return scope, contexts

    @staticmethod
    def _group_k_noise(group, k_lo: float = 9.0, k_hi: float = 12.0) -> Optional[float]:
        """RMS of k^2 chi between k_lo and k_hi (None when that k is unreachable)."""
        arrs = getattr(getattr(group, "session", None), "arrays", None)
        k = getattr(arrs, "k", None) if arrs is not None else None
        chi = getattr(arrs, "chi", None) if arrs is not None else None
        if k is None or chi is None:
            return None
        kk = np.asarray(k, dtype=float)
        cc = np.asarray(chi, dtype=float)
        n = min(kk.size, cc.size)
        if n < 8:
            return None
        m = (kk[:n] >= k_lo) & (kk[:n] <= k_hi) & np.isfinite(cc[:n])
        if int(m.sum()) < 4:
            return None
        return float(np.sqrt(np.mean((kk[:n][m] ** 2 * cc[:n][m]) ** 2)))

    # ================== ONE AI Check (v3.19.5, top bar) ==================
    def _build_ai_check_bar(self) -> "QWidget":
        """AI Check above the page tabs: one control for every page.

        Categories are user-ticked (data processing / fitting) because not
        every dataset is meant to be fitted; the run sends evidence for the
        ticked categories only and the parser refuses the others.
        """
        host = QFrame()
        host.setObjectName("aiCheckBar")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 2)
        lay.setSpacing(2)
        # v3.19.6 layout: row 1 = the three actions, row 2 = the options
        self.btn_ai_check = QPushButton()
        self.btn_ai_check.setObjectName("ghostBtn")
        self.btn_ai_check.clicked.connect(self._on_ai_check_clicked)
        self.btn_ai_check_apply = QPushButton()
        self.btn_ai_check_apply.setObjectName("ghostBtn")
        self.btn_ai_check_apply.clicked.connect(self._read_apply_ai_check)
        self.btn_ai_check_undo = self._register_undo_btn("ai_check")
        # natural widths here (not the shared module width): six items share
        # this one row
        for b in (self.btn_ai_check, self.btn_ai_check_apply):
            b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        rad_cur, rad_mk, rad_all = self._make_scope_radios("ai_check")
        saved = str(self.store.value("ai_check/categories", "process,fit") or "process,fit")
        saved_cats = {c.strip().lower() for c in saved.split(",") if c.strip()}
        self.chk_ai_cat_process = QCheckBox()
        self.chk_ai_cat_fit = QCheckBox()
        self.chk_ai_cat_process.setChecked("process" in saved_cats)
        self.chk_ai_cat_fit.setChecked("fit" in saved_cats)
        for box in (self.chk_ai_cat_process, self.chk_ai_cat_fit):
            box.toggled.connect(self._ai_check_categories_changed)
        # v3.19.6: the categories are asked in a dialog on the AI Check click
        # (_ask_ai_check_categories); these two boxes only hold the choice.
        for box in (self.chk_ai_cat_process, self.chk_ai_cat_fit):
            box.setParent(host)
            box.hide()
        # ONE row (v3.19.6): [AI Check] ~ ○ Current ~ ○ Marked ~ ○ All ~ [Apply][↶]
        # - this row is what sets the right panel's minimum width
        top = QWidget()
        th = QHBoxLayout(top)
        th.setContentsMargins(0, 2, 0, 2)
        th.setSpacing(4)
        th.addWidget(self.btn_ai_check, 0)
        for r in (rad_cur, rad_mk, rad_all):
            r.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            th.addStretch(1)
            th.addWidget(r, 0)
        th.addStretch(1)
        th.addWidget(self.btn_ai_check_apply, 0)
        th.addWidget(self.btn_ai_check_undo, 0)
        lay.addWidget(top)
        self.ai_check_row = top
        self.ai_check_bar = host
        return host

    def _ai_check_categories(self) -> tuple:
        """(process, fit) as ticked in the top AI Check bar."""
        return (
            bool(self.chk_ai_cat_process.isChecked()),
            bool(self.chk_ai_cat_fit.isChecked()),
        )

    def _ai_check_categories_changed(self, *_args) -> None:
        want_process, want_fit = self._ai_check_categories()
        cats = [c for c, on in (("process", want_process), ("fit", want_fit)) if on]
        self.store.setValue("ai_check/categories", ",".join(cats))

    def _show_ai_check_report(
        self, suggestion: dict, *, switch_page: bool = False, channel: str = ""
    ) -> None:
        """Render an AI Check answer in the report pane beside the plot."""
        try:
            report = format_ai_check_report(suggestion, language=self.i18n.lang, channel=channel)
        except Exception as exc:  # never lose the raw answer to a format bug
            report = str(exc)
        base = (
            self.last_fit.to_text()
            if isinstance(self.last_fit, FitResult) and self.last_fit.ok
            else ""
        )
        self._set_fit_result_text(f"{base}\n\n{report}" if base else report)
        if switch_page:
            self._set_left_mode("fit")

    def _on_ai_check_clicked(self) -> None:
        """AI Check button: pick the categories in a dialog, then run."""
        if self._ask_ai_check_categories():
            self._run_ai_check()

    def _ask_ai_check_categories(self) -> bool:
        """Dialog: what may the AI look at? Pre-ticked with the last choice.

        Returns False on Cancel. OK needs at least one category.
        """
        t = self.i18n.t
        dlg = QDialog(self)
        dlg.setWindowTitle(t("ai_check"))
        lay = QVBoxLayout(dlg)
        lab = QLabel(t("ai_check_cat_ask"))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        boxes = []
        for src, key in ((self.chk_ai_cat_process, "ai_check_cat_process"), (self.chk_ai_cat_fit, "ai_check_cat_fit")):
            cb = QCheckBox(t(key))
            cb.setToolTip(t(key + "_tip"))
            cb.setChecked(src.isChecked())
            tip = QLabel(t(key + "_tip"))
            tip.setWordWrap(True)
            tip.setStyleSheet("color:#6b625d; font-size:11px; margin-left:22px;")
            lay.addWidget(cb)
            lay.addWidget(tip)
            boxes.append(cb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(t("ai_check"))
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(t("cancel"))
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)

        def _sync_ok(*_a):
            ok_btn.setEnabled(any(b.isChecked() for b in boxes))

        for b in boxes:
            b.toggled.connect(_sync_ok)
        _sync_ok()
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.setMinimumWidth(360)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        self.chk_ai_cat_process.setChecked(boxes[0].isChecked())
        self.chk_ai_cat_fit.setChecked(boxes[1].isChecked())
        return True

    def _run_ai_check(self) -> None:
        """Run the single AI Check for the ticked categories and scope."""
        want_process, want_fit = self._ai_check_categories()
        if not (want_process or want_fit):
            QMessageBox.information(
                self, self.i18n.t("ai_check"), self.i18n.t("ai_check_need_category")
            )
            return
        scope = self._ai_check_scope()
        evidence: list = []
        categories: list = []
        if want_process:
            _p_scope, process_ctx = self._normalization_ai_contexts(scope)
            if process_ctx:
                categories.append("process")
                evidence.extend({"category": "process", **ctx} for ctx in process_ctx)
            record = getattr(self.project, "processing_record", None)
            if record is not None:
                try:
                    evidence.append({
                        "category": "process",
                        "processing_record": record.to_dict(),
                    })
                except Exception:
                    pass
        if want_fit:
            _f_scope, fit_ctx = self._ai_suggestion_contexts(scope)
            if fit_ctx:
                categories.append("fit")
                evidence.extend({"category": "fit", **ctx} for ctx in fit_ctx)
            else:
                self._log("AI Check: no completed fit for this scope; fit category skipped")
        if not categories:
            QMessageBox.information(
                self, self.i18n.t("ai_check"), self.i18n.t("ai_suggestion_no_context")
            )
            return
        channels, problems = self._ai_check_channels()
        if not channels:
            self._explain_no_ai_channel(problems)
            return
        if self._ai_check_busy():
            QMessageBox.information(
                self, self.i18n.t("ai_check"), self.i18n.t("ai_check_busy")
            )
            return
        import datetime

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        prefix = "ai_check_" + "-".join(categories)
        result_path = self._ai_suggestion_cache_dir() / f"{prefix}_{stamp}_result.json"
        evidence = self._redact_ai_evidence(evidence)
        prompt_text = build_ai_check_prompt(
            scope, categories, evidence, language=self.i18n.lang
        )
        self.store.setValue("ai_check/categories", ",".join(categories))
        self._ai_check_n_items = len(evidence)
        self._ai_check_run = {
            "prompt": prompt_text, "result_path": result_path, "scope": scope,
            "channels": channels, "problems": problems, "failures": [],
            "sign_in": [], "cancel": threading.Event(), "t0": time.monotonic(),
        }
        self._ai_check_progress_start()
        self._ai_check_try(self._ai_check_run, 0)

    # ---- AI Check channels (v4.4.0): tick-to-activate CLI rows and API rows ----
    def _ai_flag(self, key: str) -> bool:
        return str(self.store.value(key, False) or "").strip().lower() in ("1", "true", "yes")

    def _ai_cli_active(self, agent: str) -> bool:
        return agent in AGENTREG.AI_CHECK_CLIS and self._ai_flag(f"ai_active/cli_{agent}")

    def _ai_api_active(self, provider: str) -> bool:
        return provider in AI_REVIEW_PROVIDERS and self._ai_flag(f"ai_active/api_{provider}")

    def _ai_check_use(self) -> str:
        """Settings "AI Check uses": auto, or one channel id (cli:<agent> / api:<provider>)."""
        return str(self.store.value("ai_check_use", "auto") or "auto").strip().lower()

    def _ai_channel_label(self, kind: str, name: str) -> str:
        if kind == "cli":
            return f"{self._coding_agent_label(name)} CLI"
        return f"{self._api_provider_label(name)} API"

    def _migrate_ai_activation(self) -> None:
        """v4.4.0: turn the old Default AI / channel pickers into ticked rows, once.

        Saved API keys become ticked API rows (unless the old channel was CLI
        only); an explicit Claude default becomes the ticked Claude CLI row
        (unless the old channel was API only).  The old keys are then dropped.
        """
        if self.store.contains("ai_active/v1"):
            return
        old_agent = str(self.store.value("ai_suggestion_agent", "") or "").strip().lower()
        old_channel = str(self.store.value("ai_check_channel", "auto") or "auto").strip().lower()
        if old_channel != "cli":
            keys = self._coding_api_keys()
            for prov, _label, _sk in AGENTREG.API_SLOTS:
                if prov in AI_REVIEW_PROVIDERS and keys.get(prov):
                    self.store.setValue(f"ai_active/api_{prov}", True)
        if old_channel != "api" and old_agent == "claude":
            self.store.setValue("ai_active/cli_claude", True)
        for key in ("ai_suggestion_agent", "ai_check_channel", "ai_review_provider", "ai_suggestion_model"):
            self.store.remove(key)
        self.store.setValue("ai_active/v1", True)

    def _ai_check_channels(self) -> tuple:
        """Channels AI Check will try, in order, and why ticked rows are left out.

        Auto = the ticked CLIs first (Settings row order), then the ticked APIs.
        A ticked row that cannot run (CLI not found, no key, no model) is
        reported, never skipped silently.  Returns ([(kind, name, detail)], [problem]);
        detail is the CLI path or the API model.
        """
        self._migrate_ai_activation()
        t = self.i18n.t
        ready: list = []
        problems: list = []
        for agent in AGENTREG.AI_CHECK_CLIS:
            if not self._ai_cli_active(agent):
                continue
            path = self._resolve_coding_cli_path(agent)
            if path:
                ready.append(("cli", agent, path))
            else:
                problems.append(t("ai_channel_no_cli", label=self._ai_channel_label("cli", agent)))
        for prov, _label, _sk in AGENTREG.API_SLOTS:
            if not self._ai_api_active(prov):
                continue
            label = self._ai_channel_label("api", prov)
            model = self._ai_review_model(prov)
            if not self._ai_review_api_key(prov):
                problems.append(t("ai_channel_no_key", label=label))
            elif not model:
                problems.append(t("ai_channel_no_model", label=label))
            else:
                ready.append(("api", prov, model))
        use = self._ai_check_use()
        if use != "auto":
            picked = [c for c in ready if f"{c[0]}:{c[1]}" == use]
            if not picked:
                kind, _sep, name = use.partition(":")
                problems.insert(0, t("ai_channel_use_missing", label=self._ai_channel_label(kind, name)))
            ready = picked
        return ready, problems

    def _explain_no_ai_channel(self, problems: list) -> None:
        """Nothing usable is ticked: say what is missing and offer Settings."""
        t = self.i18n.t
        text = t("ai_check_no_channel")
        if problems:
            text += "\n\n- " + "\n- ".join(problems)
        text += "\n\n" + t("ai_check_open_settings_ask")
        answer = QMessageBox.question(
            self, t("ai_check"), text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._open_settings()

    @staticmethod
    def _ai_cli_workdir() -> Path:
        """Neutral folder the confined CLIs start in, outside any project:
        Claude Code and Kimi read CLAUDE.md / AGENTS.md from where they start."""
        path = Path(tempfile.gettempdir()) / "smartlarch_ai_check"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _ai_check_runner(self, channel: tuple, prompt: str, cancel):
        """Zero-argument job for one channel (runs on a worker thread)."""
        kind, name, detail = channel
        if kind == "cli":
            cwd = str(self._ai_cli_workdir())
            env = self._coding_env(None)  # key-free: the CLI uses its own login
            if name == "claude":
                return lambda: run_claude_headless(detail, prompt, cwd=cwd, env=env, cancel=cancel)
            return lambda: run_kimi_headless(detail, prompt, cwd=cwd, env=env, cancel=cancel)
        api_key = self._ai_review_api_key(name)
        region = self._qwen_region()
        return lambda: request_fit_review(
            name, api_key, detail, prompt, region=region, max_tokens=AI_CHECK_MAX_TOKENS
        )

    def _ai_check_busy(self) -> bool:
        run = getattr(self, "_ai_check_run", None)
        return bool(run) and not run.get("done")

    @staticmethod
    def _short_error(trace: str) -> str:
        """Last traceback line without its ``module.Class:`` prefix."""
        lines = (trace or "").strip().splitlines()
        line = lines[-1].strip() if lines else ""
        head, sep, rest = line.partition(": ")
        if sep and re.fullmatch(r"[\w.]+", head):
            return rest
        return line

    def _ai_check_try(self, run: dict, index: int) -> None:
        """Start channel ``index`` of this run; a failure moves on to the next one."""
        if run is not self._ai_check_run or run.get("done"):
            return
        channels = run["channels"]
        if index >= len(channels):
            self._ai_check_all_failed(run)
            return
        t = self.i18n.t
        channel = channels[index]
        label = self._ai_channel_label(channel[0], channel[1])
        if channel[0] == "api":
            label += f" / {channel[2]}"
        self._ai_check_progress_channel(run, label, index, len(channels))
        # AI Check threads run a subprocess or an HTTP call, never larch: they
        # stay off self.worker so FEFF / fit are not blocked and Cancel frees
        # the button at once (the old thread's late answer is dropped).
        worker = Worker(self._ai_check_runner(channel, run["prompt"], run["cancel"]), self)
        self._ai_workers.append(worker)

        def _next(detail: str) -> None:
            run["failures"].append((label, detail))
            self._log(t("ai_check_channel_failed", label=label, detail=detail))
            self._ai_check_try(run, index + 1)

        def _done(text: str) -> None:
            if run is not self._ai_check_run or run.get("done"):
                return
            try:
                suggestion = parse_ai_check_response(text)
            except AISuggestionError as exc:
                _next(str(exc))
                return
            run["done"] = True
            self._ai_check_progress_stop()
            secs = int(time.monotonic() - run["t0"])
            result_path = run["result_path"]
            try:
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(text, encoding="utf-8")
                # only a usable answer replaces the one Apply reads
                self.store.setValue("ai_check_result", str(result_path))
                self.store.setValue("ai_check_scope", run["scope"])
            except OSError as exc:
                self._log(f"AI Check result not saved: {exc}")
            self._show_ai_check_report(
                suggestion, switch_page=("fit" in (suggestion.get("categories") or [])),
                channel=f"{label} · {secs} s",
            )
            self._status(t("ai_check_done", label=label, secs=secs), 15000)  # _status logs it

        def _failed(trace: str) -> None:
            if run is not self._ai_check_run or run.get("done"):
                return
            if run["cancel"].is_set() or "AICheckCancelled" in trace:
                return
            if "SignInRequired" in trace:
                run["sign_in"].append(channel)
            _next(self._short_error(trace) or t("ai_review_failed"))

        def _finished() -> None:
            if worker in self._ai_workers:
                self._ai_workers.remove(worker)
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

        worker.finished_ok.connect(_done)
        worker.failed.connect(_failed)
        worker.finished.connect(_finished)
        worker.start()

    def _ai_check_all_failed(self, run: dict) -> None:
        """Every channel failed: one message that says why for each; when a CLI
        only needs a sign-in, offer it (the check then resumes by itself)."""
        run["done"] = True
        self._ai_check_progress_stop()
        t = self.i18n.t
        lines = [f"{label}: {detail}" for label, detail in run["failures"]] + list(run["problems"])
        self._log(t("ai_check_all_failed") + " " + " | ".join(lines))
        if run["sign_in"]:
            _kind, agent, path = run["sign_in"][0]
            self._cli_sign_in(agent, path, resume=self._run_ai_check)
            return
        QMessageBox.warning(
            self, t("ai_check"), t("ai_check_all_failed") + "\n\n- " + "\n- ".join(lines)
        )

    def _ai_check_progress_start(self) -> None:
        """A background AI Check has no window of its own: show a progress window.

        Indeterminate bar (the model reports no percentage), the channel in
        use, elapsed seconds, an honest expected duration, Hide and Cancel.
        """
        self._ai_check_progress_stop()
        t = self.i18n.t
        self._ai_check_t0 = time.monotonic()
        n_items = int(getattr(self, "_ai_check_n_items", 1) or 1)
        try:
            self.btn_ai_check.setEnabled(False)
        except RuntimeError:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(t("ai_progress_title"))
        dlg.setModal(False)
        dlg.setMinimumWidth(440)
        lay = QVBoxLayout(dlg)
        head = QLabel("")
        head.setWordWrap(True)
        switch = QLabel("")
        switch.setWordWrap(True)
        switch.setStyleSheet("color:#9a5b13; font-size:11px;")
        switch.setVisible(False)
        bar = QProgressBar()
        bar.setRange(0, 0)  # busy indicator: no fake percentages
        elapsed = QLabel("")
        expect = QLabel(t("ai_progress_expect_many" if n_items > 3 else "ai_progress_expect"))
        expect.setWordWrap(True)
        expect.setStyleSheet("color:#6b625d; font-size:11px;")
        row = QHBoxLayout()
        btn_hide = QPushButton(t("ai_progress_hide"))
        btn_cancel = QPushButton(t("ai_progress_cancel"))
        row.addStretch(1)
        row.addWidget(btn_hide)
        row.addWidget(btn_cancel)
        for w_ in (head, switch, bar, elapsed, expect):
            lay.addWidget(w_)
        lay.addLayout(row)
        btn_hide.clicked.connect(dlg.hide)
        btn_cancel.clicked.connect(self._ai_check_cancel)
        self._ai_check_dlg = dlg
        self._ai_check_dlg_head = head
        self._ai_check_dlg_switch = switch
        self._ai_check_dlg_elapsed = elapsed
        timer = QTimer(self)
        timer.setInterval(1000)
        timer.timeout.connect(self._ai_check_progress_tick)
        self._ai_check_timer = timer
        self._ai_check_progress_tick()
        timer.start()
        dlg.show()

    def _ai_check_progress_channel(self, run: dict, label: str, index: int, n: int) -> None:
        """Name the channel now in use; after a failure say which one gave up and why."""
        t = self.i18n.t
        self._log(t("ai_check_started", label=label, i=index + 1, n=n))
        head = getattr(self, "_ai_check_dlg_head", None)
        switch = getattr(self, "_ai_check_dlg_switch", None)
        try:
            if head is not None:
                head.setText(t("ai_progress_channel", label=label, i=index + 1, n=n))
            if switch is not None and run["failures"]:
                prev, why = run["failures"][-1]
                # short translated reason on screen; the CLI's own words in the tooltip
                switch.setText(t("ai_progress_switch", prev=prev, reason=self._ai_reason(why), label=label))
                switch.setToolTip(why)
                switch.setVisible(True)
                dlg = getattr(self, "_ai_check_dlg", None)
                if dlg is not None:
                    dlg.adjustSize()  # the extra line must not push the hint text out of view
        except RuntimeError:
            pass

    def _ai_check_progress_tick(self) -> None:
        secs = int(time.monotonic() - getattr(self, "_ai_check_t0", time.monotonic()))
        t = self.i18n.t
        try:
            self.btn_ai_check.setText(t("ai_check_working", secs=secs))
            lab = getattr(self, "_ai_check_dlg_elapsed", None)
            if lab is not None:
                lab.setText(t("ai_progress_elapsed", secs=secs))
        except RuntimeError:
            return
        if secs and secs % 20 == 0:
            self._log(t("ai_check_still", secs=secs))

    def _ai_check_progress_stop(self) -> None:
        timer = getattr(self, "_ai_check_timer", None)
        self._ai_check_timer = None
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        dlg = getattr(self, "_ai_check_dlg", None)
        self._ai_check_dlg = None
        self._ai_check_dlg_head = None
        self._ai_check_dlg_switch = None
        self._ai_check_dlg_elapsed = None
        if dlg is not None:
            try:
                dlg.close()
                dlg.deleteLater()
            except RuntimeError:
                pass
        try:
            self.btn_ai_check.setEnabled(True)
            self.btn_ai_check.setText(self.i18n.t("ai_check"))
        except RuntimeError:
            pass

    def _ai_check_cancel(self) -> None:
        """Cancel: kill the CLI process (or stop waiting for the API reply).

        The button is free again at once: the old thread's late answer is dropped.
        """
        run = getattr(self, "_ai_check_run", None)
        if run is not None and not run.get("done"):
            run["cancel"].set()
            run["done"] = True
        self._ai_check_progress_stop()
        self._log(self.i18n.t("ai_check_cancelled"))

    def _cli_sign_in(self, agent: str, cli_path: str, resume=None) -> None:
        """Offer the CLI's own sign-in in a visible terminal, then continue.

        Claude: ``claude auth login`` (opens the browser itself); Kimi:
        ``kimi login`` (device code).  When the window closes we ask the CLI for
        real and resume the AI Check (``resume``); without ``resume`` (Verify)
        we just say what to do next.
        """
        t = self.i18n.t
        name = self._coding_agent_label(agent)
        login = ["auth", "login"] if agent == "claude" else ["login"]
        command = " ".join([Path(cli_path).stem] + login)
        answer = QMessageBox.question(
            self, t("ai_check"), t("ai_cli_signin_ask", name=name, command=command),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            proc = subprocess.Popen(
                claude_cli_head(cli_path) + login,
                env=self._coding_env(None), close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except OSError as exc:
            QMessageBox.warning(self, t("ai_check"), str(exc))
            return
        self._log(t("ai_cli_signin_started", command=command))
        self._cli_login_proc = proc
        started = time.monotonic()
        timer = QTimer(self)
        timer.setInterval(1000)

        def _finished(rc: int) -> None:
            """The login window is gone.  Its exit code is not trusted (closing the
            window gives 129, success is not documented): ask the CLI for real."""
            self._log(t("ai_cli_signin_checking", name=name))
            env = self._coding_env(None)
            if agent == "claude":
                probe = lambda: run_claude_headless(cli_path, "Reply with exactly: OK", timeout=60, env=env)  # noqa: E731
            else:
                cwd = str(self._ai_cli_workdir())
                probe = lambda: run_kimi_headless(cli_path, "Reply with exactly: OK", cwd=cwd, timeout=90, env=env)  # noqa: E731
            worker = Worker(probe, self)
            self._signin_worker = worker

            def _ok(_text) -> None:
                self._log(t("ai_cli_signin_done", name=name))
                if resume is not None:
                    resume()
                else:
                    QMessageBox.information(self, t("ai_verify"), t("ai_cli_signin_done", name=name))

            def _bad(trace: str) -> None:
                self._log(t("ai_cli_signin_failed", name=name, rc=rc))
                QMessageBox.warning(self, t("ai_check"), t("ai_cli_signin_failed", name=name, rc=rc))

            worker.finished_ok.connect(_ok)
            worker.failed.connect(_bad)
            worker.finished.connect(worker.deleteLater)
            worker.start()

        def _poll() -> None:
            rc = proc.poll()
            if rc is None and time.monotonic() - started < 900:
                return
            timer.stop()
            timer.deleteLater()
            if rc is None:  # 15 min with no result: stop waiting, leave the window alone
                self._log(t("ai_cli_signin_timeout"))
                return
            _finished(rc)

        timer.timeout.connect(_poll)
        timer.start()

    # ---- AI Check: comparison table + apply (nothing is written silently) ----
    AI_CHECK_PARAM_LABELS = {
        "e0_auto": "E0 auto",
        "e0": "E0 (eV)",
        "pre1": "pre1 (eV)",
        "pre2": "pre2 (eV)",
        "norm1": "norm1 (eV)",
        "norm2": "norm2 (eV)",
        "nnorm": "nnorm",
        "flatten": "flatten",
        "nvict": "nvict",
        "rbkg": "Rbkg (A)",
        "bkg_kmin": "bkg kmin",
        "bkg_kmax": "bkg kmax",
        "kmin": "FT kmin",
        "kmax": "FT kmax",
    }

    @staticmethod
    def _ai_check_current_value(settings, key: str):
        """Current value of one AI Check process parameter for the compare table."""
        if key == "e0_auto":
            return settings.e0 is None
        if key == "flatten":
            return bool(getattr(settings, "make_flat", False))
        return getattr(settings, key, None)

    def _ai_check_compare_rows(self, suggestion: dict, scope: str) -> list:
        """Build (group, parameter, current, suggested, reason, warn) rows."""
        targets = self._targets_for_scope(scope)
        by_name = {group.name: group for group in targets}
        rows: list = []
        process = suggestion.get("process") or {}
        if isinstance(process, dict):
            for row in process.get("datasets") or []:
                name = str(row.get("name") or "")
                group = by_name.get(name)
                for key, value in (row.get("parameters") or {}).items():
                    label = self.AI_CHECK_PARAM_LABELS.get(key, key)
                    current = (
                        self._ai_check_current_value(group.settings, key)
                        if group is not None else None
                    )
                    rows.append({
                        "group": name,
                        "parameter": label,
                        "key": key,
                        "current": current,
                        "suggested": value,
                        "reason": str(row.get("summary") or ""),
                        "warn": "" if group is not None else "no such dataset",
                    })
        return rows

    def _ai_check_confirm_dialog(self, rows: list) -> bool:
        """Show the compare table; True only when the user accepts."""
        t = self.i18n.t
        dlg = QDialog(self)
        dlg.setWindowTitle(t("ai_check_compare_title"))
        dlg.setMinimumSize(620, 380)
        dlg.resize(760, 500)
        lay = QVBoxLayout(dlg)
        hint = QLabel(t("ai_check_compare_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b625d; font-size:11px;")
        lay.addWidget(hint)
        tbl = QTableWidget(0, 5)
        tbl.setHorizontalHeaderLabels([
            t("ai_check_col_group"), t("ai_check_col_param"),
            t("ai_check_col_current"), t("ai_check_col_suggested"),
            t("ai_check_col_reason"),
        ])
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setWordWrap(True)  # the reason is the point of the review: show all of it
        tbl.verticalHeader().setVisible(False)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        tbl.setRowCount(len(rows))
        for i, row in enumerate(rows):
            def _fmt(value):
                if value is None:
                    return "-"
                if isinstance(value, bool):
                    return t("yes") if value else t("no")
                if isinstance(value, float):
                    return f"{value:.3f}".rstrip("0").rstrip(".")
                return str(value)
            cells = [
                str(row.get("group") or ""),
                str(row.get("parameter") or ""),
                _fmt(row.get("current")),
                _fmt(row.get("suggested")),
                str(row.get("reason") or ""),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if row.get("warn"):
                    item.setForeground(QBrush(Qt.GlobalColor.darkYellow))
                tbl.setItem(i, c, item)
        lay.addWidget(tbl, 1)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(t("ai_check_compare_ok"))
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(t("cancel"))
        # wrapped reasons need their row heights once the stretch column has its width
        QTimer.singleShot(0, tbl.resizeRowsToContents)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        return bool(dlg.exec())

    def _apply_process_suggestion(self, suggestion: dict, scope: str) -> tuple:
        """Write per-dataset data-processing parameters; self-check each group.

        Returns (applied, failures).  A group whose normalization fails the
        shared self-check is reverted to its previous settings and reported, so
        an AI suggestion can never leave a curve visibly wrong.
        """
        targets = self._targets_for_scope(scope)
        by_name = {group.name: group for group in targets}
        process = suggestion.get("process") or {}
        patches = list(process.get("datasets") or []) if isinstance(process, dict) else []
        unknown = [row.get("name", "") for row in patches if row.get("name") not in by_name]
        if unknown:
            raise AISuggestionError(
                "AI Check names no dataset in this scope: " + ", ".join(unknown) + "."
            )
        if not patches:
            return 0, []
        touched = [by_name[row["name"]] for row in patches]
        self._push_module_undo("ai_check", touched)
        applied = 0
        failures: list = []
        for row in patches:
            group = by_name[row["name"]]
            values = dict(row.get("parameters") or {})
            before = deepcopy(group.settings)
            before_shift = float(group.energy_shift)
            settings = deepcopy(group.settings)
            if values.get("e0_auto") is True:
                settings.e0 = None
            elif "e0" in values:
                settings.e0 = float(values["e0"])
            for key in ("pre1", "pre2", "norm1", "norm2", "rbkg",
                        "bkg_kmin", "bkg_kmax", "kmin", "kmax"):
                if key in values:
                    setattr(settings, key, float(values[key]))
            for key in ("nnorm", "nvict"):
                if key in values:
                    setattr(settings, key, int(values[key]))
            if "flatten" in values:
                settings.make_flat = bool(values["flatten"])
            if "energy_shift" in values:
                group.energy_shift = float(values["energy_shift"])
            group.settings = settings
            result = group.process()
            if not result.ok:
                group.settings = before
                group.energy_shift = before_shift
                group.process()
                failures.append(f"{group.name}: {result.message[:120]}")
                self._log(f"AI Check process error [{group.name}]: {result.message[:200]}")
                continue
            e = np.asarray(group.energy, dtype=float) + float(group.energy_shift)
            ok_check, check_text = self._ensure_normalization_ok(
                group, settings, e, np.asarray(group.mu, dtype=float)
            )
            if not ok_check:
                # revert: the shared rule set refuses this normalization
                group.settings = before
                group.energy_shift = before_shift
                group.process()
                failures.append(f"{group.name}: {check_text}")
                self._log(
                    f"AI Check reverted [{group.name}]: normalization check failed ({check_text})"
                )
                continue
            applied += 1
        current = self.project.current
        if current is not None:
            self._load_group_to_controls(current)
            self._update_meta()
        self._replot()
        return applied, failures

    def _apply_fit_suggestion(self, suggestion: dict, scope: str) -> list:
        """Apply the fit category: GDS patches (Current only) + read-only FEFF."""
        applied: list = []
        fit = suggestion.get("fit") or {}
        if not isinstance(fit, dict):
            return applied
        gds = list(fit.get("gds") or [])
        if gds:
            if scope != "current":
                self._log(
                    "AI Check GDS patches skipped: only Current scope can change the GDS table"
                )
            else:
                # _apply_ai_suggestion_gds expects the SECTION shape
                self._apply_ai_suggestion_gds(
                    {"fit_scope": "current", "gds": {"parameters": gds}}
                )
                applied.append("GDS")
        feff = fit.get("feff") or {}
        if isinstance(feff, dict) and (feff.get("summary") or feff.get("recommendations")):
            self._show_ai_suggestion_feff({"feff": feff})
            applied.append("FEFF")
        return applied

    def _apply_ai_check(self, suggestion: dict) -> dict:
        """Apply only the categories this result carries (never unrequested ones)."""
        stored_scope = str(self.store.value("ai_check_scope", "") or "")
        scope = stored_scope or suggestion.get("scope") or self._ai_check_scope()
        if suggestion.get("scope") != scope:
            raise AISuggestionError(
                "AI Check result scope (" + str(suggestion.get("scope")) + ") does not match the "
                "scope used for the run (" + scope + "). Re-run AI Check."
            )
        categories = [str(c) for c in (suggestion.get("categories") or [])]
        out = {"applied": 0, "failures": [], "sections": [], "skipped": []}
        if "process" in categories and suggestion.get("process"):
            applied, failures = self._apply_process_suggestion(suggestion, scope)
            out["applied"] = applied
            out["failures"] = failures
            if applied:
                out["sections"].append("process")
        if "fit" in categories and suggestion.get("fit"):
            sections = self._apply_fit_suggestion(suggestion, scope)
            out["sections"].extend(sections)
        return out

    def _read_apply_ai_check(self) -> None:
        """Read the cached AI Check answer, show the compare table, then apply."""
        result_path = Path(str(self.store.value("ai_check_result", "") or ""))
        if not result_path.is_file():
            QMessageBox.information(
                self, self.i18n.t("ai_check_apply"), self.i18n.t("ai_check_no_result")
            )
            return
        try:
            if result_path.stat().st_size > 16_000_000:
                raise AISuggestionError("AI Check cache is unexpectedly large.")
            suggestion = parse_ai_check_response(
                result_path.read_text(encoding="utf-8", errors="replace")
            )
        except (OSError, AISuggestionError) as exc:
            QMessageBox.warning(self, self.i18n.t("ai_suggestion_failed"), str(exc)[:800])
            self._log(f"AI Check not applied: {exc}")
            return
        stored_scope = str(self.store.value("ai_check_scope", "") or "")
        scope = stored_scope or suggestion.get("scope") or self._ai_check_scope()
        rows = self._ai_check_compare_rows(suggestion, scope)
        if rows and not self._ai_check_confirm_dialog(rows):
            self._log("AI Check apply cancelled at the compare table")
            return
        try:
            outcome = self._apply_ai_check(suggestion)
        except Exception as exc:
            QMessageBox.warning(self, self.i18n.t("ai_suggestion_failed"), str(exc)[:800])
            self._log(f"AI Check not applied: {exc}")
            return
        self._show_ai_check_report(
            suggestion, switch_page=("fit" in (suggestion.get("categories") or []))
        )
        applied = int(outcome.get("applied") or 0)
        failures = list(outcome.get("failures") or [])
        sections = ", ".join(outcome.get("sections") or []) or "-"
        self._log(
            f"AI Check applied [{sections}]: {applied} dataset(s), {len(failures)} reverted"
        )
        text = self.i18n.t("ai_check_applied", n=applied, sections=sections)
        if failures:
            text += "\n\n" + self.i18n.t("ai_check_reverted") + "\n- " + "\n- ".join(failures)
        QMessageBox.information(self, self.i18n.t("ai_check_apply"), text)
        self._status(self.i18n.t("ai_check_applied", n=applied, sections=sections), 8000)

    def _snapshot_fit_ui(self) -> dict:
        """Capture GDS / paths / fit-window UI for auto-fit reinit."""
        return {
            "gds": [p.to_dict() for p in self._gds_from_table()],
            "paths": [p.to_dict() for p in self._paths_from_table()],
            "result_text": self.txt_fit_result.toPlainText() if hasattr(self, "txt_fit_result") else "",
        }

    def _restore_fit_ui_snapshot(self, snap: dict) -> None:
        gds = [GDSParam.from_dict(d) for d in (snap.get("gds") or [])]
        paths = [PathDef.from_dict(d) for d in (snap.get("paths") or [])]
        self.tbl_gds.setRowCount(0)
        for p in gds:
            self._gds_add_row(p)
        self.tbl_paths.setRowCount(0)
        for pd in paths:
            self._paths_add_row(pd)
        if hasattr(self, "txt_fit_result"):
            self._set_fit_result_text(str(snap.get("result_text") or ""))

    def _build_fit_settings_from_ui(self) -> FitSettings:
        s = self._settings_from_controls()
        return FitSettings(
            kmin=s.kmin,
            kmax=s.kmax,
            dk=s.dk,
            kwindow=s.kwindow,
            kw_list=(1, 2, 3),
            rmin=s.rmin,
            rmax=s.rmax,
            dr=s.dr,
            rwindow=s.rwindow,
            fit_space="r",
            rbkg=s.rbkg,
        )

    def _ensure_fit_model_ready(self) -> bool:
        """Ensure GDS/paths usable; load Model A + prepare FEFF if needed. Returns False if still not ready."""
        gds = self._gds_from_table()
        paths = self._paths_from_table()
        used = [p for p in paths if p.use and p.feff_file]
        if not gds or not used:
            self._load_model_a_preset()
            gds = self._gds_from_table()
            paths = self._paths_from_table()
            used = [p for p in paths if p.use and p.feff_file]
        if not used:
            self._log(self.i18n.t("no_usable_paths"))
            return False
        missing = [p.feff_file for p in used if not Path(p.feff_file).is_file()]
        if missing:
            model_a_names = {"path1_PtPt_2.788.dat", "path2_PtPt_3.052.dat"}
            if all(Path(m).name in model_a_names for m in missing):
                try:
                    prepare_model_a_paths(force=False)
                except Exception as exc:
                    self._log(f"FEFF prepare failed: {exc}")
                # Reload Model A paths in case prepare wrote stable names
                self._load_model_a_preset()
                paths = self._paths_from_table()
                used = [p for p in paths if p.use and p.feff_file]
                missing = [p.feff_file for p in used if not Path(p.feff_file).is_file()]
            if missing:
                self._log(self.i18n.t("feff_missing", f="\n".join(missing)))
                return False
        return True

    def _auto_fit(self, scope: str = "current") -> None:
        """
        Batch fit Current / Marked / All with the current GDS / paths / windows.
        Snapshots fit panel for Undo.
        """
        targets = self._targets_for_scope(scope)
        if not targets:
            return
        if not self._ensure_fit_model_ready():
            return

        fit_ui = self._snapshot_fit_ui()
        self._push_module_undo("auto_fit", targets, fit_ui=fit_ui)

        gds = self._gds_from_table()
        paths = self._paths_from_table()
        fs = self._build_fit_settings_from_ui()
        n_total = len(targets)
        self._progress_begin(
            self.i18n.t("progress_auto_fit_working", n=n_total),
            maximum=n_total,
            window_title=self.i18n.t("progress_auto_fit_title"),
        )
        n_ok = 0
        last_report = ""
        last_result: Optional[FitResult] = None
        cancelled = False
        try:
            for i, g in enumerate(targets):
                if not self._progress_update(
                    i,
                    self.i18n.t(
                        "progress_auto_fit_group", i=i + 1, n=n_total, name=g.name
                    ),
                ):
                    cancelled = True
                    self._log(
                        self.i18n.t(
                            "progress_auto_fit_cancelled", ok=n_ok, n=n_total
                        )
                    )
                    break
                try:
                    if g.session is None or g.session.group is None:
                        # Process with group's own settings (or panel settings)
                        if g.settings is None:
                            g.settings = self._settings_from_controls()
                        r = g.process()
                        if not r.ok:
                            self._log(f"auto-fit preprocess fail [{g.name}]: {r.message[:200]}")
                            self._progress_update(i + 1)
                            continue
                    if g.session is None or g.session.group is None:
                        self._log(f"auto-fit skip {g.name}: no processed group")
                        self._progress_update(i + 1)
                        continue
                    group = g.session.snapshot_group_for_fit()
                    # element→path assignment (v0.9.0): fit each group only with
                    # paths tagged for its element (untagged = fallback)
                    used_g, kind = self._paths_for_group(g, paths)
                    if not used_g:
                        self._log(
                            self._bi(
                                f"auto-fit 跳过 {g.name}：{g.element} 边无匹配路径（请检查元素/文件/路径分配）",
                                f"auto-fit skip {g.name}: no paths assigned for {g.element} edge (check element/file/path assignment)",
                            )
                        )
                        self._progress_update(i + 1)
                        continue
                    if kind == "fallback-untagged":
                        self._log(
                            self._bi(
                                f"auto-fit 注意 {g.name}：{g.element} 边使用未分配路径（{len(used_g)} 条）",
                                f"auto-fit note {g.name}: {g.element} edge uses untagged paths ({len(used_g)})",
                            )
                        )
                    result = run_feffit(group, gds, used_g, fs)
                    if isinstance(result, FitResult) and result.ok:
                        n_ok += 1
                        last_report = result.to_text()
                        last_result = result
                        try:
                            save_fit_to_history(
                                result, gds, used_g, fs, data_desc=g.name,
                                history_dir=self._history_cache_dir(),
                            )
                        except Exception as exc:
                            self._log(f"history save failed [{g.name}]: {exc}")
                        rf = (result.statistics or {}).get("rfactor") or (
                            result.statistics or {}
                        ).get("r_factor")
                        self._log(f"auto-fit OK {g.name}: R={rf if rf is not None else '?'}")
                    else:
                        msg = (
                            result.message
                            if isinstance(result, FitResult)
                            else str(result)
                        )
                        self._log(f"auto-fit fail [{g.name}]: {str(msg)[:300]}")
                        if isinstance(result, FitResult):
                            last_report = result.to_text()
                except Exception as exc:
                    self._log(f"auto-fit error [{g.name}]: {exc}")
                self._progress_update(i + 1)
            if not cancelled:
                self._progress_update(n_total, self.i18n.t("progress_done"))
        finally:
            self._progress_end()

        if last_report:
            self._set_fit_result_text(last_report)
        if last_result is not None:
            self.last_fit = last_result  # overlay the last successful batch fit
        try:
            self._refresh_fit_history()
        except Exception:
            pass
        self._refresh_fit_panel()
        self._replot()
        self._log(self.i18n.t("auto_fit_done", ok=n_ok, n=n_total))

    def _auto_fit_reinit(self) -> None:
        """Undo last Auto fit (panel + group snaps via module undo)."""
        if "auto_fit" not in (getattr(self, "_module_undo", None) or {}):
            self._log(self.i18n.t("auto_fit_reinit_none"))
            return
        self._undo_module("auto_fit")
        self._refresh_fit_panel()

    # ========== File IO ==========
    def _open_start_dir(self) -> str:
        # The 📂 quick-folder setting anchors the Open dialog too (v2.16.18 —
        # it used to feed only the Quick .prj combo, so Open always fell
        # through to Rawdatabase/); then the folder last actually opened from.
        for key in ("quick_prj_dir", "last_open_dir"):
            d = str(self.store.value(key, "") or "")
            if d and Path(d).is_dir():
                return d
        for cand in (
            self.paths["root"] / "Rawdatabase",
            self.paths.get("data"),
            self.paths["root"],
        ):
            if cand and Path(cand).is_dir():
                return str(cand)
        return str(Path.home())

    def _quick_prj_dirs(self) -> list:
        """Directories scanned for quick .prj.

        Once the user picks a folder (📂 button) it becomes the ONLY scan
        folder — otherwise old .prj files under Rawdatabase/ + data/ + root
        keep showing up in the dropdown as "past records" alongside the
        chosen folder's files.  The defaults below are only the initial
        convenience set before any folder has been chosen.
        """
        user_dir = str(self.store.value("quick_prj_dir", ""))
        if user_dir and Path(user_dir).is_dir():
            return [Path(user_dir)]
        dirs = [self.paths["root"] / "Rawdatabase", self.paths.get("data"), self.paths["root"]]
        return [d for d in dirs if d and Path(d).is_dir()]

    def _choose_quick_prj_dir(self):
        d = _pick_directory(
            self,
            self._bi("选择快速 prj 目录", "Choose Quick .prj folder"),
            str(self.store.value("quick_prj_dir", "") or self._open_start_dir()),
        )
        if not d:
            return
        self.store.setValue("quick_prj_dir", d)
        self._log(self._bi(f"快速 prj 目录 → {d}", f"Quick .prj folder → {d}"))
        self._rebuild_quick_prj_buttons()

    def _rebuild_quick_prj_buttons(self):
        """Fill the Quick .prj combo with .prj found under the quick dirs."""
        if not hasattr(self, "cmb_quick_prj"):
            return
        found = []
        for folder in self._quick_prj_dirs():
            for p in sorted(Path(folder).glob("*.prj")):
                if p.resolve() not in [x.resolve() for x in found]:
                    found.append(p)
        self.cmb_quick_prj.blockSignals(True)
        self.cmb_quick_prj.clear()
        self.cmb_quick_prj.addItem(self.i18n.t("quick_prj") + "…", None)
        if not found:
            self.cmb_quick_prj.addItem(self.i18n.t("quick_prj_empty"), None)
        names = [p.name for p in found[:12]]
        for p in found[:12]:
            # disambiguate same-name files from different folders (Rawdatabase/ vs data/)
            label = f"{p.name}  ({p.parent.name})" if names.count(p.name) > 1 else p.name
            self.cmb_quick_prj.addItem(label, str(p))
        self.cmb_quick_prj.setCurrentIndex(0)
        # tooltip: where we are scanning
        dirs_tip = "\n".join(str(d) for d in self._quick_prj_dirs())
        self.cmb_quick_prj.setToolTip(dirs_tip)
        self.cmb_quick_prj.blockSignals(False)

    def _quick_prj_activated(self, idx: int):
        path = self.cmb_quick_prj.itemData(idx)
        # reset so picking the same entry again re-triggers
        self.cmb_quick_prj.blockSignals(True)
        self.cmb_quick_prj.setCurrentIndex(0)
        self.cmb_quick_prj.blockSignals(False)
        if path:
            self._open_prj_path(str(path))

    def _progress_begin(
        self,
        title: str,
        maximum: int = 0,
        *,
        window_title: Optional[str] = None,
        sliding: bool = False,
    ) -> QProgressDialog:
        """Show a modal progress dialog (0 = busy/indeterminate).

        *sliding* (v2.18.1, data loading): the bar stays indeterminate for
        the dialog's whole life — counts go into the label text instead
        ("file 3/14", "curve 2/14").  A determinate bar that sat at 100 %
        while the plot was still being built read as "done but frozen".
        """
        if sliding:
            maximum = 0
        dlg = QProgressDialog(title, self.i18n.t("progress_cancel"), 0, max(0, int(maximum)), self)
        dlg.setProperty("sliding", bool(sliding))
        dlg.setWindowTitle(window_title or self.i18n.t("progress_load_title"))
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(420)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        dlg.show()
        QApplication.processEvents()
        self._load_progress = dlg
        return dlg

    def _progress_update(
        self,
        value: Optional[int] = None,
        label: str = "",
        maximum: Optional[int] = None,
        *,
        log: bool = False,
    ) -> bool:
        """Update progress. Returns False if user cancelled.

        *log*: also append *label* to the log terminal (use sparingly for milestones).
        """
        dlg = getattr(self, "_load_progress", None)
        if dlg is None:
            if log and label:
                self._log(label)
            return True
        sliding = bool(dlg.property("sliding"))
        if maximum is not None and not sliding:
            dlg.setMaximum(int(maximum))
        if label:
            dlg.setLabelText(label)
            if log:
                self._log(label)
        if value is not None:
            # Indeterminate dialog (max==0) rejects setValue; keep busy until max is set
            if dlg.maximum() > 0:
                dlg.setValue(min(int(value), dlg.maximum()))
        QApplication.processEvents()
        return not dlg.wasCanceled()

    def _progress_end(self) -> None:
        dlg = getattr(self, "_load_progress", None)
        self._load_progress = None
        if dlg is not None:
            dlg.reset()
            dlg.close()
            dlg.deleteLater()
        QApplication.processEvents()

    def _run_with_busy(self, fn, name: str = "", text: str = ""):
        """Run a blocking read on a worker thread while the modal busy dialog
        keeps animating. The GUI thread only pumps events (no larch/Qt work),
        so the non-thread-safe larch calls inside *fn* run alone.

        While *fn* runs the bar switches to the indeterminate (sliding)
        style with "Reading <name> … N s" (v2.18.0): a determinate bar
        parked at "file 3/12" during a long read looked frozen.  The
        previous range/value are restored afterwards.
        """
        import threading

        box: list = []

        def work():
            try:
                box.append(fn())
            except BaseException as exc:
                box.append(exc)

        dlg = getattr(self, "_load_progress", None)
        saved = None
        if dlg is not None:
            saved = (dlg.maximum(), dlg.value(), dlg.labelText())
            dlg.setRange(0, 0)
        pos = getattr(self, "_batch_pos", None)
        if text:
            busy_text = text
        elif pos:
            busy_text = self.i18n.t("progress_reading_pos", name=name or "…", i=pos[0], n=pos[1])
        else:
            busy_text = self.i18n.t("progress_reading_short", name=name or "…")
        t0 = time.monotonic()
        last_sec = -1
        th = threading.Thread(target=work, daemon=True)
        th.start()
        try:
            while th.is_alive():
                sec = int(time.monotonic() - t0)
                if dlg is not None and sec != last_sec:
                    last_sec = sec
                    dlg.setLabelText(
                        self.i18n.t("progress_busy_elapsed", text=busy_text, sec=sec)
                    )
                QApplication.processEvents()
                th.join(0.03)
        finally:
            if dlg is not None and saved is not None:
                mx, val, label = saved
                dlg.setRange(0, mx)
                if mx > 0:
                    dlg.setValue(max(0, min(val, mx)))
                dlg.setLabelText(label)
        res = box[0] if box else RuntimeError("read worker died")
        if isinstance(res, BaseException):
            raise res
        return res

    def _open_prj_path(self, path: str):
        """Open a known .prj path (quick combo / drop / dialog result)."""
        self._log(f"Opening: {path}")
        QApplication.processEvents()
        n_before = len(self.project.groups)
        # One sliding dialog from the first byte read until the curves are
        # on screen (_finish_open closes it) — v2.18.1.
        self._progress_begin(
            self.i18n.t("progress_opening", name=Path(path).name), sliding=True
        )
        try:
            ok = self._import_path(path, replace=True, use_outer_progress=True)
        except BaseException:
            self._progress_end()
            raise
        self._finish_open(n_before, had_error=not ok)

    def _open_files(self, append: bool = False):
        # Non-native dialog by default: the Windows native dialog hangs or opens
        # behind the window on OneDrive paths / multi-monitor setups, which reads
        # as "prj won't open". The Qt dialog is in-process and always reliable.
        dlg = QFileDialog(self, self._bi("打开 XAFS 数据", "Open XAFS data"), self._open_start_dir())
        dlg.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dlg.setNameFilters(
            [
                "XAFS / Athena (*.prj *.xdi *.csv *.dat *.txt)",
                "Athena project (*.prj)",
                "All (*.*)",
            ]
        )
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if not dlg.exec():
            self._log(self.i18n.t("open_cancelled"))
            return
        paths = dlg.selectedFiles()
        if not paths:
            return
        try:
            self.store.setValue("last_open_dir", str(Path(paths[0]).parent))
        except Exception:
            pass
        n_before = len(self.project.groups)
        had_error = False
        n_files = len(paths)
        # Batch context: import dialog picks which files (checkboxes) + shared column spec
        self._import_batch = {
            "files": list(paths),
            "spec": None,
            "selected": None,  # set after dialog: set of normalized paths
            "cancelled": False,
            "errors": [],
        }
        batch = self._import_batch
        replace_pending = not append
        self._progress_begin(self.i18n.t("progress_loading_files", n=n_files), sliding=True)
        try:
            for i, p in enumerate(paths):
                if self._import_batch.get("cancelled"):
                    self._log(self.i18n.t("open_cancelled"))
                    break
                self._set_batch_pos(i + 1, n_files)
                if not self._progress_update(
                    i,
                    self.i18n.t("progress_file", i=i + 1, n=n_files, name=Path(p).name),
                ):
                    self._log(self.i18n.t("progress_cancelled"))
                    break
                # After dialog: only import files the user checked
                if self._batch_should_skip(p):
                    continue
                project_before = self.project
                ok = self._import_path(
                    p,
                    replace=replace_pending,
                    use_outer_progress=True,
                )
                # A skipped raw file leaves the project object untouched, so
                # the next selected file must still perform the replacement.
                if ok and self.project is not project_before:
                    replace_pending = False
                had_error = (not ok) or had_error
        except BaseException:
            self._progress_end()
            raise
        finally:
            self._import_batch = None
            self._batch_pos = None
        # the sliding dialog stays up through processing + drawing
        self._finish_open(n_before, had_error=had_error)
        self._show_import_error_summary(batch.get("errors", []))
        self._rebuild_quick_prj_buttons()

    def _finish_open(self, n_before: int = 0, had_error: bool = False):
        self._refresh_list()
        run_batch = bool(getattr(self, "_batch_requested", False))
        self._batch_requested = False
        try:
            if self.project.current:
                self._load_group_to_controls(self.project.current)
                self._update_meta()
            if run_batch:
                # Auto-process after import (v3.19.5): the rules kernel runs
                # once over everything that was just imported, inside this
                # same sliding dialog, BEFORE the first draw.
                fresh = [
                    g for g in self.project.groups
                    if getattr(g, "session", None) is None
                    and not getattr(g, "is_derived", False)
                ]
                try:
                    self._run_batch_pipeline(fresh)
                except Exception as exc:
                    self._log(f"batch pipeline failed: {exc}")
                    traceback.print_exc()
            if self.project.current:
                # Fresh data context: autoscale instead of keeping the old view
                self._draw_after_load()
        finally:
            # the loading dialog closes only now — curves are on screen
            self._progress_end()
        n = len(self.project.groups)
        self._log(f"Open done — {n} group(s) in project (was {n_before})")
        if n == 0:
            # skip the generic hint when a specific error dialog was already shown
            if not had_error:
                QMessageBox.warning(self, self.i18n.t("open"), self.i18n.t("no_groups_loaded"))
        else:
            self._status(f"Loaded {n} group(s)", 10000)
            self.raise_()
            self.activateWindow()

    # ============== Import -> batch pipeline (v3.19.5, plan T3) ==============
    def _batch_pipeline_enabled(self, spec=None) -> bool:
        """Is auto-process-after-import ON for this import?"""
        if isinstance(spec, dict) and "auto_batch" in spec:
            return bool(spec.get("auto_batch"))
        return str(self.store.value("import/auto_batch", "0")).strip().lower() in (
            "1", "true", "yes", "on",
        )

    def _spec_for_path(self, path) -> dict:
        """Import spec that produced *path* ({} when it was not a raw import)."""
        specs = getattr(self, "_import_specs", None) or {}
        try:
            return dict(specs.get(self._norm_import_path(path)) or {})
        except Exception:
            return {}

    @staticmethod
    def _channel_specs(spec: dict) -> tuple:
        """(trans, fluo, ref) specs derived from one raw-import spec."""
        base = dict(spec or {})
        i0 = base.get("i0_col")
        it = base.get("it_col")
        ref = base.get("ref_col")
        fl = base.get("fl_col")
        mode = str(base.get("mode") or "")
        trans = None
        if mode in ("trans", "fluo") and i0 is not None and it is not None:
            trans = {**base, "mode": "trans", "ln": True}
        fluo = None
        if fl is not None:
            fluo = {**base, "mode": "fluo", "it_col": int(fl), "ln": False}
        elif mode == "fluo" and it is not None:
            fluo = {**base, "mode": "fluo", "it_col": int(it), "ln": False}
        refspec = None
        if ref is not None and it is not None:
            refspec = {**base, "mode": "ref", "ref_col": int(ref), "it_col": int(it), "ln": True}
        return trans, fluo, refspec

    def _auto_ref_spec(self, path, spec: dict):
        """Reference-channel spec discovered from the file header.

        The import dialog only carries a ref_col when the user ticked "also
        import reference"; with auto-batch on, calibrating every scan on its
        own foil is the whole point, so look for an Iref / I2 column directly.
        Returns None when the file has none (benchtop mu-column files).
        """
        base = dict(spec or {})
        it = base.get("it_col")
        if it is None or str(base.get("mode") or "") not in ("trans", "fluo"):
            return None
        try:
            labels, _n, _s = sniff_raw_columns(str(path))
        except Exception:
            return None
        for i, label in enumerate(labels):
            low = str(label or "").lower()
            if i == int(it):
                continue
            if "iref" in low or "reference" in low or low.startswith("i2"):
                return {**base, "mode": "ref", "ref_col": int(i), "it_col": int(it), "ln": True}
        return None

    def _auto_fluo_spec(self, path, spec: dict):
        """Fluorescence-channel spec discovered from the file header.

        A transmission import carries no fl_col unless the user picked the
        "Transmission + Fluorescence" mode, yet the channel decision (plan T1
        choose_channel) needs both channels.  MEX1 XDI name the summed
        detector ``ifluor``; per-element ``ifluor_indv_*`` columns are skipped.
        Returns None when the file has no fluorescence column.
        """
        base = dict(spec or {})
        if str(base.get("mode") or "") != "trans" or base.get("i0_col") is None:
            return None
        try:
            labels, _n, _s = sniff_raw_columns(str(path))
        except Exception:
            return None
        used = {base.get("i0_col"), base.get("it_col"), base.get("ref_col")}
        for i, label in enumerate(labels):
            low = str(label or "").lower()
            if i in used or "indv" in low or low.startswith("mu"):
                continue
            if low.startswith("ifluor") or low.startswith("ifl") or low.startswith("fluor"):
                return {**base, "mode": "fluo", "it_col": int(i), "mu_col": -1,
                        "ln": False, "fl_col": None, "ref_col": None}
        return None

    @staticmethod
    def _scan_tag(name: str) -> str:
        """Short source label for the record: the trailing scan number."""
        tail = str(name).rsplit("_", 1)[-1]
        return tail if tail.isdigit() else str(name)

    def _run_batch_pipeline(self, groups) -> None:
        """Run the batch with panel write-back suspended (see the inner body)."""
        self._panel_writeback_suspended = True
        try:
            self._run_batch_pipeline_inner(groups)
        finally:
            self._panel_writeback_suspended = False
            # the panel no longer describes any group's pending edits
            self._controls_group_id = None
            cur = self.project.current
            if cur is not None:
                self._load_group_to_controls(cur)
                self._update_meta()

    def _run_batch_pipeline_inner(self, groups) -> None:
        """One rule-driven pass over the freshly imported groups (plan T3).

        Fixed order: quality -> channel -> per-scan calibration -> repeat
        consistency + merge -> rebin -> Auto norm -> uniform FT -> self-check
        -> record.  Nothing is exported and nothing is deleted: bad scans and
        superseded source curves are only UNMARKED (one-undo reversible), and
        the whole run is a single undo entry (module "batch").
        """
        targets = [
            g for g in (groups or [])
            if g is not None and not getattr(g, "is_derived", False)
        ]
        if not targets:
            return
        record = pipeline_rules.ProcessingRecord(
            title=self._bi(
                f"smartLarch Platform 导入即批处理（{len(targets)} 个数据组）",
                f"smartLarch Platform import batch pipeline ({len(targets)} groups)",
            )
        )
        self._push_module_undo("batch", targets)
        stack = self._module_undo.get("batch") or []
        entry = stack[-1] if stack else None
        created: list = []
        # record bookkeeping (acceptance fix): where each curve came from and
        # which calibration evidence its energy axis carries
        prov: dict = {g.id: self._scan_tag(g.name) for g in targets}
        calib: dict = {}      # group id -> (dE, basis text)
        actions: dict = {}    # group id -> list of steps applied
        set_notes: list = []  # run-level header lines for Notes
        parent: dict = {}     # derived group id -> one source group id

        def _set_targets_on_entry() -> None:
            if entry is not None:
                entry["added_ids"] = list(created)

        n_total = len(targets)
        # ---- 1. bad-scan screen (raw detector medians) ----
        stats: dict = {}
        for i, g in enumerate(targets):
            if not self._progress_update(None, self._bi(
                f"批处理 · 质量检查 {i + 1}/{n_total}：{g.name}",
                f"Batch - quality check {i + 1}/{n_total}: {g.name}",
            )):
                return
            spec = self._spec_for_path(getattr(g, "path", ""))
            if not spec or str(spec.get("mode") or "") == "mu":
                continue
            try:
                stats[g.id] = self._run_with_busy(
                    lambda s=spec, path=g.path: pipeline_rules.raw_channel_medians(
                        path, s, label=str(path)
                    ),
                    Path(g.path).name,
                    text=self._bi(f"读取原始列：{g.name}", f"Reading raw columns: {g.name}"),
                )
            except Exception as exc:
                self._log(f"batch: raw-column read failed [{g.name}]: {exc}")
        bad_ids: set = set()
        for g in targets:
            st = stats.get(g.id)
            if not st:
                continue
            sibs = [
                s for gid, s in stats.items()
                if gid != g.id
                and str((self._find_group_by_id(gid) or g).element or "") == str(g.element or "")
                and str((self._find_group_by_id(gid) or g).edge or "") == str(g.edge or "")
            ]
            q = pipeline_rules.scan_quality(st, sibs)
            if q.get("ok"):
                continue
            bad_ids.add(g.id)
            g.marked = False
            g.note = str(q.get("reason_en") or "")
            record.exclude(g.name, str(q.get("reason_en") or ""))
            self._log(self._bi(
                f"⚠ {g.name} 判为坏扫描（已取消勾选，未删除）：{q.get('reason_zh')}",
                f"⚠ {g.name} looks bad (unmarked, NOT deleted): {q.get('reason_en')}",
            ))
        good = [g for g in targets if g.id not in bad_ids]
        # ---- 2. measurement channel (one per element/edge set) ----
        channel_by_id: dict = {}
        channel_by_set: dict = {}  # (element, edge) -> (channel, reason); merged/
        #                             rebinned curves inherit their set's decision
        sets: dict = {}
        for g in good:
            sets.setdefault((str(g.element or ""), str(g.edge or "")), []).append(g)
        for (el, edge), members in sets.items():
            decisions, labels = [], []
            for g in members:
                spec = self._spec_for_path(getattr(g, "path", ""))
                trans_spec, fluo_spec, _ref = self._channel_specs(spec)
                if trans_spec and not fluo_spec:
                    fluo_spec = self._auto_fluo_spec(getattr(g, "path", ""), spec)
                if not (trans_spec and fluo_spec):
                    continue
                tab = edge_table_energy(el, edge)
                if tab is None:
                    continue
                if not self._progress_update(None, self._bi(
                    f"批处理 · 选通道：{g.name}",
                    f"Batch - measurement channel: {g.name}",
                )):
                    return
                try:
                    e_t, mu_t = self._run_with_busy(
                        lambda s=trans_spec, path=g.path: load_energy_mu_with_spec(path, s)[:2],
                        Path(g.path).name,
                        text=self._bi(f"读取透射通道：{g.name}", f"Reading transmission: {g.name}"),
                    )
                    e_f, mu_f = self._run_with_busy(
                        lambda s=fluo_spec, path=g.path: load_energy_mu_with_spec(path, s)[:2],
                        Path(g.path).name,
                        text=self._bi(f"读取荧光通道：{g.name}", f"Reading fluorescence: {g.name}"),
                    )
                except Exception as exc:
                    self._log(f"batch: channel read failed [{g.name}]: {exc}")
                    continue
                decisions.append(pipeline_rules.choose_channel(e_t, mu_t, mu_f, tab))
                labels.append(g.name)
            if not decisions:
                for g in members:
                    spec = self._spec_for_path(getattr(g, "path", ""))
                    if not spec or str(spec.get("mode") or "") == "mu":
                        channel_by_id[g.id] = ("μ", self._bi("文件自带 μ 列（无通道判断）", "ready μ column (no channel test)"))
                    elif str(spec.get("mode") or "") == "fluo":
                        channel_by_id[g.id] = ("fluo", self._bi("导入时选荧光（文件无透射列可比）", "fluorescence chosen at import (no transmission to compare)"))
                    else:
                        channel_by_id[g.id] = ("trans", self._bi("透射（文件无荧光列可比）", "transmission (no fluorescence column to compare)"))
                continue
            chosen = pipeline_rules.choose_set_channel(decisions, labels=labels)
            word = str(chosen.get("value") or "trans")
            wr = [d.get("white_ratio") for d in decisions if d.get("white_ratio") is not None]
            nr = [d.get("noise_ratio") for d in decisions if d.get("noise_ratio") is not None]
            numbers = ""
            if wr:
                numbers += f"fluo/trans white line {min(wr):.3f}–{max(wr):.3f}"
            if nr:
                numbers += (", " if numbers else "") + f"fluo/trans high-k noise {min(nr):.1f}–{max(nr):.1f}×"
            note = str(chosen.get("reason_en") or "") + (f" [{numbers}]" if numbers else "")
            set_notes.append(f"Channel {el} {edge}: {word} — {note}")
            self._log(f"batch: {el} {edge} channel -> {word} ({note})")
            channel_by_set[(el, edge)] = (word, note)
            for g in members:
                channel_by_id[g.id] = (word, note)
                if word != "fluo":
                    continue
                # keep the fluorescence curve, unmark the transmission one
                stem = str(g.name)[:-len("_trans")] if str(g.name).endswith("_trans") else str(g.name)
                twin = next((x for x in self.project.groups if x.name == stem + "_fluo"), None)
                if twin is None:
                    spec = self._spec_for_path(getattr(g, "path", ""))
                    fspec = self._channel_specs(spec)[1] or self._auto_fluo_spec(getattr(g, "path", ""), spec)
                    if fspec is not None:
                        try:
                            e_f, mu_f = load_energy_mu_with_spec(g.path, fspec)[:2]
                            twin = self.project.add_group(stem + "_fluo", e_f, mu_f, path=g.path, after_id=g.id)
                            twin.element, twin.edge = g.element, g.edge
                            prov[twin.id] = prov.get(g.id, self._scan_tag(g.name)) + " (fluo)"
                            good.append(twin)
                        except Exception as exc:
                            self._log(f"batch: fluorescence curve not built [{g.name}]: {exc}")
                            twin = None
                if twin is not None and twin is not g:
                    twin.marked = True
                    g.marked = False
                    channel_by_id[twin.id] = (word, note)
        # ---- 3. per-scan energy calibration on the reference channel ----
        for i, g in enumerate(good):
            if not g.marked:
                continue
            spec = self._spec_for_path(getattr(g, "path", ""))
            _trans, _fluo, ref_spec = self._channel_specs(spec)
            if not ref_spec and spec:
                ref_spec = self._auto_ref_spec(getattr(g, "path", ""), spec)
            if not ref_spec:
                continue
            if not self._progress_update(None, self._bi(
                f"批处理 · 能量校准 {i + 1}/{len(good)}：{g.name}",
                f"Batch - calibration {i + 1}/{len(good)}: {g.name}",
            )):
                return
            try:
                e_r, mu_r = self._run_with_busy(
                    lambda s=ref_spec, path=g.path: load_energy_mu_with_spec(path, s)[:2],
                    Path(g.path).name,
                    text=self._bi(f"读取参考通道：{g.name}", f"Reading reference channel: {g.name}"),
                )
            except Exception as exc:
                self._log(f"batch: reference read failed [{g.name}]: {exc}")
                continue
            shift = pipeline_rules.reference_shift(e_r, mu_r, g.element, g.edge)
            if not shift.get("ok") or shift.get("value") is None:
                self._log(f"batch: no calibration for {g.name} ({shift.get('reason_en')})")
                continue
            g.energy_shift = round(float(shift["value"]), 3)
            calib[g.id] = (g.energy_shift, "reference channel " + str(shift.get("reason_en") or ""))
            tab = edge_table_energy(g.element, g.edge)
            if tab is not None and "foil" in str(g.name).lower():
                g.settings.e0 = float(tab)  # foil E0 pinned at the table value
            self._log(
                f"batch: {g.name} calibrated on its reference foil -> dE "
                f"{g.energy_shift:+.2f} eV"
            )
        # ---- 3b. benchtop (ready-mu) files carry no reference channel: take
        # ONE dE per element from that element's own foil in this batch ----
        by_element: dict = {}
        for g in good:
            if g.marked:
                by_element.setdefault(str(g.element or ""), []).append(g)
        for el, members in by_element.items():
            foils = [g for g in members if "foil" in str(g.name).lower()]
            if not foils:
                continue
            foil = foils[0]
            if abs(float(foil.energy_shift or 0.0)) > 1e-9:
                continue  # already calibrated on its own reference channel
            shift = pipeline_rules.reference_shift(
                foil.energy, foil.mu, foil.element, foil.edge
            )
            if not shift.get("ok") or shift.get("value") is None:
                continue
            delta = round(float(shift["value"]), 3)
            edge = str(foil.edge or "K")
            for g in members:
                if str(g.edge or "K") != edge:
                    continue
                g.energy_shift = delta
                calib[g.id] = (delta, f"from {foil.name}: " + str(shift.get("reason_en") or ""))
            tab = edge_table_energy(foil.element, foil.edge)
            if tab is not None:
                foil.settings.e0 = float(tab)
            self._log(
                f"batch: {el} {edge} dE {delta:+.2f} eV taken from {foil.name} "
                f"(first inflection) and applied to {len(members)} scan(s)"
            )
            set_notes.append(
                f"Calibration {el} {edge}: one ΔE {delta:+.2f} eV for all scans, from {foil.name} "
                f"({shift.get('reason_en')}); foil E0 pinned at the table value"
            )
        # ---- 4. repeat consistency, then merge what agrees ----
        working = [g for g in good if g.marked]
        self._set_batch_pos(1, 1)
        if len(working) >= 2:
            if not self._progress_update(None, self._bi(
                "批处理 · 检查重复扫描一致性",
                "Batch - checking repeat consistency",
            )):
                return
            suggestion = self._merge_suggestion(working)
            plan = []
            by_id = {g.id: g for g in working}
            for sg in suggestion:
                members = [by_id[i] for i in sg["ids"] if i in by_id]
                if len(members) < 2:
                    continue
                rep = pipeline_rules.group_repeat_consistency(members)
                if rep.get("ok"):
                    plan.append((sg["label"], members))
                    self._log(
                        f"batch: merge {sg['label']} <- {len(members)} scans "
                        f"(repeat rms {rep.get('value'):.4f})"
                    )
                else:
                    for m in members:
                        m.note = f"repeats differ: {rep.get('reason_en')}"
                    record.exclude(
                        sg["label"],
                        f"not merged: {rep.get('reason_en')}",
                    )
                    self._log(
                        f"batch: NOT merging {sg['label']} - {rep.get('reason_en')}"
                    )
            if plan:
                made = self._merge_by_plan(plan, keep_originals=True, use_norm=False)
                created.extend(m.id for m in made)
                _set_targets_on_entry()
                by_label = {lab: mem for lab, mem in plan}
                for ng in made:
                    label = str(ng.name)[: -len("_merged")] if str(ng.name).endswith("_merged") else str(ng.name)
                    mem = by_label.get(label) or []
                    prov[ng.id] = " + ".join(prov.get(m.id, self._scan_tag(m.name)) for m in mem)
                    shifts = sorted({round(calib[m.id][0], 2) for m in mem if m.id in calib})
                    if shifts:
                        calib[ng.id] = (
                            shifts[0] if len(shifts) == 1 else None,
                            "applied to each scan before merging: "
                            + ", ".join(f"{self._scan_tag(m.name)} {calib[m.id][0]:+.2f}" for m in mem if m.id in calib),
                        )
                    if mem:
                        parent[ng.id] = mem[0].id
                    actions[ng.id] = [f"merged {len(mem)} scans (repeat rms ok)"]
                    set_notes.append(f"Merged {ng.name} = {prov[ng.id]}")
        # ---- 5. rebin the dense slew scans (original curve kept) ----
        final: list = []
        # Only THIS run's curves: on an append-import the project may already
        # hold marked curves from an earlier session, and they must not be
        # rebinned/normalized again by this batch.
        fresh_ids = {g.id for g in good} | set(created)
        for i, g in enumerate(
            [x for x in self.project.groups if x.marked and x.id in fresh_ids]
        ):
            if not self._progress_update(None, self._bi(
                f"批处理 · rebin 检查：{g.name}",
                f"Batch - rebin check: {g.name}",
            )):
                return
            need = pipeline_rules.needs_rebin(g.energy)
            if not need.get("ok") or not need.get("value"):
                final.append(g)
                continue
            e = np.asarray(g.energy, dtype=float) + float(g.energy_shift)
            r = g.process(force=True)
            if not r.ok or g.session is None or g.session.arrays is None:
                final.append(g)
                continue
            kw = self._rebin_kwargs_for_group(g)
            kw["e0"] = float(g.session.arrays.e0)
            rb = self._run_with_busy(
                lambda: rebin_group(e, np.asarray(g.mu, dtype=float), **kw),
                g.name,
                text=self._bi(f"rebin：{g.name}", f"Rebinning: {g.name}"),
            )
            if not rb.ok:
                self._log(f"batch: rebin failed [{g.name}]: {rb.message}")
                final.append(g)
                continue
            e2, m2 = rb.data
            shift = float(g.energy_shift)
            ng = self.project.add_group(
                f"{g.name}_rebin", np.asarray(e2, dtype=float) - shift, m2,
                path=g.path, after_id=g.id, is_derived=True,
            )
            ng.element, ng.edge = g.element, g.edge
            ng.datatype = g.datatype
            ng.importance = g.importance
            ng.settings = deepcopy(g.settings)
            ng.energy_shift = shift
            ng.marked = True
            g.marked = False
            ng.note = f"rebinned from {g.name}: {len(e)} -> {len(e2)} points"
            prov[ng.id] = prov.get(g.id, self._scan_tag(g.name))
            parent[ng.id] = g.id
            if g.id in calib:
                calib[ng.id] = calib[g.id]
            actions[ng.id] = list(actions.get(g.id, [])) + [f"rebinned {len(e)} → {len(e2)} points"]
            created.append(ng.id)
            _set_targets_on_entry()
            final.append(ng)
        # ---- 6. one Auto norm pass (self-check + retry + uniform FT) ----
        if final:
            if not self._progress_update(None, self._bi(
                "批处理 · 自动归一化 / 自检", "Batch - auto norm / self-check",
            )):
                return
            self._auto_normalize("marked", targets=final)
        # ---- 7. record ----
        for g in final:
            # merged / rebinned curves inherit their source scans' channel
            src = parent.get(g.id, g.id)
            while src not in channel_by_id and src in parent:
                src = parent[src]
            channel, basis = channel_by_id.get(g.id) or channel_by_id.get(src) or channel_by_set.get(
                (str(g.element or ""), str(g.edge or ""))
            ) or ("", "")
            entry_row = pipeline_rules.describe_group(g, note=" ")
            entry_row.warn = ""  # provenance notes are not warnings
            entry_row.channel = channel or "n/a"
            entry_row.channel_basis = basis
            entry_row.source = prov.get(g.id, self._scan_tag(g.name))
            steps = actions.get(g.id) or ["single scan"]
            entry_row.action = "; ".join(steps)
            if g.id in calib:
                d_e, d_basis = calib[g.id]
                if d_e is not None:
                    entry_row.dE = float(d_e)
                entry_row.dE_basis = d_basis
            else:
                entry_row.dE_basis = self._bi("未校准（无参考通道 / 同元素箔）", "not calibrated (no reference channel or foil)")
            check = pipeline_rules.normalization_check(g)
            entry_row.check = self._bi("合格", "ok") if check.get("ok") else str(check.get("reason_zh") or "")
            warns = []
            if not check.get("ok"):
                warns.append(str(check.get("reason_zh") or ""))
            if str(getattr(g, "note", "") or "").startswith("repeats differ"):
                warns.append(str(g.note))
            if g.id not in calib:
                warns.append(self._bi("未校准", "not calibrated"))
            entry_row.warn = "; ".join(w for w in warns if w)
            record.add(entry_row)
        ft_sets: dict = {}
        for g in final:
            ft_sets.setdefault((g.element, g.edge), set()).add(
                f"{g.settings.kmin:.1f}–{g.settings.kmax:.1f}"
            )
        for (el, edge), wins in ft_sets.items():
            set_notes.append(f"FT k window {el} {edge}: {', '.join(sorted(wins))} Å⁻¹")
        record.notes = list(set_notes)  # to_lines() prints the title itself
        self.project.processing_record = record
        self._refresh_list()
        self._replot(preserve_view=False)
        self._log(
            f"batch pipeline done: {len(record.entries)} delivered group(s), "
            f"{len(record.excluded)} excluded/not-merged"
        )
        self._show_batch_result_dialog(record)

    def _show_batch_result_dialog(self, record) -> None:
        """Non-blocking result table (warnings first) - review, then export."""
        t = self.i18n.t
        dlg = QDialog(self)
        dlg.setWindowTitle(t("batch_result_title"))
        dlg.setMinimumSize(720, 400)
        dlg.resize(900, 520)
        dlg.setSizeGripEnabled(True)
        lay = QVBoxLayout(dlg)
        hint = QLabel(t("batch_result_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b625d; font-size:11px;")
        lay.addWidget(hint)
        rows = record.to_rows()
        columns = [
            ("group", t("batch_col_group")),
            ("source", t("batch_col_source")),
            ("channel", t("batch_col_channel")),
            ("dE_eV", "dE"),
            ("E0_eV", "E0"),
            ("pre_eV", "pre"),
            ("norm_eV", "norm"),
            ("FT_k", "FT k"),
            ("check", t("batch_col_check")),
            ("warn", "⚠"),
        ]
        tbl = QTableWidget(len(rows), len(columns))
        tbl.setHorizontalHeaderLabels([label for _key, label in columns])
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.verticalHeader().setVisible(False)
        for r, row in enumerate(rows):
            for c, (key, _label) in enumerate(columns):
                value = row.get(key)
                text = "" if value is None else (
                    f"{value:+.2f}" if key == "dE_eV" else str(value)
                )
                item = QTableWidgetItem(text)
                if row.get("warn"):
                    item.setForeground(QBrush(Qt.GlobalColor.darkYellow))
                tbl.setItem(r, c, item)
        hdr = tbl.horizontalHeader()
        for c in range(len(columns) - 2):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(len(columns) - 2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(len(columns) - 1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(tbl, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_undo = self._register_undo_btn("batch")
        btn_undo.setParent(dlg)
        btn_undo.setEnabled(bool(self._module_undo.get("batch")))
        btn_row.addWidget(btn_undo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.button(QDialogButtonBox.StandardButton.Close).setText(t("close"))
        btn_row.addWidget(bb)
        lay.addLayout(btn_row)
        dlg.show()
        self._batch_result_dialog = dlg

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p:
                paths.append(p)
        if not paths:
            return
        self._log(f"Drop: {len(paths)} file(s)")
        n_before = len(self.project.groups)
        had_error = False
        n_files = len(paths)
        # Same batch context as Open… — checkboxes choose which files to import
        self._import_batch = {
            "files": list(paths),
            "spec": None,
            "selected": None,
            "cancelled": False,
            "errors": [],
        }
        batch = self._import_batch
        self._progress_begin(self.i18n.t("progress_loading_files", n=n_files), sliding=True)
        try:
            for i, p in enumerate(paths):
                if self._import_batch.get("cancelled"):
                    self._log(self.i18n.t("open_cancelled"))
                    break
                if self._batch_should_skip(p):
                    continue
                self._set_batch_pos(i + 1, n_files)
                if not self._progress_update(
                    i,
                    self.i18n.t("progress_file", i=i + 1, n=n_files, name=Path(p).name),
                ):
                    self._log(self.i18n.t("progress_cancelled"))
                    break
                had_error = (
                    not self._import_path(
                        p,
                        replace=(i == 0 and p.lower().endswith(".prj")),
                        use_outer_progress=True,
                    )
                ) or had_error
        except BaseException:
            self._progress_end()
            raise
        finally:
            self._import_batch = None
            self._batch_pos = None
        self._finish_open(n_before, had_error=had_error)
        self._show_import_error_summary(batch.get("errors", []))
        event.acceptProposedAction()

    def _set_batch_pos(self, i: int, n: int) -> None:
        """Batch position shown in the busy reading text ("3/14")."""
        self._batch_pos = (int(i), int(n)) if n > 1 else None

    def _draw_after_load(self) -> None:
        """Process + draw the freshly loaded view while the loading dialog
        keeps sliding (v2.18.1).

        After the list refreshed, the old path ran larch on every plotted
        curve and rendered the 2×2 figure on the GUI thread with no dialog:
        seconds of "loaded but frozen" on 6320-point MEX1 slew scans.
        Now each curve that still needs processing runs on the busy worker
        ("Processing curve i/N: name"), the artists are built on the GUI
        thread, the Agg render (the slow part) runs on the worker too, and
        the dialog closes after the canvas has painted.
        """
        if getattr(self, "_load_progress", None) is None:
            self._progress_begin(self.i18n.t("progress_drawing_prep"), sliding=True)
        todo = [
            g for g in self._groups_to_plot()
            if g.energy is not None and g.mu is not None and not g.frozen
            and (
                g.session is None or g.session.arrays is None
                or g.session.arrays.energy is None
            )
        ]
        for i, g in enumerate(todo):
            self._run_with_busy(
                g.process,
                text=self.i18n.t("progress_processing", i=i + 1, n=len(todo), name=g.name),
            )
        draw_text = self.i18n.t("progress_drawing", n=len(self._groups_to_plot()))
        self._progress_update(None, draw_text)
        self._replot(preserve_view=False)
        if getattr(self.canvas, "mode", "") == "wt":
            return
        self._render_canvas_busy(draw_text)

    def _render_canvas_busy(self, text: str) -> None:
        """Render the figure on the busy worker, then paint it.

        Only the Agg rasterisation runs off the GUI thread.  Meanwhile the
        canvas is flagged ``_is_drawing`` so no GUI-thread draw_idle/resize
        renders the same figure concurrently, and the queued draw_idle is
        cancelled (``_draw_pending = False``) so it is not rendered twice.
        Any failure falls back to a normal draw.
        """
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        canvas = self.canvas
        toolbar = getattr(canvas, "toolbar", None)
        try:
            canvas._draw_pending = False
            canvas._is_drawing = True
            canvas.toolbar = None  # its wait-cursor hook is Qt → GUI thread only
            self._run_with_busy(lambda: FigureCanvasAgg.draw(canvas), text=text)
        except Exception as exc:
            self._log(f"background render failed, drawing normally: {exc}")
            canvas._is_drawing = False
            canvas.draw()
        finally:
            canvas._is_drawing = False
            canvas.toolbar = toolbar
        canvas.update()
        QApplication.processEvents()

    def _report_import_error(self, path: str, detail: object) -> None:
        """Queue a batch failure, or show the usual single-file warning."""
        batch = getattr(self, "_import_batch", None)
        if isinstance(batch, dict):
            batch.setdefault("errors", []).append(
                {"path": str(path), "detail": str(detail)}
            )
            return
        QMessageBox.warning(
            self,
            self.i18n.t("load_failed"),
            f"{path}\n\n{detail}",
        )

    def _show_import_error_summary(self, errors) -> None:
        """Show every failed file in one scrollable dialog with one OK."""
        errors = list(errors or [])
        if not errors:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(self.i18n.t("load_failed"))
        dlg.setMinimumSize(560, 300)
        dlg.resize(720, 420)
        lay = QVBoxLayout(dlg)
        intro = QLabel(self.i18n.t("load_failed_batch", n=len(errors)))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        details = QPlainTextEdit()
        details.setObjectName("importErrorDetails")
        details.setReadOnly(True)
        blocks = []
        for index, item in enumerate(errors, start=1):
            path = str(item.get("path", "")) if isinstance(item, dict) else ""
            detail = str(item.get("detail", "")) if isinstance(item, dict) else str(item)
            blocks.append(f"{index}. {Path(path).name or path}\n{path}\n{detail}")
        details.setPlainText("\n\n".join(blocks))
        lay.addWidget(details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        dlg.exec()

    @staticmethod
    def _norm_import_path(path) -> str:
        """Normalize a path for batch selection membership checks."""
        try:
            return str(Path(path).resolve())
        except Exception:
            return str(Path(path))

    def _batch_should_skip(self, path) -> bool:
        """True when multi-import dialog already chose a selection and *path* is not in it."""
        batch = getattr(self, "_import_batch", None)
        if not isinstance(batch, dict):
            return False
        selected = batch.get("selected")
        if selected is None:
            return False  # dialog not finished yet — do not skip
        key = self._norm_import_path(path)
        if key in selected:
            return False
        self._log(self.i18n.t("imp_batch_skip", name=Path(path).name))
        return True

    def _raw_import_spec(self, p: Path):
        """Athena-style import dialog for raw text data (xdi/csv/dat/txt):
        per-column info table, role pickers, natural-log checkbox, reference
        channel, energy range, and (multi-file) checkable list on the right.
        Returns a spec dict, {} for auto mode (don't-ask),
        or None when the user cancels."""
        noask = str(self.store.value("raw_import_noask", "") or "").lower() in ("1", "true", "yes")
        if noask:
            return {}
        batch = getattr(self, "_import_batch", None)
        if batch and batch.get("spec") is not None:
            return batch["spec"]
        try:
            # column sniff reads the file — keep the progress bar alive
            labels, n_cols, sample = self._run_with_busy(
                lambda: sniff_raw_columns(str(p)), p.name
            )
        except Exception:
            return {}  # not sniffable → silent auto path
        t = self.i18n.t
        dlg = QDialog(self)
        dlg.setWindowTitle(t("imp_title"))
        # Compact default; user can grow — preview table owns extra height
        dlg.setMinimumSize(560, 420)
        dlg.resize(640, 520)
        dlg.setSizeGripEnabled(True)
        # Grid (v2.19.2): col 0 = data side, col 1 = batch file list.
        #   row 0  file name + caption        | "N in batch"  A U I
        #   row 1  column preview table       | file list ┐ (rows 1–2)
        #   row 2  column-role form           | Import / Cancel ┘ bottom = range row
        #   row 3                             | "same as Groups…" hint
        #   row 4  (spare height)
        grid = QGridLayout(dlg)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(4, 1)
        left = QVBoxLayout()
        left.setSpacing(4)
        grid.addLayout(left, 0, 0, Qt.AlignmentFlag.AlignBottom)

        name_lbl = QLabel(p.name)
        name_lbl.setStyleSheet("font-weight:600;")
        left.addWidget(name_lbl)
        prev_lbl = QLabel(t("imp_preview"))
        prev_lbl.setWordWrap(True)
        prev_lbl.setStyleSheet("color:#6b625d; font-size:11px;")
        left.addWidget(prev_lbl)

        def _short_col_title(raw: str, idx: int) -> str:
            """Front token only (Energy / μ / I0…) — full name stays on tooltip."""
            s = str(raw or "").strip()
            if not s:
                return f"#{idx + 1}"
            low = s.lower()
            if low.startswith("energy") or low.startswith("ener") or "e(ev)" in low:
                return "Energy"
            if low.startswith("angle"):
                return "Angle"
            if (
                "mu_from" in low
                or low.startswith("mutrans")
                or low.strip() in ("mu", "μ", "norm")
            ):
                return "μ"
            if low.startswith("izero") or low.startswith("i_zero") or low == "i0":
                return "I0"
            if low.startswith("it_") or low.startswith("itrans") or low == "it":
                return "It"
            if "ifluor" in low or low.startswith("ifl"):
                return "Ifl"
            if "irefer" in low or low.startswith("iref"):
                return "Iref"
            if "(" in s:
                s = s.split("(", 1)[0].strip()
            if "_" in s:
                head = s.split("_", 1)[0].strip()
                if head:
                    s = head
            # keep header short so columns stay compact
            if len(s) > 8:
                s = s[:7] + "…"
            return s or f"#{idx + 1}"

        # 2-row preview: row0 = 1-based column number, row1 = first sample value
        # Vertical: both rows STRETCH so enlarging the window grows the data cells
        # (not empty chrome under fixed-height rows).
        # Horizontal: fixed narrow columns + scroll; never stretch into a long ribbon.
        tbl = QTableWidget(2, n_cols)
        short_headers = [_short_col_title(labels[i], i) for i in range(n_cols)]
        full_headers = [str(labels[i] or f"col{i + 1}") for i in range(n_cols)]
        tbl.setHorizontalHeaderLabels(short_headers)
        tbl.setVerticalHeaderLabels([t("imp_col_index"), t("imp_col_sample")])
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tbl.setAlternatingRowColors(True)
        tbl.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for i in range(n_cols):
            # 1-based column number for humans (internal still 0-based)
            num_item = QTableWidgetItem(str(i + 1))
            num_item.setTextAlignment(
                int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            )
            num_item.setToolTip(full_headers[i])
            tbl.setItem(0, i, num_item)
            sv = f"{sample[i]:.6g}" if i < len(sample) else ""
            smp_item = QTableWidgetItem(sv)
            smp_item.setTextAlignment(
                int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            )
            smp_item.setToolTip(full_headers[i])
            tbl.setItem(1, i, smp_item)
        col_w = 72
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hdr.setDefaultSectionSize(col_w)
        hdr.setMinimumSectionSize(56)
        hdr.setStretchLastSection(False)
        for i in range(n_cols):
            tbl.setColumnWidth(i, col_w)
            item = tbl.horizontalHeaderItem(i)
            if item is not None:
                item.setToolTip(full_headers[i])
        vhdr = tbl.verticalHeader()
        # Rows keep a fixed height (v2.19.1): enlarging the window used to
        # stretch the two preview rows into tall empty cells.  The table
        # grows sideways only; its height is header + 2 rows + scrollbar.
        vhdr.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vhdr.setMinimumWidth(72)
        vhdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        row_h = max(vhdr.defaultSectionSize(), 26)
        vhdr.setDefaultSectionSize(row_h)
        tbl.setFixedHeight(
            hdr.sizeHint().height() + 2 * row_h
            + tbl.horizontalScrollBar().sizeHint().height() + 2 * tbl.frameWidth() + 2
        )
        tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(tbl, 1, 0)

        # Role pickers keep the complete source header.  Only the preview table
        # uses compact headings; its extra horizontal space is not constrained
        # like the closed combo field.
        role_labels = [f"{i + 1}  {full_headers[i]}" for i in range(n_cols)]

        def _mk(items, cur=0, tip: str = ""):
            c = QComboBox()
            c.addItems(items)
            c.setCurrentIndex(max(0, min(cur, len(items) - 1)))
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(10)
            c.setMaximumWidth(280)
            popup = c.view()
            popup.setTextElideMode(Qt.TextElideMode.ElideRight)
            c.setLabelDrawingMode(QComboBox.LabelDrawingMode.UseDelegate)
            for i, text in enumerate(items):
                c.setItemData(i, text, Qt.ItemDataRole.ToolTipRole)
            content_width = max(
                (popup.fontMetrics().horizontalAdvance(str(text)) for text in items),
                default=0,
            ) + 42
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            screen_cap = 720
            if screen is not None:
                screen_cap = max(320, screen.availableGeometry().width() - 80)
            popup_width = max(c.maximumWidth(), min(content_width, screen_cap))
            popup.setMinimumWidth(popup_width)
            popup.setMaximumWidth(popup_width)
            if tip:
                c.setToolTip(tip)
            return c

        def _find(*keys, default=0):
            for k in keys:
                kl = k.lower()
                for i, l in enumerate(labels):
                    low = (l or "").lower()
                    if kl in low:
                        return i
            return default

        def _find_mu_col(default=1):
            for i, l in enumerate(labels):
                low = (l or "").lower()
                if any(
                    k in low
                    for k in ("mutrans", "mu_from", "mu(", "mu_mean", "mu_roi")
                ) or low.strip() in ("mu", "μ", "norm"):
                    return i
            for i, l in enumerate(labels):
                low = (l or "").lower()
                if "mu" in low and "maximum" not in low and "angle" not in low:
                    return i
            return default if n_cols > default else 0

        # Modes (v2.19.2): 0 trans · 1 fluo · 2 trans+fluo · 3 ready μ column
        # (benchtop).  easyxanes CSVs ship a μ column → default "use μ";
        # otherwise the last raw mode the user picked.
        has_ready_mu = _find_mu_col(default=-1) >= 0
        try:
            last_raw = int(self.store.value("import/raw_mode", 0) or 0)
        except (TypeError, ValueError):
            last_raw = 0
        mode_default = 3 if has_ready_mu else (last_raw if last_raw in (0, 1, 2) else 0)

        form_host = QWidget()
        form_host.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(4)
        grid.addWidget(form_host, 2, 0, Qt.AlignmentFlag.AlignTop)

        cmb_e = _mk(role_labels, _find("energy", "e(ev)", "e_ev", "ener"))
        cmb_mode = _mk(
            [t("imp_mode_trans"), t("imp_mode_fluo"), t("imp_mode_both"), t("imp_mode_mu")],
            mode_default,
        )
        mode_tips = (t("imp_mode_trans_tip"), t("imp_mode_fluo_tip"),
                     t("imp_mode_both_tip"), t("imp_mode_mu_tip"))
        for i, tip in enumerate(mode_tips):
            cmb_mode.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
        cmb_i0 = _mk(
            role_labels,
            _find("izero", "i_zero", "i0", default=1 if n_cols > 1 else 0),
        )
        # It and Ifl each have their own row now (v2.19.2) — no more one
        # picker whose meaning flipped with the mode.
        it_raw = _find("itrans", "it_roi", "i1", default=-1)
        fl_raw = _find("ifluor", "ifl", default=-1)
        cmb_sig = _mk(role_labels, it_raw if it_raw >= 0 else (2 if n_cols > 2 else 0))
        cmb_fl = _mk(role_labels, fl_raw if fl_raw >= 0 else (2 if n_cols > 2 else 0))
        cmb_mu = _mk(role_labels, _find_mu_col(default=1 if n_cols > 1 else 0))
        cmb_ref = _mk(role_labels, _find("irefer", "iref", default=0))
        chk_ln = QCheckBox(t("imp_ln"))
        chk_ln.setChecked(True)
        chk_ln.setToolTip(t("imp_ln_tip"))
        chk_ref = QCheckBox(t("imp_ref"))

        # Rows = title | 280 px picker | option on the right (v2.19.1).
        # Synchrotron rows first; the benchtop "ready μ column" row last.
        def _row(title: str, field: QWidget, option: Optional[QWidget] = None) -> None:
            host = QWidget()
            hl = QHBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            field.setFixedWidth(280)  # = _mk max width; options line up
            hl.addWidget(field)
            if option is not None:
                hl.addWidget(option)
            hl.addStretch(1)
            form.addRow(title, host)

        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _row(t("imp_col_energy"), cmb_e)
        _row(t("imp_mode"), cmb_mode)
        _row(t("imp_col_i0"), cmb_i0)
        _row(t("imp_col_sig"), cmb_sig, chk_ln)
        _row(t("imp_fl_col"), cmb_fl)
        _row(t("imp_ref_col"), cmb_ref, chk_ref)
        _row(t("imp_col_mu"), cmb_mu)
        ed_lo = QLineEdit()
        ed_hi = QLineEdit()
        ed_lo.setMaximumWidth(120)
        ed_hi.setMaximumWidth(120)
        rng_row = QHBoxLayout()
        rng_row.addWidget(ed_lo)
        rng_row.addWidget(QLabel("→"))
        rng_row.addWidget(ed_hi)
        rng_row.addStretch(1)
        form.addRow(t("imp_range"), rng_row)
        # Auto-process after import (v3.19.5, design A): runs the whole batch
        # pipeline.  It never exports - the user reviews, then exports.
        chk_auto_batch = QCheckBox(t("imp_auto_batch"))
        chk_auto_batch.setToolTip(t("imp_auto_batch_tip"))
        chk_auto_batch.setChecked(
            str(self.store.value("import/auto_batch", "0")).strip().lower()
            in ("1", "true", "yes", "on")
        )
        # v3.19.6: placed under the Import / Cancel buttons (right column)

        def _sync(*_):
            m = cmb_mode.currentIndex()
            trans, fluo = m in (0, 2), m in (1, 2)
            cmb_i0.setEnabled(m in (0, 1, 2))
            cmb_sig.setEnabled(trans)
            chk_ln.setEnabled(trans)
            cmb_fl.setEnabled(fluo)
            cmb_mu.setEnabled(m == 3)
            cmb_ref.setEnabled(chk_ref.isChecked())
            if 0 <= m < len(mode_tips):
                cmb_mode.setToolTip(mode_tips[m])

        cmb_mode.currentIndexChanged.connect(_sync)
        chk_ref.toggled.connect(_sync)
        _sync()

        # right side: same mark-list contract as main Groups list
        # (icon marks, A/U/I, bold+underline current — never ItemIsUserCheckable)
        files = list((batch or {}).get("files") or [str(p)])
        multi = len(files) > 1
        # v3.19.6: one file gets the SAME layout as many (list + A/U/I,
        # Import / Cancel under the list) - the dialog no longer changes shape
        show_list = True
        file_list = None
        # UserRole = path str; UserRole+1 = marked bool
        _ROLE_PATH = Qt.ItemDataRole.UserRole
        _ROLE_MARKED = Qt.ItemDataRole.UserRole + 1
        cur_key = self._norm_import_path(p)
        if show_list:
            right_host = QWidget()
            # Match main left panel narrow band (list + A/U/I)
            right_host.setFixedWidth(240)
            right_host.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
            )
            right = QVBoxLayout(right_host)
            right.setContentsMargins(0, 0, 0, 0)
            right.setSpacing(6)

            hdr = QHBoxLayout()
            hdr.setContentsMargins(0, 0, 0, 0)
            hdr.setSpacing(4)
            hdr_lbl = QLabel(t("imp_files_count", n=len(files)))
            hdr_lbl.setStyleSheet("font-weight:600;")
            hdr.addWidget(hdr_lbl, 1)
            # Same A / U / I toolbuttons as main Groups header
            btn_a = QToolButton()
            btn_u = QToolButton()
            btn_i = QToolButton()
            for b, letter, tip_key in (
                (btn_a, "A", "mark_all_detail"),
                (btn_u, "U", "mark_none_detail"),
                (btn_i, "I", "mark_invert_detail"),
            ):
                b.setText(letter)
                b.setFixedSize(22, 22)
                b.setAutoRaise(True)
                b.setToolTip(t(tip_key))
                b.setStyleSheet(
                    "QToolButton { background: #fff; border: 1px solid #ccc; "
                    "border-radius: 3px; font-size: 12px; font-weight: 700; "
                    "color: #555; padding: 0; }"
                    "QToolButton:hover { border-color: #e64626; color: #e64626; }"
                )
                hdr.addWidget(b)
            grid.addLayout(hdr, 0, 1, Qt.AlignmentFlag.AlignBottom)

            file_list = QListWidget()
            file_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            file_list.setWordWrap(False)
            file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
            file_list.setIconSize(QSize(18, 18))
            file_list.setMinimumHeight(60)  # never pushes the form rows apart

            def _is_cur_path(f: str) -> bool:
                try:
                    return self._norm_import_path(f) == cur_key
                except Exception:
                    return Path(f).name == p.name

            def _paint_imp_item(it: QListWidgetItem) -> None:
                path = it.data(_ROLE_PATH)
                marked = bool(it.data(_ROLE_MARKED))
                self._paint_mark_list_item(
                    it, marked=marked, is_current=_is_cur_path(str(path or ""))
                )

            for f in files:
                it = QListWidgetItem(Path(f).name)
                # Same flags as group list — NEVER ItemIsUserCheckable
                it.setFlags(
                    it.flags()
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                it.setData(_ROLE_PATH, f)
                # Default: only the preview file is marked (user marks more)
                it.setData(_ROLE_MARKED, bool(_is_cur_path(f)))
                it.setToolTip(str(f))
                _paint_imp_item(it)
                file_list.addItem(it)
            file_list.clearSelection()
            right.addWidget(file_list, 1)

            def _imp_item_clicked(item: QListWidgetItem):
                if not self._mark_list_icon_zone_hit(file_list, item):
                    file_list.clearSelection()  # blue is not for "current"
                    return
                item.setData(_ROLE_MARKED, not bool(item.data(_ROLE_MARKED)))
                _paint_imp_item(item)
                file_list.clearSelection()
                _refresh_ok_label()

            def _imp_clear_sel():
                file_list.blockSignals(True)
                file_list.clearSelection()
                file_list.blockSignals(False)

            file_list.itemClicked.connect(_imp_item_clicked)
            file_list.itemSelectionChanged.connect(_imp_clear_sel)

            def _mark_all_imp(on: bool):
                for i in range(file_list.count()):
                    it = file_list.item(i)
                    it.setData(_ROLE_MARKED, on)
                    _paint_imp_item(it)
                _refresh_ok_label()

            def _invert_imp():
                for i in range(file_list.count()):
                    it = file_list.item(i)
                    it.setData(_ROLE_MARKED, not bool(it.data(_ROLE_MARKED)))
                    _paint_imp_item(it)
                _refresh_ok_label()

            btn_a.clicked.connect(lambda: _mark_all_imp(True))
            btn_u.clicked.connect(lambda: _mark_all_imp(False))
            btn_i.clicked.connect(_invert_imp)

            # no batch hint under the buttons (v2.19.3, user: adds nothing —
            # the list behaves exactly like the Groups list)
            grid.addWidget(right_host, 1, 1, 2, 1)  # top = preview top

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(t("cancel"))

        def _refresh_ok_label(*_):
            if ok_btn is None or file_list is None:
                return
            n = sum(
                1
                for i in range(file_list.count())
                if bool(file_list.item(i).data(_ROLE_MARKED))
            )
            ok_btn.setText(t("imp_ok_batch", n=n))
            ok_btn.setEnabled(n > 0)

        if file_list is not None:
            _refresh_ok_label()
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        # Buttons: under the file list, bottom level with the energy-range
        # row (rows 1–2 span); Auto-process right under them (v3.19.6).
        if show_list:
            right.addWidget(bb, 0, Qt.AlignmentFlag.AlignRight)
            grid.addWidget(chk_auto_batch, 3, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        else:
            grid.addWidget(bb, 3, 0, Qt.AlignmentFlag.AlignRight)
        # wide enough for title | 280 px picker | option, plus the file list
        need_w = form_host.sizeHint().width() + (250 if show_list else 0) + 40
        dlg.setMinimumWidth(need_w)
        dlg.resize(max(dlg.width(), need_w), dlg.height())
        if not dlg.exec():
            if batch is not None:
                batch["cancelled"] = True
            return None
        def _num(ed):
            txt = ed.text().strip()
            if not txt:
                return None
            try:
                return float(txt)
            except ValueError:
                return None

        m = cmb_mode.currentIndex()
        if m in (0, 1, 2):
            self.store.setValue("import/raw_mode", m)  # next import starts here
        # Combo indices are still 0-based (items listed in column order).
        # trans+fluo = a "trans" spec + fl_col → fluo_channel_spec adds _fluo.
        spec = {
            "e_col": cmb_e.currentIndex(),
            "mode": {0: "trans", 1: "fluo", 2: "trans", 3: "mu"}[m],
            "i0_col": cmb_i0.currentIndex(),
            "it_col": cmb_fl.currentIndex() if m == 1 else cmb_sig.currentIndex(),
            "mu_col": cmb_mu.currentIndex(),
            "ln": chk_ln.isChecked() if m in (0, 2) else False,
            "ref_col": cmb_ref.currentIndex() if chk_ref.isChecked() else None,
            "fl_col": cmb_fl.currentIndex() if m == 2 else None,
            "emin": _num(ed_lo),
            "emax": _num(ed_hi),
            "auto_batch": bool(chk_auto_batch.isChecked()),
        }
        self.store.setValue(
            "import/auto_batch", "1" if spec["auto_batch"] else "0"
        )
        if multi and batch is not None and file_list is not None:
            selected: set = set()
            for i in range(file_list.count()):
                it = file_list.item(i)
                if bool(it.data(_ROLE_MARKED)):
                    selected.add(
                        self._norm_import_path(it.data(_ROLE_PATH))
                    )
            if not selected:
                QMessageBox.warning(dlg, t("imp_title"), t("imp_none_selected"))
                batch["cancelled"] = True
                return None
            batch["selected"] = selected
            batch["spec"] = spec  # shared columns for every marked raw file
            self._log(t("imp_batch_applied", n=len(selected)))
        return spec

    def _import_path(self, path: str, replace: bool = False, use_outer_progress: bool = False) -> bool:
        """Load data/prj. Auto-edge only the first group so large prj stay responsive.

        replace=True clears the current project first (default for Open .prj).
        use_outer_progress=True: caller already owns the progress dialog (multi-file).
        Returns True on success, False after showing an error dialog.
        """
        p = Path(path)
        if not p.is_file():
            msg = f"File not found:\n{path}"
            self._log(msg)
            self._report_import_error(path, msg)
            return False
        own_progress = not use_outer_progress
        try:
            if own_progress:
                self._progress_begin(
                    self.i18n.t("progress_opening", name=p.name), sliding=True
                )

            # Force OneDrive hydrate if needed.  On a cloud-only file this
            # first read IS the download — run it on the worker so the bar
            # keeps sliding instead of freezing (v2.18.0).
            def _hydrate() -> int:
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                try:
                    with open(p, "rb") as fh:
                        _ = fh.read(16)
                except OSError as oe:
                    raise RuntimeError(f"Cannot read file (OneDrive offline?): {oe}") from oe
                return size

            size_b = self._run_with_busy(_hydrate, p.name)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            if replace and p.suffix.lower() == ".prj":
                self.project = AthenaProject(p.stem if p.suffix.lower() == ".prj" else "Untitled")
                self.last_fit = None  # new data — previous fit overlay no longer applies

            loaded: list[GroupRecord] = []
            suf = p.suffix.lower()
            if suf == ".prj":
                size_mb = size_b / (1024 * 1024) if size_b else 0
                self._progress_update(
                    None,
                    self.i18n.t(
                        "progress_parsing_prj",
                        name=p.name,
                        size=f"{size_mb:.1f}" if size_mb else "?",
                    ),
                    maximum=0,
                    log=True,
                )
                # Retry: Athena may be re-saving this file right now (gzip switch)
                # or OneDrive may still be syncing it — a partial read is transient.
                groups = None
                last_exc: Optional[Exception] = None
                for attempt in (1, 2, 3):
                    if not self._progress_update(
                        None,
                        self.i18n.t(
                            "progress_parse_attempt",
                            name=p.name,
                            attempt=attempt,
                        ),
                    ):
                        self._log(self.i18n.t("progress_cancelled"))
                        return False
                    try:
                        groups = self._run_with_busy(lambda: load_all_from_prj(str(p)), p.name)
                        break
                    except Exception as le:
                        last_exc = le
                        self._log(f"  read attempt {attempt}/3 failed: {le}")
                        if attempt < 3:
                            QApplication.processEvents()
                            time.sleep(0.8)
                if groups is None:
                    raise RuntimeError(
                        self._bi(
                            f"读取 {p.name} 失败（重试 3 次）。\n{last_exc}\n\n"
                            "如果 Athena 刚保存过这个文件、或 OneDrive 还在同步，等几秒再打开一次。",
                            f"Cannot read {p.name} (3 attempts).\n{last_exc}\n\n"
                            "If Athena just re-saved this file or OneDrive is still syncing it, wait a few seconds and open it again.",
                        )
                    ) from last_exc
                self._log(f"  parser returned {len(groups)} group(s)")
                if not groups:
                    # last resort: first group via load_spectrum
                    try:
                        self._progress_update(None, self.i18n.t("progress_fallback", name=p.name))
                        e, mu, name = load_energy_mu_from_file(str(p))
                        g = self.project.add_group(name, e, mu, path=str(p))
                        g.marked = True
                        loaded.append(g)
                        self._log(f"  fallback single group: {name}")
                    except Exception as fe:
                        raise RuntimeError(
                            f"No usable groups in {p.name}.\n{fe}"
                        ) from fe
                else:
                    n_g = len(groups)
                    # Determinate bar while registering each spectrum
                    for i, row in enumerate(groups):
                        name, e, mu = row[0], row[1], row[2]
                        meta = row[3] if len(row) > 3 else {}
                        if not self._progress_update(
                            i + 1,
                            self.i18n.t(
                                "progress_group",
                                i=i + 1,
                                n=n_g,
                                name=name,
                            ),
                            maximum=n_g + 1,  # +1 for auto-edge step
                        ):
                            self._log(self.i18n.t("progress_cancelled"))
                            break
                        g = self.project.add_group(name, e, mu, path=str(p))
                        apply_prj_meta_to_group(g, meta if isinstance(meta, dict) else {})
                        if "marked" not in (meta or {}):
                            g.marked = True  # Athena: show all selected by default
                        loaded.append(g)
                self.project.title = p.stem
                self.project.source_path = str(p)
                if self._batch_pipeline_enabled() and loaded:
                    # a .prj usually carries hand-tuned parameters: the remembered
                    # raw-import switch must not silently re-process it
                    QApplication.restoreOverrideCursor()
                    answer = QMessageBox.question(
                        self,
                        self._bi("自动批量处理", "Auto-process"),
                        self._bi(
                            f"“导入后自动批量处理”已开启。\n要对 {p.name} 的 {len(loaded)} 条曲线重新跑批处理吗？\n"
                            "会覆盖工程里保存的归一化 / 背景 / FT 参数（可一步撤销）。",
                            f"“Auto-process after import” is on.\nRe-run the batch on the {len(loaded)} curve(s) of {p.name}?\n"
                            "This overwrites the project's saved normalization / background / FT parameters (one-step undo).",
                        ),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                    if answer == QMessageBox.StandardButton.Yes:
                        self._batch_requested = True
                self.store.setValue("last_prj_save", str(p))
                self._log(f"Loaded prj {p.name}: {len(loaded)} groups")
            else:
                # Raw text data: Athena-style import dialog (columns / mode /
                # ln / reference / range). Restore the wait cursor while the
                # dialog is open so the pointer doesn't spin over it.
                QApplication.restoreOverrideCursor()
                spec = self._raw_import_spec(p)
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                if spec is None:
                    # User cancelled import dialog — abort rest of multi-file batch
                    batch = getattr(self, "_import_batch", None)
                    if isinstance(batch, dict):
                        batch["cancelled"] = True
                    self._log(self.i18n.t("open_cancelled"))
                    return False
                # Multi-file: dialog may leave this file unchecked — skip without aborting batch
                if self._batch_should_skip(p):
                    return True
                # Remember the spec + the auto-batch request for the pipeline
                # that runs once the whole batch is imported (v3.19.5).
                if not hasattr(self, "_import_specs"):
                    self._import_specs = {}
                self._import_specs[self._norm_import_path(p)] = dict(spec or {})
                if self._batch_pipeline_enabled(spec):
                    self._batch_requested = True
                # The raw options dialog was accepted and this file is selected;
                # only now is it safe for Replace List to discard the old data.
                if replace:
                    self.project = AthenaProject("Untitled")
                    self.last_fit = None
                self._progress_update(
                    None,
                    self.i18n.t("progress_reading_spectrum", name=p.name),
                    maximum=2,
                )
                if spec:
                    e, mu, name = self._run_with_busy(lambda: load_energy_mu_with_spec(str(p), spec), p.name)
                else:
                    e, mu, name = self._run_with_busy(lambda: load_energy_mu_from_file(str(p)), p.name)
                self._progress_update(1, self.i18n.t("progress_group", i=1, n=1, name=name))
                fl_spec = fluo_channel_spec(spec)
                g = self.project.add_group(
                    name + "_trans" if fl_spec else name, e, mu, path=str(p)
                )
                g.marked = True
                loaded.append(g)
                self._log(f"Loaded {g.name} ({len(e)} pts)")
                # fluorescence channel (v2.18.0): <stem>_fluo next to _trans
                if fl_spec is not None:
                    try:
                        e_f, mu_f, _ = self._run_with_busy(
                            lambda: load_energy_mu_with_spec(str(p), fl_spec), p.name
                        )
                        gf = self.project.add_group(name + "_fluo", e_f, mu_f, path=str(p))
                        gf.marked = True
                        loaded.append(gf)
                        self._log(self._bi(
                            f"荧光通道已导入: {gf.name}（{len(e_f)} 点）",
                            f"Fluorescence channel imported: {gf.name} ({len(e_f)} pts)",
                        ))
                    except Exception as exc:
                        # never drop silently: the trans group stays, the user
                        # sees why the fluo one is missing
                        self._log(self._bi(
                            f"荧光通道未导入 [{p.name}]: {exc}",
                            f"Fluorescence channel skipped [{p.name}]: {exc}",
                        ))
                # reference channel (Athena): extra <name>_ref group
                if spec and spec.get("ref_col") is not None:
                    try:
                        e_r, mu_r, _ = self._run_with_busy(
                            lambda: load_energy_mu_with_spec(str(p), {**spec, "mode": "ref"}),
                            p.name,
                        )
                        gr = self.project.add_group(name + "_ref", e_r, mu_r, path=str(p))
                        gr.marked = False
                        loaded.append(gr)
                        self._log(self._bi(f"参考通道已导入: {gr.name}", f"Reference channel imported: {gr.name}"))
                    except Exception as exc:
                        self._log(f"reference channel skipped: {exc}")

            if loaded:
                self.project.set_current(loaded[0].id)
                max_v = getattr(self, "_load_progress", None)
                max_now = max_v.maximum() if max_v is not None else 0
                self._progress_update(
                    max_now if max_now > 0 else None,
                    self.i18n.t("progress_auto_edge", name=loaded[0].name),
                )
                # Resolve every imported absorber up front so the element filter is
                # complete immediately; only process the current curve for speed.
                # Tagging is passive (element/edge only — no e0 pin, no reprocess)
                # and must NOT depend on auto-edge-on-select: with the checkbox off
                # the left element filter silently lost every choice (v0.34.20).
                # Selection-time auto edge stays gated by the checkbox.
                self._auto_detect_loaded_group_elements(loaded)
                # No per-file larch run here (v2.18.1): a batch of 14 MEX1
                # slew files spent ~11 s each in autobk for curves that might
                # never be plotted.  _finish_open → _draw_after_load processes
                # exactly the plotted curves on the busy worker; any other
                # path's _replot processes the current one on demand.
                # Reset means the completed import state, including detected
                # element/edge/E0 and the initial marked state.
                for imported_group in loaded:
                    imported_group.commit_initial_state()
                self._refresh_list()
            self._set_left_mode("norm")
            # Marked plot mode when many groups
            if len(loaded) > 1 and hasattr(self, "rad_plot_marked"):
                self.rad_plot_marked.setChecked(True)
            return True
        except Exception as exc:
            tb = traceback.format_exc()
            self._log(f"Load failed: {exc}\n{tb}")
            try:
                DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
                with open(DEFAULT_OUTPUT / "gui_errors.log", "a", encoding="utf-8") as fh:
                    import datetime

                    fh.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} load {path} =====\n{tb}")
            except Exception:
                pass
            self._report_import_error(path, exc)
            return False
        finally:
            QApplication.restoreOverrideCursor()
            if own_progress:
                self._progress_end()

    def _flush_panel_to_current_group(self) -> None:
        """Write the visible panel onto the current group without reprocessing."""
        g = self.project.current
        if g is None or g.frozen:
            return
        g.settings = self._settings_from_controls()
        g.energy_shift = float(self.sp_eshift.value())
        g.importance = float(self.sp_importance.value())
        el = self.cmb_element.currentText().strip()
        if el:
            g.element = el
        edge = self.cmb_edge.currentText().strip()
        if edge:
            g.edge = edge

    def _save_snapshot(self):
        """Save the loaded groups as an Athena-compatible .prj (not a text dump)."""
        if not self.project.groups:
            QMessageBox.information(
                self,
                self.i18n.t("save"),
                self._bi("没有可保存的数据组。", "No groups to save."),
            )
            return
        self._flush_panel_to_current_group()
        title = (self.project.title or "project").strip() or "project"
        # strip accidental extension from title for default file name
        for ext in (".prj", ".txt", ".xdi"):
            if title.lower().endswith(ext):
                title = title[: -len(ext)]
                break
        default_name = f"{title}.prj"
        src = str(getattr(self.project, "source_path", "") or "").strip()
        last = str(self.store.value("last_prj_save", "") or "").strip()
        if src and Path(src).suffix.lower() == ".prj":
            start = src
        elif last:
            start = str(Path(last).with_name(default_name))
        else:
            start = str(DEFAULT_OUTPUT / default_name)
        path, _ = QFileDialog.getSaveFileName(
            self,
            self._bi("保存 Athena 项目 (.prj)", "Save Athena project (.prj)"),
            start,
            "Athena project (*.prj);;All files (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path:
            return
        if not str(path).lower().endswith(".prj"):
            path = str(Path(path).with_suffix(".prj"))
        res = save_project_to_prj(self.project, path)
        if not res.ok:
            QMessageBox.warning(
                self,
                self.i18n.t("save"),
                self._bi(
                    f"保存 .prj 失败：\n{res.message}",
                    f"Failed to save .prj:\n{res.message}",
                ),
            )
            self._log(f"save .prj failed: {res.message}")
            return
        self.store.setValue("last_prj_save", str(path))
        self.project.source_path = str(path)
        stem = Path(path).stem
        if stem:
            self.project.title = stem
        self._log(res.message)
        self._status(
            self._bi(f"已保存 {Path(path).name}", f"Saved {Path(path).name}")
        )

    def _open_output(self):
        DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(DEFAULT_OUTPUT.resolve())))

    # ========== Plot / click marks ==========
    def _curve_boxes_changed(self, *_):
        if getattr(self, "_curve_updating", False):
            return
        self._curve_updating = True
        try:
            self._update_curve_boxes(sender=self.sender())
        finally:
            self._curve_updating = False
        self._replot()

    def _update_curve_boxes(self, sender=None):
        """E-panel curve checkbox logic (v0.35.0):
        - **Solo views are exclusive but never greyed out**: μ(E) / norm /
          dμ/dE / d²μ stay clickable; checking one simply unchecks the others
          (click-to-switch). The old behaviour disabled the conflicting boxes,
          so e.g. with dμ/dE on the user could not click μ(E)/norm back on.
        - **μ(E) companions**: bkg/pre/post only make sense with μ(E) (baseline
          only displays correctly there) — they grey out when μ(E) is off.
        """
        solos = (self.chk_norm, self.chk_d1, self.chk_d2)
        companions = (self.chk_bkg, self.chk_pre, self.chk_post)
        if sender in solos and sender.isChecked():
            # a solo view takes over: uncheck μ + the other solos
            self.chk_mu.setChecked(False)
            for b in companions:
                b.setChecked(False)
            for s in solos:
                if s is not sender:
                    s.setChecked(False)
        elif sender is self.chk_mu and self.chk_mu.isChecked():
            # μ(E) takes back the view: drop any active solo
            for s in solos:
                s.setChecked(False)
        mu_on = self.chk_mu.isChecked() and not any(s.isChecked() for s in solos)
        for b in companions:
            b.setEnabled(mu_on)
            if not mu_on:
                b.setChecked(False)

    def _show_flags(self) -> dict:
        return {
            "mu": self.chk_mu.isChecked() and not self.chk_norm.isChecked(),
            "normalized": self.chk_norm.isChecked(),
            "flat": self.chk_flatten.isChecked(),
            "pre": self.chk_pre.isChecked(),
            "post": self.chk_post.isChecked(),
            "bkg": self.chk_bkg.isChecked(),
            "deriv1": self.chk_d1.isChecked(),
            "deriv2": self.chk_d2.isChecked(),
        }

    def _groups_to_plot(self) -> List[GroupRecord]:
        """Athena-like scope: Current = selected only; Marked = overlay.

        Marked always includes the current group (appended last so it draws
        on top). Unchecking current while other rows stay marked used to drop
        it from the plot; energy guides then came from vis[0].settings, so
        dragging pre/norm wrote the panel and the next replot restored the
        neighbour's window (v0.34.18).
        """
        cur = self.project.current
        if hasattr(self, "rad_plot_current") and self.rad_plot_current.isChecked():
            groups = [cur] if cur else []
        else:
            groups = list(self.project.marked_groups())
            if cur is not None and cur not in groups:
                groups.append(cur)
        wanted_element = self._curve_element_filter().lower()
        if wanted_element:
            groups = [
                g for g in groups
                if str(g.element or "").strip().lower() == wanted_element
            ]
            # Current stays on the plot even if guess_edge retagged it
            # off the filter (v1.15.20). Empty plot = lost energy guides.
            if cur is not None and cur not in groups:
                groups.append(cur)
        return groups

    def _energy_guides(self) -> dict:
        """Absolute-energy positions for E0 / pre / norm guide lines on the E plot.

        The guide source is the **current** group when it is on the plot
        (Marked overlay always includes current, v0.34.18). Otherwise the
        first plotted group (element filter dropped current) so the guides
        still match a visible curve.
        """
        none_ = {"e0": None, "pre1": None, "pre2": None, "norm1": None, "norm2": None}
        vis = self._groups_to_plot()
        cur = self.project.current
        g = cur if (cur is not None and cur in vis) else (vis[0] if vis else cur)
        if g is None:
            return none_
        if g is cur:
            # live panel values for the current group (editing preview)
            e0 = float(self.sp_e0.value() or 0.0)
            if g.session is not None and g.session.arrays is not None:
                ae0 = getattr(g.session.arrays, "e0", float("nan"))
                if self.chk_e0_auto.isChecked() and isinstance(ae0, (int, float)) and np.isfinite(ae0):
                    e0 = float(ae0)
                elif e0 <= 0 and isinstance(ae0, (int, float)) and np.isfinite(ae0):
                    e0 = float(ae0)
            pre1 = float(self.sp_pre1.value())
            pre2 = float(self.sp_pre2.value())
            norm1 = float(self.sp_norm1.value())
            norm2 = float(self.sp_norm2.value())
        else:
            # off-screen current group: use the viewed group's own settings
            s = g.settings
            e0 = float(s.e0) if s.e0 else 0.0
            if e0 <= 0 and g.session is not None and g.session.arrays is not None:
                ae0 = getattr(g.session.arrays, "e0", float("nan"))
                if isinstance(ae0, (int, float)) and np.isfinite(ae0):
                    e0 = float(ae0)
            pre1, pre2 = float(s.pre1), float(s.pre2)
            norm1, norm2 = float(s.norm1), float(s.norm2)
        return {
            "e0": e0 if e0 > 0 else None,
            "pre1": e0 + pre1 if e0 > 0 else None,
            "pre2": e0 + pre2 if e0 > 0 else None,
            "norm1": e0 + norm1 if e0 > 0 else None,
            "norm2": e0 + norm2 if e0 > 0 else None,
        }

    def _plot_windows(self) -> dict:
        """k / R / bkg-k spans to highlight when the matching eye toggle is on."""
        wins: dict = {}
        if getattr(self, "btn_win_k", None) is not None and self.btn_win_k.isChecked():
            wins["k"] = (self.sp_kmin.value(), self.sp_kmax.value())
        if getattr(self, "btn_win_r", None) is not None and self.btn_win_r.isChecked():
            wins["r"] = (self.sp_rmin.value(), self.sp_rmax.value())
        if getattr(self, "btn_win_bkg", None) is not None and self.btn_win_bkg.isChecked():
            wins["k_bkg"] = (self.sp_kmin_bkg.value(), self.sp_kmax_bkg.value())
        return wins

    def _visible_e_windows(self) -> set:
        """Which E-panel windows (band + dashed edges + baseline) are shown:
        driven by the eye toggles on the Norm panel (v0.35.8).  Not shown →
        not draggable."""
        vis = set()
        if getattr(self, "btn_win_pre", None) is not None and self.btn_win_pre.isChecked():
            vis.add("pre")
        if getattr(self, "btn_win_norm", None) is not None and self.btn_win_norm.isChecked():
            vis.add("norm")
        return vis

    def _window_visibility_changed(self, window: str, shown: bool) -> None:
        """Hide/show all artists immediately, then refresh when safe."""
        if hasattr(self, "canvas"):
            self.canvas.set_window_visibility(window, shown)
        if not self._block:
            self._replot()

    def _replot(self, preserve_view: bool = True):
        if self._block:
            return  # control batch-update in progress — caller replots once at the end
        if not hasattr(self, "canvas"):
            return
        if getattr(self.canvas, "mode", "") == "wt":
            # Full-panel wavelet heatmap: a read-only 2-D image, so none of the
            # 1-D curve pipeline below applies (spec §4.3b).
            self._draw_wt_panel()
            return
        # Zoom / pan is user state (v0.35.1): edits replot in place and KEEP
        # the current view.  Only explicit resets pass preserve_view=False —
        # double-click (_reset_plot_view), view-range spins, Reset All,
        # import (_finish_open), remove-all.  Mode switches rebuild the axes
        # before replotting; the has-content guard then skips the restore and
        # the fresh axes autoscale.
        saved: dict = {}
        show_flags = self._show_flags()
        # A y-mode change (μ ↔ norm/flat ↔ derivative) invalidates the old
        # y-scale entirely — re-frame instead of preserving (v0.35.8).
        ymode = (
            bool(show_flags.get("normalized")), bool(show_flags.get("flat")),
            bool(show_flags.get("deriv1")), bool(show_flags.get("deriv2")),
        )
        if ymode != getattr(self, "_last_ymode", None):
            preserve_view = False
        self._last_ymode = ymode
        if preserve_view:
            for name, ax in self.canvas.axes.items():
                try:
                    if ax.lines or ax.patches or ax.collections:
                        saved[name] = (tuple(ax.get_xlim()), tuple(ax.get_ylim()))
                except Exception:
                    pass
        cur = self.project.current
        selected_fit_weight = (
            self.cmb_fit_plot_kw.currentData()
            if hasattr(self, "cmb_fit_plot_kw") else None
        )
        selected_fit_weight = int(selected_fit_weight) if selected_fit_weight in (1, 2, 3) else None
        if cur is not None and cur.energy is not None and cur.mu is not None:
            arr = cur.ensure_session().arrays
            if arr is None or arr.energy is None or not np.isfinite(getattr(arr, "e0", np.nan)):
                cur.process()
            if (
                not self._block
                and self.chk_e0_auto.isChecked()
                and cur.session is not None
                and cur.session.arrays is not None
                and np.isfinite(getattr(cur.session.arrays, "e0", np.nan))
            ):
                self._block = True
                self.sp_e0.setValue(float(cur.session.arrays.e0))
                self._block = False
        self.canvas.draw_groups(
            self._groups_to_plot(),
            mode="all",
            show=show_flags,
            kw=(
                int(selected_fit_weight or 2)
                if hasattr(self, "cmb_fit_plot") and str(self.cmb_fit_plot.currentData() or "standard") != "standard"
                else self.sp_kw_plot.value()
            ),
            emin=self.sp_emin.value(),
            emax=self.sp_emax.value(),
            marks=self.energy_marks,
            fit=self.last_fit,
            fit_views=(self.last_fit.plot_views if isinstance(self.last_fit, FitResult) else {}),
            fit_weight=selected_fit_weight,
            guides=self._energy_guides(),
            current_id=cur.id if cur else None,
            windows=self._plot_windows(),
            view_ranges=self._view_ranges,
            energy_point_mode=(
                hasattr(self, "rad_deglitch_click")
                and self.rad_deglitch_click.isChecked()
            ),
            deglitch_selected_energy=self._deglitch_selected_energy,
            analysis_overlay=getattr(self, "_analysis_overlay", None),
            visible_e_windows=self._visible_e_windows(),
        )
        for name, (xl, yl) in saved.items():
            ax = self.canvas.axes.get(name)
            if ax is None:
                continue
            try:
                ax.set_xlim(*xl)
                ax.set_ylim(*yl)
                twin = getattr(ax, "_kq_twin", None)
                if twin is not None:
                    twin.set_xlim(ax.get_xlim())
            except Exception:
                pass
        if saved:
            self.canvas.draw_idle()

    def _preserve_plot_zoom(self, redraw, shift_x: float = 0.0) -> None:
        """Run *redraw* (replot / apply) without throwing away the user's
        wheel-zoom / middle-drag pan window (v0.35.0).

        Snapshots every panel's limits before the redraw and restores them
        after; ``shift_x`` translates restored x-limits (energy calibration
        shifts all absolute energies by delta, so the same feature stays in
        view).  Double-click remains the explicit full-view reset.
        """
        old: dict = {}
        if hasattr(self, "canvas"):
            for name, ax in self.canvas.axes.items():
                try:
                    old[name] = (tuple(ax.get_xlim()), tuple(ax.get_ylim()))
                except Exception:
                    pass
        redraw()
        if not old or not hasattr(self, "canvas"):
            return
        for name, (xl, yl) in old.items():
            ax = self.canvas.axes.get(name)
            if ax is None:
                continue
            try:
                ax.set_xlim(xl[0] + shift_x, xl[1] + shift_x)
                ax.set_ylim(*yl)
                twin = getattr(ax, "_kq_twin", None)
                if twin is not None:
                    twin.set_xlim(ax.get_xlim())
            except Exception:
                pass
        self.canvas.draw_idle()

    def _plot_double_clicked(self, event=None):
        """Double-click on the plot (v0.35.1, refined v0.35.6):
        - E0 pin armed + double-click on the E panel → set E0 there (E0 is
          set ONLY this way; it is no longer a draggable guide).
        - any other pin just consumed the FIRST click of this double-click
          (set a value and disarmed) → swallow, never zoom out mid-gesture.
        - no pin involved → restore the full view (explicit zoom-out)."""
        ax = getattr(event, "inaxes", None)
        x = getattr(event, "xdata", None)
        if (
            self.click_mode == "e0"
            and ax is not None
            and ax is self.canvas.axes.get("E")
            and x is not None
        ):
            if self.project.current is None:
                self._log(self.i18n.t("no_current_group"))
                self._set_click_mode("none")
                return
            self._block = True
            self.chk_e0_auto.setChecked(False)
            self.sp_e0.setValue(float(x))
            self._block = False
            # Pin the value into the group's settings IMMEDIATELY (v0.35.12):
            # with auto-reprocess off the pick used to live only in the spin,
            # and any same-group controls reload (undo / auto-norm / section
            # apply) reverted it to the auto-detected point.
            g = self.project.current
            if g is not None:
                g.settings.e0 = float(x)
            self._log(f"E0 set to {float(x):.4f} by double-click")
            # No energy_marks entry (v0.35.8): every set used to leave a
            # permanent purple line; the red dot already shows the current E0.
            if self.chk_auto.isChecked():
                self._apply_current()
            else:
                self._replot()
            self._set_click_mode("none")
            return
        # v0.35.6: a pin/deglitch single-click set fired <0.6 s ago — this
        # dblclick event is its second half, not a zoom-out request.
        if time.monotonic() - getattr(self, "_last_pin_click_at", 0.0) < 0.6:
            return
        self._reset_plot_view()

    def _reset_plot_view(self):
        """Double-click on the plot: restore the original full view (spin-defined
        E/R windows + autoscale on k/q) after wheel zooming / middle-drag pan."""
        self._replot(preserve_view=False)
        self._status(self.i18n.t("view_reset"))

    def _on_drag_guide(self, panel: str, name: str, x_abs: float, live: bool = True) -> None:
        """Drag guide lines on the plot panels: E (pre/norm window edges), k
        (FT/bkg windows), R (R window). Live moves only the artists; release
        replots (and reprocesses when auto-apply is on)."""
        moved_x = float(x_abs)
        if panel == "E":
            if name == "e0":
                self._block = True
                self.chk_e0_auto.setChecked(False)
                self.sp_e0.setValue(float(x_abs))
                self._block = False
                moved_x = float(self.sp_e0.value())
            else:
                e0 = float(self.sp_e0.value() or 0.0)
                g = self.project.current
                if e0 <= 0 and g is not None and g.session and g.session.arrays is not None:
                    ae0 = getattr(g.session.arrays, "e0", float("nan"))
                    if isinstance(ae0, (int, float)) and np.isfinite(ae0):
                        e0 = float(ae0)
                if e0 <= 0:
                    e0 = float(x_abs)
                spin = {
                    "pre1": self.sp_pre1,
                    "pre2": self.sp_pre2,
                    "norm1": self.sp_norm1,
                    "norm2": self.sp_norm2,
                }.get(name)
                if spin is None:
                    return
                self._block = True
                spin.setValue(float(x_abs) - e0)
                self._block = False
                moved_x = e0 + float(spin.value())
        else:
            spin = {
                ("k", "kmin"): self.sp_kmin,
                ("k", "kmax"): self.sp_kmax,
                ("k", "bkmin"): self.sp_kmin_bkg,
                ("k", "bkmax"): self.sp_kmax_bkg,
                ("R", "rmin"): self.sp_rmin,
                ("R", "rmax"): self.sp_rmax,
            }.get((panel, name))
            if spin is None:
                return
            self._block = True
            spin.setValue(float(x_abs))
            self._block = False
            moved_x = float(spin.value())
        # Live: move only the guide artists (no full replot); release: replot
        # once (+ reprocess when auto-apply is on). _replot preserves the
        # user's zoom by default (v0.35.1).
        if live:
            self.canvas.move_guide(panel, name, moved_x)
        else:
            if self.chk_auto.isChecked():
                self._apply_current()
            else:
                self._replot()
            self._log(self.i18n.t("op_ok_drag_edge", name=name))

    def _side_toggle_icons_refresh(self):
        dark = getattr(self, "_theme_dark", False)
        for btn in (getattr(self, "_undo_buttons", None) or {}).values():
            try:
                btn.setIcon(_undo_icon(dark))
            except RuntimeError:  # batch dialog closed -> widget deleted
                pass
        self.btn_toggle_left.setIcon(
            _side_toggle_icon("left" if self._left_visible else "right", dark)
        )
        self.btn_toggle_right.setIcon(
            _side_toggle_icon("right" if self._right_visible else "left", dark)
        )

    def _toggle_side_panel(self, side: str):
        """Toolbar corner buttons: collapse/expand the left list / right params."""
        if side == "left":
            self._left_visible = not self._left_visible
            self._left_panel.setVisible(self._left_visible)
        else:
            self._right_visible = not self._right_visible
            self._right_panel.setVisible(self._right_visible)
        self._side_toggle_icons_refresh()

    def _current_view_space(self) -> str:
        mode = getattr(self.canvas, "mode", "grid")
        return {"grid": "E", "kq": "k"}.get(mode, mode)

    def _load_view_range_spins(self) -> None:
        """Load the range spins with the current panel's stored range."""
        space = self._current_view_space()
        self._block = True
        try:
            if space == "E":
                self.sp_view_min.setValue(self.sp_emin.value())
                self.sp_view_max.setValue(self.sp_emax.value())
            else:
                lo, hi = self._view_ranges.get(space, (0.0, 15.0))
                self.sp_view_min.setValue(lo)
                self.sp_view_max.setValue(hi)
        finally:
            self._block = False

    def _view_range_changed(self, *_):
        """Range spins edited: store into the current panel's slot and replot."""
        if self._block:
            return
        if getattr(self.canvas, "mode", "") == "wt":
            return  # the WT heatmap has no 1-D view range to edit
        space = self._current_view_space()
        lo, hi = self.sp_view_min.value(), self.sp_view_max.value()
        if space == "E":
            self._block = True
            try:
                self.sp_emin.setValue(lo)
                self.sp_emax.setValue(hi)
            finally:
                self._block = False
        else:
            self._view_ranges[space] = (lo, hi)
        # The view-range spins ARE the view: applying them resets the window
        self._replot(preserve_view=False)

    def _focus_panel(self, name: str):
        """Switch 2×2 grid / focused E/k/R/q panel and replot."""
        if hasattr(self, "cmb_fit_plot"):
            self.cmb_fit_plot.blockSignals(True)
            self.cmb_fit_plot.setCurrentIndex(self.cmb_fit_plot.findData("standard"))
            self.cmb_fit_plot.blockSignals(False)
        for k, b in getattr(self, "_space_btns", {}).items():
            b.setChecked(k == name)
        self.canvas.set_mode(name)
        self._load_view_range_spins()
        self._replot(preserve_view=False)  # axes were rebuilt — fresh autoscale

    def _fit_plot_changed(self, _index: int = -1):
        """Select a precomputed Artemis-style result view, never a new fit."""
        code = str(self.cmb_fit_plot.currentData() or "standard")
        if code == "standard":
            self._focus_panel("grid")
            return
        for button in getattr(self, "_space_btns", {}).values():
            button.setChecked(False)
        self.canvas.set_mode(f"fit_{code}")
        self._replot()

    def _fit_plot_weight_changed(self, _index: int = -1):
        """Refresh the selected precomputed result weight without re-fitting."""
        self._replot()

    def _win_toggle(self) -> QToolButton:
        """Window toggle at the LEFT of a range row (v0.35.8): show/hide that
        window's band + edges (+ baseline on E) on the plot; while shown the
        edges are draggable.  Icon: simple eye — shown = yellow bg + open eye,
        hidden = slashed eye (v0.35.10).  Width fixed at 24, height stretches with the row."""
        b = QToolButton()
        b.setCheckable(True)
        b.setAutoRaise(True)
        _assets = Path(__file__).resolve().parent / "assets"
        ic = QIcon()
        ic.addFile(str(_assets / "eye_off.svg"), QSize(), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addFile(str(_assets / "eye_on.svg"), QSize(), QIcon.Mode.Normal, QIcon.State.On)
        b.setIcon(ic)
        b.setIconSize(QSize(14, 14))
        # Fixed width; height is pinned to the row's spin box AFTER the theme
        # is applied (_sync_win_toggle_heights) — an Expanding policy let the
        # parent layout stretch it taller than the fields (v0.35.10)
        b.setFixedWidth(24)
        b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        b.setStyleSheet(
            "QToolButton { background: #fff; border: 1px solid #ccc; border-radius: 3px; padding: 0; }"
            "QToolButton:hover { border-color: #e64626; }"
            "QToolButton:pressed { background: #f6e3da; border-top-color: #a83215; border-left-color: #a83215; }"
            "QToolButton:checked { background: #ffe08a; border: 1px solid #e0a800; }"
        )
        return b


    def _e_window_toggled(self, *_):
        """pre/norm eye toggles: replot to show/hide the window (edges become
        drag targets via _visible_e_windows).  View is preserved."""
        self._replot()

    def _pin(self, mode: str) -> QToolButton:
        """Athena/Larix-style pin: arm this field, next click on the E plot fills it."""
        b = QToolButton()
        b.setText("📌")
        b.setCheckable(True)
        b.setFixedSize(22, 22)
        b.setAutoRaise(True)
        # armed = light ochre tint + 2px accent frame: a solid red background
        # swallowed the red 📌 glyph (v0.35.4)
        b.setStyleSheet(
            "QToolButton { background: #fff; border: 1px solid #ccc; border-radius: 3px; font-size: 11px; padding: 0; }"
            "QToolButton:hover { border-color: #e64626; }"
            "QToolButton:pressed { background: #f6e3da; border-top-color: #a83215; border-left-color: #a83215; }"
            "QToolButton:checked { background: #f8d9cd; border: 2px solid #e64626; }"
            "QToolButton:checked:pressed { background: #f0c4b4; border-top-color: #a83215; border-left-color: #a83215; }"
        )
        b.clicked.connect(lambda *_a, m=mode: self._pin_toggled(m))
        self._pin_btns[mode] = b
        return b

    def _pin_toggled(self, mode: str):
        # clicking the armed pin again disarms; arming one disarms the others
        self._set_click_mode("none" if self.click_mode == mode else mode)

    def _set_click_mode(self, mode: str):
        self.click_mode = mode
        armed = mode != "none"
        for m, b in getattr(self, "_pin_btns", {}).items():
            b.blockSignals(True)
            b.setChecked(m == mode)
            b.blockSignals(False)
        if hasattr(self, "canvas"):
            direct_deglitch = (
                hasattr(self, "rad_deglitch_click")
                and self.rad_deglitch_click.isChecked()
                and hasattr(self, "rad_dg_point")
                and self.rad_dg_point.isChecked()
            )
            self.canvas.setCursor(
                Qt.CursorShape.CrossCursor
                if armed or direct_deglitch
                else Qt.CursorShape.ArrowCursor
            )
        if armed:
            # Prefer dedicated tip keys for deglitch pins
            tip = self.i18n.t(f"pin_{mode}_tip")
            if tip == f"pin_{mode}_tip":
                tip = self.i18n.t(f"cm_{mode}")
            self._log(self._bi(f"📌 已取针：{tip}", f"📌 Armed: {tip}"))
        # Armed pins auto-disarm after 15 s idle, with a live countdown on
        # the plot (v0.35.5)
        if armed:
            self._pin_deadline = time.monotonic() + 15.0
            self._pin_countdown_timer.start()
            self._pin_countdown_tick()
        else:
            self._pin_countdown_timer.stop()
            if hasattr(self, "canvas"):
                self.canvas.set_countdown(None)
        # Armed pins own their window guides: replot to show/hide the band +
        # dashed edges + baseline (v0.35.1).  View is preserved by default.
        self._replot()

    def _pin_countdown_tick(self):
        """1 s ticker while a pin is armed: update the on-plot countdown,
        auto-disarm at 0 (v0.35.5)."""
        if self.click_mode == "none":
            self._pin_countdown_timer.stop()
            if hasattr(self, "canvas"):
                self.canvas.set_countdown(None)
            return
        remaining = int(round(self._pin_deadline - time.monotonic()))
        if remaining <= 0:
            self._set_click_mode("none")
            self._log(self.i18n.t("pin_auto_disarm"))
            return
        if hasattr(self, "canvas"):
            label = self.i18n.t(f"cm_{self.click_mode}")
            if label == f"cm_{self.click_mode}":
                label = self.click_mode
            self.canvas.set_countdown(f"📌 {label} · {remaining}s")

    def _clear_fit_result_display(self) -> None:
        """Clear every transient fit *and* analysis (LCF/PCA/pre-edge) output.

        Editable Fit inputs (GDS / paths tables) are kept — only results go away.
        """
        self.last_fit = None
        # LCF / PCA / pre-edge overlay on the E plot (was left behind by Reset All)
        self._analysis_overlay = None
        if hasattr(self, "txt_fit_result") and self.txt_fit_result is not None:
            self.txt_fit_result.clear()
            self.txt_fit_result.setVisible(False)
        if hasattr(self, "fit_quality_card"):
            self.fit_quality_card.setStyleSheet("")
        if hasattr(self, "lbl_fit_grade"):
            self.lbl_fit_grade.setStyleSheet("font-weight:700; font-size:11px;")
            self.lbl_fit_grade.setText(self.i18n.t("fit_quality_ph"))
        if hasattr(self, "lbl_fit_quality"):
            self.lbl_fit_quality.setText("")
        if hasattr(self, "lbl_fit_warning"):
            self.lbl_fit_warning.setText("")
        if hasattr(self, "cmb_fit_plot"):
            idx = self.cmb_fit_plot.findData("standard")
            if idx >= 0:
                self.cmb_fit_plot.blockSignals(True)
                self.cmb_fit_plot.setCurrentIndex(idx)
                self.cmb_fit_plot.blockSignals(False)
        # Deselect history so a residual history preview cannot re-inject curves
        if hasattr(self, "cmb_fit_history"):
            self.cmb_fit_history.blockSignals(True)
            self.cmb_fit_history.setCurrentIndex(-1)
            self.cmb_fit_history.blockSignals(False)

    def _clear_marks_and_reset(self):
        """Restore imported groups and UI state to their post-import baseline."""
        self._timer.stop()
        restored = []
        for group in list(self.project.groups):
            if getattr(group, "is_derived", False):
                continue
            if group.restore_initial_state():
                restored.append(group)
                try:
                    group.process()
                except Exception as exc:
                    self._log(f"reset process [{group.name}]: {exc}")
        self.project.groups = restored
        if self.project.current_id not in {g.id for g in restored}:
            self.project.current_id = restored[0].id if restored else None
        self._module_undo.clear()
        for button in self._undo_buttons.values():
            button.setEnabled(False)
        self.energy_marks.clear()
        self._deglitch_selected_energy = None
        self._set_click_mode("none")
        for sp in (
            getattr(self, "sp_dg_e", None),
            getattr(self, "sp_dg_e1", None),
            getattr(self, "sp_dg_e2", None),
        ):
            if sp is not None:
                self._block = True
                sp.setValue(0.0)
                self._block = False
        # Fit report, fit overlay, LCF/PCA/pre-edge overlay + report text, history selection
        self._clear_fit_result_display()
        self._view_ranges = {"k": (0.0, 15.0), "R": (0.0, 6.0), "q": (0.0, 30.0)}
        for name, button in self._space_btns.items():
            button.setChecked(name == "grid")
        self.canvas.set_mode("grid")
        self._curve_updating = True
        try:
            self.chk_mu.setChecked(True)
            for box in (self.chk_norm, self.chk_bkg, self.chk_pre, self.chk_post, self.chk_d1, self.chk_d2):
                box.setChecked(False)
            self._update_curve_boxes()
        finally:
            self._curve_updating = False
        current = self.project.current
        if current is not None:
            self._load_group_to_controls(current)
        self._refresh_list()
        self._update_meta()
        self._replot(preserve_view=False)  # Reset All = explicit full reset
        self._log(self.i18n.t("op_ok_clear_reset"))

    def _nearest_deglitch_sample(
        self, ax, x: float, tolerance_px: float = 14.0
    ) -> Optional[float]:
        """Snap a click to a nearby real energy sample using screen distance."""
        g = self.project.current
        if g is None or g.energy is None:
            return None
        energy = np.asarray(g.energy, dtype=float) + float(g.energy_shift)
        energy = energy[np.isfinite(energy)]
        if energy.size == 0:
            return None
        nearest = float(energy[int(np.argmin(np.abs(energy - float(x))))])
        try:
            click_px = float(ax.transData.transform((float(x), 0.0))[0])
            sample_px = float(ax.transData.transform((nearest, 0.0))[0])
            if abs(sample_px - click_px) <= float(tolerance_px):
                return nearest
        except (TypeError, ValueError, OverflowError):
            pass
        return None

    def _plot_clicked(self, ax, x, y, event):
        """Handle armed Energy pins or a direct Click-Deglitch point pick.

        Other unarmed left-clicks remain reserved for browsing and zooming.
        """
        # only energy axis for parameter marks
        if ax != self.canvas.axes.get("E"):
            return
        mode = self.click_mode
        direct_point = (
            mode == "none"
            and self.rad_deglitch_click.isChecked()
            and self.rad_dg_point.isChecked()
        )
        if direct_point:
            mode = "dg_e"
        # No armed pin → do nothing (default = browse / zoom only)
        if mode == "none":
            return

        g = self.project.current
        # Deglitch pins only need absolute energy; edge pins need a group
        is_dg = mode in ("dg_e", "dg_e1", "dg_e2")
        if not is_dg and g is None:
            self._log(self.i18n.t("no_current_group"))
            self._set_click_mode("none")
            return

        if is_dg:
            # Adding the picked-point marker needs a full redraw, but a normal
            # click must not throw away the user's wheel-zoomed inspection
            # window.  Double-click remains the explicit full-view reset.
            old_xlim = tuple(ax.get_xlim())
            old_ylim = tuple(ax.get_ylim())
            if mode == "dg_e":
                snapped = self._nearest_deglitch_sample(ax, float(x))
                if snapped is None:
                    self._log(self.i18n.t("deglitch_no_near_point"))
                    return
                x = snapped
                self.sp_dg_e.setValue(float(x))
                self._deglitch_selected_energy = float(x)
                self._log(self.i18n.t("deglitch_e_picked", e=f"{x:.4f}"))
            elif mode == "dg_e1":
                self.sp_dg_e1.setValue(float(x))
                self.rad_dg_range.setChecked(True)
                self._log(self.i18n.t("deglitch_e1_picked", e=f"{x:.4f}"))
            else:  # dg_e2
                self.sp_dg_e2.setValue(float(x))
                self.rad_dg_range.setChecked(True)
                self._log(self.i18n.t("deglitch_e2_picked", e=f"{x:.4f}"))
            if mode != "dg_e":
                self.energy_marks.append(x)
            self._replot()
            new_ax = self.canvas.axes.get("E")
            if new_ax is not None:
                new_ax.set_xlim(*old_xlim)
                new_ax.set_ylim(*old_ylim)
                self.canvas.draw_idle()
            # remember the set so a trailing dblclick event doesn't zoom out
            self._last_pin_click_at = time.monotonic()
            self._set_click_mode("none")
            return

        if mode == "e0":
            # E0 is set by pin + DOUBLE-click only (v0.35.1) — a single click
            # stays a plain browse/zoom gesture.
            return

        # pre/norm click-set pins were removed in v0.35.8 — the merged eye
        # toggles show/hide the window and its edges are dragged instead.
        return

    # ========== Energy ops ==========
    def _need_current(self, op_name: str) -> Optional[GroupRecord]:
        g = self.project.current
        if not g:
            self._log(f"{op_name}: " + self._bi("无当前组——先打开数据 / 选择组", "no current group — open data / select a group first"))
            QMessageBox.information(self, op_name, self.i18n.t("select_group_first"))
            return None
        return g

    def _update_smooth_rebin_ui(self, *_):
        if not hasattr(self, "smooth_params"):
            return
        smooth_on = self.rad_tool_smooth.isChecked()
        self.smooth_params.setVisible(smooth_on)
        self.rebin_params.setVisible(not smooth_on)
        self.chk_rebin_auto.setVisible(not smooth_on)

    def _park_dg_e_under(self, slot: Optional["QVBoxLayout"]) -> None:
        """Move shared E picker into *slot* (or hide if slot is None / range mode).

        Nested layouts are not the widget's parent.layout(), so we remove from
        every known slot before re-adding — otherwise E can stick under an old
        radio while also being claimed by another layout.
        """
        if not hasattr(self, "dg_e_host"):
            return
        for s in (self.dg_slot_point, self.dg_slot_before, self.dg_slot_after):
            if s.indexOf(self.dg_e_host) >= 0:
                s.removeWidget(self.dg_e_host)
        if slot is None:
            self.dg_e_host.hide()
            self.dg_e_host.setParent(self.click_deglitch_opts)
            return
        self.dg_e_host.show()
        slot.addWidget(self.dg_e_host)

    def _update_deglitch_ui(self, *_):
        if not hasattr(self, "lbl_deglitch_hint"):
            return
        click = self.rad_deglitch_click.isChecked()
        point_mode_changed = click != bool(self._deglitch_point_plot_active)
        self._deglitch_point_plot_active = click
        self.click_deglitch_opts.setVisible(click)
        if not click:
            self.lbl_deglitch_hint.setText(self.i18n.t("deglitch_hint_auto"))
            self._park_dg_e_under(None)
            if hasattr(self, "dg_range_host"):
                self.dg_range_host.hide()
            if hasattr(self, "canvas") and self.click_mode == "none":
                self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
            if point_mode_changed and hasattr(self, "canvas"):
                self._replot()
            return
        is_range = self.rad_dg_range.isChecked()
        # Park shared E host directly under the active point/before/after radio
        if is_range:
            self._park_dg_e_under(None)
        elif self.rad_dg_point.isChecked():
            self._park_dg_e_under(self.dg_slot_point)
        elif self.rad_dg_before.isChecked():
            self._park_dg_e_under(self.dg_slot_before)
        else:
            self._park_dg_e_under(self.dg_slot_after)
        if hasattr(self, "dg_range_host"):
            self.dg_range_host.setVisible(is_range)
        if self.rad_dg_before.isChecked():
            self.lbl_deglitch_hint.setText(self.i18n.t("deglitch_hint_before"))
        elif self.rad_dg_after.isChecked():
            self.lbl_deglitch_hint.setText(self.i18n.t("deglitch_hint_after"))
        elif self.rad_dg_range.isChecked():
            self.lbl_deglitch_hint.setText(self.i18n.t("deglitch_hint_range"))
        else:
            self.lbl_deglitch_hint.setText(self.i18n.t("deglitch_hint_click"))
        if hasattr(self, "canvas"):
            direct_point = self.rad_dg_point.isChecked()
            self.canvas.setCursor(
                Qt.CursorShape.CrossCursor
                if self.click_mode != "none" or direct_point
                else Qt.CursorShape.ArrowCursor
            )
        if point_mode_changed and hasattr(self, "canvas"):
            self._replot()

    def _rebin_fields(self) -> dict:
        """name -> widget for the eight Rebin fields (method = the combo)."""
        return {
            "pre1": self.sp_rebin_pre1, "pre2": self.sp_rebin_pre2,
            "pre_step": self.sp_rebin_pre_step, "xanes_step": self.sp_rebin_xanes,
            "exafs1": self.sp_rebin_exafs1, "exafs2": self.sp_rebin_exafs2,
            "exafs_kstep": self.sp_rebin_kstep, "method": self.cmb_rebin_method,
        }

    def _rebin_auto_changed(self, on: bool) -> None:
        """Auto rebin on: show rebin_group's defaults greyed (data-dependent
        ones read "auto") and keep the manual values; off: give them back."""
        import inspect

        defaults = {
            k: v.default for k, v in inspect.signature(rebin_group).parameters.items()
            if v.default is not inspect.Parameter.empty
        }
        for name, w in self._rebin_fields().items():
            is_combo = isinstance(w, QComboBox)
            if on:
                self._rebin_manual[name] = w.currentText() if is_combo else w.value()
                d = defaults.get(name)
                if is_combo:
                    w.setCurrentText(str(d or "boxcar"))
                elif d is None:  # depends on the scan: first / auto / last point
                    w.setSpecialValueText(self.i18n.t("rebin_auto"))
                    w.setValue(w.minimum())
                else:
                    w.setValue(float(d))
            else:
                if not is_combo:
                    w.setSpecialValueText("")
                if name in self._rebin_manual:
                    if is_combo:
                        w.setCurrentText(str(self._rebin_manual[name]))
                    else:
                        w.setValue(float(self._rebin_manual[name]))
            w.setEnabled(not on)

    def _rebin_kwargs_for_group(self, g: "GroupRecord") -> dict:
        e0 = None if self.chk_e0_auto.isChecked() else self.sp_e0.value()
        if e0 is None and g.session and np.isfinite(getattr(g.session.arrays, "e0", float("nan"))):
            e0 = float(g.session.arrays.e0)
        if self.chk_rebin_auto.isChecked():
            return {"e0": e0}  # automatic rebin: rebin_group's own defaults
        kw = {
            "e0": e0,
            "pre2": self.sp_rebin_pre2.value(),
            "pre_step": self.sp_rebin_pre_step.value(),
            "exafs1": self.sp_rebin_exafs1.value(),
            "exafs_kstep": self.sp_rebin_kstep.value(),
            "method": self.cmb_rebin_method.currentText(),
            "pre1": self.sp_rebin_pre1.value(),
            "xanes_step": self.sp_rebin_xanes.value(),
            "exafs2": self.sp_rebin_exafs2.value(),
        }
        return kw

    def _targets_for_tool(
        self, marked: bool = False, all_groups: bool = False
    ) -> List["GroupRecord"]:
        if all_groups:
            return self._targets_for_scope("all")
        if marked:
            return self._targets_for_scope("marked")
        g = self._need_current("Tool")
        return [g] if g else []

    def _tool_scope_log_key(self, marked: bool, all_groups: bool) -> str:
        if all_groups:
            return "op_ok_section_all"
        if marked:
            return "op_ok_section_marked"
        return "op_ok_section"

    def _op_apply_smooth_rebin(self, marked: bool = False, all_groups: bool = False):
        """Tools: Smooth or Rebin on current, marked, or all groups."""
        groups = self._targets_for_tool(marked=marked, all_groups=all_groups)
        if not groups:
            return
        n_ok = 0
        key = self._tool_scope_log_key(marked, all_groups)
        # Smooth/Rebin creates new curves; undo only deletes those (no param restore)
        ids_before = {g.id for g in self.project.groups}
        if self.rad_tool_smooth.isChecked():
            w = int(self.sp_boxcar.value())
            if w % 2 == 0:
                w += 1
                self.sp_boxcar.setValue(w)
            for g in groups:
                # smooth on the shifted energy grid, same convention as rebin
                e_shifted = np.asarray(g.energy, dtype=float) + float(g.energy_shift)
                res = smooth_mu(e_shifted, g.mu, boxcar=w)
                if not res.ok:
                    self._log(f"Smooth failed [{g.name}]: {res.message}")
                    continue
                ng = self.project.add_group(
                    g.name + f" boxcar{w}", e_shifted.copy(), res.data, path=g.path,
                    after_id=g.id,
                    datatype=g.datatype,
                    element=g.element,
                    edge=g.edge,
                    importance=g.importance,
                    is_derived=True,
                )
                ng.marked = True
                ng.settings = deepcopy(g.settings)
                ng.energy_shift = 0.0  # shift already baked into the new grid
                ng.process()
                n_ok += 1
            added = [g.id for g in self.project.groups if g.id not in ids_before]
            self._push_module_undo("smooth", [], added_ids=added)
            self._refresh_list()
            self._log(self.i18n.t(key, name=self.i18n.t("op_smooth_mode")))
            self._replot()
            return
        # Rebin (Athena/Larix 3-region parameters)
        for g in groups:
            kw = self._rebin_kwargs_for_group(g)
            res = rebin_group(g.energy + g.energy_shift, g.mu, **kw)
            if not res.ok:
                self._log(f"Rebin failed [{g.name}]: {res.message}")
                continue
            e2, m2 = res.data
            # energy already includes shift; store as absolute raw (shift=0 on new group)
            ng = self.project.add_group(
                g.name + " rebinned",
                e2,
                m2,
                path=g.path,
                after_id=g.id,
                datatype=g.datatype,
                element=g.element,
                edge=g.edge,
                importance=g.importance,
                is_derived=True,
            )
            ng.marked = True
            ng.settings = deepcopy(g.settings)
            ng.energy_shift = 0.0
            ng.process()
            n_ok += 1
            self._log(f"{g.name}: {res.message}")
        added = [g.id for g in self.project.groups if g.id not in ids_before]
        self._push_module_undo("smooth", [], added_ids=added)
        self._refresh_list()
        self._log(self.i18n.t(key, name=self.i18n.t("op_rebin_mode")))
        self._replot()

    def _op_apply_deglitch(self, marked: bool = False, all_groups: bool = False):
        """Tools: Auto deglitch, or apply click-style mask using E / E1–E2 spins."""
        key = self._tool_scope_log_key(marked, all_groups)
        if not self.rad_deglitch_click.isChecked():
            groups = self._targets_for_tool(marked=marked, all_groups=all_groups)
            if not groups:
                return
            self._push_module_undo("deglitch", groups)
            for g in groups:
                res = deglitch_auto(g.energy, g.mu, thresh=float(self.sp_dg_thresh.value()))
                if res.ok:
                    g.mu = res.data
                    g.process()
                else:
                    self._log(f"Deglitch failed [{g.name}]: {res.message}")
            self._log(self.i18n.t(key, name=self.i18n.t("op_deglitch_auto_mode")))
            self._replot()
            return

        # Click-style: use numeric E (from pin or manual entry)
        style = self._deglitch_click_style()
        # Validate energies once up front (batch must not abort midway)
        if style == "range":
            e1, e2 = float(self.sp_dg_e1.value()), float(self.sp_dg_e2.value())
            if e1 <= 0 and e2 <= 0:
                self._log(self.i18n.t("deglitch_need_energy"))
                return
            lo, hi = (e1, e2) if e1 <= e2 else (e2, e1)
        else:
            x = float(self.sp_dg_e.value())
            if x <= 0:
                self._log(self.i18n.t("deglitch_need_energy"))
                return
        groups = self._targets_for_tool(marked=marked, all_groups=all_groups)
        if not groups:
            return
        self._push_module_undo("deglitch", groups)
        for g in groups:
            if g.energy is None:
                continue
            e = np.asarray(g.energy, dtype=float) + float(g.energy_shift)
            if style == "range":
                bad = (e >= lo) & (e <= hi)
                self._apply_deglitch_mask(g, bad)
                continue
            idx = int(np.argmin(np.abs(e - x)))
            if style == "point":
                bad = np.zeros(len(e), dtype=bool)
                bad[idx] = True
                bad |= np.abs(e - e[idx]) < 0.5
                self._apply_deglitch_mask(g, bad)
            else:
                # before/after mean *truncate*: drop the points, never
                # extrapolate a fake tail line through them
                keep = (e >= e[idx]) if style == "before" else (e <= e[idx])
                self._apply_deglitch_truncate(g, keep)
        self._log(self.i18n.t(key, name=self.i18n.t("box_tools_deglitch")))
        self._replot()

    def _deglitch_click_style(self) -> str:
        if self.rad_dg_before.isChecked():
            return "before"
        if self.rad_dg_after.isChecked():
            return "after"
        if self.rad_dg_range.isChecked():
            return "range"
        return "point"

    def _apply_deglitch_mask(self, g: "GroupRecord", bad: np.ndarray) -> bool:
        e = g.energy + g.energy_shift
        res = deglitch_interp(e, g.mu, bad)
        if res.ok:
            g.mu = res.data
            g.process()
            self._log(f"{g.name}: {res.message}")
            return True
        self._log(f"{g.name}: deglitch failed — {res.message}")
        return False

    def _apply_deglitch_truncate(self, g: "GroupRecord", keep: np.ndarray) -> bool:
        """Drop points outside ``keep`` entirely (before/after styles).

        Interpolation can only fake a constant/extrapolated tail, so cutting
        the spectrum is the only defensible meaning of "remove after E".
        """
        keep = np.asarray(keep, dtype=bool)
        n_before = int(keep.size)
        n_after = int(keep.sum())
        if n_after < 10:
            self._log(f"{g.name}: trim refused — would leave only {n_after} points")
            return False
        g.energy = np.asarray(g.energy, dtype=float)[keep]
        g.mu = np.asarray(g.mu, dtype=float)[keep]
        g.process()
        self._log(f"{g.name}: trimmed {n_before} → {n_after} points")
        return True

    def _op_align(self):
        # Reference = the curve picked in the combo (defaults to current group)
        ref = None
        ref_id = (
            self.cmb_align_ref.currentData()
            if hasattr(self, "cmb_align_ref")
            else None
        )
        if ref_id:
            ref = self._find_group_by_id(str(ref_id))
        if ref is None:
            ref = self._need_current("Align")
        if not ref:
            return
        marked = [g for g in self._marked_groups_for_scope() if g.id != ref.id]
        if not marked:
            QMessageBox.information(self, "Align", self.i18n.t("align_hint"))
            self._log("Align: mark ≥1 other groups")
            return
        self._push_module_undo("energy", marked)
        n = 0
        moved = []  # (name, ΔE) — per-group feedback
        for g in marked:
            res = energy_align(
                g.energy, g.mu,
                np.asarray(ref.energy, dtype=float) + float(ref.energy_shift), ref.mu,
            )
            if res.ok:
                d = float(res.data)
                g.energy_shift += d
                g.process()
                n += 1
                moved.append((g.name, d))
                self._log(f"{g.name}: {res.message}")
        self._refresh_visible_rows()
        self._replot()
        head = self._bi(
            f"已对齐 {n} 组 → 基准 {ref.name}",
            f"Aligned {n} group(s) → ref {ref.name}",
        )
        self._log(head)
        if not moved:
            return
        # The per-group ΔE IS the result of this op — show it, don't bury it
        # in the log (cap the list; the log always has the full version).
        lines = [f"{name}   ΔE = {d:+.3f} eV" for name, d in moved[:12]]
        if len(moved) > 12:
            lines.append(self._bi(f"…共 {len(moved)} 组，详见日志", f"…{len(moved)} total, see log"))
        QMessageBox.information(
            self,
            self.i18n.t("op_align"),
            head + "\n\n" + "\n".join(lines),
        )

    @staticmethod
    def _theoretical_edge_energy(element: str, edge: str) -> Optional[float]:
        """xraydb tabulated edge energy (eV) for element/edge, or None."""
        el = str(element or "").strip().capitalize()
        ed = str(edge or "").strip().upper()
        if not el or not ed:
            return None
        try:
            from xraydb import xray_edge

            rec = xray_edge(el, ed)
            return float(rec.energy) if rec is not None else None
        except Exception:
            return None

    def _on_std_e_user_edited(self, *_args) -> None:
        # valueChanged fires for code and UI edits alike; programmatic fills
        # run under self._block (group load) or reset the flag explicitly
        # (calibrate), so anything outside _block is genuine user input.
        if not self._block:
            self._std_e_user_set = True

    def _prefill_std_e(self) -> None:
        """Std E0 follows the selected element/edge (v0.35.17).

        Previously this only filled an *empty* field, so after filtering the
        group list by element the field kept showing the first "all" file's
        edge energy.  Now an auto/theory value tracks element+edge on every
        group load.  A value the user typed (``_std_e_user_set``) is kept —
        it may be a measured foil energy.  Clearing the field to 0 re-arms
        the auto-fill.
        """
        if not hasattr(self, "sp_std_e"):
            return
        cur = float(self.sp_std_e.value() or 0.0)
        if cur > 0 and self._std_e_user_set:
            return
        th = self._theoretical_edge_energy(
            self.cmb_element.currentText(), self.cmb_edge.currentText()
        )
        if th and abs(cur - th) > 0.05:
            self.sp_std_e.setValue(th)
        self._std_e_user_set = False

    def _op_calibrate(self):
        """Calibrate current spectrum E0 → known energy.

        Applies to the current group only; push the same ΔE further with the
        Apply ΔE scope row (Current / Marked / All)."""
        g = self._need_current("Calibrate")
        if not g:
            return
        # Reference energy priority: Std E0 field → xraydb theory for the
        # selected element/edge (filled into the field so the user sees it)
        # → manual E0 field → prompt.
        known = float(self.sp_std_e.value() or 0.0)
        if known <= 0:
            th = self._theoretical_edge_energy(
                self.cmb_element.currentText(), self.cmb_edge.currentText()
            )
            if th:
                known = th
                self.sp_std_e.setValue(th)
                self._std_e_user_set = False  # theory auto-fill, not user input
                self._log(
                    self._bi(
                        f"标准值取理论边能 {self.cmb_element.currentText().strip()} "
                        f"{self.cmb_edge.currentText().strip()} = {th:.2f} eV",
                        f"Std = theoretical edge {self.cmb_element.currentText().strip()} "
                        f"{self.cmb_edge.currentText().strip()} = {th:.2f} eV",
                    )
                )
        if known <= 0 and not self.chk_e0_auto.isChecked():
            known = float(self.sp_e0.value() or 0.0)
        if known <= 0:
            known, ok = QInputDialog.getDouble(
                self,
                self.i18n.t("op_calibrate"),
                self.i18n.t("calibrate_prompt"),
                float(known or 7112.0),
                0,
                120000,
                3,
            )
            if not ok:
                self._log("Calibrate cancelled")
                return
        # A pinned E0 (pin + double-click, Auto off) is the calibration
        # reference: shift = standard − the USER's picked point (v0.35.13).
        # Re-detecting the edge here discarded their pick — the E0 looked
        # like it "reverted to the initial value".  Only auto-E0 groups (no
        # manual pick) use find_e0 detection.
        if not self.chk_e0_auto.isChecked() and float(self.sp_e0.value() or 0.0) > 0:
            picked = float(self.sp_e0.value())
            res = OpResult(True, f"picked e0={picked:.3f}, shift={known - picked:.3f}", known - picked)
        else:
            res = calibrate_e0(g.energy + g.energy_shift, g.mu, known)
        if not res.ok:
            self._log(f"Calibrate failed: {res.message}")
            return
        self._push_module_undo("energy", [g])
        delta = float(res.data)
        g.energy_shift += delta
        self._last_cal_delta = delta
        self._last_cal_group_id = g.id
        self.sp_eshift.setValue(g.energy_shift)
        self.chk_e0_auto.setChecked(False)
        self.sp_e0.setValue(known)
        g.settings.e0 = known
        g.process()
        self._log(self.i18n.t("op_ok_calibrate", d=f"{delta:+.4f}"))
        self._refresh_visible_rows()
        # Calibration shifts all absolute energies by delta — keep the same
        # feature in view instead of zooming out to the full range (v0.35.0)
        self._preserve_plot_zoom(self._replot, shift_x=delta)

    def _op_apply_shift_delta(self, scope: str = "marked"):
        """Sync ΔE (absolute energy_shift) across the chosen scope.

        SET semantics, not addition: every target's energy_shift becomes the
        reference value — the calibrated group's actual shift when a calibrate
        just ran, otherwise a prompted absolute value. Idempotent: applying
        twice changes nothing, and a group that was accidentally shifted twice
        is repaired. (The old additive version double-shifted the calibrated
        standard itself when it was inside the scope.)"""
        targets = self._targets_for_scope(scope)
        if not targets:
            QMessageBox.information(
                self, self.i18n.t("btn_apply_shift_marked"), self.i18n.t("mark_groups_first")
            )
            return
        ref = (
            self._find_group_by_id(self._last_cal_group_id)
            if self._last_cal_group_id
            else None
        )
        if ref is not None:
            # Sync to the calibrated group's actual total shift
            value = float(ref.energy_shift)
        else:
            # No calibrate this session: prompt for the absolute target value
            cur = self.project.current
            default = float(cur.energy_shift) if cur is not None else 0.0
            value, ok = QInputDialog.getDouble(
                self,
                self.i18n.t("btn_apply_shift_marked"),
                self.i18n.t("eshift_prompt"),
                default,
                -10000,
                10000,
                2,
            )
            if not ok:
                return
        value = float(value)
        changed = [
            g for g in targets if abs(float(g.energy_shift) - value) > 1e-9
        ]
        if not changed:
            self._log(self.i18n.t("op_ok_shift_marked", n=0, d=f"{value:+.4f}"))
            return
        self._push_module_undo("energy", list(changed))
        for g in changed:
            g.energy_shift = value
            g.process()
        self._refresh_visible_rows()
        self._log(self.i18n.t("op_ok_shift_marked", n=len(changed), d=f"{value:+.4f}"))
        self._replot()

    def _find_group_by_id(self, gid: str) -> Optional[GroupRecord]:
        for g in self.project.groups:
            if g.id == gid:
                return g
        return None

    def _analysis_get_xy(self, g: GroupRecord, arrayname: str = "norm"):
        """Return (x, y) for analysis.

        Energy-space arrays use absolute eV. χ(k) returns (k, χ) so larch can
        store k on Group.energy for lincombo/pca.
        """
        arrayname = (arrayname or "norm").lower()
        stored_kind = str(getattr(g, "datatype", "xmu") or "xmu").lower()
        # Derived analysis curves are not raw absorption spectra.  Preserve
        # their native axis/value semantics instead of sending k/chi or dmu/dE
        # through pre_edge/AUTOBK as if they were energy/mu.
        if g.is_derived and g.energy is not None and g.mu is not None:
            x_native = np.asarray(g.energy, dtype=float).ravel()
            y_native = np.asarray(g.mu, dtype=float).ravel()
            n_native = min(x_native.size, y_native.size)
            if n_native >= 5:
                x_native, y_native = x_native[:n_native], y_native[:n_native]
                if stored_kind == arrayname:
                    return x_native.copy(), y_native.copy()
                if stored_kind == "chi" and arrayname in ("chi", "kchi", "k2chi", "k3chi"):
                    power = {"chi": 0, "kchi": 1, "k2chi": 2, "k3chi": 3}[arrayname]
                    return x_native.copy(), y_native * (x_native ** power)
                if stored_kind in ("mu", "xmu") and arrayname == "mu":
                    return x_native.copy(), y_native.copy()

        try:
            g.process()
        except Exception:
            pass
        a = g.session.arrays if g.session else None
        if arrayname == "mu":
            if g.energy is None or g.mu is None:
                return None, None
            e = np.asarray(g.energy, dtype=float) + float(g.energy_shift or 0.0)
            return e, np.asarray(g.mu, dtype=float)
        if arrayname in ("chi", "kchi", "k2chi", "k3chi"):
            if a is None or a.k is None or a.chi is None:
                return None, None
            k = np.asarray(a.k, dtype=float).ravel()
            chi = np.asarray(a.chi, dtype=float).ravel()
            n = min(k.size, chi.size)
            if n < 5:
                return None, None
            k, chi = k[:n], chi[:n]
            if arrayname == "kchi":
                chi = chi * k
            elif arrayname == "k2chi":
                chi = chi * (k ** 2)
            elif arrayname == "k3chi":
                chi = chi * (k ** 3)
            return k, chi
        if a is None or a.energy is None:
            if g.energy is None or g.mu is None:
                return None, None
            e = np.asarray(g.energy, dtype=float) + float(g.energy_shift or 0.0)
            return e, np.asarray(g.mu, dtype=float)
        e = np.asarray(a.energy, dtype=float)
        if arrayname == "dmude":
            y = getattr(a, "dmude", None)
            if y is None and a.mu is not None:
                y = np.gradient(np.asarray(a.mu, dtype=float), e)
            if y is None:
                return None, None
            return e, np.asarray(y, dtype=float)
        # default: norm
        y = a.norm
        if y is None:
            return None, None
        return e, np.asarray(y, dtype=float)

    def _analysis_fit_window(self, g: GroupRecord) -> tuple:
        """Default LCF/PCA energy window: XANES-style E0−20 … E0+60 eV (absolute)."""
        e, _ = self._analysis_get_xy(g, "norm")
        e0 = None
        if g.session and g.session.arrays and np.isfinite(getattr(g.session.arrays, "e0", float("nan"))):
            e0 = float(g.session.arrays.e0)
        elif g.settings.e0 is not None:
            e0 = float(g.settings.e0)
        if e0 is None or not np.isfinite(e0):
            if e is not None and e.size:
                return float(np.nanmin(e)), float(np.nanmax(e))
            return None, None
        # Prefer Athena/XANES LCF-PCA window (not full pre→post-edge span)
        from .athena_ops import xanes_default_window

        xmin, xmax = xanes_default_window(e0, lo_rel=-20.0, hi_rel=60.0)
        if e is not None and e.size:
            xmin = max(xmin, float(np.nanmin(e)))
            xmax = min(xmax, float(np.nanmax(e)))
        return xmin, xmax

    def _show_analysis_result(self, title: str, report: str, overlay: Optional[dict] = None) -> None:
        """Log + central report panel + optional E-plot overlay (no modal spam)."""
        self._log((report.splitlines()[0] if report else title) + f"  [{title}]")
        self._set_fit_result_text(report)
        self._analysis_overlay = overlay
        self._replot()

    def _show_wavelet_result(self, report: str, overlay: Optional[dict] = None) -> None:
        """Wavelet dialog result: log + report pane; the WT view follows.

        v4.2.0: the main WT view always shows the CURRENT group, computed with
        the Wavelet tool's saved settings (``_wt_for_group``).  "Show on main
        plot" therefore just selects that group and switches to WT; a plain Run
        (which saved new settings) refreshes the WT view if it is open.
        Deliberately does NOT touch ``_analysis_overlay`` (1-D E/k/R/q curves).
        """
        self._log((report.splitlines()[0] if report else "Wavelet") + "  [WT]")
        self._set_fit_result_text(report)
        gid = str((overlay or {}).get("group_id") or "")
        if overlay and overlay.get("focus"):
            if gid:
                self._select_group_in_list(gid, replot=False)
            self._focus_panel("wt")
        elif getattr(self.canvas, "mode", "") == "wt":
            self._replot()

    def _select_group_in_list(self, gid: str, replot: bool = True) -> None:
        """Make *gid* the current group as if clicked in the Groups list (used
        by the Wavelet tool so both sides select the same curve)."""
        g = self._find_group_by_id(gid)
        if g is None or (self.project.current is not None and self.project.current.id == gid):
            if replot and getattr(self.canvas, "mode", "") == "wt":
                self._replot()
            return
        for i in range(self.list_groups.count()):
            item = self.list_groups.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == gid:
                self.list_groups.setCurrentItem(item)  # -> _group_selected (replots)
                return
        # hidden by the element filter: still make it current
        self.project.set_current(gid)
        self._load_group_to_controls(g)
        self._update_meta()
        if replot:
            self._replot()

    def _wt_for_group(self, g) -> tuple:
        """(result, why) - |W| of *g*'s chi(k) for the main WT view.

        Same rule as the Wavelet tool and the WT export: kmin/kmax/dk from the
        group's own k, everything else (k weight, R window, dR, wavelet shape)
        from the tool's saved settings (QSettings ``wt/*``).  Cached per group,
        data and settings, so clicking back and forth does not recompute.
        """
        from . import wavelet as wt_kernel

        k, chi = self._analysis_get_xy(g, "chi")
        if k is None or chi is None or len(np.asarray(k).ravel()) < 2:
            return None, "This group has no chi(k) yet: check E0 / normalization / background"
        k = np.asarray(k, dtype=float).ravel()
        chi = np.asarray(chi, dtype=float).ravel()
        try:
            params = wt_kernel.auto_params(k)
        except ValueError as exc:
            return None, str(exc)
        params.update(wt_stored_params(self.store))
        digest = hashlib.sha1(k.tobytes() + chi.tobytes()).hexdigest()
        key = (g.id, digest, tuple(sorted((p, repr(v)) for p, v in params.items())))
        cache = self.__dict__.setdefault("_wt_cache", {})
        if key in cache:
            return cache[key], ""
        try:
            k_grid, R_grid, W = wt_kernel.cwt(k, chi, **params)
        except ValueError as exc:
            return None, f"Wavelet settings do not fit this group: {exc}"
        result = {
            "kind": "wt", "group_id": g.id, "name": g.name, "title": f"WT - {g.name}",  # ASCII: canvas text (draw_wt maps non-ASCII to ?)
            "k_grid": k_grid, "R_grid": R_grid, "W": W,
            "kmin": params["kmin"], "kmax": params["kmax"], "params": dict(params),
        }
        if len(cache) >= 24:  # small LRU-ish bound: drop the oldest entry
            cache.pop(next(iter(cache)))
        cache[key] = result
        return result, ""

    def _draw_wt_panel(self) -> None:
        """Paint the stored |W| into the full-canvas WT view (read-only)."""
        ax = self.canvas.axes.get("wt")
        if ax is None:
            return
        from . import wavelet as wt_kernel

        face = getattr(self.canvas, "_axes_face", "white")
        g = self.project.current
        result, why = self._wt_for_group(g) if g is not None else (None, "")
        if not result:
            for stale in [a for a in list(self.canvas.fig.axes) if a is not ax]:
                try:
                    stale.remove()
                except Exception:
                    pass
            ax.clear()
            ax.set_facecolor(face)
            ax.set_xticks([])
            ax.set_yticks([])
            # Canvas text stays English like every other plot in the app —
            # matplotlib's default font has no CJK glyphs (zh renders as tofu).
            ax.text(
                0.5, 0.5, why or "Select a group in the list: the WT view follows it",
                ha="center", va="center", transform=ax.transAxes, fontsize=10, wrap=True,
            )
            self.canvas.draw_idle()
            return
        ax.clear()  # draw_wt replaces only its own colour bar, not the axes
        wt_kernel.draw_wt(
            ax,
            result["k_grid"],
            result["R_grid"],
            result["W"],
            kmin=result.get("kmin"),
            kmax=result.get("kmax"),
            title=str(result.get("title") or ""),
            fontsize=14,  # full-panel view: default 10 pt reads tiny here
        )
        ax.set_facecolor(face)
        self.canvas.draw_idle()

    def _op_wavelet(self):
        """Open the Wavelet (CWT) dialog: groups + parameters → Run → Show / Export."""
        if not self.project.groups:
            QMessageBox.information(
                self, self.i18n.t("op_wavelet"), self._bi("请先载入谱", "Load spectra first")
            )
            return
        exp = self._data_export_dir()
        dlg = WaveletDialog(
            self,
            self.project.groups,
            current_id=self.project.current_id,
            marked_ids=[g.id for g in self._marked_groups_for_scope()],
            find_group=self._find_group_by_id,
            get_xy=self._analysis_get_xy,
            bi=self._bi,
            default_export_dir=exp,
            store=self.store,
        )
        dlg.set_result_callback(self._show_wavelet_result)
        # picking a group in the tool's "Heatmap shows" selects it here too
        dlg.set_select_callback(lambda gid: self._select_group_in_list(gid))
        dlg.exec()

    def _op_lcf(self):
        """Open LCF dialog: user picks data + standards + params, then Run / Export."""
        if not self.project.groups:
            QMessageBox.information(self, "LCF", self._bi("请先载入谱", "Load spectra first"))
            return
        exp = self._data_export_dir()
        dlg = LCFDialog(
            self,
            self.project.groups,
            current_id=self.project.current_id,
            marked_ids=[g.id for g in self._marked_groups_for_scope()],
            find_group=self._find_group_by_id,
            get_xy=self._analysis_get_xy,
            default_window=self._analysis_fit_window,
            bi=self._bi,
            default_export_dir=exp,
        )
        dlg.set_result_callback(lambda report, ov: self._show_analysis_result("LCF", report, ov))
        dlg.exec()

    def _op_pca(self):
        """Open PCA dialog: user picks training set + ncomp + window, then Run / Export."""
        if len(self.project.groups) < 2:
            QMessageBox.information(self, "PCA", self.i18n.t("mark_two_groups"))
            return
        exp = self._data_export_dir()
        dlg = PCADialog(
            self,
            self.project.groups,
            current_id=self.project.current_id,
            marked_ids=[g.id for g in self._marked_groups_for_scope()],
            find_group=self._find_group_by_id,
            get_xy=self._analysis_get_xy,
            default_window=self._analysis_fit_window,
            bi=self._bi,
            default_export_dir=exp,
        )
        dlg.set_result_callback(lambda report, ov: self._show_analysis_result("PCA", report, ov))
        dlg.exec()

    def _op_mcr(self):
        """Open MCR-ALS dialog (pyMCR) under PCA: series → pure spectra + C."""
        if len(self.project.groups) < 2:
            QMessageBox.information(self, "MCR-ALS", self.i18n.t("mark_two_groups"))
            return
        exp = self._data_export_dir()
        dlg = MCRDialog(
            self,
            self.project.groups,
            current_id=self.project.current_id,
            marked_ids=[g.id for g in self._marked_groups_for_scope()],
            find_group=self._find_group_by_id,
            get_xy=self._analysis_get_xy,
            default_window=self._analysis_fit_window,
            bi=self._bi,
            default_export_dir=exp,
        )
        dlg.set_result_callback(lambda report, ov: self._show_analysis_result("MCR-ALS", report, ov))
        dlg.set_write_groups_callback(self._mcr_write_pure_groups)
        dlg.exec()

    def _mcr_write_pure_groups(self, payload: dict) -> int:
        """Write MCR pure spectra into Groups as derived standards (full chain)."""
        pures = payload.get("pure_groups") or []
        if not pures:
            return 0
        # Template metadata from first series spectrum name if present
        template: Optional[GroupRecord] = None
        names = payload.get("names") or []
        if names:
            for g in self.project.groups:
                if g.name == names[0]:
                    template = g
                    break
        if template is None and self.project.groups:
            template = self.project.groups[0]

        ids_before = {g.id for g in self.project.groups}
        n_ok = 0
        stamp = time.strftime("%H%M%S")
        arrayname = str(payload.get("arrayname") or "norm").lower()
        ncomp = int(payload.get("ncomp") or len(pures))

        for i, pure in enumerate(pures):
            x = np.asarray(pure.get("x"), dtype=float).ravel()
            y = np.asarray(pure.get("y"), dtype=float).ravel()
            n = min(x.size, y.size)
            if n < 5:
                continue
            x, y = x[:n].copy(), y[:n].copy()
            base = str(pure.get("name") or f"Pure{i + 1}")
            gname = f"MCR_{base}_n{ncomp}_{stamp}"
            # energy + y stored as mu; note records that this is an analysis pure
            kw = dict(
                path="",
                is_derived=True,
                note=str(pure.get("note") or f"MCR-ALS pure ({arrayname})"),
                datatype="xmu" if arrayname == "mu" else arrayname,
            )
            if template is not None:
                kw.update(
                    element=template.element,
                    edge=template.edge,
                    importance=template.importance,
                )
            ng = self.project.add_group(gname, x, y, **kw)
            ng.marked = True
            ng.energy_shift = 0.0
            if template is not None:
                try:
                    ng.settings = deepcopy(template.settings)
                except Exception:
                    pass
            # Only a true mu(E) pure is eligible for Athena processing.  Norm,
            # dmu/dE and chi curves remain native derived arrays and are read
            # directly by _analysis_get_xy above.
            if arrayname == "mu":
                try:
                    ng.process()
                except Exception as exc:
                    self._log(f"MCR pure process warn [{gname}]: {exc}")
            n_ok += 1

        added = [g.id for g in self.project.groups if g.id not in ids_before]
        if added:
            try:
                self._push_module_undo("mcr_pures", [], added_ids=added)
            except Exception:
                pass
        self._refresh_list()
        self._log(
            self._bi(
                f"MCR-ALS: 已写入 {n_ok} 条纯相谱到 Groups",
                f"MCR-ALS: wrote {n_ok} pure spectrum group(s)",
            )
        )
        self._replot()
        return n_ok

    def _op_peaks(self):
        """Open pre-edge dialog: pick spectrum + windows, then Run / Export."""
        if not self.project.groups:
            QMessageBox.information(self, "Pre-edge", self._bi("请先载入谱", "Load spectra first"))
            return
        exp = self._data_export_dir()
        dlg = PrepeakDialog(
            self,
            self.project.groups,
            current_id=self.project.current_id,
            find_group=self._find_group_by_id,
            bi=self._bi,
            default_export_dir=exp,
        )
        dlg.set_result_callback(
            lambda report, ov: self._show_analysis_result("Pre-edge peaks", report, ov)
        )
        dlg.exec()

    def _export_kinds_selected(self) -> List[str]:
        checks = getattr(self, "_export_kind_checks", {})
        return [key for key, chk in checks.items() if chk.isChecked() and key in EXPORT_KINDS]

    def _export_kinds_set_all(self, on: bool) -> None:
        for chk in getattr(self, "_export_kind_checks", {}).values():
            chk.setChecked(bool(on))

    def _data_export_dir(self) -> Path:
        """Shared folder for Tools→Export curves and Analysis (LCF/PCA/pre-edge) results.

        Configured in the Tools→Export panel (QSettings ``export_dir``); default ``output/export``.
        """
        raw = str(self.store.value("export_dir", "") or "").strip()
        d = Path(raw) if raw else (DEFAULT_OUTPUT / "export")
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            d = DEFAULT_OUTPUT / "export"
            d.mkdir(parents=True, exist_ok=True)
        return d

    def _set_data_export_dir(self, path: str | Path, *, log: bool = True) -> Path:
        """Persist export folder (Tools→Export panel is the UI for this path)."""
        d = Path(str(path).strip() or str(DEFAULT_OUTPUT / "export"))
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            d = DEFAULT_OUTPUT / "export"
            d.mkdir(parents=True, exist_ok=True)
        self.store.setValue("export_dir", str(d))
        if log:
            self._log(self._bi(f"导出目录 → {d}", f"Export folder → {d}"))
        return d

    def _export_dir_edited(self) -> None:
        d = self._set_data_export_dir(self.ed_export_dir.text(), log=False)
        self.ed_export_dir.setText(str(d))

    def _browse_export_dir(self) -> None:
        start = self.ed_export_dir.text().strip() or str(DEFAULT_OUTPUT / "export")
        selected = _pick_directory(
            self,
            self.i18n.t("settings_data_export_folder"),
            start,
        )
        if selected:
            d = self._set_data_export_dir(selected, log=True)
            self.ed_export_dir.setText(str(d))

    def _open_export_dir(self) -> None:
        self._export_dir_edited()
        d = self._data_export_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d.resolve())))

    def _export_name_affix_edited(self) -> None:
        """Persist custom export file-name prefix/suffix (Tools→Export panel)."""
        self.store.setValue("export_name_prefix", self.ed_export_prefix.text().strip())
        self.store.setValue("export_name_suffix", self.ed_export_suffix.text().strip())

    def _export_batch_basename(self, stamp: str) -> str:
        """``[<prefix>_]export_<stamp>[_<suffix>]`` — affixes sanitized, empty = omitted."""
        pre_txt = self.ed_export_prefix.text().strip() if hasattr(self, "ed_export_prefix") else ""
        suf_txt = self.ed_export_suffix.text().strip() if hasattr(self, "ed_export_suffix") else ""
        pre = _safe_filename(pre_txt, max_len=40) if pre_txt else ""
        suf = _safe_filename(suf_txt, max_len=40) if suf_txt else ""
        return f"{pre + '_' if pre else ''}export_{stamp}{'_' + suf if suf else ''}"

    def _op_export(self) -> None:
        """One-click batch export of current/marked curves to CSV or Excel."""
        kinds = self._export_kinds_selected()
        if not kinds:
            QMessageBox.information(self, self.i18n.t("box_tools_export"), self.i18n.t("export_need_kinds"))
            self._log(self.i18n.t("export_need_kinds"))
            return
        try:
            out_dir = self._data_export_dir()
        except OSError as exc:
            QMessageBox.warning(
                self, self.i18n.t("box_tools_export"), self.i18n.t("export_need_folder") + f"\n{exc}"
            )
            return
        self._set_data_export_dir(out_dir, log=False)

        if self.rad_export_current.isChecked():
            g = self.project.current
            groups = [g] if g else []
        else:
            groups = list(self._marked_groups_for_scope())
            if not groups and self.project.current:
                # fall back to current if nothing marked
                groups = [self.project.current]
        if not groups:
            QMessageBox.information(self, self.i18n.t("box_tools_export"), self.i18n.t("export_need_groups"))
            self._log(self.i18n.t("export_need_groups"))
            return
        self._flush_panel_to_current_group()
        cur_id = self.project.current_id

        n_g = len(groups)
        # Process phase + write phase (write progress comes from export_* callbacks)
        self._progress_begin(
            self.i18n.t("progress_export_working", n=n_g),
            maximum=n_g + 1,
            window_title=self.i18n.t("progress_export_title"),
        )
        cancelled = False
        res = None
        target_path = ""
        try:
            # Ensure processed arrays exist for export (with live progress)
            for i, g in enumerate(groups):
                if not self._progress_update(
                    i,
                    self.i18n.t(
                        "progress_export_process",
                        i=i + 1,
                        n=n_g,
                        name=g.name,
                    ),
                ):
                    cancelled = True
                    break
                if g.frozen:
                    continue
                need = g.session is None or g.session.arrays is None or g.session.arrays.energy is None
                if need or g.id == cur_id:
                    g.process()
                elif any(k != "mu" for k in kinds) and (
                    g.session.arrays.norm is None and g.session.arrays.k is None
                ):
                    g.process()

            if cancelled:
                self._log(self.i18n.t("progress_export_cancelled"))
                return

            stamp = time.strftime("%Y%m%d_%H%M%S")
            self._export_name_affix_edited()  # persist prefix/suffix for next run
            batch_base = self._export_batch_basename(stamp)
            # Write phase: map exporter progress onto the last dialog slots
            write_base = n_g  # first write tick lands at n_g / (n_g+1)

            def _write_progress(cur: int, total: int, label: str) -> bool:
                # cur/total from exporter → value in [n_g, n_g]… then finish
                t = max(1, int(total))
                c = max(0, min(int(cur), t))
                # leave one step for "done"
                value = write_base if t <= 1 else write_base
                # Use indeterminate-style label updates; keep max = n_g+1
                # and set value to n_g while writing, n_g+1 on complete.
                val = write_base if c < t else write_base + 1
                return self._progress_update(
                    val,
                    self.i18n.t("progress_export_write", detail=label),
                    maximum=n_g + 1,
                )

            if not self._progress_update(
                write_base,
                self.i18n.t("progress_export_write", detail="…"),
            ):
                self._log(self.i18n.t("progress_export_cancelled"))
                return

            # Notes page (v3.19.5): the processing record travels with the
            # export - first sheet of every Excel workbook, or
            # processing_notes.csv next to the CSVs.
            record = getattr(self.project, "processing_record", None)
            notes_rows = list(record.to_rows()) if record is not None else None
            notes_lines = (
                # header lines only: the parameter table follows on the same sheet
                record.to_lines(language=self.i18n.lang, with_rows=False)
                if record is not None else None
            )
            try:
                if self.rad_export_csv.isChecked():
                    batch_dir = out_dir / batch_base
                    res = export_groups_csv(
                        groups, batch_dir, kinds, progress_cb=_write_progress,
                        notes_rows=notes_rows, notes_lines=notes_lines,
                    )
                    target_path = str(batch_dir)
                else:
                    xlsx_path = out_dir / f"{batch_base}.xlsx"
                    res = export_groups_xlsx(
                        groups, xlsx_path, kinds, progress_cb=_write_progress,
                        notes_rows=notes_rows, notes_lines=notes_lines,
                    )
                    written = (
                        list(res.data)
                        if res is not None and isinstance(res.data, list)
                        else []
                    )
                    if len(written) == 1:
                        target_path = written[0]
                    else:
                        target_path = str(out_dir)
            except Exception as exc:
                tb = traceback.format_exc()
                self._log(f"Export error: {exc}\n{tb}")
                QMessageBox.warning(
                    self,
                    self.i18n.t("box_tools_export"),
                    self.i18n.t("export_fail", msg=str(exc)),
                )
                return
        finally:
            self._progress_end()

        if res is None:
            return
        if not res.ok:
            if "cancel" in str(res.message).lower():
                self._log(self.i18n.t("progress_export_cancelled"))
                return
            self._log(self.i18n.t("export_fail", msg=res.message))
            QMessageBox.warning(
                self, self.i18n.t("box_tools_export"), self.i18n.t("export_fail", msg=res.message)
            )
            return
        n = len(res.data) if isinstance(res.data, list) else 0
        msg = self.i18n.t("export_ok", n=n, path=target_path)
        want_wt_data = (
            getattr(self, "chk_export_wavelet", None) is not None
            and self.chk_export_wavelet.isChecked()
        )
        want_wt_png = (
            getattr(self, "chk_export_wavelet_png", None) is not None
            and self.chk_export_wavelet_png.isChecked()
        )
        if want_wt_data or want_wt_png:
            # Data follows the main format choice (v2.17.0): CSV mode writes
            # wt_<group>.csv into the batch folder; XLSX mode writes a single
            # <batch>_wt.xlsx (element sheets) next to the curve workbook.
            csv_mode = self.rad_export_csv.isChecked()
            wt_dir = (out_dir / batch_base) if csv_mode else out_dir
            msg += self._export_wavelet_sidecars(
                groups,
                wt_dir,
                write_data=want_wt_data,
                write_png=want_wt_png,
                xlsx_path=None if csv_mode else out_dir / f"{batch_base}_wt.xlsx",
            )
        self._log(f"{msg}  ({res.message})")
        QMessageBox.information(self, self.i18n.t("box_tools_export"), msg)

    def _export_wavelet_sidecars(
        self, groups, wt_dir, *, write_data=True, write_png=True, xlsx_path=None
    ) -> str:
        """Batch CWT next to a curve export.

        Data files follow the main CSV/Excel choice (v2.17.0): CSV mode
        (*xlsx_path* = ``None``) writes one ``wt_<group>.csv`` per group into
        *wt_dir*; Excel mode writes a single workbook at *xlsx_path* with one
        sheet per element and that element's groups side-by-side. Heatmap PNGs
        are an independent opt-in (``wt_<group>.png`` in *wt_dir*). The two
        opt-ins are the Export-box checkboxes (v2.16.14): the caller only
        invokes this when at least one is on. Parameters come from the Wavelet
        dialog's last saved set (QSettings ``wt/*``); kmin/kmax/dk are always
        re-derived from each group's own data (spec §3). A group without χ(k),
        or whose k range does not cover the window, is skipped and named in the
        completion message — never silently dropped (§B.8).

        Runs on the GUI thread and pumps the progress dialog per group: the kernel
        is pure numpy (no larch), so there is no thread-safety constraint, and a
        per-group pump is the documented alternative to ``_run_with_busy``.
        """
        from . import wavelet as wt_kernel

        wt_dir = Path(wt_dir)
        try:
            wt_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            note = self.i18n.t("export_wt_fail", msg=str(exc))
            self._log(note)
            return f"\n{note}"
        stored = wt_stored_params(self.store)
        total = len(groups)
        self._progress_begin(
            self.i18n.t("progress_export_wavelet", i=0, n=total, name=""),
            maximum=total + 1,
            window_title=self.i18n.t("progress_export_wavelet_title"),
        )
        done = []  # (group, k_grid, R_grid, W) for every successful CWT
        skipped: List[str] = []
        failures: List[str] = []
        cancelled = False
        try:
            for i, g in enumerate(groups):
                if not self._progress_update(
                    i, self.i18n.t("progress_export_wavelet", i=i + 1, n=total, name=g.name)
                ):
                    cancelled = True
                    break
                k, chi = self._analysis_get_xy(g, "chi")
                if k is None or chi is None or len(np.asarray(k).ravel()) < 2:
                    skipped.append(g.name)
                    continue
                params = wt_kernel.auto_params(k)
                params.update(stored)
                stem = f"wt_{wt_safe_name(g.name)}"
                try:
                    k_grid, R_grid, W = wt_kernel.cwt(k, chi, **params)
                    if write_png:
                        wt_kernel.save_wt_png(
                            wt_dir / f"{stem}.png", k_grid, R_grid, W,
                            params["kmin"], params["kmax"], title=g.name,
                        )
                except Exception as exc:
                    failures.append(f"{g.name}: {exc}")
                    continue
                done.append((g, k_grid, R_grid, W))
            if not cancelled and write_data and done:
                self._progress_update(
                    total,
                    self.i18n.t("progress_export_wavelet", i=total, n=total, name="…"),
                )
                try:
                    if xlsx_path is None:
                        for g, k_grid, R_grid, W in done:
                            wt_kernel.write_wt_csv(
                                wt_dir / f"wt_{wt_safe_name(g.name)}.csv",
                                k_grid, R_grid, W,
                            )
                    else:
                        grids = {id(g): (k, R, W) for g, k, R, W in done}
                        sheets = [
                            (elem, [(g.name, *grids[id(g)]) for g in gsub])
                            for elem, gsub in _partition_groups_by_element(
                                [g for g, *_ in done]
                            )
                        ]
                        wt_kernel.write_wt_xlsx(str(xlsx_path), sheets)
                except Exception as exc:
                    failures.append(str(exc))
        finally:
            self._progress_end()
        data_kind = "wt_*.csv" if xlsx_path is None else Path(xlsx_path).name
        kinds = " + ".join(
            part for part, on in ((data_kind, write_data), ("wt_*.png", write_png)) if on
        )
        notes: List[str] = []
        if done:
            notes.append(self.i18n.t("export_wt_done", n=len(done), kinds=kinds, path=str(wt_dir)))
        if skipped:
            shown = ", ".join(skipped[:8]) + ("…" if len(skipped) > 8 else "")
            notes.append(self.i18n.t("export_wt_skipped", n=len(skipped), names=shown))
        notes.extend(self.i18n.t("export_wt_fail", msg=line) for line in failures)
        for note in notes:
            self._log(note)
        return ("\n" + "\n".join(notes)) if notes else ""

    def _op_merge(self):
        """Smart merge (v2.19.0): auto-group the marked curves by sample /
        channel / element / edge, let the user adjust the grouping in a
        preview table, then merge each group separately.

        Replaces the old one-click "average everything marked into one",
        which silently mixed different samples, edges and trans/fluo.
        """
        gs = self._marked_groups_for_scope()
        if len(gs) < 2:
            QMessageBox.information(self, self.i18n.t("op_merge"), self.i18n.t("mark_two_groups"))
            self._log("Merge: mark ≥2 groups")
            return
        plan = self._merge_plan_dialog(gs)
        if plan is None:
            self._log(self.i18n.t("merge_cancelled"))
            return
        self._merge_by_plan(plan["groups"], keep_originals=plan["keep"], use_norm=plan["use_norm"])

    def _merge_suggestion(self, gs: List[GroupRecord]) -> List[dict]:
        items = []
        for g in gs:
            e = np.asarray(g.energy, dtype=float) + float(g.energy_shift or 0.0)
            items.append({
                "id": g.id, "name": g.name, "element": g.element, "edge": g.edge,
                "erange": (float(np.nanmin(e)), float(np.nanmax(e))) if e.size else (0.0, 0.0),
            })
        return suggest_merge_groups(items)

    def _merge_plan_dialog(self, gs: List[GroupRecord]) -> Optional[dict]:
        """Preview table: one row per marked curve, its proposed merge group
        (editable combo; "— don't merge —" excludes it), live summary, and
        the options Keep originals / Merge on μ or norm.

        Returns ``{"groups": [(label, [GroupRecord, ...]), ...], "keep",
        "use_norm"}`` with only groups of ≥ 2 curves, or None on cancel.
        """
        t = self.i18n.t
        skip = t("merge_skip")
        by_id = {g.id: g for g in gs}
        dlg = QDialog(self)
        dlg.setWindowTitle(t("merge_title"))
        dlg.setMinimumSize(560, 360)
        dlg.resize(720, 480)
        dlg.setSizeGripEnabled(True)
        lay = QVBoxLayout(dlg)
        hint = QLabel(t("merge_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b625d; font-size:11px;")
        lay.addWidget(hint)
        tbl = QTableWidget(0, 3)
        tbl.setObjectName("mergePlanTable")
        tbl.setHorizontalHeaderLabels([t("merge_col_curve"), t("merge_col_edge"), t("merge_col_group")])
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.verticalHeader().setVisible(False)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(tbl, 1)
        summary = QLabel()
        summary.setObjectName("mergePlanSummary")
        summary.setWordWrap(True)
        lay.addWidget(summary)
        opt = QHBoxLayout()
        chk_keep = QCheckBox(t("merge_keep"))
        chk_keep.setToolTip(t("merge_keep_tip"))
        keep_saved = str(self.store.value("merge/keep_originals", "1") or "1").lower()
        chk_keep.setChecked(keep_saved in ("1", "true", "yes"))
        cmb_src = QComboBox()
        cmb_src.addItems([t("merge_src_mu"), t("merge_src_norm")])
        cmb_src.setToolTip(t("merge_src_tip"))
        cmb_src.setCurrentIndex(1 if str(self.store.value("merge/use_norm", "0")) == "1" else 0)
        btn_auto = QPushButton(t("merge_regroup"))
        btn_auto.setObjectName("ghostBtn")
        btn_auto.setToolTip(t("merge_regroup_tip"))
        opt.addWidget(chk_keep)
        opt.addWidget(cmb_src)
        opt.addStretch(1)
        opt.addWidget(btn_auto)
        lay.addLayout(opt)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        combos: list = []  # (group id, QComboBox)

        def _plan_from_table() -> List[Tuple[str, List[GroupRecord]]]:
            order: List[str] = []
            members: dict = {}
            for gid, cmb in combos:
                label = cmb.currentText().strip()
                if not label or label == skip:
                    continue
                if label not in members:
                    order.append(label)
                    members[label] = []
                members[label].append(by_id[gid])
            return [(lab, members[lab]) for lab in order]

        def _refresh_summary(*_):
            plan = _plan_from_table()
            n_merge = sum(1 for _l, m in plan if len(m) >= 2)
            n_curves = sum(len(m) for _l, m in plan if len(m) >= 2)
            n_single = sum(1 for _l, m in plan if len(m) == 1)
            summary.setText(t("merge_summary", groups=n_merge, curves=n_curves, single=n_single))
            ok_btn.setText(t("merge_ok", n=n_merge))
            ok_btn.setEnabled(n_merge > 0)

        def _fill(suggestion: List[dict]):
            labels = [sg["label"] for sg in suggestion]
            label_of = {gid: sg["label"] for sg in suggestion for gid in sg["ids"]}
            ordered = [by_id[gid] for sg in suggestion for gid in sg["ids"]]
            combos.clear()
            tbl.setRowCount(len(ordered))
            for row, g in enumerate(ordered):
                it = QTableWidgetItem(g.name)
                it.setToolTip(g.name)
                tbl.setItem(row, 0, it)
                tbl.setItem(row, 1, QTableWidgetItem(f"{g.element} {g.edge}".strip() or "?"))
                cmb = QComboBox()
                cmb.setEditable(True)  # type a new group name to split / join by hand
                cmb.addItems(labels + [skip])
                cmb.setCurrentText(label_of.get(g.id, skip))
                cmb.setToolTip(t("merge_col_group_tip"))
                cmb.currentTextChanged.connect(_refresh_summary)
                tbl.setCellWidget(row, 2, cmb)
                combos.append((g.id, cmb))
            _refresh_summary()

        btn_auto.clicked.connect(lambda: _fill(self._merge_suggestion(gs)))
        _fill(self._merge_suggestion(gs))
        if not dlg.exec():
            return None
        keep = chk_keep.isChecked()
        use_norm = cmb_src.currentIndex() == 1
        self.store.setValue("merge/keep_originals", "1" if keep else "0")
        self.store.setValue("merge/use_norm", "1" if use_norm else "0")
        groups = [(lab, m) for lab, m in _plan_from_table() if len(m) >= 2]
        return {"groups": groups, "keep": keep, "use_norm": use_norm}

    def _merge_by_plan(
        self,
        plan: List[Tuple[str, List[GroupRecord]]],
        *,
        keep_originals: bool = True,
        use_norm: bool = False,
    ) -> List[GroupRecord]:
        """Merge each (label, members) group into ``<label>_merged``.

        Master = the current curve when it is a member, else the first one
        (its settings / element / edge go to the merged curve).  Weights =
        importance, as before.  The merged curve is inserted right after its
        last member and marked.  Originals: kept but unmarked (so the plot
        shows the merged result), or removed when *keep_originals* is off.
        One undo entry restores everything (Merge undo button).
        """
        plan = [(lab, list(m)) for lab, m in plan if len(m) >= 2]
        if not plan:
            return []
        own = getattr(self, "_load_progress", None) is None
        if own:
            self._progress_begin(self.i18n.t("merge_title"), sliding=True)
        made: List[GroupRecord] = []
        failed: List[str] = []
        unmarked: List[str] = []
        removed: List[GroupRecord] = []
        order_before = [g.id for g in self.project.groups]  # undo restores this order
        names = {g.name for g in self.project.groups}
        try:
            for k, (label, members) in enumerate(plan):
                cur = self.project.current
                master = next((i for i, g in enumerate(members) if cur is not None and g.id == cur.id), 0)
                specs = []
                for g in members:
                    if use_norm:
                        self._run_with_busy(
                            g.process,
                            text=self.i18n.t("merge_progress", i=k + 1, n=len(plan), name=label),
                        )
                    a = g.session.arrays if g.session else None
                    if use_norm and a is not None and a.energy is not None and a.norm is not None:
                        specs.append((g.name, a.energy, a.norm))
                    else:
                        specs.append((g.name, np.asarray(g.energy, dtype=float) + float(g.energy_shift), np.asarray(g.mu, dtype=float)))
                weights = [float(getattr(g, "importance", 1.0) or 0.0) for g in members]
                res = self._run_with_busy(
                    lambda: merge_spectra(specs, master_index=master, weights=weights),
                    text=self.i18n.t("merge_progress", i=k + 1, n=len(plan), name=label),
                )
                if not res.ok:
                    failed.append(f"{label}: {res.message}")
                    self._log(f"Merge failed [{label}]: {res.message}")
                    continue
                e2, m2 = res.data
                new_name = f"{label}_merged"
                n = 2
                while new_name in names:
                    new_name = f"{label}_merged{n}"
                    n += 1
                names.add(new_name)
                last_id = members[-1].id
                idx = {g.id: i for i, g in enumerate(self.project.groups)}
                after = max(members, key=lambda g: idx.get(g.id, -1)).id if idx else last_id
                ng = self.project.add_group(new_name, e2, m2, path="", after_id=after, is_derived=True)
                ng.marked = True
                ng.settings = deepcopy(members[master].settings)
                ng.element = members[master].element
                ng.edge = members[master].edge
                ng.note = f"merged {len(members)}: " + ", ".join(g.name for g in members)
                made.append(ng)
                for g in members:
                    if keep_originals:
                        if g.marked:
                            g.marked = False
                            unmarked.append(g.id)
                    else:
                        removed.append(g)
                        self.project.groups = [x for x in self.project.groups if x.id != g.id]
                self._log(self.i18n.t("merge_one_done", name=new_name, n=len(members), msg=res.message))
        finally:
            if own:
                self._progress_end()
        if made or removed or unmarked:
            stack = self._module_undo.setdefault("merge", [])
            stack.append({
                "added_ids": [g.id for g in made],
                "removed": removed,
                "order": order_before,
                "unmarked": unmarked,
                "current": self.project.current_id,
            })
            btn = self._undo_buttons.get("merge")
            if btn is not None:
                btn.setEnabled(True)
        if made:
            self.project.set_current(made[0].id)
        elif self.project.current is None and self.project.groups:
            self.project.current_id = self.project.groups[0].id
        self._refresh_list()
        cur = self.project.current
        if cur is not None:
            self._load_group_to_controls(cur)
            self._update_meta()
        self._replot(preserve_view=False)
        self._log(self.i18n.t(
            "merge_all_done", made=len(made),
            kept=self.i18n.t("merge_kept") if keep_originals else self.i18n.t("merge_removed"),
        ))
        if failed:
            QMessageBox.warning(self, self.i18n.t("merge_title"), "\n".join(failed))
        return made

    def _undo_merge(self) -> None:
        """Undo the last smart merge: drop merged curves, put removed
        originals back at their old positions, re-mark unmarked ones."""
        stack = (self._module_undo or {}).get("merge") or []
        if not stack:
            self._log(self.i18n.t("undo_none", name=self.i18n.t("op_merge")))
            return
        entry = stack.pop()
        added = set(entry.get("added_ids") or [])
        live = {g.id: g for g in self.project.groups if g.id not in added}
        for g in entry.get("removed") or []:
            live[g.id] = g
        order = [gid for gid in (entry.get("order") or []) if gid in live]
        # pre-merge order first; anything created after the merge stays at the end
        later = [g for g in self.project.groups if g.id in live and g.id not in set(order)]
        self.project.groups = [live[gid] for gid in order] + later
        back = set(entry.get("unmarked") or [])
        for g in self.project.groups:
            if g.id in back:
                g.marked = True
        prev = entry.get("current")
        if prev and any(g.id == prev for g in self.project.groups):
            self.project.current_id = prev
        elif self.project.current is None:
            self.project.current_id = self.project.groups[0].id if self.project.groups else None
        if not stack:
            self._module_undo.pop("merge", None)
        btn = self._undo_buttons.get("merge")
        if btn is not None:
            btn.setEnabled(bool(stack))
        self._refresh_list()
        cur = self.project.current
        if cur is not None:
            self._load_group_to_controls(cur)
            self._update_meta()
        self._replot(preserve_view=False)
        self._log(self.i18n.t("merge_undone", n=len(added)))

    # ========== Fit ==========
    def _worker_is_running(self) -> bool:
        """Return worker state and discard Qt objects already deleted by Qt."""
        worker = self.worker
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:
            # A completed QThread can be deleted by Qt before Python clears
            # the wrapper.  Never let that stale wrapper block the next job.
            self.worker = None
            return False

    def _retire_worker(self, worker: Worker) -> None:
        """Release one completed worker without clearing a newer replacement."""
        if self.worker is worker:
            self.worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            # Qt may have disposed the wrapper before the queued signal runs.
            pass

    def _run_feff(self, on_done=None):
        if self._worker_is_running():
            return  # double-click while a job is running — do not orphan the thread
        self._worker_busy = False  # previous (chained) worker already finished
        self._set_left_mode("fit")
        self._log(self._bi("运行 FEFF…", "Running FEFF…"))

        def job():
            return prepare_model_a_paths(force=False)

        def _done(p):
            try:
                self._log(f"FEFF paths ready: {p['path1'].name}, {p['path2'].name}")
            except Exception:
                self._log(f"FEFF paths ready: {p}")
            self._refresh_fit_panel()
            self.worker = None  # finished — a chained job must not see this thread
            if on_done is not None:
                on_done(p)  # chained worker keeps _worker_busy True
            else:
                self._worker_busy = False

        def _failed(t):
            self._log(t)
            self.worker = None
            self._worker_busy = False

        self._worker_busy = True
        worker = Worker(job, self)
        self.worker = worker
        worker.finished_ok.connect(_done)
        worker.failed.connect(_failed)
        worker.finished.connect(lambda: self._retire_worker(worker))
        worker.start()

    def _run_fit(self):
        if self._worker_is_running():
            return  # double-click while a job is running — do not orphan the thread
        self._worker_busy = False  # previous (chained) worker already finished
        self._set_left_mode("fit")
        g = self.project.current
        if not g:
            QMessageBox.information(self, "Fit", self.i18n.t("no_current_group"))
            return
        if g.session is None or g.session.group is None:
            self._set_left_mode("norm")
            self._apply_current()
            self._set_left_mode("fit")
        if g.session is None or g.session.group is None:
            QMessageBox.information(self, "Fit", self.i18n.t("fit_need_process"))
            self._set_left_mode("norm")
            return
        gds = self._gds_from_table()
        paths = self._paths_from_table()
        used, match_kind = self._paths_for_group(g, paths)
        if match_kind == "mismatch":
            ans = QMessageBox.question(
                self,
                "Fit",
                self._bi(
                    f"当前组是 {g.element} {g.edge} 边，但路径表中没有分配给 {g.element} 的路径（也没有未分配的路径）。\n请确认选定了正确的元素/文件/路径。\n\n仍要强行拟合吗？",
                    f"Group '{g.name}' is a {g.element} {g.edge} edge, but no path in the table is assigned to {g.element} (and no untagged paths either).\nPlease confirm the correct element / file / path selection.\n\nFit anyway?",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                self._log(self._bi("已取消拟合（元素/路径不匹配）", "Fit cancelled (element/path mismatch)"))
                return
            used = [p for p in paths if p.use and p.feff_file]
        elif match_kind == "fallback-untagged":
            self._log(
                self._bi(
                    f"注意：{g.name} 是 {g.element} 边，将使用未分配元素的路径（{len(used)} 条）",
                    f"Note: {g.name} is a {g.element} edge — using untagged paths ({len(used)})",
                )
            )
        missing = [p.feff_file for p in used if not Path(p.feff_file).is_file()]
        if missing:
            # Model A stable names → FEFF on a worker thread (GUI stays responsive), then retry
            model_a_names = {"path1_PtPt_2.788.dat", "path2_PtPt_3.052.dat"}
            if all(Path(m).name in model_a_names for m in missing):
                self._log(self._bi("缺少 Model A feff 路径——先准备 FEFF…", "Model A feff paths missing — preparing FEFF first…"))
                self._run_feff(on_done=lambda *_: self._run_fit())
                return
        if not self._ensure_fit_model_ready():
            QMessageBox.information(self, "Fit", self.i18n.t("no_usable_paths"))
            self._log(self.i18n.t("no_usable_paths"))
            return
        gds = self._gds_from_table()
        paths = self._paths_from_table()
        used = [p for p in paths if p.use and p.feff_file]
        fs = self._build_fit_settings_from_ui()

        self._log(self._bi(f"拟合 {g.name}（{len(used)} 条路径）…", f"Fitting {g.name} ({len(used)} paths)…"))
        self._set_fit_result_text(self._bi("拟合运行中…", "Running fit…"))
        self._refresh_fit_panel()
        group = g.session.snapshot_group_for_fit()
        data_desc = g.name

        def job():
            return run_feffit(group, gds, paths, fs)

        def _done(result):
            self.worker = None
            self._fit_done(result)
            if isinstance(result, FitResult) and result.ok:
                try:
                    save_fit_to_history(
                        result, gds, paths, fs, data_desc=data_desc,
                        history_dir=self._history_cache_dir(),
                    )
                    self._refresh_fit_history()
                except Exception as exc:
                    self._log(f"history save failed: {exc}")

        self._worker_busy = True
        worker = Worker(job, self)
        self.worker = worker
        worker.finished_ok.connect(_done)
        def _failed(t):
            self.worker = None
            self._fit_failed(t)

        worker.failed.connect(_failed)
        worker.finished.connect(lambda: self._retire_worker(worker))
        worker.start()

    def _fit_failed(self, tb: str):
        self._worker_busy = False
        self._log(tb)
        self._set_fit_result_text(tb)
        self.lbl_fit_grade.setText(self._bi("拟合失败", "Fit failed"))
        self.lbl_fit_quality.setText("")
        self.lbl_fit_warning.setText("")
        self.fit_quality_card.setStyleSheet("QFrame { background:#fdecea; border:1px solid #c0392b; border-radius:5px; }")
        self._set_left_mode("fit")

    def _fit_quality_state(self, result: "FitResult") -> dict:
        """Return a transparent diagnostic state; it is not a validity proof."""
        st = result.statistics or {}
        try:
            r_factor = float(st.get("r_factor", float("nan")))
        except Exception:
            r_factor = float("nan")
        if not np.isfinite(r_factor):
            return {"grade": "unknown", "color": "#6b625d", "warning": "Fit statistics unavailable"}
        if r_factor < 0.02:
            grade, color = "excellent", "#1f8a45"
        elif r_factor < 0.05:
            grade, color = "good", "#1f8a45"
        elif r_factor < 0.10:
            grade, color = "fair", "#b07d10"
        else:
            grade, color = "poor", "#c0392b"
        warnings = []
        n_ind = float(st.get("n_independent", float("nan")) or float("nan"))
        n_varys = int(st.get("n_varys", 0) or 0)
        if np.isfinite(n_ind) and n_varys > n_ind:
            warnings.append(f"Over-parameterized: Nvarys={n_varys} exceeds Nidp={n_ind:.1f}")
        strong = [c for c in (result.correlations or []) if len(c) >= 3 and abs(float(c[2])) >= 0.90]
        if strong:
            a, b, corr = strong[0][:3]
            warnings.append(f"High correlation: {a}/{b} ({float(corr):+.2f})")
        return {"grade": grade, "color": color, "warning": "; ".join(warnings), "r_factor": r_factor}

    def _update_fit_quality(self, result: "FitResult") -> None:
        """Show a colour-coded fit summary plus model-complexity warnings."""
        if not isinstance(result, FitResult) or not result.ok:
            self._fit_failed(result.message if isinstance(result, FitResult) else "Fit failed")
            return
        st = result.statistics or {}

        def _f(v, fmt="{:.4g}"):
            try:
                fv = float(v)
                return fmt.format(fv) if np.isfinite(fv) else "—"
            except Exception:
                return "—"

        state = self._fit_quality_state(result)
        rf = state.get("r_factor", float("nan"))
        if not np.isfinite(rf):
            self.lbl_fit_grade.setText(self._bi("统计不可用", "Statistics unavailable"))
            self.lbl_fit_quality.setText("")
            self.lbl_fit_warning.setText(state["warning"])
            return
        grade = state["grade"]
        tag = self._bi(
            {"excellent": "优秀", "good": "良好", "fair": "一般", "poor": "较差"}.get(grade, "未知"),
            grade,
        )
        color = state["color"]
        background = {"#1f8a45": "#edf8f0", "#b07d10": "#fff7df", "#c0392b": "#fdecea"}.get(color, "#f5f5f5")
        self.fit_quality_card.setStyleSheet(
            f"QFrame {{ background:{background}; border:1px solid {color}; border-radius:5px; }}"
        )
        self.lbl_fit_grade.setStyleSheet(f"font-weight:700; font-size:11px; color:{color};")
        self.lbl_fit_grade.setText(f"{self._bi('拟合评价', 'Fit quality')}: {tag}")
        self.lbl_fit_quality.setTextFormat(Qt.TextFormat.RichText)
        redchi = st.get("reduced_chi_square", st.get("redchi"))
        self.lbl_fit_quality.setText(
            f"<b>R={_f(rf)}</b> · χ²={_f(st.get('chi_square'))} · "
            f"χ²<sub>red</sub>={_f(redchi)} · "
            f"N<sub>idp</sub>={_f(st.get('n_independent'), '{:.1f}')} · "
            f"N<sub>varys</sub>={int(st.get('n_varys', 0) or 0)}"
        )
        warning = state["warning"]
        self.lbl_fit_warning.setText(warning if warning else self._bi("未发现明显的统计警告", "No obvious statistical warning"))

    def _ai_review_model(self, provider: str) -> str:
        """The model the user picked for *provider* - no built-in default any
        more (models and their effort levels change too fast to hard-code)."""
        configured = str(self.store.value(f"ai_review_model_{provider}", "") or "").strip()
        if configured and configured == LEGACY_DEFAULT_MODELS.get(provider):
            # an old built-in default the old Settings page auto-saved - not a choice
            self.store.remove(f"ai_review_model_{provider}")
            configured = ""
        return configured or provider_default_model(provider)

    def _ai_models_for(self, provider: str) -> list:
        """Model ids last fetched from *provider* by Verify connection."""
        raw = self.store.value(f"ai_models_{provider}", "")
        try:
            data = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
        except ValueError:
            data = []
        return [str(m) for m in data if str(m).strip()] if isinstance(data, list) else []

    def _api_provider_label(self, provider: str) -> str:
        for prov, label, _sk in AGENTREG.API_SLOTS:
            if prov == provider:
                return self.i18n.t(label)
        return provider

    def _ai_review_api_key(self, provider: str) -> str:
        return self._coding_api_keys().get(provider, "")

    def _cache_root(self) -> Path:
        configured = str(self.store.value("analysis_cache_folder", "") or "").strip()
        path = Path(configured).expanduser() if configured else DEFAULT_OUTPUT
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _history_cache_dir(self) -> Path:
        path = self._cache_root() / "history"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _ai_review_cache_dir(self) -> Path:
        path = self._cache_root() / "ai_reviews"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _ai_suggestion_cache_dir(self) -> Path:
        path = self._cache_root() / "ai_suggestions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def _redact_ai_evidence(cls, value):
        """Remove local absolute paths before evidence is sent to a provider."""
        if isinstance(value, dict):
            return {key: cls._redact_ai_evidence(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_ai_evidence(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._redact_ai_evidence(item) for item in value)
        if isinstance(value, str):
            return re.sub(
                r"(?i)(?:[a-z]:[\\/][^\r\n,;]+|/(?:users|home|opt|beamline|private|var|tmp)/[^\r\n,;]+)",
                "[local-path-redacted]",
                value,
            )
        return value

    def _snapshot_fit_review_context(self, snap: dict) -> dict:
        """Turn one saved history record into the bounded AI evidence shape."""
        curve_samples: dict[str, list[list[float]]] = {}
        raw_views = snap.get("plot_views") or {}
        if isinstance(raw_views, dict):
            view = raw_views.get("2") or raw_views.get(2) or next(iter(raw_views.values()), {})
            if isinstance(view, dict):
                try:
                    curve_samples["k"] = downsample_curves(
                        view.get("k", []), view.get("data_kchi", []), view.get("fit_kchi", []), limit=24
                    )
                    curve_samples["R"] = downsample_curves(
                        view.get("r", []), view.get("data_rmag", []), view.get("fit_rmag", []),
                        view.get("residual_rre", []), limit=24
                    )
                except (TypeError, ValueError):
                    curve_samples = {}
        return {
            "dataset": {"name": str(snap.get("data", "")), "element": "", "edge": ""},
            "fit_windows": dict(snap.get("settings") or {}),
            "statistics": dict(snap.get("statistics") or {}),
            "parameters": dict(snap.get("params") or {}),
            "paths": list(snap.get("path_results") or snap.get("paths") or []),
            "correlations": list(snap.get("correlations") or []),
            "curve_sample_columns": {
                "k": ["k", "data_k2chi", "fit_k2chi"],
                "R": ["R", "data_abs_chiR", "fit_abs_chiR", "residual_Re_chiR"],
            },
            "curve_samples": curve_samples,
            "fit_report_excerpt": str(snap.get("report_text", "") or "")[:6000],
        }

    def _ai_suggestion_contexts(self, scope: Optional[str] = None) -> tuple[str, list[dict]]:
        """Fit evidence for the AI Check fit category (bounded, per scope)."""
        scope = str(scope or self._ai_check_scope())
        if scope == "current":
            if isinstance(self.last_fit, FitResult) and self.last_fit.ok:
                return scope, [self._fit_review_context(self.last_fit)]
            return scope, []
        marked_names = {group.name for group in self._marked_groups_for_scope()} if scope == "marked" else set()
        contexts: list[dict] = []
        for row in list_fit_history(self._history_cache_dir()):
            try:
                snapshot = load_fit_snapshot(row["file"])
                if scope != "marked" or str(snapshot.get("data", "")) in marked_names:
                    contexts.append(self._snapshot_fit_review_context(snapshot))
            except Exception:
                continue
        return scope, contexts

    def _probe_cli_version(self, agent: str, path=None, env=None) -> tuple[bool, str]:
        """CLI check: found and answers ``--version``; for an AI Check CLI also
        one tiny real call (``--version`` works with a missing or expired login).

        Verify resolves ``path`` / ``env`` on the GUI thread and passes them in:
        its probes run in parallel threads and must not share QSettings reads.
        """
        if path is None:
            path = self._resolve_coding_cli_path(agent)
        if env is None:
            env = self._coding_env(None)
        if not path:
            return False, "not installed"
        if path.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c", path, "--version"]
        elif path.lower().endswith(".ps1"):
            cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "--version"]
        else:
            cmd = [path, "--version"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)[:120]
        lines = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines()
        lines = [ln.strip() for ln in lines if ln.strip()]
        if proc.returncode != 0:
            return False, (lines[-1] if lines else f"exit {proc.returncode}")[:120]
        version = (lines[0] if lines else "ok")[:80]
        if agent not in AGENTREG.AI_CHECK_CLIS:
            return True, version
        try:
            if agent == "claude":
                run_claude_headless(path, "Reply with exactly: OK", timeout=60, env=env)
            else:
                run_kimi_headless(
                    path, "Reply with exactly: OK", cwd=str(self._ai_cli_workdir()), timeout=90, env=env
                )
        except AISuggestionError as exc:
            return False, f"{version}; {exc}"[:260]
        return True, f"{version}; signed in"

    def _ai_reason(self, detail: str) -> str:
        """Short, translated reason for a failed Verify / channel (full text stays in the tooltip)."""
        low = str(detail or "").lower()
        t = self.i18n.t
        if "not installed" in low:
            return t("ai_reason_not_installed")
        if "no api key" in low:
            return t("ai_reason_no_key")
        if "login" in low or "sign in" in low or "not logged" in low:
            return t("ai_reason_signin")
        if "key rejected" in low or "401" in low or "403" in low:
            return t("ai_reason_key_rejected")
        if "network" in low or "connection" in low or "timed out" in low:
            return t("ai_reason_network")
        return str(detail or "")[:60]

    def _set_ai_status(self, label, state: str, detail: str = "") -> None:
        """One row's Verify mark: ✓ ok, ✗ failed (reason in the tooltip), ? ticked
        but not verified yet, — not ticked, … being checked."""
        text, color = {
            "ok": ("✓", "#2e7d32"), "bad": ("✗", "#c62828"),
            "unknown": ("?", "#9e9e9e"), "off": ("—", "#9e9e9e"),
        }.get(state, ("…", "#6b625d"))
        if not detail and state in ("unknown", "off"):
            detail = self.i18n.t("ai_mark_unknown" if state == "unknown" else "ai_mark_off")
        try:
            label.setText(text)
            label.setStyleSheet(f"color:{color}; font-weight:700;")
            label.setToolTip(detail)
        except RuntimeError:
            pass  # settings dialog already closed

    def _ai_ticked_keys(self) -> list:
        """Ticked channels in Settings order: AI Check CLIs first, then APIs."""
        keys = [("cli", a) for a in AGENTREG.AI_CHECK_CLIS if self._ai_cli_active(a)]
        keys += [
            ("api", p) for p, _label, _sk in AGENTREG.API_SLOTS if self._ai_api_active(p)
        ]
        return keys

    def _ai_order_keys(self) -> list:
        """Ticked channels in the order AI Check tries them (the "uses" choice applied)."""
        keys = self._ai_ticked_keys()
        use = self._ai_check_use()
        if use != "auto":
            keys = [k for k in keys if f"{k[0]}:{k[1]}" == use]
        return keys

    def _ai_order_text(self, results: Optional[dict] = None) -> str:
        """Settings summary: which channels AI Check uses, in order; marks after Verify."""
        t = self.i18n.t
        keys = self._ai_order_keys()
        if not keys:
            return t("ai_order_none")
        parts = []
        for key in keys:
            label = self._ai_channel_label(*key)
            if results and key in results:
                ok, detail = results[key][0], results[key][1]
                label += " ✓" if ok else f" ✗（{self._ai_reason(detail)}）"
            parts.append(label)
        head = t("ai_order_auto") if self._ai_check_use() == "auto" else t("ai_order_only")
        return head + " → ".join(parts)

    def _verify_ai_connections(self, rows: dict, button, summary=None, on_done=None) -> None:
        """Check every TICKED channel at once and mark its row ✓ / ✗ (v4.4.0).

        ``rows`` maps ("cli", agent) / ("api", provider) to that row's status
        label.  Unticked rows show "—" and are not contacted.  CLIs get one tiny
        real call; APIs list their models, which fills that row's model picker.
        """
        prev = getattr(self, "_verify_worker", None)
        if prev is not None:
            try:
                if prev.isRunning():
                    return
            except RuntimeError:
                pass  # finished + deleteLater'd: the C++ object is gone, not running
            self._verify_worker = None
        t = self.i18n.t
        active = [
            key for key in rows
            if (self._ai_cli_active(key[1]) if key[0] == "cli" else self._ai_api_active(key[1]))
        ]
        keys = self._coding_api_keys()
        region = self._qwen_region()
        # settings are read here, on the GUI thread; the probes only get values
        cli_paths = {name: self._resolve_coding_cli_path(name) or "" for kind, name in active if kind == "cli"}
        env = self._coding_env(None)

        def probe(key) -> tuple:
            kind, name = key
            if kind == "cli":
                ok, detail = self._probe_cli_version(name, path=cli_paths[name], env=env)
                return ok, detail, []
            if not keys.get(name):
                return False, "no API key saved", []
            return fetch_models(name, keys[name], region=region)

        def job() -> dict:
            from concurrent.futures import ThreadPoolExecutor

            if not active:
                return {}
            with ThreadPoolExecutor(max_workers=len(active)) as pool:
                return dict(zip(active, pool.map(probe, active)))

        for key, lab in rows.items():
            self._set_ai_status(lab, "busy" if key in active else "off")
        button.setEnabled(False)
        button.setText(t("ai_verify_running"))
        worker = Worker(job, self)
        self._verify_worker = worker

        def _apply(results: dict) -> None:
            sign_in = None
            for key, (ok, detail, ids) in results.items():
                kind, name = key
                if kind == "api" and ok and ids:  # the picker offers what the provider has now
                    self.store.setValue(f"ai_models_{name}", json.dumps(ids))
                self._set_ai_status(rows[key], "ok" if ok else "bad", detail)
                self._log(f"Verify {kind.upper()} {name}: {'OK' if ok else 'FAIL'} ({detail})")
                if kind == "cli" and not ok and sign_in is None and "login" in detail.lower():
                    sign_in = name
            try:
                if summary is not None:
                    summary.setText(self._ai_order_text(results))
                button.setEnabled(True)
                button.setText(t("ai_verify"))
            except RuntimeError:
                pass  # settings dialog already closed
            if on_done is not None:
                try:
                    on_done()
                except RuntimeError:
                    pass  # settings dialog already closed
            if sign_in is not None:
                path = self._resolve_coding_cli_path(sign_in)
                if path:
                    self._cli_sign_in(sign_in, path, resume=None)

        def _failed(tb: str) -> None:
            self._log(f"Verify connections error: {self._short_error(tb) or '?'}")
            try:
                button.setEnabled(True)
                button.setText(t("ai_verify"))
            except RuntimeError:
                pass

        worker.finished_ok.connect(_apply)
        worker.failed.connect(_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _resolve_coding_cli_path(self, agent: str) -> Optional[str]:
        """Resolve a real CLI executable, never a desktop/protocol client."""
        candidates: list[str] = []
        saved = self._normalize_coding_target(str(self.store.value(f"coding_path_{agent}", "") or ""))
        if saved:
            candidates.append(saved)
        candidates.extend(self._coding_launch_candidates(agent))
        for raw in candidates:
            target = self._normalize_coding_target(raw)
            if not target or target.lower().startswith(("appx:", "protocol:")):
                continue
            if "*" in target or "?" in target:
                hit = self._glob_newest_file(target)
                if hit is None:
                    continue
                target = str(hit.resolve())
            p = Path(os.path.expandvars(target))
            if not p.is_file():
                found = AGENTREG.which_cli(target)
                if not found:
                    continue
                p = Path(found)
            resolved = str(p.resolve())
            if self._is_cli_only_coding_target(agent, resolved):
                return resolved
        return None

    # _run_ai_suggestion / _run_ai_assist_via_api were folded into the single
    # top-level AI Check (v3.19.5): see _run_ai_check / _ai_check_try (v4.4.0).

    def _require_current_suggestion(self, suggestion: dict) -> None:
        if suggestion.get("fit_scope") != "current":
            raise AISuggestionError(self.i18n.t("ai_suggestion_scope_not_applicable"))

    def _apply_ai_suggestion_gds(self, suggestion: dict) -> None:
        """Apply only the validated GDS subsection; FEFF and windows stay untouched."""
        self._require_current_suggestion(suggestion)
        patches = list((suggestion.get("gds") or {}).get("parameters") or [])
        rows_by_name = {}
        for row in range(self.tbl_gds.rowCount()):
            item = self.tbl_gds.item(row, 0)
            if item and item.text().strip():
                rows_by_name[item.text().strip()] = row
        unknown = [patch["name"] for patch in patches if patch["name"] not in rows_by_name]
        if unknown:
            raise AISuggestionError(f"AI suggestion names no current GDS row: {', '.join(unknown)}.")
        for patch in patches:
            row = rows_by_name[patch["name"]]
            for key, col in (("value", 1), ("expr", 3), ("min", 4), ("max", 5)):
                if key in patch:
                    value = patch[key]
                    self.tbl_gds.setItem(row, col, QTableWidgetItem(f"{value:g}" if isinstance(value, float) else str(value)))
            if "kind" in patch:
                box = self.tbl_gds.cellWidget(row, 2)
                if isinstance(box, QComboBox):
                    box.setCurrentText(patch["kind"])

    # _apply_ai_suggestion_normalization / _apply_selected_ai_suggestion /
    # _read_apply_selected_ai_suggestion were removed in v3.19.5: the single AI
    # Check applies the process category through _apply_process_suggestion (with
    # the shared normalization self-check) and the fit category through
    # _apply_fit_suggestion.
    def _show_ai_suggestion_feff(self, suggestion: dict) -> None:
        """Display FEFF advice without mutating the FEFF path table."""
        feff = suggestion.get("feff") or {}
        recommendations = "\n".join(f"- {item}" for item in feff.get("recommendations", []))
        self._set_fit_result_text(
            f"=== AI Suggestion · FEFF (read only) ===\n{feff.get('summary', '')}\n\n{recommendations}"
        )
        self._set_left_mode("fit")

    # (the checked GDS/Normalization/FEFF apply row is gone - see the note above)
    def _fit_review_context(self, result: FitResult) -> dict:
        """Make a bounded, traceable fit-evidence payload for one explicit review."""
        g = self.project.current
        curve_samples: dict[str, list[list[float]]] = {}
        views = result.plot_views or {}
        view = views.get(2) or (next(iter(views.values())) if views else {})
        if view:
            try:
                curve_samples["k"] = downsample_curves(
                    view.get("k", []), view.get("data_kchi", []), view.get("fit_kchi", []), limit=24
                )
                curve_samples["R"] = downsample_curves(
                    view.get("r", []), view.get("data_rmag", []), view.get("fit_rmag", []),
                    view.get("residual_rre", []), limit=24
                )
                if view.get("q") is not None:
                    curve_samples["q"] = downsample_curves(
                        view.get("q", []), view.get("data_qre", []), view.get("fit_qre", []), limit=24
                    )
            except (TypeError, ValueError):
                curve_samples = {}
        try:
            fs = self._build_fit_settings_from_ui()
            fit_windows = {
                "kmin": fs.kmin, "kmax": fs.kmax, "dk": fs.dk, "kwindow": fs.kwindow,
                "rmin": fs.rmin, "rmax": fs.rmax, "dr": fs.dr, "rwindow": fs.rwindow,
                "fit_kweights": list(fs.kw_list),
            }
        except Exception:
            fit_windows = {}
        return {
            "dataset": {
                "name": str(g.name if g else ""),
                "element": str(g.element if g else ""),
                "edge": str(g.edge if g else ""),
            },
            "fit_windows": fit_windows,
            "statistics": dict(result.statistics or {}),
            "parameters": dict(result.params or {}),
            "paths": list(result.paths or []),
            "correlations": [list(c) for c in (result.correlations or [])],
            "curve_sample_columns": {
                "k": ["k", "data_k2chi", "fit_k2chi"],
                "R": ["R", "data_abs_chiR", "fit_abs_chiR", "residual_Re_chiR"],
                "q": ["q", "data_Re_chiq", "fit_Re_chiq"],
            },
            "curve_samples": curve_samples,
            "fit_report_excerpt": str(result.to_text() or "")[:6000],
        }

    def _fit_export_dir(self) -> Path:
        """Same folder as Settings → data export (and Tools / Analysis exports)."""
        return self._data_export_dir()

    def _export_fit_all(self) -> None:
        """Export report .txt + k/R CSVs together (single button at bottom of Fit page)."""
        res = self.last_fit
        if not isinstance(res, FitResult) or not res.ok:
            self._log(self._bi("没有可导出的拟合结果", "No fit result to export"))
            QMessageBox.information(
                self,
                self.i18n.t("fit_export_all"),
                self._bi("没有可导出的拟合结果", "No fit result to export"),
            )
            return
        import datetime

        g = self.project.current
        name = (g.name if g else "fit").replace(" ", "_")[:40]
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = self._fit_export_dir()
        written: List[Path] = []
        report_path = out_dir / f"fit_report_{name}_{ts}.txt"
        report_path.write_text(res.to_text(), encoding="utf-8")
        written.append(report_path)

        kw = int(self.sp_kw_plot.value()) if hasattr(self, "sp_kw_plot") else 2
        view = res.plot_views.get(kw) if getattr(res, "plot_views", None) else None
        try:
            if view:
                kd = np.asarray(view["k"], dtype=float)
                p = out_dir / f"fit_k_{name}_{ts}.csv"
                np.savetxt(
                    p,
                    np.column_stack([kd, view["data_kchi"], view["fit_kchi"]]),
                    delimiter=",",
                    header=f"k,data_k{kw}chi,fit_k{kw}chi",
                    comments="# ",
                )
                written.append(p)
                cols = [view["r"], view["data_rmag"], view["fit_rmag"]]
                header = "R,data_chir_mag,fit_chir_mag"
                if view.get("data_rre") is not None and view.get("fit_rre") is not None:
                    cols.extend([view["data_rre"], view["fit_rre"], view.get("residual_rre", view["data_rre"] * 0)])
                    header = "R,data_chir_mag,fit_chir_mag,data_Re_chir,fit_Re_chir,residual_Re_chir"
                elif view.get("residual_rre") is not None:
                    cols.append(view["residual_rre"])
                    header = "R,data_chir_mag,fit_chir_mag,residual_Re_chir"
                p = out_dir / f"fit_R_{name}_{ts}.csv"
                np.savetxt(
                    p,
                    np.column_stack(cols),
                    delimiter=",",
                    header=header,
                    comments="# ",
                )
                written.append(p)
            elif res.k is not None and res.chi is not None and res.fit_k is not None and res.fit_chi is not None:
                kd, cd = np.asarray(res.k, dtype=float), np.asarray(res.chi, dtype=float)
                kf, cf = np.asarray(res.fit_k, dtype=float), np.asarray(res.fit_chi, dtype=float)
                cf_i = np.interp(kd, kf, cf)
                p = out_dir / f"fit_k_{name}_{ts}.csv"
                arr = np.column_stack([kd, cd, cd * kd**kw, cf_i, cf_i * kd**kw])
                np.savetxt(
                    p,
                    arr,
                    delimiter=",",
                    header=f"k,data_chi,data_k{kw}chi,fit_chi,fit_k{kw}chi",
                    comments="# ",
                )
                written.append(p)
                if res.r is not None and res.chir_mag is not None and res.fit_chir_mag is not None:
                    rd = np.asarray(res.r, dtype=float)
                    md = np.asarray(res.chir_mag, dtype=float)
                    fr = np.asarray(res.fit_r, dtype=float) if res.fit_r is not None else rd
                    mf = np.interp(rd, fr, np.asarray(res.fit_chir_mag, dtype=float))
                    p = out_dir / f"fit_R_{name}_{ts}.csv"
                    np.savetxt(
                        p,
                        np.column_stack([rd, md, mf, md - mf]),
                        delimiter=",",
                        header="R,data_chir_mag,fit_chir_mag,residual_Re_chir",
                        comments="# ",
                    )
                    written.append(p)
        except Exception as exc:
            self._log(f"fit curve export partial fail: {exc}")

        names = "\n".join(str(p) for p in written)
        self._log(self._bi(f"已导出拟合报告与数据：\n{names}", f"Exported fit report + data:\n{names}"))
        QMessageBox.information(
            self,
            self.i18n.t("fit_export_all"),
            self._bi(f"已导出：\n{names}", f"Exported:\n{names}"),
        )

    def _export_fit_report(self):
        """Back-compat → combined export."""
        self._export_fit_all()

    def _export_fit_data(self):
        """Back-compat → combined export."""
        self._export_fit_all()

    def _fit_done(self, result: FitResult):
        self._worker_busy = False
        self.last_fit = result
        report = result.to_text()
        self._log(report[:2000])
        self._set_fit_result_text(report)
        self._update_fit_quality(result)
        self._set_left_mode("fit")  # stay on Fit panel with results
        self._refresh_fit_panel()
        if result.ok:
            # Rmr exposes magnitude and the physically meaningful real-part
            # residual at once; other display views remain one click away.
            idx = self.cmb_fit_plot.findData("rmr")
            if idx >= 0:
                # A second completed fit may arrive while Rmr is already the
                # selected combo item, which does not emit indexChanged.
                self.cmb_fit_plot.blockSignals(True)
                self.cmb_fit_plot.setCurrentIndex(idx)
                self.cmb_fit_plot.blockSignals(False)
                self.canvas.set_mode("fit_rmr")
                self._replot()
            else:
                self._focus_panel("grid")
        else:
            self._replot()
        if result.ok:
            self._status((result.validation or {}).get("overall", "fit ok"), 15000)

    def _demo_xdi_path(self) -> Optional[Path]:
        """Configured demo folder first; bundled Fit92 spectrum is the fallback."""
        folder = str(self.store.value("demo_folder", "") or "").strip()
        if folder:
            base = Path(folder)
            preferred = base / self.paths["demo_xdi"].name
            if preferred.is_file():
                return preferred
            if base.is_dir():
                candidates = sorted(base.glob("*.xdi"))
                if candidates:
                    return candidates[0]
        bundled = self.paths["demo_xdi"]
        return bundled if bundled.is_file() else None

    def _choose_demo_folder(self) -> None:
        saved = str(self.store.value("demo_folder", "") or "").strip()
        start = saved if Path(saved).is_dir() else str(self.paths["data"])
        folder = QFileDialog.getExistingDirectory(
            self,
            self._bi("选择 Demo 文件夹", "Choose demo folder"),
            start,
        )
        if not folder:
            return
        self.store.setValue("demo_folder", folder)
        demo = self._demo_xdi_path()
        if demo is None:
            self._log(self.i18n.t("demo_file_missing", folder=folder))
            return
        self._log(self._bi(f"Demo 文件：{demo}", f"Demo file: {demo}"))

    def _begin_demo_session(self) -> None:
        """Drop all view-only state before demo data replaces the project."""
        self._clear_marks_and_reset()
        # The E-panel guide bands come from these controls rather than from
        # ``energy_marks``.  Reset them before loading the demo so pre/post
        # windows from the preceding file cannot appear on its spectrum.
        fresh = AthenaSettings()
        was_blocked = self._block
        self._block = True
        try:
            self.chk_e0_auto.setChecked(True)
            self.sp_e0.setValue(0.0)
            self.sp_pre1.setValue(fresh.pre1)
            self.sp_pre2.setValue(fresh.pre2)
            self.sp_norm1.setValue(fresh.norm1)
            self.sp_norm2.setValue(fresh.norm2)
            self.sp_eshift.setValue(0.0)
            self._panel_nvict = fresh.nvict
        finally:
            self._block = was_blocked
        self.project = AthenaProject("Fit92 validation")

    def _run_validate(self):
        demo = self._demo_xdi_path()
        if demo is None:
            folder = str(self.store.value("demo_folder", self.paths["data"]) or self.paths["data"])
            self._log(self.i18n.t("demo_file_missing", folder=folder))
            return
        self._begin_demo_session()
        self._import_path(str(demo))
        self._refresh_list()
        # Import normally lands on Norm, but demo check is a chained FEFF+fit
        # workflow and must remain visibly on Fit from start to finish.
        self._set_left_mode("fit")
        # Pt L3 defaults (short symbol)
        self.cmb_element.setEditText("Pt")
        self.cmb_edge.setCurrentText("L3")
        self.sp_kmin.setValue(2.0)
        self.sp_kmax.setValue(10.0)
        self.sp_rmin.setValue(1.0)
        self.sp_rmax.setValue(4.0)
        self._apply_current()
        self._load_model_a_preset()
        # chain fit AFTER the FEFF worker finishes (no fixed-timer race)
        self._run_feff(on_done=lambda *_: self._run_fit())

    def _build_about_dialog(self) -> QDialog:
        if self.i18n.lang == "zh":
            body = (
                "Athena 风格预处理 + Larch EXAFS 拟合一体化客户端。\n\n"
                "Implemented:\n"
                "• 多格式数据导入、多组曲线管理、元素筛选与图例点击显隐\n"
                "• E0、pre-edge、normalization、background、k/R 窗口与 Fourier transform\n"
                "• 平滑、重采样、对齐、校准、去毛刺、LCF、PCA、MCR-ALS、峰拟合与批处理\n"
                "• FEFF 运行、多元素路径分类、GDS 参数、k/q/R 多视图与 1/2/3 k-weight\n"
                "• Fit History 曲线恢复、统计评价、API AI Review 与终端 AI Suggestion\n"
                "• GDS/Normalization 分区应用、只读 FEFF 建议与可配置分析缓存\n\n"
                "完整功能差异见 docs/ATHENA_FEATURE_MAP.md。\n\n"
                "Acknowledgements: xraylarch (https://xraypy.github.io/xraylarch/)\n"
                "Design reference: KaifengZheng/zanes (https://github.com/kaifengZheng/zanes)\n\n"
                f"{COPYRIGHT}"
            )
        else:
            body = (
                "Unified Athena-style preparation + Larch EXAFS fitting client.\n\n"
                "Implemented:\n"
                "• Multi-format import, multi-group curve management, element filtering, legend toggling\n"
                "• E0, pre-edge, normalization, background, k/R windows, and Fourier transform\n"
                "• Smooth, rebin, align, calibrate, deglitch, LCF, PCA, MCR-ALS, peaks, and batch tools\n"
                "• FEFF runs, multi-element path assignment, GDS, k/q/R views, and k-weights 1/2/3\n"
                "• Fit History curve restore, fit-quality assessment, API AI Review, and terminal AI Suggestion\n"
                "• Separate GDS/Normalization application, read-only FEFF advice, configurable analysis cache\n\n"
                "See docs/ATHENA_FEATURE_MAP.md for the remaining gap to full Athena.\n\n"
                "Acknowledgements: xraylarch (https://xraypy.github.io/xraylarch/)\n"
                "Design reference: KaifengZheng/zanes (https://github.com/kaifengZheng/zanes)\n\n"
                f"{COPYRIGHT}"
            )
        dlg = QDialog(self)
        dlg.setWindowTitle(self.i18n.t("about"))
        # match the app theme — otherwise the dialog follows the OS palette and
        # can show a dark box inside a light window
        _dark = getattr(self, "_theme_dark", False)
        _bg = "#232f3b" if _dark else "#f8f5f2"
        _fg = "#e6edf3" if _dark else "#242424"
        dlg.setStyleSheet(
            f"QDialog {{ background: {_bg}; color: {_fg}; }}"
            f"QLabel {{ background: transparent; color: {_fg}; }}"
        )
        lay = QVBoxLayout(dlg)
        top = QHBoxLayout()
        assets = Path(__file__).resolve().parent / "assets"
        for index, fn in enumerate(("app_icon_g.png",)):
            fp = assets / fn
            if fp.is_file():
                pm = QPixmap(str(fp))
                if not pm.isNull():
                    lab = QLabel()
                    lab.setStyleSheet("background:transparent;")
                    lab.setPixmap(pm.scaledToHeight(40, Qt.TransformationMode.SmoothTransformation))
                    top.addWidget(lab)
            if index == 0:
                top.addStretch(1)
        lay.addLayout(top)
        lay.addSpacing(12)  # breathing room between the logos and the body
        title = QLabel(f"{APP_NAME} v{APP_VERSION}")
        title.setStyleSheet(f"font-size:17px; font-weight:700; color:{_fg}; background:transparent;")
        lay.addWidget(title)
        lay.addSpacing(4)
        # Plain label sized to content: text stays top-left and never grows a
        # scrollbar (the old read-only QPlainTextEdit kept its default
        # sizeHint, so the box scrolled and the text drifted when resized).
        text = QLabel(body)
        text.setObjectName("aboutText")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setMinimumWidth(660)  # widen instead of scrolling (version-info text)
        lay.addWidget(text)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.adjustSize()  # fit the content instead of a fixed large window
        dlg.setFixedSize(dlg.size())  # About box: lock to content size (no resize drift)
        return dlg

    def _about(self):
        self._build_about_dialog().exec()
        return
        if self.i18n.lang == "zh":
            body = (
                f"{APP_NAME} v{APP_VERSION}\n\n"
                "Athena 风格预处理 + Larch 拟合一体化客户端。\n\n"
                "实现：多组列表、全选/反选、能量平移、归一化/背景/FT 参数、\n"
                "μ/归一化/导数绘图、点击设 E0/边界、2×2 与单图聚焦视图、\n"
                "平滑、重采样、对齐、校准、去毛刺、LCF、PCA、MCR-ALS、峰拟合、\n"
                "应用到勾选组、Model A 拟合。\n\n"
                "与完整 Athena 的差距见 docs/ATHENA_FEATURE_MAP.md。\n\n"
                "致谢：本软件基于 xraylarch 开发\n"
                "(https://xraypy.github.io/xraylarch/)，\n"
                "并参考了 KaifengZheng 的 zanes 包\n"
                "(https://github.com/kaifengZheng/zanes)。\n\n"
                f"{COPYRIGHT}"
            )
        else:
            body = (
                f"{APP_NAME} v{APP_VERSION}\n\n"
                "Unified Athena-style prep + Larch fit client.\n\n"
                "Implemented: multi-group list, mark all/none/invert, energy shift,\n"
                "norm/bkg/FT params, plot μ/norm/deriv, click-set E0/ranges,\n"
                "2×2 grid + focused E/k/R/q views, smooth, rebin, align, calibrate,\n"
                "deglitch, LCF, PCA, MCR-ALS, peaks, apply-to-marked, Model A fit.\n\n"
                "See docs/ATHENA_FEATURE_MAP.md for gap list vs full Athena.\n\n"
                "Acknowledgements: built on xraylarch\n"
                "(https://xraypy.github.io/xraylarch/),\n"
                "with design reference to KaifengZheng's zanes package\n"
                "(https://github.com/kaifengZheng/zanes).\n\n"
                f"{COPYRIGHT}"
            )
        dlg = QDialog(self)
        dlg.setWindowTitle(self.i18n.t("about"))
        lay = QVBoxLayout(dlg)
        # both logos in one row, top-right
        top = QHBoxLayout()
        top.addStretch(1)
        assets = Path(__file__).resolve().parent / "assets"
        for fn in ("app_icon_g.png",):
            fp = assets / fn
            if not fp.is_file():
                continue
            pm = QPixmap(str(fp))
            if pm.isNull():
                continue
            lab = QLabel()
            lab.setStyleSheet("background:transparent;")
            lab.setPixmap(pm.scaledToHeight(40, Qt.TransformationMode.SmoothTransformation))
            top.addWidget(lab)
        lay.addLayout(top)
        text = QLabel(body)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(text)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    # ========== Settings / tray / update folder (Code-Web style) ==========
    def _close_action(self) -> str:
        ca = str(self.store.value("close_action", "quit") or "quit").strip().lower()
        return "tray" if ca in ("tray", "minimize", "min_tray", "tray_icon") else "quit"

    def _update_folder(self) -> str:
        return str(self.store.value("update_folder", "") or "").strip()

    def _plugins_dir(self) -> Path:
        """User plugin directory (default: <project>/plugins)."""
        configured = str(self.store.value("plugins_folder", "") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return plugins_manager.default_plugins_dir(self.paths)

    def _user_guide_path(self) -> Path:
        return Path(self.paths["root"]) / "docs" / "user-guide" / "USER_GUIDE.html"

    def _open_user_guide(self) -> None:
        """Header ? button: the packaged HTML guide in the system browser."""
        path = self._user_guide_path()
        if not path.is_file():
            QMessageBox.information(
                self, self.i18n.t("user_guide_tip"), self.i18n.t("user_guide_missing", path=str(path))
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _open_plugins_folder(self) -> None:
        d = self._plugins_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d.resolve())))

    def _open_plugin(self, slot_id: str) -> None:
        d = self._plugins_dir()
        plugins_manager.ensure_bundled_plugins_seed(self.paths, d)
        path = plugins_manager.active_plugin_path(d, slot_id)
        if path is None or not path.is_file():
            QMessageBox.warning(
                self,
                self.i18n.t("box_tools_plugins"),
                self.i18n.t("plugin_missing", name=slot_id),
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        st = next(
            (s for s in plugins_manager.list_slot_status(d) if s["id"] == slot_id),
            None,
        )
        title = (st or {}).get("title_zh" if self.i18n.lang != "en" else "title_en") or slot_id
        self._status(self.i18n.t("plugin_opened", name=title))
        self._log(f"[plugins] open {path.name}")

    def _app_icon(self) -> QIcon:
        ico = Path(__file__).resolve().parent / "assets" / "app_icon.ico"
        if ico.is_file():
            return QIcon(str(ico))
        return self.windowIcon()

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return
        if self._tray is not None:
            return
        tray = QSystemTrayIcon(self._app_icon(), self)
        tray.setToolTip(f"{APP_NAME} v{APP_VERSION}")
        tray.activated.connect(self._on_tray_activated)
        self._tray = tray
        self._refresh_tray_menu()
        # Show tray icon only when actually needed (close/minimize to tray)
        # so it doesn't clutter the tray while the window is open.
        tray.hide()

    def _refresh_tray_menu(self) -> None:
        if self._tray is None:
            return
        t = self.i18n.t
        menu = QMenu()
        act_show = QAction(t("tray_show"), self)
        act_show.triggered.connect(self._show_from_tray)
        act_settings = QAction(t("settings"), self)
        act_settings.triggered.connect(self._open_settings)
        act_quit = QAction(t("tray_quit"), self)
        act_quit.triggered.connect(self._quit_from_tray)
        menu.addAction(act_show)
        menu.addAction(act_settings)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray.setContextMenu(menu)

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        # Keep tray icon only when close-to-tray is the configured close action
        if self._tray is not None and self._close_action() != "tray":
            self._tray.hide()

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        if self._tray is not None:
            self._tray.hide()
        self.close()
        # Closing a HIDDEN window emits no lastWindowClosed (nothing visible),
        # so exec() would keep running with zero windows — a windowless zombie
        # that then swallows every relaunch through the single-instance pipe
        # (v0.35.18: tray Quit from the tray-hidden state never exited).
        # quit() is idempotent; run_app's tail still does store.sync/hard-exit.
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_second_launch(self, conn=None) -> None:
        """A second process tried to start: surface this window instead."""
        self._show_from_tray()
        if conn is not None:
            # Handshake ack (v0.35.18): the launching process only exits once a
            # running window has actually answered. No answer = stuck instance.
            try:
                conn.write(b"ok")
                conn.flush()
                conn.waitForBytesWritten(400)
                conn.disconnectFromServer()
            except Exception:
                pass
        self._log(
            self._bi(
                "检测到重复启动，已激活当前窗口。",
                "Second launch detected — activated the running window.",
            )
        )

    def _hide_to_tray(self, notify: bool = True) -> bool:
        """Hide window into the tray. Returns False when no tray is available."""
        self._setup_tray()
        if self._tray is None:
            return False
        self._tray.show()
        self.hide()
        if notify and self._tray.supportsMessages():
            self._tray.showMessage(
                APP_NAME,
                self.i18n.t("tray_hidden"),
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        return True

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._force_quit and self._close_action() == "tray":
            if self._hide_to_tray(notify=True):
                event.ignore()
                return
            # tray unavailable — fall through and close normally
        if self._tray is not None:
            self._tray.hide()
        # Real quit (X with close_action=quit, or tray menu Quit): if a larch
        # job is still running we must hard-exit afterwards — see run_app tail.
        self._hard_exit_needed = self._workers_running()
        super().closeEvent(event)

    def _workers_running(self) -> bool:
        """Any background job (FEFF / fit / verify / AI Check) still running?"""
        workers = [getattr(self, attr, None) for attr in ("worker", "_verify_worker")]
        for worker in workers + list(getattr(self, "_ai_workers", [])):
            if worker is None:
                continue
            is_running = getattr(worker, "isRunning", None)
            try:
                if callable(is_running) and is_running():
                    return True
            except RuntimeError:
                pass
        return False

    def _coding_workdir(self) -> str:
        """Folder for coding agents / review terminal (current spectrum or project root)."""
        g = self.project.current
        if g and g.path:
            p = Path(g.path)
            d = p if p.is_dir() else p.parent
            if d.is_dir():
                return str(d.resolve())
        root = self.paths.get("root") if isinstance(self.paths, dict) else None
        if root and Path(root).is_dir():
            return str(Path(root).resolve())
        return str(Path.cwd())

    def _coding_agent_label(self, agent: str) -> str:
        return self.i18n.t(f"coding_{agent}") if agent in AGENTREG.AGENTS else agent

    def _glob_newest_file(self, pattern: str) -> Optional[Path]:
        """Return newest existing file matching a glob pattern (may include *)."""
        try:
            from glob import glob as _glob

            paths = [Path(p) for p in _glob(pattern) if Path(p).is_file()]
            if not paths:
                return None
            paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return paths[0]
        except Exception:
            return None

    def _protocol_registered(self, scheme: str) -> bool:
        """True if Windows URL protocol (e.g. codex / claude / kimi) is registered."""
        scheme = (scheme or "").strip().rstrip(":").lower()
        if not scheme:
            return False
        cache = getattr(self, "_protocol_cache", None)
        if cache is None:
            self._protocol_cache = {}
            cache = self._protocol_cache
        if scheme in cache:
            return cache[scheme]
        ok = False
        try:
            import winreg

            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, rf"Software\Classes\{scheme}"):
                        ok = True
                        break
                except OSError:
                    continue
        except Exception:
            ok = False
        cache[scheme] = ok
        return ok

    def _appx_aumi_available(self, aumi: str) -> bool:
        """True if AppUserModelId / package family looks installed (cached)."""
        aumi = (aumi or "").strip()
        if not aumi:
            return False
        cache = getattr(self, "_appx_cache", None)
        if cache is None:
            self._appx_cache = {}
            cache = self._appx_cache
        if aumi in cache:
            return cache[aumi]
        family = aumi.split("!", 1)[0]  # e.g. OpenAI.Codex_2p2nqsd0c76g0
        ok = False
        # Full package names look like Name_Version_Arch__PublisherId
        # Family is Name_PublisherId — not a literal substring of the full name.
        fam_name, fam_pub = (family.rsplit("_", 1) + [""])[:2] if "_" in family else (family, "")
        try:
            import winreg

            base = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    if name == family or name.startswith(family + "_"):
                        ok = True
                        break
                    if fam_pub and name.startswith(fam_name + "_") and (
                        name.endswith(fam_pub) or f"__{fam_pub}" in name
                    ):
                        ok = True
                        break
                    i += 1
        except Exception:
            pass
        # Start menu (covers non-MSIX ids like com.moonshot.kimichat) — only if reg miss
        if not ok:
            try:
                r = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"(Get-StartApps | Where-Object {{ $_.AppID -eq '{aumi}' }}).Count",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                ok = r.returncode == 0 and (r.stdout or "").strip() not in ("", "0")
            except Exception:
                ok = False
        cache[aumi] = ok
        return ok

    def _coding_launch_candidates(self, agent: str) -> List[str]:
        """
        Ordered launch targets for an agent.
        Formats:
          - appx:<AppUserModelId>   → shell:AppsFolder\\… (Store / MSIX desktop)
          - protocol:<scheme>:      → URL protocol (codex: / claude: / kimi:)
          - absolute .exe path      → process / new console
          - bare PATH name          → shutil.which
        Kimi / Grok prefer their coding CLIs in a visible terminal.  Codex /
        Claude retain the desktop-GUI-first order.
        """
        home = Path.home()
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        vscode_ext = home / ".vscode" / "extensions"
        cursor_ext = home / ".cursor" / "extensions"
        out: List[str] = []

        if agent == "codex":
            # Prefer URL protocol over appx (never restart explorer for launch)
            # Store app is labeled "ChatGPT" but package family OpenAI.Codex_*
            out.extend(
                [
                    "protocol:codex:",
                    "appx:OpenAI.Codex_2p2nqsd0c76g0!App",
                    str(local / "OpenAI" / "Codex" / "bin" / "codex.exe"),
                    str(
                        vscode_ext
                        / "openai.chatgpt-*-win32-x64"
                        / "bin"
                        / "windows-x86_64"
                        / "codex.exe"
                    ),
                    str(
                        cursor_ext
                        / "openai.chatgpt-*-win32-x64"
                        / "bin"
                        / "windows-x86_64"
                        / "codex.exe"
                    ),
                    str(home / ".codex" / "bin" / "codex.exe"),
                    "codex",
                ]
            )
        elif agent == "claude":
            out.extend(
                [
                    "protocol:claude:",
                    "appx:Claude_pzs8sxrjxfjjc!Claude",
                    str(local / "Programs" / "claude" / "Claude.exe"),
                    str(local / "Programs" / "Claude" / "Claude.exe"),
                    str(
                        vscode_ext
                        / "anthropic.claude-code-*-win32-x64"
                        / "resources"
                        / "native-binary"
                        / "claude.exe"
                    ),
                    str(
                        cursor_ext
                        / "anthropic.claude-code-*-win32-x64"
                        / "resources"
                        / "native-binary"
                        / "claude.exe"
                    ),
                    str(home / ".local" / "bin" / "claude.exe"),
                    str(home / ".claude" / "bin" / "claude.exe"),
                    # Claude desktop (MSIX) ships its own Claude Code CLI in a
                    # versioned folder that changes on every update; newest wins
                    str(
                        Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
                        / "Claude" / "claude-code" / "*" / "*" / "claude.exe"
                    ),
                    "claude",
                ]
            )
        elif agent == "kimi":
            out.extend(
                [
                    str(home / ".kimi-code" / "bin" / "kimi.exe"),
                    str(home / ".kimi-code" / "bin" / "kimi"),
                    "kimi",
                    "protocol:kimi:",
                    "appx:com.moonshot.kimichat",
                    str(local / "Programs" / "kimi-desktop" / "Kimi.exe"),
                    str(local / "Kimi-Code-Web" / "Kimi-Code-Web.exe"),
                ]
            )
        elif agent == "grok":
            out.extend(
                [
                    str(home / ".grok" / "bin" / "grok.exe"),
                    str(home / ".local" / "bin" / "grok.exe"),
                    "grok",
                    str(local / "xAI-Code-Web" / "xAI-Code-Web.exe"),
                ]
            )
        elif agent == "qwen":
            # Qwen Code self-updates; its launcher stays under qwen-code\bin
            out.extend(
                [
                    str(local / "qwen-code" / "bin" / "qwen.cmd"),
                    str(local / "qwen-code" / "qwen-code" / "bin" / "qwen.cmd"),
                    str(local / "qwen-code" / "bin" / "qwen.exe"),
                    "qwen.cmd",
                    "qwen",
                ]
            )
        elif agent == "deepseek":
            # DeepSeek Harness: npm i -g @deepseek-ai/dsh
            roaming = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
            out.extend(
                [
                    str(roaming / "npm" / "dsh.cmd"),
                    str(roaming / "npm" / "dsh.ps1"),
                    "dsh.cmd",
                    "dsh",
                ]
            )
        return out

    def _normalize_coding_target(self, raw: str) -> str:
        """Normalize user/settings text into a launch target string."""
        t = (raw or "").strip()
        if not t:
            return ""
        low = t.lower()
        # Already tagged
        if low.startswith("protocol:") or low.startswith("appx:"):
            return t
        # Allow pasting shell:AppsFolder\AUMI
        if low.startswith("shell:appsfolder\\"):
            return "appx:" + t.split("\\", 1)[1]
        if low.startswith("shell:appsfolder/"):
            return "appx:" + t.split("/", 1)[1]
        # bare protocol like "codex:" / "claude:"
        if t.endswith(":") and "\\" not in t and "/" not in t and len(t) < 32:
            return f"protocol:{low}"
        return t

    def _coding_target_is_launchable(self, target: str) -> bool:
        """Whether a resolved target can be launched now."""
        t = self._normalize_coding_target(target)
        if not t:
            return False
        if t.lower().startswith("appx:"):
            return self._appx_aumi_available(t[5:])
        if t.lower().startswith("protocol:"):
            scheme = t[9:].rstrip(":")
            return self._protocol_registered(scheme)
        # file or PATH name
        p = Path(os.path.expandvars(t))
        if p.is_file():
            return True
        if ("*" in t or "?" in t) and self._glob_newest_file(t) is not None:
            return True
        if AGENTREG.which_cli(t):
            return True
        return False

    def _resolve_coding_path(self, agent: str, prefer_saved: bool = True) -> Optional[str]:
        """
        Return launch target for agent (appx:/protocol:/exe path), or None.
        Kimi / Grok prefer coding CLIs; Codex / Claude prefer desktop GUIs.

        Important: the two preferences must not both rewrite the same saved
        path.  v0.34.2 migration (GUI→CLI for Kimi/Grok) plus the older
        Codex/Claude GUI upgrade (CLI→GUI) used to flip-flop on every Settings
        open when both a CLI and a Code-Web GUI were installed.
        """
        if prefer_saved:
            custom = self._normalize_coding_target(
                str(self.store.value(f"coding_path_{agent}", "") or "")
            )
            if custom and self._coding_target_is_launchable(custom):
                # Kimi / Grok / Qwen / DeepSeek are terminal-first: keep a saved
                # CLI; migrate stale desktop / Code-Web targets to the coding
                # CLI when one is installed.  Never re-upgrade a CLI to a GUI.
                if agent in AGENTREG.TERMINAL_FIRST:
                    if self._is_cli_only_coding_target(agent, custom):
                        return custom
                    cli = self._first_cli_coding_target(agent)
                    if cli:
                        return cli
                    return custom
                # Prefer protocol over saved appx (safer; appx used to restart explorer)
                if custom.lower().startswith("appx:"):
                    for c in self._coding_launch_candidates(agent):
                        if c.lower().startswith("protocol:"):
                            scheme = c[9:].rstrip(":")
                            if self._protocol_registered(scheme):
                                return c
                # Codex / Claude only: desktop-GUI preference over bare CLI paths.
                if agent in {"codex", "claude"} and self._is_cli_only_coding_target(
                    agent, custom
                ):
                    gui = self._first_gui_coding_target(agent)
                    if gui:
                        return gui
                return custom

        for c in self._coding_launch_candidates(agent):
            # Expand / validate each candidate
            if c.lower().startswith("appx:"):
                if self._appx_aumi_available(c[5:]):
                    return c
                continue
            if c.lower().startswith("protocol:"):
                scheme = c[9:].rstrip(":")
                if self._protocol_registered(scheme):
                    return c
                continue
            if "*" in c or "?" in c:
                hit = self._glob_newest_file(c)
                if hit is not None:
                    return str(hit.resolve())
                continue
            p = Path(os.path.expandvars(c))
            if p.is_file():
                if agent == "qwen" and not AGENTREG.qwen_binary_ok(str(p)):
                    continue
                return str(p.resolve())
            found = AGENTREG.which_cli(c)
            if found and (agent != "qwen" or AGENTREG.qwen_binary_ok(found)):
                return str(Path(found).resolve())
        return None

    def _is_cli_only_coding_target(self, agent: str, target: str) -> bool:
        """Heuristic: path looks like a coding CLI binary (not a desktop shell)."""
        t = self._normalize_coding_target(target).lower().replace("/", "\\")
        if t.startswith("appx:") or t.startswith("protocol:"):
            return False
        name = Path(t).name
        if agent == "qwen" and not AGENTREG.qwen_binary_ok(t):
            return False  # QwenCloud account CLI / launch-* helpers are not Qwen Code
        cli_names = AGENTREG.CLI_NAMES.get(agent, set())
        if name in cli_names:
            # Desktop shells / Code-Web are not "CLI only"
            if "code-web" in t or "kimi-desktop" in t or "\\programs\\claude" in t:
                return False
            return True
        return False

    def _first_cli_coding_target(self, agent: str) -> Optional[str]:
        """Return the first launchable CLI candidate for *agent*."""
        for candidate in self._coding_launch_candidates(agent):
            target = self._normalize_coding_target(candidate)
            if not self._is_cli_only_coding_target(agent, target):
                continue
            if not self._coding_target_is_launchable(target):
                continue
            if "*" in target or "?" in target:
                hit = self._glob_newest_file(target)
                if hit is not None:
                    return str(hit.resolve())
            path = Path(os.path.expandvars(target))
            if path.is_file():
                return str(path.resolve())
            found = AGENTREG.which_cli(target)
            if found:
                return str(Path(found).resolve())
            return target
        return None

    def _first_gui_coding_target(self, agent: str) -> Optional[str]:
        for c in self._coding_launch_candidates(agent):
            cl = c.lower()
            if cl.startswith("appx:") and self._appx_aumi_available(c[5:]):
                return c
            if cl.startswith("protocol:") and self._protocol_registered(c[9:].rstrip(":")):
                return c
            p = Path(os.path.expandvars(c))
            if not p.is_file():
                continue
            s = str(p).lower().replace("/", "\\")
            name = p.name.lower()
            if "code-web" in name or "kimi-desktop" in s:
                return str(p.resolve())
            if "\\programs\\" in s and name in ("kimi.exe", "claude.exe", "chatgpt.exe"):
                return str(p.resolve())
        return None

    def _browse_coding_client(self, agent: str, path_edit: "QLineEdit") -> None:
        """File dialog to pick agent client; save to settings + field."""
        label = self._coding_agent_label(agent)
        start = path_edit.text().strip()
        if not start or start.lower().startswith(("appx:", "protocol:", "shell:")):
            resolved = self._resolve_coding_path(agent) or ""
            if resolved and Path(os.path.expandvars(resolved)).is_file():
                start = resolved
            else:
                start = str(Path.home())
        start_path = Path(os.path.expandvars(start))
        start_dir = str(start_path.parent) if start_path.suffix else str(start_path)
        if not Path(start_dir).is_dir():
            start_dir = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.i18n.t("coding_browse_title", name=label),
            start_dir,
            "Executable (*.exe *.cmd *.bat);;All (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path:
            return
        path_edit.setText(path)
        self.store.setValue(f"coding_path_{agent}", path)
        self._log(self.i18n.t("coding_path_saved", name=label) + f": {path}")

    def _open_coding_agent(self, agent: str, path_override: str = "") -> None:
        """Launch a coding agent using its configured agent-specific preference."""
        label = self._coding_agent_label(agent)
        cwd = self._coding_workdir()
        raw = (path_override or "").strip()
        path = self._normalize_coding_target(raw) if raw else ""
        if not path or not self._coding_target_is_launchable(path):
            path = self._resolve_coding_path(agent) or ""
        if not path or not self._coding_target_is_launchable(path):
            QMessageBox.warning(
                self,
                self.i18n.t("settings"),
                self.i18n.t("coding_need_path", name=label),
            )
            self._log(f"Coding agent path missing: {agent}")
            return
        # Remember successful target
        self.store.setValue(f"coding_path_{agent}", path)
        try:
            self._launch_coding_target(path, cwd, label, agent=agent)
            self._log(f"Launched {label}: {path}  cwd={cwd}")
        except Exception as exc:
            QMessageBox.warning(
                self,
                self.i18n.t("settings"),
                self.i18n.t("coding_open_failed", name=label, detail=str(exc)),
            )
            self._log(f"Launch {agent} failed: {exc}")

    def _coding_api_inject_enabled(self) -> bool:
        return str(self.store.value("coding_api_inject", "true")).lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def _coding_api_keys(self) -> dict:
        """Return bound API keys from settings (empty strings omitted)."""
        mapping = {
            prov: str(self.store.value(store_key, "") or "").strip()
            for prov, _label, store_key in AGENTREG.API_SLOTS
        }
        return {k: v for k, v in mapping.items() if v}

    def _qwen_region(self) -> str:
        """Token Plan region of the saved Qwen key: "intl" (default) or "cn"."""
        r = str(self.store.value("coding_api_qwen_region", "intl") or "intl").strip().lower()
        return r if r in ("intl", "cn") else "intl"

    def _coding_env(self, agent: Optional[str] = None) -> dict:
        """
        Environment for one selected terminal / CLI launch.

        A child receives only its own provider key. ``agent=None`` is
        deliberately key-free for blank terminals and installer processes.
        """
        env = os.environ.copy()
        if not self._coding_api_inject_enabled():
            return env
        agent = str(agent or "").strip().lower()
        keys = self._coding_api_keys()
        key = keys.get(AGENTREG.AGENT_PROVIDER.get(agent, ""), "")
        if key:
            for var in AGENTREG.AGENT_ENV.get(agent, ()):
                env[var] = key
        return env

    @staticmethod
    def _shell_execute_open(target: str) -> bool:
        """
        Open a URL / shell: path via ShellExecuteW.
        Never spawn explorer.exe as a process (that restarts the desktop shell
        and can turn the whole desktop black).
        """
        try:
            import ctypes

            # > 32 means success for ShellExecute
            rc = int(
                ctypes.windll.shell32.ShellExecuteW(
                    None, "open", target, None, None, 1
                )
            )
            return rc > 32
        except Exception:
            return False

    def _launch_coding_target(
        self, target: str, cwd: str, title: str, *, agent: Optional[str] = None
    ) -> None:
        """Dispatch launch for appx / protocol / GUI exe / CLI."""
        t = self._normalize_coding_target(target)
        tl = t.lower()

        # --- Store / MSIX desktop apps (NO explorer.exe) ---
        if tl.startswith("appx:"):
            aumi = t[5:]
            shell_target = f"shell:AppsFolder\\{aumi}"
            if self._shell_execute_open(shell_target):
                return
            # Fallback: cmd start — still does not restart the explorer shell
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", shell_target],
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return

        # --- URL protocols (codex: / claude: / kimi:) ---
        if tl.startswith("protocol:"):
            url = t[9:]
            if not url.endswith(":"):
                url = url + ":"
            if self._shell_execute_open(url):
                return
            try:
                os.startfile(url)  # type: ignore[attr-defined]
            except Exception:
                subprocess.Popen(
                    ["cmd.exe", "/c", "start", "", url],
                    close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            return

        # --- Filesystem path / PATH name ---
        exe_s = t
        if "*" in t or "?" in t:
            hit = self._glob_newest_file(t)
            if hit is None:
                raise FileNotFoundError(t)
            exe_s = str(hit.resolve())
        else:
            p = Path(os.path.expandvars(t))
            if p.is_file():
                exe_s = str(p.resolve())
            else:
                found = AGENTREG.which_cli(t)
                if not found:
                    raise FileNotFoundError(t)
                exe_s = str(Path(found).resolve())
        # npm-style script launchers (qwen.cmd, dsh.cmd, dsh.ps1) are CLIs:
        # run them through their interpreter in a visible terminal
        if exe_s.lower().endswith((".cmd", ".bat")):
            self._spawn_cli_in_terminal(["cmd.exe", "/d", "/k", exe_s], cwd, title, env=self._coding_env(agent))
            return
        if exe_s.lower().endswith(".ps1"):
            self._spawn_cli_in_terminal(
                ["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", exe_s],
                cwd, title, env=self._coding_env(agent),
            )
            return

        exe = Path(exe_s)
        name_l = exe.name.lower()
        # GUI desktop shells (detached, no console). kimi.exe is only GUI when it
        # is the kimi-desktop client (checked below via path) — elsewhere it is
        # the Kimi Code CLI and must get a terminal.
        gui_names = {
            "kimi-code-web.exe",
            "xai-code-web.exe",
            "chatgpt.exe",
            "claude.exe",  # desktop Claude under Programs\, not always CLI
        }
        is_gui = (
            "code-web" in name_l
            or name_l in gui_names
            or "kimi-desktop" in str(exe).lower()
            or "\\programs\\claude" in str(exe).lower().replace("/", "\\")
        )
        # Extension / OpenAI bin CLI tools need a console
        is_cli = (
            name_l in ("codex.exe", "claude.exe", "grok.exe", "agent.exe", "kimi.exe")
            and (
                "\\openai\\codex\\bin\\" in str(exe).lower().replace("/", "\\")
                or "native-binary" in str(exe).lower()
                or "extensions" in str(exe).lower()
                or "\\.grok\\bin\\" in str(exe).lower().replace("/", "\\")
                or "\\.codex\\" in str(exe).lower().replace("/", "\\")
                or "\\.claude\\" in str(exe).lower().replace("/", "\\")
                or "\\.kimi-code\\" in str(exe).lower().replace("/", "\\")
                # Claude desktop's bundled Claude Code (v3.19.7 search path)
                or "\\claude-code\\" in str(exe).lower().replace("/", "\\")
            )
        )
        env = self._coding_env(agent)
        if is_gui and not is_cli:
            subprocess.Popen(
                [str(exe)],
                cwd=str(exe.parent),
                env=env,
                close_fds=True,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:
            self._spawn_cli_in_terminal([str(exe)], cwd, title, env=env)

    def _spawn_cli_in_terminal(
        self,
        argv: List[str],
        cwd: str,
        title: str,
        env: Optional[dict] = None,
    ) -> None:
        """Run CLI in Windows Terminal if available, else new console window."""
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        run_env = env if env is not None else self._coding_env(None)
        # Prefer Windows Terminal so TUI clients (codex/claude) get a proper console
        wt = shutil.which("wt") or shutil.which("wt.exe")
        if wt:
            try:
                subprocess.Popen(
                    [wt, "-d", cwd, "--title", title] + list(argv),
                    env=run_env,
                    close_fds=True,
                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                return
            except Exception:
                pass
        subprocess.Popen(
            argv,
            cwd=cwd,
            env=run_env,
            creationflags=flags,
            close_fds=True,
        )

    def _rebuild_ai_menu(self) -> None:
        """Populate log-band AI dropdown: open each coding client only."""
        menu = getattr(self, "_ai_menu", None)
        if menu is None:
            return
        menu.clear()
        t = self.i18n.t
        for key in AGENTREG.AGENTS:
            label = t(f"coding_{key}")
            path = self._resolve_coding_path(key) or ""
            act = QAction(label, menu)
            act.setEnabled(bool(path and self._coding_target_is_launchable(path)))
            if path:
                act.setToolTip(path)
            else:
                act.setToolTip(t("coding_need_path", name=label))
            act.triggered.connect(lambda *_a, k=key: self._open_coding_agent(k))
            menu.addAction(act)

    def _open_coding_terminal(self) -> None:
        """Open a key-free blank review terminal in the data folder."""
        cwd = self._coding_workdir()
        env = self._coding_env(None)
        try:
            wt = shutil.which("wt") or shutil.which("wt.exe")
            if wt:
                subprocess.Popen(
                    [wt, "-d", cwd, "--title", "smartLarch Platform review"],
                    env=env,
                    close_fds=True,
                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            else:
                subprocess.Popen(
                    ["cmd.exe", "/k"],
                    cwd=cwd,
                    env=env,
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
            self._log(self._bi(f"审核终端 → {cwd}", f"Review terminal → {cwd}"))
        except Exception as exc:
            QMessageBox.warning(
                self,
                self.i18n.t("coding_terminal"),
                self.i18n.t("coding_open_failed", name=self.i18n.t("coding_terminal"), detail=str(exc)),
            )

    def _install_ai_cli(self, agent: str) -> None:
        """Open the vendor installation guide; never execute downloaded installer code."""
        answer = QMessageBox.question(
            self, self.i18n.t("ai_cli_install"), self.i18n.t("ai_cli_install_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        url = ai_cli_install_command(agent)
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, self.i18n.t("ai_cli_install"), url)

    def _open_settings(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(self.i18n.t("settings"))
        dlg.setMinimumWidth(640)  # AI rows: tick | name | key or path | model or buttons | mark
        dlg.setMinimumHeight(420)
        # Root: fixed tip on top + scrollable body absorbs vertical resize
        # (avoid stretch going into the small hint label).
        root = QVBoxLayout(dlg)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        hint = QLabel(self.i18n.t("settings_auto_saved"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b625d; font-size:11px;")
        hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        root.addWidget(hint, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(10)

        # --- General: language + behaviour + tray + cache + updates (one frame) ---
        box_gen = QGroupBox(self.i18n.t("settings_general_block"))
        gen = QFormLayout(box_gen)
        gen.setSpacing(6)
        gen.setVerticalSpacing(6)

        title_ink = "#e6edf3" if getattr(self, "_theme_dark", False) else "#333"
        hint_ink = "#9fb0c0" if getattr(self, "_theme_dark", False) else "#6b625d"

        def _section_title(text: str) -> QLabel:
            """Bold folder/block title with extra top gap so sections do not blend."""
            lab = QLabel(text)
            lab.setStyleSheet(
                f"font-weight:700; font-size:12px; color:{title_ink};"
                " margin-top:12px; margin-bottom:2px; padding-top:4px;"
            )
            lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            return lab

        def _section_hint(text: str) -> QLabel:
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setStyleSheet(
                f"color:{hint_ink}; font-size:11px; font-weight:400;"
                " margin-top:0; margin-bottom:4px;"
            )
            lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            return lab
        # Language: two side-by-side radios (no dropdown)
        rad_lang_zh = QRadioButton(self.i18n.t("lang_zh"))
        rad_lang_en = QRadioButton(self.i18n.t("lang_en"))
        grp_lang = QButtonGroup(dlg)
        grp_lang.setExclusive(True)
        grp_lang.addButton(rad_lang_zh)
        grp_lang.addButton(rad_lang_en)
        if self.i18n.lang == "en":
            rad_lang_en.setChecked(True)
        else:
            rad_lang_zh.setChecked(True)
        lang_row = QHBoxLayout()
        lang_row.setContentsMargins(0, 0, 0, 0)
        lang_row.setSpacing(16)
        lang_row.addWidget(rad_lang_zh)
        lang_row.addWidget(rad_lang_en)
        lang_row.addStretch(1)
        gen.addRow(self.i18n.t("settings_language"), lang_row)
        # On window close: directly under language (same two-option radio layout)
        rad_close_quit = QRadioButton(self.i18n.t("close_action_quit"))
        rad_close_tray = QRadioButton(self.i18n.t("close_action_tray"))
        grp_close = QButtonGroup(dlg)
        grp_close.setExclusive(True)
        grp_close.addButton(rad_close_quit)
        grp_close.addButton(rad_close_tray)
        if self._close_action() == "tray":
            rad_close_tray.setChecked(True)
        else:
            rad_close_quit.setChecked(True)
        close_row = QHBoxLayout()
        close_row.setContentsMargins(0, 0, 0, 0)
        close_row.setSpacing(16)
        close_row.addWidget(rad_close_quit)
        close_row.addWidget(rad_close_tray)
        close_row.addStretch(1)
        gen.addRow(self.i18n.t("close_action"), close_row)
        chk_aes = QCheckBox(self.i18n.t("auto_edge_on_select"))
        chk_aes.setChecked(self.chk_auto_edge_on_select.isChecked())
        chk_aes.toggled.connect(self.chk_auto_edge_on_select.setChecked)
        gen.addRow(chk_aes)
        chk_auto_re = QCheckBox(self.i18n.t("auto_reprocess"))
        chk_auto_re.setChecked(self.chk_auto.isChecked())
        chk_auto_re.toggled.connect(self.chk_auto.setChecked)
        gen.addRow(chk_auto_re)
        chk_raw_noask = QCheckBox(self.i18n.t("imp_noask"))
        chk_raw_noask.setObjectName("rawImportNoAskCheck")
        chk_raw_noask.setChecked(
            str(self.store.value("raw_import_noask", False) or "").lower()
            in ("1", "true", "yes")
        )
        chk_raw_noask.toggled.connect(
            lambda checked: self.store.setValue("raw_import_noask", bool(checked))
        )
        gen.addRow(chk_raw_noask)

        # Folder blocks: bold titles + extra top gap (same form was blending together)
        lbl_cache_title = _section_title(self.i18n.t("settings_cache_folder"))
        gen.addRow(lbl_cache_title)
        cache_hint = _section_hint(self.i18n.t("settings_cache_folder_hint"))
        gen.addRow(cache_hint)
        cache_row = QHBoxLayout()
        ed_cache = QLineEdit(str(self._cache_root()))
        btn_cache_browse = QPushButton(self.i18n.t("browse"))
        btn_cache_browse.setObjectName("ghostBtn")
        btn_cache_open = QPushButton(self.i18n.t("open_folder"))
        btn_cache_open.setObjectName("ghostBtn")
        btn_cache_clear = QPushButton(self.i18n.t("settings_cache_clear"))
        btn_cache_clear.setObjectName("ghostBtn")
        cache_row.addWidget(ed_cache, 1)
        cache_row.addWidget(btn_cache_browse)
        cache_row.addWidget(btn_cache_open)
        cache_row.addWidget(btn_cache_clear)
        gen.addRow(cache_row)

        # Curve / Analysis data export folder moved to the Tools→Export panel (v0.34.9).

        # Engineering mode layout export folder (draft JSON for agents)
        from .eng_mode import SETTINGS_KEY_ENG_EXPORT_DIR, default_ui_dir, project_ui_dir

        lbl_eng_title = _section_title(self.i18n.t("settings_eng_export_folder"))
        gen.addRow(lbl_eng_title)
        eng_hint = _section_hint(self.i18n.t("settings_eng_export_hint"))
        gen.addRow(eng_hint)
        eng_row = QHBoxLayout()
        default_eng = str(default_ui_dir(self.paths))
        saved_eng = str(self.store.value(SETTINGS_KEY_ENG_EXPORT_DIR, "") or "").strip()
        ed_eng = QLineEdit(saved_eng or default_eng)
        ed_eng.setPlaceholderText(default_eng)
        btn_eng_browse = QPushButton(self.i18n.t("browse"))
        btn_eng_browse.setObjectName("ghostBtn")
        btn_eng_open = QPushButton(self.i18n.t("open_folder"))
        btn_eng_open.setObjectName("ghostBtn")
        btn_eng_reset = QPushButton(self.i18n.t("settings_eng_export_reset"))
        btn_eng_reset.setObjectName("ghostBtn")
        eng_row.addWidget(ed_eng, 1)
        eng_row.addWidget(btn_eng_browse)
        eng_row.addWidget(btn_eng_open)
        eng_row.addWidget(btn_eng_reset)
        gen.addRow(eng_row)

        # Main program updates
        lbl_update_title = _section_title(self.i18n.t("settings_update_folder"))
        gen.addRow(lbl_update_title)
        folder_hint = _section_hint(self.i18n.t("settings_update_folder_hint"))
        gen.addRow(folder_hint)
        row = QHBoxLayout()
        ed_folder = QLineEdit(self._update_folder())
        ed_folder.setPlaceholderText(r"D:\…\releases  or  \\share\smartLarchPlatform")
        btn_browse = QPushButton(self.i18n.t("browse"))
        btn_browse.setObjectName("ghostBtn")
        btn_open = QPushButton(self.i18n.t("open_folder"))
        btn_open.setObjectName("ghostBtn")
        row.addWidget(ed_folder, 1)
        row.addWidget(btn_browse)
        row.addWidget(btn_open)
        gen.addRow(row)

        # Plugin directory
        lbl_plug_title = _section_title(self.i18n.t("settings_plugins_folder"))
        gen.addRow(lbl_plug_title)
        plug_hint = _section_hint(self.i18n.t("settings_plugins_folder_hint"))
        gen.addRow(plug_hint)
        plug_row = QHBoxLayout()
        default_plug = str(plugins_manager.default_plugins_dir(self.paths))
        ed_plugins = QLineEdit(str(self._plugins_dir()))
        ed_plugins.setPlaceholderText(default_plug)
        btn_plug_browse = QPushButton(self.i18n.t("browse"))
        btn_plug_browse.setObjectName("ghostBtn")
        btn_plug_open = QPushButton(self.i18n.t("open_folder"))
        btn_plug_open.setObjectName("ghostBtn")
        plug_row.addWidget(ed_plugins, 1)
        plug_row.addWidget(btn_plug_browse)
        plug_row.addWidget(btn_plug_open)
        gen.addRow(plug_row)
        # Auto-update runs on startup — no Check / Open-new-version buttons
        lbl_auto = _section_hint(self.i18n.t("settings_update_auto_note"))
        gen.addRow(lbl_auto)

        # Scientific runtime (managed Miniconda / existing xraylarch)
        lbl_rt_title = _section_title(self.i18n.t("settings_runtime_block"))
        gen.addRow(lbl_rt_title)
        gen.addRow(_section_hint(self.i18n.t("settings_runtime_hint")))
        rt_py = sys.executable
        marker = runtime_paths.read_runtime_marker() or {}
        if marker.get("python"):
            rt_py = str(marker.get("python"))
        ed_runtime = QLineEdit(rt_py)
        ed_runtime.setReadOnly(True)
        gen.addRow(self.i18n.t("settings_runtime_path"), ed_runtime)
        btn_rt_install = QPushButton(self.i18n.t("settings_runtime_install"))
        btn_rt_install.setObjectName("ghostBtn")

        def _install_runtime() -> None:
            root = Path(self.paths["root"])
            if sys.platform.startswith("win"):
                script = root / "Install-Runtime.ps1"
                if not script.is_file():
                    QMessageBox.warning(
                        dlg, APP_NAME, f"Missing {script.name} next to the app."
                    )
                    return
                subprocess.Popen(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                    ],
                    cwd=str(root),
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
            else:
                script = root / "Install-Runtime.sh"
                if not script.is_file():
                    QMessageBox.warning(
                        dlg, APP_NAME, f"Missing {script.name} next to the app."
                    )
                    return
                subprocess.Popen(
                    ["bash", str(script)],
                    cwd=str(root),
                    start_new_session=True,
                )
            self._log("[runtime] launched Install-Runtime script")

        gen.addRow(btn_rt_install)
        btn_rt_install.clicked.connect(_install_runtime)
        lay.addWidget(box_gen)

        # --- AI (v4.4.0): every CLI row and API row has its own "AI Check" tick;
        # only ticked rows are used, so a CLI and an API can never fight over
        # one shared Default-AI picker.  Auto = ticked CLIs first, then APIs.
        self._migrate_ai_activation()
        ai_tr: list = []  # retranslate callbacks of the three AI blocks
        status_rows: dict = {}  # ("cli"|"api", name) -> Verify mark
        model_boxes: dict = {}  # provider -> model picker

        def _small(text: str = "") -> QLabel:
            lab = QLabel(text)
            lab.setStyleSheet("color:#6b625d; font-size:11px;")
            return lab

        def _mark() -> QLabel:
            lab = QLabel()
            lab.setFixedWidth(18)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return lab

        def _mark_state(kind: str, name: str) -> str:
            on = self._ai_cli_active(name) if kind == "cli" else self._ai_api_active(name)
            return "unknown" if on else "off"

        # --- Coding agents: [AI Check] name | client | Browse… | Open | mark ---
        box_code = QGroupBox(self.i18n.t("settings_coding_block"))
        code_lay = QVBoxLayout(box_code)
        code_hint = QLabel(self.i18n.t("settings_coding_hint"))
        code_hint.setWordWrap(True)
        code_hint.setStyleSheet("color:#6b625d; font-size:11px;")
        code_lay.addWidget(code_hint)
        code_grid = QGridLayout()
        code_grid.setHorizontalSpacing(6)
        code_grid.setColumnStretch(2, 1)
        head_code_ai = _small()
        head_code_mark = _small()
        code_grid.addWidget(head_code_ai, 0, 0)
        code_grid.addWidget(head_code_mark, 0, 5)
        path_eds: dict = {}
        for r, key in enumerate(AGENTREG.AGENTS, start=1):
            name_key = f"coding_{key}"
            supported = key in AGENTREG.AI_CHECK_CLIS
            chk = QCheckBox()
            chk.setEnabled(supported)
            chk.setChecked(supported and self._ai_cli_active(key))
            lbl = QLabel(self.i18n.t(name_key))
            lbl.setMinimumWidth(72)
            ed = QLineEdit()
            # Prefill with best launchable target and migrate stale auto-saved defaults.
            found = self._resolve_coding_path(key, prefer_saved=True) or ""
            ed.setText(found)
            if found:
                self.store.setValue(f"coding_path_{key}", found)
            path_eds[key] = ed
            btn_browse = QPushButton()
            btn_browse.setObjectName("ghostBtn")
            btn_browse.clicked.connect(
                lambda *_a, k=key, e=ed: self._browse_coding_client(k, e)
            )
            btn_open = QPushButton()
            btn_open.clicked.connect(
                lambda *_a, k=key, e=ed: self._open_coding_agent(k, e.text().strip())
            )
            mark = _mark()
            code_grid.addWidget(chk, r, 0, Qt.AlignmentFlag.AlignCenter)
            code_grid.addWidget(lbl, r, 1)
            code_grid.addWidget(ed, r, 2)
            code_grid.addWidget(btn_browse, r, 3)
            code_grid.addWidget(btn_open, r, 4)
            code_grid.addWidget(mark, r, 5)
            if supported:
                status_rows[("cli", key)] = mark
                self._set_ai_status(mark, _mark_state("cli", key))
                chk.toggled.connect(lambda on, k=key: _set_active("cli", k, on))
            cli_path = self._resolve_coding_cli_path(key) if supported else None

            def _tr_code_row(c=chk, lb=lbl, e=ed, b=btn_browse, o=btn_open, nk=name_key,
                             s=supported, cp=cli_path) -> None:
                t = self.i18n.t
                name = t(nk)
                lb.setText(name)
                if s:
                    used = t("ai_cli_uses", path=cp) if cp else t("ai_cli_not_found", name=name)
                    c.setToolTip(t("ai_cli_active_tip", name=name) + "\n" + used)
                else:
                    c.setToolTip(t("ai_cli_no_check", name=name))
                e.setPlaceholderText(t("coding_client_path"))
                e.setToolTip(t("settings_coding_hint") + "\n\nappx:… / protocol:codex: / full path to .exe")
                b.setText(t("coding_browse"))
                b.setToolTip(t("coding_browse_title", name=name))
                o.setText(t("coding_open"))
                o.setToolTip(t("coding_open"))

            ai_tr.append(_tr_code_row)
        code_lay.addLayout(code_grid)
        btn_install_cli = QPushButton()
        btn_install_cli.setObjectName("ghostBtn")
        install_menu = QMenu(btn_install_cli)
        install_acts = []
        for key in AGENTREG.AGENTS:
            act = install_menu.addAction(self.i18n.t(f"coding_{key}"))
            act.triggered.connect(lambda *_a, k=key: self._install_ai_cli(k))
            install_acts.append((act, f"coding_{key}"))
        btn_install_cli.setMenu(install_menu)
        install_row = QHBoxLayout()
        install_row.addStretch(1)
        install_row.addWidget(btn_install_cli)
        code_lay.addLayout(install_row)
        lay.addWidget(box_code)

        # --- API binding: [AI Check] provider | key | model | mark ---
        box_api = QGroupBox(self.i18n.t("settings_api_block"))
        api_lay = QVBoxLayout(box_api)
        api_hint = QLabel(self.i18n.t("settings_api_hint"))
        api_hint.setWordWrap(True)
        api_hint.setStyleSheet("color:#6b625d; font-size:11px;")
        api_lay.addWidget(api_hint)
        api_grid = QGridLayout()
        api_grid.setHorizontalSpacing(6)
        api_grid.setColumnStretch(2, 1)
        api_grid.setColumnStretch(3, 1)
        head_api_ai = _small()
        head_api_key = _small()
        head_api_model = _small()
        head_api_mark = _small()
        api_grid.addWidget(head_api_ai, 0, 0)
        api_grid.addWidget(head_api_key, 0, 2)
        api_grid.addWidget(head_api_model, 0, 3)
        api_grid.addWidget(head_api_mark, 0, 4)
        api_eds: dict = {}
        r = 1
        for prov, i18n_key, store_key in AGENTREG.API_SLOTS:
            chk = QCheckBox()
            chk.setChecked(self._ai_api_active(prov))
            lab = QLabel(self.i18n.t(i18n_key))
            ed = QLineEdit()
            ed.setEchoMode(QLineEdit.EchoMode.Password)
            ed.setText(str(self.store.value(store_key, "") or ""))
            ed.setMinimumWidth(140)

            def _save_api(_t="", sk=store_key, e=ed) -> None:
                self.store.setValue(sk, e.text().strip())

            ed.editingFinished.connect(_save_api)
            api_eds[store_key] = ed
            model = QComboBox()
            model.setEditable(True)
            model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            model.setMinimumWidth(140)
            model.addItems(self._ai_models_for(prov))
            model.setEditText(self._ai_review_model(prov))

            def _save_model(*_a, p=prov, m=model) -> None:
                self.store.setValue(f"ai_review_model_{p}", m.currentText().strip())

            model.lineEdit().editingFinished.connect(_save_model)
            model.activated.connect(_save_model)
            model_boxes[prov] = model
            mark = _mark()
            status_rows[("api", prov)] = mark
            self._set_ai_status(mark, _mark_state("api", prov))
            chk.toggled.connect(lambda on, p=prov: _set_active("api", p, on))
            api_grid.addWidget(chk, r, 0, Qt.AlignmentFlag.AlignCenter)
            api_grid.addWidget(lab, r, 1)
            api_grid.addWidget(ed, r, 2)
            api_grid.addWidget(model, r, 3)
            api_grid.addWidget(mark, r, 4)

            def _tr_api_row(c=chk, lb=lab, e=ed, m=model, ik=i18n_key) -> None:
                t = self.i18n.t
                lb.setText(t(ik))
                c.setToolTip(t("ai_api_active_tip", name=t(ik)))
                e.setPlaceholderText(t("api_key_ph"))
                m.setToolTip(t("ai_review_model_tip"))
                m.lineEdit().setPlaceholderText(t("ai_review_model_ph"))

            ai_tr.append(_tr_api_row)
            r += 1
            if prov == "qwen":
                # Token Plan keys only work on their own region's endpoint
                lbl_region = QLabel()
                cmb_qwen_region = QComboBox()
                cmb_qwen_region.addItem(self.i18n.t("api_qwen_region_intl"), "intl")
                cmb_qwen_region.addItem(self.i18n.t("api_qwen_region_cn"), "cn")
                cmb_qwen_region.setCurrentIndex(max(0, cmb_qwen_region.findData(self._qwen_region())))
                cmb_qwen_region.currentIndexChanged.connect(
                    lambda *_a, c=cmb_qwen_region: self.store.setValue("coding_api_qwen_region", c.currentData())
                )
                api_grid.addWidget(lbl_region, r, 1)
                api_grid.addWidget(cmb_qwen_region, r, 2)

                def _tr_region(lb=lbl_region, c=cmb_qwen_region) -> None:
                    t = self.i18n.t
                    lb.setText(t("api_qwen_region"))
                    c.setItemText(0, t("api_qwen_region_intl"))
                    c.setItemText(1, t("api_qwen_region_cn"))
                    c.setToolTip(t("api_qwen_region_tip"))

                ai_tr.append(_tr_region)
                r += 1
        api_lay.addLayout(api_grid)
        chk_inject = QCheckBox()
        chk_inject.setChecked(self._coding_api_inject_enabled())
        api_lay.addWidget(chk_inject)
        btn_api_term = QPushButton()
        btn_api_term.setObjectName("ghostBtn")
        api_lay.addWidget(btn_api_term)
        lay.addWidget(box_api)

        # --- AI Check: which ticked channel, the order auto uses, Verify ---
        box_ai = QGroupBox()
        ai_lay = QVBoxLayout(box_ai)
        ai_hint = QLabel()
        ai_hint.setWordWrap(True)
        ai_hint.setStyleSheet("color:#6b625d; font-size:11px;")
        ai_lay.addWidget(ai_hint)
        use_form = QFormLayout()
        cmb_use = QComboBox()
        use_form.addRow(QLabel(), cmb_use)
        ai_lay.addLayout(use_form)
        lbl_order = QLabel()
        lbl_order.setWordWrap(True)
        ai_lay.addWidget(lbl_order)
        btn_verify_ai = QPushButton()
        btn_verify_ai.setObjectName("ghostBtn")
        ai_lay.addWidget(btn_verify_ai)
        lay.addWidget(box_ai)

        def _fill_use() -> None:
            """'AI Check uses' lists Auto + the ticked channels; an unticked pick falls back to Auto."""
            current = self._ai_check_use()
            cmb_use.blockSignals(True)
            cmb_use.clear()
            cmb_use.addItem(self.i18n.t("ai_check_use_auto"), "auto")
            for kind, name in self._ai_ticked_keys():
                cmb_use.addItem(self._ai_channel_label(kind, name), f"{kind}:{name}")
            idx = cmb_use.findData(current)
            if idx < 0:
                idx = 0
                self.store.setValue("ai_check_use", "auto")
            cmb_use.setCurrentIndex(idx)
            cmb_use.blockSignals(False)
            lbl_order.setText(self._ai_order_text())

        def _set_active(kind: str, name: str, on: bool) -> None:
            self.store.setValue(f"ai_active/{kind}_{name}", bool(on))
            self._set_ai_status(status_rows[(kind, name)], _mark_state(kind, name))
            _fill_use()

        def _on_use(idx: int) -> None:
            self.store.setValue("ai_check_use", str(cmb_use.itemData(idx) or "auto"))
            lbl_order.setText(self._ai_order_text())

        def _refresh_models() -> None:
            """Verify fetched each provider's live model list: offer it, keep the choice."""
            for prov, box in model_boxes.items():
                text = box.currentText()
                box.blockSignals(True)
                box.clear()
                box.addItems(self._ai_models_for(prov))
                box.setEditText(text)
                box.blockSignals(False)

        def _tr_ai_blocks() -> None:
            t = self.i18n.t
            box_code.setTitle(t("settings_coding_block"))
            code_hint.setText(t("settings_coding_hint"))
            head_code_ai.setText(t("ai_col_active"))
            head_code_mark.setText(t("ai_col_mark"))
            btn_install_cli.setText(t("ai_cli_install"))
            for act, nk in install_acts:
                act.setText(t(nk))
            box_api.setTitle(t("settings_api_block"))
            api_hint.setText(t("settings_api_hint"))
            head_api_ai.setText(t("ai_col_active"))
            head_api_key.setText(t("ai_col_key"))
            head_api_model.setText(t("ai_col_model"))
            head_api_mark.setText(t("ai_col_mark"))
            chk_inject.setText(t("api_inject"))
            btn_api_term.setText(t("api_open_terminal"))
            box_ai.setTitle(t("settings_ai_check_block"))
            ai_hint.setText(t("ai_check_settings_hint"))
            use_label = use_form.labelForField(cmb_use)
            if use_label is not None:
                use_label.setText(t("ai_check_use"))
            cmb_use.setToolTip(t("ai_check_use_tip"))
            btn_verify_ai.setText(t("ai_verify"))
            btn_verify_ai.setToolTip(t("ai_verify_tip"))
            _fill_use()

        ai_tr.append(_tr_ai_blocks)
        for fn in ai_tr:
            fn()
        cmb_use.currentIndexChanged.connect(_on_use)
        btn_verify_ai.clicked.connect(
            lambda *_: self._verify_ai_connections(
                status_rows, btn_verify_ai, lbl_order, on_done=_refresh_models
            )
        )

        # --- About: plain button only (no outer group box) ---
        btn_about = QPushButton(self.i18n.t("about"))
        btn_about.setObjectName("ghostBtn")
        btn_about.clicked.connect(self._about)
        lay.addWidget(btn_about)
        lay.addStretch(0)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)  # vertical stretch goes here, not the tip line
        # No bottom Close button — use the title-bar close (X)

        # Fit the dialog to the content height (capped by the screen) and cap
        # the maximum height at the content: no initial scrollbar, and no
        # empty void under the About button no matter how far it is dragged.
        def _fit_settings_height() -> None:
            lay.activate()
            content_h = body.sizeHint().height()
            chrome_h = (
                hint.sizeHint().height()
                + root.contentsMargins().top()
                + root.contentsMargins().bottom()
                + root.spacing()
            )
            screen_cap = int(QApplication.primaryScreen().availableGeometry().height() * 0.85)
            dlg.setMaximumHeight(min(content_h + chrome_h + 8, screen_cap))

        _fit_settings_height()
        dlg.resize(max(560, dlg.sizeHint().width()), dlg.maximumHeight())

        def _save_folder() -> None:
            path = ed_folder.text().strip()
            self.store.setValue("update_folder", path)

        def _save_plugins_folder() -> None:
            path = ed_plugins.text().strip()
            if path:
                self.store.setValue("plugins_folder", path)
            else:
                self.store.remove("plugins_folder")
            self._retranslate()  # refresh plugin button labels / enable

        def _browse_plugins() -> None:
            start = ed_plugins.text().strip() or default_plug
            selected = _pick_directory(
                dlg,
                self.i18n.t("settings_plugins_folder"),
                start,
            )
            if selected:
                ed_plugins.setText(selected)
                _save_plugins_folder()

        def _open_plugins_settings() -> None:
            _save_plugins_folder()
            d = Path(ed_plugins.text().strip() or default_plug)
            d.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(d.resolve())))

        def _save_cache_folder() -> None:
            path = ed_cache.text().strip()
            if path:
                self.store.setValue("analysis_cache_folder", path)

        def _save_eng_export_folder() -> None:
            path = ed_eng.text().strip()
            if path:
                self.store.setValue(SETTINGS_KEY_ENG_EXPORT_DIR, path)
            else:
                self.store.remove(SETTINGS_KEY_ENG_EXPORT_DIR)
            if hasattr(self, "_eng_mode"):
                self._eng_mode.refresh_export_path_label()

        def _browse_eng_export() -> None:
            start = ed_eng.text().strip() or default_eng
            selected = _pick_directory(
                dlg,
                self.i18n.t("settings_eng_export_folder"),
                start,
            )
            if selected:
                ed_eng.setText(selected)
                _save_eng_export_folder()

        def _open_eng_export() -> None:
            _save_eng_export_folder()
            p = project_ui_dir(self.paths, store=self.store)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

        def _reset_eng_export() -> None:
            self.store.remove(SETTINGS_KEY_ENG_EXPORT_DIR)
            ed_eng.setText(default_eng)
            if hasattr(self, "_eng_mode"):
                self._eng_mode.refresh_export_path_label()

        def _browse_cache() -> None:
            selected = _pick_directory(
                dlg, self.i18n.t("settings_cache_folder"), ed_cache.text().strip() or str(Path.home()),
            )
            if selected:
                ed_cache.setText(selected)
                _save_cache_folder()
                self._refresh_fit_history()

        def _open_cache() -> None:
            _save_cache_folder()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._cache_root())))

        def _clear_cache() -> None:
            _save_cache_folder()
            answer = QMessageBox.question(
                dlg, self.i18n.t("settings_cache_clear"), self.i18n.t("settings_cache_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            n = clear_analysis_cache(self._cache_root())
            self._refresh_fit_history()
            QMessageBox.information(dlg, self.i18n.t("settings_cache_clear"), self.i18n.t("settings_cache_cleared", n=n))

        def _browse() -> None:
            start = ed_folder.text().strip() or str(Path.home())
            d = _pick_directory(
                dlg,
                self.i18n.t("settings_update_folder"),
                start,
            )
            if d:
                ed_folder.setText(d)
                _save_folder()

        def _open_dir() -> None:
            _save_folder()
            p = ed_folder.text().strip()
            if p and Path(p).is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(p))
            else:
                QMessageBox.information(dlg, self.i18n.t("settings"), self.i18n.t("update_no_folder"))

        def _on_lang(_checked: bool = False) -> None:
            lang = "en" if rad_lang_en.isChecked() else "zh"
            if lang == self.i18n.lang:
                return
            self.i18n.lang = str(lang)
            self.store.setValue("lang", self.i18n.lang)
            self._retranslate()
            dlg.setWindowTitle(self.i18n.t("settings"))
            hint.setText(self.i18n.t("settings_auto_saved"))
            box_gen.setTitle(self.i18n.t("settings_general_block"))
            # General form field labels
            lang_label = gen.labelForField(lang_row)
            if lang_label is not None:
                lang_label.setText(self.i18n.t("settings_language"))
            close_label = gen.labelForField(close_row)
            if close_label is not None:
                close_label.setText(self.i18n.t("close_action"))
            chk_aes.setText(self.i18n.t("auto_edge_on_select"))
            chk_auto_re.setText(self.i18n.t("auto_reprocess"))
            chk_raw_noask.setText(self.i18n.t("imp_noask"))
            rad_lang_zh.setText(self.i18n.t("lang_zh"))
            rad_lang_en.setText(self.i18n.t("lang_en"))
            rad_close_quit.setText(self.i18n.t("close_action_quit"))
            rad_close_tray.setText(self.i18n.t("close_action_tray"))
            lbl_cache_title.setText(self.i18n.t("settings_cache_folder"))
            cache_hint.setText(self.i18n.t("settings_cache_folder_hint"))
            lbl_eng_title.setText(self.i18n.t("settings_eng_export_folder"))
            lbl_update_title.setText(self.i18n.t("settings_update_folder"))
            folder_hint.setText(self.i18n.t("settings_update_folder_hint"))
            eng_hint.setText(self.i18n.t("settings_eng_export_hint"))
            btn_cache_browse.setText(self.i18n.t("browse"))
            btn_cache_open.setText(self.i18n.t("open_folder"))
            btn_cache_clear.setText(self.i18n.t("settings_cache_clear"))
            btn_eng_browse.setText(self.i18n.t("browse"))
            btn_eng_open.setText(self.i18n.t("open_folder"))
            btn_eng_reset.setText(self.i18n.t("settings_eng_export_reset"))
            btn_browse.setText(self.i18n.t("browse"))
            btn_open.setText(self.i18n.t("open_folder"))
            lbl_plug_title.setText(self.i18n.t("settings_plugins_folder"))
            plug_hint.setText(self.i18n.t("settings_plugins_folder_hint"))
            btn_plug_browse.setText(self.i18n.t("browse"))
            btn_plug_open.setText(self.i18n.t("open_folder"))
            lbl_auto.setText(self.i18n.t("settings_update_auto_note"))
            for fn in ai_tr:  # Coding agents / API binding / AI Check blocks
                fn()
            btn_about.setText(self.i18n.t("about"))
            self._log(f"Language → {self.i18n.lang}")
            _fit_settings_height()

        def _on_close_action(_checked: bool = False) -> None:
            ca = "tray" if rad_close_tray.isChecked() else "quit"
            self.store.setValue("close_action", ca)
            if ca == "tray":
                self._setup_tray()

        def _on_api_inject(checked: bool) -> None:
            self.store.setValue("coding_api_inject", "true" if checked else "false")

        def _save_all_api() -> None:
            for sk, e in api_eds.items():
                self.store.setValue(sk, e.text().strip())
            self._log(self.i18n.t("api_saved"))

        def _open_api_term() -> None:
            _save_all_api()
            self._open_coding_terminal()

        ed_folder.editingFinished.connect(_save_folder)
        ed_plugins.editingFinished.connect(_save_plugins_folder)
        ed_cache.editingFinished.connect(_save_cache_folder)
        ed_eng.editingFinished.connect(_save_eng_export_folder)
        btn_eng_browse.clicked.connect(_browse_eng_export)
        btn_eng_open.clicked.connect(_open_eng_export)
        btn_eng_reset.clicked.connect(_reset_eng_export)
        btn_cache_browse.clicked.connect(_browse_cache)
        btn_cache_open.clicked.connect(_open_cache)
        btn_cache_clear.clicked.connect(_clear_cache)
        btn_browse.clicked.connect(_browse)
        btn_open.clicked.connect(_open_dir)
        btn_plug_browse.clicked.connect(_browse_plugins)
        btn_plug_open.clicked.connect(_open_plugins_settings)
        rad_lang_zh.toggled.connect(_on_lang)
        rad_lang_en.toggled.connect(_on_lang)
        rad_close_quit.toggled.connect(_on_close_action)
        rad_close_tray.toggled.connect(_on_close_action)
        chk_inject.toggled.connect(_on_api_inject)
        btn_api_term.clicked.connect(_open_api_term)

        dlg.exec()

    def _scan_for_update(self, folder: str) -> Optional[dict]:
        """Return a newer, platform-matching ZIP only after SHA-256 verification.

        Update metadata is treated as untrusted input.  Targets must remain
        below the configured update root; loose folders, launchers and 7z files
        are never accepted.
        """
        if not folder:
            return None
        root = Path(folder).resolve()
        if not root.is_dir():
            return None
        platform_name = "windows" if sys.platform.startswith("win") else "macos" if sys.platform == "darwin" else ""
        if not platform_name:
            return None
        current = _version_tuple(APP_VERSION)

        for manifest_name in (f"latest_{platform_name}.json", "latest.json"):
            manifest = root / manifest_name
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                package = data
                if manifest_name == "latest.json":
                    package = (data.get("packages") or {}).get(platform_name) or {}
                    if isinstance(package, str):
                        package = {"path": package}
                    if not isinstance(package, dict):
                        continue
                    package = {**data, **package}
                declared_os = str(package.get("os") or platform_name).strip().lower()
                version = str(package.get("version") or data.get("version") or "")
                rel = str(package.get("path") or "").strip()
                digest = str(package.get("sha256") or "").strip().lower()
                if declared_os != platform_name or _version_tuple(version) <= current:
                    continue
                if not re.fullmatch(r"[0-9a-f]{64}", digest) or not rel:
                    continue
                target = (root / rel).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    continue
                if not target.is_file() or target.suffix.lower() != ".zip":
                    continue
                hasher = hashlib.sha256()
                with target.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        hasher.update(chunk)
                if not hmac.compare_digest(hasher.hexdigest(), digest):
                    self._log(f"[update] rejected SHA-256 mismatch: {target.name}")
                    continue
                return {
                    "version": ".".join(str(x) for x in _version_tuple(version)),
                    "path": str(target),
                    "kind": "verified-manifest",
                    "sha256": digest,
                    "os": platform_name,
                }
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return None

    def _auto_update_plugins_now(self) -> str:
        """Overwrite active plugin HTML from newer versioned files. Returns status text."""
        d = self._plugins_dir()
        try:
            plugins_manager.ensure_bundled_plugins_seed(self.paths, d)
            applied = plugins_manager.auto_update_plugins(d)
        except Exception as exc:
            self._log(f"[plugins] auto-update failed: {exc}")
            return ""
        if not applied:
            return ""
        names = ", ".join(f"{a['id']}→v{a['version']}" for a in applied)
        msg = self.i18n.t("plugins_auto_updated", list=names)
        self._log(f"[plugins] auto-updated: {names}")
        self._status(msg)
        if hasattr(self, "_plugin_btns"):
            self._retranslate()
        return msg

    def _apply_platform_update(self, info: dict, quiet: bool = True) -> None:
        """Open the new main-program package folder (cannot hot-swap running code)."""
        if not info:
            return
        target = Path(info["path"])
        if target.is_file() and target.suffix.lower() == ".zip":
            folder = target.parent
        else:
            folder = target if target.is_dir() else target.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        msg = self.i18n.t(
            "platform_auto_update",
            new=info.get("version", "?"),
            cur=APP_VERSION,
        )
        self._status(msg)
        self._log(f"[update] verified package v{info.get('version')}; opened folder {folder}")
        if not quiet:
            QMessageBox.information(self, self.i18n.t("apply_update"), msg)

    def _startup_update_scan(self) -> None:
        """Auto-update plugins and report a verified main-program package."""
        try:
            self._auto_update_plugins_now()
        except Exception as exc:
            self._log(f"[plugins] startup scan error: {exc}")
        folder = self._update_folder()
        if not folder:
            return
        info = self._scan_for_update(folder)
        if not info:
            return
        self._pending_update = info
        msg = self.i18n.t("platform_auto_update", new=info.get("version", "?"), cur=APP_VERSION)
        self._status(msg)
        self._log(f"[update] verified package ready: {info['path']}")


def _install_error_hook(win: "MainWindow") -> None:
    """Write every uncaught exception to output/gui_errors.log (pythonw has no console)."""
    log_path = DEFAULT_OUTPUT / "gui_errors.log"

    def _hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                import datetime

                fh.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{text}")
        except Exception:
            pass
        try:
            win._log(f"ERROR (see output/gui_errors.log): {exc}")
        except Exception:
            pass

    sys.excepthook = _hook


def run_app(app=None, splash=None, eng: bool = False, single_instance=None):
    # pythonw has sys.stdout/stderr = None — larch's bytes2str decodes with
    # sys.stdout.encoding, so missing streams break gzip .prj reads (and any
    # library print()). Redirect to devnull before anything touches larch.
    import os

    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
            except Exception:
                pass
    _ensure_qt_plugin_env()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(True)
    ico = Path(__file__).resolve().parent / "assets" / "app_icon.ico"
    if ico.is_file():
        app.setWindowIcon(QIcon(str(ico)))
    win = MainWindow()
    _install_error_hook(win)
    if single_instance is not None:
        # Keep the server alive for the whole app lifetime and surface this
        # window whenever a second process tries to start.
        app._single_instance_server = single_instance
        single_instance.raise_requested.connect(win._on_second_launch)
        # Window exists now: raise requests get an "ok" ack from the handler
        # instead of the server's pre-window "boot" reply.
        single_instance.ready = True
    win.show()
    if splash is not None:
        # Close the early-boot splash (shown by main.py during heavy imports)
        splash.finish(win)
        splash = None
    win.raise_()
    win.activateWindow()
    win._log(
        f"{APP_NAME} v{APP_VERSION} ready. "
        + win._bi("打开 .prj：工具栏 / 快速 prj 下拉 / 拖放。", "Open .prj: toolbar / Quick .prj dropdown / drag-drop.")
    )
    win._rebuild_quick_prj_buttons()
    # Engineering mode: CLI --eng or SMARTLARCH_ENG=1 (legacy AUTO_LARCHUI_ENG) starts eng tools
    _eng_env = str(
        os.environ.get("SMARTLARCH_ENG")
        or os.environ.get("AUTO_LARCHUI_ENG")
        or ""
    ).strip()
    start_eng = bool(eng) or _eng_env.lower() in ("1", "true", "yes")
    if start_eng and hasattr(win, "_eng_mode"):
        win._eng_mode.set_active(True)
    code = app.exec()
    if getattr(win, "_hard_exit_needed", False):
        # A larch job was still running when the window closed. Python-level
        # thread kills (QThread.terminate) orphan the worker's PyThreadState
        # and can deadlock interpreter finalization on the GIL, so end the
        # process at OS level instead — the OS reaps everything.
        try:
            win.store.sync()
        except Exception:
            pass
        os._exit(code)
    return code
