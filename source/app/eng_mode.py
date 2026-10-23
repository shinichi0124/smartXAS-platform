"""Engineering mode for smartLarch Platform — draft layout capture / export only.

Workspace rule §B.23 + skill workspace-ui-design §Engineering Mode.

**Does not permanently change the production UI.** Toolbar values are a draft:
pick controls, set target sizes / notes, then **Export JSON**. An agent reads
the file and patches source (main_window / i18n). Exit restores any temporary
selection rings; geometry of the running app is not applied from the draft.

Entry: log-bar 🔧 and CLI ``--eng``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .version import APP_NAME, APP_VERSION

SCHEMA_ID = "sa-ui-layout/v1"

DEFAULT_CONSTRAINTS = {
    "left": {"min_w": 260, "max_w": 330},
    "right": {"min_w": 360, "max_w": 460},
    "log": {"min_h": 60, "max_h": 110},
    "window": {"w": 1440, "h": 880, "min_w": 1180, "min_h": 700},
    "main_splitter": [270, 790, 380],
}

SETTINGS_KEY_ENG_EXPORT_DIR = "eng_export_dir"


def default_ui_dir(paths: dict | None = None) -> Path:
    """Default: ``<project>/ui`` (not a system cache)."""
    root = Path((paths or {}).get("root") or Path(__file__).resolve().parents[2])
    return root / "ui"


def project_ui_dir(paths: dict | None = None, store=None) -> Path:
    """Resolve export folder: QSettings ``eng_export_dir`` or default ``<project>/ui``."""
    custom = ""
    if store is not None:
        try:
            custom = str(store.value(SETTINGS_KEY_ENG_EXPORT_DIR, "") or "").strip()
        except Exception:
            custom = ""
    if custom:
        p = Path(custom)
    else:
        p = default_ui_dir(paths or {})
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fall back to project ui if custom path is unwritable
        p = default_ui_dir(paths or {})
        p.mkdir(parents=True, exist_ok=True)
    return p


def _is_headless() -> bool:
    return str(os.environ.get("QT_QPA_PLATFORM", "")).lower() == "offscreen"


class EngModeController(QObject):
    """Attached to MainWindow; owns 🔧, toolbar, capture/export."""

    def __init__(self, window: QWidget, log_layout: Optional[QVBoxLayout] = None):
        super().__init__(window)
        self.win = window
        self._log_layout = log_layout
        self.active = False
        self._text_overrides: Dict[str, Dict[str, str]] = {}
        self._user_notes: str = ""
        self._filter_installed = False
        self._block_spins = False
        # Draft only — never applied to the live window as permanent layout
        self._draft: Dict[str, Any] = {
            "left_w": None,
            "right_w": None,
            "log_h": None,
            "main_sizes": None,
            "fit_sizes": None,
        }
        self._baseline: Dict[str, Any] = {}
        # Per-widget draft sizes from pick mode (ui_id → geometry dict)
        self._widget_overrides: Dict[str, Dict[str, Any]] = {}
        self._widget_refs: Dict[str, QWidget] = {}
        self._selected: Optional[QWidget] = None
        self._selected_old_style: str = ""
        self._pick_mode = False
        self._block_sel_spins = False

        # --- 🔧 toggle (also opens actions menu on right-click / second click path)
        self.btn = QToolButton(window)
        self.btn.setObjectName("engModeButton")
        self.btn.setText("🔧")
        self.btn.setAutoRaise(True)
        self.btn.setCheckable(True)
        self.btn.setFixedSize(28, 22)
        self.btn.setStyleSheet(
            "QToolButton { border: none; font-size: 13px; padding: 0 2px; color: #6b625d; }"
            "QToolButton:hover { color: #e64626; }"
            "QToolButton:checked { color: #e64626; background: rgba(230,70,38,0.18); "
            "border-radius: 3px; }"
        )
        self.btn.clicked.connect(self._on_btn_clicked)
        self.btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn.customContextMenuRequested.connect(self._show_menu)

        self._menu = QMenu(self.btn)
        self._act_notes = QAction(self)
        self._act_notes.triggered.connect(lambda: self.edit_notes(prompt_export=False))
        self._act_export = QAction(self)
        self._act_export.triggered.connect(self.export_layout)
        self._act_apply = QAction(self)
        self._act_apply.triggered.connect(self.apply_from_file_dialog)
        self._act_open = QAction(self)
        self._act_open.triggered.connect(self.open_ui_folder)
        self._act_help = QAction(self)
        self._act_help.triggered.connect(self.show_help)
        self._act_exit = QAction(self)
        self._act_exit.triggered.connect(lambda: self.set_active(False))
        for a in (
            self._act_notes,
            self._act_export,
            self._act_apply,
            self._act_open,
            self._act_help,
        ):
            self._menu.addAction(a)
        self._menu.addSeparator()
        self._menu.addAction(self._act_exit)

        # Single row: main-window column drafts (left) | stretch | Pick+actions (right)
        # Per-control W/H/rename → right-click menu on selected control (not toolbar)
        self.bar = QFrame(window)
        self.bar.setObjectName("engModeBar")
        self.bar.setVisible(False)
        self.bar.setStyleSheet(
            "#engModeBar {"
            "  background: #fff4ef;"
            "  border: 1px solid #e64626;"
            "  border-radius: 4px;"
            "}"
            "#engModeBar QLabel { color: #5c4033; font-size: 11px; }"
            "#engModeBar QToolButton {"
            "  background: #fff; border: 1px solid #eaa893; border-radius: 3px;"
            "  padding: 1px 6px; font-size: 11px; font-weight: 600; color: #333;"
            "}"
            "#engModeBar QToolButton:hover { border-color: #e64626; color: #e64626; }"
            "#engModeBar QToolButton:checked {"
            "  background: #e64626; color: #fff; border-color: #c53d20;"
            "}"
            "#engModeBar QSpinBox { min-width: 48px; max-width: 60px; }"
        )
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(6, 2, 6, 2)
        bl.setSpacing(4)

        # --- left: main window 3-column + log band targets ---
        self.lbl_eng_title = QLabel()
        self.lbl_eng_title.setStyleSheet("font-weight:700; color:#e64626; font-size:11px;")
        bl.addWidget(self.lbl_eng_title)

        self.lbl_left_w = QLabel()
        bl.addWidget(self.lbl_left_w)
        self.sp_left = QSpinBox()
        self.sp_left.setRange(120, 800)
        self.sp_left.setSingleStep(10)
        self.sp_left.valueChanged.connect(self._on_spin_left)
        bl.addWidget(self.sp_left)

        self.lbl_right_w = QLabel()
        bl.addWidget(self.lbl_right_w)
        self.sp_right = QSpinBox()
        self.sp_right.setRange(200, 900)
        self.sp_right.setSingleStep(10)
        self.sp_right.valueChanged.connect(self._on_spin_right)
        bl.addWidget(self.sp_right)

        self.lbl_log_h = QLabel()
        bl.addWidget(self.lbl_log_h)
        self.sp_log = QSpinBox()
        self.sp_log.setRange(40, 480)
        self.sp_log.setSingleStep(10)
        self.sp_log.valueChanged.connect(self._on_spin_log)
        bl.addWidget(self.sp_log)

        bl.addStretch(1)

        # --- right-aligned: selection status + pick + session ---
        self.lbl_sel = QLabel()
        self.lbl_sel.setMinimumWidth(0)
        self.lbl_sel.setMaximumWidth(160)
        self.lbl_sel.setStyleSheet("font-weight:600; color:#e64626; font-size:11px;")
        bl.addWidget(self.lbl_sel)

        self.btn_pick = QToolButton()
        self.btn_pick.setCheckable(True)
        self.btn_pick.toggled.connect(self._on_pick_toggled)
        bl.addWidget(self.btn_pick)

        self.btn_preview = QToolButton()
        self.btn_preview.setCheckable(True)
        self.btn_preview.toggled.connect(self._on_preview_toggled)
        bl.addWidget(self.btn_preview)
        self._preview_on = False
        self._preview_snap: Dict[str, Any] = {}

        self.btn_notes = QToolButton()
        self.btn_notes.clicked.connect(lambda: self.edit_notes(prompt_export=False))
        bl.addWidget(self.btn_notes)

        self.btn_export = QToolButton()
        self.btn_export.clicked.connect(self.export_layout)
        bl.addWidget(self.btn_export)

        # Always-visible export directory (full path in tooltip)
        self.lbl_export_path = QLabel()
        self.lbl_export_path.setMinimumWidth(80)
        self.lbl_export_path.setMaximumWidth(280)
        self.lbl_export_path.setStyleSheet("color:#6b625d; font-size:10px;")
        self.lbl_export_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        bl.addWidget(self.lbl_export_path)

        self.btn_help = QToolButton()
        self.btn_help.clicked.connect(self.show_help)
        bl.addWidget(self.btn_help)

        self.btn_exit = QToolButton()
        self.btn_exit.clicked.connect(lambda: self.set_active(False))
        bl.addWidget(self.btn_exit)

        # Hidden legacy spins kept for draft helpers / tests (not on toolbar)
        self.sp_sel_w = QSpinBox()
        self.sp_sel_w.setRange(12, 1200)
        self.sp_sel_w.hide()
        self.sp_sel_h = QSpinBox()
        self.sp_sel_h.setRange(12, 1200)
        self.sp_sel_h.hide()
        self.lbl_sel_w = QLabel()
        self.lbl_sel_w.hide()
        self.lbl_sel_h = QLabel()
        self.lbl_sel_h.hide()
        self.btn_edit_text = QToolButton()
        self.btn_edit_text.hide()
        self.btn_sel_clear = QToolButton()
        self.btn_sel_clear.hide()
        self.lbl_pick_hint = QLabel()
        self.lbl_pick_hint.hide()

        if log_layout is not None:
            log_layout.insertWidget(0, self.bar)

        self.retranslate()

    # ----- chrome -----
    def retranslate(self) -> None:
        t = self.win.i18n.t
        tip = t("eng_mode_tip")
        if self._user_notes.strip():
            preview = self._user_notes.strip().replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "…"
            tip = f"{tip}\n\n📝 {preview}"
        self.btn.setToolTip(tip)
        self._act_notes.setText(t("eng_notes"))
        self._act_export.setText(t("eng_export"))
        self._act_apply.setText(t("eng_apply_file"))
        self._act_open.setText(t("eng_open_ui_dir"))
        self._act_help.setText(t("eng_help"))
        self._act_exit.setText(t("eng_exit"))
        self.lbl_eng_title.setText(t("eng_bar_title"))
        self.lbl_eng_title.setToolTip(t("eng_main_layout_tip"))
        self.lbl_left_w.setText(t("eng_left_w"))
        self.lbl_left_w.setToolTip(t("eng_left_w_tip"))
        self.lbl_right_w.setText(t("eng_right_w"))
        self.lbl_right_w.setToolTip(t("eng_right_w_tip"))
        self.lbl_log_h.setText(t("eng_log_h"))
        self.lbl_log_h.setToolTip(t("eng_log_h_tip"))
        self.sp_left.setToolTip(t("eng_left_w_tip"))
        self.sp_right.setToolTip(t("eng_right_w_tip"))
        self.sp_log.setToolTip(t("eng_log_h_tip"))
        self.btn_notes.setText(t("eng_notes_short"))
        self.btn_export.setText(t("eng_export_short"))
        self.btn_help.setText(t("eng_help_short"))
        self.btn_exit.setText(t("eng_exit_short"))
        self.btn_notes.setToolTip(t("eng_notes"))
        self.btn_export.setToolTip(t("eng_export"))
        self.btn_help.setToolTip(t("eng_help"))
        self.btn_exit.setToolTip(t("eng_exit"))
        self.btn_pick.setText(t("eng_pick"))
        self.btn_pick.setToolTip(t("eng_pick_tip") + "\n" + t("eng_pick_hint"))
        self.btn_preview.setText(t("eng_preview"))
        self.btn_preview.setToolTip(t("eng_preview_tip"))
        if self._selected is None:
            self.lbl_sel.setText(t("eng_sel_none_short"))
            self.lbl_sel.setToolTip(t("eng_sel_none"))
        if self._user_notes.strip():
            self.btn.setText("🔧*")
        else:
            self.btn.setText("🔧")
        self.refresh_export_path_label()

    def export_dir(self) -> Path:
        store = getattr(self.win, "store", None)
        paths = getattr(self.win, "paths", {}) or {}
        return project_ui_dir(paths, store=store)

    def refresh_export_path_label(self) -> None:
        """Show full export folder path on the eng toolbar."""
        if not hasattr(self, "lbl_export_path"):
            return
        p = str(self.export_dir())
        self.lbl_export_path.setText(p)
        self.lbl_export_path.setToolTip(
            self.win.i18n.t("eng_export_path_tip") + "\n" + p
        )

    def _on_btn_clicked(self, checked: bool) -> None:
        # Toggle eng mode; when turning ON, ensure toolbar is obvious
        self.set_active(bool(checked))
        if self.active and not _is_headless():
            # One-shot tip in log (not a modal) so user knows what to do
            try:
                self.win._log(self.win.i18n.t("eng_on_detail"))
            except Exception:
                pass

    def _show_menu(self, pos) -> None:
        self._menu.exec(self.btn.mapToGlobal(pos))

    def show_help(self) -> None:
        t = self.win.i18n.t
        QMessageBox.information(self.win, t("eng_mode"), t("eng_help_body"))

    # ----- spins → draft only (never mutate production layout) -----
    def _snapshot_baseline(self) -> None:
        c = self._read_live_constraints()
        main_sizes = []
        split = getattr(self.win, "_main_splitter", None)
        if isinstance(split, QSplitter):
            main_sizes = [int(x) for x in split.sizes()]
        fit_sizes = []
        fit = getattr(self.win, "fit_display_splitter", None)
        if isinstance(fit, QSplitter):
            fit_sizes = [int(x) for x in fit.sizes()]
        self._baseline = {
            "left_w": int(c["left"]["w"] or DEFAULT_CONSTRAINTS["left"]["min_w"]),
            "right_w": int(c["right"]["w"] or DEFAULT_CONSTRAINTS["right"]["min_w"]),
            "log_h": int(c["log"]["h"] or DEFAULT_CONSTRAINTS["log"]["max_h"]),
            "main_sizes": main_sizes,
            "fit_sizes": fit_sizes,
            "window": c["window"],
        }

    def _fill_spins_from_draft(self) -> None:
        self._block_spins = True
        try:
            b = self._baseline
            d = self._draft
            self.sp_left.setValue(int(d.get("left_w") or b.get("left_w") or 270))
            self.sp_right.setValue(int(d.get("right_w") or b.get("right_w") or 380))
            self.sp_log.setValue(int(d.get("log_h") or b.get("log_h") or 110))
        finally:
            self._block_spins = False

    def _on_spin_left(self, val: int) -> None:
        if self._block_spins or not self.active:
            return
        self._draft["left_w"] = int(val)
        # proposed main sizes: left fixed, center residual, right from draft/baseline
        self._draft_main_from_sides()
        if self._preview_on:
            self._apply_preview(log=False)

    def _on_spin_right(self, val: int) -> None:
        if self._block_spins or not self.active:
            return
        self._draft["right_w"] = int(val)
        self._draft_main_from_sides()
        if self._preview_on:
            self._apply_preview(log=False)

    def _on_spin_log(self, val: int) -> None:
        if self._block_spins or not self.active:
            return
        self._draft["log_h"] = int(val)
        if self._preview_on:
            self._apply_preview(log=False)

    # ----- preview: draft temporarily applied to the live window -----
    # Preview is a *trial render* of the draft: every mutation is snapshotted
    # first and fully restored when preview turns off or eng mode exits.  The
    # draft (and its exported JSON) stays the only record for the agent.
    def _on_preview_toggled(self, on: bool) -> None:
        if on:
            self._apply_preview()
        else:
            self._restore_preview()

    def _apply_preview(self, *, log: bool = True) -> None:
        snap = dict(self._preview_snap)
        # Column widths: fixed-width the side panels (their min/max are
        # snapshotted and restored).  setSizes() alone cannot push past the
        # panels' sizeHint clamps, so a draft would silently not render.
        left = getattr(self.win, "_left_panel", None)
        right = getattr(self.win, "_right_panel", None)
        split = getattr(self.win, "_main_splitter", None)
        if isinstance(split, QSplitter):
            snap.setdefault("main_sizes", [int(x) for x in split.sizes()])
        lw = self._draft.get("left_w")
        rw = self._draft.get("right_w")
        clamps = snap.setdefault("panel_clamps", [])
        if not clamps:
            for panel in (left, right):
                if panel is not None:
                    clamps.append((panel, panel.minimumWidth(), panel.maximumWidth()))
        if left is not None and lw:
            left.setFixedWidth(int(lw))
        if right is not None and rw:
            right.setFixedWidth(int(rw))
        logw = getattr(self.win, "txt_log", None)
        log_h = self._draft.get("log_h")
        if logw is not None and log_h:
            snap.setdefault("log", (logw.minimumHeight(), logw.maximumHeight()))
            logw.setFixedHeight(int(log_h))
        for uid, row in list(self._widget_overrides.items()):
            w = self._widget_refs.get(uid)
            if w is not None:
                self._apply_preview_widget(uid, w, row, snap)
        self._preview_snap = snap
        self._preview_on = True
        if log:
            self._log_msg("eng_preview_on")

    def _apply_preview_widget(self, uid: str, w: QWidget, row: dict, snap: dict) -> None:
        wsnap = snap.setdefault("widgets", {})
        if uid not in wsnap:
            entry: Dict[str, Any] = {"min": w.minimumSize(), "max": w.maximumSize()}
            if hasattr(w, "text"):
                try:
                    entry["text"] = w.text()
                except Exception:
                    pass
            wsnap[uid] = entry
        tw, th = row.get("w"), row.get("h")
        if tw and th:
            w.setFixedSize(int(tw), int(th))
        shown = row.get("text_zh") if self.win.i18n.lang == "zh" else row.get("text_en")
        if shown and hasattr(w, "setText"):
            try:
                w.setText(str(shown))
            except Exception:
                pass

    def _restore_preview(self) -> None:
        snap, self._preview_snap = self._preview_snap, {}
        was_on = self._preview_on
        self._preview_on = False
        for panel, min_w, max_w in snap.get("panel_clamps") or []:
            try:
                panel.setMinimumWidth(int(min_w))
                panel.setMaximumWidth(int(max_w))
            except Exception:
                pass
        split = getattr(self.win, "_main_splitter", None)
        if isinstance(split, QSplitter) and snap.get("main_sizes"):
            split.setSizes([int(x) for x in snap["main_sizes"]])
        logw = getattr(self.win, "txt_log", None)
        if logw is not None and snap.get("log"):
            min_h, max_h = snap["log"]
            logw.setMinimumHeight(int(min_h))
            logw.setMaximumHeight(int(max_h))
        for uid, entry in (snap.get("widgets") or {}).items():
            w = self._widget_refs.get(uid)
            if w is None:
                continue
            try:
                w.setMinimumSize(entry["min"])
                w.setMaximumSize(entry["max"])
                if "text" in entry and hasattr(w, "setText"):
                    w.setText(entry["text"])
            except Exception:
                pass
        if self.btn_preview.isChecked():
            self.btn_preview.blockSignals(True)
            self.btn_preview.setChecked(False)
            self.btn_preview.blockSignals(False)
        if was_on:
            self._log_msg("eng_preview_off")

    def _draft_main_from_sides(self) -> None:
        base = self._baseline.get("main_sizes") or [270, 790, 380]
        total = sum(base) if base else 1440
        lw = int(self._draft.get("left_w") or self._baseline.get("left_w") or base[0])
        rw = int(self._draft.get("right_w") or self._baseline.get("right_w") or base[-1])
        mid = max(200, total - lw - rw)
        self._draft["main_sizes"] = [lw, mid, rw]

    def _sync_spins_from_live(self, *_args) -> None:
        """No-op for layout: draft is not driven by live drag (UI is not mutated)."""
        return

    # ----- pick any widget → W×H -----
    def _on_pick_toggled(self, on: bool) -> None:
        self._pick_mode = bool(on)
        try:
            if self._pick_mode:
                self.win._log(self.win.i18n.t("eng_pick_on"))
                QApplication.setOverrideCursor(Qt.CursorShape.CrossCursor)
            else:
                QApplication.restoreOverrideCursor()
                self.win._log(self.win.i18n.t("eng_pick_off"))
        except Exception:
            pass

    def _is_eng_chrome(self, w: Optional[QWidget]) -> bool:
        if w is None:
            return True
        if w is self.btn or w is self.bar:
            return True
        if self.bar.isAncestorOf(w) or self.btn.isAncestorOf(w):
            return True
        return False

    def _widget_path(self, w: QWidget) -> str:
        parts = []
        cur: Optional[QWidget] = w
        depth = 0
        while cur is not None and cur is not self.win and depth < 12:
            name = cur.objectName() or type(cur).__name__
            parts.append(name)
            cur = cur.parentWidget()
            depth += 1
        parts.reverse()
        return "/".join(parts) if parts else type(w).__name__

    def _ensure_ui_id(self, w: QWidget) -> str:
        oid = (w.objectName() or "").strip()
        if oid and not oid.startswith("qt_"):
            return oid
        # Stable synthetic id from class + path hash
        path = self._widget_path(w)
        base = type(w).__name__
        text = ""
        if hasattr(w, "text"):
            try:
                text = (w.text() or "")[:24]
            except Exception:
                text = ""
        # Prefer attribute name on MainWindow if this is a direct attribute
        for attr, val in vars(self.win).items():
            if val is w and not attr.startswith("_"):
                w.setObjectName(attr)
                return attr
        safe = "".join(ch if ch.isalnum() else "_" for ch in (text or base))[:40]
        uid = f"eng_{base}_{safe}_{abs(hash(path)) % 100000}"
        w.setObjectName(uid)
        return uid

    def select_widget(self, w: QWidget) -> None:
        if self._is_eng_chrome(w):
            return
        # Prefer a meaningful leaf (button/label) over plain container if click landed on layout child
        self.clear_selection(restore_style=True)
        self._selected = w
        self._selected_old_style = w.styleSheet() or ""
        ring = (
            "border: 2px solid #e64626 !important;"
            "background-color: rgba(230,70,38,0.08);"
        )
        w.setStyleSheet((self._selected_old_style + "\n" + ring).strip())
        uid = self._ensure_ui_id(w)
        self._widget_refs[uid] = w
        uid = self._ensure_ui_id(w)
        prev = self._widget_overrides.get(uid) or {}
        tw = max(12, int(prev.get("w") or w.width()))
        th = max(12, int(prev.get("h") or w.height()))
        self._block_sel_spins = True
        try:
            self.sp_sel_w.setValue(tw)
            self.sp_sel_h.setValue(th)
            label = uid
            if hasattr(w, "text"):
                try:
                    tx = (w.text() or "").strip()
                    if tx and len(tx) < 40:
                        label = f"{uid} 「{tx}」"
                except Exception:
                    pass
            self.lbl_sel.setText(label)
            self.lbl_sel.setToolTip(
                f"{type(w).__name__}\n{self._widget_path(w)}\n"
                f"当前 {w.width()}×{w.height()} → 草稿 {tw}×{th}\n"
                f"右键：设宽 / 设高 / 改文字（重命名）"
            )
        finally:
            self._block_sel_spins = False
        self._record_selected_geometry(target_w=tw, target_h=th)
        try:
            self.win._log(
                self.win.i18n.t("eng_sel_picked").format(id=uid, w=tw, h=th)
            )
        except Exception:
            pass

    def _edit_selected_text(self) -> None:
        if self._selected is None:
            try:
                self.win._log(self.win.i18n.t("eng_edit_text_need_pick"))
            except Exception:
                pass
            return
        self._edit_widget_text(self._selected)

    def _show_control_context_menu(self, global_pos) -> None:
        """Right-click menu: set draft width/height/rename for selected control."""
        t = self.win.i18n.t
        if self._selected is None:
            return
        uid = self._ensure_ui_id(self._selected)
        prev = self._widget_overrides.get(uid) or {}
        cur_w = int(prev.get("w") or self._selected.width())
        cur_h = int(prev.get("h") or self._selected.height())

        menu = QMenu(self.win)
        act_w = menu.addAction(t("eng_ctx_width").format(w=cur_w))
        act_h = menu.addAction(t("eng_ctx_height").format(h=cur_h))
        act_text = menu.addAction(t("eng_ctx_rename"))
        menu.addSeparator()
        act_clear = menu.addAction(t("eng_sel_clear"))
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is act_w:
            val, ok = QInputDialog.getInt(
                self.win,
                t("eng_ctx_width_title"),
                t("eng_ctx_width_label"),
                cur_w,
                12,
                1200,
                1,
            )
            if ok:
                self.sp_sel_w.setValue(int(val))
                self._record_selected_geometry(target_w=int(val), target_h=cur_h)
                self._refresh_sel_label()
        elif chosen is act_h:
            val, ok = QInputDialog.getInt(
                self.win,
                t("eng_ctx_height_title"),
                t("eng_ctx_height_label"),
                cur_h,
                12,
                1200,
                1,
            )
            if ok:
                self.sp_sel_h.setValue(int(val))
                self._record_selected_geometry(target_w=cur_w, target_h=int(val))
                self._refresh_sel_label()
        elif chosen is act_text:
            self._edit_widget_text(self._selected)
        elif chosen is act_clear:
            self.clear_selection(restore_style=True)

    def _refresh_sel_label(self) -> None:
        w = self._selected
        if w is None:
            return
        uid = self._ensure_ui_id(w)
        prev = self._widget_overrides.get(uid) or {}
        tw = int(prev.get("w") or w.width())
        th = int(prev.get("h") or w.height())
        label = f"{uid} {tw}×{th}"
        if prev.get("text_zh") or prev.get("text_en"):
            shown = prev.get("text_zh") if self.win.i18n.lang == "zh" else prev.get("text_en")
            if shown:
                label = f"{uid} 「{str(shown)[:24]}」 {tw}×{th}"
        self.lbl_sel.setText(label)

    def clear_selection(self, restore_style: bool = True) -> None:
        if self._selected is not None and restore_style:
            try:
                self._selected.setStyleSheet(self._selected_old_style)
            except Exception:
                pass
        self._selected = None
        self._selected_old_style = ""
        try:
            self.lbl_sel.setText(self.win.i18n.t("eng_sel_none_short"))
            self.lbl_sel.setToolTip(self.win.i18n.t("eng_sel_none"))
        except Exception:
            pass

    def _record_selected_geometry(self, *, target_w: Optional[int] = None, target_h: Optional[int] = None) -> None:
        """Write draft size for selected widget — does not resize the live control."""
        w = self._selected
        if w is None:
            return
        uid = self._ensure_ui_id(w)
        prev = dict(self._widget_overrides.get(uid) or {})
        tw = int(target_w if target_w is not None else prev.get("w") or w.width())
        th = int(target_h if target_h is not None else prev.get("h") or w.height())
        entry = {
            "ui_id": uid,
            "class": type(w).__name__,
            "path": self._widget_path(w),
            "baseline_w": int(w.width()),
            "baseline_h": int(w.height()),
            "w": tw,
            "h": th,
            "min_w": tw,
            "max_w": tw,
            "min_h": th,
            "max_h": th,
            "fixed": True,
            "draft_only": True,
            "apply": "agent-source",  # agent patches production source, not live UI
        }
        if hasattr(w, "text"):
            try:
                entry["text"] = w.text()
            except Exception:
                pass
        f = w.font()
        if f.pointSize() > 0:
            entry["font_pt"] = f.pointSize()
        # Keep any prior text draft from double-click
        if prev.get("text_zh") or prev.get("text_en"):
            entry["text_zh"] = prev.get("text_zh")
            entry["text_en"] = prev.get("text_en")
        entry = {k: v for k, v in entry.items() if v is not None}
        self._widget_overrides[uid] = entry
        self._widget_refs[uid] = w
        if self._preview_on:
            self._apply_preview(log=False)

    def _on_sel_w(self, val: int) -> None:
        if self._block_sel_spins or not self.active or self._selected is None:
            return
        # Draft only — live widget size unchanged
        self._record_selected_geometry(target_w=int(val), target_h=self.sp_sel_h.value())

    def _on_sel_h(self, val: int) -> None:
        if self._block_sel_spins or not self.active or self._selected is None:
            return
        self._record_selected_geometry(target_w=self.sp_sel_w.value(), target_h=int(val))

    def _pick_target_at(self, global_pos) -> Optional[QWidget]:
        """Resolve a resizable control under the cursor."""
        w = QApplication.widgetAt(global_pos)
        if w is None or self._is_eng_chrome(w):
            return None
        if not self.win.isAncestorOf(w):
            return None
        # Walk up to a sensible control (skip pure containers if child is better)
        cur: Optional[QWidget] = w
        best: Optional[QWidget] = w
        for _ in range(8):
            if cur is None or cur is self.win:
                break
            if self._is_eng_chrome(cur):
                return None
            if isinstance(
                cur,
                (
                    QAbstractButton,
                    QLabel,
                    QLineEdit,
                    QComboBox,
                    QSpinBox,
                    QTableWidget,
                    QPlainTextEdit,
                    QFrame,
                ),
            ):
                best = cur
                # Prefer buttons/labels over frames
                if isinstance(cur, (QAbstractButton, QLabel, QToolButton, QLineEdit, QComboBox, QSpinBox)):
                    return cur
            cur = cur.parentWidget()
        return best

    # ----- user remarks -----
    def edit_notes(self, *, prompt_export: bool = False) -> bool:
        t = self.win.i18n.t
        dlg = QDialog(self.win)
        dlg.setWindowTitle(t("eng_notes"))
        dlg.resize(520, 360)
        root = QVBoxLayout(dlg)
        hint = QLabel(t("eng_notes_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b625d;")
        root.addWidget(hint)
        ed = QPlainTextEdit()
        ed.setPlaceholderText(t("eng_notes_placeholder"))
        ed.setPlainText(self._user_notes)
        ed.setMinimumHeight(200)
        root.addWidget(ed, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        if prompt_export:
            skip = buttons.addButton(
                t("eng_notes_skip"), QDialogButtonBox.ButtonRole.ActionRole
            )
            skip.clicked.connect(lambda: dlg.done(2))
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        root.addWidget(buttons)
        code = dlg.exec()
        if code == QDialog.DialogCode.Rejected:
            return False
        if code == 2:
            return True
        self._user_notes = ed.toPlainText()
        self.retranslate()
        try:
            n = len(self._user_notes.strip())
            if n:
                self.win._log(t("eng_notes_saved").format(n=n))
            else:
                self.win._log(t("eng_notes_cleared"))
        except Exception:
            pass
        return True

    # ----- activate -----
    def set_active(self, on: bool) -> None:
        on = bool(on)
        if on == self.active:
            self.btn.setChecked(on)
            self.bar.setVisible(on)
            return
        self.active = on
        self.btn.setChecked(on)
        if on:
            self._enter()
        else:
            self._leave()
        base = f"{APP_NAME} v{APP_VERSION}"
        if on:
            title = self.win.windowTitle()
            if "[ENG]" not in title:
                self.win.setWindowTitle(f"{title}  [ENG]")
        else:
            try:
                self.win.setWindowTitle(
                    f"{self.win.i18n.t('app_title')}  v{APP_VERSION}"
                )
            except Exception:
                self.win.setWindowTitle(base)

    def _enter(self) -> None:
        # Snapshot live baseline into draft fields; do not restyle or resize the app
        self._snapshot_baseline()
        if self._draft.get("left_w") is None:
            self._draft["left_w"] = self._baseline.get("left_w")
        if self._draft.get("right_w") is None:
            self._draft["right_w"] = self._baseline.get("right_w")
        if self._draft.get("log_h") is None:
            self._draft["log_h"] = self._baseline.get("log_h")
        if not self._draft.get("main_sizes"):
            self._draft["main_sizes"] = list(self._baseline.get("main_sizes") or [])
        if not self._draft.get("fit_sizes"):
            self._draft["fit_sizes"] = list(self._baseline.get("fit_sizes") or [])

        app = QApplication.instance()
        if app is not None and not self._filter_installed:
            app.installEventFilter(self)
            self._filter_installed = True

        self.bar.setVisible(True)
        self._fill_spins_from_draft()
        self.refresh_export_path_label()
        self._log_msg("eng_on")
        try:
            self.win._log(self.win.i18n.t("eng_on_detail"))
            self.win._log(
                self.win.i18n.t("eng_export_dir_line").format(path=str(self.export_dir()))
            )
        except Exception:
            pass

    def _strip_all_selection_rings(self) -> None:
        """Remove temporary pick-mode rings (the only live visual eng may add)."""
        self.clear_selection(restore_style=True)
        for uid, w in list(self._widget_refs.items()):
            if w is None:
                continue
            try:
                ss = w.styleSheet() or ""
                if "e64626" in ss or "rgba(230,70,38" in ss:
                    lines = [
                        ln
                        for ln in ss.splitlines()
                        if "e64626" not in ln and "230,70,38" not in ln
                    ]
                    w.setStyleSheet("\n".join(lines).strip())
            except Exception:
                pass

    def _leave(self) -> None:
        # Draft kept in memory for this session; live UI was never resized by eng
        app = QApplication.instance()
        if app is not None and self._filter_installed:
            app.removeEventFilter(self)
            self._filter_installed = False
        if self._pick_mode:
            self.btn_pick.setChecked(False)
            self._pick_mode = False
            try:
                while QApplication.overrideCursor() is not None:
                    QApplication.restoreOverrideCursor()
            except Exception:
                pass
        self._strip_all_selection_rings()
        if self._preview_on or self.btn_preview.isChecked():
            self._restore_preview()
        self.bar.setVisible(False)
        self._log_msg("eng_off")

    def _log_msg(self, key: str) -> None:
        try:
            self.win._log(self.win.i18n.t(key))
        except Exception:
            pass

    # ----- capture -----
    def _read_live_constraints(self) -> dict:
        left = getattr(self.win, "_left_panel", None)
        right = getattr(self.win, "_right_panel", None)
        log = getattr(self.win, "txt_log", None)
        geo = self.win.geometry()
        return {
            "left": {
                "min_w": left.minimumWidth() if left else DEFAULT_CONSTRAINTS["left"]["min_w"],
                "max_w": left.maximumWidth() if left else DEFAULT_CONSTRAINTS["left"]["max_w"],
                "w": left.width() if left else 0,
            },
            "right": {
                "min_w": right.minimumWidth() if right else DEFAULT_CONSTRAINTS["right"]["min_w"],
                "max_w": right.maximumWidth() if right else DEFAULT_CONSTRAINTS["right"]["max_w"],
                "w": right.width() if right else 0,
            },
            "log": {
                "min_h": log.minimumHeight() if log else DEFAULT_CONSTRAINTS["log"]["min_h"],
                "max_h": log.maximumHeight() if log else DEFAULT_CONSTRAINTS["log"]["max_h"],
                "h": log.height() if log else 0,
            },
            "window": {
                "w": geo.width(),
                "h": geo.height(),
                "min_w": self.win.minimumWidth(),
                "min_h": self.win.minimumHeight(),
                "x": geo.x(),
                "y": geo.y(),
            },
        }

    def capture(self) -> dict:
        """Build export from **draft targets** + live baseline (no live mutation)."""
        if not self._baseline:
            self._snapshot_baseline()
        c = self._read_live_constraints()
        base = self._baseline
        d = self._draft
        left_w = int(d.get("left_w") or base.get("left_w") or c["left"]["w"] or 270)
        right_w = int(d.get("right_w") or base.get("right_w") or c["right"]["w"] or 380)
        log_h = int(d.get("log_h") or base.get("log_h") or c["log"]["h"] or 110)
        main_sizes = list(
            d.get("main_sizes") or base.get("main_sizes") or [left_w, 790, right_w]
        )
        if len(main_sizes) >= 3:
            main_sizes[0] = left_w
            main_sizes[2] = right_w
            total = sum(main_sizes)
            main_sizes[1] = max(200, total - left_w - right_w)
        fit_sizes = list(d.get("fit_sizes") or base.get("fit_sizes") or [])

        widgets: Dict[str, Any] = {}
        # User-picked widgets: draft targets (do not overwrite with live size)
        for uid, ov in self._widget_overrides.items():
            widgets[uid] = dict(ov)

        text: Dict[str, Any] = dict(self._text_overrides)

        user_notes = (self._user_notes or "").strip()
        payload = {
            "schema": SCHEMA_ID,
            "app": APP_NAME,
            "version": APP_VERSION,
            "surface": "main",
            "mode": "draft-export",
            "apply_mode": "agent-source-only",
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user_notes": user_notes,
            "notes": user_notes,
            "baseline": {
                "left_w": base.get("left_w"),
                "right_w": base.get("right_w"),
                "log_h": base.get("log_h"),
                "main_sizes": base.get("main_sizes"),
                "window": base.get("window") or c["window"],
            },
            "agent_instructions": (
                "Engineering draft export (UI was NOT live-mutated). "
                "1) Read user_notes first. "
                "2) Apply panels/splitters/widgets/text to production SOURCE "
                "(main_window.py geometry constants + i18n STRINGS). "
                "3) Do not expect the running app to already match draft sizes. "
                "4) Bump APP_VERSION; sync layout.current.json; quit GUI."
            ),
            "window": base.get("window") or c["window"],
            "panels": {
                "left": {
                    "id": "panel.left",
                    "baseline_w": base.get("left_w"),
                    "live_w": c["left"]["w"],
                    "w": left_w,
                    "min_w": max(200, min(left_w, DEFAULT_CONSTRAINTS["left"]["min_w"])),
                    "max_w": max(left_w, DEFAULT_CONSTRAINTS["left"]["max_w"]),
                    "recommended_fixed_band": [max(200, left_w - 20), left_w + 20],
                },
                "right": {
                    "id": "panel.right",
                    "baseline_w": base.get("right_w"),
                    "live_w": c["right"]["w"],
                    "w": right_w,
                    "min_w": max(280, min(right_w, DEFAULT_CONSTRAINTS["right"]["min_w"])),
                    "max_w": max(right_w, DEFAULT_CONSTRAINTS["right"]["max_w"]),
                    "recommended_fixed_band": [max(280, right_w - 20), right_w + 20],
                },
                "log": {
                    "id": "panel.log",
                    "baseline_h": base.get("log_h"),
                    "live_h": c["log"]["h"],
                    "h": log_h,
                    "min_h": max(40, min(log_h, DEFAULT_CONSTRAINTS["log"]["min_h"])),
                    "max_h": max(log_h, DEFAULT_CONSTRAINTS["log"]["max_h"]),
                },
            },
            "splitters": {
                "main": {
                    "orientation": "horizontal",
                    "baseline_sizes": base.get("main_sizes"),
                    "sizes": main_sizes,
                    "stretch": [0, 1, 0],
                    "code_hook": "main_window._build_ui → split.setSizes",
                },
                "fit_display": {
                    "orientation": "vertical",
                    "baseline_sizes": base.get("fit_sizes"),
                    "sizes": fit_sizes,
                    "stretch": [4, 1],
                    "code_hook": "main_window.fit_display_splitter",
                },
            },
            "widgets": widgets,
            "text": text,
            "code_hooks": {
                "main_splitter_sizes": {
                    "file": "source/app/main_window.py",
                    "symbol": "split.setSizes",
                    "note": "Update setSizes([...]) to splitters.main.sizes",
                },
                "left_panel_width": {
                    "file": "source/app/main_window.py",
                    "symbol": "left.setMinimumWidth / left.setMaximumWidth",
                    "note": "Use panels.left.w for production min/max band",
                },
                "right_panel_width": {
                    "file": "source/app/main_window.py",
                    "symbol": "right.setMinimumWidth / right.setMaximumWidth",
                    "note": "Use panels.right.w",
                },
                "log_height": {
                    "file": "source/app/main_window.py",
                    "symbol": "txt_log.setMaximumHeight / setMinimumHeight",
                    "note": "Use panels.log.h",
                },
                "window_size": {
                    "file": "source/app/main_window.py",
                    "symbol": "self.resize / setMinimumSize",
                    "note": "Use window.w/h and min_*",
                },
                "i18n_text": {
                    "file": "source/app/i18n.py",
                    "symbol": "STRINGS",
                    "note": "Apply text entries with i18n:<key> to both zh and en",
                },
                "picked_widgets": {
                    "file": "source/app/main_window.py",
                    "symbol": "setFixedWidth/Height or min/max on named controls",
                    "note": "Apply widgets.* draft w/h by ui_id / path / text label",
                },
            },
        }
        return payload

    def export_layout(
        self, *, show_dialog: bool = True, prompt_notes: bool = True
    ) -> Optional[Path]:
        headless = _is_headless()
        interactive = show_dialog and not headless
        if interactive and prompt_notes:
            if not self.edit_notes(prompt_export=True):
                return None

        ui_dir = self.export_dir()
        self.refresh_export_path_label()
        payload = self.capture()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = ui_dir / f"layout.export-{stamp}.json"
        current_path = ui_dir / "layout.current.json"
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        export_path.write_text(text, encoding="utf-8")
        current_path.write_text(text, encoding="utf-8")
        try:
            # Always log full paths (folder + file)
            self.win._log(
                self.win.i18n.t("eng_export_dir_line").format(path=str(ui_dir))
            )
            msg = self.win.i18n.t("eng_exported").format(path=str(export_path))
            if payload.get("user_notes"):
                msg += "  " + self.win.i18n.t("eng_exported_with_notes")
            self.win._log(msg)
        except Exception:
            pass
        if interactive:
            body = (
                self.win.i18n.t("eng_export_dir_line").format(path=str(ui_dir))
                + "\n\n"
                + self.win.i18n.t("eng_exported").format(path=str(export_path))
            )
            notes = (payload.get("user_notes") or "").strip()
            if notes:
                preview = notes if len(notes) <= 400 else notes[:397] + "…"
                body += "\n\n" + self.win.i18n.t("eng_notes") + ":\n" + preview
            else:
                body += "\n\n" + self.win.i18n.t("eng_exported_no_notes")
            QMessageBox.information(
                self.win,
                self.win.i18n.t("eng_export"),
                body,
            )
        return export_path

    def apply_from_file_dialog(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        ui_dir = self.export_dir()
        path, _ = QFileDialog.getOpenFileName(
            self.win,
            self.win.i18n.t("eng_apply_file"),
            str(ui_dir),
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.warning(self.win, APP_NAME, str(exc))
            return
        self.apply_payload(data)
        try:
            self.win._log(self.win.i18n.t("eng_applied").format(path=path))
        except Exception:
            pass

    def apply_payload(self, data: dict) -> None:
        """Load JSON into **draft only** — does not resize the live production UI."""
        if not isinstance(data, dict) or data.get("schema") != SCHEMA_ID:
            QMessageBox.warning(
                self.win,
                APP_NAME,
                f"Invalid layout schema (need {SCHEMA_ID})",
            )
            return
        un = data.get("user_notes")
        if un is None:
            un = data.get("notes") if isinstance(data.get("notes"), str) else ""
            if isinstance(un, str) and (
                un.startswith("Exported from smartLarch Platform")
                or un.startswith("Exported from Auto LarchUI")
                or un.startswith("Engineering draft")
            ):
                un = ""
        if isinstance(un, str):
            self._user_notes = un
            self.retranslate()

        panels = data.get("panels") or {}
        if "left" in panels:
            p = panels["left"]
            self._draft["left_w"] = int(p.get("w") or p.get("live_w") or self._draft.get("left_w") or 270)
        if "right" in panels:
            p = panels["right"]
            self._draft["right_w"] = int(p.get("w") or p.get("live_w") or self._draft.get("right_w") or 380)
        if "log" in panels:
            p = panels["log"]
            self._draft["log_h"] = int(p.get("h") or p.get("live_h") or self._draft.get("log_h") or 110)

        splitters = data.get("splitters") or {}
        if splitters.get("main", {}).get("sizes"):
            self._draft["main_sizes"] = [int(x) for x in splitters["main"]["sizes"]]
        if splitters.get("fit_display", {}).get("sizes"):
            self._draft["fit_sizes"] = [int(x) for x in splitters["fit_display"]["sizes"]]

        for uid, spec in (data.get("widgets") or {}).items():
            if isinstance(spec, dict):
                self._widget_overrides[uid] = dict(spec)

        for key, val in (data.get("text") or {}).items():
            if isinstance(val, dict):
                self._text_overrides[key] = dict(val)

        if self.active:
            self._fill_spins_from_draft()
        try:
            self.win._log(self.win.i18n.t("eng_draft_loaded"))
        except Exception:
            pass

    def open_ui_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        ui_dir = self.export_dir()
        self.refresh_export_path_label()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ui_dir)))

    # ----- pick / right-click menu -----
    def eventFilter(self, obj, event):  # noqa: N802
        if not self.active:
            return super().eventFilter(obj, event)

        def _gpos():
            try:
                return event.globalPosition().toPoint()
            except Exception:
                try:
                    return event.globalPos()
                except Exception:
                    return QCursor.pos()

        # Right-click: select under cursor + control context menu (W/H/rename)
        if event.type() == QEvent.Type.MouseButtonPress:
            try:
                btn = event.button()
            except Exception:
                return super().eventFilter(obj, event)
            if self._is_eng_chrome(obj if isinstance(obj, QWidget) else None):
                return super().eventFilter(obj, event)
            gpos = _gpos()
            if btn == Qt.MouseButton.RightButton:
                target = self._pick_target_at(gpos)
                if target is not None:
                    self.select_widget(target)
                    self._show_control_context_menu(gpos)
                    return True
                if self._selected is not None:
                    self._show_control_context_menu(gpos)
                    return True
            # Pick mode left-click: select only
            if self._pick_mode and btn == Qt.MouseButton.LeftButton:
                target = self._pick_target_at(gpos)
                if target is not None:
                    self.select_widget(target)
                    return True
        return super().eventFilter(obj, event)

    def _editable_ancestor(self, w: Optional[QWidget]) -> Optional[QWidget]:
        cur = w
        depth = 0
        while cur is not None and depth < 8:
            if isinstance(cur, QLabel) and cur.text().strip():
                return cur
            if isinstance(cur, QAbstractButton):
                tx = cur.text().strip() if cur.text() else ""
                if tx and tx not in ("🔧", "🔧*", "📂", ">_"):
                    return cur
            cur = cur.parentWidget()
            depth += 1
        return None

    def _edit_widget_text(self, widget: QWidget) -> None:
        t = self.win.i18n.t
        current = widget.text() if hasattr(widget, "text") else ""
        i18n_key = self._guess_i18n_key(current)

        dlg = QDialog(self.win)
        dlg.setWindowTitle(t("eng_edit_text"))
        form = QFormLayout(dlg)
        ed_zh = QLineEdit()
        ed_en = QLineEdit()
        if i18n_key:
            try:
                from . import i18n as i18n_mod

                pair = i18n_mod.STRINGS.get(i18n_key, {})
                ed_zh.setText(pair.get("zh", current if self.win.i18n.lang == "zh" else ""))
                ed_en.setText(pair.get("en", current if self.win.i18n.lang != "zh" else ""))
            except Exception:
                ed_zh.setText(current if self.win.i18n.lang == "zh" else "")
                ed_en.setText(current if self.win.i18n.lang != "zh" else "")
        else:
            if self.win.i18n.lang == "zh":
                ed_zh.setText(current)
            else:
                ed_en.setText(current)
        form.addRow(t("eng_text_zh"), ed_zh)
        form.addRow(t("eng_text_en"), ed_en)
        if i18n_key:
            form.addRow(QLabel(f"i18n:{i18n_key}"))
        else:
            form.addRow(QLabel(t("eng_text_no_key")))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        zh, en = ed_zh.text(), ed_en.text()
        # Draft only — do not mutate live i18n or widget text
        if i18n_key:
            key = f"i18n:{i18n_key}"
            self._text_overrides[key] = {
                "zh": zh,
                "en": en,
                "source": i18n_key,
            }
        else:
            oid = self._ensure_ui_id(widget) if widget is not None else "widget"
            key = f"widget:{oid}"
            self._text_overrides[key] = {
                "zh": zh,
                "en": en,
                "source": oid,
            }
        # Also attach on widget draft row for agents that only scan widgets.*
        try:
            uid = self._ensure_ui_id(widget)
            row = dict(self._widget_overrides.get(uid) or {})
            row["ui_id"] = uid
            row["class"] = type(widget).__name__
            row["path"] = self._widget_path(widget)
            row["text_zh"] = zh
            row["text_en"] = en
            if i18n_key:
                row["i18n_key"] = i18n_key
            row["draft_only"] = True
            row["apply"] = "agent-source"
            self._widget_overrides[uid] = row
            self._widget_refs[uid] = widget
            if self._selected is widget:
                # Refresh label preview with drafted zh/en
                shown = zh if self.win.i18n.lang == "zh" else (en or zh)
                if shown:
                    self.lbl_sel.setText(f"{uid} 「{shown[:32]}」")
        except Exception:
            pass
        if self._preview_on:
            self._apply_preview(log=False)
        try:
            self.win._log(self.win.i18n.t("eng_text_drafted"))
        except Exception:
            pass

    def _guess_i18n_key(self, text: str) -> Optional[str]:
        if not text:
            return None
        try:
            from . import i18n as i18n_mod

            lang = self.win.i18n.lang
            for key, pair in i18n_mod.STRINGS.items():
                if pair.get(lang) == text:
                    return key
        except Exception:
            return None
        return None


def attach_eng_button(window, log_head_layout, log_layout=None) -> EngModeController:
    """Create controller, place 🔧 on log header, insert eng toolbar into log layout."""
    ctrl = EngModeController(window, log_layout=log_layout)
    window._eng_mode = ctrl
    log_head_layout.addWidget(ctrl.btn)
    return ctrl
