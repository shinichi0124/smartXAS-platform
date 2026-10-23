"""
EXAFS fit engine for smartLarch Platform (xraylarch backend).

Primary validated recipe: Artemis "Fit 92" Model A
  - Two Pt–Pt paths only (no Pt–La)
  - S0^2 (amp) fixed at 0.67
  - 5 free params: e0, n1, dr1, ss1, dr2
  - Second-shell N=2 and ss2=0.008 fixed
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import traceback
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# larch imports are deferred in helpers so GUI can still open if import fails
# and report a clear error.

APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = APP_ROOT / "data"
DEFAULT_OUTPUT = APP_ROOT / "output"
DEFAULT_FEFF_IN = DEFAULT_DATA / "feff_inputs"
DEFAULT_FEFF_OUT = DEFAULT_DATA / "feff_paths"

# FEFF runs write fixed-name files in shared folders — serialize across worker threads
_FEFF_LOCK = threading.Lock()

# Artemis Fit 92 Model A gold standard (from Fit92_ModelA_report.txt)
GOLD_MODEL_A = {
    "n1": 9.086,
    "n1_err": 1.295,
    "e0": 9.845,
    "e0_err": 0.846,
    "ss1": 0.00349,
    "ss1_err": 0.00127,
    "dr1": -0.01223,
    "dr1_err": 0.00699,
    "dr2": 0.06995,
    "dr2_err": 0.05370,
    "r_factor": 0.0324,
    "r1": 2.776,  # Reff 2.788 + dr1
    "r2": 3.122,  # Reff 3.052 + dr2
    "reff1": 2.78820,
    "reff2": 3.05210,
    "amp": 0.67,
    "ss2": 0.008,
    "n2": 2.0,
}


@dataclass
class FitSettings:
    kmin: float = 2.0
    kmax: float = 10.0
    dk: float = 1.0
    kwindow: str = "hanning"
    kw_list: Tuple[int, ...] = (1, 2, 3)
    rmin: float = 1.0
    rmax: float = 4.0
    dr: float = 0.0
    rwindow: str = "hanning"
    fit_space: str = "r"  # r | k | q
    rbkg: float = 1.0
    # Model A fixed / free
    amp: float = 0.67
    e0_init: float = 8.0
    n1_init: float = 12.0
    dr1_init: float = 0.0
    ss1_init: float = 0.003
    dr2_init: float = 0.0
    ss2: float = 0.008
    n2: float = 2.0  # FEFF degeneracy for path 2 (path nlegs degeneracy in file)
    path1_feff: str = ""  # feffNNNN.dat for first shell Pt-Pt ~2.788
    path2_feff: str = ""  # feffNNNN.dat for second shell Pt-Pt ~3.052
    path1_degen: float = 1.0  # path file N set to 1; n1 multiplies amp
    path2_degen: float = 2.0


@dataclass
class FitResult:
    ok: bool
    message: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)
    paths: List[Dict[str, Any]] = field(default_factory=list)
    correlations: List[Tuple[str, str, float]] = field(default_factory=list)
    report_text: str = ""
    validation: Dict[str, Any] = field(default_factory=dict)
    # arrays for plotting (optional)
    k: Optional[np.ndarray] = None
    chi: Optional[np.ndarray] = None
    kwin: Optional[np.ndarray] = None
    fit_k: Optional[np.ndarray] = None
    fit_chi: Optional[np.ndarray] = None
    r: Optional[np.ndarray] = None
    chir_mag: Optional[np.ndarray] = None
    fit_r: Optional[np.ndarray] = None
    fit_chir_mag: Optional[np.ndarray] = None
    residual_r: Optional[np.ndarray] = None
    # Fit visualisation arrays recomputed from the same raw data/model pair for
    # each requested plotting k-weight.  These are display-only: changing a
    # plot never alters the least-squares objective or re-fits parameters.
    plot_views: Dict[int, Dict[str, np.ndarray]] = field(default_factory=dict)

    def to_text(self) -> str:
        lines = [self.report_text or "", ""]
        if self.validation:
            lines.append("=== Validation vs Artemis Fit 92 (Model A) ===")
            for k, v in self.validation.items():
                lines.append(f"  {k}: {v}")
        if not self.ok:
            lines.append(f"ERROR: {self.message}")
        return "\n".join(lines).strip() + "\n"


def build_fit_plot_views(
    k: np.ndarray,
    data_chi: np.ndarray,
    model_k: np.ndarray,
    model_chi: np.ndarray,
    settings: FitSettings,
    *,
    xftf_func=None,
    xftr_func=None,
    group_factory=None,
) -> Dict[int, Dict[str, np.ndarray]]:
    """Return k/R/q plotting arrays for every requested k-weight.

    ``feffit`` only writes one transformed output (the first fitting weight),
    while Artemis plot weights are purely a display choice.  Re-transform both
    the fitted model and its data with the same settings here, so they always
    share a vertical scale.  The residual is explicitly ``Re[data χ(R)] -
    Re[fit χ(R)]``; subtracting magnitudes is not a complex-FT residual.
    """
    if xftf_func is None or xftr_func is None or group_factory is None:
        L = _require_larch()
        xftf_func = xftf_func or L["xftf"]
        xftr_func = xftr_func or L["xftr"]
        if group_factory is None:
            from larch import Group

            group_factory = Group

    k = np.asarray(k, dtype=float)
    data_chi = np.asarray(data_chi, dtype=float)
    model_k = np.asarray(model_k, dtype=float)
    model_chi = np.asarray(model_chi, dtype=float)
    if not (k.size and data_chi.size and model_k.size and model_chi.size):
        return {}
    n = min(k.size, data_chi.size)
    k, data_chi = k[:n], data_chi[:n]
    model_on_data_k = np.interp(k, model_k, model_chi)
    weights = tuple(dict.fromkeys(int(w) for w in settings.kw_list if int(w) >= 0)) or (2,)
    views: Dict[int, Dict[str, np.ndarray]] = {}

    def _transformed(chi: np.ndarray, kw: int):
        group = group_factory()
        xftf_func(
            k,
            chi,
            group=group,
            kmin=settings.kmin,
            kmax=settings.kmax,
            dk=settings.dk,
            window=settings.kwindow,
            kweight=kw,
            rmax_out=max(10.0, settings.rmax + 2.0),
        )
        xftr_func(
            group.r,
            group.chir,
            group=group,
            rmin=settings.rmin,
            rmax=settings.rmax,
            dr=settings.dr,
            window=settings.rwindow,
        )
        return group

    for kw in weights:
        data = _transformed(data_chi, kw)
        model = _transformed(model_on_data_k, kw)
        nr = min(len(data.r), len(model.r), len(data.chir_mag), len(model.chir_mag))
        nq = min(len(data.q), len(model.q), len(data.chiq_re), len(model.chiq_re))
        views[kw] = {
            "k": k.copy(),
            "data_kchi": data_chi * k**kw,
            "fit_kchi": model_on_data_k * k**kw,
            "r": np.asarray(data.r[:nr], dtype=float).copy(),
            "data_rmag": np.asarray(data.chir_mag[:nr], dtype=float).copy(),
            "fit_rmag": np.asarray(model.chir_mag[:nr], dtype=float).copy(),
            "data_rre": np.asarray(data.chir_re[:nr], dtype=float).copy(),
            "fit_rre": np.asarray(model.chir_re[:nr], dtype=float).copy(),
            "residual_rre": np.asarray(data.chir_re[:nr] - model.chir_re[:nr], dtype=float).copy(),
            "q": np.asarray(data.q[:nq], dtype=float).copy(),
            "data_qre": np.asarray(data.chiq_re[:nq], dtype=float).copy(),
            "fit_qre": np.asarray(model.chiq_re[:nq], dtype=float).copy(),
        }
    return views


def _require_larch():
    try:
        import larch  # noqa: F401
        from larch import Group
        from larch.fitting import param, param_group
        from larch.io import read_ascii, read_athena
        from larch.xafs import (
            autobk,
            feffit,
            feffit_dataset,
            feffit_report,
            feffit_transform,
            feffpath,
            pre_edge,
            xftf,
            xftr,
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "xraylarch is not available in this Python. "
            "Activate the `larch` conda env or install: "
            "conda install -c conda-forge xraylarch\n"
            f"Detail: {exc}"
        ) from exc
    return {
        "Group": Group,
        "param": param,
        "param_group": param_group,
        "read_ascii": read_ascii,
        "read_athena": read_athena,
        "pre_edge": pre_edge,
        "autobk": autobk,
        "xftf": xftf,
        "xftr": xftr,
        "feffpath": feffpath,
        "feffit_transform": feffit_transform,
        "feffit_dataset": feffit_dataset,
        "feffit": feffit,
        "feffit_report": feffit_report,
        "larch_version": getattr(__import__("larch"), "__version__", "?"),
    }


def project_paths() -> Dict[str, Path]:
    return {
        "root": APP_ROOT,
        "data": DEFAULT_DATA,
        "output": DEFAULT_OUTPUT,
        "feff_in": DEFAULT_FEFF_IN,
        "feff_out": DEFAULT_FEFF_OUT,
        "demo_xdi": DEFAULT_DATA / "demo_PtLa_001.xdi",
        "pt_feff_inp": DEFAULT_FEFF_IN / "Pt_foil_feff.inp",
        "lapt_feff_inp": DEFAULT_FEFF_IN / "LaPt_feff.inp",
    }


def list_athena_groups(prj_path: str | Path) -> List[str]:
    L = _require_larch()
    prj = L["read_athena"](str(prj_path))
    names = []
    # read_athena returns Group with subgroups
    for key in dir(prj):
        if key.startswith("_"):
            continue
        g = getattr(prj, key, None)
        if g is None:
            continue
        if hasattr(g, "energy") or hasattr(g, "mu") or hasattr(g, "chi"):
            names.append(key)
    if not names and hasattr(prj, "groups"):
        try:
            names = list(prj.groups.keys())
        except Exception:
            pass
    return names


def load_spectrum(
    path: str | Path,
    group_name: Optional[str] = None,
    rbkg: float = 1.0,
) -> Any:
    """Load .xdi / column ASCII or Athena .prj group; return processed larch Group with chi(k)."""
    L = _require_larch()
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    if path.suffix.lower() == ".prj":
        prj = L["read_athena"](str(path))
        g = None
        if group_name and hasattr(prj, group_name):
            g = getattr(prj, group_name)
        else:
            # first usable group
            for key in dir(prj):
                if key.startswith("_"):
                    continue
                cand = getattr(prj, key, None)
                if cand is not None and (hasattr(cand, "energy") or hasattr(cand, "chi")):
                    g = cand
                    group_name = key
                    break
        if g is None:
            raise RuntimeError(f"No usable group in {path}")
        g.filename = str(path)
        g.group_name = group_name
    else:
        # XDI / ASCII — try energy/mu columns
        g = L["read_ascii"](str(path))
        # map common column names
        cols = {c.lower(): c for c in getattr(g, "array_labels", []) or []}
        # read_ascii stores columns as attributes from labels
        if not hasattr(g, "energy"):
            for cand in ("energy", "e", "en"):
                if cand in cols:
                    g.energy = np.asarray(getattr(g, cols[cand]), dtype=float)
                    break
        if not hasattr(g, "mu"):
            # XDI may have i0, itrans → mu = ln(i0/itrans) or already mu
            labels = [c.lower() for c in getattr(g, "array_labels", []) or []]
            # Prefer explicit mu
            if "mu" in labels:
                g.mu = np.asarray(getattr(g, g.array_labels[labels.index("mu")]), dtype=float)
            elif "i0" in labels and "itrans" in labels:
                i0 = np.asarray(getattr(g, g.array_labels[labels.index("i0")]), dtype=float)
                it = np.asarray(getattr(g, g.array_labels[labels.index("itrans")]), dtype=float)
                with np.errstate(divide="ignore", invalid="ignore"):
                    g.mu = np.log(i0 / it)
            elif hasattr(g, "data") and getattr(g, "data", None) is not None:
                arr = np.asarray(g.data, dtype=float)
                if arr.ndim == 2 and arr.shape[0] >= 2:
                    g.energy = arr[0]
                    g.mu = arr[1]
        g.filename = str(path)
        g.group_name = path.stem
        # drop non-finite points (log of ≤0 counts, NaNs) before processing
        if hasattr(g, "energy") and hasattr(g, "mu"):
            _e = np.asarray(g.energy, dtype=float)
            _m = np.asarray(g.mu, dtype=float)
            _good = np.isfinite(_e) & np.isfinite(_m)
            if not _good.all():
                g.energy = _e[_good]
                g.mu = _m[_good]

    # Ensure energy/mu then process
    if hasattr(g, "chi") and g.chi is not None and len(np.asarray(g.chi)) > 10:
        # already has chi — still ensure k
        if not hasattr(g, "k") or g.k is None:
            # try rebuild from energy if present
            if hasattr(g, "energy") and hasattr(g, "mu"):
                L["pre_edge"](g)
                L["autobk"](g, rbkg=rbkg)
        else:
            # re-FT later
            pass
    else:
        if not hasattr(g, "energy") or not hasattr(g, "mu"):
            # last resort: read raw columns from xdi manually
            g = _load_xdi_manual(path, L)
        L["pre_edge"](g)
        L["autobk"](g, rbkg=rbkg)

    return g


def _load_xdi_manual(path: Path, L: dict) -> Any:
    """Parse XDI-like file: header lines starting with #, then columns energy i0 itrans…"""
    from .athena_ops import _energy_scale_from_label

    energy, i0, itrans, mu_col = [], [], [], []
    labels: List[str] = []
    energy_label = ""  # full label with units, for keV detection
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                if "Column." in s or "column." in s.lower():
                    # # Column.1: energy  (label may carry units: "energy eV")
                    try:
                        part = s.split(":", 1)[1].strip().lower()
                        # keep first whitespace token only — units break the
                        # "energy"/"mu" key match below
                        tok = part.split()[0] if part else ""
                        if tok == "energy" and "energy" not in labels:
                            energy_label = part
                        labels.append(tok)
                    except Exception:
                        pass
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            try:
                vals = [float(x) for x in parts]
            except ValueError:
                continue
            if labels:
                row = {labels[i]: vals[i] for i in range(min(len(labels), len(vals)))}
                if "energy" in row:
                    energy.append(row["energy"])
                if "i0" in row:
                    i0.append(row["i0"])
                if "itrans" in row:
                    itrans.append(row["itrans"])
                if "mu" in row:
                    mu_col.append(row["mu"])
            else:
                energy.append(vals[0])
                if len(vals) >= 5:
                    i0.append(vals[3])
                    itrans.append(vals[4])
                elif len(vals) >= 2:
                    mu_col.append(vals[1])

    g = L["Group"]()
    e_arr = np.asarray(energy, dtype=float)
    g.energy = e_arr * _energy_scale_from_label(energy_label, e_arr)
    if mu_col:
        g.mu = np.asarray(mu_col, dtype=float)
    elif i0 and itrans:
        i0a = np.asarray(i0, dtype=float)
        ita = np.asarray(itrans, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            g.mu = np.log(i0a / ita)
    else:
        raise RuntimeError(f"Cannot parse energy/mu from {path}")
    if len(g.energy) != len(g.mu) or len(g.energy) < 10:
        # e.g. unit-tagged column labels failed to match — let callers fall back
        raise RuntimeError(f"Cannot parse matching energy/mu columns from {path}")
    # drop non-finite points (log of ≤0 counts, NaNs) like load_spectrum does
    good = np.isfinite(g.energy) & np.isfinite(g.mu)
    if not good.all():
        g.energy = g.energy[good]
        g.mu = g.mu[good]
    if len(g.energy) < 10:
        raise RuntimeError(f"Too few finite energy/mu points in {path}")
    g.filename = str(path)
    g.group_name = path.stem
    return g


def _parse_feff_dat_header(path: Path) -> Dict[str, float]:
    """Read Reff / degeneracy / nleg from feffNNNN.dat header lines."""
    info = {"path": str(path), "reff": float("nan"), "degen": float("nan"), "nleg": float("nan")}
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return info
    # FEFF6L path header looks like:
    #   "   2  12.000   2.7882    2.8787   -3.36168 nleg, deg, reff, rnrmav(bohr), edge"
    # i.e. nleg, deg, reff, rnrmav, edge followed by a trailing text comment.
    # Take the leading run of float-able tokens so the comment never pollutes
    # the values, and stop at the FIRST plausible header line — later numeric
    # rows are atom coords / k-grid data.
    for line in lines[:40]:
        parts = line.replace(",", " ").split()
        lead: List[float] = []
        for tok in parts:
            if _is_float(tok):
                lead.append(float(tok))
            else:
                break
        if len(lead) >= 5:
            nleg, degen, reff = lead[0], lead[1], lead[2]
            if (
                nleg == int(nleg)
                and 2 <= int(nleg) <= 8
                and 0 < degen < 100
                and 1.0 < reff < 6.0
            ):
                info["nleg"] = float(int(nleg))
                info["degen"] = degen
                info["reff"] = reff
                return info
        # fallback: keyword line such as "Reff = 2.788" — first float AFTER the
        # keyword, so a trailing "… reff, rnrmav(bohr), edge" comment can't win
        low = line.lower()
        pos = low.find("reff")
        if pos < 0:
            pos = low.find("r effective")
        if pos >= 0 and not np.isfinite(info["reff"]):
            nums = [
                float(x)
                for x in line[pos:].replace(",", " ").replace("=", " ").split()
                if _is_float(x)
            ]
            if nums and 1.0 < nums[0] < 6.0:
                info["reff"] = nums[0]
    return info


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except Exception:
        return False


def run_feff(
    feff_inp: str | Path,
    out_dir: str | Path,
    clean: bool = True,
) -> List[Path]:
    """
    Run FEFF6 (via larch) on a Demeter-style feff.inp.
    Returns list of generated feffNNNN.dat paths.
    """
    L = _require_larch()
    feff_inp = Path(feff_inp)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not feff_inp.is_file():
        raise FileNotFoundError(feff_inp)

    work = out_dir
    dest_inp = work / "feff.inp"
    shutil.copy2(feff_inp, dest_inp)

    # Prefer larch's bundled FEFF6l (Windows: larch/bin/win64/feff6l.exe)
    ran = False
    errors = []
    try:
        from larch.xafs import feff6l

        feff6l(feffinp="feff.inp", folder=str(work), verbose=True)
        ran = True
    except Exception as e1:
        errors.append(f"feff6l: {e1}")
        try:
            from larch.xafs import FeffRunner

            runner = FeffRunner(feffinp="feff.inp", folder=str(work), verbose=True)
            runner.run()
            ran = True
        except Exception as e2:
            errors.append(f"FeffRunner: {e2}")

    dats = sorted(work.glob("feff*.dat"))
    dats = [p for p in dats if p.name.lower().startswith("feff") and p.suffix.lower() == ".dat"]
    # FEFF sometimes writes feff0001.dat etc.; also ignore tiny stubs
    dats = [p for p in dats if p.stat().st_size > 200]
    if not dats:
        # list folder for debugging
        listing = "\n".join(f"  {p.name} ({p.stat().st_size})" for p in sorted(work.iterdir()))
        raise RuntimeError(
            "FEFF produced no feffNNNN.dat files.\n"
            + "\n".join(errors)
            + f"\nFolder listing:\n{listing}"
        )
    return dats


def prepare_model_a_paths(
    feff_out: Optional[Path] = None,
    force: bool = False,
) -> Dict[str, Path]:
    """Thread-safe wrapper — FEFF runs use fixed filenames in shared folders."""
    with _FEFF_LOCK:
        return _prepare_model_a_paths(feff_out=feff_out, force=force)


def _prepare_model_a_paths(
    feff_out: Optional[Path] = None,
    force: bool = False,
) -> Dict[str, Path]:
    """
    Ensure Model A path files exist:
      path1: first-shell Pt–Pt Reff ~ 2.788 from Pt foil FEFF
      path2: second-shell Pt–Pt Reff ~ 3.052 from LaPt FEFF
    """
    paths = project_paths()
    feff_out = Path(feff_out or paths["feff_out"])
    pt_dir = feff_out / "pt_foil"
    lapt_dir = feff_out / "lapt"
    pt_dir.mkdir(parents=True, exist_ok=True)
    lapt_dir.mkdir(parents=True, exist_ok=True)

    def _reff_of(p: Path) -> float:
        try:
            L = _require_larch()
            return float(L["feffpath"](str(p)).reff)
        except Exception:
            info = _parse_feff_dat_header(p)
            return float(info.get("reff", float("nan")))

    def _have_good(folder: Path, reff_target: float, tol: float = 0.05) -> Optional[Path]:
        best = None
        best_d = 1e9
        for p in list(folder.glob("feff*.dat")) + list(folder.glob("path*.dat")):
            if p.stat().st_size < 500:
                continue
            r = _reff_of(p)
            if r != r:
                continue
            d = abs(r - reff_target)
            if d < best_d:
                best_d = d
                best = p
        if best is not None and best_d <= tol:
            return best
        return None

    stable1 = feff_out / "path1_PtPt_2.788.dat"
    stable2 = feff_out / "path2_PtPt_3.052.dat"
    if not force and stable1.is_file() and stable2.is_file():
        r1s, r2s = _reff_of(stable1), _reff_of(stable2)
        if abs(r1s - GOLD_MODEL_A["reff1"]) <= 0.05 and abs(r2s - GOLD_MODEL_A["reff2"]) <= 0.05:
            return {
                "path1": stable1,
                "path2": stable2,
                "path1_src": stable1,
                "path2_src": stable2,
            }

    p1 = None if force else _have_good(pt_dir, GOLD_MODEL_A["reff1"])
    p2 = None if force else _have_good(lapt_dir, GOLD_MODEL_A["reff2"])

    if p1 is None:
        dats = run_feff(paths["pt_feff_inp"], pt_dir)
        p1 = _pick_path_by_reff(dats, GOLD_MODEL_A["reff1"])
    if p2 is None:
        dats = run_feff(paths["lapt_feff_inp"], lapt_dir)
        p2 = _pick_path_by_reff(dats, GOLD_MODEL_A["reff2"])

    shutil.copy2(p1, stable1)
    shutil.copy2(p2, stable2)
    return {"path1": stable1, "path2": stable2, "path1_src": p1, "path2_src": p2}


def _pick_path_by_reff(dats: Sequence[Path], reff_target: float) -> Path:
    L = _require_larch()
    best = None
    best_d = 1e9
    details = []
    for p in dats:
        try:
            fp = L["feffpath"](str(p))
            r = float(fp.reff)
            deg = float(getattr(fp, "degen", getattr(fp, "n", float("nan"))))
            details.append(f"{p.name}: Reff={r:.5f} degen={deg}")
            d = abs(r - reff_target)
            if d < best_d:
                best_d = d
                best = p
        except Exception as exc:
            details.append(f"{p.name}: ERR {exc}")
    if best is None:
        raise RuntimeError("No readable feff path.\n" + "\n".join(details))
    if best_d > 0.15:
        raise RuntimeError(
            f"Closest path Reff off by {best_d:.3f} from target {reff_target}.\n"
            + "\n".join(details)
        )
    return best


def run_model_a_fit(
    data_path: str | Path | None = None,
    settings: Optional[FitSettings] = None,
    group_name: Optional[str] = None,
    output_dir: Optional[Path] = None,
    prepare_paths: bool = True,
    group=None,
) -> FitResult:
    """Run Model A EXAFS fit and validate against Artemis Fit 92 gold numbers.

    Pass either:
      - ``group``: already Athena-processed larch Group (preferred from UI), or
      - ``data_path``: path to .xdi/.prj (auto pre_edge+autobk with defaults).
    """
    L = _require_larch()
    settings = replace(settings) if settings is not None else FitSettings()
    output_dir = Path(output_dir or DEFAULT_OUTPUT)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        need_paths = (
            not settings.path1_feff
            or not settings.path2_feff
            or not Path(settings.path1_feff).is_file()
            or not Path(settings.path2_feff).is_file()
        )
        if prepare_paths and need_paths:
            prepared = prepare_model_a_paths(force=False)
            settings.path1_feff = str(prepared["path1"])
            settings.path2_feff = str(prepared["path2"])

        for p in (settings.path1_feff, settings.path2_feff):
            if not Path(p).is_file():
                raise FileNotFoundError(f"Missing FEFF path file: {p}")

        if group is not None:
            g = group
            if not hasattr(g, "chi") or g.chi is None:
                raise RuntimeError("Provided group has no chi(k) — run Athena processing first")
        else:
            if data_path is None:
                raise ValueError("Provide data_path or group")
            g = load_spectrum(data_path, group_name=group_name, rbkg=settings.rbkg)

        # FT for visualization
        L["xftf"](
            g,
            kmin=settings.kmin,
            kmax=settings.kmax,
            dk=settings.dk,
            window=settings.kwindow,
            kweight=2,
        )

        # Parameters — Model A
        pars = L["param_group"](
            amp=L["param"](settings.amp, vary=False),
            e0=L["param"](settings.e0_init, vary=True),
            n1=L["param"](settings.n1_init, vary=True, min=0.1, max=20),
            dr1=L["param"](settings.dr1_init, vary=True),
            ss1=L["param"](settings.ss1_init, vary=True, min=1e-5, max=0.05),
            dr2=L["param"](settings.dr2_init, vary=True),
            ss2=L["param"](settings.ss2, vary=False),
        )

        # Paths
        # Path1: N in file = 1, S02 = amp*n1 so n1 is coordination number
        path1 = L["feffpath"](
            settings.path1_feff,
            degen=settings.path1_degen,
            s02="amp * n1",
            e0="e0",
            deltar="dr1",
            sigma2="ss1",
        )
        # Path2: degeneracy 2 in file, S02 = amp only (N fixed at FEFF degen)
        path2 = L["feffpath"](
            settings.path2_feff,
            degen=settings.path2_degen,
            s02="amp",
            e0="e0",
            deltar="dr2",
            sigma2="ss2",
        )

        trans = L["feffit_transform"](
            kmin=settings.kmin,
            kmax=settings.kmax,
            dk=settings.dk,
            kw=list(settings.kw_list),
            window=settings.kwindow,
            fitspace=settings.fit_space,
            rmin=settings.rmin,
            rmax=settings.rmax,
            dr=settings.dr,
            rwindow=settings.rwindow,
        )

        dset = L["feffit_dataset"](g, [path1, path2], trans)
        out = L["feffit"](pars, dset)

        report = L["feffit_report"](out)
        # Extract param values (lmfit Parameters: dict-like + attribute access)
        def _pv(name: str) -> Tuple[float, float]:
            p = None
            params_obj = getattr(out, "params", None)
            if params_obj is None:
                return float("nan"), float("nan")
            try:
                p = params_obj[name]
            except Exception:
                p = getattr(params_obj, name, None)
            if p is None:
                return float("nan"), float("nan")
            try:
                val = float(p.value)
            except Exception:
                try:
                    val = float(p)
                except Exception:
                    return float("nan"), float("nan")
            try:
                err = p.stderr
                err = float(err) if err is not None else float("nan")
            except Exception:
                err = float("nan")
            return val, err

        e0, e0e = _pv("e0")
        n1, n1e = _pv("n1")
        dr1, dr1e = _pv("dr1")
        ss1, ss1e = _pv("ss1")
        dr2, dr2e = _pv("dr2")
        amp, _ = _pv("amp")
        ss2, _ = _pv("ss2")

        reff1 = float(getattr(path1, "reff", GOLD_MODEL_A["reff1"]))
        reff2 = float(getattr(path2, "reff", GOLD_MODEL_A["reff2"]))
        r1 = reff1 + dr1
        r2 = reff2 + dr2

        # Statistics
        r_factor = float(getattr(out, "rfactor", getattr(out, "r_factor", float("nan"))))
        chi2 = float(getattr(out, "chi_square", getattr(out, "chi2", float("nan"))))
        redchi = float(getattr(out, "chi2_reduced", getattr(out, "redchi", float("nan"))))
        n_ind = float(getattr(out, "n_independent", float("nan")))
        if not np.isfinite(n_ind):
            # N_idp ≈ 2·Δk·ΔR/π + 2 (Stern 1993) — nvarys is NOT a valid fallback
            n_ind = 2.0 * (settings.kmax - settings.kmin) * (settings.rmax - settings.rmin) / np.pi + 2.0
        # some versions nest under out.datasets[0]
        if not np.isfinite(r_factor):
            try:
                r_factor = float(out.datasets[0].rfactor)
            except Exception:
                pass

        # Correlations
        corrs: List[Tuple[str, str, float]] = []
        covar_names = list(getattr(out, "var_names", []) or [])
        try:
            # lmfit-like
            for i, a in enumerate(covar_names):
                for j, b in enumerate(covar_names):
                    if j <= i:
                        continue
                    try:
                        c = out.params[a].correl.get(b)
                        if c is not None and abs(c) >= 0.4:
                            corrs.append((a, b, float(c)))
                    except Exception:
                        pass
        except Exception:
            pass
        corrs.sort(key=lambda t: -abs(t[2]))

        params = {
            "amp": {"value": amp, "stderr": 0.0, "vary": False},
            "e0": {"value": e0, "stderr": e0e, "vary": True},
            "n1": {"value": n1, "stderr": n1e, "vary": True},
            "dr1": {"value": dr1, "stderr": dr1e, "vary": True},
            "ss1": {"value": ss1, "stderr": ss1e, "vary": True},
            "dr2": {"value": dr2, "stderr": dr2e, "vary": True},
            "ss2": {"value": ss2, "stderr": 0.0, "vary": False},
        }
        stats = {
            "r_factor": r_factor,
            "chi_square": chi2,
            "reduced_chi_square": redchi,
            "n_independent": n_ind,
            "n_variables": 5,
            "larch_version": L["larch_version"],
            "data": str(data_path),
            "group": getattr(g, "group_name", ""),
        }
        path_rows = [
            {
                "name": "Pt-Pt 1st (path1)",
                "N": n1,
                "S02": amp * n1,
                "sigma2": ss1,
                "e0": e0,
                "delR": dr1,
                "Reff": reff1,
                "R": r1,
            },
            {
                "name": "Pt-Pt 2nd (path2)",
                "N": settings.path2_degen,
                "S02": amp,
                "sigma2": ss2,
                "e0": e0,
                "delR": dr2,
                "Reff": reff2,
                "R": r2,
            },
        ]

        validation = _validate_against_gold(
            {
                "n1": n1,
                "e0": e0,
                "ss1": ss1,
                "dr1": dr1,
                "dr2": dr2,
                "r_factor": r_factor,
                "r1": r1,
                "r2": r2,
            }
        )

        # Plot arrays: data chi(R) and fit
        k = np.asarray(getattr(g, "k", []), dtype=float)
        chi = np.asarray(getattr(g, "chi", []), dtype=float)
        r = np.asarray(getattr(g, "r", []), dtype=float)
        chir_mag = np.asarray(getattr(g, "chir_mag", []), dtype=float)

        fit_r = None
        fit_chir_mag = None
        residual = None
        try:
            # feffit result model in R space
            ds0 = out.datasets[0]
            if hasattr(ds0, "data") and hasattr(ds0.data, "r"):
                fit_r = np.asarray(ds0.data.r, dtype=float)
                data_mag = np.asarray(getattr(ds0.data, "chir_mag", []), dtype=float)
                model_mag = np.asarray(getattr(ds0.model, "chir_mag", []), dtype=float)
                if model_mag.size:
                    fit_chir_mag = model_mag
                    if data_mag.size == model_mag.size:
                        residual = data_mag - model_mag
                    chir_mag = data_mag if data_mag.size else chir_mag
                    r = fit_r if fit_r is not None and fit_r.size else r
        except Exception:
            pass

        # Save report + json
        report_path = output_dir / "fit92_model_a_report.txt"
        json_path = output_dir / "fit92_model_a_result.json"
        full_report = _format_report(params, stats, path_rows, corrs, report, validation)
        report_path.write_text(full_report, encoding="utf-8")
        serial = {
            "params": params,
            "statistics": stats,
            "paths": path_rows,
            "correlations": corrs,
            "validation": validation,
            "settings": asdict(settings),
        }
        json_path.write_text(json.dumps(serial, indent=2, default=str), encoding="utf-8")

        # Save R-space plot
        plot_path = output_dir / "fit92_model_a_chiR.png"
        try:
            _save_r_plot(r, chir_mag, fit_chir_mag, residual, plot_path, title="Model A |χ(R)|")
        except Exception as plot_exc:
            full_report += f"\n[plot warning] {plot_exc}\n"

        result = FitResult(
            ok=True,
            message="Fit completed",
            params=params,
            statistics=stats,
            paths=path_rows,
            correlations=corrs,
            report_text=full_report,
            validation=validation,
            k=k,
            chi=chi,
            r=r,
            chir_mag=chir_mag,
            fit_r=fit_r if fit_r is not None else r,
            fit_chir_mag=fit_chir_mag,
            residual_r=residual,
        )
        # feffit writes R-space arrays at its first fitting k-weight only.
        # Rebuild all requested plotting weights from the raw data/model pair
        # so demo check uses the same coherent display path as manual Auto fit.
        try:
            ds0 = out.datasets[0]
            model_k = np.asarray(getattr(ds0.model, "k", k), dtype=float)
            model_chi = np.asarray(getattr(ds0.model, "chi", []), dtype=float)
            if model_chi.size:
                result.plot_views = build_fit_plot_views(k, chi, model_k, model_chi, settings)
                preferred = int(settings.kw_list[0]) if settings.kw_list else 2
                view = result.plot_views.get(preferred) or next(iter(result.plot_views.values()), None)
                if view:
                    result.r = view["r"]
                    result.chir_mag = view["data_rmag"]
                    result.fit_r = view["r"]
                    result.fit_chir_mag = view["fit_rmag"]
                    result.residual_r = view["residual_rre"]
        except Exception:
            result.plot_views = {}
        return result
    except Exception as exc:
        tb = traceback.format_exc()
        return FitResult(ok=False, message=f"{exc}\n{tb}", report_text=str(exc))


def _validate_against_gold(vals: Dict[str, float], rel_tol: float = 0.25) -> Dict[str, str]:
    """
    Compare key results to Artemis Fit 92.

    Hard checks (must pass): R1, R2, r_factor — geometry + fit quality.
    Soft checks (bkg-sensitive): n1, e0, ss1 — raw XDI vs Athena-processed
    chi often shifts n1 by ~20–30% while R and R-factor stay good.
    Overall PASS if all hard pass and ≥2 soft pass.
    """
    # name -> (got, expected, tol, kind, mode)  mode: 'abs' | 'rel'
    checks = [
        ("R1", vals.get("r1"), GOLD_MODEL_A["r1"], 0.02, "hard", "abs"),
        ("R2", vals.get("r2"), GOLD_MODEL_A["r2"], 0.05, "hard", "abs"),
        ("r_factor", vals.get("r_factor"), GOLD_MODEL_A["r_factor"], 0.5, "hard", "rel"),
        ("n1", vals.get("n1"), GOLD_MODEL_A["n1"], 0.35, "soft", "rel"),
        ("e0", vals.get("e0"), GOLD_MODEL_A["e0"], 0.40, "soft", "rel"),
        ("ss1", vals.get("ss1"), GOLD_MODEL_A["ss1"], 0.5, "soft", "rel"),
    ]
    out: Dict[str, str] = {}
    hard_ok = soft_ok = soft_n = 0
    for name, got, exp, tol, kind, mode in checks:
        if got is None or not np.isfinite(got):
            out[name] = f"FAIL (got={got}, expected≈{exp}) [{kind}]"
            continue
        if mode == "abs":
            ok = abs(got - exp) <= tol
            delta = got - exp
            msg = (
                f"{'PASS' if ok else 'FAIL'} got={got:.4f} expected≈{exp:.4f} "
                f"Δ={delta:+.4f} Å (tol {tol} Å) [{kind}]"
            )
        else:
            ok = abs(got - exp) / abs(exp) <= tol if abs(exp) > 1e-12 else abs(got - exp) <= tol
            pct = 100.0 * abs(got - exp) / abs(exp) if abs(exp) > 1e-12 else float("nan")
            msg = (
                f"{'PASS' if ok else 'FAIL'} got={got:.5g} expected≈{exp:.5g} "
                f"({pct:.1f}% off, tol {tol*100:.0f}%) [{kind}]"
            )
        out[name] = msg
        if kind == "hard":
            hard_ok += int(ok)
        else:
            soft_n += 1
            soft_ok += int(ok)
    hard_total = 3
    if hard_ok == hard_total and soft_ok >= 2:
        out["overall"] = (
            f"PASS (hard {hard_ok}/{hard_total}, soft {soft_ok}/{soft_n}; "
            "n1 may differ on raw XDI vs Athena-processed chi)"
        )
    else:
        out["overall"] = (
            f"FAIL (hard {hard_ok}/{hard_total}, soft {soft_ok}/{soft_n}; "
            "check FEFF path assignment / background)"
        )
    return out


def _format_report(params, stats, path_rows, corrs, raw_report, validation) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append(" smartLarch Platform — Model A fit report (xraylarch)")
    lines.append("=" * 70)
    lines.append(f" Data           : {stats.get('data')}")
    lines.append(f" Group          : {stats.get('group')}")
    lines.append(f" larch version  : {stats.get('larch_version')}")
    lines.append("")
    lines.append("--- Statistics ---")
    for k in ("r_factor", "chi_square", "reduced_chi_square", "n_independent", "n_variables"):
        lines.append(f"  {k:20s} = {stats.get(k)}")
    lines.append("")
    lines.append("--- Parameters ---")
    for name, d in params.items():
        flag = "guess" if d.get("vary") else "set  "
        err = d.get("stderr")
        err_s = f"+/- {err:.6g}" if err is not None and np.isfinite(err) else ""
        lines.append(f"  {name:8s} ({flag}) = {d['value']:.6g}  {err_s}")
    lines.append("")
    lines.append("--- Paths ---")
    lines.append(
        f"  {'name':22s} {'N':>8s} {'S02':>8s} {'sigma2':>10s} {'e0':>8s} "
        f"{'delR':>10s} {'Reff':>10s} {'R':>10s}"
    )
    for p in path_rows:
        lines.append(
            f"  {p['name'][:22]:22s} {p['N']:8.3f} {p['S02']:8.3f} {p['sigma2']:10.5f} "
            f"{p['e0']:8.3f} {p['delR']:10.5f} {p['Reff']:10.5f} {p['R']:10.5f}"
        )
    lines.append("")
    lines.append("--- Strong correlations (|c|>=0.4) ---")
    if corrs:
        for a, b, c in corrs:
            lines.append(f"  {a} & {b} --> {c:.3f}")
    else:
        lines.append("  (none reported)")
    lines.append("")
    lines.append("--- Validation vs Artemis Fit 92 ---")
    for k, v in validation.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("--- Raw larch feffit_report ---")
    lines.append(str(raw_report))
    lines.append("")
    return "\n".join(lines)


def _save_r_plot(r, data_mag, fit_mag, residual, path: Path, title: str = ""):
    # Use Figure + Agg canvas directly — matplotlib.use("Agg") at call time would
    # switch the global backend out from under the GUI's Qt canvas (and pyplot's
    # first import would happen on a worker thread).
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(7.5, 6.5))
    FigureCanvasAgg(fig)
    axes = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    ax = axes[0]
    if r is not None and len(r) and data_mag is not None and len(data_mag):
        ax.plot(r, data_mag, "k-", lw=1.4, label="data |χ(R)|")
    if r is not None and fit_mag is not None and len(fit_mag):
        rr = r if len(r) == len(fit_mag) else np.arange(len(fit_mag))
        ax.plot(rr, fit_mag, "r-", lw=1.4, label="fit |χ(R)|")
    ax.set_ylabel("|χ(R)|")
    ax.set_title(title or "R-space fit")
    ax.legend(loc="best")
    ax.set_xlim(0, 6)
    ax.grid(True, alpha=0.3)

    ax2 = axes[1]
    if residual is not None and r is not None and len(residual):
        rr = r if len(r) == len(residual) else np.arange(len(residual))
        ax2.plot(rr, residual, "b-", lw=1.0)
    ax2.axhline(0, color="gray", lw=0.8)
    ax2.set_xlabel("R (Å)")
    ax2.set_ylabel("residual")
    ax2.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)


def run_cli_validate(data_path: Optional[str] = None) -> int:
    """CLI entry: prepare paths, fit Model A, print validation, return 0 if overall PASS."""
    paths = project_paths()
    data_path = data_path or str(paths["demo_xdi"])
    print("smartLarch Platform — Fit 92 Model A validation")
    print(f"  data: {data_path}")
    print("  preparing FEFF paths…")
    try:
        prepared = prepare_model_a_paths(force=False)
        print(f"  path1: {prepared['path1']}")
        print(f"  path2: {prepared['path2']}")
    except Exception as exc:
        print(f"FEFF prepare failed: {exc}")
        return 2

    settings = FitSettings(
        path1_feff=str(prepared["path1"]),
        path2_feff=str(prepared["path2"]),
    )
    result = run_model_a_fit(data_path, settings=settings, prepare_paths=False)
    print(result.to_text())
    if not result.ok:
        return 1
    overall = (result.validation or {}).get("overall", "")
    return 0 if str(overall).startswith("PASS") else 3


if __name__ == "__main__":
    import sys

    sys.exit(run_cli_validate(sys.argv[1] if len(sys.argv) > 1 else None))


# =====================================================================
# Generic feffit engine (GDS params + arbitrary path list)
# =====================================================================


@dataclass
class GDSParam:
    """One row of the GDS (guess/def/set/skip) parameter table."""

    name: str
    value: float = 0.0
    kind: str = "vary"  # vary (guess) | fix (set) | expr (def) | skip
    expr: str = ""
    min: Optional[float] = None
    max: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "GDSParam":
        return GDSParam(**{k: d.get(k) for k in ("name", "value", "kind", "expr", "min", "max")})


@dataclass
class PathDef:
    """One feffNNNN.dat + parameter expressions (strings may reference GDS names)."""

    label: str = ""
    feff_file: str = ""
    use: bool = True
    s02: str = "1.0"
    e0: str = "0.0"
    deltar: str = "0.0"
    sigma2: str = "0.0"
    third: str = ""
    ei: str = ""
    degen: Optional[float] = None
    # absorber-element tag for group↔path assignment ("" = untagged/wildcard)
    elem: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PathDef":
        return PathDef(**{k: d.get(k) for k in ("label", "feff_file", "use", "s02", "e0", "deltar", "sigma2", "third", "ei", "degen", "elem")})


def model_a_preset() -> Tuple[List[GDSParam], List[PathDef]]:
    """The validated Fit 92 Model A as GDS + path rows (gold parameterisation)."""
    gds = [
        GDSParam("amp", 0.67, "fix"),
        GDSParam("e0", 8.0, "vary"),
        GDSParam("n1", 12.0, "vary", min=0.1, max=20),
        GDSParam("dr1", 0.0, "vary"),
        GDSParam("ss1", 0.003, "vary", min=1e-5, max=0.05),
        GDSParam("dr2", 0.0, "vary"),
        GDSParam("ss2", 0.008, "fix"),
    ]
    paths = project_paths()
    p1 = paths["feff_out"] / "path1_PtPt_2.788.dat"
    p2 = paths["feff_out"] / "path2_PtPt_3.052.dat"
    plist = [
        PathDef("Pt-Pt1", str(p1), True, "amp * n1", "e0", "dr1", "ss1", degen=1.0),
        PathDef("Pt-Pt2", str(p2), True, "amp", "e0", "dr2", "ss2", degen=2.0),
    ]
    return gds, plist


def run_feffit(
    group,
    gds: List[GDSParam],
    paths: List[PathDef],
    settings: Optional[FitSettings] = None,
) -> FitResult:
    """Generic EXAFS fit: GDS parameter table + arbitrary path expressions.

    ``group`` must be an Athena-processed larch Group (needs chi(k)).
    Parameter/path expressions may reference any GDS name (larch evaluates
    them in the fit namespace, like Artemis mathexp).
    """
    L = _require_larch()
    settings = settings or FitSettings()

    used = [p for p in paths if p.use and p.feff_file]
    if not used:
        raise ValueError("No usable paths (need ≥1 with use=True and a feff file)")
    active = [g for g in gds if g.kind != "skip" and g.name.strip()]
    if not active:
        raise ValueError("No active GDS parameters")

    try:
        # --- parameters ---
        pkw: Dict[str, Any] = {}
        for g in active:
            name = g.name.strip()
            if g.kind == "expr" and g.expr.strip():
                pkw[name] = L["param"](expr=g.expr.strip())
            elif g.kind == "fix":
                pkw[name] = L["param"](float(g.value), vary=False)
            else:
                vk: Dict[str, Any] = {}
                if g.min is not None:
                    vk["min"] = float(g.min)
                if g.max is not None:
                    vk["max"] = float(g.max)
                pkw[name] = L["param"](float(g.value), vary=True, **vk)
        pars = L["param_group"](**pkw)

        # --- paths ---
        feff_paths = []
        for pd in used:
            if not Path(pd.feff_file).is_file():
                raise FileNotFoundError(f"Missing FEFF path file: {pd.feff_file}")
            fp_kw: Dict[str, Any] = {
                "s02": pd.s02 or "1.0",
                "e0": pd.e0 or "0.0",
                "deltar": pd.deltar or "0.0",
                "sigma2": pd.sigma2 or "0.0",
            }
            if pd.third:
                fp_kw["third"] = pd.third
            if pd.ei:
                fp_kw["ei"] = pd.ei
            if pd.degen is not None:
                fp_kw["degen"] = float(pd.degen)
            feff_paths.append((pd, L["feffpath"](pd.feff_file, **fp_kw)))

        trans = L["feffit_transform"](
            kmin=settings.kmin,
            kmax=settings.kmax,
            dk=settings.dk,
            kw=list(settings.kw_list),
            window=settings.kwindow,
            fitspace=settings.fit_space,
            rmin=settings.rmin,
            rmax=settings.rmax,
            dr=settings.dr,
            rwindow=settings.rwindow,
        )
        dset = L["feffit_dataset"](group, [fp for _, fp in feff_paths], trans)
        out = L["feffit"](pars, dset)
        report = L["feffit_report"](out)

        # --- extract fitted params ---
        def _pv(name: str) -> Tuple[float, float]:
            params_obj = getattr(out, "params", None)
            if params_obj is None:
                return float("nan"), float("nan")
            try:
                p = params_obj[name]
            except Exception:
                p = getattr(params_obj, name, None)
            if p is None:
                return float("nan"), float("nan")
            try:
                val = float(p.value)
            except Exception:
                try:
                    val = float(p)
                except Exception:
                    return float("nan"), float("nan")
            try:
                err = float(p.stderr) if p.stderr is not None else float("nan")
            except Exception:
                err = float("nan")
            return val, err

        params_out: Dict[str, Any] = {}
        for g in active:
            v, e = _pv(g.name.strip())
            params_out[g.name.strip()] = {"value": v, "stderr": e, "kind": g.kind, "expr": g.expr}

        # --- statistics ---
        r_factor = float(getattr(out, "rfactor", getattr(out, "r_factor", float("nan"))))
        chi2 = float(getattr(out, "chi_square", getattr(out, "chi2", float("nan"))))
        redchi = float(getattr(out, "chi2_reduced", getattr(out, "redchi", float("nan"))))
        n_ind = float(getattr(out, "n_independent", float("nan")))
        if not np.isfinite(n_ind):
            n_ind = 2.0 * (settings.kmax - settings.kmin) * (settings.rmax - settings.rmin) / np.pi + 2.0
        if not np.isfinite(r_factor):
            try:
                r_factor = float(out.datasets[0].rfactor)
            except Exception:
                pass

        # --- correlations ---
        corrs: List[Tuple[str, str, float]] = []
        try:
            # lmfit Parameters has no `.correlations` — pairwise correl dicts
            # live on each varying Parameter; names from feffit's var_names
            var_names = list(getattr(out, "var_names", []) or [])
            for i, a in enumerate(var_names):
                for b in var_names[i + 1:]:
                    try:
                        c = out.params[a].correl.get(b)
                        if c is not None and abs(c) >= 0.1:
                            corrs.append((a, b, float(c)))
                    except Exception:
                        pass
        except Exception:
            pass

        # --- per-path evaluated values ---
        path_infos: List[Dict[str, Any]] = []
        for pd, fp in feff_paths:
            reff = float(getattr(fp, "reff", float("nan")))
            # evaluate path expressions against the fitted params — after the
            # fit fp.s02 etc. are still raw expr strings, so use larch's own
            # dict_report() (needs fp.params pointing at the result params)
            fp.params = out.params
            if getattr(fp, "dataset", None) is None:
                # feffit fits dataset-internal path copies, so our fp.dataset
                # stays None; path param names embed the dataset hashkey
                # (feffdat.pathpar_name) — mirror it or dict_report KeyErrors
                fp.dataset = getattr(dset, "hashkey", None)
            try:
                drep = fp.dict_report()
            except Exception:
                drep = {}

            def _fe(name: str):
                # dict_report "<name>_value" is the gformat string of the
                # *evaluated* expression; None when unavailable
                try:
                    return float(drep.get(f"{name}_value"))
                except Exception:
                    return None

            dr_v, dr_e = (0.0, 0.0)
            # map deltar expression → GDS param stderr when it is a bare name
            expr = (pd.deltar or "").strip()
            if expr in params_out:
                dr_v, dr_e = params_out[expr]["value"], params_out[expr]["stderr"]
            deltar_v = _fe("deltar")
            if deltar_v is not None:
                # evaluated deltar (works for any expr, not just bare names)
                r_v = reff + deltar_v if np.isfinite(reff) else float("nan")
            else:
                # fallback: only correct when deltar is a bare GDS param name
                r_v = reff + dr_v if np.isfinite(reff) else float("nan")
            path_infos.append(
                {
                    "label": pd.label or Path(pd.feff_file).stem,
                    "feff_file": pd.feff_file,
                    "reff": reff,
                    "degen": float(getattr(fp, "degen", pd.degen or float("nan"))),
                    "s02": _fe("s02"),
                    "e0": _fe("e0"),
                    "deltar": deltar_v,
                    "sigma2": _fe("sigma2"),
                    "r": r_v,
                    "r_err": dr_e,
                    "exprs": {"s02": pd.s02, "e0": pd.e0, "deltar": pd.deltar, "sigma2": pd.sigma2},
                }
            )

        # --- arrays for plotting (data vs model in k and R) ---
        def _arr(obj, name):
            v = getattr(obj, name, None)
            if v is None:
                return None
            a = np.asarray(v, dtype=float)
            return a.copy() if a.size else None

        ds0 = out.datasets[0]
        data, model = ds0.data, ds0.model
        fit_k = _arr(data, "k")
        data_chi = _arr(data, "chi")
        fit_chi = _arr(model, "chi")
        fit_r = _arr(data, "r")
        data_chir = _arr(data, "chir_mag")
        fit_chir = _arr(model, "chir_mag")
        resid_r = None
        if data_chir is not None and fit_chir is not None and len(data_chir) == len(fit_chir):
            resid_r = data_chir - fit_chir

        res = FitResult(ok=True, message="fit done")
        res.params = params_out
        res.statistics = {
            "r_factor": r_factor,
            "chi_square": chi2,
            "redchi": redchi,
            "n_independent": n_ind,
            "n_varys": int(getattr(out, "nvarys", 0) or 0),
        }
        res.paths = path_infos
        res.correlations = sorted(corrs, key=lambda x: -abs(x[2]))
        res.report_text = str(report)
        res.k = fit_k
        res.chi = data_chi
        res.fit_k = fit_k if fit_chi is not None else None
        res.fit_chi = fit_chi
        res.r = fit_r
        res.chir_mag = data_chir
        res.fit_r = fit_r
        res.fit_chir_mag = fit_chir
        res.residual_r = resid_r
        try:
            res.plot_views = build_fit_plot_views(
                fit_k,
                data_chi,
                fit_k,
                fit_chi,
                settings,
            )
            # Keep legacy/export fields on a coherent transformed pair too.
            # ``feffit`` itself stores only the first fitting k-weight, which
            # must never be compared against an Athena FT at another weight.
            preferred_weight = int(settings.kw_list[0]) if settings.kw_list else 2
            view = res.plot_views.get(preferred_weight) or next(iter(res.plot_views.values()), None)
            if view:
                res.r = view["r"]
                res.chir_mag = view["data_rmag"]
                res.fit_r = view["r"]
                res.fit_chir_mag = view["fit_rmag"]
                res.residual_r = view["residual_rre"]
        except Exception:
            # A fit result stays valid even if a display-only transform fails.
            # The UI will retain its basic k/R overlay and show the error-free
            # numerical report rather than turning a completed fit into failure.
            res.plot_views = {}
        return res
    except Exception as exc:
        res = FitResult(ok=False, message=f"{exc}")
        res.report_text = traceback.format_exc()
        return res


# =====================================================================
# Fit history (Artemis-style snapshots)
# =====================================================================

HISTORY_DIR = DEFAULT_OUTPUT / "history"


def save_fit_to_history(
    result: FitResult,
    gds: List[GDSParam],
    paths: List[PathDef],
    settings: FitSettings,
    data_desc: str = "",
    history_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Snapshot one fit (params/paths/settings/report) for later restore/compare."""
    if not result.ok:
        return None
    import datetime

    hdir = Path(history_dir or HISTORY_DIR)
    hdir.mkdir(parents=True, exist_ok=True)
    # microseconds: two fits in the same second must not overwrite each other
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    rf = result.statistics.get("r_factor", float("nan"))
    # Keep display arrays with the snapshot.  A report alone cannot redraw an
    # historical fit, while numpy arrays need explicit JSON conversion.
    plot_views = {
        str(weight): {
            str(name): np.asarray(values, dtype=float).tolist()
            for name, values in (view or {}).items()
        }
        for weight, view in (result.plot_views or {}).items()
    }
    snap = {
        "stamp": stamp,
        "data": data_desc,
        "gds": [g.to_dict() for g in gds],
        "paths": [p.to_dict() for p in paths],
        "settings": asdict(settings),
        "statistics": result.statistics,
        "params": result.params,
        "path_results": result.paths,
        "correlations": [list(c) for c in result.correlations],
        "report_text": result.report_text,
        "plot_views": plot_views,
    }
    fp = hdir / f"fit_{stamp}_R{rf:.4f}.json"
    fp.write_text(json.dumps(snap, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return fp


def list_fit_history(history_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Summary rows newest-first: stamp / data / r_factor / file."""
    hdir = Path(history_dir or HISTORY_DIR)
    rows = []
    if not hdir.is_dir():
        return rows
    for fp in sorted(hdir.glob("fit_*.json"), reverse=True):
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
            rows.append(
                {
                    "file": str(fp),
                    "stamp": d.get("stamp", fp.stem),
                    "data": d.get("data", ""),
                    "r_factor": d.get("statistics", {}).get("r_factor"),
                    "n_paths": len(d.get("paths", [])),
                    "has_curves": bool(d.get("plot_views")),
                }
            )
        except Exception:
            continue
    return rows


def clear_fit_history(history_dir: Optional[Path] = None) -> int:
    """Delete saved fit snapshots while preserving other output files."""
    hdir = Path(history_dir or HISTORY_DIR)
    if not hdir.is_dir():
        return 0
    removed = 0
    for fp in hdir.glob("fit_*.json"):
        try:
            fp.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def load_fit_snapshot(json_path: str | Path) -> Dict[str, Any]:
    """Full snapshot for restore: gds / paths / settings / report_text."""
    return json.loads(Path(json_path).read_text(encoding="utf-8"))
