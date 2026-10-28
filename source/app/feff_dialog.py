"""Fit-page structure dialogs: AMCSD search, path picker, structure settings.

Text comes from i18n keys (see docs/PLAN_CifFeff_MultiFit_20261026.md section 9);
the host passes an I18n instance plus a bi() fallback for generic words.

Mark contract (AGENTS.md UI notes): a tick is an ICON in column 0, never
ItemIsUserCheckable / checkState; the tick toggles only when the click lands in
the left ~28 px icon zone; A/U/I affect visible (unfiltered) rows only; nothing
is ticked by default.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QCursor, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import feff_build
from .fit_engine import PathDef

_TICK_ROLE = Qt.ItemDataRole.UserRole + 1


def _icons() -> tuple:
    folder = Path(__file__).resolve().parent / "assets"
    return QIcon(str(folder / "checkbox_checked.svg")), QIcon(str(folder / "checkbox_empty.svg"))


class _MarkTableMixin:
    """Shared icon-tick behaviour for the dialogs' result tables."""

    tbl: QTableWidget

    def _init_marks(self) -> None:
        self._icon_on, self._icon_off = _icons()
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.cellClicked.connect(self._on_mark_cell_clicked)

    def _mark_item(self, item: QTableWidgetItem, marked: bool) -> None:
        item.setData(_TICK_ROLE, bool(marked))
        item.setIcon(self._icon_on if marked else self._icon_off)
        item.setFlags(
            (item.flags() | Qt.ItemFlag.ItemIsEnabled) & ~Qt.ItemFlag.ItemIsUserCheckable
        )

    def _is_marked(self, row: int) -> bool:
        item = self.tbl.item(row, 0)
        return bool(item and item.data(_TICK_ROLE))

    def _on_mark_cell_clicked(self, row: int, col: int) -> None:
        self.tbl.clearSelection()
        if col != 0:
            return
        item = self.tbl.item(row, 0)
        if item is None:
            return
        rect = self.tbl.visualItemRect(item)
        pos = self.tbl.viewport().mapFromGlobal(QCursor.pos())
        if not (rect.left() - 2 <= pos.x() <= rect.left() + 28):
            return
        self._mark_item(item, not bool(item.data(_TICK_ROLE)))

    def _bulk_mark(self, mode: str, rows: Optional[Sequence[int]] = None) -> None:
        if rows is None:
            rows = [r for r in range(self.tbl.rowCount()) if not self.tbl.isRowHidden(r)]
        for row in rows:
            item = self.tbl.item(row, 0)
            if item is None:
                continue
            old = bool(item.data(_TICK_ROLE))
            marked = True if mode == "all" else False if mode == "none" else not old
            self._mark_item(item, marked)

    def _mark_tools(self) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        for text, tip, mode in (
            ("A", self._t("mark_all_detail"), "all"),
            ("U", self._t("mark_none_detail"), "none"),
            ("I", self._t("mark_invert_detail"), "invert"),
        ):
            btn = QToolButton()
            btn.setText(text)
            btn.setFixedSize(22, 22)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda *_, m=mode: self._bulk_mark(m))
            row.addWidget(btn)
        row.addStretch(1)
        return host


class AmcsdSearchDialog(QDialog, _MarkTableMixin):
    """Search the AMCSD and add structures to the library table."""

    def __init__(
        self,
        parent,
        *,
        t: Callable,
        bi: Callable,
        lib_dir: Path,
        run_busy: Callable,
        on_add: Callable[[List[feff_build.StructureEntry]], None],
        has_full: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(parent)
        self._t = t
        self._bi = bi
        self._lib = Path(lib_dir)
        self._run_busy = run_busy
        self._on_add = on_add
        self._has_full = has_full or feff_build.has_full_amcsd
        self._hits: List[feff_build.CifHit] = []
        self.setWindowTitle(t("amcsd_dlg_title"))
        self.resize(640, 480)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        top = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText(t("cif_search_ph"))
        self.ed_search.returnPressed.connect(self._on_search)
        self.btn_search = QPushButton(t("cif_search"))
        self.btn_search.clicked.connect(self._on_search)
        self.chk_strict = QCheckBox(t("cif_strict"))
        self.chk_strict.setToolTip(t("cif_strict_tip"))
        top.addWidget(self.ed_search, 1)
        top.addWidget(self.btn_search)
        top.addWidget(self.chk_strict)
        root.addLayout(top)

        root.addWidget(self._mark_tools())
        self.tbl = QTableWidget(0, 5)
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setHorizontalHeaderLabels(
            [
                t("cif_col_id"),
                t("cif_col_mineral"),
                t("cif_col_formula"),
                t("cif_col_sg"),
                "a",
            ]
        )
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._init_marks()
        root.addWidget(self.tbl, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        row = QHBoxLayout()
        self.btn_add = QPushButton(t("cif_add_to_table"))
        self.btn_add.clicked.connect(self._on_add_clicked)
        self.btn_full = QPushButton(t("cif_full_db"))
        self.btn_full.setToolTip(t("cif_full_db_tip"))
        self.btn_full.clicked.connect(self._on_full_db)
        self.btn_full.setVisible(not bool(self._has_full()))
        self.btn_close = QPushButton(bi("关闭", "Close"))
        self.btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_full)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        root.addLayout(row)

    # -- search ---------------------------------------------------------
    def _on_search(self) -> None:
        text = self.ed_search.text().strip()
        if not text:
            return
        strict = self.chk_strict.isChecked()
        try:
            hits = self._run_busy(
                lambda: feff_build.search_amcsd(text, strict=strict),
                self._bi("AMCSD", "AMCSD"),
                self._t("cif_search"),
            )
        except Exception as exc:
            QMessageBox.warning(self, self.windowTitle(), self._t("cif_no_db", msg=str(exc)))
            return
        self._hits = list(hits or [])
        self._fill(self._hits)
        self.lbl_status.setText(f"{len(self._hits)}")

    def _fill(self, hits: Sequence[feff_build.CifHit]) -> None:
        self.tbl.setRowCount(0)
        for hit in hits:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            item = QTableWidgetItem(str(hit.ams_id))
            self._mark_item(item, False)
            item.setToolTip(hit.citation)
            self.tbl.setItem(r, 0, item)
            self.tbl.setItem(r, 1, QTableWidgetItem(hit.mineral))
            self.tbl.setItem(r, 2, QTableWidgetItem(hit.formula))
            self.tbl.setItem(r, 3, QTableWidgetItem(hit.spacegroup))
            a_item = QTableWidgetItem(
                "" if hit.a != hit.a else f"{hit.a:.4f}"
            )
            self.tbl.setItem(r, 4, a_item)
        self.tbl.clearSelection()

    def _checked_hits(self) -> List[feff_build.CifHit]:
        out = []
        for r, hit in enumerate(self._hits):
            if r < self.tbl.rowCount() and self._is_marked(r):
                out.append(hit)
        return out

    def _on_add_clicked(self) -> None:
        hits = self._checked_hits()
        if not hits:
            QMessageBox.information(self, self.windowTitle(), self._t("cif_add_none"))
            return
        entries: List[feff_build.StructureEntry] = []
        errors: List[str] = []
        for hit in hits:
            try:
                path = self._run_busy(
                    lambda h=hit: feff_build.export_amcsd_cif(h.ams_id, self._lib),
                    hit.mineral,
                    self._t("cif_add_to_table"),
                )
                entries.append(
                    feff_build.StructureEntry(
                        key=feff_build._ascii_name(f"amcsd_{hit.ams_id}", f"amcsd_{hit.ams_id}"),
                        source="amcsd",
                        source_id=str(hit.ams_id),
                        cif_path=str(path),
                        label=hit.mineral or f"AMCSD {hit.ams_id}",
                        formula=hit.formula,
                        spacegroup=hit.spacegroup,
                    )
                )
            except Exception as exc:
                errors.append(f"AMCSD {hit.ams_id}: {exc}")
        if entries:
            self._on_add(entries)
        if errors:
            QMessageBox.warning(self, self.windowTitle(), "\n".join(errors))
        if entries:
            self.accept()

    # -- full database --------------------------------------------------
    def _on_full_db(self) -> None:
        target = str(feff_build.full_amcsd_path())
        answer = QMessageBox.question(
            self,
            self.windowTitle(),
            self._t("cif_full_db_confirm", path=target),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path = self._run_busy(
                lambda: feff_build.download_full_amcsd(timeout=60),
                "AMCSD",
                self._t("cif_full_db"),
            )
        except Exception as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        self.lbl_status.setText(self._t("cif_full_db_done", path=str(path)))
        self.btn_full.setVisible(False)


class PathPickDialog(QDialog, _MarkTableMixin):
    """Pick FEFF paths from structure runs; build a table or a starting model."""

    def __init__(
        self,
        parent,
        *,
        t: Callable,
        bi: Callable,
        paths: Sequence[feff_build.FeffPathInfo],
        windows,
        s02: float = 0.90,
        rmax: float = 4.0,
        on_add_paths: Callable[[List[PathDef]], None],
        on_model: Callable[[list, list, dict, bool], None],
        marked: Optional[Sequence[int]] = None,
        show_per_dataset: bool = False,
        auto_model: bool = False,
    ):
        super().__init__(parent)
        self._t = t
        self._bi = bi
        self._paths = list(paths)
        self._windows = windows
        self._on_add_paths = on_add_paths
        self._on_model = on_model
        self.setWindowTitle(t("paths_dlg_title"))
        self.resize(760, 520)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        filt = QHBoxLayout()
        self.chk_ss = QCheckBox(t("cif_ss_only"))
        self.chk_ss.setChecked(True)
        self.chk_ss.toggled.connect(self._apply_filter)
        self.lbl_rmax = QLabel(t("cif_rmax_filter"))
        self.sp_rmax = QDoubleSpinBox()
        self.sp_rmax.setDecimals(2)
        self.sp_rmax.setRange(0.5, 10.0)
        self.sp_rmax.setSingleStep(0.1)
        self.sp_rmax.setValue(float(rmax))
        self.sp_rmax.valueChanged.connect(self._apply_filter)
        filt.addWidget(self.chk_ss)
        filt.addWidget(self.lbl_rmax)
        filt.addWidget(self.sp_rmax)
        filt.addStretch(1)
        root.addLayout(filt)

        root.addWidget(self._mark_tools())
        self.tbl = QTableWidget(0, 6)
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.setHorizontalHeaderLabels(
            [
                t("cif_col_source"),
                t("cif_col_reff"),
                t("cif_col_n"),
                t("cif_col_legs"),
                t("cif_col_atoms"),
                t("cif_col_amp"),
            ]
        )
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._init_marks()
        root.addWidget(self.tbl, 1)

        bottom = QHBoxLayout()
        lbl_s02 = QLabel(t("cif_s02"))
        lbl_s02.setToolTip(t("cif_s02_tip"))
        self.sp_s02 = QDoubleSpinBox()
        self.sp_s02.setDecimals(3)
        self.sp_s02.setRange(0.10, 1.50)
        self.sp_s02.setSingleStep(0.01)
        self.sp_s02.setValue(float(s02))
        self.sp_s02.setToolTip(t("cif_s02_tip"))
        self.chk_model_fit = QCheckBox(t("cif_model_fit"))
        self.chk_per_ds = QCheckBox(t("cif_model_per_ds"))
        self.chk_per_ds.setToolTip(t("cif_model_per_ds_tip"))
        self.btn_add = QPushButton(t("cif_add"))
        self.btn_add.setToolTip(t("cif_add_tip"))
        self.btn_model = QPushButton(t("cif_model"))
        self.btn_model.setToolTip(t("cif_model_tip"))
        self.btn_close = QPushButton(bi("关闭", "Close"))
        self.btn_add.clicked.connect(self._on_add)
        self.btn_model.clicked.connect(self._on_model_clicked)
        self.btn_close.clicked.connect(self.reject)
        bottom.addWidget(lbl_s02)
        bottom.addWidget(self.sp_s02)
        bottom.addWidget(self.btn_add)
        bottom.addWidget(self.btn_model)
        bottom.addWidget(self.chk_model_fit)
        bottom.addWidget(self.chk_per_ds)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_close)
        root.addLayout(bottom)

        self._fill(marked)
        self._apply_filter()
        if auto_model:
            # Quick fit: the ticked rows are exactly the default rule's choice,
            # so go straight to the starting-model confirm (S0^2 still editable).
            # Quick fit promises "… → Auto fit": the box must be ticked before
            # the confirm reads it.
            from PySide6.QtCore import QTimer

            self.chk_model_fit.setChecked(True)

            # bound to the dialog: Qt drops the call if the dialog is gone first
            QTimer.singleShot(0, self, self._on_model_clicked)

    # -- table ----------------------------------------------------------
    def _fill(self, marked: Optional[Sequence[int]]) -> None:
        wanted = set(int(i) for i in (marked or []))
        for p in self._paths:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            label = p.source_label or Path(p.run_dir).name
            item = QTableWidgetItem(label)
            self._mark_item(item, r in wanted)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p.feff_file)
            self.tbl.setItem(r, 0, item)
            self.tbl.setItem(r, 1, QTableWidgetItem(f"{p.reff:.4f}"))
            self.tbl.setItem(
                r, 2, QTableWidgetItem("" if p.degen != p.degen else f"{p.degen:.4g}")
            )
            self.tbl.setItem(r, 3, QTableWidgetItem("" if p.nleg != p.nleg else f"{int(p.nleg)}"))
            self.tbl.setItem(r, 4, QTableWidgetItem(p.atoms or "?"))
            self.tbl.setItem(
                r, 5, QTableWidgetItem("" if p.amp != p.amp else f"{p.amp:.1f}")
            )
        self.tbl.clearSelection()

    def _apply_filter(self, *_args) -> None:
        rmax = float(self.sp_rmax.value())
        ss_only = self.chk_ss.isChecked()
        for r in range(self.tbl.rowCount()):
            item = self.tbl.item(r, 0)
            path = item.data(Qt.ItemDataRole.UserRole) if item else None
            hide = False
            if isinstance(path, feff_build.FeffPathInfo):
                if ss_only and not path.single_scattering:
                    hide = True
                elif path.reff != path.reff or float(path.reff) > rmax + 1e-9:
                    hide = True
            self.tbl.setRowHidden(r, hide)

    def _checked_paths(self) -> List[feff_build.FeffPathInfo]:
        out: List[feff_build.FeffPathInfo] = []
        for r in range(self.tbl.rowCount()):
            if self.tbl.isRowHidden(r):
                continue
            if not self._is_marked(r):
                continue
            item = self.tbl.item(r, 0)
            path = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(path, feff_build.FeffPathInfo):
                out.append(path)
        return out

    def _pathdefs(self, chosen: Sequence[feff_build.FeffPathInfo]) -> List[PathDef]:
        out: List[PathDef] = []
        for p in chosen:
            elem = p.absorber or ""
            out.append(
                PathDef(
                    label=f"{elem}-{(p.scatterers[0] if p.scatterers else '?')} {p.reff:.2f}",
                    feff_file=p.feff_file,
                    use=True,
                    elem=elem,
                )
            )
        return out

    # -- actions --------------------------------------------------------
    def _on_add(self) -> None:
        chosen = self._checked_paths()
        if not chosen:
            QMessageBox.information(self, self.windowTitle(), self._t("cif_add_none"))
            return
        self._on_add_paths(self._pathdefs(chosen))

    def _on_model_clicked(self) -> None:
        chosen = self._checked_paths()
        if not chosen:
            QMessageBox.information(self, self.windowTitle(), self._t("cif_add_none"))
            return
        s02 = float(self.sp_s02.value())
        per_ds = "{ds}" if bool(self.chk_per_ds.isChecked()) else ""
        try:
            gds, pathdefs, report = feff_build.starting_model(
                chosen, s02, self._windows, per_dataset=per_ds
            )
        except ValueError as exc:
            code = str(exc)
            if code == "multiple-scattering":
                QMessageBox.information(self, self.windowTitle(), self._t("cif_model_ms"))
            elif code == "budget":
                nidp = feff_build.n_independent(self._windows)
                QMessageBox.information(
                    self,
                    self.windowTitle(),
                    self._t("cif_model_budget_full", nidp=nidp),
                )
            else:
                QMessageBox.warning(self, self.windowTitle(), code)
            return
        shells = report.get("shells") or []
        nvar = int(report.get("nvar", 0))
        nidp = float(report.get("nidp", float("nan")))
        try:
            budget_msg = self._t("cif_model_budget", nvar=nvar, nidp=nidp)
        except Exception:
            budget_msg = ""
        if nvar >= nidp:
            QMessageBox.information(self, self.windowTitle(), budget_msg)
            return
        table = "\n".join(
            f"{s.get('tag')}  {s.get('absorber')}-{s.get('scatterer')}  "
            f"Reff {float(s.get('reff', 0)):.3f}  N0 {float(s.get('n0', 0)):.4g}"
            for s in shells
        )
        detail = f"{table}\n\nnvar {nvar} / nidp {nidp:.1f}"
        if report.get("warn"):
            detail += "\n" + self._t("cif_model_budget_warn", nvar=nvar, nidp=nidp)
        answer = QMessageBox.question(
            self,
            self.windowTitle(),
            self._t("cif_model_confirm", n=len(shells)) + "\n\n" + detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._model_detail = detail
        self._on_model(gds, pathdefs, report, bool(self.chk_model_fit.isChecked()))


class CopyToGroupsDialog(QDialog):
    """Pick the datasets a copied path row should be assigned to (v4.8.0)."""

    def __init__(self, parent, *, t: Callable, bi: Callable, names: Sequence[str]):
        super().__init__(parent)
        self._t = t
        self._bi = bi
        self.setWindowTitle(t("path_copy_title"))
        self.resize(380, 360)
        root = QVBoxLayout(self)
        self.lst = QListWidget()
        self._icon_on, self._icon_off = _icons()
        self.lst.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for name in names:
            item = QListWidgetItem(str(name))
            item.setData(Qt.ItemDataRole.UserRole, str(name))
            item.setData(_TICK_ROLE, False)
            item.setIcon(self._icon_off)
            item.setFlags(
                (item.flags() | Qt.ItemFlag.ItemIsEnabled) & ~Qt.ItemFlag.ItemIsUserCheckable
            )
            self.lst.addItem(item)
        self.lst.itemClicked.connect(self._on_item_clicked)
        tools_row = QHBoxLayout()
        tools_row.setContentsMargins(0, 0, 0, 0)
        tools_row.setSpacing(3)
        for text, tip, mode in (
            ("A", t("mark_all_detail"), "all"),
            ("U", t("mark_none_detail"), "none"),
            ("I", t("mark_invert_detail"), "invert"),
        ):
            btn = QToolButton()
            btn.setText(text)
            btn.setFixedSize(22, 22)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda *_, m=mode: self._bulk(m))
            tools_row.addWidget(btn)
        tools_row.addStretch(1)
        root.addLayout(tools_row)
        root.addWidget(self.lst, 1)
        row = QHBoxLayout()
        self.btn_ok = QPushButton(bi("确定", "OK"))
        self.btn_cancel = QPushButton(bi("取消", "Cancel"))
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(self.btn_ok)
        row.addWidget(self.btn_cancel)
        root.addLayout(row)

    def _on_item_clicked(self, item) -> None:
        self.lst.clearSelection()
        rect = self.lst.visualItemRect(item)
        pos = self.lst.viewport().mapFromGlobal(QCursor.pos())
        if not (rect.left() - 2 <= pos.x() <= rect.left() + 28):
            return
        marked = not bool(item.data(_TICK_ROLE))
        item.setData(_TICK_ROLE, marked)
        item.setIcon(self._icon_on if marked else self._icon_off)

    def _bulk(self, mode: str) -> None:
        for i in range(self.lst.count()):
            item = self.lst.item(i)
            old = bool(item.data(_TICK_ROLE))
            marked = True if mode == "all" else False if mode == "none" else not old
            item.setData(_TICK_ROLE, marked)
            item.setIcon(self._icon_on if marked else self._icon_off)

    def selected_names(self) -> List[str]:
        out = []
        for i in range(self.lst.count()):
            item = self.lst.item(i)
            if bool(item.data(_TICK_ROLE)):
                out.append(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        return [n for n in out if n]


class CifSearchDialog(QDialog, _MarkTableMixin):
    """Search CIFs from one source picked in a dropdown (v4.11.0): the offline
    library (zip / folder / CSD pack), the AMCSD bundled with larch, the CSD
    through the CCDC bridge, or COD.

    Ticked rows can be added to the structure library or compared: two rows side
    by side, or one offline row against the online entry with the same id.
    """

    SOURCES = ("offline", "amcsd", "ccdc", "cod")

    FIELDS = (
        ("cifsrc_field_name", "name"),
        ("cifsrc_field_formula", "formula"),
        ("cifsrc_field_id", "id"),
        ("cifsrc_field_all", "all"),
    )

    def __init__(
        self,
        parent,
        *,
        t: Callable,
        bi: Callable,
        lib_dir: Path,
        run_busy: Callable,
        on_add: Callable[[List[feff_build.StructureEntry]], None],
        offline_source: str = "",
        index_dir: Optional[Path] = None,
        python: str = "",
        ccdc_ok: bool = False,
        default_elements: Sequence[str] = (),
        online: bool = False,
        bridge=None,
        cod=None,
        amcsd=None,
        source: str = "",
        cancelled: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(parent)
        from . import ccdc_bridge, cif_library

        self._t = t
        self._bi = bi
        self._lib = Path(lib_dir)
        self._index_dir = Path(index_dir) if index_dir else self._lib / "cif_index"
        self._run_busy = run_busy
        self._on_add = on_add
        self._offline = str(offline_source or "")
        self._python = str(python or "")
        self._ccdc_ok = bool(ccdc_ok)
        self._bridge = bridge or ccdc_bridge
        self._cod = cod or cif_library  # cod_search / cod_cif_text (injectable for tests)
        self._amcsd = amcsd or feff_build  # search_amcsd / export_amcsd_cif / cif_text_for_ams_id
        self._cancelled = cancelled
        self._index: Optional[dict] = None
        self._hits: List[dict] = []
        self.setWindowTitle(t("cifsrc_dlg_title"))
        self.resize(820, 540)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        top = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText(t("cif_search_ph"))
        self.ed_search.returnPressed.connect(self._on_search)
        self.cmb_field = QComboBox()
        for key, value in self.FIELDS:
            self.cmb_field.addItem(t(key), value)
        self.cmb_field.setCurrentIndex(3)
        self.btn_search = QPushButton(t("cif_search"))
        self.btn_search.clicked.connect(self._on_search)
        top.addWidget(self.ed_search, 1)
        top.addWidget(self.cmb_field)
        top.addWidget(self.btn_search)
        root.addLayout(top)

        opts = QHBoxLayout()
        self.lbl_must = QLabel(t("ccdc_must_have"))
        self.ed_must = QLineEdit(" ".join(default_elements))
        self.chk_strict = QCheckBox(t("cif_strict"))
        self.chk_strict.setToolTip(t("cif_strict_tip"))
        self.chk_disorder = QCheckBox(t("ccdc_no_disorder"))
        self.chk_disorder.setChecked(True)
        opts.addWidget(self.lbl_must)
        opts.addWidget(self.ed_must, 1)
        opts.addWidget(self.chk_strict)
        opts.addWidget(self.chk_disorder)
        root.addLayout(opts)

        # One source dropdown (v4.11.0): offline library / AMCSD / CCDC / COD
        src = QHBoxLayout()
        self.lbl_source = QLabel(t("cifsrc_source"))
        self.cmb_source = QComboBox()
        for key in self.SOURCES:
            self.cmb_source.addItem(t(f"cifsrc_src_{key}"), key)
            self.cmb_source.setItemData(
                self.cmb_source.count() - 1, t(f"cifsrc_src_{key}_tip"), Qt.ItemDataRole.ToolTipRole
            )
        self.cmb_source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.lbl_offline = QLabel("")
        self.lbl_offline.setStyleSheet("color:#6b625d;")
        self.btn_full = QPushButton(t("cif_full_db"))
        self.btn_full.setToolTip(t("cif_full_db_tip"))
        self.btn_full.clicked.connect(self._on_full_db)
        src.addWidget(self.lbl_source)
        src.addWidget(self.cmb_source)
        src.addWidget(self.lbl_offline, 1)
        src.addWidget(self.btn_full)
        root.addLayout(src)

        root.addWidget(self._mark_tools())
        self.tbl = QTableWidget(0, 8)
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.setHorizontalHeaderLabels(
            [
                t("cifsrc_col_id"),
                t("ccdc_col_name"),
                t("cif_col_formula"),
                t("cif_col_sg"),
                t("cifsrc_col_a"),
                t("cifsrc_col_r"),
                t("cifsrc_col_year"),
                t("cifsrc_col_source"),
            ]
        )
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._init_marks()
        root.addWidget(self.tbl, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        row = QHBoxLayout()
        self.btn_add = QPushButton(t("cif_add_to_table"))
        self.btn_add.clicked.connect(self._on_add_clicked)
        self.btn_compare = QPushButton(t("cifsrc_compare"))
        self.btn_compare.setToolTip(t("cifsrc_compare_tip"))
        self.btn_compare.clicked.connect(self._on_compare)
        self.btn_close = QPushButton(bi("关闭", "Close"))
        self.btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_compare)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        root.addLayout(row)

        # default: the offline library when set, else the CSD when connected,
        # else the AMCSD (no network); `online` asks for an online source
        if source not in self.SOURCES:
            if online or not self._offline:
                source = "ccdc" if self._ccdc_ok else ("cod" if online else "amcsd")
            else:
                source = "offline"
        self.cmb_source.currentIndexChanged.connect(lambda *_: self._source_changed())
        self.cmb_source.setCurrentIndex(self.SOURCES.index(source))
        self._source_changed()

    # -- helpers --------------------------------------------------------
    def _source_changed(self) -> None:
        source = self._source()
        name = Path(self._offline).name if self._offline else ""
        if source == "offline":
            self.lbl_offline.setText(name or self._t("cifsrc_offline_none"))
            self.lbl_offline.setToolTip(self._offline)
        else:
            self.lbl_offline.setText(self._t(f"cifsrc_src_{source}_tip"))
            self.lbl_offline.setToolTip("")
        self.chk_disorder.setEnabled(source == "ccdc")
        try:
            show_full = source == "amcsd" and not bool(self._amcsd.has_full_amcsd())
        except Exception:
            show_full = False
        self.btn_full.setVisible(show_full)

    def _source(self) -> str:
        return str(self.cmb_source.currentData() or "offline")

    def _online_source(self) -> str:
        """Where one ticked offline hit looks for its twin: the CSD, else COD."""
        return "ccdc" if (self._ccdc_ok and self._python) else "cod"

    def _cancel_kw(self) -> dict:
        return {"cancel": self._cancelled} if self._cancelled is not None else {}

    def _elements(self) -> List[str]:
        return [tok for tok in self.ed_must.text().replace(",", " ").split() if tok]

    def _fail_text(self, answer: dict) -> str:
        """Bilingual CSD failure text; an unknown kind never shows a raw i18n key."""
        kind = str(answer.get("kind") or "other")
        kind = {"not_found": "notfound"}.get(kind, kind)
        if kind != "other":
            key = f"ccdc_fail_{kind}"
            text = self._t(key)
            if text and text != key:
                return text
        return self._t("ccdc_fail_other", msg=str(answer.get("message", "") or kind)[:400])

    def _offline_index(self) -> Optional[dict]:
        if self._index is not None:
            return self._index
        from . import cif_library

        try:
            self._index = self._run_busy(
                lambda: cif_library.load_or_build_index(
                    self._offline, self._index_dir, cancel=self._cancelled
                ),
                Path(self._offline).name,
                self._t("cifsrc_indexing"),
            )
        except cif_library.IndexCancelled:
            return None
        except Exception as exc:
            QMessageBox.warning(self, self.windowTitle(), self._t("cif_read_failed", msg=str(exc)[:400]))
            return None
        return self._index

    # -- search ---------------------------------------------------------
    def _on_search(self) -> None:
        from . import cif_library

        text = self.ed_search.text().strip()
        field = str(self.cmb_field.currentData() or "all")
        elements = self._elements()
        if not text and not elements:
            return
        source = self._source()
        hits: List[dict] = []
        if source == "offline":
            if not self._offline:
                QMessageBox.information(self, self.windowTitle(), self._t("cifsrc_no_offline"))
                return
            index = self._offline_index()
            if index is None:
                return
            for e in cif_library.search_library(index, text, field, elements, self.chk_strict.isChecked()):
                hits.append({**e, "source": "offline"})
        elif source == "ccdc":
            if not (self._ccdc_ok and self._python):
                QMessageBox.information(self, self.windowTitle(), self._t("cifsrc_ccdc_off"))
                return
            ccdc_field = {"name": "name", "formula": "all", "id": "refcode", "all": "all"}[field]
            try:
                answer = self._run_busy(
                    lambda: self._bridge.search(
                        self._python,
                        text=text,
                        field=ccdc_field,
                        must_have=elements,
                        no_disorder=bool(self.chk_disorder.isChecked()),
                        max_hits=200,
                        **self._cancel_kw(),
                    ),
                    "CSD",
                    self._t("cif_search"),
                )
            except Exception as exc:
                QMessageBox.warning(self, self.windowTitle(), str(exc)[:600])
                return
            if not answer.get("ok"):
                if answer.get("kind") != "cancelled":
                    QMessageBox.warning(self, self.windowTitle(), self._fail_text(answer))
                return
            for h in answer.get("hits") or []:
                hits.append(
                    {
                        "source": "ccdc",
                        "id": str(h.get("refcode", "")),
                        "name": str(h.get("name", "")),
                        "formula": str(h.get("formula", "")),
                        "spacegroup": str(h.get("spacegroup", "")),
                        "r_factor": h.get("r_factor"),
                        "disorder": bool(h.get("disorder")),
                        "doi": str(h.get("doi", "")),
                    }
                )
        elif source == "amcsd":
            # AMCSD searches a mineral name or an element list; required
            # elements alone become the element list
            query = text or " ".join(elements)
            strict = bool(self.chk_strict.isChecked()) and not text
            try:
                found = self._run_busy(
                    lambda: self._amcsd.search_amcsd(query, strict=strict),
                    "AMCSD",
                    self._t("cif_search"),
                )
            except Exception as exc:
                QMessageBox.warning(self, self.windowTitle(), self._t("cif_no_db", msg=str(exc)))
                return
            need = {e for e in elements}
            for h in found or []:
                els = cif_library.formula_elements(h.formula)
                if text and need and (
                    not need <= set(els) or (self.chk_strict.isChecked() and set(els) != need)
                ):
                    continue
                hits.append(
                    {
                        "source": "amcsd",
                        "id": str(h.ams_id),
                        "name": h.mineral,
                        "formula": h.formula,
                        "elements": els,
                        "spacegroup": h.spacegroup,
                        "a": h.a,
                        "doi": h.citation,
                    }
                )
        else:  # cod
            try:
                hits = list(
                    self._run_busy(
                        lambda: self._cod.cod_search(text, field, elements, self.chk_strict.isChecked()),
                        "COD",
                        self._t("cif_search"),
                    )
                    or []
                )
            except cif_library.TooManyResults as exc:
                QMessageBox.information(self, self.windowTitle(), self._t("cifsrc_too_many", n=exc.count))
                return
            except Exception as exc:
                QMessageBox.warning(self, self.windowTitle(), self._t("cifsrc_net_failed", msg=str(exc)[:400]))
                return
        self._hits = hits
        self._fill(hits)
        self.lbl_status.setText(
            self._t("cifsrc_found", n=len(hits), src=self._t(f"cifsrc_source_{source}"))
            + "  ·  "
            + self._t("ccdc_saving_to", path=str(self._lib / "cif"))
        )

    def _fill(self, hits: Sequence[dict]) -> None:
        self.tbl.setRowCount(0)
        for hit in hits:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            item = QTableWidgetItem(str(hit.get("id", "")))
            self._mark_item(item, False)
            item.setToolTip(str(hit.get("member") or hit.get("doi") or ""))
            item.setData(Qt.ItemDataRole.UserRole, hit)
            self.tbl.setItem(r, 0, item)
            self.tbl.setItem(r, 1, QTableWidgetItem(str(hit.get("name", ""))))
            self.tbl.setItem(r, 2, QTableWidgetItem(str(hit.get("formula", ""))))
            self.tbl.setItem(r, 3, QTableWidgetItem(str(hit.get("spacegroup", ""))))
            a = hit.get("a")
            rf = hit.get("r_factor")
            year = hit.get("year")
            self.tbl.setItem(r, 4, QTableWidgetItem(_fmt(a, "{:.4f}")))
            self.tbl.setItem(r, 5, QTableWidgetItem(_fmt(rf, "{:.2f}")))
            self.tbl.setItem(r, 6, QTableWidgetItem(str(year) if year else "—"))
            self.tbl.setItem(
                r, 7, QTableWidgetItem(self._t(f"cifsrc_source_{hit.get('source', 'offline')}"))
            )
        self.tbl.clearSelection()

    def _checked_hits(self) -> List[dict]:
        out = []
        for r in range(self.tbl.rowCount()):
            if self._is_marked(r):
                item = self.tbl.item(r, 0)
                hit = item.data(Qt.ItemDataRole.UserRole) if item else None
                if isinstance(hit, dict):
                    out.append(hit)
        return out

    # -- fetching CIFs ----------------------------------------------------
    def _save_hit(self, hit: dict, out_dir: Path) -> Path:
        """Write the hit's CIF into *out_dir* (runs inside run_busy)."""
        from . import cif_library

        source = hit.get("source")
        cid = str(hit.get("id", "")).strip()
        if source == "offline":
            return cif_library.extract_member(self._offline, str(hit["member"]), out_dir, cid)
        if source == "cod":
            return cif_library.save_cif(self._cod.cod_cif_text(cid), out_dir, "COD", cid)
        if source == "amcsd":
            if Path(out_dir) == self._lib / "cif":  # same file name as before the merge
                return self._amcsd.export_amcsd_cif(int(cid), self._lib)
            return cif_library.save_cif(self._amcsd.cif_text_for_ams_id(int(cid)), out_dir, "AMCSD", cid)
        out = Path(out_dir) / f"CSD_{feff_build._ascii_name(cid, 'refcode')}.cif"
        out.parent.mkdir(parents=True, exist_ok=True)
        answer = self._bridge.export_cif(self._python, cid, out, **self._cancel_kw())
        if not answer.get("ok"):
            raise RuntimeError(self._fail_text(answer) if answer.get("kind") != "cancelled" else "cancelled")
        return out

    def _on_add_clicked(self) -> None:
        hits = self._checked_hits()
        if not hits:
            QMessageBox.information(self, self.windowTitle(), self._t("cif_add_none"))
            return
        entries: List[feff_build.StructureEntry] = []
        errors: List[str] = []
        for hit in hits:
            if self._cancelled is not None and self._cancelled():
                break  # Cancel on the previous fetch stops the batch
            cid = str(hit.get("id", "")).strip()
            try:
                path = self._run_busy(
                    lambda h=hit: self._save_hit(h, self._lib / "cif"),
                    cid,
                    self._t("cif_add_to_table"),
                )
            except Exception as exc:
                errors.append(f"{cid}: {exc}")
                continue
            source = str(hit.get("source"))
            entries.append(
                feff_build.StructureEntry(
                    key=feff_build._ascii_name(f"{source}_{cid}", f"{source}_{cid}"),
                    source=source,
                    source_id=cid,
                    cif_path=str(path),
                    label=str(hit.get("name") or cid),
                    formula=str(hit.get("formula", "")),
                    spacegroup=str(hit.get("spacegroup", "")),
                )
            )
        if entries:
            self._on_add(entries)
        if errors:
            QMessageBox.warning(self, self.windowTitle(), chr(10).join(errors))
        if entries:
            self.accept()

    # -- comparison -------------------------------------------------------
    def _online_twin(self, hit: dict) -> Optional[dict]:
        """The online entry with the same id as an offline hit (CCDC refcode / COD id)."""
        cid = str(hit.get("id", "")).strip()
        source = self._online_source()
        if source == "ccdc":
            answer = self._run_busy(
                lambda: self._bridge.search(
                    self._python, text=cid, field="refcode", must_have=[], no_disorder=False,
                    max_hits=5, **self._cancel_kw(),
                ),
                "CSD",
                self._t("cifsrc_compare"),
            )
            for h in (answer.get("hits") or []) if answer.get("ok") else []:
                if str(h.get("refcode", "")).upper() == cid.upper():
                    return {"source": "ccdc", "id": str(h.get("refcode")), "name": str(h.get("name", ""))}
            return None
        if cid.isdigit():
            return {"source": "cod", "id": cid}
        return None

    def _on_compare(self) -> None:
        from . import cif_library

        hits = self._checked_hits()
        if len(hits) == 1 and hits[0].get("source") == "offline":
            try:
                twin = self._online_twin(hits[0])
            except Exception as exc:
                QMessageBox.warning(self, self.windowTitle(), self._t("cifsrc_net_failed", msg=str(exc)[:400]))
                return
            if twin is None:
                QMessageBox.information(
                    self, self.windowTitle(), self._t("cifsrc_lookup_none", id=hits[0].get("id", ""))
                )
                return
            hits = [hits[0], twin]
        if len(hits) != 2:
            QMessageBox.information(self, self.windowTitle(), self._t("cifsrc_compare_need"))
            return
        cache = self._lib / "cif_compare"
        texts = []
        try:
            for hit in hits:
                path = self._run_busy(
                    lambda h=hit: self._save_hit(h, cache), str(hit.get("id", "")), self._t("cifsrc_compare")
                )
                texts.append(Path(path).read_text(encoding="utf-8", errors="replace"))
            result = self._run_busy(
                lambda: cif_library.compare_cifs(texts[0], texts[1]), "CIF", self._t("cifsrc_compare")
            )
        except Exception as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc)[:600])
            return
        labels = [
            f"{self._t('cifsrc_source_' + str(h.get('source', 'offline')))} {h.get('id', '')}" for h in hits
        ]
        dlg = CifCompareDialog(self, t=self._t, bi=self._bi, result=result, labels=labels)
        dlg.exec()

    # -- AMCSD full database ------------------------------------------------
    def _on_full_db(self) -> None:
        target = str(self._amcsd.full_amcsd_path())
        answer = QMessageBox.question(
            self,
            self.windowTitle(),
            self._t("cif_full_db_confirm", path=target),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path = self._run_busy(
                lambda: self._amcsd.download_full_amcsd(timeout=60), "AMCSD", self._t("cif_full_db")
            )
        except Exception as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        self.lbl_status.setText(self._t("cif_full_db_done", path=str(path)))
        self.btn_full.setVisible(False)


def _fmt(value, pattern: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return pattern.format(number) if math.isfinite(number) else "—"


class CifCompareDialog(QDialog):
    """Two CIFs side by side: header fields, then the atomic-position summary."""

    def __init__(self, parent, *, t: Callable, bi: Callable, result: dict, labels: Sequence[str]):
        super().__init__(parent)
        self.setWindowTitle(t("cmp_title"))
        self.resize(640, 520)
        root = QVBoxLayout(self)
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(
            [t("cmp_col_item"), str(labels[0]), str(labels[1]), t("cmp_col_diff")]
        )
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        differ = QBrush(Qt.GlobalColor.yellow)
        for row in result.get("rows") or []:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            field = str(row.get("field"))
            if row.get("delta") is not None or field in ("a", "b", "c", "alpha", "beta", "gamma", "volume", "year", "r_factor"):
                pat = "{:.0f}" if field == "year" else "{:.4f}" if field in ("a", "b", "c") else "{:.2f}"
                cells = [_fmt(row.get("a"), pat), _fmt(row.get("b"), pat), _fmt(row.get("delta"), "{:+.4f}")]
            else:
                cells = [str(row.get("a") or "—"), str(row.get("b") or "—"), "" if row.get("same") else "≠"]
            items = [QTableWidgetItem(t(f"cmp_field_{field}"))] + [QTableWidgetItem(c) for c in cells]
            for c, item in enumerate(items):
                if not row.get("same"):
                    item.setBackground(differ)
                self.tbl.setItem(r, c, item)
        root.addWidget(self.tbl, 1)
        coords = result.get("coords") or {}
        if coords.get("ok"):
            text = t(
                "cmp_coords",
                max=float(coords.get("max", float("nan"))),
                mean=float(coords.get("mean", float("nan"))),
                n=int(coords.get("unmatched", 0)),
                na=int(coords.get("n_a", 0)),
                nb=int(coords.get("n_b", 0)),
                shift=", ".join(f"{v:g}" for v in (coords.get("origin_shift") or [0, 0, 0])),
            )
        else:
            text = t("cmp_coords_fail", msg=str(coords.get("error", "")))
        self.lbl_coords = QLabel(text + "\n" + t("cmp_note"))
        self.lbl_coords.setWordWrap(True)
        root.addWidget(self.lbl_coords)
        btn = QPushButton(bi("关闭", "Close"))
        btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        root.addLayout(row)


class AutoModelDialog(QDialog):
    """Auto fit from structures: settings page, then the same dialog shows the
    search result (was Auto model before v4.11.0).

    Nothing runs until Start is pressed; the host performs the search and calls
    show_result(), then exec()s the dialog again for the result page. With
    ``tables_only`` the settings page also offers "Fit current tables only"
    (the structures stay ticked but the user wants the existing model).
    """

    MODE_SINGLE = "single"
    MODE_MIXED = "mixed"

    def __init__(
        self,
        parent,
        *,
        t: Callable,
        bi: Callable,
        mode: str,
        candidates: Sequence[str] = (),
        s02: float = 0.90,
        has_ms: bool = False,
        max_phases: int = 2,
        user_notes: str = "",
        tables_only: bool = False,
    ):
        super().__init__(parent)
        self._t = t
        self._bi = bi
        self.mode = mode if mode in (self.MODE_SINGLE, self.MODE_MIXED) else self.MODE_SINGLE
        self._applied = False
        self._tables_only = False
        self.setWindowTitle(t("auto_dlg_title"))
        self.resize(560, 460)
        self.stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.addWidget(self.stack, 1)

        # ---- page 0: settings ----
        page0 = QWidget()
        v0 = QVBoxLayout(page0)
        form = QFormLayout()
        self.sp_s02 = QDoubleSpinBox()
        self.sp_s02.setDecimals(3)
        self.sp_s02.setRange(0.10, 1.50)
        self.sp_s02.setSingleStep(0.01)
        self.sp_s02.setValue(float(s02))
        self.sp_s02.setToolTip(t("cif_s02_tip"))
        form.addRow(QLabel(t("cif_s02")), self.sp_s02)
        self.chk_s02_free = QCheckBox(t("auto_s02_free"))
        self.chk_s02_free.setToolTip(t("auto_s02_free_tip"))
        self.chk_s02_free.setVisible(self.mode == self.MODE_SINGLE)
        form.addRow(QLabel(""), self.chk_s02_free)
        self.chk_try_ms = QCheckBox(t("auto_try_ms"))
        self.chk_try_ms.setToolTip(t("auto_try_ms_tip"))
        self.chk_try_ms.setVisible(self.mode == self.MODE_SINGLE and bool(has_ms))
        form.addRow(QLabel(""), self.chk_try_ms)
        self.cmb_main = QComboBox()
        self.cmb_main.addItem(t("auto_main_auto"), "")
        for name in candidates:
            self.cmb_main.addItem(str(name), str(name))
        self.cmb_main.setVisible(self.mode == self.MODE_MIXED)
        form.addRow(QLabel(t("auto_main_phase")), self.cmb_main)
        self.chk_limits = QCheckBox(t("auto_limits"))
        self.chk_limits.setToolTip(t("auto_limits_tip"))
        self.chk_limits.setChecked(True)
        self.chk_limits.setVisible(self.mode == self.MODE_MIXED)
        form.addRow(QLabel(""), self.chk_limits)
        self.sp_max_phases = QSpinBox()
        self.sp_max_phases.setRange(1, 4)
        self.sp_max_phases.setValue(int(max_phases))
        form.addRow(QLabel(t("max_phases")), self.sp_max_phases)
        # main phase / max phases only mean something for a mixed fit; hide
        # the whole row (label included) in single mode
        mixed = self.mode == self.MODE_MIXED
        form.setRowVisible(self.cmb_main, mixed)
        form.setRowVisible(self.sp_max_phases, mixed)
        v0.addLayout(form)
        self.txt_notes = QPlainTextEdit()
        self.txt_notes.setPlaceholderText(t("auto_notes_ph"))
        self.txt_notes.setMaximumHeight(80)
        self.txt_notes.setPlainText(str(user_notes or "")[:500])
        v0.addWidget(QLabel(t("auto_notes")))
        v0.addWidget(self.txt_notes)
        row0 = QHBoxLayout()
        self.btn_start = QPushButton(t("auto_start"))
        self.btn_cancel = QPushButton(bi("取消", "Cancel"))
        self.btn_start.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_tables_only = QPushButton(t("auto_tables_only"))
        self.btn_tables_only.setToolTip(t("auto_tables_only_tip"))
        self.btn_tables_only.clicked.connect(self._on_tables_only)
        self.btn_tables_only.setVisible(bool(tables_only))
        row0.addWidget(self.btn_tables_only)
        row0.addStretch(1)
        row0.addWidget(self.btn_start)
        row0.addWidget(self.btn_cancel)
        v0.addLayout(row0)
        self.stack.addWidget(page0)

        # ---- page 1: result ----
        page1 = QWidget()
        v1 = QVBoxLayout(page1)
        self.tbl_result = QTableWidget(0, 7)
        self.tbl_result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_result.horizontalHeader().setStretchLastSection(True)
        self.tbl_result.setHorizontalHeaderLabels(
            [
                t("auto_col_step"),
                t("auto_col_phase"),
                t("auto_col_chi"),
                t("auto_col_r"),
                t("auto_col_p"),
                t("auto_col_audit"),
                t("auto_col_result"),
            ]
        )
        self.tbl_result.verticalHeader().setVisible(False)
        self.tbl_result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_result.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        look = QFontMetrics(self.tbl_result.font()).height() + 10
        self.tbl_result.setMinimumHeight(look * 7)
        v1.addWidget(self.tbl_result, 1)
        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        v1.addWidget(self.lbl_summary)
        row1 = QHBoxLayout()
        self.btn_apply = QPushButton(t("auto_apply"))
        self.btn_close = QPushButton(bi("关闭", "Close"))
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_close.clicked.connect(self.reject)
        row1.addWidget(self.btn_apply)
        row1.addStretch(1)
        row1.addWidget(self.btn_close)
        v1.addLayout(row1)
        self.stack.addWidget(page1)

    # -- settings page --------------------------------------------------
    def values(self) -> dict:
        return {
            "mode": self.mode,
            "s02": float(self.sp_s02.value()),
            "s02_free": bool(self.chk_s02_free.isChecked()),
            "try_ms": bool(self.chk_try_ms.isChecked()),
            "main": str(self.cmb_main.currentData() or ""),
            "limits": bool(self.chk_limits.isChecked()),
            "max_phases": int(self.sp_max_phases.value()),
            "notes": str(self.txt_notes.toPlainText())[:500],
        }

    def _on_tables_only(self) -> None:
        self._tables_only = True
        self.reject()

    def tables_only_chosen(self) -> bool:
        return bool(self._tables_only)

    # -- result page ----------------------------------------------------
    def show_result(self, rows: Sequence[dict], summary: str, applied: bool = False) -> None:
        """Fill the result page. ``applied``: the host already wrote the final
        model to the tables (Auto fit), so Apply is not offered again."""
        self.tbl_result.setRowCount(0)
        for row in rows or []:
            r = self.tbl_result.rowCount()
            self.tbl_result.insertRow(r)
            values = [
                str(row.get("step", "")),
                str(row.get("tag", "")),
                f"{float(row['chi_square']):.4g}" if row.get("chi_square") == row.get("chi_square") else "—",
                f"{float(row['r_factor']):.4f}" if row.get("r_factor") == row.get("r_factor") else "—",
                f"{float(row['p']):.3f}" if isinstance(row.get("p"), (int, float)) else "—",
                str(row.get("audit", "")),
                str(row.get("result_text", "")),
            ]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if c == 5:
                    level = str(row.get("audit_level", ""))
                    item.setForeground(
                        QBrush(
                            Qt.GlobalColor.darkRed
                            if level == "fail"
                            else Qt.GlobalColor.darkYellow
                            if level == "warn"
                            else Qt.GlobalColor.darkGreen
                        )
                    )
                tooltip = str(row.get("tooltip", "") or "")
                if tooltip:
                    item.setToolTip(tooltip)
                self.tbl_result.setItem(r, c, item)
        self.lbl_summary.setText(str(summary or ""))
        self.btn_apply.setEnabled(bool(rows) and not applied)
        self.btn_apply.setVisible(not applied)
        self.stack.setCurrentIndex(1)

    def _on_apply(self) -> None:
        self._applied = True
        self.accept()

    def was_applied(self) -> bool:
        return bool(self._applied)


class QuickCheckDialog(QDialog):
    """Quick check result (v4.11.0): one minimal single-phase fit per ticked
    structure, best R-factor first. Read-only; "Tick only this one" keeps the
    chosen structure ticked for Auto fit.
    """

    def __init__(
        self,
        parent,
        *,
        t: Callable,
        bi: Callable,
        rows: Sequence[dict],
        on_keep: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self._t = t
        self._rows = list(rows or [])
        self._on_keep = on_keep
        self.setWindowTitle(t("qc_title"))
        self.resize(620, 360)
        root = QVBoxLayout(self)
        hint = QLabel(t("qc_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b625d; font-size:11px;")
        root.addWidget(hint)
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(
            [
                t("qc_col_struct"),
                t("auto_col_r"),
                t("qc_col_redchi"),
                t("auto_col_audit"),
                t("qc_col_note"),
            ]
        )
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for row in self._rows:
            self._add_row(row)
        if self.tbl.rowCount():
            self.tbl.selectRow(0)
        root.addWidget(self.tbl, 1)
        buttons = QHBoxLayout()
        self.btn_keep = QPushButton(t("qc_keep"))
        self.btn_keep.setToolTip(t("qc_keep_tip"))
        self.btn_keep.clicked.connect(self._keep)
        self.btn_keep.setEnabled(any(r.get("ok") for r in self._rows) and on_keep is not None)
        self.btn_close = QPushButton(bi("关闭", "Close"))
        self.btn_close.clicked.connect(self.reject)
        buttons.addWidget(self.btn_keep)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_close)
        root.addLayout(buttons)

    @staticmethod
    def _num(value, fmt: str) -> str:
        return fmt.format(float(value)) if isinstance(value, (int, float)) and value == value else "—"

    def _add_row(self, row: dict) -> None:
        t = self._t
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        level = str(row.get("audit_level") or "")
        if not row.get("ok"):
            audit = "—"
        else:
            audit = t({"fail": "auto_audit_fail", "warn": "auto_audit_warn"}.get(level, "auto_audit_pass"))
        note = str(row.get("error") or "")[:120] if not row.get("ok") else " ".join(row.get("flags") or [])
        if not row.get("ok") and not note:
            note = t("qc_failed")
        values = [
            str(row.get("label", "")),
            self._num(row.get("r_factor"), "{:.4f}"),
            self._num(row.get("redchi"), "{:.4g}"),
            audit,
            note,
        ]
        for c, text in enumerate(values):
            item = QTableWidgetItem(text)
            if c == 3 and row.get("ok"):
                item.setForeground(
                    QBrush(
                        Qt.GlobalColor.darkRed
                        if level == "fail"
                        else Qt.GlobalColor.darkYellow
                        if level == "warn"
                        else Qt.GlobalColor.darkGreen
                    )
                )
            if c == 4 and note:
                item.setToolTip(note)
            self.tbl.setItem(r, c, item)

    def selected_label(self) -> str:
        r = self.tbl.currentRow()
        if r < 0 or r >= len(self._rows) or not self._rows[r].get("ok"):
            return ""
        return str(self._rows[r].get("label", ""))

    def _keep(self) -> None:
        label = self.selected_label()
        if label and self._on_keep is not None:
            self._on_keep(label)
            self.accept()


class StructureSettingsDialog(QDialog):
    """Absorber / edge / site / cluster / absorber-substitution for one entry."""

    def __init__(
        self,
        parent,
        *,
        t: Callable,
        bi: Callable,
        entry: feff_build.StructureEntry,
        elements: Sequence[str],
        run_busy: Callable,
    ):
        super().__init__(parent)
        self._t = t
        self._bi = bi
        self._entry = entry
        self._elements = [e for e in elements if e]
        self._run_busy = run_busy
        self.setWindowTitle(t("struct_dlg_title"))
        self.resize(460, 300)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.cmb_absorber = QComboBox()
        self.cmb_absorber.addItems(self._elements)
        if entry.absorber and entry.absorber not in self._elements:
            self.cmb_absorber.addItem(entry.absorber)
        self.cmb_absorber.setCurrentText(entry.absorber or (self._elements[0] if self._elements else ""))
        self.cmb_edge = QComboBox()
        self.cmb_edge.addItems(["K", "L1", "L2", "L3"])
        self.cmb_edge.setCurrentText(entry.edge or "K")
        self.cmb_site = QComboBox()
        self.cmb_site.setToolTip(t("cif_site_tip"))
        self.sp_cluster = QDoubleSpinBox()
        self.sp_cluster.setDecimals(1)
        self.sp_cluster.setRange(4.0, 10.0)
        self.sp_cluster.setSingleStep(0.5)
        self.sp_cluster.setValue(float(entry.cluster or 6.0))
        self.cmb_as = QComboBox()
        self.cmb_as.addItem("\u2014", "")
        for el in self._elements:
            self.cmb_as.addItem(el, el)
        idx = self.cmb_as.findData(entry.absorber_as or "")
        self.cmb_as.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_as.setToolTip(t("cif_as_tip"))

        form.addRow(QLabel(t("cif_absorber")), self.cmb_absorber)
        form.addRow(QLabel(t("cif_edge")), self.cmb_edge)
        form.addRow(QLabel(t("cif_site")), self.cmb_site)
        form.addRow(QLabel(t("cif_cluster")), self.sp_cluster)
        form.addRow(QLabel(t("cif_as")), self.cmb_as)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_ok = QPushButton(bi("确定", "OK"))
        self.btn_cancel = QPushButton(bi("取消", "Cancel"))
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(self.btn_ok)
        row.addWidget(self.btn_cancel)
        root.addLayout(row)

        self._sites: List[feff_build.SiteInfo] = []
        self._load_sites()
        # a site of one element must never be kept for another absorber
        self.cmb_absorber.currentTextChanged.connect(lambda *_: self._load_sites())

    def _load_sites(self) -> None:
        absorber = self.cmb_absorber.currentText().strip()
        self.cmb_site.clear()
        self.cmb_site.addItem(self._t("cif_site_all"), "all")
        if not absorber:
            return
        try:
            sites = self._run_busy(
                lambda: feff_build.absorber_sites(
                    feff_build.read_cif_text(self._entry.cif_path), absorber
                ),
                self._entry.label,
                self._t("cif_site"),
            )
        except Exception as exc:
            self.cmb_site.setToolTip(str(exc))
            return
        self._sites = list(sites or [])
        for info in self._sites:
            self.cmb_site.addItem(info.label or f"s{info.site_index}", str(info.site_index))
        wanted = str(self._entry.site or "all")
        idx = self.cmb_site.findData(wanted)
        self.cmb_site.setCurrentIndex(idx if idx >= 0 else 0)

    def result_values(self) -> Dict[str, object]:
        return {
            "absorber": self.cmb_absorber.currentText().strip(),
            "edge": self.cmb_edge.currentText().strip() or "K",
            "site": str(self.cmb_site.currentData() or "all"),
            "cluster": float(self.sp_cluster.value()),
            "absorber_as": str(self.cmb_as.currentData() or ""),
        }
