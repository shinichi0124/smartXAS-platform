"""
Athena-style operations backed by xraylarch where available.

These power the Energy / Mark / Merge-style menus so the client is not fit-only.
"""

from __future__ import annotations

import csv
import gzip
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .project_model import GroupRecord

# Export data kinds (Athena-like): key → (x_attr, y_spec, x_label, y_label)
# y_spec is a SpectrumArrays field, or a computed token:
#   k1chi / k2chi / k3chi  → k**n * chi
#   dmude / d2mude         → d(μ)/dE computed from the μ array (NOT the
#                            SpectrumArrays.dmude field: larch pre_edge sets
#                            group.dmude = d(norm)/dE, which would duplicate dnormde)
#   dnormde / d2normde     → d(norm)/dE from norm array
EXPORT_KINDS: Dict[str, Tuple[str, str, str, str]] = {
    # --- μ(E) + derivatives ---
    "mu": ("energy", "mu", "energy_eV", "mu"),
    "dmude": ("energy", "dmude", "energy_eV", "dmu_dE"),
    "d2mude": ("energy", "d2mude", "energy_eV", "d2mu_dE2"),
    # --- Normalized + derivatives ---
    "norm": ("energy", "norm", "energy_eV", "norm"),
    "dnormde": ("energy", "dnormde", "energy_eV", "dnorm_dE"),
    "d2normde": ("energy", "d2normde", "energy_eV", "d2norm_dE2"),
    # --- k-space ---
    "chi": ("k", "chi", "k_A-1", "chi"),
    "kchi": ("k", "k1chi", "k_A-1", "k_chi"),
    "k2chi": ("k", "k2chi", "k_A-1", "k2_chi"),
    "k3chi": ("k", "k3chi", "k_A-1", "k3_chi"),
    # --- R-space (xR = |χ(R)| magnitude) ---
    "chir_mag": ("r", "chir_mag", "R_A", "chir_mag"),
    "chir_re": ("r", "chir_re", "R_A", "chir_re"),
    "chir_im": ("r", "chir_im", "R_A", "chir_im"),
    "chir_pha": ("r", "chir_pha", "R_A", "chir_pha"),
    # --- q-space ---
    "chiq_mag": ("q", "chiq_mag", "q_A-1", "chiq_mag"),
    "chiq_re": ("q", "chiq_re", "q_A-1", "chiq_re"),
    "chiq_im": ("q", "chiq_im", "q_A-1", "chiq_im"),
    "chiq_pha": ("q", "chiq_pha", "q_A-1", "chiq_pha"),
}

# UI rows: first two rows have 3 options (μ / Norm); k/R/q have 4.
# File/sheet space tags: Eμ and En both write under E (same energy axis table).
EXPORT_KIND_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("E(μ)", ("mu", "dmude", "d2mude")),
    ("norE", ("norm", "dnormde", "d2normde")),
    ("k", ("chi", "kchi", "k2chi", "k3chi")),
    ("R", ("chir_mag", "chir_re", "chir_im", "chir_pha")),
    ("q", ("chiq_mag", "chiq_re", "chiq_im", "chiq_pha")),
)
# When building tables/files, merge E(μ)+norE → single "E" space
EXPORT_SPACE_FILE_TAG: Dict[str, str] = {
    "E(μ)": "E",
    "norE": "E",
    "k": "k",
    "R": "R",
    "q": "q",
}
EXPORT_DEFAULT_KINDS: Tuple[str, ...] = ("norm", "k2chi", "chir_mag", "chiq_re")
EXPORT_KIND_SPACE: Dict[str, str] = {
    k: space for space, keys in EXPORT_KIND_GROUPS for k in keys
}


@dataclass
class OpResult:
    ok: bool
    message: str = ""
    data: object = None


# Photoelectron k (Å⁻¹): k = sqrt((E−E0) / KTOE).
# KTOE = ħ² / (2 m_e e) in eV·Å² = 3.8099819442818976
# (CODATA via scipy.constants; identical to larch.xafs.xafsutils.KTOE).
KTOE_EV_A2 = 3.8099819442818976
# Auto FT / background k windows (Calvin, XAFS for Everyone, 2013; Athena
# practice: cut the FT where noise dominates, not at a fixed 10 Å⁻¹).
FT_KMIN_DEFAULT = 2.0
FT_KMAX_CAP = 15.0
BKG_KMAX_CAP = 15.0
FT_K_MIN_SPAN = 4.0
FT_K_ENV_WIDTH = 1.0
FT_K_SIGNAL_LO = 3.0
FT_K_SIGNAL_HI = 6.0
FT_K_CUT_FRAC = 0.15
FT_K_SMOOTH_FRAC = 0.35
FT_K_WEIGHT = 2
BKG_K_MARGIN = 0.2


def etok_rel(delta_e: float) -> float:
    """k (Å⁻¹) from photoelectron energy E−E0 (eV). Negative → 0."""
    de = float(delta_e)
    if not np.isfinite(de) or de <= 0.0:
        return 0.0
    return float(np.sqrt(de / KTOE_EV_A2))


def k_available_from_energy(energy: np.ndarray, e0: float) -> float:
    """Largest k this scan can reach: etok(E_max − E0)."""
    e = np.asarray(energy, dtype=float).ravel()
    e = e[np.isfinite(e)]
    if e.size == 0 or not np.isfinite(float(e0)):
        return 0.0
    return etok_rel(float(np.nanmax(e)) - float(e0))


def suggest_bkg_k_range(
    energy: np.ndarray, e0: float, *, cap: float = BKG_KMAX_CAP
) -> Dict[str, float]:
    """Background spline k-range: 0 → min(cap, k_available − margin)."""
    k_av = k_available_from_energy(energy, e0)
    hi = min(float(cap), max(k_av - BKG_K_MARGIN, 0.5))
    return {
        "bkg_kmin": 0.0,
        "bkg_kmax": _round_k(hi),
        "k_available": float(k_av),
    }


def _round_k(val: float) -> float:
    return float(np.round(float(val) * 10.0) / 10.0)


def _rolling_mean(y: np.ndarray, n: int) -> np.ndarray:
    n = max(3, int(n) | 1)
    pad = n // 2
    yp = np.pad(np.asarray(y, dtype=float).ravel(), (pad, pad), mode="edge")
    return np.convolve(yp, np.ones(n) / n, mode="valid")


def _rolling_rms(y: np.ndarray, n: int) -> np.ndarray:
    return np.sqrt(np.maximum(_rolling_mean(np.asarray(y, dtype=float) ** 2, n), 0.0))


def suggest_ft_k_window(
    k: np.ndarray,
    chi: np.ndarray,
    *,
    kweight: int = FT_K_WEIGHT,
    kmin_default: float = FT_KMIN_DEFAULT,
    kmax_cap: float = FT_KMAX_CAP,
    min_span: float = FT_K_MIN_SPAN,
) -> Dict[str, float]:
    """FT k-window from k^w χ: skip XANES, cut where noise dominates.

    Envelope = rolling mean of |k^w χ| over ~1 Å⁻¹. Smoothness = envelope /
    RMS(d(k^w χ)/dk) over the same width. kmax is the last k after
    kmin+min_span where envelope ≥ 15% of the 3–6 Å⁻¹ median *and*
    smoothness ≥ 35% of that region's median (noisy high-k fails smoothness
    even if the amplitude stays large). Short scans use the available k.
    """
    kk = np.asarray(k, dtype=float).ravel()
    cc = np.asarray(chi, dtype=float).ravel()
    n = min(kk.size, cc.size)
    if n < 8:
        kmin = float(kmin_default)
        kmax = float(kmax_cap)
        return {"kmin": kmin, "kmax": kmax, "k_hi": kmax, "reason": "too_short"}
    kk, cc = kk[:n], cc[:n]
    ok = np.isfinite(kk) & np.isfinite(cc)
    kk, cc = kk[ok], cc[ok]
    if kk.size < 8:
        return {
            "kmin": float(kmin_default),
            "kmax": float(kmax_cap),
            "k_hi": float(kmax_cap),
            "reason": "too_short",
        }
    order = np.argsort(kk)
    kk, cc = kk[order], cc[order]
    k_lo = float(kk[0])
    k_hi = float(kk[-1])
    kmin = float(kmin_default)
    if k_lo > kmin - 0.05:
        kmin = k_lo
    if k_hi - kmin < min_span:
        kmin = max(k_lo, k_hi - min_span) if k_hi > min_span else k_lo
    kmin = min(kmin, max(k_lo, k_hi - 0.5))

    y = (kk ** int(kweight)) * cc
    dk = float(np.median(np.diff(kk))) if kk.size > 1 else 0.05
    dk = max(dk, 1e-4)
    n_env = max(5, int(round(FT_K_ENV_WIDTH / dk)) | 1)
    env = _rolling_mean(np.abs(y), n_env)
    hf = _rolling_rms(np.gradient(y, kk), n_env)
    smoothness = env / (hf + 1e-12)
    sig = (kk >= FT_K_SIGNAL_LO) & (kk <= FT_K_SIGNAL_HI)
    if int(sig.sum()) < 8:
        sig = (kk >= kmin) & (kk <= min(k_hi, kmin + min_span))
    if int(sig.sum()) < 4:
        sig = np.ones(kk.shape, dtype=bool)
    e_sig = float(np.median(env[sig])) if np.any(sig) else float(np.median(env))
    sm_sig = float(np.median(smoothness[sig])) if np.any(sig) else float(np.median(smoothness))
    thresh_env = FT_K_CUT_FRAC * e_sig
    thresh_sm = FT_K_SMOOTH_FRAC * sm_sig
    usable = (
        (kk >= kmin + min(min_span, max(1.0, 0.5 * (k_hi - kmin))))
        & (env >= thresh_env)
        & (smoothness >= thresh_sm)
    )
    reason = "env_and_smoothness"
    if np.any(usable):
        kmax = float(kk[np.where(usable)[0][-1]])
    else:
        kmax = k_hi
        reason = "full_available"
    kmax = min(kmax, k_hi, float(kmax_cap))
    kmin = min(kmin, kmax - 0.5) if kmax - 0.5 > k_lo else kmin
    kmin = max(kmin, k_lo)
    kmax = max(kmax, kmin + 0.5)
    return {
        "kmin": _round_k(kmin),
        "kmax": _round_k(kmax),
        "k_hi": _round_k(k_hi),
        "reason": reason,
        "e_sig": e_sig,
    }


# Auto-norm pre/post window contract (v2.19.5, evidence: docs/VALIDATION.md).
PRE2_MAX_REL = -20.0  # pre2 stays ≤ E0−20: 1s→3d / pre-edge peaks sit at E0−20..−2
PRE2_DEFAULT_REL = -30.0  # Athena's pre2 when no quiet stretch is found
PRE_PEAK_LO_REL = -30.0  # pre-edge feature search window (eV rel. E0) ...
PRE_PEAK_HI_REL = -3.0
PRE_PEAK_MIN_FRAC = 0.01  # ... onset = μ ≥1 % of the step (and ≥4σ, ≥3× local scatter) up
PRE_FAR_LO_REL = -45.0  # E0−45..−30: only a strong kink counts (a real step/feature; tails,
PRE_FAR_MIN_FRAC = 0.03  # waves and gentle slopes there are not 1s→3d peaks): ≥3 % step ...
PRE_FAR_SCATTER = 6.0  # ... and ≥6× the local scatter
PRE_PEAK_MARGIN = 3.0  # pre2 sits this far before a detected pre-edge onset
PRE_MIN_POINTS = 5  # a pre-edge line needs ≥5 samples ...
PRE_MIN_SPAN = 8.0  # ... over ≥8 eV, or pre2 moves right (≤ −8) to get them
VICT_MIN_SPAN = 100.0  # Victoreen only judged on ≥100 eV of pre-edge
VICT_GAIN = 0.8  # ... and kept only if it cuts the long-window rms by ≥20 %
NEXT_EDGE_MIN_STEP = 0.15  # a following edge must add ≥15 % of this edge's step


def _span_pre_norm_windows(emin_rel: float, emax_rel: float) -> Dict[str, float]:
    """Fallback when the curve is too short to judge shape (old Auto-norm clamps)."""
    pre1, pre2 = -150.0, -30.0
    if emin_rel > pre1:
        pre1 = max(-40.0, emin_rel + 5.0)
    if pre1 >= pre2 - 5.0:
        pre2 = min(-8.0, pre1 + max(15.0, abs(pre1) * 0.3))
    if pre2 >= -2.0:
        pre2 = -5.0
    if pre1 >= pre2:
        pre1 = pre2 - 20.0
    if emax_rel > 450:
        norm1, norm2 = 50.0, 400.0
    elif emax_rel > 120:
        norm1 = 40.0
        norm2 = max(norm1 + 60.0, emax_rel - 15.0)
    else:
        norm1 = max(8.0, emax_rel * 0.12)
        norm2 = max(norm1 + 20.0, emax_rel - 5.0)
    if norm2 > emax_rel - 3.0:
        norm2 = emax_rel - 3.0
    if norm2 <= norm1:
        norm1 = max(5.0, emax_rel * 0.1)
        norm2 = max(norm1 + 15.0, emax_rel - 3.0)
    nnorm = 3 if (norm2 - norm1) >= 180.0 else 2
    return {
        "pre1": float(pre1),
        "pre2": float(pre2),
        "norm1": float(norm1),
        "norm2": float(norm2),
        "nnorm": int(nnorm),
        "nvict": 0,
        "reason": "span_fallback",
    }


def _smooth_boxcar(y: np.ndarray, n: int) -> np.ndarray:
    n = max(3, int(n) | 1)
    pad = n // 2
    yp = np.pad(np.asarray(y, dtype=float).ravel(), (pad, pad), mode="edge")
    return np.convolve(yp, np.ones(n) / n, mode="valid")


def _pre_model(
    e_fit: np.ndarray, m_fit: np.ndarray, nvict: int, e_eval: np.ndarray, e_ref: float
) -> np.ndarray:
    """larch/Athena pre-edge model μ·E^nvict = a + b·E (nvict 0 = straight line).

    E is scaled by *e_ref* only for conditioning; the family of curves is the
    same as larch ``pre_edge(..., nvict=n)``.
    """
    w_fit = (e_fit / e_ref) ** int(nvict)
    coef = np.polyfit(e_fit - e_ref, m_fit * w_fit, 1)
    return np.polyval(coef, e_eval - e_ref) / (e_eval / e_ref) ** int(nvict)


def _noise_sigma(y: np.ndarray) -> float:
    """Point noise from second differences: 1.4826·MAD(Δ²y)/√6."""
    if y.size < 5:
        return 0.0
    d2 = np.diff(np.asarray(y, dtype=float), 2)
    return float(1.4826 * np.median(np.abs(d2 - np.median(d2))) / np.sqrt(6.0))


def _pre_edge_onset_rel(
    e: np.ndarray, m: np.ndarray, e0: float, step: float, noise: float
) -> Optional[float]:
    """Leftmost energy (rel. *e0*) in [−45, −3] eV where μ kinks up off the
    pre-edge: every sample over the next 0.8 eV (at least 3) sits above a
    line fitted on the 23 eV just before it (E−25..E−2) by
    max(4σ, 3× that fit's rms, 1 % step) — or, left of E0−30, by
    max(4σ, 6× rms, 3 % step).

    Catches 1s→3d pre-edge peaks, a real step below the edge and the foot of a
    broad rise so pre2 can stay before them. The line is local and the bar
    scales with its own scatter, so a gently curved or wavy benchtop baseline
    does not read as a peak. None when nothing kinks up.
    """
    win = np.where((e >= e0 + PRE_FAR_LO_REL) & (e <= e0 + PRE_PEAK_HI_REL))[0]
    if win.size < 3:
        return None
    stride = max(1, int(win.size // 200))
    for j in win[::stride]:
        k = max(j + 3, int(np.searchsorted(e, e[j] + 0.8, side="right")))
        if k > e.size:
            break
        fit = (e >= e[j] - 25.0) & (e <= e[j] - 2.0)
        if int(fit.sum()) < 3:
            continue
        coef = np.polyfit(e[fit] - e0, m[fit], 1)
        scatter = float(np.std(m[fit] - np.polyval(coef, e[fit] - e0)))
        if e[j] >= e0 + PRE_PEAK_LO_REL:
            thr = max(4.0 * noise, 3.0 * scatter, PRE_PEAK_MIN_FRAC * step)
        else:
            thr = max(4.0 * noise, PRE_FAR_SCATTER * scatter, PRE_FAR_MIN_FRAC * step)
        if bool(np.all(m[j:k] - np.polyval(coef, e[j:k] - e0) > thr)):
            return float(e[j] - e0)
    return None


def _is_following_edge(
    e: np.ndarray, md: np.ndarray, e0: float, ec: float, norm1_rel: float, step: float
) -> bool:
    """A dμ/dE peak at *ec* is a following edge only if μ (pre-line removed)
    steps up by ≥15 % of this edge's step: median over ec+8..+60 eV minus
    median over ec−60..−8 eV. EXAFS of a metal foil wiggles but adds no step
    (Fe foil read +96 eV as an edge before v2.19.5). Unjudgeable → True."""
    before = (e >= max(e0 + norm1_rel, ec - 60.0)) & (e <= ec - 8.0)
    after = (e >= ec + 8.0) & (e <= ec + 60.0)
    if int(before.sum()) < 3 or int(after.sum()) < 3 or not step > 0:
        return True
    jump = float(np.median(md[after]) - np.median(md[before]))
    return jump >= NEXT_EDGE_MIN_STEP * step


def suggest_pre_norm_windows(
    energy: np.ndarray,
    mu: np.ndarray,
    e0: float,
) -> Dict[str, float]:
    """Per-curve pre-edge / post-edge windows from μ(E) shape, not a copied default.

    Relative to *e0* (eV), Athena-style signs (pre negative, post positive):

    * **pre2** — last energy still on the flat pre-edge ( |dμ/dE| low for a
      few eV), never above E0−20 eV, and ≥3 eV before a detected pre-edge
      peak / rise onset (v2.19.5: E0−8..−16 used to sit on the 1s→3d peak).
    * **nvict** — with ≥100 eV of pre-edge, the larch Victoreen exponent
      (μ·E^n = a+bE, n 0–3) that fits that whole stretch best, kept only if it
      beats the straight line by ≥20 % rms (5 keV transmission bends visibly).
    * **pre1** — walk further left while that pre-edge model still fits;
      stop before a previous edge or the scan start.
    * **norm1** — after the white line / main XANES (dμ/dE has settled), so
      a sharp L3 white line and a broad K-edge get different starts.
    * **norm2** — toward the end of the scan, or ~25 eV before a following
      edge: a dμ/dE peak that also adds a real step (EXAFS alone does not).

    Short traces fall back to span clamps. Citations: Calvin, *XAFS for
    Everyone* (2013) Ch. 4–5; Athena pre/norm conventions (Ravel & Newville);
    Victoreen pre-edge form as in larch ``pre_edge`` (nvict).
    """
    e = np.asarray(energy, dtype=float).ravel()
    m = np.asarray(mu, dtype=float).ravel()
    n = min(e.size, m.size)
    e, m = e[:n], m[:n]
    ok = np.isfinite(e) & np.isfinite(m)
    e, m = e[ok], m[ok]
    if e.size < 24 or not np.isfinite(float(e0)):
        if e.size < 2:
            return _span_pre_norm_windows(-150.0, 400.0)
        return _span_pre_norm_windows(float(np.min(e) - e0), float(np.max(e) - e0))
    order = np.argsort(e)
    e, m = e[order], m[order]
    e0 = float(e0)
    emin_rel = float(e[0] - e0)
    emax_rel = float(e[-1] - e0)
    de_med = float(np.median(np.diff(e))) if e.size > 1 else 1.0
    de_med = max(de_med, 0.05)
    smooth_n = max(5, int(round(2.5 / de_med)) | 1)
    ms = _smooth_boxcar(m, smooth_n)
    dmu = np.gradient(ms, e)

    near = (e >= e0 - 25.0) & (e <= e0 + 40.0)
    if int(near.sum()) < 5:
        near = np.ones(e.shape, dtype=bool)
    d_near = np.where(near, dmu, -np.inf)
    i_peak = int(np.nanargmax(d_near))
    peak = float(abs(dmu[i_peak]))
    if not np.isfinite(peak) or peak <= 1e-12:
        return _span_pre_norm_windows(emin_rel, emax_rel)

    # Scale + noise for the thresholds below: edge step against a line on
    # the pre-edge (E0−90..−30, or what the scan has), σ from Δ²μ there.
    pre_zone = (e >= e0 - 90.0) & (e <= e0 - 30.0)
    if int(pre_zone.sum()) < 4:
        pre_zone = e <= e0 - 20.0
    post_zone = (e >= e0 + 15.0) & (e <= e0 + 60.0)
    step = 0.0
    if int(pre_zone.sum()) >= 2 and int(post_zone.sum()) >= 2:
        c0 = np.polyfit(e[pre_zone] - e0, m[pre_zone], 1)
        step = float(np.median(m[post_zone] - np.polyval(c0, e[post_zone] - e0)))
    if not np.isfinite(step) or step <= 0.0:
        step = float(np.ptp(ms)) * 0.5
    noise = _noise_sigma(m[pre_zone]) if int(pre_zone.sum()) >= 5 else 0.0

    # --- pre2: rightmost quiet pre-edge ≤ E0−20, before any pre-edge peak ---
    pre2_rel = PRE2_DEFAULT_REL
    left = e <= (e0 + PRE2_MAX_REL)
    thresh = 0.10 * peak
    idxs = np.where(left & (np.abs(dmu) < thresh))[0]
    if idxs.size:
        for i in idxs[::-1]:
            # quiet for 4 eV or 3 samples, whichever is longer (5 eV benchtop grids)
            j = min(int(np.searchsorted(e, e[i] - 4.0)), i - 2)
            if j >= 0 and float(np.max(np.abs(dmu[j : i + 1]))) < 0.14 * peak:
                pre2_rel = float(e[i] - e0)
                break
    onset_rel = _pre_edge_onset_rel(e, m, e0, step, noise)
    if onset_rel is not None:
        pre2_rel = min(pre2_rel, onset_rel - PRE_PEAK_MARGIN)
    pre2_rel = float(np.clip(pre2_rel, -60.0, PRE2_MAX_REL))
    if int(np.searchsorted(e, e0 + pre2_rel, side="right")) < PRE_MIN_POINTS or (
        pre2_rel - emin_rel < PRE_MIN_SPAN
    ):
        # scan starts too close to the edge: a line needs ≥5 samples / 8 eV,
        # even if that reaches into the pre-edge rise (old behaviour, ≤ −8)
        pre2_rel = min(-8.0, max(emin_rel + PRE_MIN_SPAN, float(e[PRE_MIN_POINTS - 1] - e0)))

    # --- previous edge: a dμ/dE peak well left of this edge bounds pre1 ---
    # (stay on *this* pre-edge, not on the previous post-edge plateau)
    lo_rel = max(emin_rel + 5.0, -250.0)
    far_left = e < (e0 + pre2_rel - 20.0)
    if far_left.any():
        d_left = np.where(far_left, dmu, 0.0)
        i_prev = int(np.nanargmax(d_left))
        if d_left[i_prev] > 0.30 * peak:
            lo_rel = max(lo_rel, float(e[i_prev] - e0) + 35.0)

    pre2_abs = e0 + pre2_rel
    i2 = int(np.searchsorted(e, pre2_abs))
    i2 = min(max(i2, 8), int(e.size) - 1)

    def _model_rms(i1: int, i2_: int, nv: int) -> float:
        if i2_ - i1 < 6:
            return float("inf")
        ee, mm = e[i1:i2_], m[i1:i2_]
        return float(np.std(mm - _pre_model(ee, mm, nv, ee, e0)))

    # --- nvict: Victoreen only when a long pre-edge clearly bends ---
    nvict = 0
    i_lo = int(np.searchsorted(e, e0 + lo_rel))
    if pre2_rel - lo_rel >= VICT_MIN_SPAN and i2 - i_lo >= 12:
        rms_n = [_model_rms(i_lo, i2, nv) for nv in range(4)]
        best = int(np.argmin(rms_n))
        if best > 0 and np.isfinite(rms_n[best]) and rms_n[best] <= VICT_GAIN * rms_n[0]:
            nvict = best

    # --- pre1: walk left while that pre-edge model still fits ---
    i_ref = int(np.searchsorted(e, pre2_abs - 40.0))
    i_ref = max(0, min(i_ref, i2 - 6))
    base_rms = _model_rms(i_ref, i2, nvict)
    if not np.isfinite(base_rms):
        base_rms = 0.0
    base_rms = max(base_rms, 2e-4 * step, 1e-9)
    res_near = None  # μ minus the model fitted on the near window [pre2−40, pre2]
    if i2 - i_ref >= 2:
        res_near = m - _pre_model(e[i_ref:i2], m[i_ref:i2], nvict, e, e0)
    thr_prev = max(0.03 * step, 4.0 * noise)
    i_lim = int(np.searchsorted(e, e0 + lo_rel))
    i_lim = max(0, min(i_lim, i_ref))
    i_pre1 = i_ref
    walk = max(1, int(e.size // 250) or 1)
    for i in range(i_ref, i_lim - 1, -walk):
        if _model_rms(i, i2, nvict) > 2.5 * base_rms:
            break
        # previous edge: μ climbing above the near pre-edge model as we go
        # left (edge-up convention). Detrended, so a steep falling 5 keV
        # background is not mistaken for one.
        if res_near is not None and i2 - i > 8:
            mid = i + (i2 - i) // 4
            if float(np.median(res_near[i:mid])) > float(np.median(res_near[mid:i2])) + thr_prev:
                break
        i_pre1 = i
    pre1_rel = max(float(e[i_pre1] - e0), lo_rel)
    pre1_rel = min(pre1_rel, pre2_rel - 20.0)
    pre1_rel = max(pre1_rel, emin_rel + 5.0, -250.0)
    if pre2_rel - pre1_rel < PRE_MIN_SPAN:
        pre1_rel = float(max(emin_rel, pre2_rel - PRE_MIN_SPAN))  # short scan: from its start
    if pre2_rel - pre1_rel < VICT_MIN_SPAN:
        nvict = 0  # the walk stopped short: no curvature to speak of

    # --- norm1: after white line / main XANES ---
    after = np.where(e > e[i_peak])[0]
    settled_rel = None
    for i in after:
        if e[i] < e0 + 12.0:
            continue
        if abs(dmu[i]) >= 0.15 * peak:
            continue
        ahead = (e >= e[i]) & (e <= e[i] + 10.0)
        if int(ahead.sum()) >= 3 and float(np.mean(np.abs(dmu[ahead]))) < 0.20 * peak:
            settled_rel = float(e[i] - e0)
            break
    wl_rel = 0.0
    after_mu = (e >= e0) & (e <= e0 + 45.0)
    if after_mu.any():
        i_wl = int(np.nanargmax(np.where(after_mu, m, -np.inf)))
        wl_rel = float(e[i_wl] - e0)
    norm1_rel = 50.0 if settled_rel is None else settled_rel + 15.0
    if wl_rel > 3.0:
        norm1_rel = max(norm1_rel, wl_rel + 12.0)
    # broad XANES: keep walking until a 25 eV window of |dmu| is quiet
    if settled_rel is not None and settled_rel > 40.0:
        norm1_rel = max(norm1_rel, min(settled_rel + 20.0, 120.0))
    cap_hi = min(120.0, emax_rel - 30.0) if emax_rel > 50.0 else max(8.0, emax_rel * 0.2)
    norm1_rel = float(np.clip(norm1_rel, 25.0 if emax_rel > 80.0 else 8.0, max(cap_hi, 8.0)))

    # --- norm2: end of scan or before the next edge (a real step) ---
    norm2_rel = min(emax_rel - 8.0, 800.0)
    later = e > (e0 + max(norm1_rel + 40.0, 80.0))
    if later.any():
        i1p = max(0, min(int(np.searchsorted(e, e0 + pre1_rel)), i2 - 2))
        md = m - _pre_model(e[i1p:i2], m[i1p:i2], nvict, e, e0)
        post = (e >= e0 + norm1_rel) & (e <= e0 + norm1_rel + 80.0)
        step_post = float(np.median(md[post])) if int(post.sum()) >= 3 else step
        if not np.isfinite(step_post) or step_post <= 0.0:
            step_post = step
        d_later = np.where(later, dmu, 0.0)
        cand = np.where(later & (d_later > 0.35 * peak))[0]
        for i in cand:
            lo_i, hi_i = max(i - 1, 0), min(i + 1, int(e.size) - 1)
            if d_later[i] < d_later[lo_i] or d_later[i] < d_later[hi_i]:
                continue  # not a local maximum of dμ/dE
            if _is_following_edge(e, md, e0, float(e[i]), norm1_rel, step_post):
                norm2_rel = min(norm2_rel, float(e[i] - e0) - 25.0)
                break
    if norm2_rel < norm1_rel + 40.0:
        norm2_rel = min(emax_rel - 5.0, norm1_rel + 40.0)
    nnorm = 3 if (norm2_rel - norm1_rel) >= 180.0 else 2
    return {
        "pre1": float(round(pre1_rel)),
        "pre2": float(round(pre2_rel)),
        "norm1": float(round(norm1_rel)),
        "norm2": float(round(norm2_rel)),
        "nnorm": int(nnorm),
        "nvict": int(nvict),
        "reason": "shape",
    }


def _L():
    from .fit_engine import _require_larch

    return _require_larch()


def load_energy_mu_from_file(path: str) -> Tuple[np.ndarray, np.ndarray, str]:
    """Return energy, mu, suggested group name."""
    from pathlib import Path
    from .fit_engine import _load_xdi_manual, load_spectrum

    p = Path(path)
    L = _L()
    if p.suffix.lower() == ".prj":
        # load first group only at this level — caller expands
        g = load_spectrum(p, rbkg=1.0)
        e = np.asarray(g.energy, dtype=float)
        mu = np.asarray(g.mu, dtype=float)
        return e, mu, getattr(g, "group_name", p.stem)

    # CSV / XDI / dat
    try:
        g = _load_xdi_manual(p, L)
        return np.asarray(g.energy, dtype=float), np.asarray(g.mu, dtype=float), p.stem
    except Exception:
        pass

    # plain multi-column CSV / whitespace (auto: energy=col0, prefer a MU-like col)
    try:
        labels, n_cols, _sample = sniff_raw_columns(str(p))
    except Exception:
        labels, n_cols = [], 0
    rows = _parse_numeric_rows(p)
    if len(rows) < 3:
        raise RuntimeError(f"Cannot parse energy/mu from {path}")
    width = max(len(r) for r in rows)
    arr = np.full((len(rows), width), np.nan, dtype=float)
    for i, r in enumerate(rows):
        arr[i, : len(r)] = r
    e = arr[:, 0]
    e_label = labels[0] if labels else ""
    e = e * _energy_scale_from_label(e_label, e)
    mu_col = 1 if width > 1 else 0
    for i, lab in enumerate(labels):
        low = (lab or "").lower()
        if i == 0:
            continue
        if any(
            k in low
            for k in ("mutrans", "mufluor", "mu_from", "mu(", "mu_")
        ) or low == "mu":
            mu_col = i
            break
    mu = arr[:, mu_col]
    m = np.isfinite(e) & np.isfinite(mu)
    e, mu = e[m], mu[m]
    if e.size < 3:
        raise RuntimeError(f"Cannot parse energy/mu from {path} (usable points={e.size})")
    order = np.argsort(e)
    return e[order], mu[order], p.stem


def sniff_raw_columns(path: str) -> Tuple[List[str], int, List[float]]:
    """Peek a raw text data file: (column labels, n_data_columns, first data row).

    Labels come from XDI ``# Column.N:`` headers when present (kept as the full
    title after the colon, e.g. ``energy eV``), else from a CSV header line of
    non-numeric tokens, else generic ``col0``/``col1``… names. Used by the
    import-options dialog.
    """
    from pathlib import Path
    import re

    p = Path(path)
    # index → label from XDI Column.N: lines (preserve order by N when possible)
    xdi_by_idx: dict = {}
    labels_ordered: List[str] = []
    n_cols = 0
    sample: List[float] = []
    header_guess: List[str] = []
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                # # Column.1: energy eV   or   # column.2: i0
                m = re.search(r"[Cc]olumn\s*\.?\s*(\d+)\s*:\s*(.+)$", s)
                if m:
                    idx = int(m.group(1)) - 1  # 1-based → 0-based
                    title = m.group(2).strip()
                    # keep full title (units ok); collapse runs of space
                    title = re.sub(r"\s+", " ", title)
                    if idx >= 0:
                        xdi_by_idx[idx] = title or f"col{idx}"
                    continue
                if "olumn." in s and ":" in s:
                    try:
                        part = s.split(":", 1)[1].strip()
                        if part:
                            labels_ordered.append(re.sub(r"\s+", " ", part))
                    except Exception:
                        pass
                continue
            if s.startswith("!"):
                continue
            parts = s.replace(",", " ").split()
            try:
                vals = [float(x) for x in parts]
            except ValueError:
                # Non-numeric line before data → treat as CSV-style header
                if not sample and not header_guess and len(parts) >= 2:
                    header_guess = [re.sub(r"\s+", " ", x) for x in parts]
                continue
            n_cols = max(n_cols, len(vals))
            if not sample:
                sample = vals
            # stop once we have a sample and enough labels
            if sample and n_cols:
                if xdi_by_idx or labels_ordered or header_guess:
                    break
                # no labels yet — still need n_cols; one data row is enough
                break
    if n_cols == 0:
        raise RuntimeError(f"No numeric data found in {path}")
    labels: List[str] = []
    if xdi_by_idx:
        for i in range(n_cols):
            labels.append(xdi_by_idx.get(i) or f"col{i}")
    elif labels_ordered:
        labels = list(labels_ordered[:n_cols])
    elif header_guess and len(header_guess) >= n_cols:
        labels = list(header_guess[:n_cols])
    if len(labels) < n_cols:
        labels = labels + [f"col{i}" for i in range(len(labels), n_cols)]
    return labels[:n_cols], n_cols, sample[:n_cols]


def _energy_scale_from_label(label: str, e: np.ndarray) -> float:
    """keV→eV scale factor for an imported energy column (1000.0 or 1.0).

    MEX1 beamline slew XDI stores energy in keV (`# Column.1: energy keV`)
    while step scans and Athena convention are eV.  Untagged, a 4.76–5.71 keV
    axis is read as ~5 eV and find_e0/guess_edge land on B/C L edges
    (v2.16.18).  Rule: an explicit "keV" unit tag wins; with no unit tag,
    max(|E|) < 100 cannot be a real XAFS edge in eV, so treat as keV.
    """
    import re as _re

    if _re.search(r"\bkev\b", str(label or ""), flags=_re.IGNORECASE):
        return 1000.0
    if _re.search(r"\bev\b", str(label or ""), flags=_re.IGNORECASE):
        return 1.0
    try:
        arr = np.asarray(e, dtype=float)
        if arr.size and float(np.nanmax(np.abs(arr))) < 100.0:
            return 1000.0
    except (TypeError, ValueError):
        pass
    return 1.0


def _parse_numeric_rows(path: Path) -> List[List[float]]:
    """Read numeric rows from CSV / whitespace text (skip # / ! / header lines)."""
    rows: List[List[float]] = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("!"):
                continue
            # Decide at the data-row boundary, not from metadata.  XDI header
            # values commonly contain commas while their numeric body remains
            # whitespace-delimited; a file-wide comma count misclassifies it.
            parts = s.replace(",", " ").split()
            try:
                rows.append([float(x) for x in parts])
            except ValueError:
                continue
    return rows


def _compute_mu_from_columns(arr: np.ndarray, spec: dict) -> Tuple[np.ndarray, str]:
    """Build μ from columns; returns (mu, how_note). Falls back if ln yields all-NaN."""
    mode = str(spec.get("mode", "mu") or "mu")
    ln = bool(spec.get("ln", True))
    e_col = int(spec["e_col"])
    i0 = int(spec.get("i0_col") or 0)
    it = int(spec.get("it_col") or 0)
    mu_c = int(spec.get("mu_col") or 0)
    ref = spec.get("ref_col")
    note = mode

    def _safe_ln(ratio: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.log(ratio)
        # keep only positive ratios; others → nan
        out = np.where(np.isfinite(ratio) & (ratio > 0), out, np.nan)
        return out

    with np.errstate(divide="ignore", invalid="ignore"):
        if mode == "trans":
            ratio = arr[:, i0] / arr[:, it]
            mu = _safe_ln(ratio) if ln else ratio
            note = "trans+ln" if ln else "trans"
            # If ln wiped everything (common when columns are not I0/It), fall back
            if ln and not np.any(np.isfinite(mu)):
                mu = ratio
                note = "trans (ln skipped: non-positive ratios)"
            if not np.any(np.isfinite(mu)) and 0 <= mu_c < arr.shape[1]:
                mu = arr[:, mu_c]
                note = f"fallback mu col {mu_c}"
        elif mode == "fluo":
            mu = arr[:, it] / arr[:, i0]
            note = "fluo"
            if not np.any(np.isfinite(mu)) and 0 <= mu_c < arr.shape[1]:
                mu = arr[:, mu_c]
                note = f"fallback mu col {mu_c}"
        elif mode == "ref" and ref is not None:
            ratio = arr[:, it] / arr[:, int(ref)]
            mu = _safe_ln(ratio) if ln else ratio
            note = "ref+ln" if ln else "ref"
            if ln and not np.any(np.isfinite(mu)):
                mu = ratio
                note = "ref (ln skipped)"
        else:
            mu = arr[:, mu_c]
            note = f"mu col {mu_c}"
    return np.asarray(mu, dtype=float), note


def load_energy_mu_with_spec(path: str, spec: dict) -> Tuple[np.ndarray, np.ndarray, str]:
    """Load a raw text file with explicit user choices (import dialog).

    spec: {"e_col": int, "mode": "trans"|"fluo"|"mu",
           "i0_col": int, "it_col": int, "mu_col": int,
           "emin": float|None, "emax": float|None}
    Modes: trans = ln(I0/It), fluo = Ifl/I0, mu = direct μ column.

    Robust to easyxanes-style CSVs (header row + many columns + precomputed MU).
    """
    from pathlib import Path

    p = Path(path)
    rows = _parse_numeric_rows(p)
    if len(rows) < 3:
        raise RuntimeError(
            f"Cannot parse numeric data from {p.name} "
            f"(only {len(rows)} numeric row(s); need ≥3). "
            f"Check delimiter / encoding."
        )
    need = max(
        int(spec["e_col"]),
        int(spec.get("i0_col") or 0),
        int(spec.get("it_col") or 0),
        int(spec.get("mu_col") or 0),
        int(spec.get("ref_col") or 0),
    )
    # len must be > need (0-based index)
    wide = [r for r in rows if len(r) > need]
    if len(wide) < 3:
        raise RuntimeError(
            f"Selected columns out of range for {p.name}: "
            f"need column index {need}, but most rows have fewer fields "
            f"({len(wide)} usable / {len(rows)} numeric). "
            f"Re-open import options and check column roles for this file."
        )
    # pad short rows only if rare (ragged)
    width = max(len(r) for r in wide)
    arr = np.full((len(wide), width), np.nan, dtype=float)
    for i, r in enumerate(wide):
        arr[i, : len(r)] = r

    e_col = int(spec["e_col"])
    e = arr[:, e_col]
    # Normalize keV axes (e.g. MEX1 slew XDI) to eV before the emin/emax
    # filter — the dialog's range values are eV (v2.16.18).
    try:
        _labels, _nc, _smp = sniff_raw_columns(str(p))
        e_label = _labels[e_col] if e_col < len(_labels) else ""
    except Exception:
        e_label = ""
    e = e * _energy_scale_from_label(e_label, e)
    mu, how = _compute_mu_from_columns(arr, spec)

    emin, emax = spec.get("emin"), spec.get("emax")
    m = np.isfinite(e) & np.isfinite(mu)
    n_finite = int(np.count_nonzero(m))
    # If range filter kills everything, drop the range once and retry
    if n_finite == 0 and (emin is not None or emax is not None):
        m = np.isfinite(e) & np.isfinite(mu)
        n_finite = int(np.count_nonzero(m))
        emin, emax = None, None
    if emin is not None:
        m &= e >= float(emin)
    if emax is not None:
        m &= e <= float(emax)
    e2, mu2 = e[m], mu[m]
    if e2.size == 0:
        # Last resort: pure μ column if available and different from failed mode
        mu_c = int(spec.get("mu_col") or 0)
        if 0 <= mu_c < arr.shape[1]:
            mu_try = arr[:, mu_c]
            m2 = np.isfinite(e) & np.isfinite(mu_try)
            e2, mu2 = e[m2], mu_try[m2]
            how = f"emergency mu col {mu_c} (mode {spec.get('mode')} gave 0 finite points)"
    if e2.size < 3:
        raise RuntimeError(
            f"Too few usable points ({e2.size}) from {p.name}. "
            f"Tried mode={spec.get('mode')}, e_col={e_col}, "
            f"i0={spec.get('i0_col')}, it/ifl={spec.get('it_col')}, mu={spec.get('mu_col')}; "
            f"finite before range={n_finite}. "
            f"Tip: easyxanes CSVs often already have a MU column — choose mode “Use μ column as-is”."
        )
    order = np.argsort(e2)
    return e2[order], mu2[order], p.stem


def fluo_channel_spec(spec: Optional[dict]) -> Optional[dict]:
    """Second-channel spec for "Also import fluorescence" (v2.18.0).

    The primary spec keeps its transmission / ready-μ meaning; ``fl_col``
    names the fluorescence column.  Transmission primary → μ = Ifl/I0 (never
    ln); ready-μ primary → that column as μ.  ``mu_col = -1`` disables the
    loader's μ-column fallbacks so a broken fluorescence column can never
    silently come back as a copy of the transmission μ (§B.14c).
    Returns None when no fluorescence channel was requested.
    """
    if not spec or spec.get("fl_col") is None:
        return None
    fl = int(spec["fl_col"])
    mode = str(spec.get("mode") or "")
    if mode == "trans":
        return {**spec, "mode": "fluo", "it_col": fl, "mu_col": -1, "ln": False,
                "ref_col": None, "fl_col": None}
    if mode == "mu":
        return {**spec, "mode": "mu", "mu_col": fl, "ref_col": None, "fl_col": None}
    return None


def _prj_group_display_name(key: str, g) -> str:
    """Prefer Athena label/filename over opaque hash keys."""
    for attr in ("label", "filename", "group_name", "name"):
        val = getattr(g, attr, None)
        if isinstance(val, str) and val.strip():
            name = val.strip()
            # filename may be a full path — use stem for display
            if attr == "filename" and ("/" in name or "\\" in name):
                name = Path(name).stem or name
            if name and not name.startswith("0x"):
                return name
    args = getattr(g, "args", None)
    if isinstance(args, dict):
        for k in ("label", "original_label", "file", "source"):
            val = args.get(k)
            if isinstance(val, str) and val.strip() and not str(val).startswith("0x"):
                return str(val).strip()
    return str(key)


def _read_athena_lenient(path: str):
    """Fallback read for .prj files whose @args lines contain raw Windows
    backslash paths.

    larch's Perl-Athena parser evals each @args line as a Python literal;
    a raw backslash path (C:\\Users\\...) breaks that eval
    ("truncated \\UXXXXXXXX escape") and read_athena then crashes with
    "object of type 'NoneType' has no len()".  smartLarch <0.34.11 wrote
    such paths.  Escape lone backslashes in @args lines and retry once.
    Returns the parsed project, or None when nothing was fixable.
    """
    import gzip
    import os
    import tempfile

    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", "replace")
    fixed = "\n".join(
        re.sub(r"(?<!\\)\\(?!\\)", r"\\\\", line)
        if line.lstrip().startswith("@args")
        else line
        for line in text.split("\n")
    )
    if fixed == text:
        return None
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".prj", delete=False, encoding="utf-8", newline="\n"
        ) as tmp:
            tmp.write(fixed)
            tmp_name = tmp.name
        return _L()["read_athena"](
            tmp_name, do_preedge=False, do_bkg=False, do_fft=False
        )
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _arg_float(args: dict, *keys, default=None):
    for key in keys:
        val = args.get(key) if isinstance(args, dict) else None
        if val in (None, ""):
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return default


def _arg_str(args: dict, *keys, default: str = "") -> str:
    for key in keys:
        val = args.get(key) if isinstance(args, dict) else None
        if isinstance(val, str) and val.strip() and not val.startswith("0x"):
            return val.strip()
    return default


def _arg_bool(args: dict, key: str, default: bool = False) -> bool:
    if not isinstance(args, dict):
        return default
    val = args.get(key)
    if val in (None, ""):
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _finite_float(val, default=None):
    if val in (None, ""):
        return default
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(num):
        return default
    return num


# Athena's bkg_nnorm is the NUMBER of post-edge polynomial terms (1 constant,
# 2 linear, 3 quadratic — bkg_nc0..nc2); larch pre_edge's nnorm is the DEGREE.
# larch's own read_athena / AthenaProject.save pass the value through unchanged,
# so a .prj written by larch (Larix, smartLarch <= 4.4.1; header line
# "# Using Larch version ...") holds the degree.  smartLarch >= 4.4.2 writes
# Athena's number of terms and replaces that header line with PRJ_HEADER_MARK
# (v4.4.2; evidence: docs/VALIDATION.md).
PRJ_HEADER_MARK = "# Written by smartLarch Platform (Athena bkg_nnorm convention)"
_LARCH_HEADER = "# Using Larch version"


def prj_nnorm_is_degree(header) -> bool:
    """True for a .prj whose bkg_nnorm holds larch's degree (written by larch)."""
    text = str(header or "")
    return _LARCH_HEADER in text and PRJ_HEADER_MARK not in text


def athena_nnorm_to_larch(value, *, degree_coded: bool = False) -> Optional[int]:
    """Stored bkg_nnorm → larch pre_edge nnorm (None when absent)."""
    num = _finite_float(value)
    if num is None:
        return None
    n = int(round(num))
    if degree_coded:
        return max(0, min(3, n))
    return max(0, min(2, n - 1))


def larch_nnorm_to_athena(nnorm) -> int:
    """larch degree → Athena number of terms (1–3); a cubic (3) has no Athena
    equivalent and is written as quadratic — save_project_to_prj says so."""
    return max(1, min(3, int(nnorm) + 1))


def _mark_prj_header(path: Path) -> None:
    """Replace larch's "Using Larch version" header line with PRJ_HEADER_MARK."""
    from .version import APP_VERSION

    raw = path.read_bytes()
    gz = raw[:2] == b"\x1f\x8b"
    text = gzip.decompress(raw) if gz else raw
    old = _LARCH_HEADER.encode("ascii")
    if old not in text[:2000]:
        raise ValueError("larch header line not found; bkg_nnorm convention unmarked")
    new = f"{PRJ_HEADER_MARK} v{APP_VERSION}; Larch version".encode("ascii")
    text = text.replace(old, new, 1)
    path.write_bytes(gzip.compress(text) if gz else text)


def prj_meta_from_athena_group(g, *, nnorm_is_degree: bool = False) -> dict:
    """Pull saved processing flags out of a larch Athena group.

    After read_athena, parameters live on ``athena_params.bkg`` / ``.fft``,
    not on a raw ``args`` dict (that exists only while saving).
    ``nnorm`` is returned as larch's degree: Athena's bkg_nnorm − 1, unless
    the file holds the degree already (``prj_nnorm_is_degree`` of its header).
    """
    args = getattr(g, "args", None)
    if not isinstance(args, dict):
        args = {}
    ap = getattr(g, "athena_params", None)
    bkg = getattr(ap, "bkg", None) if ap is not None else None
    fft = getattr(ap, "fft", None) if ap is not None else None

    def pick(*vals, default=None):
        for val in vals:
            num = _finite_float(val)
            if num is not None:
                return num
        return default

    def a(obj, name):
        return getattr(obj, name, None) if obj is not None else None

    marked = _arg_bool(args, "marked", True) if "marked" in args else True
    if "marked" not in args and ap is not None and hasattr(ap, "marked"):
        marked = bool(_finite_float(getattr(ap, "marked"), 1.0))
    frozen = _arg_bool(args, "frozen") if "frozen" in args else False
    if "frozen" not in args and ap is not None and hasattr(ap, "frozen"):
        frozen = bool(_finite_float(getattr(ap, "frozen"), 0.0))

    kwindow = _arg_str(args, "fft_kwindow", "fft_win") or a(fft, "kwindow") or a(fft, "win")
    rwindow = _arg_str(args, "bft_rwindow", "bft_win") or a(ap, "bft_rwindow")
    element = _arg_str(args, "atsym") or str(getattr(g, "atsym", "") or "").strip()
    edge = _arg_str(args, "edge") or str(getattr(g, "edge", "") or "").strip()
    return {
        "e0": pick(args.get("bkg_e0"), a(bkg, "e0"), getattr(g, "e0", None)),
        "pre1": pick(args.get("bkg_pre1"), a(bkg, "pre1")),
        "pre2": pick(args.get("bkg_pre2"), a(bkg, "pre2")),
        "norm1": pick(args.get("bkg_nor1"), a(bkg, "nor1")),
        "norm2": pick(args.get("bkg_nor2"), a(bkg, "nor2")),
        "nnorm": athena_nnorm_to_larch(
            pick(args.get("bkg_nnorm"), a(bkg, "nnorm")), degree_coded=nnorm_is_degree
        ),
        "make_flat": _arg_bool(args, "bkg_flatten", True) if "bkg_flatten" in args else (
            bool(_finite_float(a(bkg, "flatten"), 1.0))
        ),
        "rbkg": pick(args.get("bkg_rbkg"), a(bkg, "rbkg")),
        "bkg_kweight": pick(args.get("bkg_kw"), a(bkg, "kw")),
        "bkg_kmin": pick(args.get("bkg_spl1"), a(bkg, "spl1")),
        "bkg_kmax": pick(args.get("bkg_spl2"), a(bkg, "spl2")),
        "bkg_dk": pick(args.get("bkg_dk"), a(bkg, "dk")),
        "kmin": pick(args.get("fft_kmin"), a(fft, "kmin")),
        "kmax": pick(args.get("fft_kmax"), a(fft, "kmax")),
        "dk": pick(args.get("fft_dk"), a(fft, "dk")),
        "kwindow": str(kwindow).strip() if kwindow else None,
        "kweight_plot": pick(args.get("fft_kw"), a(fft, "kw")),
        "rmin": pick(args.get("bft_rmin"), a(ap, "bft_rmin")),
        "rmax": pick(args.get("bft_rmax"), a(ap, "bft_rmax")),
        "dr": pick(args.get("bft_dr"), a(ap, "bft_dr")),
        "rwindow": str(rwindow).strip() if rwindow else None,
        "element": element,
        "edge": edge,
        "frozen": frozen,
        "marked": marked,
        "importance": pick(args.get("importance"), a(ap, "importance"), default=1.0),
        "energy_shift": pick(
            args.get("bkg_eshift"),
            a(bkg, "eshift"),
            getattr(g, "energy_shift", None),
            default=0.0,
        ),
    }


def apply_prj_meta_to_group(gr: GroupRecord, meta: Optional[dict]) -> None:
    """Restore AthenaSettings / flags saved into a .prj."""
    if not meta:
        return
    s = gr.settings
    if meta.get("e0") is not None:
        s.e0 = float(meta["e0"])
    for name in (
        "pre1", "pre2", "norm1", "norm2", "nnorm", "rbkg",
        "bkg_kweight", "bkg_kmin", "bkg_kmax", "bkg_dk",
        "kmin", "kmax", "dk", "kweight_plot", "rmin", "rmax", "dr",
    ):
        val = meta.get(name)
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if name in ("nnorm", "bkg_kweight", "kweight_plot"):
            setattr(s, name, int(round(num)))
        else:
            setattr(s, name, num)
    if meta.get("make_flat") is not None:
        s.make_flat = bool(meta["make_flat"])
    if meta.get("kwindow"):
        s.kwindow = str(meta["kwindow"])
    if meta.get("rwindow"):
        s.rwindow = str(meta["rwindow"])
    if meta.get("element"):
        gr.element = str(meta["element"])
    if meta.get("edge"):
        gr.edge = str(meta["edge"])
    if "frozen" in meta:
        gr.frozen = bool(meta["frozen"])
    if "marked" in meta:
        gr.marked = bool(meta["marked"])
    if meta.get("importance") is not None:
        try:
            gr.importance = float(meta["importance"])
        except (TypeError, ValueError):
            pass
    if meta.get("energy_shift") is not None:
        try:
            gr.energy_shift = float(meta["energy_shift"])
        except (TypeError, ValueError):
            pass


def load_all_from_prj(path: str) -> List[Tuple[str, np.ndarray, np.ndarray, dict]]:
    """Load every usable group from Athena .prj → (name, energy, mu, meta)."""
    L = _L()
    try:
        prj = L["read_athena"](
            str(path), do_preedge=False, do_bkg=False, do_fft=False
        )
    except Exception:
        prj = _read_athena_lenient(str(path))
        if prj is None:
            raise
    out = []
    seen = set()
    nnorm_is_degree = prj_nnorm_is_degree(getattr(prj, "header", ""))

    def _try_add(name: str, g) -> None:
        if g is None:
            return
        if name.startswith("_") or name in ("filename", "journal", "header", "groups", "file"):
            return
        if not (hasattr(g, "energy") and hasattr(g, "mu")):
            return
        try:
            e = np.asarray(g.energy, dtype=float)
            mu = np.asarray(g.mu, dtype=float)
        except Exception:
            return
        if e.size > 10 and mu.size == e.size:
            display = _prj_group_display_name(name, g)
            # de-dupe by display name (same group may appear as attr + dict key)
            if display in seen:
                return
            seen.add(display)
            out.append((display, e, mu, prj_meta_from_athena_group(g, nnorm_is_degree=nnorm_is_degree)))

    # Prefer official groups dict when present
    gdict = getattr(prj, "groups", None)
    if isinstance(gdict, dict) and gdict:
        for key, g in gdict.items():
            _try_add(str(key), g)

    # Also attributes / dir (AthenaGroup stores groups as attrs too)
    for key in dir(prj):
        if key.startswith("_"):
            continue
        _try_add(key, getattr(prj, key, None))

    if hasattr(prj, "__dict__"):
        for key, g in prj.__dict__.items():
            _try_add(str(key), g)

    return out


def _patch_athena_args_from_group(args: dict, gr: GroupRecord) -> None:
    """Write our AthenaSettings / flags into an Athena args dict after add_group."""
    s = gr.settings
    if s.e0 is not None:
        try:
            args["bkg_e0"] = float(s.e0)
        except (TypeError, ValueError):
            pass
    args["bkg_pre1"] = float(s.pre1)
    args["bkg_pre2"] = float(s.pre2)
    args["bkg_nor1"] = float(s.norm1)
    args["bkg_nor2"] = float(s.norm2)
    args["bkg_nnorm"] = larch_nnorm_to_athena(s.nnorm)
    args["bkg_flatten"] = "1" if s.make_flat else "0"
    args["bkg_rbkg"] = float(s.rbkg)
    args["bkg_kw"] = int(s.bkg_kweight)
    args["bkg_dk"] = float(s.bkg_dk)
    args["bkg_spl1"] = float(s.bkg_kmin)
    args["bkg_spl2"] = float(s.bkg_kmax)
    args["fft_kmin"] = float(s.kmin)
    args["fft_kmax"] = float(s.kmax)
    args["fft_dk"] = float(s.dk)
    args["fft_kwindow"] = str(s.kwindow or "hanning")
    args["fft_kw"] = int(s.kweight_plot)
    args["bft_rmin"] = float(s.rmin)
    args["bft_rmax"] = float(s.rmax)
    args["bft_dr"] = float(s.dr)
    args["bft_rwindow"] = str(s.rwindow or "hanning")
    args["frozen"] = "1" if gr.frozen else "0"
    args["marked"] = "1" if gr.marked else "0"
    args["importance"] = float(gr.importance)
    args["label"] = gr.name
    if gr.element:
        args["atsym"] = str(gr.element)
    if gr.edge:
        args["edge"] = str(gr.edge)
    args["bkg_eshift"] = float(getattr(gr, "energy_shift", 0.0) or 0.0)
    # Athena args are re-parsed as Python literals on read — raw Windows
    # backslashes break that eval, so paths are stored with forward slashes.
    if gr.path:
        fwd = str(gr.path).replace("\\", "/")
        args["source"] = fwd
        args["file"] = fwd


def save_project_to_prj(project, path: str | Path, *, use_gzip: bool = True) -> OpResult:
    """Write an Athena-compatible .prj from the in-memory multi-group project.

    Uses larch ``create_athena`` / ``AthenaProject.save`` (open API). Each group
    must have energy + μ. Store the unshifted grid plus ``bkg_eshift`` so
    reopen shows the same ΔE (v1.15.28). Baking E+ΔE and writing eshift=0
    made every reload look uncalibrated.
    """
    path = Path(path)
    if path.suffix.lower() != ".prj":
        path = path.with_suffix(".prj")

    groups = list(getattr(project, "groups", []) or [])
    usable = [
        g
        for g in groups
        if g is not None
        and getattr(g, "energy", None) is not None
        and getattr(g, "mu", None) is not None
    ]
    if not usable:
        return OpResult(False, "No groups with energy/μ to save")

    try:
        from larch import Group
        from larch.io import create_athena
    except Exception as exc:
        return OpResult(False, f"xraylarch not available: {exc}")

    try:
        # Never pass an existing path: AthenaProject.__init__ would READ the
        # old file, add_group would append, and load de-dupes by name keeping
        # the first (unmodified) copy. Overwrite must start empty (v1.15.24).
        ap = create_athena(None)
        n_ok = 0
        skipped: List[str] = []
        cubic: List[str] = []
        for gr in usable:
            try:
                e = np.asarray(gr.energy, dtype=float)
                mu = np.asarray(gr.mu, dtype=float)
                if e.size < 5 or mu.size != e.size or not np.all(np.isfinite(e)):
                    skipped.append(getattr(gr, "name", "?"))
                    continue
                g = Group(energy=e.copy(), mu=mu.copy())
                g.filename = str(gr.name)
                g.label = str(gr.name)
                # make_athena_args prefers a non-hex __name__ for the label
                try:
                    g.__name__ = str(gr.name)
                except Exception:
                    pass
                s = gr.settings
                if s.e0 is not None:
                    try:
                        g.e0 = float(s.e0)
                    except (TypeError, ValueError):
                        pass
                # Prefer known edge_step so add_group may skip re-running pre_edge
                edge_step = None
                if gr.session is not None and getattr(gr.session, "arrays", None) is not None:
                    es = getattr(gr.session.arrays, "edge_step", None)
                    if es is not None and np.isfinite(float(es)) and float(es) > 0:
                        edge_step = float(es)
                if edge_step is not None and hasattr(g, "e0"):
                    g.edge_step = edge_step
                g.energy_shift = float(getattr(gr, "energy_shift", 0.0) or 0.0)
                if gr.element:
                    g.atsym = str(gr.element)
                if gr.edge:
                    g.edge = str(gr.edge)

                ap.add_group(g)
                # Patch the group we just added (last matching label / all new)
                # add_group stores under a hashkey; find by energy identity or label.
                target = None
                for ag in ap.groups.values():
                    if getattr(ag, "label", None) == gr.name or getattr(ag, "filename", None) == gr.name:
                        target = ag
                if target is None and ap.groups:
                    target = list(ap.groups.values())[-1]
                if target is not None and isinstance(getattr(target, "args", None), dict):
                    _patch_athena_args_from_group(target.args, gr)
                    target.label = gr.name
                    target.filename = gr.name
                n_ok += 1
                if int(s.nnorm) > 2:
                    cubic.append(str(gr.name))
            except Exception as exc:
                skipped.append(f"{getattr(gr, 'name', '?')} ({exc})")

        if n_ok == 0:
            return OpResult(False, "No groups could be written to .prj: " + "; ".join(skipped[:5]))

        path.parent.mkdir(parents=True, exist_ok=True)
        # Save + mark a temp file, then replace: an unmarked file would be read
        # back as larch-degree coded (one order too high).
        tmp = path.with_name(path.name + ".tmp")
        try:
            ap.save(str(tmp), use_gzip=bool(use_gzip))
            _mark_prj_header(tmp)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        msg = f"Saved {n_ok} group(s) → {path}"
        if skipped:
            msg += f" (skipped {len(skipped)}: {', '.join(skipped[:5])})"
        if cubic:
            msg += (
                f"; cubic post-edge (order 3) has no Athena equivalent, saved as quadratic"
                f" for {len(cubic)}: {', '.join(cubic[:5])}"
            )
        return OpResult(True, msg, data={"path": str(path), "n": n_ok, "skipped": skipped, "cubic": cubic})
    except Exception as exc:
        return OpResult(False, f"Failed to save .prj: {exc}")


def smooth_mu(energy: np.ndarray, mu: np.ndarray, boxcar: int = 3) -> OpResult:
    try:
        from larch.math import boxcar as boxcar_fn
        from larch.math import smooth as smooth_fn

        if boxcar and boxcar > 1:
            y = boxcar_fn(mu, boxcar)
        else:
            # larch.math.smooth(x, y, sigma=1, ...) — lorentzian convolution
            y = smooth_fn(energy, mu)
        return OpResult(True, f"smoothed (boxcar={boxcar})", y)
    except Exception as exc:
        # numpy fallback
        w = max(1, int(boxcar))
        if w % 2 == 0:
            w += 1
        pad = w // 2
        yp = np.pad(mu, (pad, pad), mode="edge")
        kernel = np.ones(w) / w
        y = np.convolve(yp, kernel, mode="valid")
        return OpResult(True, f"smoothed numpy boxcar={w} ({exc})", y)


def rebin_group(
    energy: np.ndarray,
    mu: np.ndarray,
    e0: Optional[float] = None,
    pre1: Optional[float] = None,
    pre2: float = -30.0,
    pre_step: float = 2.0,
    xanes_step: Optional[float] = None,
    exafs1: float = 15.0,
    exafs2: Optional[float] = None,
    exafs_kstep: float = 0.05,
    method: str = "boxcar",
) -> OpResult:
    """Rebin μ(E) with larch rebin_xafs (Athena/Larix 3-region scan).

    Energy bounds are relative to *e0* (same convention as larch / Athena).
    """
    try:
        from larch.xafs import rebin_xafs
        from larch import Group

        g = Group()
        kw = {
            "group": g,
            "pre2": float(pre2),
            "pre_step": float(pre_step),
            "exafs1": float(exafs1),
            "exafs_kstep": float(exafs_kstep),
            "method": str(method or "boxcar").lower(),
        }
        if e0 is not None:
            kw["e0"] = float(e0)
        if pre1 is not None:
            kw["pre1"] = float(pre1)
        if xanes_step is not None:
            kw["xanes_step"] = float(xanes_step)
        if exafs2 is not None:
            kw["exafs2"] = float(exafs2)
        rebin_xafs(np.asarray(energy, dtype=float), np.asarray(mu, dtype=float), **kw)
        e2 = np.asarray(getattr(g, "energy", energy), dtype=float)
        m2 = np.asarray(getattr(g, "mu", mu), dtype=float)
        # larch may put result under g.rebinned
        rb = getattr(g, "rebinned", None)
        if rb is not None:
            e2 = np.asarray(getattr(rb, "energy", e2), dtype=float)
            m2 = np.asarray(getattr(rb, "mu", m2), dtype=float)
        return OpResult(
            True,
            f"rebin_xafs ok method={kw['method']} pre2={pre2} exafs1={exafs1} kstep={exafs_kstep}",
            (e2, m2),
        )
    except Exception as exc:
        # simple uniform rebin ~ half points
        n = max(50, len(energy) // 2)
        e2 = np.linspace(float(np.min(energy)), float(np.max(energy)), n)
        m2 = np.interp(e2, energy, mu)
        return OpResult(True, f"rebin fallback linspace ({exc})", (e2, m2))


def energy_align(
    energy: np.ndarray,
    mu: np.ndarray,
    ref_energy: np.ndarray,
    ref_mu: np.ndarray,
) -> OpResult:
    try:
        from larch.xafs import energy_align as ea, find_e0
        from larch import Group

        # larch's energy_align body calls `gradient` from the larch session
        # namespace, which does not exist when invoked as a plain function —
        # inject numpy's equivalent (identical ratio for uniform spacing)
        _fn = getattr(ea, "__wrapped__", ea)
        if "gradient" not in _fn.__globals__:
            _fn.__globals__["gradient"] = np.gradient

        g = Group(energy=np.asarray(energy, dtype=float).copy(), mu=np.asarray(mu, dtype=float).copy())
        ref = Group(energy=np.asarray(ref_energy, dtype=float).copy(), mu=np.asarray(ref_mu, dtype=float).copy())
        # larch's energy_align needs e0 on BOTH groups (fits dmude near the edge)
        for grp in (g, ref):
            if not hasattr(grp, "e0"):
                try:
                    find_e0(grp)
                except Exception:
                    grp.e0 = grp.energy[int(np.argmax(np.gradient(grp.mu)))]
        shift = ea(g, ref)
        if shift is None:
            shift = getattr(g, "eshift", 0.0)
        return OpResult(True, f"aligned (larch), shift={float(shift):.4f} eV", float(shift))
    except Exception as exc:
        # crude: align max of derivative peaks
        def peak_e(e, y):
            dy = np.gradient(y, e)
            return e[int(np.nanargmax(dy))]

        try:
            de = peak_e(ref_energy, ref_mu) - peak_e(energy, mu)
            return OpResult(True, f"align FALLBACK peak shift={de:.4f} ({exc})", float(de))
        except Exception as e2:
            return OpResult(False, f"align failed: {exc}; {e2}")


def deglitch_interp(
    energy: np.ndarray,
    mu: np.ndarray,
    bad_mask: np.ndarray,
) -> OpResult:
    """Remove glitch points (True=bad) by linear interpolation."""
    try:
        good = ~np.asarray(bad_mask, dtype=bool)
        if good.sum() < 10:
            return OpResult(False, "too few good points")
        e_g, m_g = energy[good], mu[good]
        m2 = np.interp(energy, e_g, m_g)
        # keep good points exact
        m2[good] = mu[good]
        nbad = int((~good).sum())
        return OpResult(True, f"deglitched {nbad} points", m2)
    except Exception as exc:
        return OpResult(False, str(exc))


def deglitch_auto(energy: np.ndarray, mu: np.ndarray, thresh: float = 3.0) -> OpResult:
    """Auto-deglitch isolated single-point spikes.

    A point is flagged when its deviation from the linear interpolation of
    its two neighbours (exact for non-uniform energy grids) exceeds
    ``thresh`` × the *local* robust noise (windowed MAD with the candidate's
    own ±s/2 poison excluded).  Candidates whose residual has the same sign
    as both neighbours are rejected as real curvature — XANES structure is
    never eaten.  Endpoints are never touched: interpolation cannot defend
    an unbracketed edge.

    Calibrated on MEX1_58271 (Ni K, 2026-08): catches the real jitter spikes
    on the noisy fluorescence channel at thresh 3 while the old global-MAD
    detector found nothing at 3 and 129 false points (edge rise included) at
    1.5.  Very clean channels may need thresh ≈ 2.5 for small spikes.
    """
    e = np.asarray(energy, dtype=float)
    y = np.asarray(mu, dtype=float).copy()
    n = len(y)
    bad = np.zeros(n, dtype=bool)
    if n < 5:
        return deglitch_interp(e, y, bad)
    win = 8
    yw = y.copy()
    # Single pass: iterative passes cascade false positives on patched data.
    t = (e[1:-1] - e[:-2]) / (e[2:] - e[:-2] + 1e-12)
    ypred = yw[:-2] + t * (yw[2:] - yw[:-2])
    res = yw[1:-1] - ypred
    loc = np.full(n - 2, 1e-12)
    for j in range(n - 2):
        lo, hi = max(0, j - win), min(n - 2, j + win + 1)
        # exclude the candidate and its two neighbours so a spike cannot
        # inflate its own noise bar (its ±s/2 poison sits right there)
        seg = np.concatenate((res[lo:max(lo, j - 1)], res[min(hi, j + 2):hi]))
        if seg.size < 4:
            seg = res[lo:hi]
        loc[j] = 1.4826 * np.median(np.abs(seg - np.median(seg))) + 1e-12
    cand = np.where(np.abs(res) / loc > float(thresh))[0]
    # Spike signature: the centre residual flips sign against both neighbours
    # ([-s/2, +s, -s/2]).  Real curvature bends the same way over several
    # points (same-sign run) and must never be eaten.
    isolated = []
    for j in cand:
        left_same = j > 0 and np.sign(res[j - 1]) == np.sign(res[j]) != 0
        right_same = j < n - 3 and np.sign(res[j + 1]) == np.sign(res[j]) != 0
        if not (left_same and right_same):
            isolated.append(j)
    if isolated:
        bad[1:-1][isolated] = True
        for i in np.where(bad)[0]:
            if 0 < i < n - 1:
                yw[i] = 0.5 * (yw[i - 1] + yw[i + 1])
    # np.interp can only hold a constant at an unbracketed endpoint.  That is
    # extrapolation, not a defensible deglitch, so leave both endpoints intact.
    if n:
        bad[0] = False
        bad[-1] = False
    result = deglitch_interp(e, y, bad)
    if result.ok and np.any(bad):
        picked = e[bad]
        shown = ", ".join(f"{value:.2f}" for value in picked[:12])
        if picked.size > 12:
            shown += f", … (+{picked.size - 12})"
        result.message = f"{result.message}; E={shown} eV"
    return result


def derivative(energy: np.ndarray, y: np.ndarray, order: int = 1) -> np.ndarray:
    out = y.astype(float)
    for _ in range(max(1, order)):
        out = np.gradient(out, energy)
    return out


def _larch_named_group(
    name: str,
    energy: np.ndarray,
    y: np.ndarray,
    *,
    arrayname: str = "norm",
) -> object:
    """Build a larch Group with a stable label so lincombo/pca report readable names.

    Note: larch lincombo/pca always use the attribute named ``energy`` as the
    abscissa. For χ(k) LCF/PCA, pass *k* as *energy* and set arrayname='chi'.
    """
    from larch import Group

    e = np.asarray(energy, dtype=float).ravel()
    yy = np.asarray(y, dtype=float).ravel()
    n = min(e.size, yy.size)
    e, yy = e[:n], yy[:n]
    arr = str(arrayname or "norm").lower()
    if arr in ("chi", "kchi", "k2chi", "k3chi"):
        # The caller already supplies any requested k weighting.  Store the
        # values as native chi so Larch does not apply a second weighting.
        g = Group(k=e.copy(), chi=yy.copy(), e0=0.0)
    else:
        g = Group(energy=e.copy())
        setattr(g, arr, yy.copy())
    g.filename = str(name)
    g.label = str(name)
    try:
        g.__name__ = str(name)
    except Exception:
        pass
    return g


def _larch_arrayname(arrayname: str) -> str:
    """Map smartLarch array labels to the native Larch attribute name."""
    arr = str(arrayname or "norm").lower()
    return "chi" if arr in ("chi", "kchi", "k2chi", "k3chi") else arr


def xanes_default_window(
    e0: float,
    *,
    lo_rel: float = -20.0,
    hi_rel: float = 60.0,
) -> Tuple[float, float]:
    """Athena-like default LCF/PCA energy window relative to E0 (absolute eV)."""
    e0 = float(e0)
    return e0 + float(lo_rel), e0 + float(hi_rel)


def _extract_lcf_ycomps(result) -> dict:
    """Pull component curves from a larch lincombo result if present."""
    for attr in ("ycomps", "fcomps"):
        obj = getattr(result, attr, None)
        if isinstance(obj, dict) and obj:
            return {
                str(k): np.asarray(v, dtype=float).copy()
                for k, v in obj.items()
                if np.asarray(v).size > 1
            }
    return {}


def _result_to_lcf_payload(result, *, arrayname: str, xmin, xmax, space: str = "E") -> dict:
    """Convert a larch lincombo result Group into a plain serializable dict."""
    weights = {
        str(k): float(v) for k, v in dict(getattr(result, "weights", {}) or {}).items()
    }
    e0_shift = 0.0
    try:
        p = getattr(result, "params", None)
        if p is not None and "e0_shift" in p:
            e0_shift = float(p["e0_shift"].value)
    except Exception:
        e0_shift = 0.0
    return {
        "engine": "larch.math.lincombo_fit",
        "arrayname": arrayname,
        "space": space,
        "weights": weights,
        "weights_lstsq": {
            str(k): float(v)
            for k, v in dict(getattr(result, "weights_lstsq", {}) or {}).items()
        },
        "rfactor": float(getattr(result, "rfactor", float("nan"))),
        "chisqr": float(getattr(result, "chisqr", float("nan"))),
        "redchi": float(getattr(result, "redchi", float("nan"))),
        "xmin": getattr(result, "xmin", xmin),
        "xmax": getattr(result, "xmax", xmax),
        "xdata": np.asarray(getattr(result, "xdata", []), dtype=float).copy(),
        "ydata": np.asarray(getattr(result, "ydata", []), dtype=float).copy(),
        "yfit": np.asarray(getattr(result, "yfit", []), dtype=float).copy(),
        "ycomps": _extract_lcf_ycomps(result),
        "e0_shift": e0_shift,
        "standard_names": list(weights.keys()),
    }


def lincombo_fit(
    energy: np.ndarray,
    data_y: np.ndarray,
    standards: Sequence[Tuple[str, np.ndarray, np.ndarray]],
    *,
    arrayname: str = "norm",
    xmin: Optional[float] = None,
    xmax: Optional[float] = None,
    sum_to_one: bool = True,
    vary_e0: bool = False,
    min_weight: Optional[float] = 0.0,
    data_name: str = "data",
) -> OpResult:
    """Linear combination via larch.math.lincombo_fit (not a reimplementation).

    *standards*: list of (name, x, y). For energy-space fits x is eV; for χ(k)
    pass k as x and arrayname='chi' (stored on Group.energy for larch).
    """
    energy = np.asarray(energy, dtype=float).ravel()
    data_y = np.asarray(data_y, dtype=float).ravel()
    if energy.size < 5 or data_y.size != energy.size:
        return OpResult(False, "LCF: data energy/y size mismatch or too short")
    finite = np.isfinite(energy) & np.isfinite(data_y)
    if finite.sum() < 5:
        return OpResult(False, "LCF: too few finite data points")
    energy = energy[finite]
    data_y = data_y[finite]
    if not standards:
        return OpResult(False, "LCF: no standards")

    try:
        from larch.math import lincombo_fit as larch_lincombo_fit
    except Exception as exc:
        return OpResult(False, f"LCF: larch.math.lincombo_fit unavailable: {exc}")

    space = "k" if str(arrayname).lower() in ("chi", "kchi", "k2chi", "k3chi") else "E"
    try:
        requested = [(data_name, energy, data_y), *standards]
        names, x_grid, matrix = _common_grid_matrix(requested, xmin=xmin, xmax=xmax)
        if len(names) != len(requested):
            return OpResult(False, "LCF: one or more spectra are unusable on the common range")
        larch_arr = _larch_arrayname(arrayname)
        gdat = _larch_named_group(names[0], x_grid, matrix[0], arrayname=larch_arr)
        comps = [
            _larch_named_group(name, x_grid, matrix[i], arrayname=larch_arr)
            for i, name in enumerate(names[1:], start=1)
        ]

        kw: dict = {
            "arrayname": larch_arr,
            "sum_to_one": bool(sum_to_one),
            "vary_e0": bool(vary_e0) and space == "E",
        }
        if space == "E":
            kw["xmin"] = float(x_grid[0])
            kw["xmax"] = float(x_grid[-1])
        ncomp = len(comps)
        if min_weight is not None and np.isfinite(min_weight):
            minw = float(min_weight)
            if minw < 0:
                return OpResult(False, "LCF: min_weight must be non-negative")
            if sum_to_one and ncomp * minw > 1.0 + 1e-12:
                return OpResult(
                    False,
                    f"LCF: infeasible bounds: {ncomp} x min_weight={ncomp * minw:.6g} > 1",
                )
            kw["minvals"] = [minw] * ncomp
            kw["maxvals"] = [1.0] * ncomp if sum_to_one else None

        result = larch_lincombo_fit(gdat, comps, **kw)
        payload = _result_to_lcf_payload(
            result, arrayname=arrayname, xmin=xmin, xmax=xmax, space=space
        )
        # Prefer extracted component curves
        yc = _extract_lcf_ycomps(result)
        if yc:
            payload["ycomps"] = yc
        # Ensure x/y arrays
        if payload["xdata"].size < 2:
            payload["xdata"] = x_grid.copy()
            payload["ydata"] = matrix[0].copy()
        payload["data_name"] = data_name
        wtxt = ", ".join(f"{k}={v:.4f}" for k, v in payload["weights"].items())
        msg = (
            f"LCF (larch) R={payload['rfactor']:.5g} redχ²={payload['redchi']:.4g}  "
            f"[{wtxt}]"
        )
        return OpResult(True, msg, payload)
    except Exception as exc:
        return OpResult(False, f"LCF (larch.math.lincombo_fit) failed: {exc}")


def lincombo_fitall(
    energy: np.ndarray,
    data_y: np.ndarray,
    standards: Sequence[Tuple[str, np.ndarray, np.ndarray]],
    *,
    arrayname: str = "norm",
    xmin: Optional[float] = None,
    xmax: Optional[float] = None,
    sum_to_one: bool = True,
    vary_e0: bool = False,
    min_weight: float = 0.0,
    max_ncomps: Optional[int] = None,
    max_output: int = 16,
    data_name: str = "data",
) -> OpResult:
    """Exhaustive subset LCF via larch.math.lincombo_fitall, ranked by redχ².

    Returns payload with ``rankings`` (list of per-combination dicts) and
    ``best`` = rankings[0] when non-empty.
    """
    energy = np.asarray(energy, dtype=float).ravel()
    data_y = np.asarray(data_y, dtype=float).ravel()
    finite = np.isfinite(energy) & np.isfinite(data_y)
    if finite.sum() < 5:
        return OpResult(False, "LCF-all: too few finite data points")
    energy, data_y = energy[finite], data_y[finite]
    if len(standards) < 2:
        return OpResult(False, "LCF-all: need ≥2 standards for combinatorial search")

    try:
        from larch.math import lincombo_fitall as larch_fitall
    except Exception as exc:
        return OpResult(False, f"LCF-all: lincombo_fitall unavailable: {exc}")

    space = "k" if str(arrayname).lower() in ("chi", "kchi", "k2chi", "k3chi") else "E"
    try:
        requested = [(data_name, energy, data_y), *standards]
        names, x_grid, matrix = _common_grid_matrix(requested, xmin=xmin, xmax=xmax)
        if len(names) != len(requested):
            return OpResult(False, "LCF-all: one or more spectra are unusable on the common range")
        larch_arr = _larch_arrayname(arrayname)
        gdat = _larch_named_group(names[0], x_grid, matrix[0], arrayname=larch_arr)
        comps = [
            _larch_named_group(name, x_grid, matrix[i], arrayname=larch_arr)
            for i, name in enumerate(names[1:], start=1)
        ]

        kw: dict = {
            "arrayname": larch_arr,
            "sum_to_one": bool(sum_to_one),
            "vary_e0": bool(vary_e0) and space == "E",
            "min_weight": float(min_weight) if min_weight is not None else 0.0005,
            "max_output": int(max_output),
        }
        if space == "E":
            kw["xmin"] = float(x_grid[0])
            kw["xmax"] = float(x_grid[-1])
        n = len(comps)
        max_use = n if max_ncomps is None or int(max_ncomps) <= 0 else min(n, int(max_ncomps))
        if min_weight is not None and np.isfinite(min_weight):
            minw = float(min_weight)
            if minw < 0:
                return OpResult(False, "LCF-all: min_weight must be non-negative")
            if sum_to_one and minw > 0:
                max_use = min(max_use, int(np.floor((1.0 + 1e-12) / minw)))
                if max_use < 2:
                    return OpResult(
                        False,
                        f"LCF-all: infeasible bounds for every combination: min_weight={minw:.6g}",
                    )
        kw["max_ncomps"] = max_use

        results = larch_fitall(gdat, comps, **kw)
        rankings = []
        for rank, result in enumerate(results or [], start=1):
            row = _result_to_lcf_payload(
                result, arrayname=arrayname, xmin=xmin, xmax=xmax, space=space
            )
            yc = _extract_lcf_ycomps(result)
            if yc:
                row["ycomps"] = yc
            row["rank"] = rank
            rankings.append(row)
        if not rankings:
            return OpResult(False, "LCF-all: no combination passed min_weight filter")
        best = rankings[0]
        payload = {
            "engine": "larch.math.lincombo_fitall",
            "arrayname": arrayname,
            "space": space,
            "data_name": data_name,
            "rankings": rankings,
            "best": best,
            # Flatten best into top-level for plot/export convenience
            **{k: best[k] for k in (
                "weights", "rfactor", "chisqr", "redchi", "xmin", "xmax",
                "xdata", "ydata", "yfit", "ycomps", "e0_shift", "standard_names",
            ) if k in best},
            "n_rankings": len(rankings),
        }
        msg = (
            f"LCF-all: {len(rankings)} combo(s); best redχ²={best.get('redchi', float('nan')):.5g}  "
            f"[{', '.join(f'{k}={v:.3f}' for k, v in (best.get('weights') or {}).items())}]"
        )
        return OpResult(True, msg, payload)
    except Exception as exc:
        return OpResult(False, f"LCF-all failed: {exc}")


def pca_train_fit(
    spectra: Sequence[Tuple[str, np.ndarray, np.ndarray]],
    *,
    ncomp: Optional[int] = None,
    arrayname: str = "norm",
    xmin: Optional[float] = None,
    xmax: Optional[float] = None,
    fit_target: Optional[str] = None,
    tt_spectra: Optional[Sequence[Tuple[str, np.ndarray, np.ndarray]]] = None,
) -> OpResult:
    """PCA via larch.math.pca_train + pca_fit.

    *spectra*: list of (name, x, y). Trains on all; fits *fit_target*
    (or the first spectrum). Also reports IND, nsig, abstract components,
    and optional Target-Transform SPOIL for *tt_spectra*.
    """
    if len(spectra) < 2:
        return OpResult(False, "PCA: need ≥2 spectra")
    try:
        from larch.math import pca_train, pca_fit
    except Exception as exc:
        return OpResult(False, f"PCA: larch.math.pca_* unavailable: {exc}")

    space = "k" if str(arrayname).lower() in ("chi", "kchi", "k2chi", "k3chi") else "E"
    try:
        names, x_grid, matrix = _common_grid_matrix(spectra, xmin=xmin, xmax=xmax)
        if len(names) != len(spectra):
            return OpResult(False, "PCA: one or more spectra are unusable on the common range")
        larch_arr = _larch_arrayname(arrayname)
        groups = [
            _larch_named_group(name, x_grid, matrix[i], arrayname=larch_arr)
            for i, name in enumerate(names)
        ]

        svals = np.linalg.svd(matrix, compute_uv=False)
        rank_tol = (
            max(matrix.shape)
            * np.finfo(float).eps
            * max(float(svals[0]), np.finfo(float).tiny)
        )
        effective_rank = int(np.sum(svals > rank_tol))
        if effective_rank < 2:
            return OpResult(False, f"PCA: training matrix is rank deficient (rank={effective_rank})")
        if ncomp is not None and int(ncomp) > effective_rank:
            return OpResult(
                False,
                f"PCA: requested ncomp={int(ncomp)} exceeds effective rank={effective_rank}",
            )

        tkw = {"arrayname": larch_arr}
        if space == "E":
            tkw["xmin"] = float(x_grid[0])
            tkw["xmax"] = float(x_grid[-1])
        model = pca_train(groups, **tkw)

        eig = np.asarray(getattr(model, "eigenvalues", []), dtype=float).ravel()
        var = np.asarray(getattr(model, "variances", []), dtype=float).ravel()
        ind = np.asarray(getattr(model, "ind", []), dtype=float).ravel()
        nsig = int(getattr(model, "nsig", 0) or 0)
        max_comp = int(eig.size) if eig.size else len(groups)
        if ncomp is None or ncomp <= 0:
            ncomp_use = max(1, nsig or min(3, max_comp))
        else:
            ncomp_use = max(1, min(int(ncomp), max_comp, effective_rank))

        # Abstract component spectra on the PCA energy grid
        x_model = np.asarray(getattr(model, "x", []), dtype=float).ravel()
        mean = np.asarray(getattr(model, "mean", []), dtype=float).ravel()
        comps = np.asarray(getattr(model, "components", []), dtype=float)
        ycomps_abs: dict = {}
        if comps.ndim == 2 and comps.shape[0] > 0 and x_model.size:
            for i in range(min(ncomp_use, comps.shape[0])):
                ycomps_abs[f"PC{i + 1}"] = np.asarray(comps[i], dtype=float).copy()
            if mean.size == x_model.size:
                ycomps_abs["mean"] = mean.copy()

        target = fit_target if fit_target in names else names[0]
        fits = {}
        target_payload = None
        for row_index, (g, name) in enumerate(zip(groups, names)):
            try:
                pca_fit(g, model, ncomps=ncomp_use, rescale=True)
            except Exception as fe:
                # Current Larch indexes lstsq's residual array at [0].  That
                # array is legitimately empty for an exact/rank-complete fit.
                # Recover that one known upstream edge case directly from the
                # Larch PCA model; propagate every other error normally.
                if "index 0 is out of bounds" in str(fe):
                    comp_matrix = np.asarray(model.components[:ncomp_use], dtype=float).T
                    y_row = np.asarray(matrix[row_index], dtype=float)
                    model_mean = np.asarray(model.mean, dtype=float)
                    weights, *_ = np.linalg.lstsq(comp_matrix, y_row - model_mean, rcond=None)
                    y_fit = comp_matrix @ weights + model_mean
                    row = {
                        "weights": [float(w) for w in np.asarray(weights).ravel()],
                        "chi_square": float(np.sum((y_row - y_fit) ** 2)),
                        "x": x_grid.copy(),
                        "ydat": y_row.copy(),
                        "yfit": y_fit.copy(),
                    }
                    fits[name] = row
                    if name == target:
                        target_payload = row
                    continue
                fits[name] = {"error": str(fe)}
                continue
            pr = getattr(g, "pca_result", None)
            if pr is None:
                fits[name] = {"error": "no pca_result"}
                continue
            chi_raw = getattr(pr, "chi_square", float("nan"))
            try:
                chi_arr = np.asarray(chi_raw, dtype=float).ravel()
                chi_val = float(chi_arr[0]) if chi_arr.size else float("nan")
            except Exception:
                try:
                    chi_val = float(chi_raw)
                except Exception:
                    chi_val = float("nan")
            row = {
                "weights": [float(w) for w in np.asarray(getattr(pr, "weights", []), dtype=float).ravel()],
                "chi_square": chi_val,
                "x": np.asarray(getattr(pr, "x", []), dtype=float).copy(),
                "ydat": np.asarray(getattr(pr, "ydat", []), dtype=float).copy(),
                "yfit": np.asarray(getattr(pr, "yfit", []), dtype=float).copy(),
            }
            fits[name] = row
            if name == target:
                target_payload = row

        if not any("error" not in row for row in fits.values()):
            details = "; ".join(f"{name}: {row.get('error')}" for name, row in fits.items())
            return OpResult(False, f"PCA: no spectrum fit succeeded ({details})")

        # Target transform + SPOIL for external candidate standards
        tt_rows = []
        if tt_spectra:
            tt_rows = _pca_target_transforms(
                model, tt_spectra, ncomp=ncomp_use, arrayname=arrayname
            )

        cum_var = []
        acc = 0.0
        for v in var:
            acc += float(v)
            cum_var.append(acc)

        payload = {
            "engine": "larch.math.pca_train+pca_fit",
            "arrayname": arrayname,
            "space": space,
            "names": names,
            "ncomp": ncomp_use,
            "effective_rank": effective_rank,
            "nsig": nsig,
            "suggested_ncomp": max(1, nsig or 1),
            "eigenvalues": eig.tolist(),
            "variances": var.tolist(),
            "cum_variance": cum_var,
            "ind": ind.tolist(),
            "xmin": getattr(model, "xmin", xmin),
            "xmax": getattr(model, "xmax", xmax),
            "fits": fits,
            "target": target,
            "xdata": target_payload["x"] if target_payload else x_model.copy(),
            "ydata": target_payload["ydat"] if target_payload else np.asarray([]),
            "yfit": target_payload["yfit"] if target_payload else np.asarray([]),
            "ycomps": ycomps_abs,
            "tt": tt_rows,
        }
        var_txt = ", ".join(f"{100 * float(v):.1f}%" for v in var[:ncomp_use])
        msg = (
            f"PCA (larch) ncomp={ncomp_use} nsig={nsig}  "
            f"var=[{var_txt}]  target={target}"
        )
        return OpResult(True, msg, payload)
    except Exception as exc:
        return OpResult(False, f"PCA (larch) failed: {exc}")


def _pca_target_transforms(
    model,
    tt_spectra: Sequence[Tuple[str, np.ndarray, np.ndarray]],
    *,
    ncomp: int,
    arrayname: str,
) -> List[dict]:
    """Malinowski-style Target Transform + SPOIL for candidate references.

    Projects each candidate onto the first *ncomp* abstract components of
    *model* and scores ``SPOIL = RET / REP``, where
    ``RET = sqrt(max(AET² - REP², 0))`` and ``REP = RE * ||w||``.
    Including the target-transform weight norm makes the ranking invariant to
    a pure change of candidate-spectrum scale.

    - SPOIL < 3: excellent match
    - 3–6: acceptable
    - > 6: unlikely present in the training subspace
    """
    try:
        from larch.math.pca import interp  # type: ignore
    except Exception:
        interp = None
    x_model = np.asarray(getattr(model, "x", []), dtype=float).ravel()
    mean = np.asarray(getattr(model, "mean", []), dtype=float).ravel()
    comps = np.asarray(getattr(model, "components", []), dtype=float)
    eig = np.asarray(getattr(model, "eigenvalues", []), dtype=float).ravel()
    if x_model.size < 5 or comps.ndim != 2 or comps.shape[0] < 1:
        return []
    n = max(1, min(int(ncomp), comps.shape[0]))
    C = comps[:n].T  # (nE, n)
    p = int(x_model.size)
    m = int(getattr(model, "ydat", np.zeros((1, p))).shape[0]) if hasattr(model, "ydat") else max(n + 1, 2)
    # Real error of data matrix for n factors (Malinowski RE)
    if n < eig.size:
        re = float(np.sqrt(max(float(eig[n:].sum()), 0.0) / max(1.0, m * max(1, p - n))))
    else:
        re = 0.0

    rows = []
    for name, e, y in tt_spectra:
        e = np.asarray(e, dtype=float).ravel()
        y = np.asarray(y, dtype=float).ravel()
        nn = min(e.size, y.size)
        if nn < 5:
            continue
        e, y = e[:nn], y[:nn]
        # Interpolate onto PCA grid
        try:
            if interp is not None:
                y_i = np.asarray(interp(e, y, x_model, kind="cubic"), dtype=float)
            else:
                y_i = np.interp(x_model, e, y)
        except Exception:
            y_i = np.interp(x_model, e, y)
        if y_i.size != x_model.size or not np.isfinite(y_i).all():
            continue
        y_c = y_i - mean if mean.size == y_i.size else y_i
        try:
            w, *_ = np.linalg.lstsq(C, y_c, rcond=None)
        except Exception:
            continue
        y_trans = (C @ w) + (mean if mean.size == y_i.size else 0.0)
        resid = y_i - y_trans
        # Malinowski target testing: AET is the apparent target error,
        # REP = RE * ||w|| is propagated experimental error, and
        # RET = sqrt(AET^2 - REP^2).  The weight norm is what makes SPOIL
        # invariant to a change of target-spectrum scale.
        aet = float(np.sqrt(np.sum(resid ** 2) / max(1, p - n)))
        rfactor = float(np.sum(resid ** 2) / max(np.sum(y_i ** 2), 1e-30))
        train = np.asarray(getattr(model, "ydat", []), dtype=float)
        train_scale = float(np.nanstd(train)) if train.size else 1.0
        re_eff = max(re, np.finfo(float).eps * max(train_scale, 1.0), 1e-15)
        rep = re_eff * float(np.linalg.norm(w))
        ret = float(np.sqrt(max(0.0, aet * aet - rep * rep)))
        spoil = float(ret / rep) if rep > 0 else (0.0 if aet <= re_eff else float("inf"))
        if spoil < 3:
            grade = "excellent"
        elif spoil <= 6:
            grade = "acceptable"
        else:
            grade = "poor"
        rows.append(
            {
                "name": str(name),
                "spoil": spoil,
                "grade": grade,
                "ret": ret,
                "aet": aet,
                "rep": rep,
                "edl": rep,
                "rfactor": rfactor,
                "weights": [float(x) for x in np.asarray(w).ravel()],
                "x": x_model.copy(),
                "ydata": y_i.copy(),
                "yfit": np.asarray(y_trans, dtype=float).copy(),
            }
        )
    rows.sort(key=lambda r: r["spoil"])
    return rows


def _common_grid_matrix(
    spectra: Sequence[Tuple[str, np.ndarray, np.ndarray]],
    *,
    xmin: Optional[float] = None,
    xmax: Optional[float] = None,
    n_points: Optional[int] = None,
) -> Tuple[List[str], np.ndarray, np.ndarray]:
    """Interpolate spectra onto a shared abscissa; return names, x_grid, D (n_spec × n_x)."""
    cleaned: List[Tuple[str, np.ndarray, np.ndarray]] = []
    for name, e, y in spectra:
        e = np.asarray(e, dtype=float).ravel()
        y = np.asarray(y, dtype=float).ravel()
        n = min(e.size, y.size)
        if n < 5:
            continue
        e, y = e[:n], y[:n]
        m = np.isfinite(e) & np.isfinite(y)
        e, y = e[m], y[m]
        if e.size < 5:
            continue
        order = np.argsort(e, kind="mergesort")
        e, y = e[order], y[order]
        # np.interp is order-dependent when x has duplicates.  Collapse each
        # repeated coordinate to the finite arithmetic mean first.
        unique_e, inverse, counts = np.unique(e, return_inverse=True, return_counts=True)
        if unique_e.size != e.size:
            sums = np.zeros(unique_e.size, dtype=float)
            np.add.at(sums, inverse, y)
            e = unique_e
            y = sums / counts
        if e.size < 5 or np.any(np.diff(e) <= 0):
            continue
        cleaned.append((str(name), e, y))
    if len(cleaned) < 2:
        raise ValueError("need ≥2 usable spectra for matrix assembly")

    lo = max(float(np.min(e)) for _, e, _ in cleaned)
    hi = min(float(np.max(e)) for _, e, _ in cleaned)
    if xmin is not None and np.isfinite(xmin):
        lo = max(lo, float(xmin))
    if xmax is not None and np.isfinite(xmax):
        hi = min(hi, float(xmax))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        raise ValueError(f"invalid common range: {lo} … {hi}")

    # densest spectrum in-window sets grid density; floor for XANES
    dens = 0
    for _, e, _ in cleaned:
        dens = max(dens, int(np.sum((e >= lo) & (e <= hi))))
    npts = int(n_points) if n_points and n_points >= 20 else max(50, dens)
    x_grid = np.linspace(lo, hi, npts)
    rows = []
    names = []
    for name, e, y in cleaned:
        yi = np.interp(x_grid, e, y)
        if not np.isfinite(yi).all():
            continue
        rows.append(yi)
        names.append(name)
    if len(rows) < 2:
        raise ValueError("fewer than 2 spectra after interpolation")
    D = np.vstack(rows)  # (n_samples, n_features)
    return names, x_grid, D


def _mcr_init_ST(
    D: np.ndarray,
    ncomp: int,
    *,
    init: str = "svd",
    nonneg: bool = True,
) -> np.ndarray:
    """Initial pure-spectra matrix ST (ncomp × n_features)."""
    n_spec, n_x = D.shape
    ncomp = max(1, min(int(ncomp), n_spec, n_x))
    init = (init or "svd").lower()
    if init == "random":
        rng = np.random.default_rng(0)
        ST0 = rng.standard_normal((ncomp, n_x))
        if nonneg:
            ST0 = np.abs(ST0)
        ST0 /= np.maximum(ST0.max(axis=1, keepdims=True), 1e-12)
        return ST0
    if not nonneg:
        # Signed spectra plus non-negative/closure concentrations need an
        # initialization inside the observed mixture cone.  Raw SVD vectors
        # have arbitrary signs/rotations and can make every OLS concentration
        # negative on the first half-step.  Successive projection chooses
        # diverse observed rows without imposing positivity on the spectra.
        centered = D - D.mean(axis=0, keepdims=True)
        selected = [int(np.argmax(np.linalg.norm(centered, axis=1)))]
        while len(selected) < ncomp:
            basis = np.asarray(D[selected], dtype=float).T
            q, _ = np.linalg.qr(basis)
            residual = D - (D @ q) @ q.T
            score = np.linalg.norm(residual, axis=1)
            score[selected] = -np.inf
            selected.append(int(np.argmax(score)))
        return np.asarray(D[selected], dtype=float).copy()
    try:
        _U, _s, Vt = np.linalg.svd(D, full_matrices=False)
    except Exception:
        _U, _s, Vt = np.linalg.svd(D - D.mean(axis=0), full_matrices=False)
    ST0 = np.asarray(Vt[:ncomp], dtype=float).copy()
    for i in range(ST0.shape[0]):
        if float(np.sum(ST0[i])) < 0:
            ST0[i] *= -1.0
    if nonneg:
        ST0 = np.clip(ST0, 0.0, None)
    if not np.isfinite(ST0).all() or float(ST0.max()) <= 0:
        ST0 = np.abs(Vt[:ncomp])
    scale = float(np.nanmax(np.abs(D))) or 1.0
    for i in range(ST0.shape[0]):
        m = float(np.nanmax(ST0[i]))
        if m > 0:
            ST0[i] *= scale / m
    return ST0


def _mcr_solve_rows(
    A: np.ndarray,
    B: np.ndarray,
    *,
    nonneg: bool,
    regr: str,
    closure: bool = False,
) -> np.ndarray:
    """Solve A @ X.T ≈ B.T row-wise (B rows are targets). A is (n_feat, n_comp)."""
    from scipy.optimize import nnls

    n_rows = B.shape[0]
    n_comp = A.shape[1]
    out = np.zeros((n_rows, n_comp), dtype=float)
    # Keep the regressor contract identical to pyMCR: NNLS performs the
    # constrained regression, while OLS solves first and then applies the
    # requested non-negativity constraint.
    use_nnls = str(regr).upper() == "NNLS"
    for i in range(n_rows):
        y = B[i]
        if use_nnls:
            x, _ = nnls(A, y)
        else:
            x, *_ = np.linalg.lstsq(A, y, rcond=None)
            if nonneg:
                x = np.clip(x, 0.0, None)
        if closure:
            s = float(np.sum(x))
            if s > 1e-15:
                x = x / s
        out[i] = x
    return out


def _mcr_als_builtin(
    D: np.ndarray,
    ST0: np.ndarray,
    *,
    c_nonneg: bool,
    c_closure: bool,
    st_nonneg: bool,
    c_regr: str,
    st_regr: str,
    max_iter: int,
    tol_err_change: float,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """Bilinear ALS with non-neg / closure (numpy + scipy.nnls). No pyMCR needed."""
    ST = np.asarray(ST0, dtype=float).copy()
    n_spec = D.shape[0]
    max_iter = int(max(1, max_iter))
    err_hist: List[float] = []
    C = np.zeros((n_spec, ST.shape[0]), dtype=float)
    best_err = float("inf")
    best_C, best_ST = C.copy(), ST.copy()
    n_iter_opt = 0
    exit_reason = "max_iter"

    for it in range(1, max_iter + 1):
        # C: D ≈ C @ ST  →  ST.T @ c ≈ d  (per spectrum)
        C = _mcr_solve_rows(
            ST.T,
            D,
            nonneg=c_nonneg,
            regr=c_regr,
            closure=c_closure,
        )
        # ST: D.T ≈ ST.T @ C.T  →  C @ st ≈ d_channel  (per energy)
        ST = _mcr_solve_rows(
            C,
            D.T,
            nonneg=st_nonneg,
            regr=st_regr,
            closure=False,
        ).T

        resid = D - C @ ST
        err = float(np.sum(resid ** 2))
        err_hist.append(err)
        if err < best_err:
            best_err = err
            best_C, best_ST = C.copy(), ST.copy()
            n_iter_opt = it
        if len(err_hist) >= 2:
            prev = err_hist[-2]
            if prev > 0 and abs(err - prev) / prev < float(tol_err_change):
                exit_reason = "tol_err_change"
                break
        if not np.isfinite(err):
            exit_reason = "non_finite"
            break

    meta = {
        "n_iter": int(len(err_hist)),
        "n_iter_opt": int(n_iter_opt),
        "exit_reasons": [exit_reason],
        "err_final": float(best_err) if np.isfinite(best_err) else float("nan"),
        "err_hist": err_hist,
    }
    return best_C, best_ST, meta


def _pymcr_safe_to_import() -> bool:
    """True only if pymcr imports cleanly in a *subprocess*.

    Some ProgramData xraylarch builds hard-crash (not raise) on ``import pymcr``
    when the wheel is only in the user site-packages. Probing in-process would
    kill the GUI — always probe out-of-process first.
    """
    import subprocess
    import sys

    try:
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pymcr.mcr import McrAR; from pymcr.constraints import ConstraintNonneg; print('OK')",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return r.returncode == 0 and "OK" in (r.stdout or "")
    except Exception:
        return False


def _mcr_als_pymcr(
    D: np.ndarray,
    ST0: np.ndarray,
    *,
    c_nonneg: bool,
    c_closure: bool,
    st_nonneg: bool,
    c_regr: str,
    st_regr: str,
    max_iter: int,
    tol_err_change: float,
    tol_increase: float,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    from pymcr.mcr import McrAR
    from pymcr.constraints import ConstraintNonneg, ConstraintNorm
    import logging

    c_constraints = []
    if c_nonneg:
        c_constraints.append(ConstraintNonneg())
    if c_closure:
        c_constraints.append(ConstraintNorm())
    st_constraints = []
    if st_nonneg:
        st_constraints.append(ConstraintNonneg())

    mcr = McrAR(
        c_regr=str(c_regr or "OLS").upper(),
        st_regr=str(st_regr or "OLS").upper(),
        c_constraints=c_constraints,
        st_constraints=st_constraints,
        max_iter=int(max(1, max_iter)),
        tol_increase=float(tol_increase) if tol_increase is not None else None,
        tol_err_change=float(tol_err_change) if tol_err_change is not None else None,
    )
    for name in ("pymcr", "pymcr.mcr"):
        log = logging.getLogger(name)
        log.setLevel(logging.ERROR)
        log.propagate = False
        for h in list(log.handlers):
            log.removeHandler(h)
    mcr.fit(D, ST=ST0)

    C = np.asarray(
        getattr(mcr, "C_opt_", None)
        if getattr(mcr, "C_opt_", None) is not None
        else mcr.C_,
        dtype=float,
    )
    ST = np.asarray(
        getattr(mcr, "ST_opt_", None)
        if getattr(mcr, "ST_opt_", None) is not None
        else mcr.ST_,
        dtype=float,
    )
    exits = []
    for attr, lab in (
        ("exit_max_iter_reached", "max_iter"),
        ("exit_tol_increase", "tol_increase"),
        ("exit_tol_n_increase", "tol_n_increase"),
        ("exit_tol_err_change", "tol_err_change"),
        ("exit_tol_n_above_min", "tol_n_above_min"),
    ):
        if getattr(mcr, attr, False):
            exits.append(lab)
    err_list = list(getattr(mcr, "err", None) or [])
    meta = {
        "n_iter": int(getattr(mcr, "n_iter", 0) or 0),
        "n_iter_opt": getattr(mcr, "n_iter_opt", None),
        "exit_reasons": exits or ["ok"],
        "err_final": float(err_list[-1]) if err_list else float("nan"),
    }
    return C, ST, meta


def mcr_als_fit(
    spectra: Sequence[Tuple[str, np.ndarray, np.ndarray]],
    *,
    ncomp: int = 2,
    arrayname: str = "norm",
    xmin: Optional[float] = None,
    xmax: Optional[float] = None,
    c_nonneg: bool = True,
    c_closure: bool = True,
    st_nonneg: Optional[bool] = None,
    c_regr: str = "OLS",
    st_regr: str = "OLS",
    max_iter: int = 100,
    tol_err_change: float = 1e-8,
    tol_increase: float = 1.0,
    init: str = "svd",
) -> OpResult:
    """MCR-ALS: prefer pyMCR when safe; always fall back to built-in ALS.

    Pipeline (docs/PCA_LCF_Pre-edge fitting):
      common energy grid → optional rank from PCA elsewhere → ALS with
      non-negativity + closure → pure spectra S^T and concentrations C.

    *spectra*: list of (name, x, y). Matrix D is (n_spectra × n_energy).

    Engine selection (newbie-safe):
      1. Subprocess-probe ``pymcr`` (avoids hard-crash on some ProgramData builds)
      2. Else built-in ALS (numpy + scipy.optimize.nnls) — same constraints, no extra install
      Install-Runtime still ships pymcr for the preferred path.
    """
    if len(spectra) < 2:
        return OpResult(False, "MCR-ALS: need ≥2 spectra")

    space = "k" if str(arrayname).lower() in ("chi", "kchi", "k2chi", "k3chi") else "E"
    try:
        names, x_grid, D = _common_grid_matrix(spectra, xmin=xmin, xmax=xmax)
    except Exception as exc:
        return OpResult(False, f"MCR-ALS: grid assembly failed: {exc}")
    if len(names) != len(spectra):
        return OpResult(False, "MCR-ALS: one or more spectra are unusable on the common range")

    n_spec, n_x = D.shape
    try:
        ncomp_use = int(ncomp)
    except (TypeError, ValueError):
        return OpResult(False, "MCR-ALS: ncomp must be an integer")
    if ncomp_use < 1 or ncomp_use > min(n_spec, n_x):
        return OpResult(
            False,
            f"MCR-ALS: ncomp={ncomp_use} is outside 1..{min(n_spec, n_x)}",
        )

    magnitude = max(float(np.max(np.abs(D))), np.finfo(float).tiny)
    zero_tol = np.finfo(float).eps * max(D.shape) * magnitude
    row_norms = np.linalg.norm(D, axis=1)
    if np.any(row_norms <= zero_tol):
        bad = [names[i] for i in np.where(row_norms <= zero_tol)[0]]
        return OpResult(False, f"MCR-ALS: zero or near-zero spectrum: {', '.join(bad)}")
    row_variation = np.linalg.norm(D - D.mean(axis=1, keepdims=True), axis=1)
    if np.any(row_variation <= zero_tol):
        bad = [names[i] for i in np.where(row_variation <= zero_tol)[0]]
        return OpResult(False, f"MCR-ALS: zero spectral variance: {', '.join(bad)}")
    singular = np.linalg.svd(D, compute_uv=False)
    rank_tol = (
        max(D.shape)
        * np.finfo(float).eps
        * max(float(singular[0]), np.finfo(float).tiny)
    )
    effective_rank = int(np.sum(singular > rank_tol))
    if ncomp_use > effective_rank:
        return OpResult(
            False,
            f"MCR-ALS: requested ncomp={ncomp_use} exceeds effective rank={effective_rank}",
        )

    signed_space = str(arrayname).lower() in ("chi", "kchi", "k2chi", "k3chi", "dmude")
    st_nonneg_use = (not signed_space) if st_nonneg is None else bool(st_nonneg)
    # pyMCR's tol_err_change is absolute.  Work on a dimensionless matrix so
    # changing intensity units cannot change the stopping point, then restore
    # the pure spectra to the original y units.
    data_scale = float(np.sqrt(np.mean(D ** 2)))
    if not np.isfinite(data_scale) or data_scale <= zero_tol:
        return OpResult(False, "MCR-ALS: zero or invalid signal scale")
    D_work = D / data_scale

    try:
        ST0 = _mcr_init_ST(D_work, ncomp_use, init=init, nonneg=st_nonneg_use)
    except Exception as exc:
        return OpResult(False, f"MCR-ALS: initialization failed: {exc}")

    engine = "builtin.als"
    engine_note = ""
    try:
        if _pymcr_safe_to_import():
            C, ST, meta = _mcr_als_pymcr(
                D_work,
                ST0,
                c_nonneg=c_nonneg,
                c_closure=c_closure,
                st_nonneg=st_nonneg_use,
                c_regr=c_regr,
                st_regr=st_regr,
                max_iter=max_iter,
                tol_err_change=tol_err_change,
                tol_increase=tol_increase,
            )
            engine = "pymcr.mcr.McrAR"
        else:
            C, ST, meta = _mcr_als_builtin(
                D_work,
                ST0,
                c_nonneg=c_nonneg,
                c_closure=c_closure,
                st_nonneg=st_nonneg_use,
                c_regr=c_regr,
                st_regr=st_regr,
                max_iter=max_iter,
                tol_err_change=tol_err_change,
            )
            engine_note = (
                "pyMCR unavailable/unstable in this Python — used built-in ALS "
                "(numpy+scipy). Install-Runtime installs pymcr for the preferred engine."
            )
    except Exception as exc:
        # Last resort: always try built-in
        try:
            C, ST, meta = _mcr_als_builtin(
                D_work,
                ST0,
                c_nonneg=c_nonneg,
                c_closure=c_closure,
                st_nonneg=st_nonneg_use,
                c_regr=c_regr,
                st_regr=st_regr,
                max_iter=max_iter,
                tol_err_change=tol_err_change,
            )
            engine = "builtin.als"
            engine_note = f"pyMCR path failed ({exc}); used built-in ALS."
        except Exception as exc2:
            return OpResult(False, f"MCR-ALS failed: {exc2}")

    if C.ndim != 2 or ST.ndim != 2:
        return OpResult(False, "MCR-ALS: empty C/ST result")
    if not np.isfinite(C).all() or not np.isfinite(ST).all():
        return OpResult(False, "MCR-ALS: solver returned non-finite C/ST")
    if c_closure and not np.allclose(C.sum(axis=1), 1.0, rtol=0, atol=1e-6):
        return OpResult(False, "MCR-ALS: concentration closure could not be satisfied")
    ST = ST * data_scale

    D_hat = C @ ST
    resid = D - D_hat
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((D - D.mean()) ** 2))
    r2 = float("nan") if ss_tot <= zero_tol ** 2 else 1.0 - ss_res / ss_tot
    lof = 100.0 * float(np.sqrt(ss_res / max(float(np.sum(D ** 2)), 1e-30)))
    rmse = float(np.sqrt(ss_res / max(D.size, 1)))
    mse = float(ss_res / max(D.size, 1))

    pure_names = [f"Pure{i + 1}" for i in range(ST.shape[0])]
    ycomps = {pn: ST[i].copy() for i, pn in enumerate(pure_names)}
    ydata_mean = D.mean(axis=0)
    yfit_mean = D_hat.mean(axis=0)

    concentrations = []
    for i, name in enumerate(names):
        concentrations.append(
            {
                "name": name,
                "weights": [float(C[i, j]) for j in range(C.shape[1])],
                "rmse": float(np.sqrt(np.mean(resid[i] ** 2))),
            }
        )

    pure_groups = []
    for i, pn in enumerate(pure_names):
        pure_groups.append(
            {
                "name": pn,
                "x": x_grid.copy(),
                "y": ST[i].copy(),
                "arrayname": arrayname,
                "note": f"MCR-ALS pure component {i + 1}/{ncomp_use} ({arrayname})",
            }
        )

    payload = {
        "engine": engine,
        "engine_note": engine_note,
        "arrayname": arrayname,
        "space": space,
        "names": names,
        "ncomp": ncomp_use,
        "n_spectra": n_spec,
        "n_points": int(n_x),
        "xmin": float(x_grid[0]),
        "xmax": float(x_grid[-1]),
        "c_nonneg": bool(c_nonneg),
        "c_closure": bool(c_closure),
        "st_nonneg": bool(st_nonneg_use),
        "c_regr": str(c_regr).upper(),
        "st_regr": str(st_regr).upper(),
        "max_iter": int(max_iter),
        "n_iter": int(meta.get("n_iter") or 0),
        "n_iter_opt": meta.get("n_iter_opt"),
        "exit_reasons": list(meta.get("exit_reasons") or []),
        "error_metric": "MSE",
        "err_final": mse,
        "sse": ss_res,
        "mse": mse,
        "r2": r2,
        "lof_percent": lof,
        "rmse": rmse,
        "init": (init or "svd").lower(),
        "effective_rank": effective_rank,
        "data_scale": data_scale,
        "concentrations": concentrations,
        "C": C.copy(),
        "ST": ST.copy(),
        "pure_names": pure_names,
        "pure_groups": pure_groups,
        "xdata": x_grid.copy(),
        "ydata": ydata_mean.copy(),
        "yfit": yfit_mean.copy(),
        "ycomps": ycomps,
        "target": "mean(series)",
    }
    tag = "pyMCR" if engine.startswith("pymcr") else "builtin"
    msg = (
        f"MCR-ALS ({tag}) ncomp={ncomp_use} n_spec={n_spec}  "
        f"R²={r2:.4f} LOF={lof:.2f}%  iter={payload['n_iter']}"
    )
    return OpResult(True, msg, payload)


def format_mcr_report(data: dict) -> str:
    """Human-readable MCR-ALS report (concentrations + diagnostics)."""
    unit = "Å⁻¹" if data.get("space") == "k" else "eV"
    eng = str(data.get("engine") or "builtin.als")
    lines = [
        f"=== MCR-ALS  ({eng}) ===",
        f"engine: {eng}",
    ]
    if data.get("engine_note"):
        lines.append(f"note: {data['engine_note']}")
    lines += [
        f"array: {data.get('arrayname', 'norm')}   space: {data.get('space', 'E')}",
        f"ncomp={data.get('ncomp')}   n_spectra={data.get('n_spectra')}   "
        f"n_points={data.get('n_points')}",
        f"range: {data.get('xmin')} … {data.get('xmax')} {unit}",
        f"init: {data.get('init', 'svd')}   "
        f"c_regr={data.get('c_regr')}  st_regr={data.get('st_regr')}",
        f"constraints: C_nonneg={data.get('c_nonneg')}  "
        f"C_closure={data.get('c_closure')}  ST_nonneg={data.get('st_nonneg')}",
        f"iter: {data.get('n_iter')} (opt @ {data.get('n_iter_opt')})  "
        f"max_iter={data.get('max_iter')}",
        f"exit: {', '.join(data.get('exit_reasons') or ['ok'])}",
        f"R²={float(data.get('r2', float('nan'))):.5f}  "
        f"LOF={float(data.get('lof_percent', float('nan'))):.3f}%  "
        f"RMSE={float(data.get('rmse', float('nan'))):.5g}  "
        f"MSE={float(data.get('mse', data.get('err_final', float('nan')))):.5g}",
        "",
        "Concentrations C (rows=spectra, cols=pure components):",
    ]
    pnames = data.get("pure_names") or [
        f"Pure{i + 1}" for i in range(int(data.get("ncomp") or 0))
    ]
    header = "  spectrum".ljust(24) + "".join(f"{p:>12s}" for p in pnames) + "  RMSE"
    lines.append(header)
    for row in data.get("concentrations") or []:
        w = row.get("weights") or []
        wtxt = "".join(f"{float(x):12.4f}" for x in w)
        lines.append(
            f"  {str(row.get('name', '?'))[:22]:22s}{wtxt}  "
            f"{float(row.get('rmse', float('nan'))):.4g}"
        )
    lines.append("")
    lines.append(
        "Pure spectra S^T exported as ycomps / pure_groups "
        "(use 「写入 Groups」 to add as derived standards)."
    )
    lines.append(
        "Note: rotational ambiguity — non-negativity + closure reduce unphysical solutions."
    )
    warns = data.get("warnings") or []
    if warns:
        lines.append("")
        lines.append("warnings:")
        for w in warns:
            lines.append(f"  • {w}")
    return "\n".join(lines)


def prepeak_fit(
    energy: np.ndarray,
    mu: np.ndarray,
    e0: float,
    *,
    emin: Optional[float] = None,
    emax: Optional[float] = None,
    elo: Optional[float] = None,
    ehi: Optional[float] = None,
    n_peaks: int = 2,
    peak_shape: str = "gaussian",
    baseline: str = "linear",
    couple_widths: bool = False,
) -> OpResult:
    """Pre-edge peaks via larch prepeaks_setup + prepeaks_fit (lmfit model).

    Parameters
    ----------
    n_peaks : 1–3 peak components auto-initialized from residual maxima.
    peak_shape : 'gaussian' | 'lorentzian' | 'pseudovoigt'
    baseline : 'linear' | 'linear+arctan' (step at main edge)
    couple_widths : if True, peaks 2..n share peak-1 width parameter.
    """
    try:
        from larch import Group
        from larch.xafs import pre_edge, prepeaks_fit as larch_prepeaks_fit
        from larch.xafs import prepeaks_setup
        from lmfit.models import (
            GaussianModel,
            LinearModel,
            LorentzianModel,
            PseudoVoigtModel,
            StepModel,
        )

        energy = np.asarray(energy, dtype=float).ravel()
        mu = np.asarray(mu, dtype=float).ravel()
        if energy.size < 20 or mu.size != energy.size:
            return OpResult(False, "pre-peak: spectrum too short")
        if not np.isfinite(energy).all() or not np.isfinite(mu).all():
            return OpResult(False, "pre-peak: spectrum contains non-finite values")
        if np.any(np.diff(energy) <= 0):
            return OpResult(False, "pre-peak: energy must be strictly increasing")
        if not np.isfinite(e0):
            return OpResult(False, "pre-peak: e0 is not set")
        e0 = float(e0)
        data_min, data_max = float(energy[0]), float(energy[-1])
        if not data_min < e0 < data_max:
            return OpResult(False, "pre-peak: e0 is outside the scan range")
        n_peaks = max(1, min(3, int(n_peaks)))
        shape = (peak_shape or "gaussian").lower().strip()
        bl_form = (baseline or "linear").lower().strip()

        def _peak_model(prefix: str):
            if shape in ("lorentzian", "lorentz"):
                return LorentzianModel(prefix=prefix)
            if shape in ("pseudovoigt", "pseudo-voigt", "pvoigt", "voigt"):
                return PseudoVoigtModel(prefix=prefix)
            return GaussianModel(prefix=prefix)

        supplied = {"emin": emin, "emax": emax, "elo": elo, "ehi": ehi}
        for key, val in supplied.items():
            if val is not None and np.isfinite(val) and not (data_min <= float(val) <= data_max):
                return OpResult(
                    False,
                    f"pre-peak: {key}={float(val):.6g} is outside scan {data_min:.6g}..{data_max:.6g}",
                )
        # Explicit absolute defaults avoid Larch's ambiguous "below data min =
        # relative to e0" conversion and provide a longer pre-edge baseline.
        emin_use = max(data_min, e0 - 50.0) if emin is None else float(emin)
        emax_use = min(data_max, e0 - 1.0) if emax is None else float(emax)
        elo_use = max(emin_use, e0 - 15.0) if elo is None else float(elo)
        ehi_use = min(emax_use, e0 - 5.0) if ehi is None else float(ehi)
        if not (emin_use < elo_use < ehi_use < emax_use):
            return OpResult(
                False,
                "pre-peak: windows must satisfy emin < elo < ehi < emax inside the scan",
            )

        g = Group(energy=energy.copy(), mu=mu.copy(), e0=e0, filename="prepeak", label="prepeak")
        pre_edge(g, e0=e0)
        setup_kw = {"emin": emin_use, "emax": emax_use, "elo": elo_use, "ehi": ehi_use}
        prepeaks_setup(g, **setup_kw)
        pp = g.prepeaks
        epk = np.asarray(pp.energy, dtype=float)
        npk = np.asarray(pp.norm, dtype=float)
        if epk.size < 10:
            return OpResult(False, "pre-peak: too few points in pre-edge region")

        base_mask = (epk < pp.elo) | (epk > pp.ehi)
        if base_mask.sum() >= 3:
            slope, intercept = (float(v) for v in np.polyfit(epk[base_mask], npk[base_mask], 1))
        else:
            slope, intercept = 0.0, float(np.median(npk))

        peak_mask = (epk >= pp.elo) & (epk <= pp.ehi)
        if peak_mask.sum() < 5:
            return OpResult(False, "pre-peak: no points in peak window [elo, ehi]")
        epk_in = epk[peak_mask]
        resid = npk[peak_mask] - (slope * epk_in + intercept)

        # Seed peak centers from residual maxima (greedy, ≥1.2 eV apart)
        from scipy.signal import find_peaks

        step_e = float(np.median(np.diff(epk_in)))
        min_distance = max(1, int(round(0.8 / max(step_e, 1e-12))))
        peak_floor = max(1e-12, 0.005 * float(np.nanmax(resid)))
        peak_idx, _props = find_peaks(resid, prominence=peak_floor, distance=min_distance)
        peak_idx = np.asarray([i for i in peak_idx if resid[i] > 0], dtype=int)
        if peak_idx.size < n_peaks:
            return OpResult(
                False,
                f"pre-peak: requested {n_peaks} peaks but found only {peak_idx.size} distinct maxima",
            )
        order_by_height = peak_idx[np.argsort(resid[peak_idx])[::-1][:n_peaks]]
        seeds = sorted(
            [(float(epk_in[i]), float(resid[i])) for i in order_by_height],
            key=lambda pair: pair[0],
        )

        model = LinearModel(prefix="bline_")
        params = model.make_params(bline_slope=slope, bline_intercept=intercept)
        if bl_form in ("linear+arctan", "arctan", "line+arctan", "step"):
            step = StepModel(prefix="step_", form="arctan")
            model = model + step
            params.update(
                step.make_params(
                    step_amplitude=max(0.2, float(np.nanmax(npk) - np.nanmin(npk)) * 0.5),
                    step_center=float(e0),
                    step_sigma=1.5,
                )
            )
            params["step_amplitude"].min = 0.0
            params["step_amplitude"].max = 3.0
            params["step_center"].min = float(epk.min())
            params["step_center"].max = float(epk.max()) + 20.0
            params["step_sigma"].min = 0.3
            params["step_sigma"].max = 8.0

        for i, (cen, height) in enumerate(seeds, start=1):
            pref = f"p{i}_"
            pm = _peak_model(pref)
            model = model + pm
            sigma0 = 0.8
            if shape in ("lorentzian", "lorentz"):
                amp0 = max(1e-6, height * np.pi * sigma0)
            else:
                amp0 = max(1e-6, height * 2.5066 * sigma0)
            pnew = pm.make_params(
                **{
                    f"{pref}center": cen,
                    f"{pref}sigma": 0.8,
                    f"{pref}amplitude": amp0,
                }
            )
            # PseudoVoigt needs fraction
            if f"{pref}fraction" in pnew:
                pnew[f"{pref}fraction"].set(value=0.5, min=0.0, max=1.0)
            params.update(pnew)
            params[f"{pref}sigma"].min = 0.15
            params[f"{pref}sigma"].max = 4.0
            left = float(epk.min()) if i == 1 else 0.5 * (seeds[i - 2][0] + cen)
            right = float(epk.max()) if i == len(seeds) else 0.5 * (cen + seeds[i][0])
            params[f"{pref}center"].min = left
            params[f"{pref}center"].max = right
            params[f"{pref}amplitude"].min = 0.0
            if couple_widths and i > 1:
                params[f"{pref}sigma"].expr = "p1_sigma"

        pkfit = larch_prepeaks_fit(g, model, params)
        rparams = pkfit.result.params
        peaks = []
        for i in range(1, n_peaks + 1):
            pref = f"p{i}_"
            cen_key = pref + "center"
            if cen_key not in rparams:
                continue
            cen = float(rparams[cen_key].value)
            sig = float(rparams[pref + "sigma"].value)
            amp = float(rparams[pref + "amplitude"].value)
            fwhm_key, height_key = pref + "fwhm", pref + "height"
            peak_row = {
                "center": cen,
                "sigma": sig,
                "fwhm": float(rparams[fwhm_key].value) if fwhm_key in rparams else float("nan"),
                "area": amp,
                "height": float(rparams[height_key].value) if height_key in rparams else float("nan"),
                "shape": shape,
            }
            fraction_key = pref + "fraction"
            if fraction_key in rparams:
                peak_row["fraction"] = float(rparams[fraction_key].value)
            peaks.append(peak_row)
        ycomps = {}
        try:
            ycomps = {
                str(k): np.asarray(v, dtype=float).copy()
                for k, v in (getattr(pkfit, "ycomps", {}) or {}).items()
            }
        except Exception:
            ycomps = {}
        bl_info = {
            "form": bl_form,
            "slope": float(rparams["bline_slope"].value) if "bline_slope" in rparams else slope,
            "intercept": float(rparams["bline_intercept"].value)
            if "bline_intercept" in rparams
            else intercept,
        }
        if "step_amplitude" in rparams:
            bl_info["step_amplitude"] = float(rparams["step_amplitude"].value)
            bl_info["step_center"] = float(rparams["step_center"].value)
            bl_info["step_sigma"] = float(rparams["step_sigma"].value)
        payload = {
            "engine": "larch.xafs.prepeaks_setup+prepeaks_fit",
            "e0": e0,
            "peaks": peaks,
            "n_peaks": len(peaks),
            "peak_shape": shape,
            "baseline": bl_info,
            "couple_widths": bool(couple_widths),
            "emin": float(pp.emin),
            "emax": float(pp.emax),
            "elo": float(pp.elo),
            "ehi": float(pp.ehi),
            "xdata": np.asarray(getattr(pkfit, "xdata", epk), dtype=float).copy(),
            "ydata": np.asarray(getattr(pkfit, "ydata", npk), dtype=float).copy(),
            "yfit": np.asarray(getattr(pkfit, "best_fit", []), dtype=float).copy(),
            "ycomps": ycomps,
            "redchi": float(getattr(pkfit.result, "redchi", float("nan"))),
            "chisqr": float(getattr(pkfit.result, "chisqr", float("nan"))),
        }
        msg_peaks = ", ".join(
            f"E={p['center']:.2f} area={p['area']:.4g}" for p in peaks
        )
        msg = f"pre-peak (larch) {len(peaks)}×{shape}: {msg_peaks}"
        return OpResult(True, msg, payload)
    except Exception as exc:
        return OpResult(False, f"pre-peak (larch) failed: {exc}")


def format_lcf_report(data: dict) -> str:
    """Human-readable LCF report from lincombo_fit / lincombo_fitall payload."""
    engine = data.get("engine", "larch.math.lincombo_fit")
    unit = "Å⁻¹" if data.get("space") == "k" else "eV"
    lines = [
        f"=== LCF  ({engine}) ===",
        f"array: {data.get('arrayname', 'norm')}   space: {data.get('space', 'E')}",
        f"fit range: {data.get('xmin')} … {data.get('xmax')} {unit}",
        f"R-factor: {data.get('rfactor', float('nan')):.6g}",
        f"χ²: {data.get('chisqr', float('nan')):.6g}   redχ²: {data.get('redchi', float('nan')):.6g}",
        f"e0_shift: {data.get('e0_shift', 0.0):.4g} eV",
        "",
        "weights:",
    ]
    for k, v in (data.get("weights") or {}).items():
        lines.append(f"  {k:20s}  {float(v):8.4f}  ({100 * float(v):6.2f} %)")
    tot = sum(float(v) for v in (data.get("weights") or {}).values())
    lines.append(f"  {'TOTAL':20s}  {tot:8.4f}")

    rankings = data.get("rankings") or []
    if rankings:
        lines.append("")
        lines.append(f"combinatorial ranking (top {len(rankings)} by redχ²):")
        for row in rankings:
            w = row.get("weights") or {}
            wtxt = ", ".join(f"{k}={float(v):.3f}" for k, v in w.items())
            lines.append(
                f"  #{int(row.get('rank', 0)):02d}  redχ²={float(row.get('redchi', float('nan'))):.5g}  "
                f"R={float(row.get('rfactor', float('nan'))):.5g}  [{wtxt}]"
            )
    warns = data.get("warnings") or []
    if warns:
        lines.append("")
        lines.append("warnings:")
        for w in warns:
            lines.append(f"  • {w}")
    return "\n".join(lines)


def format_pca_report(data: dict) -> str:
    """Human-readable PCA report including IND / cum. variance / TT-SPOIL."""
    unit = "Å⁻¹" if data.get("space") == "k" else "eV"
    lines = [
        "=== PCA  (larch.math.pca_train + pca_fit) ===",
        f"array: {data.get('arrayname', 'norm')}   space: {data.get('space', 'E')}",
        f"ncomp={data.get('ncomp')}   nsig={data.get('nsig')}   "
        f"suggested_ncomp={data.get('suggested_ncomp', data.get('nsig'))}",
        f"range: {data.get('xmin')} … {data.get('xmax')} {unit}",
        f"target (plotted): {data.get('target')}",
        "",
        "component  eigenvalue     variance   cum%      IND",
    ]
    eig = data.get("eigenvalues") or []
    var = data.get("variances") or []
    cum = data.get("cum_variance") or []
    ind = data.get("ind") or []
    nshow = max(len(eig), len(var), int(data.get("ncomp") or 0), len(ind))
    for i in range(nshow):
        e = eig[i] if i < len(eig) else float("nan")
        v = var[i] if i < len(var) else float("nan")
        c = cum[i] if i < len(cum) else float("nan")
        # Larch ind[0] is the zero-factor statistic; PC n maps to ind[n].
        d = ind[i + 1] if i + 1 < len(ind) else float("nan")
        marker = "  ← nsig" if i + 1 == int(data.get("nsig") or -1) else ""
        lines.append(
            f"  PC{i + 1:<6d}  {float(e):12.5g}  {100 * float(v):8.3f} %  "
            f"{100 * float(c):7.2f}  {float(d):10.5g}{marker}"
        )
    lines.append("")
    lines.append("fit weights / χ² per spectrum:")
    for name, row in (data.get("fits") or {}).items():
        if "error" in row:
            lines.append(f"  {name}: ERROR {row['error']}")
            continue
        w = row.get("weights") or []
        wtxt = ", ".join(f"{float(x):+.4f}" for x in w)
        lines.append(f"  {name}: χ²={float(row.get('chi_square', float('nan'))):.5g}  w=[{wtxt}]")

    tt = data.get("tt") or []
    if tt:
        lines.append("")
        lines.append("Target Transform / SPOIL  (<3 excellent, 3–6 ok, >6 poor):")
        for row in tt:
            lines.append(
                f"  {row.get('name', '?'):20s}  SPOIL={float(row.get('spoil', float('nan'))):6.3f}  "
                f"[{row.get('grade', '?')}]  R={float(row.get('rfactor', float('nan'))):.5g}  "
                f"AET={float(row.get('aet', float('nan'))):.5g}  "
                f"REP={float(row.get('rep', row.get('edl', float('nan')))):.5g}  "
                f"RET={float(row.get('ret', float('nan'))):.5g}"
            )
    warns = data.get("warnings") or []
    if warns:
        lines.append("")
        lines.append("warnings:")
        for w in warns:
            lines.append(f"  • {w}")
    return "\n".join(lines)


def format_prepeak_report(data: dict) -> str:
    """Human-readable pre-peak report."""
    lines = [
        "=== Pre-edge peaks  (larch prepeaks_setup + prepeaks_fit) ===",
        f"E0: {data.get('e0')} eV",
        f"shape: {data.get('peak_shape', 'gaussian')}   n_peaks: {data.get('n_peaks', len(data.get('peaks') or []))}",
        f"baseline: {((data.get('baseline') or {}).get('form', 'linear'))}",
        f"couple_widths: {data.get('couple_widths', False)}",
        f"baseline window: {data.get('emin')} … {data.get('emax')} eV",
        f"peak window:     {data.get('elo')} … {data.get('ehi')} eV",
        f"redχ²: {data.get('redchi', float('nan')):.5g}",
        "",
        "peaks:",
    ]
    for i, p in enumerate(data.get("peaks") or [], start=1):
        lines.append(
            f"  #{i}  center={p['center']:.3f} eV  σ={p['sigma']:.3f}  "
            f"FWHM={p.get('fwhm', float('nan')):.3f}  "
            f"area={p['area']:.5g}  height={p['height']:.5g}"
        )
    bl = data.get("baseline") or {}
    lines.append(
        f"baseline: slope={bl.get('slope', float('nan')):.5g}  "
        f"intercept={bl.get('intercept', float('nan')):.5g}"
    )
    if "step_amplitude" in bl:
        lines.append(
            f"  step: amp={bl['step_amplitude']:.5g}  "
            f"center={bl.get('step_center', float('nan')):.3f}  "
            f"σ={bl.get('step_sigma', float('nan')):.3f}"
        )
    return "\n".join(lines)


def merge_spectra(
    specs: Sequence[Tuple[str, np.ndarray, np.ndarray]],
    master_index: int = 0,
    weights: Optional[Sequence[float]] = None,
) -> OpResult:
    """Merge spectra onto the master energy grid.

    *specs*: (name, energy, y) — y may be raw μ or normalized.
    *weights*: Athena importance. None or all-equal → unweighted mean
    (same as larch ``merge_groups``). Otherwise a weighted average;
    weight ≤ 0 drops that spectrum from the mean.
    """
    try:
        from larch import Group
        from larch.io import merge_groups
        from larch.math import interp

        if not specs:
            return OpResult(False, "no spectra to merge")
        master_index = max(0, min(int(master_index), len(specs) - 1))
        groups = []
        for name, e, y in specs:
            e = np.asarray(e, dtype=float)
            y = np.asarray(y, dtype=float)
            n = min(e.size, y.size)
            if n < 5:
                return OpResult(False, f"group too short to merge: {name}")
            groups.append(Group(energy=e[:n], mu=y[:n], name=name))

        w = None
        equal = True
        if weights is not None:
            w = np.asarray(list(weights), dtype=float).ravel()
            if w.size != len(groups):
                return OpResult(False, "merge weights length must match the group list")
            w = np.where(np.isfinite(w), np.maximum(w, 0.0), 0.0)
            if float(np.sum(w)) <= 0:
                return OpResult(False, "merge weights sum to 0")
            equal = bool(np.allclose(w, w[0]))

        if w is None or equal:
            merged = merge_groups(
                groups,
                master=groups[master_index],
                xarray="energy",
                yarray="mu",
                kind="cubic",
                trim=True,
            )
            e2 = np.asarray(getattr(merged, "energy"), dtype=float)
            m2 = np.asarray(getattr(merged, "mu"), dtype=float)
            if e2.size < 5 or m2.size != e2.size:
                return OpResult(False, "merge produced no usable arrays")
            return OpResult(True, f"merged {len(groups)} groups → {e2.size} pts", (e2, m2))

        master = groups[master_index]
        xout = np.asarray(master.energy, dtype=float)
        stack = []
        for g in groups:
            stack.append(interp(
                np.asarray(g.energy, dtype=float),
                np.asarray(g.mu, dtype=float),
                xout,
                kind="cubic",
            ))
        yave = np.average(np.vstack(stack), axis=0, weights=w)
        if xout.size < 5 or yave.size != xout.size:
            return OpResult(False, "merge produced no usable arrays")
        wtxt = ", ".join(f"{wi:g}" for wi in w)
        return OpResult(
            True,
            f"merged {len(groups)} groups (weights {wtxt}) → {xout.size} pts",
            (xout, np.asarray(yave, dtype=float)),
        )
    except Exception as exc:
        return OpResult(False, f"merge failed: {exc}")


# ---- Smart merge grouping (v2.19.0) -------------------------------------
# Repeat scans of one sample differ only by trailing scan numbers / repeat
# tags ("S1_TiK_98004", "S1_TiK_98007"; "SampleMn_00", "SampleMn_repeat_00").
_MERGE_EXT_TAIL = re.compile(r"\.(csv|xdi|dat|txt|xmu|prj|nor|chi)\b.*$", re.IGNORECASE)
_MERGE_REPEAT_TOKEN = re.compile(r"[_\-\s]+(\d+|rep(eat)?\d*|r\d+|scan\d*)$", re.IGNORECASE)
MERGE_CHANNELS = ("trans", "fluo", "ref")


def merge_sample_key(name: str) -> Tuple[str, str]:
    """(sample stem, channel) used to group repeat scans for merging.

    Channel = a trailing ``_trans`` / ``_fluo`` / ``_ref`` (import suffixes),
    else "".  Stem = name without file-extension tail (", boxcar size 11"
    included), channel suffix, and trailing scan-number / repeat tokens.
    A name that would strip to nothing keeps its original text.
    """
    s = _MERGE_EXT_TAIL.sub("", str(name or "").strip()).strip()
    channel = ""
    low = s.lower()
    for ch in MERGE_CHANNELS:
        if low.endswith("_" + ch):
            channel = ch
            s = s[: -(len(ch) + 1)]
            break
    stem = s
    while True:
        nxt = _MERGE_REPEAT_TOKEN.sub("", stem)
        if nxt == stem or not nxt.strip("_- "):
            break
        stem = nxt
    return (stem or s or str(name)), channel


def _overlap_fraction(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    shorter = min(a[1] - a[0], b[1] - b[0])
    if shorter <= 0:
        return 0.0
    return max(0.0, hi - lo) / shorter


def suggest_merge_groups(items: Sequence[dict]) -> List[dict]:
    """Auto-group curves for merging.

    *items*: dicts with ``id``, ``name``, ``element``, ``edge`` and
    ``erange`` (emin, emax) — energy WITH the group's ΔE.
    Key = (sample stem, channel, element, edge); members whose energy range
    overlaps the group's first member by < 50 % of the shorter range start
    a separate group (a XANES-only rescan is not averaged into an EXAFS
    scan).  Returns ``[{"label", "ids", "element", "edge", "channel"}]`` in
    first-appearance order; singletons are included (they are not merged).
    Labels are unique.
    """
    buckets: List[dict] = []
    for it in items:
        stem, channel = merge_sample_key(it.get("name", ""))
        el = str(it.get("element") or "").strip()
        ed = str(it.get("edge") or "").strip()
        er = tuple(float(x) for x in (it.get("erange") or (0.0, 0.0)))
        key = (stem, channel, el.lower(), ed.lower())
        home = None
        for b in buckets:
            if b["key"] == key and _overlap_fraction(b["erange"], er) >= 0.5:
                home = b
                break
        if home is None:
            home = {"key": key, "stem": stem, "channel": channel, "element": el,
                    "edge": ed, "erange": er, "ids": []}
            buckets.append(home)
        home["ids"].append(it["id"])
    out: List[dict] = []
    used: Dict[str, int] = {}
    for b in buckets:
        label = b["stem"] + (f"_{b['channel']}" if b["channel"] else "")
        n = used.get(label, 0) + 1
        used[label] = n
        if n > 1:
            label = f"{label}#{n}"
        out.append({"label": label, "ids": list(b["ids"]), "element": b["element"],
                    "edge": b["edge"], "channel": b["channel"]})
    return out


def calibrate_e0(energy: np.ndarray, mu: np.ndarray, known_e0: float) -> OpResult:
    """Shift spectrum so E0 matches the known edge energy (FIRST inflection).

    v3.19.5: the rule itself lives in pipeline_rules.foil_first_inflection (the
    ONLY rules source, plan T1/T2).  larch find_e0 is kept only as an explicit
    fallback for data where no first inflection can be found: on benchtop Fe/Ni
    foils it locks onto the taller second derivative peak and lands 8-10 eV
    high (benchtop: 7118.8 / 8342.8 instead of 7110.6 / 8330.7 eV).
    """
    from . import pipeline_rules  # lazy: pipeline_rules imports this module

    res = pipeline_rules.foil_first_inflection(energy, mu, float(known_e0))
    if res.get("ok") and res.get("value") is not None:
        e_est = float(res["value"])
        shift = float(known_e0) - e_est
        return OpResult(
            True,
            f"first_inflection={e_est:.3f}, shift={shift:.3f}",
            shift,
        )
    reason = str(res.get("reason_en") or "")
    try:
        from larch.xafs import find_e0

        e_est = float(find_e0(energy, mu))
        shift = float(known_e0) - e_est
        return OpResult(
            True,
            f"find_e0 fallback={e_est:.3f}, shift={shift:.3f} ({reason})",
            shift,
        )
    except Exception as exc:
        # derivative max
        dy = np.gradient(mu, energy)
        e_est = float(energy[int(np.nanargmax(dy))])
        shift = float(known_e0) - e_est
        return OpResult(True, f"calibrate fallback e0={e_est:.3f} ({exc})", shift)


_EDGE_TABLE: Optional[List[Tuple[str, str, float]]] = None
# Edges considered for element guessing and their cost multipliers.
# K is preferred (as larch.guess_edge does), L1/M5 are a last resort — a
# Fe K edge shifted +14 eV by oxidation used to read as "Nd L1" (v2.18.0).
_EDGE_WEIGHT = {"K": 0.5, "L3": 1.0, "L2": 1.4, "L1": 2.5, "M5": 2.5}
_EDGE_EXTRA = {"L1": 8.0, "M5": 8.0}
# Accepted e0 − tabulated window (eV): measured inflection points sit a few
# eV above the table value, oxidised species up to ~+25 eV, and an
# uncalibrated mono can read ~15 eV low.
_EDGE_DELTA_LO, _EDGE_DELTA_HI = -20.0, 40.0


def _edge_table() -> List[Tuple[str, str, float]]:
    """(symbol, edge, tabulated energy eV) for Z = 3…95, cached."""
    global _EDGE_TABLE
    if _EDGE_TABLE is None:
        from xraydb import atomic_symbol, xray_edges

        rows: List[Tuple[str, str, float]] = []
        for z in range(3, 96):
            sym = atomic_symbol(z)
            edges = xray_edges(sym)
            for name in _EDGE_WEIGHT:
                if name in edges:
                    rows.append((sym, name, float(edges[name].energy)))
        _EDGE_TABLE = rows
    return _EDGE_TABLE


def edge_table_energy(symbol: str, edge: str) -> Optional[float]:
    """Tabulated edge energy (eV) or None."""
    for sym, name, en in _edge_table():
        if sym == symbol and name == edge:
            return en
    return None


def edge_guess_cost(e0: float, symbol: str, edge: str) -> Optional[float]:
    """Cost of explaining a measured e0 by (symbol, edge); None = outside
    the plausible shift window.  Lower is better."""
    en = edge_table_energy(symbol, edge)
    if en is None:
        return None
    delta = float(e0) - en
    if not (_EDGE_DELTA_LO <= delta <= _EDGE_DELTA_HI):
        return None
    # inflection usually lies ~3 eV above the table value; below it costs 2×
    base = (delta - 3.0) if delta >= 3.0 else 2.0 * (3.0 - delta)
    cost = base * _EDGE_WEIGHT.get(edge, 3.0) + _EDGE_EXTRA.get(edge, 0.0)
    from xraydb import atomic_number

    z = atomic_number(symbol)
    if z < 10 or z > 92:
        cost *= 2.0
    return cost


def guess_edge_robust(e0: float) -> Tuple[Optional[str], Optional[str]]:
    """Element/edge for a measured e0, tolerant of chemical shift.

    larch ``guess_edge`` takes the nearest tabulated edge (with a K bonus),
    so a shifted K edge that happens to sit on an L1/M5 value is misread.
    Here every K/L3/L2/L1/M5 edge inside the shift window is scored and the
    cheapest wins; nothing in the window → larch ``guess_edge`` fallback.
    """
    best: Tuple[float, Optional[str], Optional[str]] = (float("inf"), None, None)
    for sym, name, _en in _edge_table():
        c = edge_guess_cost(e0, sym, name)
        if c is not None and c < best[0]:
            best = (c, sym, name)
    # A poor best (only a far-stretched L1/M5 explains e0, cost > 30) means
    # e0 itself is probably off the edge — larch's nearest-edge guess with
    # its K bonus is then the safer call (YL_63-32 Mn K, e0 6579.6: the
    # window version said "Ce L1", v2.18.1).
    if best[1] is not None and best[0] <= 30.0:
        return best[1], best[2]
    from larch.xray import guess_edge

    return guess_edge(e0)


def _edge_step_score(e: np.ndarray, mu: np.ndarray, c: float, noise: float) -> float:
    """Signed edge-step significance at candidate energy *c*: median of μ
    just above *c* minus the pre-edge line (fitted just below *c* and
    extrapolated), in units of point noise.  The line matters: weak edges
    often ride a steep sloping background (MEX1 Co K, 2 % step on μ≈2)
    where a plain above-minus-below median is dominated by the slope."""
    # Pre line ends 15 eV below c (it must not swallow the rise of a true
    # edge); short post window, so the rise has to happen AT c.  A candidate
    # well below the real edge only sees part of the rise; one above it
    # (noise spike on the post-edge) sees a steep pre "line" and goes
    # negative.
    pre = (e >= c - 60.0) & (e <= c - 15.0)
    post = (e >= c + 3.0) & (e <= c + 20.0)
    if pre.sum() < 3:
        pre = (e >= c - 90.0) & (e <= c - 15.0)
    if post.sum() < 3:
        post = (e >= c + 3.0) & (e <= c + 35.0)
    if pre.sum() < 3 or post.sum() < 3:
        return float("-inf")
    slope, icpt = np.polyfit(e[pre], mu[pre], 1)
    step = float(np.median(mu[post] - (slope * e[post] + icpt)))
    return step / noise


def robust_e0(energy: np.ndarray, mu: np.ndarray, e0_larch: float) -> Tuple[float, str]:
    """Second opinion on larch ``find_e0`` for weak / noisy edges (v2.18.0).

    find_e0 takes the largest (lightly smoothed) derivative; on a low edge
    step one noise spike or glitch beats the real edge (SeK_NN5 rebinned:
    +38 eV off, negative step).  Candidates = local maxima of a boxcar-
    smoothed dμ/dE; each is scored by the edge step it explains in units of
    point noise.  larch's value is kept unless a candidate ≥ 10 eV away
    explains more than twice its step (and ≥ 3σ) —
    good data keeps the
    exact find_e0 value, only a demonstrably wrong pick is replaced.
    Returns (e0, note) — note is empty when larch's value is kept.
    """
    e = np.asarray(energy, dtype=float)
    y = np.asarray(mu, dtype=float)
    ok = np.isfinite(e) & np.isfinite(y)
    e, y = e[ok], y[ok]
    order = np.argsort(e)
    e, y = e[order], y[order]
    keep = np.concatenate(([True], np.diff(e) > 0))
    e, y = e[keep], y[keep]
    if e.size < 20 or not np.isfinite(e0_larch):
        return float(e0_larch), ""
    # point noise from the 2nd difference (edge-insensitive), MAD-robust
    d2 = y[2:] - 2.0 * y[1:-1] + y[:-2]
    noise = 1.4826 * float(np.median(np.abs(d2 - np.median(d2)))) / np.sqrt(6.0)
    if not np.isfinite(noise) or noise <= 0:
        return float(e0_larch), ""
    # boxcar over ~3 eV (≥ 3 points) before differentiating.  The width is
    # in eV, not points: MEX1 slew scans sample every ~0.1 eV, and a
    # 15-point cap left 1.5 eV of smoothing — noise spikes then outranked
    # the Ti K rise (v2.18.1).
    step_e = float(np.median(np.diff(e)))
    nwin = int(max(3, min(401, round(3.0 / max(step_e, 1e-6)))))
    ys = np.convolve(y, np.ones(nwin) / nwin, mode="same")
    edge_pad = nwin // 2 + 1
    d = np.gradient(ys, e)
    d[:edge_pad] = -np.inf
    d[-edge_pad:] = -np.inf
    peaks = [
        i for i in range(1, d.size - 1)
        if np.isfinite(d[i]) and d[i] > 0 and d[i] >= d[i - 1] and d[i] >= d[i + 1]
    ]
    if not peaks:
        return float(e0_larch), ""
    peaks = sorted(peaks, key=lambda i: d[i], reverse=True)[:12]
    s_larch = _edge_step_score(e, y, float(e0_larch), noise)
    if not np.isfinite(s_larch):
        # too little pre-edge around larch's pick to judge it (short XANES
        # window) — never overrule what we cannot evaluate (§B.14c)
        return float(e0_larch), ""
    best_i, best_s = None, float("-inf")
    for i in peaks:
        s = _edge_step_score(e, y, float(e[i]), noise)
        if s > best_s:
            best_i, best_s = i, s
    if best_i is None:
        return float(e0_larch), ""
    # The step score leans a few eV high (its post window sees more of the
    # rise); the edge itself is the steepest point of that region.
    near = np.where(np.abs(e - e[best_i]) <= 8.0)[0]
    near = near[np.isfinite(d[near])]
    if near.size:
        best_i = int(near[np.argmax(d[near])])
    e_best = float(e[best_i])
    if abs(e_best - float(e0_larch)) < 10.0:
        return float(e0_larch), ""
    # Relative test (σ is tiny on boxcar-smoothed Athena groups, so an
    # absolute threshold means nothing there): overrule only when the best
    # candidate explains > 2× the step larch's pick does.  A foil's pre-edge
    # shoulder (Ni 8333 eV) sees most but not all of the rise and stays put.
    if best_s >= 3.0 and best_s > 2.0 * max(s_larch, 0.0):
        return e_best, (
            f"find_e0 {e0_larch:.1f} explains step {s_larch:.1f}σ; "
            f"using {e_best:.1f} ({best_s:.1f}σ)"
        )
    return float(e0_larch), ""


def detect_edge(
    energy: np.ndarray,
    mu: np.ndarray,
) -> OpResult:
    """
    Athena-like auto edge location from μ(E):
      1) find_e0 on the selected spectrum, cross-checked by ``robust_e0``
         (edge-step significance) so a noise spike cannot win on a weak edge
      2) element + edge from that energy via ``guess_edge_robust``
         (chemical-shift tolerant, K/L3 preferred over L1/M5)
    Returns data dict: {e0, element, edge, tabulated_e0}.
    """
    energy = np.asarray(energy, dtype=float)
    mu = np.asarray(mu, dtype=float)
    if energy.size < 10 or mu.size < 10:
        return OpResult(False, "spectrum too short for edge detection")
    try:
        from larch.xafs import find_e0
        from larch.xray import xray_edge

        e0 = float(find_e0(energy, mu))
        e0, note = robust_e0(energy, mu, e0)
        sym, edge = guess_edge_robust(e0)
        tab = None
        try:
            xe = xray_edge(sym, edge)
            tab = float(getattr(xe, "energy", xe[0] if hasattr(xe, "__getitem__") else float("nan")))
        except Exception:
            tab = None
        return OpResult(
            True,
            f"auto edge: {sym} {edge}  E0≈{e0:.2f} eV"
            + (f" (table {tab:.1f})" if tab else "")
            + (f"  [{note}]" if note else ""),
            {
                "e0": e0,
                "element": str(sym),
                "edge": str(edge),
                "tabulated_e0": tab,
                "emin_rel": float(energy.min() - e0),
                "emax_rel": float(energy.max() - e0),
            },
        )
    except Exception as exc:
        # fallback: max derivative
        try:
            dy = np.gradient(mu, energy)
            e0 = float(energy[int(np.nanargmax(dy))])
            sym, edge = guess_edge_robust(e0)
            return OpResult(
                True,
                f"auto edge fallback: {sym} {edge} E0≈{e0:.2f} ({exc})",
                {
                    "e0": e0,
                    "element": str(sym),
                    "edge": str(edge),
                    "tabulated_e0": None,
                    "emin_rel": float(energy.min() - e0),
                    "emax_rel": float(energy.max() - e0),
                },
            )
        except Exception as e2:
            return OpResult(False, f"detect_edge failed: {exc}; {e2}")


def _safe_filename(name: str, max_len: int = 80) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "spectrum").strip())
    s = re.sub(r"\s+", "_", s).strip("._") or "spectrum"
    return s[:max_len]


def _norm_display_array(group: GroupRecord, a) -> Optional[np.ndarray]:
    """Normalized E array *as shown on screen* (mirrors ``_draw_E`` in
    main_window): the flattened norm when the group's Flatten setting is on
    and a flat array exists, else the plain norm.  Export must match the
    plot — a customer exported "norm" while the screen showed the flattened
    curve and got a tail baseline off 1 (v0.35.17).
    """
    if a is None:
        return None
    if bool(getattr(getattr(group, "settings", None), "make_flat", False)):
        flat = getattr(a, "flat", None)
        if flat is not None:
            return flat
    return getattr(a, "norm", None)


def _xy_for_kind(group: GroupRecord, kind: str) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Return (x, y) for an export kind, or None if unavailable."""
    if kind not in EXPORT_KINDS:
        return None
    x_attr, y_spec, _, _ = EXPORT_KINDS[kind]
    a = group.session.arrays if group.session and group.session.arrays else None

    def _raw_e_mu():
        if group.energy is None or group.mu is None:
            return None, None
        e = np.asarray(group.energy, dtype=float) + float(group.energy_shift or 0.0)
        m = np.asarray(group.mu, dtype=float)
        return e, m

    x = y = None
    if a is not None:
        x = getattr(a, x_attr, None)

    # Computed k-weighted χ
    if y_spec in ("k1chi", "k2chi", "k3chi") and a is not None:
        k = getattr(a, "k", None)
        chi = getattr(a, "chi", None)
        if k is not None and chi is not None:
            k = np.asarray(k, dtype=float).ravel()
            chi = np.asarray(chi, dtype=float).ravel()
            n = min(k.size, chi.size)
            if n >= 2:
                p = {"k1chi": 1, "k2chi": 2, "k3chi": 3}[y_spec]
                return k[:n], (k[:n] ** p) * chi[:n]
        return None

    # Phase from Re/Im if chir_pha / chiq_pha missing
    if y_spec == "chir_pha" and a is not None:
        y = getattr(a, "chir_pha", None)
        if y is None:
            re_a, im_a = getattr(a, "chir_re", None), getattr(a, "chir_im", None)
            if re_a is not None and im_a is not None:
                y = np.arctan2(np.asarray(im_a, dtype=float), np.asarray(re_a, dtype=float))
    elif y_spec == "chiq_pha" and a is not None:
        y = getattr(a, "chiq_pha", None)
        if y is None:
            re_a, im_a = getattr(a, "chiq_re", None), getattr(a, "chiq_im", None)
            if re_a is not None and im_a is not None:
                y = np.arctan2(np.asarray(im_a, dtype=float), np.asarray(re_a, dtype=float))
    elif a is not None:
        # dmude/d2mude deliberately excluded: the SpectrumArrays.dmude field
        # holds larch's d(norm)/dE — the real d(μ)/dE is computed below.
        if y_spec == "norm":
            y = _norm_display_array(group, a)
        elif y_spec not in ("dmude", "d2mude"):
            y = getattr(a, y_spec, None)

    # μ raw fallback
    if kind == "mu" and (x is None or y is None):
        x, y = _raw_e_mu()

    # Derivatives of μ(E) — always computed from μ, never from arrays.dmude
    if kind in ("dmude", "d2mude"):
        e, m = (getattr(a, "energy", None), getattr(a, "mu", None)) if a else (None, None)
        if e is None or m is None:
            e, m = _raw_e_mu()
        if e is not None and m is not None:
            e = np.asarray(e, dtype=float).ravel()
            m = np.asarray(m, dtype=float).ravel()
            n = min(e.size, m.size)
            if n >= 3:
                d1 = np.gradient(m[:n], e[:n])
                if kind == "dmude":
                    return e[:n], d1
                return e[:n], np.gradient(d1, e[:n])

    # Derivatives of normalized μ(E) — same base array as the norm view
    # (flattened when the group's Flatten setting is on, v0.35.17)
    if kind in ("dnormde", "d2normde"):
        e = getattr(a, "energy", None) if a is not None else None
        nrm = _norm_display_array(group, a)
        if e is not None and nrm is not None:
            e = np.asarray(e, dtype=float).ravel()
            nrm = np.asarray(nrm, dtype=float).ravel()
            n = min(e.size, nrm.size)
            if n >= 3:
                d1 = np.gradient(nrm[:n], e[:n])
                if kind == "dnormde":
                    return e[:n], d1
                return e[:n], np.gradient(d1, e[:n])
        return None

    if x is None or y is None:
        return None
    x = np.asarray(x, dtype=float).ravel()
    y = np.asarray(y, dtype=float).ravel()
    n = min(x.size, y.size)
    if n < 2:
        return None
    return x[:n], y[:n]


def _kinds_by_space(kinds: Sequence[str]) -> Dict[str, List[str]]:
    """Split selected kinds into file spaces E / k / R / q (Eμ+En → E)."""
    wanted = set(kinds)
    out: Dict[str, List[str]] = {}
    for ui_space, keys in EXPORT_KIND_GROUPS:
        sel = [k for k in keys if k in wanted]
        if not sel:
            continue
        file_tag = EXPORT_SPACE_FILE_TAG.get(ui_space, ui_space)
        out.setdefault(file_tag, []).extend(sel)
    return out


def _table_for_space(
    group: GroupRecord, kind_keys: Sequence[str]
) -> Optional[Tuple[List[str], List[np.ndarray]]]:
    """Build multi-column table for one spectrum × one space (shared x axis).

    Returns (headers, columns) or None if nothing available.
    """
    if not kind_keys:
        return None
    # All kinds in a space share the same x (energy / k / r / q)
    first = _xy_for_kind(group, kind_keys[0])
    if first is None:
        return None
    x0, y0 = first
    _, _, xl, yl0 = EXPORT_KINDS[kind_keys[0]]
    headers = [xl, yl0]
    cols: List[np.ndarray] = [x0, y0]
    n0 = len(x0)
    for kind in kind_keys[1:]:
        xy = _xy_for_kind(group, kind)
        if xy is None:
            continue
        x, y = xy
        _, _, _, yl = EXPORT_KINDS[kind]
        # align length to first column (same grid expected)
        n = min(n0, len(y), len(x))
        headers.append(yl)
        cols.append(np.asarray(y[:n], dtype=float))
        # trim earlier cols if needed
        if n < n0:
            cols = [c[:n] for c in cols]
            n0 = n
    if len(cols) < 2:
        return None
    return headers, cols


def _table_for_space_all_groups(
    groups: Sequence[GroupRecord], kind_keys: Sequence[str]
) -> Optional[Tuple[List[str], List[np.ndarray]]]:
    """Side-by-side multi-group table for one space (E/k/R/q).

    Each group contributes its own x + y columns (grids need not match);
    shorter columns are padded with NaN. Headers are ``{col}__{group}``.
    """
    headers: List[str] = []
    cols: List[np.ndarray] = []
    max_n = 0
    for g in groups:
        table = _table_for_space(g, kind_keys)
        if table is None:
            continue
        hdrs, gcols = table
        safe = _safe_filename(g.name, max_len=24)
        safe = re.sub(r"[\[\]\*:/\\?\']", "_", safe) or "spectrum"
        for h, c in zip(hdrs, gcols):
            headers.append(f"{h}__{safe}")
            arr = np.asarray(c, dtype=float).ravel()
            cols.append(arr)
            max_n = max(max_n, int(arr.size))
    if not cols or max_n < 2:
        return None
    padded: List[np.ndarray] = []
    for c in cols:
        if c.size < max_n:
            p = np.full(max_n, np.nan, dtype=float)
            p[: c.size] = c
            padded.append(p)
        else:
            padded.append(c[:max_n])
    return headers, padded


def _element_export_tag(group: GroupRecord) -> str:
    """Filename token for one group's element (empty → ``unassigned``)."""
    raw = str(getattr(group, "element", "") or "").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    if not raw:
        return "unassigned"
    return _safe_filename(raw, max_len=24)


def _partition_groups_by_element(
    groups: Sequence[GroupRecord],
) -> List[Tuple[str, List[GroupRecord]]]:
    """Bucket groups by element; first-seen order; case-insensitive merge."""
    buckets: Dict[str, List[GroupRecord]] = {}
    order: List[str] = []
    display: Dict[str, str] = {}
    for g in groups:
        tag = _element_export_tag(g)
        key = tag.lower()
        if key not in buckets:
            buckets[key] = []
            order.append(key)
            display[key] = tag
        buckets[key].append(g)
    return [(display[k], buckets[k]) for k in order]


def export_groups_csv(
    groups: Sequence[GroupRecord],
    out_dir: Path | str,
    kinds: Sequence[str],
    progress_cb=None,
    notes_rows: Optional[Sequence[dict]] = None,
    notes_lines: Optional[Sequence[str]] = None,
) -> OpResult:
    """CSV has no tabs → one file per group × space (E/k/R/q).

    *progress_cb(current, total, message) -> bool*: return False to cancel.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_space = _kinds_by_space(kinds)
    if not by_space:
        return OpResult(False, "no kinds selected")
    jobs: List[Tuple[GroupRecord, str, List[str]]] = []
    for g in groups:
        for space, keys in by_space.items():
            jobs.append((g, space, list(keys)))
    total = max(1, len(jobs))
    written: List[str] = []
    skipped = 0
    for ji, (g, space, keys) in enumerate(jobs):
        if progress_cb is not None:
            try:
                if not progress_cb(ji, total, f"{g.name} · {space} ({ji + 1}/{total})"):
                    return OpResult(False, "export cancelled", written)
            except Exception:
                pass
        table = _table_for_space(g, keys)
        if table is None:
            skipped += 1
            continue
        headers, cols = table
        base = _safe_filename(g.name)
        path = out_dir / f"{base}__{space}.csv"
        n = len(cols[0])
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            for i in range(n):
                row = []
                for c in cols:
                    v = c[i] if i < len(c) else float("nan")
                    row.append(f"{v:.8g}" if np.isfinite(v) else "")
                w.writerow(row)
        written.append(str(path))
    if progress_cb is not None:
        try:
            progress_cb(total, total, "CSV done")
        except Exception:
            pass
    if not written:
        return OpResult(False, f"nothing to export (skipped={skipped})")
    # v3.19.5: the processing record travels with the CSV export too
    rows = [r for r in (notes_rows or []) if isinstance(r, dict)]
    lines = [str(x) for x in (notes_lines or [])]
    if rows or lines:
        notes_path = out_dir / "processing_notes.csv"
        try:
            with open(notes_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                for line in lines:
                    writer.writerow([line])
                if rows:
                    writer.writerow([])
                    keys = list(rows[0].keys())
                    writer.writerow(keys)
                    for row in rows:
                        writer.writerow([row.get(k, "") if row.get(k) is not None else "" for k in keys])
            written.append(str(notes_path))
        except OSError as exc:
            return OpResult(False, f"CSV notes write failed: {exc}", written)
    return OpResult(
        True,
        f"CSV: {len(written)} file(s) by E/k/R/q, skipped empty={skipped}",
        written,
    )


def _write_xlsx_workbook(
    groups: Sequence[GroupRecord],
    out_path: Path,
    kinds: Sequence[str],
    progress_cb=None,
    notes_rows: Optional[Sequence[dict]] = None,
    notes_lines: Optional[Sequence[str]] = None,
) -> OpResult:
    """Write one workbook: one sheet per space (E / k / R / q), groups side-by-side.

    v3.19.5: when the project carries a processing record, a Notes sheet is the
    FIRST sheet of every workbook (parameter table + decision reasons), the
    same layout as the delivered Excel workbooks.
    """
    try:
        from openpyxl import Workbook
    except ImportError:
        return OpResult(False, "openpyxl not installed — use CSV format")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    by_space = _kinds_by_space(kinds)
    if not by_space:
        return OpResult(False, "no kinds selected")

    space_items = list(by_space.items())
    total = max(1, len(space_items) + 1)  # +1 for save
    wb = Workbook()
    default = wb.active
    n_sheets = 0
    notes_rows = [r for r in (notes_rows or []) if isinstance(r, dict)]
    notes_lines = [str(x) for x in (notes_lines or [])]
    if notes_rows or notes_lines:
        try:
            from openpyxl.styles import Font
        except ImportError:
            Font = None  # type: ignore[assignment]
        ws = default if default is not None else wb.create_sheet(title="Notes")
        ws.title = "Notes"
        row_i = 1
        for line in notes_lines:
            ws.cell(row_i, 1, line)
            row_i += 1
        if notes_rows:
            row_i += 1
            keys = list(notes_rows[0].keys())
            for c, key in enumerate(keys, start=1):
                cell = ws.cell(row_i, c, str(key))
                if Font is not None:
                    cell.font = Font(bold=True)
            row_i += 1
            for row in notes_rows:
                for c, key in enumerate(keys, start=1):
                    value = row.get(key)
                    if value is None or value == "":
                        continue
                    ws.cell(row_i, c, value)
                row_i += 1
        ws.column_dimensions["A"].width = 34
        n_sheets += 1
    skipped = 0

    for si, (space, keys) in enumerate(space_items):
        if progress_cb is not None:
            try:
                if not progress_cb(
                    si,
                    total,
                    f"sheet {space} ({si + 1}/{len(space_items)})",
                ):
                    return OpResult(False, "export cancelled")
            except Exception:
                pass
        table = _table_for_space_all_groups(groups, keys)
        if table is None:
            skipped += 1
            continue
        headers, cols = table
        title = str(space)[:31] or "data"
        if title in wb.sheetnames:
            title = f"{title}_data"[:31]
        ws = wb.create_sheet(title=title)
        for c, hdr in enumerate(headers, start=1):
            ws.cell(1, c, hdr)
        n = len(cols[0])
        chunk = 500
        for r0 in range(0, n, chunk):
            r1 = min(n, r0 + chunk)
            for r in range(r0, r1):
                for c, arr in enumerate(cols, start=1):
                    v = arr[r]
                    if np.isfinite(v):
                        ws.cell(r + 2, c, float(v))
            if progress_cb is not None and n > chunk:
                try:
                    progress_cb(si, total, f"sheet {space}: {r1}/{n} rows")
                except Exception:
                    pass
        n_sheets += 1

    if n_sheets == 0:
        return OpResult(False, f"nothing to export (skipped={skipped})")
    if default is not None and default.title in wb.sheetnames and n_sheets > 0:
        if default.title != "Notes" and default.max_row <= 1 and default.max_column <= 1:
            wb.remove(default)
    if progress_cb is not None:
        try:
            if not progress_cb(total - 1, total, f"writing {out_path.name}…"):
                return OpResult(False, "export cancelled")
        except Exception:
            pass
    wb.save(str(out_path))
    if progress_cb is not None:
        try:
            progress_cb(total, total, "Excel done")
        except Exception:
            pass
    return OpResult(
        True,
        f"Excel: {n_sheets} sheet(s) E/k/R/q ({len(groups)} group(s)) → {out_path.name}, "
        f"skipped empty spaces={skipped}",
        [str(out_path)],
    )


def export_groups_xlsx(
    groups: Sequence[GroupRecord],
    out_path: Path | str,
    kinds: Sequence[str],
    progress_cb=None,
    notes_rows: Optional[Sequence[dict]] = None,
    notes_lines: Optional[Sequence[str]] = None,
) -> OpResult:
    """Excel: **one workbook per element**; each file has E / k / R / q sheets.

    ``out_path`` is a template. ``export_YYYYMMDD_HHMMSS.xlsx`` becomes
    ``export_YYYYMMDD_HHMMSS_Fe.xlsx``, ``…_Ni.xlsx``, …. Groups with an
    empty element go to ``unassigned``. Inside each file the groups of that
    element sit side-by-side (``energy__G1 | mu__G1 | …``). Mixed-element
    projects stay readable instead of dumping every spectrum onto one sheet.

    *progress_cb(current, total, message) -> bool*: return False to cancel.
    """
    template = Path(out_path)
    by_space = _kinds_by_space(kinds)
    if not by_space:
        return OpResult(False, "no kinds selected")
    parts = _partition_groups_by_element(groups)
    if not parts:
        return OpResult(False, "no groups to export")

    n_parts = len(parts)
    written: List[str] = []
    messages: List[str] = []
    for pi, (elem, gsub) in enumerate(parts):
        path = template.parent / f"{template.stem}_{elem}{template.suffix or '.xlsx'}"

        def _cb(cur: int, total: int, label: str, _pi=pi, _elem=elem) -> bool:
            if progress_cb is None:
                return True
            try:
                return bool(
                    progress_cb(_pi, n_parts, f"{_elem}: {label}")
                )
            except Exception:
                return True

        if progress_cb is not None:
            try:
                if not progress_cb(pi, n_parts, f"{elem} ({pi + 1}/{n_parts})"):
                    return OpResult(False, "export cancelled", written)
            except Exception:
                pass
        res = _write_xlsx_workbook(
            gsub, path, kinds, progress_cb=_cb,
            notes_rows=notes_rows, notes_lines=notes_lines,
        )
        if not res.ok:
            if "cancel" in str(res.message).lower():
                return OpResult(False, "export cancelled", written)
            return res
        if isinstance(res.data, list):
            written.extend(str(p) for p in res.data)
        else:
            written.append(str(path))
        messages.append(str(res.message))

    if progress_cb is not None:
        try:
            progress_cb(n_parts, n_parts, "Excel done")
        except Exception:
            pass
    elem_list = ", ".join(tag for tag, _ in parts)
    return OpResult(
        True,
        f"Excel: {len(written)} workbook(s) by element ({elem_list})",
        written,
    )
