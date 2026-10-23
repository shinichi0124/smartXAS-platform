"""One rules kernel for import → batch → Auto norm → AI Check (v3.19.5).

Qt-free on purpose: this module is the ONLY place where the program decides

  * whether a scan is bad (I1≈0, no fluorescence, aborted scan),
  * which measurement channel (transmission / fluorescence) to keep,
  * how to calibrate a scan on its reference foil (FIRST inflection),
  * whether repeat scans agree well enough to be merged,
  * whether a scan needs rebinning,
  * one common FT k window per element/edge set,
  * whether a normalization is acceptable, and what to retry when it is not,
  * what went into the processing record (Notes sheet / result table).

Evidence and thresholds: docs/VALIDATION.md → v3.19.5 and
output/2610_*_processed/process_*.py (the scripts that produced the delivered
Excel workbooks).  Do NOT copy these rules into main_window.py or
athena_ops.py — every entry point (import batch, Auto norm, AI Check apply,
Calibrate E0, smart merge) must call THIS file, so the first pass and the
second pass can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import athena_ops

# ---------------------------------------------------------------- thresholds
# Foil first-inflection calibration (evidence: Ti-K set 98007 foil 4964.2–4964.3 eV,
# benchtop Fe foil two derivative peaks at 7110.6 / 7118.8 eV, Ni 8330.7 / 8342.8:
# larch find_e0 picks the TALLER second peak and lands 8–10 eV high).
FOIL_GRID_EV = 0.1          # interpolation grid of the smoothed derivative
FOIL_SMOOTH_EV = 1.5        # boxcar width before differentiating
FOIL_SEARCH_LO = -15.0      # accepted first inflection = table − 15 … + 30 eV
FOIL_SEARCH_HI = 30.0
FOIL_PEAK_FRAC = 0.5        # a peak must reach 50 % of the window maximum
FOIL_CONTEXT_LO = -40.0     # interpolate from table − 40 eV …
FOIL_CONTEXT_HI = 60.0      # … to table + 60 eV

# Bad-scan screening.  98028/98029/98030: I1 ≈ 0.00–0.01 against 0.40–1.27 on
# the good scans and fluorescence 1.5k–3.5k against 90k–950k.  98358: aborted
# scan (I1 ≈ 0, fluorescence ≈ 0, 383 of 709 points usable).
SCAN_I1_MIN_FRAC = 0.05     # I1 median below 5 % of the batch median → bad
SCAN_FLUO_MIN_FRAC = 0.10   # post-edge fluorescence below 10 % → bad
SCAN_POINTS_MIN_FRAC = 0.80  # usable points below 80 % of the batch → bad

# Channel choice (transmission vs fluorescence self-absorption).  White-line
# ratio fluo/trans on Ti-K set 1/2/3/6 was 0.956 / 0.946 / 0.924 / 0.879 (dense
# samples, self-absorbed); Ti-K set 4/5 were 1.029 / 0.990.
CHANNEL_WHITE_RATIO_MIN = 0.97   # fluo white line < 97 % of trans → trans
CHANNEL_NOISE_FACTOR = 1.5       # fluo noise > 1.5× trans noise → trans
CHANNEL_TRANS_STEP_MIN = 0.05    # trans edge step below this is not usable
CHANNEL_NOISE_K_LO = 9.0         # noise is judged on k 9–12 Å⁻¹
CHANNEL_NOISE_K_HI = 12.0
CHANNEL_PRE_LO = -150.0          # normalization window used for the comparison
CHANNEL_PRE_HI = -30.0
CHANNEL_POST_LO = 50.0
CHANNEL_POST_HI = 400.0
CHANNEL_WHITE_LO = 0.0           # white line = max of normalized μ over …
CHANNEL_WHITE_HI = 60.0          # … E0 … E0+60 eV

# Repeat-scan agreement.  Ti-K set 1/2/3 = 0.0021–0.0037, mixed set 2 Mn = 0.0009 after both
# scans got the SAME normalization window (different windows read 0.071).
REPEAT_PRE_HI = -30.0
REPEAT_POST_LO = 60.0       # post-edge window = +60 … +400 eV
REPEAT_POST_HI = 400.0
REPEAT_RMS_MERGE_MAX = 0.01  # rms of (A − B) after flatten; < 1 % → merge

# Rebinning: MEX1 slew scans are 6320 points at 0.05 eV; step scans (benchtop 359
# points, W L3 709 points) keep their measured grid.
REBIN_MIN_POINTS = 2000
REBIN_MIN_STEP_EV = 0.2

# One FT k window per element/edge set so |χ(R)| peak heights compare.
UNIFORM_FT_KMIN = 2.0
UNIFORM_K_FLOOR = 0.5       # kmax is rounded DOWN to this step

# Normalization self-check on the flattened spectrum.
NORM_CHECK_TOL = 0.05       # |value| tolerance for 0 (pre) and 1 (post)
NORM_CHECK_PRE_EV = -30.0   # everything below E0−30 eV must be ~0
NORM_CHECK_FAR_EV = -150.0  # the far pre-edge point(s) must be ~0 as well
NORM_CHECK_POST_EV = 150.0  # everything above E0+150 eV must be ~1

# Retry ladder when the check fails (reference scripts used exactly these).
RETRY_MAX = 2


# ------------------------------------------------------------------- helpers
def _f(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def _sorted_xy(energy: Any, mu: Any) -> Tuple[np.ndarray, np.ndarray]:
    e = np.asarray(energy, dtype=float).ravel()
    m = np.asarray(mu, dtype=float).ravel()
    n = min(e.size, m.size)
    e, m = e[:n], m[:n]
    ok = np.isfinite(e) & np.isfinite(m)
    e, m = e[ok], m[ok]
    order = np.argsort(e)
    return e[order], m[order]


def _result(ok: bool, value: Any = None, *, reason_zh: str = "", reason_en: str = "",
            **widgets: Any) -> Dict[str, Any]:
    """Uniform return: decision + the numbers behind it + a bilingual reason."""
    out: Dict[str, Any] = {
        "ok": bool(ok),
        "value": value,
        "reason_zh": reason_zh,
        "reason_en": reason_en,
    }
    out.update(widgets)
    return out


def _fmt(value: Any, digits: int = 3) -> str:
    v = _f(value)
    return "n/a" if v is None else f"{v:.{digits}f}"


# ------------------------------------------------------- normalization helper
def pre_edge_normalize(
    energy: Any,
    mu: Any,
    e0: float,
    *,
    pre1: float = -150.0,
    pre2: float = -30.0,
    norm1: float = 50.0,
    norm2: float = 400.0,
    nnorm: int = 2,
    nvict: int = 0,
) -> Optional[Dict[str, np.ndarray]]:
    """Numpy pre-edge line + post-edge polynomial normalization (larch-free).

    Same convention as larch pre_edge for the pieces this project needs (a
    linear or Victoreen E^-nvict pre-edge term, a post-edge polynomial in
    (E − E0)), so the numbers used to JUDGE a normalization match the ones the
    GUI shows afterwards.

    Returns a dict with energy/mu/pre/post/norm/step, or None when the windows
    have too few points to be judged.
    """
    e, m = _sorted_xy(energy, mu)
    e0v = _f(e0)
    if e.size < 8 or e0v is None:
        return None
    pre_lo, pre_hi = e0v + float(pre1), e0v + float(pre2)
    post_lo, post_hi = e0v + float(norm1), e0v + float(norm2)
    pre_m = (e >= pre_lo) & (e <= pre_hi) & np.isfinite(m)
    if int(pre_m.sum()) < 3:
        return None
    nvict = int(max(0, min(int(nvict or 0), 3)))
    if nvict > 0 and (pre_hi - pre_lo) >= 20.0:
        basis = np.vstack([np.power(e[pre_m], -float(nvict)), np.ones(pre_m.sum())]).T
        try:
            coef, *_ = np.linalg.lstsq(basis, m[pre_m], rcond=None)
        except np.linalg.LinAlgError:
            return None
        pre = coef[0] * np.power(e, -float(nvict)) + coef[1]
    else:
        try:
            coef = np.polyfit(e[pre_m], m[pre_m], 1)
        except (np.linalg.LinAlgError, ValueError):
            return None
        pre = np.polyval(coef, e)
    above = m - pre
    post_m = (e >= post_lo) & (e <= post_hi) & np.isfinite(above)
    if int(post_m.sum()) < 3:
        post_m = e > e0v
    if int(post_m.sum()) < 3:
        return None
    deg = int(max(0, min(int(nnorm or 0), 3)))
    x = e[post_m] - e0v
    try:
        pcoef = np.polyfit(x, above[post_m], deg)
    except (np.linalg.LinAlgError, ValueError):
        return None
    post = np.polyval(pcoef, e - e0v)
    step = float(np.median(post[post_m]))
    if not np.isfinite(step) or abs(step) < 1e-12:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        norm = np.where(np.abs(post) > abs(step) * 0.05, above / post, np.nan)
    return {
        "energy": e,
        "mu": m,
        "pre": pre,
        "post": post,
        "norm": norm,
        "step": step,
        "e0": float(e0v),
    }


# ------------------------------------------------------- 1. foil calibration
def foil_first_inflection(
    energy: Any,
    mu: Any,
    table_e0: float,
    *,
    smooth_ev: float = FOIL_SMOOTH_EV,
) -> Dict[str, Any]:
    """Reference-foil E0 = LOWEST-energy dμ/dE maximum near the tabulated edge.

    Ported from process_cx_dyk.first_inflection (which produced the delivered
    ΔE values): interpolate on a 0.1 eV grid, boxcar-smooth 1.5 eV,
    differentiate, then take the lowest-energy local maximum inside
    [table − 15, table + 30] eV that reaches ≥ 50 % of that window's largest
    derivative.  On the benchtop foils this picks 7110.6 / 8330.7 eV where larch
    find_e0 picks the taller 7118.8 / 8342.8 eV peak.
    """
    e, m = _sorted_xy(energy, mu)
    tab = _f(table_e0)
    if tab is None or e.size < 20:
        return _result(
            False, None,
            reason_zh="参考箔数据不足或缺少表值，未校准",
            reason_en="Not enough reference data or no table value; not calibrated",
        )
    lo = max(float(e[0]), tab + FOIL_CONTEXT_LO)
    hi = min(float(e[-1]), tab + FOIL_CONTEXT_HI)
    if hi - lo < 5.0:
        return _result(
            False, None,
            reason_zh="参考箔能量范围不足 ±5 eV，未校准",
            reason_en="Reference channel covers < 5 eV near the edge; not calibrated",
            window=[lo, hi],
        )
    grid = np.arange(lo, hi, FOIL_GRID_EV)
    if grid.size < 20:
        return _result(False, None, reason_zh="参考箔网格过稀", reason_en="Reference grid too sparse")
    interp = np.interp(grid, e, m)
    n = max(3, int(round(float(smooth_ev) / FOIL_GRID_EV)))
    smooth = np.convolve(interp, np.ones(n) / n, mode="same")
    deriv = np.gradient(smooth, grid)
    sel = np.where((grid > tab + FOIL_SEARCH_LO) & (grid < tab + FOIL_SEARCH_HI))[0]
    if sel.size < 3:
        return _result(False, None, reason_zh="拐点搜索窗内无数据", reason_en="No data in the inflection search window")
    dmax = float(np.max(deriv[sel]))
    if not np.isfinite(dmax) or dmax <= 0:
        return _result(
            False, None,
            reason_zh="边附近导数无正峰（通道或列选错？）",
            reason_en="No positive derivative peak near the edge (wrong channel/column?)",
        )
    peaks = [
        int(i) for i in sel[1:-1]
        if deriv[i] >= deriv[i - 1] and deriv[i] >= deriv[i + 1]
        and deriv[i] >= FOIL_PEAK_FRAC * dmax
    ]
    if not peaks:
        return _result(
            False, None,
            reason_zh="未找到第一拐点峰，退回 find_e0",
            reason_en="No first-inflection peak found; fall back to find_e0",
            dmax=dmax,
        )
    idx = min(peaks)  # lowest-energy qualifying peak = first inflection
    e_inf = float(grid[idx])
    return _result(
        True, e_inf,
        reason_zh=f"参考箔第一拐点 {e_inf:.2f} eV（表值 {tab:.1f} eV）",
        reason_en=f"Reference first inflection at {e_inf:.2f} eV (table {tab:.1f} eV)",
        table_e0=float(tab),
        peak_height=float(deriv[idx]),
        window_max=float(dmax),
        n_peaks=len(peaks),
    )


def reference_shift(energy_ref: Any, mu_ref: Any, element: str, edge: str) -> Dict[str, Any]:
    """ΔE = table − first inflection, from the reference channel of one scan."""
    tab = athena_ops.edge_table_energy(str(element or ""), str(edge or "K"))
    if tab is None:
        return _result(
            False, None,
            reason_zh=f"元素 {element or '?'} {edge or 'K'} 无表值，未校准",
            reason_en=f"No tabulated energy for {element or '?'} {edge or 'K'}; not calibrated",
            element=str(element or ""), edge=str(edge or "K"),
        )
    inf = foil_first_inflection(energy_ref, mu_ref, float(tab))
    if not inf.get("ok"):
        out = dict(inf)
        out.update({"element": str(element or ""), "edge": str(edge or "K"), "table_e0": float(tab)})
        out["value"] = None
        return out
    e_inf = float(inf["value"])
    delta = float(tab) - e_inf
    return _result(
        True, delta,
        reason_zh=f"{element} {edge} 第一拐点 {e_inf:.2f} eV → ΔE {delta:+.2f} eV",
        reason_en=f"{element} {edge} first inflection {e_inf:.2f} eV → ΔE {delta:+.2f} eV",
        element=str(element or ""), edge=str(edge or "K"),
        table_e0=float(tab), e_inflection=e_inf,
        peak_height=inf.get("peak_height"),
    )


# --------------------------------------------------------- 2. scan quality
def scan_quality(stats: Dict[str, Any], siblings: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Bad-scan screen against the rest of the batch (same element/edge).

    stats: {"label", "i0", "it", "ifl", "iref", "n_points", "n_declared"} with
    MEDIAN detector readings.  siblings: the same dicts for the other scans of
    the batch.  A scan is bad when

      * its I1 (transmission signal) median is below 5 % of the batch median
        (beam missed the sample: 98028/98029/98030),
      * its fluorescence median is below 10 % of the batch median,
      * it has fewer than 80 % of the batch's / header's points (aborted scan:
        98358 has 383 of 709 usable).

    Advisory only: the caller unmarks the group and writes the reason; it never
    deletes data (plan 2/T3.4).
    """
    reasons_zh: List[str] = []
    reasons_en: List[str] = []
    values: Dict[str, Any] = {}
    sib = [s for s in (siblings or []) if isinstance(s, dict)]

    def _batch_median(key: str) -> Optional[float]:
        # ONE sibling is enough: a two-scan set (mixed set 1 W L3: one aborted + one
        # good) must still be screened, otherwise the aborted scan ships.
        vals = [v for v in (_f(s.get(key)) for s in sib) if v is not None]
        vals = [v for v in vals if abs(v) > 0]
        return float(np.median(vals)) if vals else None

    it = _f(stats.get("it"))
    it_base = _batch_median("it")
    if it is not None and it_base:
        values["i1_ratio"] = it / it_base
        if abs(it) < abs(it_base) * SCAN_I1_MIN_FRAC:
            reasons_zh.append(
                f"I1 中位数 {it:.3g} 只有同批 {it_base:.3g} 的 {100 * abs(it / it_base):.1f}%（光没打到样品）"
            )
            reasons_en.append(
                f"I1 median {it:.3g} is {100 * abs(it / it_base):.1f}% of the batch "
                f"({it_base:.3g}) — the beam missed the sample"
            )
    fl = _f(stats.get("ifl"))
    fl_base = _batch_median("ifl")
    if fl is not None and fl_base:
        values["fluo_ratio"] = fl / fl_base
        if abs(fl) < abs(fl_base) * SCAN_FLUO_MIN_FRAC:
            reasons_zh.append(f"荧光强度 {fl:.3g} 只有同批的 {100 * abs(fl / fl_base):.1f}%")
            reasons_en.append(
                f"Fluorescence {fl:.3g} is {100 * abs(fl / fl_base):.1f}% of the batch"
            )
    n = _f(stats.get("n_points"))
    n_declared = _f(stats.get("n_declared"))
    # Point count only compares within the same scan FAMILY: a 709-point step
    # scan next to 6319-point slew scans is a different measurement, not an
    # aborted one (AS1_WL3_98359 was flagged before this narrowing).
    family = [
        v for v in (_f(s.get("n_points")) for s in sib)
        if v and n and (n / 4.0) <= v <= (n * 4.0)
    ]
    n_base = n_declared
    if n_base is None and len(family) >= 2:
        n_base = float(np.median(family))
    if n is not None and n_base:
        values["point_ratio"] = n / n_base
        if n < n_base * SCAN_POINTS_MIN_FRAC:
            reasons_zh.append(f"可用点数 {int(n)} 只有 {int(n_base)} 的 {100 * n / n_base:.0f}%（扫描中断）")
            reasons_en.append(
                f"Only {int(n)} of {int(n_base)} points ({100 * n / n_base:.0f}%) — aborted scan"
            )
    ok = not reasons_zh
    return _result(
        ok,
        ("ok" if ok else "bad"),
        reason_zh="质量检查通过" if ok else "；".join(reasons_zh),
        reason_en="Quality check passed" if ok else "; ".join(reasons_en),
        **values,
    )


def raw_channel_medians(
    path: Any,
    spec: Dict[str, Any],
    *,
    label: str = "",
) -> Dict[str, Any]:
    """Median detector readings read straight from the raw file.

    scan_quality needs I0/I1/I2/Ifl medians, which the derived μ arrays no
    longer carry (μ = ln(I0/I1) is already a ratio).  The parse is the same
    one load_energy_mu_with_spec does, so it costs one extra file read per
    scan in the batch path only.
    """
    from pathlib import Path as _Path

    p = _Path(str(path))
    rows = athena_ops._parse_numeric_rows(p)
    if len(rows) < 3:
        return raw_column_stats(np.array([]), {}, label=label)
    width = max(len(r) for r in rows)
    arr = np.full((len(rows), width), np.nan, dtype=float)
    for i, r in enumerate(rows):
        arr[i, : len(r)] = r
    e_col = int(spec.get("e_col") or 0)
    e = arr[:, e_col] if e_col < width else np.array([])
    if e.size:
        try:
            labels, _n, _s = athena_ops.sniff_raw_columns(str(p))
            e = e * athena_ops._energy_scale_from_label(
                labels[e_col] if e_col < len(labels) else "", e
            )
        except Exception:
            pass
    columns: Dict[str, Any] = {}
    for role, key in (("i0", "i0_col"), ("it", "it_col"),
                      ("ifl", "fl_col"), ("iref", "ref_col")):
        idx = spec.get(key)
        if idx is None:
            continue
        idx = int(idx)
        if 0 <= idx < width:
            columns[role] = arr[:, idx]
    emin, emax = spec.get("emin"), spec.get("emax")
    m = np.isfinite(e)
    if emin is not None:
        m &= e >= float(emin)
    if emax is not None:
        m &= e <= float(emax)
    n_used = int(np.count_nonzero(m))
    if n_used == 0:
        m = np.isfinite(e)
        n_used = int(np.count_nonzero(m))
    columns = {k: v[m] for k, v in columns.items()}
    stats = raw_column_stats(e[m], columns, label=label)
    stats["n_rows"] = int(len(rows))
    return stats


def raw_column_stats(
    energy: Any,
    columns: Dict[str, Any],
    *,
    declared_points: Optional[float] = None,
    label: str = "",
) -> Dict[str, Any]:
    """Medians of the detector columns used by scan_quality.

    columns maps role → array (i0 / it / ifl / iref); the caller has already
    read the raw file.
    """
    out: Dict[str, Any] = {
        "label": str(label or ""),
        "n_points": float(np.asarray(energy, dtype=float).ravel().size),
        "n_declared": _f(declared_points),
    }
    for role, arr in (columns or {}).items():
        a = np.asarray(arr, dtype=float).ravel()
        a = a[np.isfinite(a)]
        out[str(role)] = float(np.median(a)) if a.size else None
    return out


# ------------------------------------------------------- 3. channel choice
def _white_line_and_step(norm_arrays: Optional[Dict[str, Any]],
                         e0: float) -> Tuple[Optional[float], Optional[float]]:
    if not norm_arrays:
        return None, None
    e = norm_arrays["energy"]
    norm = norm_arrays["norm"]
    ok = np.isfinite(e) & np.isfinite(norm)
    if not np.any(ok):
        return None, None
    wl = ok & (e >= e0 + CHANNEL_WHITE_LO) & (e <= e0 + CHANNEL_WHITE_HI)
    white = float(np.nanmax(norm[wl])) if np.any(wl) else None
    return white, _f(norm_arrays.get("step"))


def _high_k_noise(e: np.ndarray, norm: np.ndarray, e0: float) -> Optional[float]:
    """Noise at k = 9–12 Å⁻¹ from the 2nd difference of the normalized μ.

    Both channels pass through the same k² weighting afterwards, so the
    weighting cancels in the fluo/trans ratio; the 2nd difference removes the
    smooth EXAFS oscillation and leaves counting noise (this reproduces the
    "7–20× noisier" observation of the processing record).
    """
    e_lo = e0 + athena_ops.KTOE_EV_A2 * CHANNEL_NOISE_K_LO ** 2
    e_hi = e0 + athena_ops.KTOE_EV_A2 * CHANNEL_NOISE_K_HI ** 2
    m = np.isfinite(e) & np.isfinite(norm) & (e >= e_lo) & (e <= e_hi)
    if int(m.sum()) < 12:
        return None
    x, y = e[m], norm[m]
    steps = np.diff(x)
    steps = steps[steps > 0]
    if steps.size == 0:
        return None
    grid = np.arange(x[0], x[-1], max(float(np.median(steps)), 1e-3))
    if grid.size < 12:
        return None
    yi = np.interp(grid, x, y)
    d2 = np.diff(yi, 2)
    if d2.size < 3:
        return None
    return float(np.sqrt(np.mean(d2 ** 2)))


def choose_channel(
    energy: Any,
    mu_trans: Any,
    mu_fluo: Any,
    e0: float,
    *,
    pre1: float = CHANNEL_PRE_LO,
    pre2: float = CHANNEL_PRE_HI,
    norm1: float = CHANNEL_POST_LO,
    norm2: float = CHANNEL_POST_HI,
) -> Dict[str, Any]:
    """Transmission or fluorescence for ONE scan (self-absorption screen).

    Both channels are normalized on the same windows, then the white-line ratio
    r = white(fluo) / white(trans) and the k 9–12 Å⁻¹ noise decide:

      * r < 0.97                                                → transmission
      * trans edge step < 0.05 and r >= 0.97 and fluo not noisier → fluorescence
      * otherwise                                               → transmission
    """
    e0v = _f(e0)
    default = "trans"
    if e0v is None:
        return _result(False, default, reason_zh="无 E0，默认透射", reason_en="No E0; default to transmission")
    nt = pre_edge_normalize(energy, mu_trans, e0v, pre1=pre1, pre2=pre2, norm1=norm1, norm2=norm2)
    nf = (
        pre_edge_normalize(energy, mu_fluo, e0v, pre1=pre1, pre2=pre2, norm1=norm1, norm2=norm2)
        if mu_fluo is not None else None
    )
    white_t, step_t = _white_line_and_step(nt, e0v)
    white_f, step_f = _white_line_and_step(nf, e0v)
    values: Dict[str, Any] = {
        "white_trans": white_t, "white_fluo": white_f,
        "step_trans": step_t, "step_fluo": step_f,
    }
    if nf is None or not white_f or not white_t:
        return _result(
            True, default,
            reason_zh="只有透射通道（或无荧光列）→ 透射",
            reason_en="Transmission only (or no fluorescence column) → transmission",
            **values,
        )
    ratio = float(white_f / white_t)
    values["white_ratio"] = ratio
    noise_t = _high_k_noise(nt["energy"], nt["norm"], e0v) if nt else None
    noise_f = _high_k_noise(nf["energy"], nf["norm"], e0v)
    noise_ratio = float(noise_f / noise_t) if (noise_t and noise_f is not None) else None
    values["noise_trans"] = noise_t
    values["noise_fluo"] = noise_f
    values["noise_ratio"] = noise_ratio
    if ratio < CHANNEL_WHITE_RATIO_MIN:
        return _result(
            True, default,
            reason_zh=f"荧光白线只有透射的 {ratio:.3f}×（自吸收）→ 用透射",
            reason_en=(
                f"Fluorescence white line is {ratio:.3f}× the transmission one "
                "(self-absorption) → transmission"
            ),
            **values,
        )
    trans_weak = (step_t is None) or (abs(step_t) < CHANNEL_TRANS_STEP_MIN)
    if trans_weak and (noise_ratio is None or noise_ratio <= CHANNEL_NOISE_FACTOR):
        return _result(
            True, "fluo",
            reason_zh=(
                f"透射边跃 {_fmt(step_t)} 太弱、荧光白线比 {ratio:.3f}、"
                f"高 k 噪声比 {_fmt(noise_ratio, 2)} → 用荧光"
            ),
            reason_en=(
                f"Transmission edge step {_fmt(step_t)} is too weak, white-line ratio "
                f"{ratio:.3f}, high-k noise ratio {_fmt(noise_ratio, 2)} → fluorescence"
            ),
            **values,
        )
    if noise_ratio is not None and noise_ratio > CHANNEL_NOISE_FACTOR:
        return _result(
            True, default,
            reason_zh=f"荧光在 k 9–12 Å⁻¹ 的噪声是透射的 {noise_ratio:.1f}× → 用透射",
            reason_en=f"Fluorescence is {noise_ratio:.1f}× noisier at k 9–12 Å⁻¹ → transmission",
            **values,
        )
    return _result(
        True, default,
        reason_zh=f"白线比 {ratio:.3f}、噪声比 {_fmt(noise_ratio, 2)} → 用透射",
        reason_en=f"White-line ratio {ratio:.3f}, noise ratio {_fmt(noise_ratio, 2)} → transmission",
        **values,
    )


def choose_set_channel(
    decisions: Sequence[Dict[str, Any]],
    *,
    labels: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """One channel for a whole element/edge set: the majority vote.

    Comparing samples only makes sense when they were measured the same way,
    so a single dissenting scan follows the set (its own numbers stay in the
    processing record).
    """
    picks = [str(d.get("value") or "trans") for d in (decisions or []) if isinstance(d, dict)]
    if not picks:
        return _result(False, "trans", reason_zh="无通道判据", reason_en="No channel decision")
    n_fluo = sum(1 for p in picks if p == "fluo")
    n_trans = len(picks) - n_fluo
    channel = "fluo" if n_fluo > n_trans else "trans"
    dissent = [
        str((labels or [])[i]) if labels and i < len(labels) else f"#{i + 1}"
        for i, p in enumerate(picks) if p != channel
    ]
    word_zh = "荧光" if channel == "fluo" else "透射"
    word_en = "fluorescence" if channel == "fluo" else "transmission"
    return _result(
        True, channel,
        reason_zh=(
            f"整套统一用{word_zh}（{max(n_fluo, n_trans)}/{len(picks)}）"
            + (f"；个别判据不同：{', '.join(dissent)}" if dissent else "")
        ),
        reason_en=(
            f"Whole set uses {word_en} ({max(n_fluo, n_trans)}/{len(picks)})"
            + (f"; differing scans: {', '.join(dissent)}" if dissent else "")
        ),
        n_fluo=n_fluo, n_trans=n_trans, dissent=dissent,
    )


# ------------------------------------------------------ 4. repeat agreement
def repeat_consistency(
    energy_a: Any,
    mu_a: Any,
    e0_a: float,
    energy_b: Any,
    mu_b: Any,
    e0_b: float,
) -> Dict[str, Any]:
    """rms of two flattened spectra normalized on IDENTICAL windows.

    Forcing the same window is the whole point: with each scan's own
    auto-normalization window Ti-K set 3's repeats read 0.071; with one fixed window
    they read 0.0026.  Below REPEAT_RMS_MERGE_MAX the repeats may be averaged.
    """
    e0a, e0b = _f(e0_a), _f(e0_b)
    if e0a is None or e0b is None:
        return _result(
            False, None,
            reason_zh="缺少 E0，无法比较重复扫描",
            reason_en="No E0; cannot compare repeats",
        )

    def _flat(e: Any, m: Any, e0: float) -> Optional[Dict[str, Any]]:
        e_arr, _m = _sorted_xy(e, m)
        if e_arr.size < 8:
            return None
        pre1 = float(e_arr[0]) - e0  # from the scan start …
        if pre1 > REPEAT_PRE_HI - 10.0:
            return None
        return pre_edge_normalize(
            e, m, e0,
            pre1=pre1, pre2=REPEAT_PRE_HI,
            norm1=REPEAT_POST_LO, norm2=REPEAT_POST_HI, nnorm=2, nvict=0,
        )

    fa = _flat(energy_a, mu_a, e0a)
    fb = _flat(energy_b, mu_b, e0b)
    if fa is None or fb is None:
        return _result(
            False, None,
            reason_zh="重复扫描无法用统一窗口归一化（预边过短）",
            reason_en="Repeats cannot be normalized on a common window (pre-edge too short)",
        )
    ea, na = fa["energy"], fa["norm"]
    eb, nb = fb["energy"], fb["norm"]
    lo = max(float(np.nanmin(ea)), float(np.nanmin(eb)))
    hi = min(float(np.nanmax(ea)), float(np.nanmax(eb)))
    if not (hi > lo):
        return _result(False, None, reason_zh="两条扫描无重叠能量区", reason_en="Repeats do not overlap")
    step = float(np.median(np.diff(ea))) if ea.size > 1 else 0.2
    grid = np.arange(lo, hi, max(0.2, step))
    if grid.size < 8:
        return _result(False, None, reason_zh="重叠区太窄", reason_en="Overlap too narrow")
    a = np.interp(grid, ea, na)
    b = np.interp(grid, eb, nb)
    ok = np.isfinite(a) & np.isfinite(b)
    if int(ok.sum()) < 8:
        return _result(False, None, reason_zh="重叠区无可比点", reason_en="No comparable points")
    rms = float(np.sqrt(np.mean((a[ok] - b[ok]) ** 2)))
    mergeable = rms < REPEAT_RMS_MERGE_MAX
    return _result(
        mergeable, rms,
        reason_zh=(
            f"重复扫描一致（rms {rms:.4f} < {REPEAT_RMS_MERGE_MAX}）→ 可合并"
            if mergeable else
            f"重复扫描差异 rms {rms:.4f} ≥ {REPEAT_RMS_MERGE_MAX} → 不自动合并"
        ),
        reason_en=(
            f"Repeats agree (rms {rms:.4f} < {REPEAT_RMS_MERGE_MAX}) → mergeable"
            if mergeable else
            f"Repeats differ (rms {rms:.4f} ≥ {REPEAT_RMS_MERGE_MAX}) → do not auto-merge"
        ),
        points=int(ok.sum()),
        window=[float(lo), float(hi)],
    )


def _e0_of(group: Any) -> Optional[float]:
    """E0 for rule work: the pinned E0, else the processed E0, else None."""
    s = getattr(group, "settings", None)
    e0 = _f(getattr(s, "e0", None)) if s is not None else None
    if e0 is not None:
        return e0
    sess = getattr(group, "session", None)
    arrs = getattr(sess, "arrays", None) if sess is not None else None
    return _f(getattr(arrs, "e0", None)) if arrs is not None else None


def e0_or_estimate(group: Any) -> Optional[float]:
    """E0 for rules that run BEFORE Auto norm has picked one.

    Repeat consistency is judged right after import, when a group has neither
    a pinned E0 nor a processed spectrum.  Falling back to the tabulated edge
    energy (then to the derivative maximum of the raw data) keeps the
    comparison anchored at the edge instead of failing with "no E0".
    """
    e0 = _e0_of(group)
    if e0 is not None:
        return e0
    tab = athena_ops.edge_table_energy(
        str(getattr(group, "element", "") or ""), str(getattr(group, "edge", "") or "K")
    )
    if tab is not None:
        return float(tab)
    e, m = _sorted_xy(getattr(group, "energy", None), getattr(group, "mu", None))
    if e.size < 8:
        return None
    deriv = np.gradient(m, e)
    return float(e[int(np.argmax(deriv))])


def group_repeat_consistency(groups: Sequence[Any]) -> Dict[str, Any]:
    """repeat_consistency for a list of 2+ GroupRecord-like objects."""
    gs = [g for g in (groups or []) if getattr(g, "energy", None) is not None]
    if len(gs) < 2:
        return _result(False, None, reason_zh="少于两条扫描", reason_en="Fewer than two scans")
    worst: Optional[float] = None
    worst_pair: Tuple[str, str] = ("", "")
    n_ok = 0
    details: List[Dict[str, Any]] = []
    for i in range(len(gs)):
        for j in range(i + 1, len(gs)):
            a, b = gs[i], gs[j]
            res = repeat_consistency(
                a.energy, a.mu, e0_or_estimate(a),
                b.energy, b.mu, e0_or_estimate(b),
            )
            details.append({"pair": [a.name, b.name], **res})
            v = _f(res.get("value"))
            if v is None:
                continue
            if res.get("ok"):
                n_ok += 1
            if worst is None or v > worst:
                worst, worst_pair = v, (a.name, b.name)
    mergeable = worst is not None and worst < REPEAT_RMS_MERGE_MAX
    return _result(
        mergeable, worst,
        reason_zh=(
            f"最差一对 {worst_pair[0]}/{worst_pair[1]} rms {_fmt(worst, 4)}"
            + ("；可合并" if mergeable else "；不建议合并")
        ),
        reason_en=(
            f"Worst pair {worst_pair[0]}/{worst_pair[1]} rms {_fmt(worst, 4)}"
            + ("; mergeable" if mergeable else "; do not merge")
        ),
        pairs=details, pairs_ok=n_ok, n_pairs=len(details),
    )


# ----------------------------------------------------------- 5. rebin need
def needs_rebin(energy: Any) -> Dict[str, Any]:
    """Slew scans (very dense grid) are rebinned; step scans are not."""
    e = np.asarray(energy, dtype=float).ravel()
    e = e[np.isfinite(e)]
    if e.size < 3:
        return _result(False, False, reason_zh="点数不足", reason_en="Too few points", n_points=int(e.size))
    order = np.argsort(e)
    steps = np.diff(e[order])
    steps = steps[steps > 0]
    step = float(np.median(steps)) if steps.size else float("nan")
    too_many = e.size > REBIN_MIN_POINTS
    too_dense = np.isfinite(step) and step < REBIN_MIN_STEP_EV
    need = bool(too_many or too_dense)
    if need:
        why_zh = []
        why_en = []
        if too_many:
            why_zh.append(f"{e.size} 点 > {REBIN_MIN_POINTS}")
            why_en.append(f"{e.size} points > {REBIN_MIN_POINTS}")
        if too_dense:
            why_zh.append(f"中位步长 {step:.3f} eV < {REBIN_MIN_STEP_EV} eV")
            why_en.append(f"median step {step:.3f} eV < {REBIN_MIN_STEP_EV} eV")
        return _result(
            True, True,
            reason_zh="需 rebin：" + "、".join(why_zh),
            reason_en="Rebin needed: " + ", ".join(why_en),
            n_points=int(e.size), step=step,
        )
    return _result(
        True, False,
        reason_zh=f"步进扫描（{e.size} 点，步长 {_fmt(step, 3)} eV）→ 不 rebin",
        reason_en=f"Step scan ({e.size} points, {_fmt(step, 3)} eV) → no rebin",
        n_points=int(e.size), step=step,
    )


# --------------------------------------------------- 6. one FT k window
def uniform_ft_window(groups: Sequence[Any], *, kmin: float = UNIFORM_FT_KMIN) -> Dict[str, Any]:
    """Smallest common k window for one element/edge set.

    kmax = min over the members' suggest_ft_k_window results, rounded DOWN to
    0.5 Å⁻¹ and capped by every member's background bkg_kmax so the FT never
    uses k the background spline did not see.
    """
    members: List[Dict[str, Any]] = []
    for g in groups or []:
        sess = getattr(g, "session", None)
        arrs = getattr(sess, "arrays", None) if sess is not None else None
        k = np.asarray(getattr(arrs, "k", None) if arrs is not None else None, dtype=float)
        chi = np.asarray(getattr(arrs, "chi", None) if arrs is not None else None, dtype=float)
        if k.size == 0 or chi.size == 0:
            continue
        n = min(k.size, chi.size)
        if n < 8:
            continue
        win = athena_ops.suggest_ft_k_window(k[:n], chi[:n])
        bkg_cap = _f(getattr(getattr(g, "settings", None), "bkg_kmax", None))
        kmax = float(win["kmax"])
        if bkg_cap is not None:
            kmax = min(kmax, bkg_cap)
        members.append({
            "name": getattr(g, "name", ""),
            "kmax": kmax,
            "kmax_own": float(win["kmax"]),
            "bkg_kmax": bkg_cap,
            "kmin": float(win["kmin"]),
            "reason": str(win.get("reason") or ""),
        })
    if not members:
        return _result(
            False, None,
            reason_zh="没有可用的 χ(k)，FT 窗口未统一",
            reason_en="No χ(k) available; FT window not unified",
            members=[],
        )
    kmax = min(m["kmax"] for m in members)
    kmax = float(np.floor(kmax / UNIFORM_K_FLOOR) * UNIFORM_K_FLOOR)
    if kmax <= float(kmin):
        kmax = float(kmin) + UNIFORM_K_FLOOR
    tightest = min(members, key=lambda m: m["kmax"])
    return _result(
        True, (float(kmin), float(kmax)),
        reason_zh=(
            f"统一 FT k 窗口 {kmin:.1f}–{kmax:.1f} Å⁻¹（取各条最小，最紧的是 "
            f"{tightest['name']} {tightest['kmax']:.2f}）"
        ),
        reason_en=(
            f"Common FT k window {kmin:.1f}–{kmax:.1f} Å⁻¹ (smallest of the set; "
            f"tightest is {tightest['name']} at {tightest['kmax']:.2f})"
        ),
        kmin=float(kmin), kmax=float(kmax),
        members=members, tightest=tightest["name"],
    )


# --------------------------------------------------- 7. normalization check
def normalization_check(group: Any) -> Dict[str, Any]:
    """Is the current normalization acceptable?

    On the FLATTENED spectrum (what the user sees and exports):

      * median |μ| below E0 − 30 eV must be < 0.05,
      * the far pre-edge point(s) (E0 − 150 eV and below) must be < 0.05,
      * median |μ − 1| above E0 + 150 eV must be < 0.05.

    A failure means the pre/post window is wrong for this curve, so the caller
    runs the retry_windows ladder instead of shipping the numbers.
    """
    sess = getattr(group, "session", None)
    arrs = getattr(sess, "arrays", None) if sess is not None else None
    if arrs is None or getattr(arrs, "energy", None) is None:
        return _result(
            False, None,
            reason_zh="尚未处理，无法自检",
            reason_en="Not processed yet; cannot self-check",
        )
    flat = getattr(arrs, "flat", None)
    if flat is None:
        flat = getattr(arrs, "norm", None)
    if flat is None:
        return _result(False, None, reason_zh="无归一化数组", reason_en="No normalized array")
    e = np.asarray(arrs.energy, dtype=float).ravel()
    y = np.asarray(flat, dtype=float).ravel()
    n = min(e.size, y.size)
    e, y = e[:n], y[:n]
    e0 = _f(getattr(arrs, "e0", None)) or _e0_of(group)
    if e0 is None:
        return _result(False, None, reason_zh="无 E0", reason_en="No E0")
    pre = (e <= e0 + NORM_CHECK_PRE_EV) & np.isfinite(y)
    far = (e <= e0 + NORM_CHECK_FAR_EV) & np.isfinite(y)
    post = (e >= e0 + NORM_CHECK_POST_EV) & np.isfinite(y)
    if int(pre.sum()) < 3 or int(post.sum()) < 3:
        return _result(
            False, None,
            reason_zh="归一化区间点数不足，无法自检",
            reason_en="Too few points to self-check the normalization",
        )
    pre_med = float(np.median(np.abs(y[pre])))
    # The far pre-edge must return to ~0.  Judged on the MEDIAN of that
    # stretch: one noisy first sample (dilute mixed set 2 Ni sits at 0.05 on a single
    # point) is not the systematic offset the check is looking for, while the
    # Ti-K set 3 case (whole far pre-edge at ~1.2) is caught either way.  The maximum
    # is still reported for transparency.
    far_max = float(np.max(np.abs(y[far]))) if int(far.sum()) else pre_med
    far_med = float(np.median(np.abs(y[far]))) if int(far.sum()) else pre_med
    post_med = float(np.median(np.abs(y[post] - 1.0)))
    problems_zh: List[str] = []
    problems_en: List[str] = []
    if pre_med >= NORM_CHECK_TOL:
        problems_zh.append(f"边前中位 |μ|={pre_med:.3f}")
        problems_en.append(f"pre-edge median |μ|={pre_med:.3f}")
    if far_med >= NORM_CHECK_TOL:
        problems_zh.append(f"远端预边 |μ|={far_med:.3f}")
        problems_en.append(f"far pre-edge |μ|={far_med:.3f}")
    if post_med >= NORM_CHECK_TOL:
        problems_zh.append(f"边后中位 |μ−1|={post_med:.3f}")
        problems_en.append(f"post-edge median |μ−1|={post_med:.3f}")
    ok = not problems_zh
    return _result(
        ok,
        {"pre": pre_med, "far": far_med, "post": post_med},
        reason_zh="归一化自检通过" if ok else "归一化不合格：" + "、".join(problems_zh),
        reason_en="Normalization check passed" if ok else "Normalization failed: " + ", ".join(problems_en),
        pre=pre_med, far=far_med, far_max=far_max, post=post_med, e0=float(e0),
    )


def retry_windows(energy: Any, mu: Any, e0: float) -> List[Dict[str, Any]]:
    """Ordered fallback pre-edge/normalization windows for a failing curve.

    ① scan start + 5 eV → E0 − 30 eV with a Victoreen term (nvict 3):  the
       reference choice for MEX1 long, strongly curved pre-edges.
    ② scan start + 3 eV → E0 − 20 eV, linear (nvict 0):  the reference choice
       for benchtop scans whose 1s→3d peak sits at E0−8.
    """
    e = np.asarray(energy, dtype=float).ravel()
    e = e[np.isfinite(e)]
    e0v = _f(e0)
    if e.size == 0 or e0v is None:
        return []
    start_rel = float(np.min(e)) - e0v
    out: List[Dict[str, Any]] = []
    r1 = float(round(start_rel) + 5.0)
    r2 = float(round(start_rel) + 3.0)
    if r1 < -10.0:
        out.append({
            "label": "retry1",
            "pre1": r1, "pre2": -30.0, "nvict": 3, "nnorm": 3,
            "reason_zh": f"试 ① 预边 {r1:.0f}…−30 eV（Victoreen nvict 3）",
            "reason_en": f"Try (1) pre-edge {r1:.0f}…−30 eV (Victoreen nvict 3)",
        })
    if r2 < -10.0 and abs(r2 - r1) > 1.0:
        out.append({
            "label": "retry2",
            "pre1": r2, "pre2": -20.0, "nvict": 0, "nnorm": 3,
            "reason_zh": f"试 ② 预边 {r2:.0f}…−20 eV（线性 nvict 0）",
            "reason_en": f"Try (2) pre-edge {r2:.0f}…−20 eV (linear nvict 0)",
        })
    return out[:RETRY_MAX]


# ----------------------------------------------------- 8. processing record
@dataclass
class ProcessingEntry:
    """One delivered spectrum's decisions (row of the Notes sheet)."""

    name: str = ""
    element: str = ""
    edge: str = ""
    source: str = ""                 # source scan(s), e.g. "98007 + 98011"
    action: str = ""                 # merged / rebinned / single / …
    channel: str = ""                # transmission / fluorescence / n-a
    channel_basis: str = ""          # the numbers behind the channel pick
    dE: Optional[float] = None
    dE_basis: str = ""
    e0: Optional[float] = None
    edge_step: Optional[float] = None
    pre: str = ""
    norm: str = ""
    nnorm: Optional[int] = None
    nvict: Optional[int] = None
    bkg_k: str = ""
    ft_k: str = ""
    check: str = ""
    warn: str = ""
    n_points: Optional[int] = None

    def to_row(self) -> Dict[str, Any]:
        return {
            "group": self.name,
            "element": self.element,
            "edge": self.edge,
            "source": self.source,
            "action": self.action,
            "channel": self.channel,
            "channel_basis": self.channel_basis,
            "dE_eV": None if self.dE is None else round(float(self.dE), 2),
            "dE_basis": self.dE_basis,
            "E0_eV": None if self.e0 is None else round(float(self.e0), 2),
            "edge_step": None if self.edge_step is None else round(float(self.edge_step), 4),
            "pre_eV": self.pre,
            "norm_eV": self.norm,
            "nnorm": self.nnorm,
            "nvict": self.nvict,
            "bkg_k": self.bkg_k,
            "FT_k": self.ft_k,
            "check": self.check,
            "warn": self.warn,
            "n_points": self.n_points,
        }


@dataclass
class ProcessingRecord:
    """Whole-run record: header notes + per-spectrum rows (+ excluded scans)."""

    title: str = ""
    notes: List[str] = field(default_factory=list)
    entries: List[ProcessingEntry] = field(default_factory=list)
    excluded: List[Dict[str, str]] = field(default_factory=list)

    def add(self, entry: ProcessingEntry) -> None:
        self.entries.append(entry)

    def exclude(self, name: str, reason: str) -> None:
        self.excluded.append({"name": str(name), "reason": str(reason)})

    def to_rows(self) -> List[Dict[str, Any]]:
        """Rows for the UI table / Excel Notes sheet; ⚠ rows first."""
        rows = [e.to_row() for e in self.entries]
        rows.sort(key=lambda r: (0 if r.get("warn") else 1, str(r.get("group") or "")))
        return rows

    def to_lines(self, *, language: str = "zh", with_rows: bool = True) -> List[str]:
        zh = str(language or "zh").lower().startswith("zh")
        lines: List[str] = []
        if self.title:
            lines.append(self.title)
        lines.extend(self.notes)
        for item in self.excluded:
            lines.append(
                f"剔除 {item.get('name')}: {item.get('reason')}" if zh
                else f"Excluded {item.get('name')}: {item.get('reason')}"
            )
        for row in (self.to_rows() if with_rows else []):
            bits = [
                f"{row['group']}" + (f" [{row['element']} {row['edge']}]" if row.get("element") else ""),
                f"source={row['source']}" if row.get("source") else "",
                f"channel={row['channel']}" + (f" ({row['channel_basis']})" if row.get("channel_basis") else ""),
                f"dE={row['dE_eV']:+.2f} eV" if row.get("dE_eV") is not None else "",
                f"E0={row['E0_eV']}" if row.get("E0_eV") is not None else "",
                f"step={row['edge_step']}" if row.get("edge_step") is not None else "",
                f"pre={row['pre_eV']}" if row.get("pre_eV") else "",
                f"norm={row['norm_eV']}" if row.get("norm_eV") else "",
                f"nvict={row['nvict']}" if row.get("nvict") else "",
                f"bkg_k={row['bkg_k']}" if row.get("bkg_k") else "",
                f"FT_k={row['FT_k']}" if row.get("FT_k") else "",
                f"check={row['check']}" if row.get("check") else "",
                f"⚠ {row['warn']}" if row.get("warn") else "",
            ]
            lines.append(" | ".join(b for b in bits if b))
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "notes": list(self.notes),
            "excluded": [dict(x) for x in self.excluded],
            "entries": [e.to_row() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ProcessingRecord":
        if not isinstance(data, dict):
            return cls()
        rec = cls(title=str(data.get("title") or ""), notes=list(data.get("notes") or []))
        rec.excluded = [dict(x) for x in (data.get("excluded") or []) if isinstance(x, dict)]
        for row in data.get("entries") or []:
            if not isinstance(row, dict):
                continue
            rec.entries.append(ProcessingEntry(
                name=str(row.get("group") or ""),
                element=str(row.get("element") or ""),
                edge=str(row.get("edge") or ""),
                source=str(row.get("source") or ""),
                action=str(row.get("action") or ""),
                channel=str(row.get("channel") or ""),
                channel_basis=str(row.get("channel_basis") or ""),
                dE=_f(row.get("dE_eV")),
                dE_basis=str(row.get("dE_basis") or ""),
                e0=_f(row.get("E0_eV")),
                edge_step=_f(row.get("edge_step")),
                pre=str(row.get("pre_eV") or ""),
                norm=str(row.get("norm_eV") or ""),
                nnorm=row.get("nnorm"),
                nvict=row.get("nvict"),
                bkg_k=str(row.get("bkg_k") or ""),
                ft_k=str(row.get("FT_k") or ""),
                check=str(row.get("check") or ""),
                warn=str(row.get("warn") or ""),
                n_points=row.get("n_points"),
            ))
        return rec

    def __len__(self) -> int:
        return len(self.entries)


def describe_group(group: Any, *, note: str = "") -> ProcessingEntry:
    """Snapshot the current settings/arrays of one group into an entry."""
    s = getattr(group, "settings", None)
    sess = getattr(group, "session", None)
    arrs = getattr(sess, "arrays", None) if sess is not None else None
    e = np.asarray(getattr(group, "energy", np.array([])), dtype=float)
    e0 = _f(getattr(arrs, "e0", None)) if arrs is not None else None
    step = _f(getattr(arrs, "edge_step", None)) if arrs is not None else None
    entry = ProcessingEntry(
        name=str(getattr(group, "name", "")),
        element=str(getattr(group, "element", "") or ""),
        edge=str(getattr(group, "edge", "") or ""),
        dE=_f(getattr(group, "energy_shift", None)),
        e0=e0,
        edge_step=step,
        n_points=int(e.size) if e.size else None,
        warn=str(note or getattr(group, "note", "") or ""),
    )
    if s is not None:
        pre1 = _f(getattr(s, "pre1", None))
        pre2 = _f(getattr(s, "pre2", None))
        norm1 = _f(getattr(s, "norm1", None))
        norm2 = _f(getattr(s, "norm2", None))
        kmin = _f(getattr(s, "kmin", None))
        kmax = _f(getattr(s, "kmax", None))
        bk0 = _f(getattr(s, "bkg_kmin", None))
        bk1 = _f(getattr(s, "bkg_kmax", None))
        entry.pre = f"{pre1 or 0:.0f} … {pre2 or 0:.0f}"
        entry.norm = f"{norm1 or 0:.0f} … {norm2 or 0:.0f}"
        entry.nnorm = int(getattr(s, "nnorm", 0) or 0)
        entry.nvict = int(getattr(s, "nvict", 0) or 0)
        entry.bkg_k = f"{bk0 or 0:.1f}-{bk1 or 0:.1f}"
        entry.ft_k = f"{kmin or 0:.1f}-{kmax or 0:.1f}"
    return entry