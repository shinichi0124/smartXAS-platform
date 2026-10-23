"""Interactive Analysis dialogs for LCF / PCA / MCR-ALS / Pre-edge peaks.

These are *not* one-click fire-and-forget: the user must pick spectra,
set parameters, press Run, then optionally Export. Science is still
larch (lincombo_fit / pca_train+pca_fit / prepeaks_*) plus pyMCR for MCR-ALS.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .athena_ops import (
    format_lcf_report,
    format_mcr_report,
    format_pca_report,
    format_prepeak_report,
    lincombo_fit,
    lincombo_fitall,
    mcr_als_fit,
    pca_train_fit,
    prepeak_fit,
)
from .project_model import GroupRecord
from .wavelet import (
    CAUCHY,
    MORLET,
    auto_params,
    cwt,
    default_dR,
    draw_wt,
    save_wt_png,
    write_wt_csv,
)

# (group_id, display_name) helpers from host
GroupPick = Tuple[str, str]
GetXY = Callable[[GroupRecord, str], Tuple[Optional[np.ndarray], Optional[np.ndarray]]]
FindGroup = Callable[[str], Optional[GroupRecord]]
DefaultWindow = Callable[[GroupRecord], Tuple[Optional[float], Optional[float]]]
GetMeta = Callable[[GroupRecord], dict]  # e0, edge_step, etc. for soft warnings


# ---- wavelet (CWT) shared helpers -------------------------------------
# QSettings keys live here (not in wavelet.py) so the kernel stays Qt-free.
# kmin/kmax/dk are deliberately NOT persisted: every dataset has its own k
# range, so they are always re-derived from the data (spec §3).
_WT_SETTINGS = (
    ("wavelet", "wavelet_type", str),
    ("kw", "kw", int),
    ("sigma", "sigma", float),
    ("eta", "eta", float),
    ("norder", "norder", float),
    ("Rmin", "Rmin", float),
    ("Rmax", "Rmax", float),
    ("dR", "dR", float),
)


def wt_stored_params(store) -> dict:
    """Read persisted wavelet parameters. Absent/invalid keys are omitted."""
    out: dict = {}
    if store is None:
        return out
    for param_key, setting_key, cast in _WT_SETTINGS:
        raw = store.value(f"wt/{setting_key}")
        if raw is None:
            continue
        try:
            value = cast(raw)
        except (TypeError, ValueError):
            continue
        if param_key == "wavelet" and value not in (MORLET, CAUCHY):
            continue
        out[param_key] = value
    return out


def wt_save_params(store, params: dict) -> None:
    if store is None:
        return
    for param_key, setting_key, _cast in _WT_SETTINGS:
        if param_key in params:
            store.setValue(f"wt/{setting_key}", params[param_key])


def wt_safe_name(name) -> str:
    """Filesystem-safe stem for ``wt_<group>.*`` (same rule as the PCA comp columns)."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name or ""))[:40]
    return safe or "group"


def _wavelet_out_paths(out_base: Path, payload: dict) -> List[Path]:
    """Every file the wavelet branch writes — also used for the overwrite check."""
    out_base = Path(out_base)
    paths: List[Path] = []
    for row in payload.get("wt_groups") or []:
        stem = f"wt_{wt_safe_name(row.get('name'))}"
        paths.append(out_base.with_name(stem + ".txt"))
        paths.append(out_base.with_name(stem + ".png"))
    return paths


def export_analysis_bundle(
    out_base: Path,
    report: str,
    payload: dict,
    *,
    kind: str,
    overwrite: bool = False,
) -> List[Path]:
    """Write report .txt + curves .csv next to *out_base* (no extension).

    *overwrite*: the Save dialog already asked the user. Default False so
    programmatic calls cannot clobber sibling ``*_rankings.csv`` files.
    """
    out_base = Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    txt_path = out_base.with_suffix(".txt")
    csv_path = out_base.with_suffix(".csv")
    conflicts = [p for p in (txt_path, csv_path) if p.exists()]
    conflicts.extend(p for p in out_base.parent.glob(out_base.name + "_*.csv") if p.exists())
    if kind == "wavelet":
        conflicts.extend(p for p in _wavelet_out_paths(out_base, payload) if p.exists())
    if conflicts and not overwrite:
        names = ", ".join(sorted({p.name for p in conflicts}))
        raise FileExistsError(f"Export would overwrite existing analysis file(s): {names}")
    txt_path.write_text(report, encoding="utf-8")
    written = [txt_path]

    x = np.asarray(payload.get("xdata") if payload.get("xdata") is not None else payload.get("x") or [], dtype=float).ravel()
    ydata = np.asarray(payload.get("ydata") if payload.get("ydata") is not None else [], dtype=float).ravel()
    yfit = np.asarray(payload.get("yfit") if payload.get("yfit") is not None else [], dtype=float).ravel()
    ycomps = payload.get("ycomps") or {}
    if x.size >= 2:
        headers = ["x"]
        cols: List[np.ndarray] = [x]
        if ydata.size >= 2:
            headers.append("data")
            cols.append(ydata)
        if yfit.size >= 2:
            headers.append("fit")
            cols.append(yfit)
        if isinstance(ycomps, dict):
            for name, arr in ycomps.items():
                a = np.asarray(arr, dtype=float).ravel()
                if a.size < 2:
                    continue
                safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name))[:40]
                headers.append(f"comp_{safe}")
                cols.append(a)
        n = max(len(c) for c in cols)
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            for i in range(n):
                row = []
                for c in cols:
                    if i < len(c) and np.isfinite(c[i]):
                        row.append(f"{float(c[i]):.8g}")
                    else:
                        row.append("")
                w.writerow(row)
        written.append(csv_path)

    # PCA: eigenvalues + IND + optional TT-SPOIL + abstract PC curves
    if kind == "pca":
        eig = payload.get("eigenvalues") or []
        var = payload.get("variances") or []
        ind = payload.get("ind") or []
        cum = payload.get("cum_variance") or []
        if eig or var or ind:
            eig_path = out_base.with_name(out_base.name + "_components.csv")
            with open(eig_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["component", "eigenvalue", "variance_fraction", "cum_variance", "IND"])
                n = max(len(eig), len(var), len(ind), len(cum))
                for i in range(n):
                    w.writerow(
                        [
                            f"PC{i + 1}",
                            f"{float(eig[i]):.8g}" if i < len(eig) else "",
                            f"{float(var[i]):.8g}" if i < len(var) else "",
                            f"{float(cum[i]):.8g}" if i < len(cum) else "",
                            f"{float(ind[i + 1]):.8g}" if i + 1 < len(ind) else "",
                        ]
                    )
            written.append(eig_path)
        tt = payload.get("tt") or []
        if tt:
            tt_path = out_base.with_name(out_base.name + "_tt_spoil.csv")
            with open(tt_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["name", "spoil", "grade", "rfactor", "aet", "rep", "ret"])
                for row in tt:
                    w.writerow(
                        [
                            row.get("name", ""),
                            f"{float(row.get('spoil', float('nan'))):.8g}",
                            row.get("grade", ""),
                            f"{float(row.get('rfactor', float('nan'))):.8g}",
                            f"{float(row.get('aet', float('nan'))):.8g}",
                            f"{float(row.get('rep', row.get('edl', float('nan')))):.8g}",
                            f"{float(row.get('ret', float('nan'))):.8g}",
                        ]
                    )
            written.append(tt_path)
        # Abstract PC spectra (x + PC1..)
        ycomps = payload.get("ycomps") or {}
        xpc = np.asarray(payload.get("xdata") if payload.get("xdata") is not None else [], dtype=float).ravel()
        pcs = {k: np.asarray(v, dtype=float).ravel() for k, v in ycomps.items() if str(k).startswith("PC")}
        if xpc.size >= 2 and pcs:
            pc_path = out_base.with_name(out_base.name + "_pc_spectra.csv")
            headers = ["x"] + list(pcs.keys())
            cols = [xpc] + [pcs[k] for k in pcs]
            n = max(len(c) for c in cols)
            with open(pc_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(headers)
                for i in range(n):
                    row = []
                    for c in cols:
                        if i < len(c) and np.isfinite(c[i]):
                            row.append(f"{float(c[i]):.8g}")
                        else:
                            row.append("")
                    w.writerow(row)
            written.append(pc_path)
    # LCF combinatorial rankings
    if kind == "lcf":
        rankings = payload.get("rankings") or []
        if rankings:
            rk_path = out_base.with_name(out_base.name + "_rankings.csv")
            with open(rk_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["rank", "redchi", "rfactor", "weights"])
                for row in rankings:
                    wtxt = ";".join(
                        f"{k}={float(v):.6g}" for k, v in (row.get("weights") or {}).items()
                    )
                    w.writerow(
                        [
                            row.get("rank", ""),
                            f"{float(row.get('redchi', float('nan'))):.8g}",
                            f"{float(row.get('rfactor', float('nan'))):.8g}",
                            wtxt,
                        ]
                    )
            written.append(rk_path)
    # MCR-ALS: concentration matrix + pure spectra
    if kind == "mcr":
        conc = payload.get("concentrations") or []
        pnames = payload.get("pure_names") or []
        if conc:
            c_path = out_base.with_name(out_base.name + "_concentrations.csv")
            with open(c_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                headers = ["spectrum"] + list(pnames) + ["rmse"]
                w.writerow(headers)
                for row in conc:
                    weights = row.get("weights") or []
                    w.writerow(
                        [row.get("name", "")]
                        + [f"{float(x):.8g}" for x in weights]
                        + [f"{float(row.get('rmse', float('nan'))):.8g}"]
                    )
            written.append(c_path)
        xpc = np.asarray(
            payload.get("xdata") if payload.get("xdata") is not None else [], dtype=float
        ).ravel()
        ycomps = payload.get("ycomps") or {}
        if xpc.size >= 2 and isinstance(ycomps, dict) and ycomps:
            ps_path = out_base.with_name(out_base.name + "_pure_spectra.csv")
            keys = list(ycomps.keys())
            cols = [xpc] + [np.asarray(ycomps[k], dtype=float).ravel() for k in keys]
            headers = ["x"] + keys
            n = max(len(c) for c in cols)
            with open(ps_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(headers)
                for i in range(n):
                    row = []
                    for c in cols:
                        if i < len(c) and np.isfinite(c[i]):
                            row.append(f"{float(c[i]):.8g}")
                        else:
                            row.append("")
                    w.writerow(row)
            written.append(ps_path)
    # Wavelet (CWT): one wt_<group>.csv + heatmap .png per group.
    # The generic curves .csv above is skipped for this kind (payload carries no
    # xdata) — the wt_*.csv layout (K,R,coef header, %.6f, k fastest) IS the
    # interchange format (v2.17.0: csv replaces the old space-separated .txt).
    if kind == "wavelet":
        params = payload.get("wt_params") or {}
        for row in payload.get("wt_groups") or []:
            # NB: no `x or []` here — these are numpy arrays, whose truth value is
            # ambiguous. Test-only-None instead.
            k_raw, r_raw, w_raw = row.get("k_grid"), row.get("R_grid"), row.get("W")
            k_grid = np.asarray([] if k_raw is None else k_raw, dtype=float).ravel()
            R_grid = np.asarray([] if r_raw is None else r_raw, dtype=float).ravel()
            mag = np.asarray([] if w_raw is None else w_raw, dtype=float)
            if k_grid.size < 2 or R_grid.size < 1 or mag.size != k_grid.size * R_grid.size:
                continue
            mag = mag.reshape(R_grid.size, k_grid.size)
            stem = f"wt_{wt_safe_name(row.get('name'))}"
            wt_csv = out_base.with_name(stem + ".csv")
            write_wt_csv(wt_csv, k_grid, R_grid, mag)
            written.append(wt_csv)
            wt_png = out_base.with_name(stem + ".png")
            save_wt_png(
                wt_png,
                k_grid,
                R_grid,
                mag,
                params.get("kmin"),
                params.get("kmax"),
                title=str(row.get("name") or ""),
            )
            written.append(wt_png)
    return written


class _AnalysisDialogBase(QDialog):
    """Shared: report pane + Run / Export / Close."""

    def __init__(
        self,
        parent,
        title: str,
        bi: Callable,
        *,
        default_export_dir: Optional[Path] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 560)
        self._bi = bi
        self._default_export_dir = Path(default_export_dir) if default_export_dir else Path.home()
        self._last_payload: Optional[dict] = None
        self._last_report: str = ""
        self._last_overlay: Optional[dict] = None
        self._kind = "analysis"

        self.root = QVBoxLayout(self)
        self.root.setSpacing(8)

        self.params_host = QVBoxLayout()
        self.root.addLayout(self.params_host)

        self.lbl_report = QLabel(self._bi("结果", "Result"))
        self.root.addWidget(self.lbl_report)
        self.txt_report = QPlainTextEdit()
        self.txt_report.setReadOnly(True)
        self.txt_report.setPlaceholderText(
            self._bi(
                "设定参数并点「运行」；不会自动计算。",
                "Set parameters and click Run — nothing runs until then.",
            )
        )
        self.txt_report.setMinimumHeight(180)
        self.root.addWidget(self.txt_report, 1)

        row = QHBoxLayout()
        self.btn_run = QPushButton(self._bi("运行", "Run"))
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self._on_run)
        self.btn_export = QPushButton(self._bi("导出结果…", "Export result…"))
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_apply_plot = QPushButton(self._bi("叠到主图", "Overlay on plot"))
        self.btn_apply_plot.setEnabled(False)
        self.btn_apply_plot.clicked.connect(self._on_apply_plot)
        self.btn_write_groups = QPushButton(self._bi("写入 Groups", "Write to Groups"))
        self.btn_write_groups.setEnabled(False)
        self.btn_write_groups.setVisible(False)  # subclasses enable when supported
        self.btn_write_groups.setToolTip(
            self._bi(
                "将结果谱写入左侧 Groups 列表（派生组）",
                "Add result spectra to the Groups list (derived groups)",
            )
        )
        self.btn_write_groups.clicked.connect(self._on_write_groups)
        self.btn_close = QPushButton(self._bi("关闭", "Close"))
        self.btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_apply_plot)
        row.addWidget(self.btn_write_groups)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        self.root.addLayout(row)

        self._on_result: Optional[Callable] = None  # (report, overlay) -> None
        self._on_write_groups: Optional[Callable] = None  # (payload) -> int | None

    def set_result_callback(self, fn: Callable) -> None:
        self._on_result = fn

    def set_write_groups_callback(self, fn: Callable) -> None:
        """Host adds pure/result spectra as GroupRecords. Returns count written."""
        self._on_write_groups = fn
        self.btn_write_groups.setVisible(True)

    def _set_success(self, report: str, payload: dict, overlay: dict) -> None:
        self._last_report = report
        self._last_payload = payload
        self._last_overlay = overlay
        self.txt_report.setPlainText(report)
        self.btn_export.setEnabled(True)
        self.btn_apply_plot.setEnabled(True)
        if self.btn_write_groups.isVisible():
            self.btn_write_groups.setEnabled(True)
        if self._on_result:
            self._on_result(report, overlay)

    def _on_write_groups(self) -> None:
        if not self._last_payload or not self._on_write_groups:
            return
        try:
            n = self._on_write_groups(self._last_payload)
        except Exception as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        n = int(n or 0)
        QMessageBox.information(
            self,
            self.windowTitle(),
            self._bi(
                f"已写入 {n} 条纯相谱到 Groups（派生组，已标记）。",
                f"Wrote {n} pure spectrum group(s) (derived, marked).",
            ),
        )

    def _on_run(self) -> None:
        raise NotImplementedError

    def _on_export(self) -> None:
        if not self._last_payload or not self._last_report:
            return
        import time

        # Same folder as Tools → Export data / Settings → data export folder
        try:
            self._default_export_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        stamp = time.strftime("%Y%m%d_%H%M%S")
        start = self._default_export_dir / f"{self._kind}_{stamp}"
        path, _ = QFileDialog.getSaveFileName(
            self,
            self._bi("导出分析结果", "Export analysis result"),
            str(start),
            "Text report (*.txt);;All files (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path:
            return
        base = Path(path)
        if base.suffix.lower() == ".txt":
            base = base.with_suffix("")
        try:
            written = export_analysis_bundle(
                base,
                self._last_report,
                self._last_payload,
                kind=self._kind,
                overwrite=True,
            )
        except Exception as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        names = "\n".join(str(p) for p in written)
        QMessageBox.information(
            self,
            self.windowTitle(),
            self._bi(f"已导出：\n{names}", f"Exported:\n{names}"),
        )

    def _on_apply_plot(self) -> None:
        if self._last_overlay and self._on_result:
            self._on_result(self._last_report, self._last_overlay)


def _fill_group_list(
    lst: QListWidget,
    groups: Sequence[GroupRecord],
    *,
    preselect_ids: Optional[set] = None,
    multi: bool = True,
) -> None:
    """Fill a spectrum picker using the app-wide icon-mark contract."""
    lst.clear()
    lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
    preselect_ids = preselect_ids or set()
    icon_dir = Path(__file__).resolve().parent / "assets"
    checked_icon = QIcon(str(icon_dir / "checkbox_checked.svg"))
    empty_icon = QIcon(str(icon_dir / "checkbox_empty.svg"))
    lst.setProperty("analysis_checked_icon", checked_icon)
    lst.setProperty("analysis_empty_icon", empty_icon)
    for g in groups:
        it = QListWidgetItem(g.name)
        it.setData(Qt.ItemDataRole.UserRole, g.id)
        flags = it.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        it.setFlags(flags & ~Qt.ItemFlag.ItemIsUserCheckable)
        marked = g.id in preselect_ids
        it.setData(Qt.ItemDataRole.UserRole + 1, marked)
        it.setIcon(checked_icon if marked else empty_icon)
        lst.addItem(it)
    lst.clearSelection()
    if not bool(lst.property("analysis_mark_connected")):
        lst.itemClicked.connect(lambda item, widget=lst: _toggle_analysis_mark(widget, item))
        lst.setProperty("analysis_mark_connected", True)


def _set_analysis_item_marked(lst: QListWidget, item: QListWidgetItem, marked: bool) -> None:
    item.setData(Qt.ItemDataRole.UserRole + 1, bool(marked))
    icon = lst.property("analysis_checked_icon" if marked else "analysis_empty_icon")
    if isinstance(icon, QIcon):
        item.setIcon(icon)
    lst.clearSelection()


def _toggle_analysis_mark(lst: QListWidget, item: QListWidgetItem) -> None:
    rect = lst.visualItemRect(item)
    pos = lst.viewport().mapFromGlobal(QCursor.pos())
    if not (rect.left() - 2 <= pos.x() <= rect.left() + 28):
        lst.clearSelection()
        return
    marked = bool(item.data(Qt.ItemDataRole.UserRole + 1))
    _set_analysis_item_marked(lst, item, not marked)


def _analysis_mark_tools(lst: QListWidget, bi: Callable):
    """Return the shared A/U/I compact bulk-mark row and its buttons."""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(3)
    buttons = []
    tips = (
        bi("全部标记", "Mark all"),
        bi("全部取消", "Unmark all"),
        bi("反向标记", "Invert marks"),
    )
    for text, tip in zip(("A", "U", "I"), tips):
        btn = QToolButton()
        btn.setText(text)
        btn.setFixedSize(22, 22)
        btn.setToolTip(tip)
        row.addWidget(btn)
        buttons.append(btn)
    row.addStretch(1)

    def apply(mode: str) -> None:
        for i in range(lst.count()):
            item = lst.item(i)
            if item is None:
                continue
            old = bool(item.data(Qt.ItemDataRole.UserRole + 1))
            marked = True if mode == "all" else False if mode == "none" else not old
            _set_analysis_item_marked(lst, item, marked)

    buttons[0].clicked.connect(lambda: apply("all"))
    buttons[1].clicked.connect(lambda: apply("none"))
    buttons[2].clicked.connect(lambda: apply("invert"))
    return host, buttons[0], buttons[1], buttons[2]


def _analysis_mark_list_host(lst: QListWidget, tools: QWidget) -> QWidget:
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    layout.addWidget(tools)
    layout.addWidget(lst, 1)
    return host


def _sync_mcr_st_nonneg(dialog) -> None:
    """Apply the array-aware default for MCR pure-spectrum constraints."""
    arr = str(dialog.cmb_array.currentData() or "norm")
    dialog.chk_st_nonneg.setChecked(arr not in ("chi", "dmude"))
    dialog.chk_st_nonneg.setToolTip(
        dialog._bi(
            "χ(k) 与 dμ/dE 可包含负值，默认不限制纯谱非负；norm/μ 默认启用。",
            "χ(k) and dμ/dE can be signed, so pure-spectrum non-negativity defaults off; norm/μ defaults on.",
        )
    )


def _checked_ids(lst: QListWidget) -> List[str]:
    out = []
    for i in range(lst.count()):
        it = lst.item(i)
        if it is None:
            continue
        if bool(it.data(Qt.ItemDataRole.UserRole + 1)):
            gid = it.data(Qt.ItemDataRole.UserRole)
            if gid:
                out.append(str(gid))
    return out


def _energy_spins(bi, *, unit_tip: str = "eV") -> Tuple[QDoubleSpinBox, QDoubleSpinBox]:
    lo = QDoubleSpinBox()
    hi = QDoubleSpinBox()
    for sp in (lo, hi):
        sp.setDecimals(2)
        sp.setRange(-1e6, 1e6)
        sp.setSingleStep(1.0)
    lo.setToolTip(bi(f"拟合下限（绝对 {unit_tip}）", f"Fit lower limit (absolute {unit_tip})"))
    hi.setToolTip(bi(f"拟合上限（绝对 {unit_tip}）", f"Fit upper limit (absolute {unit_tip})"))
    return lo, hi


def _group_meta(g: GroupRecord) -> dict:
    """Best-effort e0 / edge_step for soft warnings."""
    e0 = None
    edge_step = None
    try:
        if g.session and g.session.arrays is not None:
            a = g.session.arrays
            if np.isfinite(getattr(a, "e0", float("nan"))):
                e0 = float(a.e0)
            if np.isfinite(getattr(a, "edge_step", float("nan"))):
                edge_step = float(a.edge_step)
    except Exception:
        pass
    if e0 is None and g.settings.e0 is not None:
        try:
            e0 = float(g.settings.e0)
        except Exception:
            e0 = None
    return {"e0": e0, "edge_step": edge_step, "name": g.name}


def _lcf_pca_warnings(
    data_meta: dict,
    std_metas: Sequence[dict],
    *,
    arrayname: str,
    bi: Callable,
) -> List[str]:
    """Soft scientific warnings (normalization / energy alignment)."""
    warns: List[str] = []
    e0s = [m.get("e0") for m in [data_meta, *std_metas] if m.get("e0") is not None]
    if len(e0s) >= 2:
        spread = float(max(e0s) - min(e0s))
        if spread > 0.5:
            warns.append(
                bi(
                    f"E0 分散 {spread:.2f} eV（>0.5）— 建议先对齐/校准能量再 LCF/PCA。",
                    f"E0 spread {spread:.2f} eV (>0.5) — align/calibrate energy before LCF/PCA.",
                )
            )
    all_meta = [data_meta, *list(std_metas)]
    steps = [float(m["edge_step"]) for m in all_meta if m.get("edge_step")]
    if len(steps) >= 2 and str(arrayname).lower() == "norm":
        med = float(np.median(steps))
        if med > 0:
            for m in all_meta:
                s = m.get("edge_step")
                if s is None:
                    continue
                if abs(float(s) - med) / med > 0.08:
                    warns.append(
                        bi(
                            f"edge_step 不一致（{m.get('name')}: {s:.4g} vs median {med:.4g}）— 归一化参数宜统一。",
                            f"edge_step mismatch ({m.get('name')}: {s:.4g} vs median {med:.4g}) — use consistent normalization.",
                        )
                    )
                    break
    if str(arrayname).lower() in ("chi", "kchi", "k2chi", "k3chi"):
        warns.append(
            bi(
                "χ(k) 拟合要求各谱 E0/归一化一致；k 窗请用 Å⁻¹。",
                "χ(k) fit needs consistent E0/normalization; use k-window in Å⁻¹.",
            )
        )
    return warns


def _overlay_from_payload(kind: str, data: dict, title: str) -> dict:
    return {
        "kind": kind,
        "space": data.get("space", "E"),
        "x": data.get("xdata"),
        "ydata": data.get("ydata"),
        "yfit": data.get("yfit"),
        "ycomps": data.get("ycomps") or {},
        "title": title,
    }


class LCFDialog(_AnalysisDialogBase):
    """Pick data + standards + window + constraints; optional combinatorial fitall."""

    def __init__(
        self,
        parent,
        groups: Sequence[GroupRecord],
        *,
        current_id: Optional[str],
        marked_ids: Sequence[str],
        find_group: FindGroup,
        get_xy: GetXY,
        default_window: DefaultWindow,
        bi: Callable,
        default_export_dir: Optional[Path] = None,
    ):
        super().__init__(
            parent,
            bi("线性组合拟合 (LCF)", "Linear combination fit (LCF)"),
            bi,
            default_export_dir=default_export_dir,
        )
        self._kind = "lcf"
        self._groups = list(groups)
        self._find = find_group
        self._get_xy = get_xy
        self._default_window = default_window
        self._rankings: List[dict] = []

        box = QGroupBox(bi("谱选择", "Spectra"))
        form = QFormLayout(box)
        self.cmb_data = QComboBox()
        for g in self._groups:
            self.cmb_data.addItem(g.name, g.id)
        if current_id:
            ix = self.cmb_data.findData(current_id)
            if ix >= 0:
                self.cmb_data.setCurrentIndex(ix)
        form.addRow(bi("数据谱（被拟合）", "Data spectrum (to fit)"), self.cmb_data)

        self.lst_std = QListWidget()
        self.lst_std.setMinimumHeight(110)
        pre = {i for i in marked_ids if i != current_id}
        if not pre and len(self._groups) > 1:
            pre = {g.id for g in self._groups if g.id != current_id}
        _fill_group_list(self.lst_std, self._groups, preselect_ids=pre, multi=True)
        std_tools, self.btn_std_all, self.btn_std_unmark, self.btn_std_invert = _analysis_mark_tools(
            self.lst_std, bi
        )
        form.addRow("", std_tools)
        hint = QLabel(
            bi(
                "勾选标准谱。数据谱若也被勾选会自动排除。",
                "Check standards. Data spectrum is excluded if checked.",
            )
        )
        hint.setWordWrap(True)
        form.addRow(bi("标准谱", "Standards"), self.lst_std)
        form.addRow("", hint)
        self.params_host.addWidget(box)

        pbox = QGroupBox(bi("拟合参数", "Fit parameters"))
        pf = QFormLayout(pbox)
        self.cmb_array = QComboBox()
        for key, lab in (
            ("norm", bi("归一化 μ (norm)", "Normalized μ (norm)")),
            ("mu", bi("原始 μ", "Raw μ")),
            ("dmude", bi("dμ/dE", "dμ/dE")),
            ("chi", bi("χ(k)", "χ(k)")),
        ):
            self.cmb_array.addItem(lab, key)
        pf.addRow(bi("拟合数组", "Array"), self.cmb_array)

        self.sp_emin, self.sp_emax = _energy_spins(bi)
        self.lbl_emin = QLabel(bi("E min (eV)", "E min (eV)"))
        self.lbl_emax = QLabel(bi("E max (eV)", "E max (eV)"))
        self.cmb_data.currentIndexChanged.connect(self._seed_window_from_data)
        self.cmb_array.currentIndexChanged.connect(self._on_array_changed)
        pf.addRow(self.lbl_emin, self.sp_emin)
        pf.addRow(self.lbl_emax, self.sp_emax)

        self.chk_sum1 = QCheckBox(bi("权重之和 = 1", "Weights sum to 1"))
        self.chk_sum1.setChecked(True)
        self.chk_vary_e0 = QCheckBox(bi("拟合时微调数据 e0", "Vary data e0 in fit"))
        self.chk_vary_e0.setChecked(False)
        self.sp_minw = QDoubleSpinBox()
        self.sp_minw.setRange(0.0, 1.0)
        self.sp_minw.setDecimals(4)
        self.sp_minw.setSingleStep(0.01)
        self.sp_minw.setValue(0.0)
        self.sp_minw.setToolTip(bi("每个标准谱权重下限（常用 0）", "Min weight per standard (often 0)"))
        self.chk_fitall = QCheckBox(
            bi("穷举组合 (fitall，按 redχ² 排序)", "Try all combinations (fitall, rank by redχ²)")
        )
        self.chk_fitall.setChecked(False)
        self.chk_fitall.setToolTip(
            bi(
                "对 ≥2 个标准做组合搜索（Athena 风格），输出 Top-N；点列表可切换叠图。",
                "Combinatorial search over ≥2 standards (Athena-style); click a rank to re-overlay.",
            )
        )
        self.sp_max_ncomps = QSpinBox()
        self.sp_max_ncomps.setRange(2, 12)
        self.sp_max_ncomps.setValue(4)
        self.sp_max_ncomps.setToolTip(bi("穷举时最多同时用几个标准", "Max standards per combination"))
        pf.addRow(self.chk_sum1)
        pf.addRow(self.chk_vary_e0)
        pf.addRow(bi("最小权重", "Min weight"), self.sp_minw)
        pf.addRow(self.chk_fitall)
        pf.addRow(bi("max ncomps", "max ncomps"), self.sp_max_ncomps)
        self.params_host.addWidget(pbox)

        rbox = QGroupBox(bi("组合结果（点选叠图）", "Combinations (click to overlay)"))
        rv = QVBoxLayout(rbox)
        self.lst_rank = QListWidget()
        self.lst_rank.setMinimumHeight(80)
        self.lst_rank.currentRowChanged.connect(self._on_rank_selected)
        rv.addWidget(self.lst_rank)
        self.params_host.addWidget(rbox)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color:#8a5a00; font-size:11px;")
        self.params_host.addWidget(self.lbl_warn)

        self._seed_window_from_data()

    def _on_array_changed(self) -> None:
        arr = str(self.cmb_array.currentData() or "norm")
        if arr == "chi":
            self.lbl_emin.setText(self._bi("k min (Å⁻¹)", "k min (Å⁻¹)"))
            self.lbl_emax.setText(self._bi("k max (Å⁻¹)", "k max (Å⁻¹)"))
            self.chk_vary_e0.setEnabled(False)
            # default k window
            if self.sp_emin.value() > 50 or self.sp_emax.value() > 100:
                self.sp_emin.setValue(2.5)
                self.sp_emax.setValue(10.0)
        else:
            self.lbl_emin.setText(self._bi("E min (eV)", "E min (eV)"))
            self.lbl_emax.setText(self._bi("E max (eV)", "E max (eV)"))
            self.chk_vary_e0.setEnabled(True)
            self._seed_window_from_data()

    def _seed_window_from_data(self) -> None:
        if str(self.cmb_array.currentData() or "norm") == "chi":
            return
        g0 = self._find(str(self.cmb_data.currentData() or ""))
        if g0 is None:
            return
        lo, hi = self._default_window(g0)
        if lo is not None:
            self.sp_emin.setValue(float(lo))
        if hi is not None:
            self.sp_emax.setValue(float(hi))

    def _on_rank_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._rankings):
            return
        data = dict(self._rankings[row])
        data["rankings"] = self._rankings
        data["engine"] = "larch.math.lincombo_fitall"
        report = format_lcf_report(data)
        gname = str(self.cmb_data.currentText() or "data")
        report = f"data: {gname}\n\n" + report
        ov = _overlay_from_payload("lcf", data, f"LCF · {gname} #{row + 1}")
        self._set_success(report, data, ov)

    def _on_run(self) -> None:
        data_id = str(self.cmb_data.currentData() or "")
        gdat = self._find(data_id)
        if gdat is None:
            QMessageBox.warning(self, self.windowTitle(), self._bi("请选择数据谱", "Pick a data spectrum"))
            return
        std_ids = [i for i in _checked_ids(self.lst_std) if i != data_id]
        if not std_ids:
            QMessageBox.warning(
                self,
                self.windowTitle(),
                self._bi("请至少选择一个标准谱（与数据谱不同）", "Select at least one standard (≠ data)"),
            )
            return
        arrayname = str(self.cmb_array.currentData() or "norm")
        e, y = self._get_xy(gdat, arrayname)
        if e is None or y is None:
            QMessageBox.warning(
                self,
                self.windowTitle(),
                self._bi("数据谱无可用曲线（χ 需先做背景扣除）", "No usable data curve (χ needs background first)"),
            )
            return
        stds = []
        std_metas = []
        for sid in std_ids:
            gs = self._find(sid)
            if gs is None:
                continue
            es, ys = self._get_xy(gs, arrayname)
            if es is None or ys is None:
                continue
            stds.append((gs.name, es, ys))
            std_metas.append(_group_meta(gs))
        if not stds:
            QMessageBox.warning(self, self.windowTitle(), self._bi("标准谱均无可用曲线", "No usable standard curves"))
            return

        warns = _lcf_pca_warnings(_group_meta(gdat), std_metas, arrayname=arrayname, bi=self._bi)
        self.lbl_warn.setText("\n".join(warns))

        xmin = float(self.sp_emin.value())
        xmax = float(self.sp_emax.value())
        kw = dict(
            arrayname=arrayname,
            xmin=xmin,
            xmax=xmax,
            sum_to_one=self.chk_sum1.isChecked(),
            vary_e0=self.chk_vary_e0.isChecked() and arrayname != "chi",
            min_weight=float(self.sp_minw.value()),
            data_name=gdat.name,
        )
        self._rankings = []
        self.lst_rank.clear()
        if self.chk_fitall.isChecked() and len(stds) >= 2:
            res = lincombo_fitall(
                e,
                y,
                stds,
                max_ncomps=int(self.sp_max_ncomps.value()),
                max_output=16,
                **kw,
            )
        else:
            res = lincombo_fit(e, y, stds, **kw)
        if not res.ok:
            QMessageBox.warning(self, self.windowTitle(), res.message)
            self.txt_report.setPlainText(res.message)
            return
        data = res.data if isinstance(res.data, dict) else {}
        data["data_name"] = gdat.name
        data["standard_names"] = [n for n, _, _ in stds]
        data["warnings"] = warns
        rankings = data.get("rankings") or []
        if rankings:
            self._rankings = rankings
            for row in rankings:
                w = row.get("weights") or {}
                wtxt = ", ".join(f"{k}={float(v):.3f}" for k, v in w.items())
                self.lst_rank.addItem(
                    f"#{int(row.get('rank', 0)):02d}  redχ²={float(row.get('redchi', float('nan'))):.5g}  {wtxt}"
                )
            self.lst_rank.setCurrentRow(0)
            # selection handler already sets success; if not, fall through
            if self._last_payload:
                return
        report = format_lcf_report(data)
        report = f"data: {gdat.name}\nstandards: {', '.join(n for n, _, _ in stds)}\n\n" + report
        ov = _overlay_from_payload("lcf", data, f"LCF · {gdat.name}")
        self._set_success(report, data, ov)


class PCADialog(_AnalysisDialogBase):
    """Training set + ncomp (IND/nsig) + optional Target Transform SPOIL."""

    def __init__(
        self,
        parent,
        groups: Sequence[GroupRecord],
        *,
        current_id: Optional[str],
        marked_ids: Sequence[str],
        find_group: FindGroup,
        get_xy: GetXY,
        default_window: DefaultWindow,
        bi: Callable,
        default_export_dir: Optional[Path] = None,
    ):
        super().__init__(
            parent,
            bi("主成分分析 (PCA)", "Principal component analysis (PCA)"),
            bi,
            default_export_dir=default_export_dir,
        )
        self._kind = "pca"
        self._groups = list(groups)
        self._find = find_group
        self._get_xy = get_xy
        self._default_window = default_window
        self._last_suggested = None

        box = QGroupBox(bi("谱选择", "Spectra"))
        form = QFormLayout(box)
        self.lst = QListWidget()
        self.lst.setMinimumHeight(110)
        pre = set(marked_ids) if marked_ids else {g.id for g in self._groups}
        _fill_group_list(self.lst, self._groups, preselect_ids=pre, multi=True)
        train_tools, self.btn_train_all, self.btn_train_unmark, self.btn_train_invert = _analysis_mark_tools(
            self.lst, bi
        )
        form.addRow("", train_tools)
        form.addRow(bi("训练谱（勾选 ≥2）", "Training set (check ≥2)"), self.lst)
        self.cmb_target = QComboBox()
        self._rebuild_target()
        self.lst.itemChanged.connect(lambda *_: self._rebuild_target())
        if current_id:
            ix = self.cmb_target.findData(current_id)
            if ix >= 0:
                self.cmb_target.setCurrentIndex(ix)
        form.addRow(bi("拟合目标（叠图）", "Fit target (plot)"), self.cmb_target)

        self.lst_tt = QListWidget()
        self.lst_tt.setMinimumHeight(70)
        # TT candidates: all groups not forced; default none checked
        _fill_group_list(self.lst_tt, self._groups, preselect_ids=set(), multi=True)
        tt_tools, self.btn_tt_all, self.btn_tt_unmark, self.btn_tt_invert = _analysis_mark_tools(
            self.lst_tt, bi
        )
        form.addRow("", tt_tools)
        form.addRow(
            bi("Target Transform 候选（可选）", "Target Transform candidates (optional)"),
            self.lst_tt,
        )
        tt_hint = QLabel(
            bi(
                "对候选标准做 Target Transform，输出 SPOIL（<3 优，3–6 可，>6 差）。",
                "Target-transform candidates → SPOIL (<3 excellent, 3–6 ok, >6 poor).",
            )
        )
        tt_hint.setWordWrap(True)
        form.addRow("", tt_hint)
        self.params_host.addWidget(box)

        pbox = QGroupBox(bi("拟合参数", "Fit parameters"))
        pf = QFormLayout(pbox)
        self.cmb_array = QComboBox()
        for key, lab in (
            ("norm", bi("归一化 μ (norm)", "Normalized μ (norm)")),
            ("mu", bi("原始 μ", "Raw μ")),
            ("dmude", bi("dμ/dE", "dμ/dE")),
            ("chi", bi("χ(k)", "χ(k)")),
        ):
            self.cmb_array.addItem(lab, key)
        self.sp_ncomp = QSpinBox()
        self.sp_ncomp.setRange(1, 20)
        self.sp_ncomp.setValue(min(3, max(1, len(pre))))
        self.btn_suggest = QPushButton(bi("用 nsig/IND 建议", "Suggest from nsig/IND"))
        self.btn_suggest.setObjectName("ghostBtn")
        self.btn_suggest.setToolTip(
            bi(
                "先用当前训练集跑一次 PCA train，把 ncomp 设为 Malinowski nsig（IND 最小）。",
                "Run a quick train and set ncomp to Malinowski nsig (IND minimum).",
            )
        )
        self.btn_suggest.clicked.connect(self._suggest_ncomp)
        self.sp_emin, self.sp_emax = _energy_spins(bi)
        self.lbl_emin = QLabel(bi("E min (eV)", "E min (eV)"))
        self.lbl_emax = QLabel(bi("E max (eV)", "E max (eV)"))
        self.cmb_array.currentIndexChanged.connect(self._on_array_changed)
        self.chk_show_pcs = QCheckBox(bi("叠图显示抽象 PC", "Overlay abstract PCs"))
        self.chk_show_pcs.setChecked(True)
        pf.addRow(bi("数组", "Array"), self.cmb_array)
        nrow = QHBoxLayout()
        nrow.addWidget(self.sp_ncomp)
        nrow.addWidget(self.btn_suggest)
        pf.addRow(bi("成分数 ncomp", "Components ncomp"), nrow)
        pf.addRow(self.lbl_emin, self.sp_emin)
        pf.addRow(self.lbl_emax, self.sp_emax)
        pf.addRow(self.chk_show_pcs)
        self.params_host.addWidget(pbox)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color:#8a5a00; font-size:11px;")
        self.params_host.addWidget(self.lbl_warn)

        g0 = find_group(current_id or (self._groups[0].id if self._groups else ""))
        if g0 is not None:
            lo, hi = default_window(g0)
            if lo is not None:
                self.sp_emin.setValue(float(lo))
            if hi is not None:
                self.sp_emax.setValue(float(hi))

    def _on_array_changed(self) -> None:
        arr = str(self.cmb_array.currentData() or "norm")
        if arr == "chi":
            self.lbl_emin.setText(self._bi("k min (Å⁻¹)", "k min (Å⁻¹)"))
            self.lbl_emax.setText(self._bi("k max (Å⁻¹)", "k max (Å⁻¹)"))
            if self.sp_emin.value() > 50:
                self.sp_emin.setValue(2.5)
                self.sp_emax.setValue(10.0)
        else:
            self.lbl_emin.setText(self._bi("E min (eV)", "E min (eV)"))
            self.lbl_emax.setText(self._bi("E max (eV)", "E max (eV)"))

    def _rebuild_target(self) -> None:
        cur = self.cmb_target.currentData()
        self.cmb_target.blockSignals(True)
        self.cmb_target.clear()
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if it is None or not bool(it.data(Qt.ItemDataRole.UserRole + 1)):
                continue
            gid = it.data(Qt.ItemDataRole.UserRole)
            self.cmb_target.addItem(it.text(), gid)
        if cur is not None:
            ix = self.cmb_target.findData(cur)
            if ix >= 0:
                self.cmb_target.setCurrentIndex(ix)
        self.cmb_target.blockSignals(False)

    def _collect_specs(self, ids: Sequence[str], arrayname: str):
        specs = []
        metas = []
        for sid in ids:
            g = self._find(sid)
            if g is None:
                continue
            e, y = self._get_xy(g, arrayname)
            if e is None or y is None:
                continue
            specs.append((g.name, e, y))
            metas.append(_group_meta(g))
        return specs, metas

    def _suggest_ncomp(self) -> None:
        ids = _checked_ids(self.lst)
        if len(ids) < 2:
            QMessageBox.information(
                self,
                self.windowTitle(),
                self._bi("请先勾选 ≥2 条训练谱", "Check ≥2 training spectra first"),
            )
            return
        arrayname = str(self.cmb_array.currentData() or "norm")
        specs, _ = self._collect_specs(ids, arrayname)
        if len(specs) < 2:
            return
        res = pca_train_fit(
            specs,
            ncomp=None,
            arrayname=arrayname,
            xmin=float(self.sp_emin.value()),
            xmax=float(self.sp_emax.value()),
        )
        if not res.ok or not isinstance(res.data, dict):
            QMessageBox.warning(self, self.windowTitle(), res.message)
            return
        sug = int(res.data.get("suggested_ncomp") or res.data.get("nsig") or 1)
        self.sp_ncomp.setValue(max(1, sug))
        self._last_suggested = sug
        ind = res.data.get("ind") or []
        msg = self._bi(
            f"建议 ncomp = {sug}（nsig / IND 最小处）。IND 前几项: "
            + ", ".join(f"{float(v):.3g}" for v in ind[: min(5, len(ind))]),
            f"Suggested ncomp = {sug} (nsig / IND min). IND head: "
            + ", ".join(f"{float(v):.3g}" for v in ind[: min(5, len(ind))]),
        )
        self.lbl_warn.setText(msg)

    def _on_run(self) -> None:
        ids = _checked_ids(self.lst)
        if len(ids) < 2:
            QMessageBox.warning(
                self,
                self.windowTitle(),
                self._bi("请至少选择 2 条训练谱", "Select at least 2 training spectra"),
            )
            return
        arrayname = str(self.cmb_array.currentData() or "norm")
        specs, metas = self._collect_specs(ids, arrayname)
        if len(specs) < 2:
            QMessageBox.warning(self, self.windowTitle(), self._bi("可用谱不足 2 条", "Fewer than 2 usable spectra"))
            return
        tid = str(self.cmb_target.currentData() or "")
        tname = self.cmb_target.currentText() or specs[0][0]
        tg = self._find(tid)
        if tg is not None:
            tname = tg.name

        train_ids = set(ids)
        tt_ids = [i for i in _checked_ids(self.lst_tt) if i not in train_ids or True]
        # Allow TT on any checked including training set members (common for library check)
        tt_specs, _ = self._collect_specs(_checked_ids(self.lst_tt), arrayname)

        warns = _lcf_pca_warnings(metas[0] if metas else {}, metas[1:], arrayname=arrayname, bi=self._bi)
        self.lbl_warn.setText("\n".join(warns))

        res = pca_train_fit(
            specs,
            ncomp=int(self.sp_ncomp.value()),
            arrayname=arrayname,
            xmin=float(self.sp_emin.value()),
            xmax=float(self.sp_emax.value()),
            fit_target=tname,
            tt_spectra=tt_specs or None,
        )
        if not res.ok:
            QMessageBox.warning(self, self.windowTitle(), res.message)
            self.txt_report.setPlainText(res.message)
            return
        data = res.data if isinstance(res.data, dict) else {}
        data["warnings"] = warns
        # After run, sync ncomp spin if user left auto-ish
        if data.get("suggested_ncomp") and self._last_suggested is None:
            pass
        report = format_pca_report(data)
        ycomps = data.get("ycomps") or {}
        if not self.chk_show_pcs.isChecked():
            ycomps = {}
        else:
            # don't drown fit with mean unless wanted — keep PCs only for overlay
            ycomps = {k: v for k, v in ycomps.items() if str(k).startswith("PC")}
        ov = {
            "kind": "pca",
            "space": data.get("space", "E"),
            "x": data.get("xdata"),
            "ydata": data.get("ydata"),
            "yfit": data.get("yfit"),
            "ycomps": ycomps,
            "title": f"PCA · {data.get('target', tname)}",
        }
        # If a TT candidate was ranked best with good SPOIL, allow residual info in report only
        self._set_success(report, data, ov)


class MCRDialog(_AnalysisDialogBase):
    """MCR-ALS (pyMCR) on a multi-spectrum series — sits under PCA in Analysis.

    Full chain: common grid → ncomp (PCA nsig suggest) → ALS with non-neg /
    closure → report + pure overlay + export + write pure spectra to Groups.
    """

    def __init__(
        self,
        parent,
        groups: Sequence[GroupRecord],
        *,
        current_id: Optional[str],
        marked_ids: Sequence[str],
        find_group: FindGroup,
        get_xy: GetXY,
        default_window: DefaultWindow,
        bi: Callable,
        default_export_dir: Optional[Path] = None,
    ):
        super().__init__(
            parent,
            bi("多元曲线分辨 (MCR-ALS)", "Multivariate curve resolution (MCR-ALS)"),
            bi,
            default_export_dir=default_export_dir,
        )
        self._kind = "mcr"
        self._groups = list(groups)
        self._find = find_group
        self._get_xy = get_xy
        self._default_window = default_window
        self.btn_write_groups.setVisible(True)
        self.btn_write_groups.setToolTip(
            bi(
                "将解出的纯相谱 S 写入左侧 Groups（派生组，便于后续 LCF / 拟合）",
                "Write resolved pure spectra S into Groups (derived; for LCF / fit)",
            )
        )

        box = QGroupBox(bi("谱选择（系列）", "Spectra (series)"))
        form = QFormLayout(box)
        self.lst = QListWidget()
        self.lst.setMinimumHeight(120)
        pre = set(marked_ids) if marked_ids else {g.id for g in self._groups}
        _fill_group_list(self.lst, self._groups, preselect_ids=pre, multi=True)
        series_tools, self.btn_series_all, self.btn_series_unmark, self.btn_series_invert = _analysis_mark_tools(
            self.lst, bi
        )
        form.addRow("", series_tools)
        form.addRow(bi("参与分解的谱（≥2）", "Series spectra (≥2)"), self.lst)
        hint = QLabel(
            bi(
                "原位/连续系列最佳。先 PCA 估成分数，再 MCR 解耦未知纯相与浓度。",
                "Best for in situ / continuous series. Use PCA for rank, then MCR for pure spectra + C.",
            )
        )
        hint.setWordWrap(True)
        form.addRow("", hint)
        self.params_host.addWidget(box)

        pbox = QGroupBox(bi("拟合参数", "Fit parameters"))
        pf = QFormLayout(pbox)
        self.cmb_array = QComboBox()
        for key, lab in (
            ("norm", bi("归一化 μ (norm)", "Normalized μ (norm)")),
            ("mu", bi("原始 μ", "Raw μ")),
            ("dmude", bi("dμ/dE", "dμ/dE")),
            ("chi", bi("χ(k)", "χ(k)")),
        ):
            self.cmb_array.addItem(lab, key)

        self.sp_ncomp = QSpinBox()
        self.sp_ncomp.setRange(1, 20)
        self.sp_ncomp.setValue(min(2, max(1, len(pre))))
        self.btn_suggest = QPushButton(bi("用 PCA nsig/IND 建议", "Suggest via PCA nsig/IND"))
        self.btn_suggest.setObjectName("ghostBtn")
        self.btn_suggest.setToolTip(
            bi(
                "对当前勾选谱跑 PCA train，把 ncomp 设为 Malinowski nsig（IND 最小）。",
                "Run PCA train on checked spectra; set ncomp to Malinowski nsig (IND min).",
            )
        )
        self.btn_suggest.clicked.connect(self._suggest_ncomp)

        self.sp_emin, self.sp_emax = _energy_spins(bi)
        self.lbl_emin = QLabel(bi("E min (eV)", "E min (eV)"))
        self.lbl_emax = QLabel(bi("E max (eV)", "E max (eV)"))
        self.cmb_array.currentIndexChanged.connect(self._on_array_changed)

        self.chk_c_nonneg = QCheckBox(bi("浓度非负 C≥0", "C non-negative"))
        self.chk_c_nonneg.setChecked(True)
        self.chk_c_closure = QCheckBox(bi("浓度闭合 ΣC=1", "C closure (sum=1)"))
        self.chk_c_closure.setChecked(True)
        self.chk_st_nonneg = QCheckBox(bi("光谱非负 S≥0", "S non-negative"))
        self.chk_st_nonneg.setChecked(True)
        self.cmb_array.currentIndexChanged.connect(lambda *_: _sync_mcr_st_nonneg(self))

        self.cmb_c_regr = QComboBox()
        self.cmb_c_regr.addItem("OLS", "OLS")
        self.cmb_c_regr.addItem("NNLS", "NNLS")
        self.cmb_st_regr = QComboBox()
        self.cmb_st_regr.addItem("OLS", "OLS")
        self.cmb_st_regr.addItem("NNLS", "NNLS")

        self.cmb_init = QComboBox()
        self.cmb_init.addItem(bi("SVD 初始化", "SVD init"), "svd")
        self.cmb_init.addItem(bi("随机非负", "Random non-neg"), "random")

        self.sp_max_iter = QSpinBox()
        self.sp_max_iter.setRange(10, 2000)
        self.sp_max_iter.setValue(100)
        self.sp_tol = QDoubleSpinBox()
        self.sp_tol.setDecimals(10)
        self.sp_tol.setRange(1e-16, 1.0)
        self.sp_tol.setValue(1e-8)
        self.sp_tol.setSingleStep(1e-8)

        self.chk_show_pures = QCheckBox(bi("叠图显示纯相谱", "Overlay pure spectra"))
        self.chk_show_pures.setChecked(True)

        pf.addRow(bi("数组", "Array"), self.cmb_array)
        nrow = QHBoxLayout()
        nrow.addWidget(self.sp_ncomp)
        nrow.addWidget(self.btn_suggest)
        pf.addRow(bi("成分数 ncomp", "Components ncomp"), nrow)
        pf.addRow(self.lbl_emin, self.sp_emin)
        pf.addRow(self.lbl_emax, self.sp_emax)
        pf.addRow(self.chk_c_nonneg)
        pf.addRow(self.chk_c_closure)
        pf.addRow(self.chk_st_nonneg)
        pf.addRow(bi("C 回归器", "C regressor"), self.cmb_c_regr)
        pf.addRow(bi("S 回归器", "S regressor"), self.cmb_st_regr)
        pf.addRow(bi("初始化", "Initialization"), self.cmb_init)
        pf.addRow(bi("最大迭代", "Max iterations"), self.sp_max_iter)
        pf.addRow(bi("收敛 tol", "tol_err_change"), self.sp_tol)
        pf.addRow(self.chk_show_pures)
        self.params_host.addWidget(pbox)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color:#8a5a00; font-size:11px;")
        self.params_host.addWidget(self.lbl_warn)

        g0 = find_group(current_id or (self._groups[0].id if self._groups else ""))
        if g0 is not None:
            lo, hi = default_window(g0)
            if lo is not None:
                self.sp_emin.setValue(float(lo))
            if hi is not None:
                self.sp_emax.setValue(float(hi))

    def _on_array_changed(self) -> None:
        arr = str(self.cmb_array.currentData() or "norm")
        if arr == "chi":
            self.lbl_emin.setText(self._bi("k min (Å⁻¹)", "k min (Å⁻¹)"))
            self.lbl_emax.setText(self._bi("k max (Å⁻¹)", "k max (Å⁻¹)"))
            if self.sp_emin.value() > 50:
                self.sp_emin.setValue(2.5)
                self.sp_emax.setValue(10.0)
        else:
            self.lbl_emin.setText(self._bi("E min (eV)", "E min (eV)"))
            self.lbl_emax.setText(self._bi("E max (eV)", "E max (eV)"))

    def _collect_specs(self, ids: Sequence[str], arrayname: str):
        specs = []
        metas = []
        for sid in ids:
            g = self._find(sid)
            if g is None:
                continue
            e, y = self._get_xy(g, arrayname)
            if e is None or y is None:
                continue
            specs.append((g.name, e, y))
            metas.append(_group_meta(g))
        return specs, metas

    def _suggest_ncomp(self) -> None:
        ids = _checked_ids(self.lst)
        if len(ids) < 2:
            QMessageBox.information(
                self,
                self.windowTitle(),
                self._bi("请先勾选 ≥2 条谱", "Check ≥2 spectra first"),
            )
            return
        arrayname = str(self.cmb_array.currentData() or "norm")
        specs, _ = self._collect_specs(ids, arrayname)
        if len(specs) < 2:
            return
        res = pca_train_fit(
            specs,
            ncomp=None,
            arrayname=arrayname,
            xmin=float(self.sp_emin.value()),
            xmax=float(self.sp_emax.value()),
        )
        if not res.ok or not isinstance(res.data, dict):
            QMessageBox.warning(self, self.windowTitle(), res.message)
            return
        sug = int(res.data.get("suggested_ncomp") or res.data.get("nsig") or 1)
        self.sp_ncomp.setValue(max(1, sug))
        ind = res.data.get("ind") or []
        self.lbl_warn.setText(
            self._bi(
                f"PCA 建议 ncomp = {sug}（nsig / IND 最小）。IND: "
                + ", ".join(f"{float(v):.3g}" for v in ind[: min(5, len(ind))]),
                f"PCA suggested ncomp = {sug} (nsig / IND min). IND: "
                + ", ".join(f"{float(v):.3g}" for v in ind[: min(5, len(ind))]),
            )
        )

    def _on_run(self) -> None:
        ids = _checked_ids(self.lst)
        if len(ids) < 2:
            QMessageBox.warning(
                self,
                self.windowTitle(),
                self._bi("请至少选择 2 条谱", "Select at least 2 spectra"),
            )
            return
        arrayname = str(self.cmb_array.currentData() or "norm")
        specs, metas = self._collect_specs(ids, arrayname)
        if len(specs) < 2:
            QMessageBox.warning(
                self, self.windowTitle(), self._bi("可用谱不足 2 条", "Fewer than 2 usable spectra")
            )
            return

        warns = _lcf_pca_warnings(
            metas[0] if metas else {}, metas[1:], arrayname=arrayname, bi=self._bi
        )
        # MCR-specific tip
        warns.append(
            self._bi(
                "旋转模糊性：务必保留非负/闭合约束；归一化质量直接影响纯相谱。",
                "Rotational ambiguity: keep non-neg/closure; poor norm propagates into pure spectra.",
            )
        )
        self.lbl_warn.setText("\n".join(warns))

        res = mcr_als_fit(
            specs,
            ncomp=int(self.sp_ncomp.value()),
            arrayname=arrayname,
            xmin=float(self.sp_emin.value()),
            xmax=float(self.sp_emax.value()),
            c_nonneg=self.chk_c_nonneg.isChecked(),
            c_closure=self.chk_c_closure.isChecked(),
            st_nonneg=self.chk_st_nonneg.isChecked(),
            c_regr=str(self.cmb_c_regr.currentData() or "OLS"),
            st_regr=str(self.cmb_st_regr.currentData() or "OLS"),
            max_iter=int(self.sp_max_iter.value()),
            tol_err_change=float(self.sp_tol.value()),
            init=str(self.cmb_init.currentData() or "svd"),
        )
        if not res.ok:
            QMessageBox.warning(self, self.windowTitle(), res.message)
            self.txt_report.setPlainText(res.message)
            return
        data = res.data if isinstance(res.data, dict) else {}
        data["warnings"] = warns
        report = format_mcr_report(data)
        ycomps = data.get("ycomps") or {}
        if not self.chk_show_pures.isChecked():
            ycomps = {}
        ov = {
            "kind": "mcr",
            "space": data.get("space", "E"),
            "x": data.get("xdata"),
            "ydata": data.get("ydata"),
            "yfit": data.get("yfit"),
            "ycomps": ycomps,
            "title": f"MCR-ALS · ncomp={data.get('ncomp')}",
        }
        self._set_success(report, data, ov)


class PrepeakDialog(_AnalysisDialogBase):
    """Pick spectrum + windows + peak model options."""

    def __init__(
        self,
        parent,
        groups: Sequence[GroupRecord],
        *,
        current_id: Optional[str],
        find_group: FindGroup,
        bi: Callable,
        default_export_dir: Optional[Path] = None,
    ):
        super().__init__(
            parent,
            bi("边前峰拟合", "Pre-edge peak fit"),
            bi,
            default_export_dir=default_export_dir,
        )
        self._kind = "prepeak"
        self._groups = list(groups)
        self._find = find_group

        box = QGroupBox(bi("谱与窗口", "Spectrum & windows"))
        form = QFormLayout(box)
        self.cmb = QComboBox()
        for g in self._groups:
            self.cmb.addItem(g.name, g.id)
        if current_id:
            ix = self.cmb.findData(current_id)
            if ix >= 0:
                self.cmb.setCurrentIndex(ix)
        form.addRow(bi("数据谱", "Spectrum"), self.cmb)

        self.sp_e0 = QDoubleSpinBox()
        self.sp_e0.setDecimals(2)
        self.sp_e0.setRange(0, 1e6)
        self.sp_e0.setSingleStep(0.1)
        self.chk_auto_win = QCheckBox(
            bi("使用稳健自动窗口（相对 E0）", "Use robust auto windows (rel. E0)")
        )
        self.chk_auto_win.setToolTip(
            bi(
                "基线 E0−50 至 E0−1 eV；峰区 E0−15 至 E0−5 eV，并裁剪到数据范围。",
                "Baseline E0−50 to E0−1 eV; peak region E0−15 to E0−5 eV, clipped to the scan.",
            )
        )
        self.chk_auto_win.setChecked(True)
        self.sp_emin = QDoubleSpinBox()
        self.sp_emax = QDoubleSpinBox()
        self.sp_elo = QDoubleSpinBox()
        self.sp_ehi = QDoubleSpinBox()
        for sp in (self.sp_emin, self.sp_emax, self.sp_elo, self.sp_ehi):
            sp.setDecimals(2)
            sp.setRange(-1e6, 1e6)
            sp.setEnabled(False)
        self.chk_auto_win.toggled.connect(self._toggle_win)
        form.addRow(bi("E0 (eV)", "E0 (eV)"), self.sp_e0)
        form.addRow(self.chk_auto_win)
        form.addRow(bi("baseline emin", "baseline emin"), self.sp_emin)
        form.addRow(bi("baseline emax", "baseline emax"), self.sp_emax)
        form.addRow(bi("peak elo", "peak elo"), self.sp_elo)
        form.addRow(bi("peak ehi", "peak ehi"), self.sp_ehi)
        self.params_host.addWidget(box)

        mbox = QGroupBox(bi("峰模型", "Peak model"))
        mf = QFormLayout(mbox)
        self.sp_npeaks = QSpinBox()
        self.sp_npeaks.setRange(1, 3)
        self.sp_npeaks.setValue(2)
        self.cmb_shape = QComboBox()
        for key, lab in (
            ("gaussian", bi("高斯 Gaussian", "Gaussian")),
            ("lorentzian", bi("洛伦兹 Lorentzian", "Lorentzian")),
            ("pseudovoigt", bi("伪 Voigt Pseudo-Voigt", "Pseudo-Voigt")),
        ):
            self.cmb_shape.addItem(lab, key)
        self.cmb_baseline = QComboBox()
        for key, lab in (
            ("linear", bi("线性 Linear", "Linear")),
            ("linear+arctan", bi("线性 + Arctan 台阶", "Linear + Arctan step")),
        ):
            self.cmb_baseline.addItem(lab, key)
        self.chk_couple = QCheckBox(bi("峰宽耦合（共享 σ）", "Couple peak widths (shared σ)"))
        self.chk_couple.setChecked(False)
        mf.addRow(bi("峰个数", "Number of peaks"), self.sp_npeaks)
        mf.addRow(bi("峰型", "Peak shape"), self.cmb_shape)
        mf.addRow(bi("基线", "Baseline"), self.cmb_baseline)
        mf.addRow(self.chk_couple)
        self.params_host.addWidget(mbox)

        self.cmb.currentIndexChanged.connect(self._seed_e0)
        self._seed_e0()

    def _toggle_win(self, on: bool) -> None:
        for sp in (self.sp_emin, self.sp_emax, self.sp_elo, self.sp_ehi):
            sp.setEnabled(not on)

    def _seed_e0(self) -> None:
        g = self._find(str(self.cmb.currentData() or ""))
        if g is None:
            return
        e0 = None
        try:
            g.process()
            if g.session and g.session.arrays is not None:
                e0 = float(g.session.arrays.e0)
        except Exception:
            e0 = None
        if e0 is None or not np.isfinite(e0):
            if g.settings.e0 is not None:
                e0 = float(g.settings.e0)
        if e0 is not None and np.isfinite(e0):
            self.sp_e0.setValue(e0)
            self.sp_emin.setValue(e0 - 50)
            self.sp_emax.setValue(e0 - 1)
            self.sp_elo.setValue(e0 - 15)
            self.sp_ehi.setValue(e0 - 5)

    def _on_run(self) -> None:
        g = self._find(str(self.cmb.currentData() or ""))
        if g is None or g.energy is None or g.mu is None:
            QMessageBox.warning(self, self.windowTitle(), self._bi("请选择有 μ(E) 的谱", "Pick a spectrum with μ(E)"))
            return
        e0 = float(self.sp_e0.value())
        e = np.asarray(g.energy, dtype=float) + float(g.energy_shift or 0.0)
        mu = np.asarray(g.mu, dtype=float)
        kw: dict = {
            "n_peaks": int(self.sp_npeaks.value()),
            "peak_shape": str(self.cmb_shape.currentData() or "gaussian"),
            "baseline": str(self.cmb_baseline.currentData() or "linear"),
            "couple_widths": self.chk_couple.isChecked(),
        }
        if not self.chk_auto_win.isChecked():
            kw.update(
                {
                    "emin": float(self.sp_emin.value()),
                    "emax": float(self.sp_emax.value()),
                    "elo": float(self.sp_elo.value()),
                    "ehi": float(self.sp_ehi.value()),
                }
            )
        res = prepeak_fit(e, mu, e0, **kw)
        if not res.ok:
            QMessageBox.warning(self, self.windowTitle(), res.message)
            self.txt_report.setPlainText(res.message)
            return
        data = res.data if isinstance(res.data, dict) else {}
        data["spectrum"] = g.name
        report = format_prepeak_report(data)
        report = f"spectrum: {g.name}\n\n" + report
        ov = _overlay_from_payload("prepeak", data, f"Pre-edge · {g.name}")
        self._set_success(report, data, ov)


def _wt_spin(decimals: int, lo: float, hi: float, step: float, tip: str = "") -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setDecimals(decimals)
    sp.setRange(lo, hi)
    sp.setSingleStep(step)
    if tip:
        sp.setToolTip(tip)
    return sp


def _wt_has_chi(g: GroupRecord) -> bool:
    """Cheap "already processed χ(k)?" probe — the ≥5-point rule of the host's
    ``_analysis_get_xy(g, "chi")`` without paying for ``g.process()``.

    Seeding the dialog must not reprocess the whole project on open: one
    ``g.process()`` costs ~1 s and the dialog is modal, so an unbounded scan
    would freeze the UI before the first paint.
    """
    try:
        arr = g.session.arrays if getattr(g, "session", None) is not None else None
        k = getattr(arr, "k", None)
        chi = getattr(arr, "chi", None)
        if k is None or chi is None:
            return False
        return min(len(np.asarray(k).ravel()), len(np.asarray(chi).ravel())) >= 5
    except Exception:
        return False


class WaveletDialog(_AnalysisDialogBase):
    """χ(k) continuous wavelet transform (Morlet / Cauchy; kernel: ``wavelet.py``).

    One shared parameter set is applied to every checked group; the k window is
    seeded from the data (kmin/kmax/dk are never persisted — each dataset has its
    own range). A group whose χ(k) is missing, or whose k range does not cover the
    window, is skipped and listed in the report rather than silently dropped.

    The dialog is modal, so the heatmap lives INSIDE it; switching the "heatmap
    shows" combo only swaps the displayed result and never recomputes (same
    contract as PCA's fit-target combo).
    """

    def __init__(
        self,
        parent,
        groups: Sequence[GroupRecord],
        *,
        current_id: Optional[str],
        marked_ids: Sequence[str],
        find_group: FindGroup,
        get_xy: GetXY,
        bi: Callable,
        default_export_dir: Optional[Path] = None,
        store=None,
    ):
        super().__init__(
            parent,
            bi("小波变换 (Wavelet)", "Wavelet transform (WT)"),
            bi,
            default_export_dir=default_export_dir,
        )
        self._kind = "wavelet"
        # 560x640 squeezed the parameter form into overlapping rows (real-display
        # check 2026-09-16); 720 keeps every row readable while still fitting a
        # small laptop screen (§B.21).
        self.setMinimumSize(560, 720)
        # The embedded heatmap makes this dialog much taller than the 1-D
        # analyses; keep the natural height under ~900 px so it fits a laptop
        # screen (§B.21). The 4:3 heatmap stays — the report pane and the group
        # list give up the space instead.
        self.txt_report.setMinimumHeight(72)
        self._groups = list(groups)
        self._find = find_group
        self._get_xy = get_xy
        self._store = store
        self._current_id = current_id
        self._results: Dict[str, dict] = {}
        self._result_order: List[str] = []

        # "Overlay on plot" reads as "show" here: the WT view is a full panel,
        # not a curve drawn on top of the 1D plots.
        self.btn_apply_plot.setText(bi("显示到主图", "Show on main plot"))
        self.btn_apply_plot.setToolTip(
            bi(
                "选中热图显示的组，并把主绘图区切到 WT 视图（主图 WT 始终跟随左侧列表当前组，用这里保存的参数）。",
                "Select the shown group and switch the main plot to its WT view (the main WT view always follows the current group in the list, with the settings saved here).",
            )
        )

        box = QGroupBox(bi("谱选择", "Spectra"))
        form = QFormLayout(box)
        self.lst = QListWidget()
        self.lst.setMinimumHeight(70)
        pre = set(marked_ids) if marked_ids else {g.id for g in self._groups}
        _fill_group_list(self.lst, self._groups, preselect_ids=pre, multi=True)
        tools, self.btn_wt_all, self.btn_wt_unmark, self.btn_wt_invert = _analysis_mark_tools(
            self.lst, bi
        )
        form.addRow("", tools)
        form.addRow(bi("参与组（勾选）", "Groups (check)"), self.lst)
        self.cmb_show = QComboBox()
        self.cmb_show.setToolTip(
            bi(
                "切换热图显示的组；只切图，不重新计算。",
                "Switch which group the heatmap shows — display only, no recompute.",
            )
        )
        self.lst.itemChanged.connect(lambda *_: self._rebuild_show())
        self.cmb_show.currentIndexChanged.connect(self._on_show_changed)
        # (the combo itself is laid out directly above the heatmap it controls —
        # a form row under the group list was overlooked by users, v2.16.13)
        self.params_host.addWidget(box)

        pbox = QGroupBox(bi("小波参数", "Wavelet parameters"))
        self._pform = QFormLayout(pbox)
        self.cmb_kw = QComboBox()
        for value in (0, 1, 2, 3):
            self.cmb_kw.addItem(str(value), value)
        self.cmb_kw.setCurrentIndex(2)
        self.cmb_kw.setToolTip(
            bi(
                "χ'(k) = χ(k)·k^kw。数据已加权（例如 Athena 导出的 k³χ）时选 0。",
                "χ'(k) = χ(k)·k^kw. Pick 0 when the data is already weighted (e.g. an Athena k³χ export).",
            )
        )
        self._pform.addRow(bi("k 加权", "k weight"), self.cmb_kw)

        self.sp_kmin = _wt_spin(3, -10000.0, 10000.0, 0.5, bi("k 下限（Å⁻¹）", "k lower limit (Å⁻¹)"))
        self.sp_kmax = _wt_spin(3, -10000.0, 10000.0, 0.5, bi("k 上限（Å⁻¹）", "k upper limit (Å⁻¹)"))
        self.sp_kmin.setPrefix("min ")
        self.sp_kmax.setPrefix("max ")
        self.sp_dk = _wt_spin(4, 0.0, 1000.0, 0.01, bi("k 网格间隔（Å⁻¹）", "k grid step (Å⁻¹)"))
        self.btn_auto = QPushButton(bi("按数据重算", "Re-derive"))
        self.btn_auto.setObjectName("ghostBtn")
        self.btn_auto.setToolTip(
            bi(
                "用第一个有 χ(k) 的勾选组重新算 kmin/kmax/dk；没保存过 dR 时 dR 也按数据算（π/(10·(kmax−kmin))，取到 0.01，限 0.01–0.05 Å）。",
                "Re-derive kmin/kmax/dk from the first checked group with χ(k); dR too unless one was saved "
                "(π/(10·(kmax−kmin)), rounded to 0.01, kept within 0.01–0.05 Å).",
            )
        )
        self.btn_auto.clicked.connect(lambda *_: self._seed_params())
        # min + max share one row: this dialog is already tall because of the
        # embedded heatmap, so the parameter grid stays compact (§B.21).
        k_range_row = QHBoxLayout()
        k_range_row.setContentsMargins(0, 0, 0, 0)
        k_range_row.setSpacing(4)
        k_range_row.addWidget(self.sp_kmin, 1)
        k_range_row.addWidget(self.sp_kmax, 1)
        self._pform.addRow(bi("k min/max (Å⁻¹)", "k min/max (Å⁻¹)"), k_range_row)
        dk_row = QHBoxLayout()
        dk_row.setContentsMargins(0, 0, 0, 0)
        dk_row.setSpacing(4)
        dk_row.addWidget(self.sp_dk, 1)
        dk_row.addWidget(self.btn_auto)
        self._pform.addRow(bi("dk (Å⁻¹)", "dk (Å⁻¹)"), dk_row)

        self.sp_rmin = _wt_spin(3, 0.0, 10000.0, 0.1, bi("R 下限（Å）", "R lower limit (Å)"))
        self.sp_rmax = _wt_spin(3, 0.0, 10000.0, 0.1, bi("R 上限（Å）", "R upper limit (Å)"))
        self.sp_rmin.setPrefix("min ")
        self.sp_rmax.setPrefix("max ")
        self.sp_dR = _wt_spin(4, 0.0, 1000.0, 0.001, bi("R 网格间隔（Å）", "R grid step (Å)"))
        r_range_row = QHBoxLayout()
        r_range_row.setContentsMargins(0, 0, 0, 0)
        r_range_row.setSpacing(4)
        r_range_row.addWidget(self.sp_rmin, 1)
        r_range_row.addWidget(self.sp_rmax, 1)
        self._pform.addRow(bi("R min/max (Å)", "R min/max (Å)"), r_range_row)
        self._pform.addRow(bi("dR (Å)", "dR (Å)"), self.sp_dR)

        self.rad_morlet = QRadioButton("Morlet")
        self.rad_cauchy = QRadioButton("Cauchy")
        self.rad_morlet.setChecked(True)
        mw_row = QHBoxLayout()
        mw_row.setContentsMargins(0, 0, 0, 0)
        mw_row.setSpacing(8)
        mw_row.addWidget(self.rad_morlet)
        mw_row.addWidget(self.rad_cauchy)
        mw_row.addStretch(1)
        mw_host = QWidget()
        mw_host.setLayout(mw_row)
        self._pform.addRow(bi("母小波", "Wavelet"), mw_host)

        self.sp_sigma = _wt_spin(3, 0.0, 1000.0, 0.1, bi("Morlet σ（>0）", "Morlet σ (>0)"))
        self.sp_sigma.setValue(1.0)
        self.sp_eta = _wt_spin(3, 0.0, 10000.0, 1.0, bi("Morlet η（>0）", "Morlet η (>0)"))
        self.sp_eta.setValue(10.0)
        mor_row = QHBoxLayout()
        mor_row.setContentsMargins(0, 0, 0, 0)
        mor_row.setSpacing(4)
        mor_row.addWidget(QLabel("σ"))
        mor_row.addWidget(self.sp_sigma, 1)
        mor_row.addWidget(QLabel("η"))
        mor_row.addWidget(self.sp_eta, 1)
        self._morlet_host = QWidget()
        self._morlet_host.setLayout(mor_row)
        self._pform.addRow(bi("Morlet 参数", "Morlet params"), self._morlet_host)

        self.sp_norder = _wt_spin(1, 0.0, 100000.0, 10.0, bi("Cauchy 阶数 n（>1）", "Cauchy order n (>1)"))
        self.sp_norder.setValue(100.0)
        self._cauchy_host = QWidget()
        cauchy_row = QHBoxLayout(self._cauchy_host)
        cauchy_row.setContentsMargins(0, 0, 0, 0)
        cauchy_row.setSpacing(4)
        cauchy_row.addWidget(QLabel("n"))
        cauchy_row.addWidget(self.sp_norder, 1)
        self._pform.addRow(bi("Cauchy 参数", "Cauchy params"), self._cauchy_host)
        self.rad_morlet.toggled.connect(self._sync_wavelet_rows)
        self.rad_cauchy.toggled.connect(self._sync_wavelet_rows)
        self.params_host.addWidget(pbox)

        fmt_note = QLabel(
            bi(
                "导出：wt_<组名>.csv（表头 K,R,coef，6 位小数，k 变化最快）+ 同名 .png 热图；Tools → Export 里 Excel 模式则为按元素分 sheet 的单个工作簿。",
                "Export: wt_<group>.csv (header K,R,coef, 6 decimals, k fastest) + a matching .png "
                "heatmap; under Tools → Export the Excel choice writes one workbook with element sheets.",
            )
        )
        fmt_note.setWordWrap(True)
        fmt_note.setStyleSheet("color:#6b625d; font-size:11px;")
        self.params_host.addWidget(fmt_note)

        # Heatmap selector sits directly above the heatmap it controls (users
        # missed it as a form row under the group list), with an eye icon so it
        # reads as a view control at a glance. The svg stroke is #444, so it is
        # tinted to the palette text colour to survive the dark theme.
        show_row = QHBoxLayout()
        show_row.setContentsMargins(0, 0, 0, 0)
        show_row.setSpacing(4)
        eye_pm = QIcon(
            str(Path(__file__).resolve().parent / "assets" / "eye_on.svg")
        ).pixmap(16, 16)
        tint = QPainter(eye_pm)
        tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tint.fillRect(eye_pm.rect(), self.palette().color(QPalette.ColorRole.WindowText))
        tint.end()
        eye_lbl = QLabel()
        eye_lbl.setPixmap(eye_pm)
        eye_lbl.setFixedSize(18, 18)
        self._wt_show_icon = eye_lbl
        show_row.addWidget(eye_lbl)
        show_row.addWidget(QLabel(bi("热图显示", "Heatmap shows")))
        show_row.addWidget(self.cmb_show, 1)
        self.params_host.addLayout(show_row)

        # Embedded heatmap (the dialog is modal, so the main plot is not visible)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure

        self._fig = Figure(figsize=(5.2, 3.9), dpi=100)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setMinimumHeight(180)
        self._wt_ax = self._fig.add_subplot(111)
        self.params_host.addWidget(self._canvas, 1)
        self._sync_wavelet_rows()
        self._draw_result(None)
        self._seed_params()
        self._rebuild_show()

    # ---- parameter seeding / persistence ----

    def _first_usable_group(self) -> Optional[GroupRecord]:
        """Group whose χ(k) seeds the k window: current → checked → rest.

        Two passes so opening the dialog stays instant: first look for a group
        that ALREADY has processed χ(k) (free), and only then pay for at most
        three ``_get_xy`` calls (each may run ``g.process()``, ~1 s).
        """
        ordered: List[GroupRecord] = []
        seen = set()
        current = self._find(str(self._current_id or "")) if self._current_id else None
        for g in [current, *(self._find(gid) for gid in _checked_ids(self.lst)), *self._groups]:
            if g is None or g.id in seen:
                continue
            seen.add(g.id)
            ordered.append(g)
        for g in ordered:
            if _wt_has_chi(g):
                return g
        for g in ordered[:3]:
            k, chi = self._get_xy(g, "chi")
            if k is not None and chi is not None and len(np.asarray(k).ravel()) >= 2:
                return g
        return None

    def _seed_params(self) -> None:
        """k window always from data; R / wavelet shape from QSettings when present."""
        g = self._first_usable_group()
        if g is None:
            return
        k, _chi = self._get_xy(g, "chi")
        try:
            auto = auto_params(k)
        except ValueError:
            return
        stored = wt_stored_params(self._store)
        self.sp_kmin.setValue(auto["kmin"])
        self.sp_kmax.setValue(auto["kmax"])
        self.sp_dk.setValue(auto["dk"])
        self.sp_rmin.setValue(float(stored.get("Rmin", auto["Rmin"])))
        self.sp_rmax.setValue(float(stored.get("Rmax", auto["Rmax"])))
        self.sp_dR.setValue(float(stored.get("dR", auto["dR"])))
        self.sp_sigma.setValue(float(stored.get("sigma", auto["sigma"])))
        self.sp_eta.setValue(float(stored.get("eta", auto["eta"])))
        self.sp_norder.setValue(float(stored.get("norder", auto["norder"])))
        kw = stored.get("kw")
        if kw in (0, 1, 2, 3):
            self.cmb_kw.setCurrentIndex(int(kw))
        wavelet = str(stored.get("wavelet") or MORLET)
        (self.rad_cauchy if wavelet == CAUCHY else self.rad_morlet).setChecked(True)

    def _sync_wavelet_rows(self, *_args) -> None:
        """Show only the active wavelet's shape parameters (label rows included)."""
        morlet = self.rad_morlet.isChecked()
        for host, visible in ((self._morlet_host, morlet), (self._cauchy_host, not morlet)):
            host.setVisible(visible)
            label = self._pform.labelForField(host)
            if label is not None:
                label.setVisible(visible)

    def _collect_params(self) -> dict:
        return {
            "kw": int(self.cmb_kw.currentData()),
            "kmin": float(self.sp_kmin.value()),
            "kmax": float(self.sp_kmax.value()),
            "dk": float(self.sp_dk.value()),
            "Rmin": float(self.sp_rmin.value()),
            "Rmax": float(self.sp_rmax.value()),
            "dR": float(self.sp_dR.value()),
            "wavelet": CAUCHY if self.rad_cauchy.isChecked() else MORLET,
            "sigma": float(self.sp_sigma.value()),
            "eta": float(self.sp_eta.value()),
            "norder": float(self.sp_norder.value()),
        }

    # ---- heatmap display ----

    def _rebuild_show(self) -> None:
        """Combo follows the checked groups (same contract as PCA's fit target)."""
        current = self.cmb_show.currentData()
        self.cmb_show.blockSignals(True)
        self.cmb_show.clear()
        for gid in _checked_ids(self.lst):
            if gid in self._results:
                self.cmb_show.addItem(str(self._results[gid]["name"]), gid)
            else:
                g = self._find(gid)
                if g is not None:
                    self.cmb_show.addItem(g.name, gid)
        if current is not None:
            index = self.cmb_show.findData(current)
            if index >= 0:
                self.cmb_show.setCurrentIndex(index)
        self.cmb_show.blockSignals(False)
        self._on_show_changed()

    def set_select_callback(self, fn: Callable) -> None:
        """Host hook (v4.2.0): the "Heatmap shows" pick selects that group in
        the main Groups list, so the main WT view follows either side."""
        self._on_select_group = fn

    def _on_show_changed(self, *_args) -> None:
        gid = str(self.cmb_show.currentData() or "")
        self._draw_result(self._results.get(gid))
        cb = getattr(self, "_on_select_group", None)
        if cb is not None and gid and gid in self._results:
            cb(gid)

    def _draw_result(self, res: Optional[dict]) -> None:
        if not res:
            for stale in [a for a in list(self._fig.axes) if a is not self._wt_ax]:
                try:
                    stale.remove()
                except Exception:
                    pass
            self._wt_ax.clear()
            self._wt_ax.set_xticks([])
            self._wt_ax.set_yticks([])
            # Canvas text stays English like every other plot in the app —
            # matplotlib's default font has no CJK glyphs (zh renders as tofu).
            self._wt_ax.text(
                0.5,
                0.5,
                "Run to show the heatmap",
                ha="center",
                va="center",
                transform=self._wt_ax.transAxes,
                fontsize=10,
            )
            self._canvas.draw_idle()
            return
        self._wt_ax.clear()  # draw_wt replaces only its own colour bar, not the axes
        draw_wt(
            self._wt_ax,
            res["k_grid"],
            res["R_grid"],
            res["W"],
            kmin=res["kmin"],
            kmax=res["kmax"],
            title=str(res["name"]),
        )
        self._canvas.draw_idle()

    # ---- run ----

    def _on_run(self) -> None:
        ids = _checked_ids(self.lst)
        if not ids:
            QMessageBox.warning(
                self,
                self.windowTitle(),
                self._bi("请先勾选至少 1 条谱", "Check at least one spectrum first"),
            )
            return
        params = self._collect_params()

        progress = QProgressDialog(
            self._bi("小波变换计算中…", "Running wavelet transform…"),
            self._bi("取消", "Cancel"),
            0,
            len(ids),
            self,
        )
        progress.setWindowTitle(self.windowTitle())
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setMinimumWidth(380)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        results: Dict[str, dict] = {}
        order: List[str] = []
        skipped: List[Tuple[str, str]] = []
        cancelled = False
        try:
            for i, gid in enumerate(ids):
                g = self._find(gid)
                if g is None:
                    continue
                if progress.wasCanceled():
                    cancelled = True
                    break
                progress.setValue(i)
                progress.setLabelText(
                    self._bi(f"小波变换 {i + 1}/{len(ids)}：{g.name}",
                             f"Wavelet {i + 1}/{len(ids)}: {g.name}")
                )
                QApplication.processEvents()
                k, chi = self._get_xy(g, "chi")
                if k is None or chi is None or len(np.asarray(k).ravel()) < 2:
                    skipped.append((g.name, self._bi("该组无 χ(k)，已跳过", "no χ(k) — skipped")))
                    continue
                k = np.asarray(k, dtype=float).ravel()
                chi = np.asarray(chi, dtype=float).ravel()
                try:
                    k_grid, R_grid, W = cwt(k, chi, **params)
                except ValueError as exc:
                    skipped.append((g.name, str(exc)))
                    continue
                results[g.id] = {
                    "name": g.name,
                    "group_id": g.id,
                    "k_grid": k_grid,
                    "R_grid": R_grid,
                    "W": W,
                    "kmin": params["kmin"],
                    "kmax": params["kmax"],
                    "n_data": int(k.size),
                    "k_data": (float(k[0]), float(k[-1])),
                    "max_mag": float(np.max(W)) if W.size else 0.0,
                }
                order.append(g.id)
        finally:
            progress.setValue(len(ids))
            progress.close()
            progress.deleteLater()
            QApplication.processEvents()

        if not order:
            reason = "\n".join(f"[skip] {name}  {why}" for name, why in skipped)
            message = self._bi("没有可计算的组", "No usable group")
            self.txt_report.setPlainText(f"{message}\n\n{reason}")
            QMessageBox.warning(self, self.windowTitle(), f"{message}\n\n{reason}")
            return

        self._results = results
        self._result_order = order
        wt_save_params(self._store, params)
        self._rebuild_show()
        shown_gid = str(self.cmb_show.currentData() or "")
        if shown_gid not in self._results:
            shown_gid = order[0]
            index = self.cmb_show.findData(shown_gid)
            if index >= 0:
                self.cmb_show.setCurrentIndex(index)
        shown = self._results.get(shown_gid) or self._results[order[0]]
        self._draw_result(shown)

        payload = {
            "wt_params": dict(params),
            "wt_groups": [results[gid] for gid in order],
            "wt_skipped": list(skipped),
            "wt_cancelled": cancelled,
        }
        report = self._format_report(payload, shown)
        overlay = dict(shown)
        overlay["kind"] = "wt"
        overlay["title"] = f"WT - {shown['name']}"
        overlay["params"] = dict(params)
        self._set_success(report, payload, overlay)

    def _format_report(self, payload: dict, shown: dict) -> str:
        """Full parameter + per-group trace (§B.8 scientific traceability)."""
        import time

        p = payload["wt_params"]
        groups = payload["wt_groups"]
        skipped = payload["wt_skipped"]
        k_grid = np.asarray(shown["k_grid"], dtype=float)
        R_grid = np.asarray(shown["R_grid"], dtype=float)
        wavelet = str(p["wavelet"])
        if wavelet == CAUCHY:
            shape = f"n={float(p['norder']):.3f}"
        else:
            shape = f"sigma={float(p['sigma']):.3f}, eta={float(p['eta']):.3f}"
        lines = [
            self._bi("小波变换 (Wavelet CWT)", "Wavelet transform (CWT)"),
            f"{self._bi('时间', 'Time')}: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"{self._bi('母小波', 'Wavelet')}: {wavelet}  ({shape})",
            f"{self._bi('k 加权', 'k weight')}: kw={int(p['kw'])}"
            + (self._bi("（0 = 数据已加权，未再乘）", " (0 = already weighted, not re-multiplied)")
               if int(p["kw"]) == 0 else ""),
            f"{self._bi('k 窗', 'k window')}: {float(p['kmin']):.3f} – {float(p['kmax']):.3f} Å⁻¹"
            f"   dk={float(p['dk']):.4f}"
            f"   {self._bi('网格', 'grid')} {k_grid.size} pts"
            f"   {self._bi('窗外按 0 计（窗边 |W| 约为一半）', 'zero outside the window (|W| about half at the edges)')}",
            f"{self._bi('R 范围', 'R range')}: {float(p['Rmin']):.3f} – {float(p['Rmax']):.3f} Å"
            f"   dR={float(p['dR']):.4f}   {R_grid.size} rows",
            f"{self._bi('导出格式', 'Export layout')}: wt_<group>.csv = K,R,coef, %.6f, "
            f"{self._bi('带表头，k 变化最快', 'header row, k fastest')}",
            "",
            f"{self._bi('组', 'Groups')}: {len(groups)} "
            f"{self._bi('计算成功', 'computed')}, {len(skipped)} {self._bi('跳过', 'skipped')}"
            + (f"  [{self._bi('已取消，结果为部分', 'cancelled — partial results')}]"
               if payload.get("wt_cancelled") else ""),
            "",
        ]
        for row in groups:
            k0, k1 = row["k_data"]
            lines.append(
                f"[ok]   {row['name']}   "
                f"k {self._bi('数据', 'data')} {k0:.3f}–{k1:.3f} ({row['n_data']} pts)   "
                f"max|W|={row['max_mag']:.6g}"
            )
        for name, why in skipped:
            lines.append(f"[skip] {name}   {why}")
        return "\n".join(lines)

    def _on_apply_plot(self) -> None:
        """Same channel as the other dialogs, plus the flag that asks the host to
        switch the main canvas to the WT panel (a plain Run must not hijack the
        user's current 2×2 / E / k / R / q view).

        The overlay is rebuilt from the CURRENTLY selected "Heatmap shows" group:
        v2.16.13-dev re-sent ``_last_overlay`` (the group that was selected at Run
        time) forever, so switching the combo and pressing Show left the main
        window showing the old group. Falls back to the run-time group when the
        selected one has no result (e.g. it was skipped for missing χ(k)).

        v4.1.1: the dialog is modal and covers the plot it just changed, so a
        successful Show closes it (users read "nothing happened" otherwise),
        and Show with nothing to send says why instead of returning silently.
        """
        if not self._on_result:
            return
        gid = str(self.cmb_show.currentData() or "")
        shown = self._results.get(gid)
        if shown is not None:
            overlay = dict(shown)
            overlay["kind"] = "wt"
            overlay["title"] = f"WT - {shown['name']}"
            overlay["params"] = dict((self._last_payload or {}).get("wt_params") or {})
        else:
            overlay = self._last_overlay
        if not overlay:
            QMessageBox.information(
                self, self.windowTitle(),
                self._bi(
                    "还没有可显示的小波结果：请先点 Run（所选组需要有 χ(k)）。",
                    "No wavelet result to show yet: click Run first (the chosen group needs χ(k)).",
                ),
            )
            return
        self._on_result(self._last_report, {**overlay, "focus": True})
        self.accept()  # the main window now shows the WT view - reveal it
