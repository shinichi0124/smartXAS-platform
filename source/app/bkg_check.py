"""Background check for EXAFS fits (v5.2.0) — Qt-free, read-only.

Three questions about the *current* group and model, answered on COPIES (the
group's settings, arrays, the path table and the GDS table are never touched,
invariant 19):

1. ``rbkg_scan``         does the answer change with AUTOBK's rbkg?  (fair
                         comparison: the fit window starts at max(rmin, rbkg))
2. ``refine_compare``    does it change when the background spline is refined
                         together with the structure (larch ``refine_bkg``)?
3. ``synthetic_recovery`` does the processing recover a KNOWN answer (the
                         current model, put back on the group's own edge)?

plus ``first_shell_leakage`` (how much of the first-shell peak lies below
1.0 / 1.2 Angstrom) and ``conclusion`` (numbers and comparisons only, no
chemistry).  Evidence: docs/VALIDATION.md, v5.2.0.
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

KTOE = 3.8099819442818976  # eV * A^2 (larch KTOE)
DEFAULT_RBKG_LIST: Tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2)
Progress = Optional[Callable[[str], None]]


def _say(progress: Progress, text: str) -> None:
    if progress is not None:
        try:
            progress(text)
        except Exception:
            pass


def _fe():
    from . import fit_engine as fe

    return fe


# ------------------------------------------------------------ processing copy
def process_copy(rec, rbkg: float, mu: Optional[np.ndarray] = None):
    """pre_edge -> AUTOBK -> FT on a COPY of a GroupRecord with another rbkg.

    Returns (larch group, SpectrumArrays).  ``mu`` replaces the raw mu (used by
    the synthetic test).  Raises RuntimeError with the first message line on
    failure.  ``rec`` itself is not modified (no ``rec.process`` call: that
    would replace its memoised session arrays).
    """
    from .athena_pipeline import AthenaSession

    if rec.energy is None or rec.mu is None:
        raise RuntimeError("group has no raw energy/mu")
    sess = AthenaSession()
    sess.settings = deepcopy(rec.settings)
    sess.settings.rbkg = float(rbkg)
    sess._raw_energy = np.asarray(rec.energy, dtype=float).copy() + float(rec.energy_shift)
    src = rec.mu if mu is None else mu
    sess._raw_mu = np.asarray(src, dtype=float).copy()
    sess.group_name = str(rec.name)
    sess.raw_path = str(rec.path)
    res = sess.reprocess()
    if not res.ok or sess.group is None:
        first = (res.message or "processing failed").splitlines()[0]
        raise RuntimeError(first)
    return sess.group, sess.arrays


# ------------------------------------------------------------------- fitting
def _nodes(rbkg: float, rmin: float, kmin: float, kmax: float) -> int:
    """Number of spline knots larch ``refine_bkg`` adds (larch rounds)."""
    return int(1 + round(2.0 * max(float(rbkg), float(rmin)) * (float(kmax) - float(kmin)) / math.pi))


def _first_shell_index(res) -> int:
    best, idx = float("inf"), 0
    for i, p in enumerate(getattr(res, "paths", []) or []):
        r = (p or {}).get("reff")
        try:
            r = float(r)
        except (TypeError, ValueError):
            continue
        if math.isfinite(r) and r < best:
            best, idx = r, i
    return idx


def summarize_fit(res) -> Dict[str, Any]:
    """One table row out of a FitResult (no arrays)."""
    from .fit_audit import audit_fit, verdict

    fe = _fe()
    row: Dict[str, Any] = {"ok": bool(res.ok), "reason": "" if res.ok else str(res.message)}
    if not res.ok:
        return row
    params = {}
    for name, info in (res.params or {}).items():
        if str(info.get("kind")) == "vary":
            params[name] = {"value": float(info.get("value", float("nan"))),
                            "stderr": float(info.get("stderr", float("nan")))}
    shells = []
    for s in fe.shell_rows(res):
        shells.append({k: s[k] for k in ("label", "reff", "cn", "cn_err", "cn_fixed", "n_s02", "r", "r_err",
                                         "sigma2", "sigma2_err")})
    flags = audit_fit(res, res.gds_used)
    stats = res.statistics or {}
    row.update(
        params=params,
        shells=shells,
        r_factor=float(stats.get("r_factor", float("nan"))),
        n_varys=int(stats.get("n_varys", 0) or 0),
        n_idp=float(stats.get("n_independent", float("nan"))),
        verdict=verdict(flags),
        flags=[f.to_dict() for f in flags],
    )
    row["first_shell"] = shells[_first_shell_index(res)] if shells else {}
    row["first_shell"] = dict(row["first_shell"])
    row["suspect_minimum"] = any(f.code in ("at_bound", "stderr_missing") for f in flags)
    return row


def _fit_once(group, fit_settings, paths, gds, *, rmin: float, refine_bkg: bool = False,
              rbkg: Optional[float] = None):
    fe = _fe()
    kw: Dict[str, Any] = {"rmin": float(rmin), "refine_bkg": bool(refine_bkg)}
    if rbkg is not None:
        kw["rbkg"] = float(rbkg)
    fs = replace(fit_settings, **kw)
    return fe.run_feffit(group, list(gds), list(paths), fs)


def _own_rbkg(rec) -> float:
    return float(getattr(rec.settings, "rbkg", 1.0) or 1.0)


# -------------------------------------------------------------------- 1. scan
def rbkg_scan(rec, fit_settings, paths, gds, rbkg_list: Sequence[float] = DEFAULT_RBKG_LIST,
              progress: Progress = None) -> List[Dict[str, Any]]:
    """Re-process the group with each rbkg and refit with the same model.

    The fit window starts at max(fit_settings.rmin, rbkg): a window that starts
    below rbkg fits data AUTOBK has just flattened and makes a larger rbkg look
    worse than it is (docs/VALIDATION.md v5.2.0)."""
    rows: List[Dict[str, Any]] = []
    n = len(rbkg_list)
    for i, rbkg in enumerate(rbkg_list):
        _say(progress, f"rbkg {rbkg:g} ({i + 1}/{n})")
        rmin = max(float(fit_settings.rmin), float(rbkg))
        row: Dict[str, Any] = {"rbkg": float(rbkg), "rmin": rmin}
        try:
            group, _arrays = process_copy(rec, rbkg)
            res = _fit_once(group, fit_settings, paths, gds, rmin=rmin)
            row.update(summarize_fit(res))
        except Exception as exc:  # one row failing never stops the others
            row.update(ok=False, reason=str(exc).splitlines()[0] if str(exc) else repr(exc))
        rows.append(row)
    return rows


# ------------------------------------------------------------ 2. refine bkg
def _ft_peak(k: np.ndarray, chi: np.ndarray, fs, kweight: int = 2,
             rlo: float = 0.0, rhi: float = 6.0) -> Tuple[float, float]:
    """(peak height, peak position) of |chi(R)| inside [rlo, rhi]."""
    from larch import Group

    L = _fe()._require_larch()
    g = Group()
    g.k = np.asarray(k, dtype=float)
    g.chi = np.asarray(chi, dtype=float)
    L["xftf"](g, kmin=fs.kmin, kmax=fs.kmax, dk=fs.dk, window=fs.kwindow, kweight=kweight)
    r = np.asarray(g.r, dtype=float)
    mag = np.asarray(g.chir_mag, dtype=float)
    sel = (r >= rlo) & (r <= rhi)
    if not sel.any():
        return float("nan"), float("nan")
    j = int(np.argmax(mag[sel]))
    return float(mag[sel][j]), float(r[sel][j])


def refine_compare(rec, fit_settings, paths, gds, flex_list: Optional[Sequence[float]] = None,
                   progress: Progress = None) -> List[Dict[str, Any]]:
    """Plain fit vs fits with the background spline refined together.

    ``flex_list`` are spline flexibilities (an rbkg-like length, A); larch turns
    each into 1 + round(2*flex*dk/pi) knots, so neighbours with the same knot
    count give identical results and are only run once."""
    base = _own_rbkg(rec)
    flex = list(flex_list) if flex_list else [max(base - 0.1, 0.5), base, base + 0.1]
    group, _arrays = process_copy(rec, base)
    rows: List[Dict[str, Any]] = []
    _say(progress, "plain fit")
    plain: Dict[str, Any] = {"mode": "plain", "rbkg": base, "nodes": 0}
    try:
        res = _fit_once(group, fit_settings, paths, gds, rmin=float(fit_settings.rmin))
        plain.update(summarize_fit(res))
    except Exception as exc:
        plain.update(ok=False, reason=str(exc).splitlines()[0] if str(exc) else repr(exc))
    rows.append(plain)

    seen: Dict[int, int] = {}
    for i, fl in enumerate(flex):
        nodes = _nodes(fl, fit_settings.rmin, fit_settings.kmin, fit_settings.kmax)
        _say(progress, f"refine flex {fl:g} ({i + 1}/{len(flex)})")
        row: Dict[str, Any] = {"mode": "refine", "rbkg": float(fl), "nodes": nodes}
        if nodes in seen:
            row.update(ok=True, same_as=seen[nodes], reason="same knot count as the previous row")
            rows.append(row)
            continue
        seen[nodes] = len(rows)
        try:
            # a fresh copy per fit: larch writes bkg outputs on the dataset group
            grp, _a = process_copy(rec, base)
            res = _fit_once(grp, fit_settings, paths, gds, rmin=float(fit_settings.rmin),
                            refine_bkg=True, rbkg=fl)
            row.update(summarize_fit(res))
            ds0 = (res.datasets or [{}])[0]
            bkg_k = ds0.get("bkg_k")
            if res.ok and bkg_k is not None and res.k is not None:
                sp_h, sp_r = _ft_peak(res.k, bkg_k, fit_settings, rlo=0.0, rhi=float(fit_settings.rmax))
                sh_h, _sh_r = _ft_peak(res.k, res.chi, fit_settings, rlo=float(fit_settings.rmin),
                                       rhi=float(fit_settings.rmax))
                row["spline_peak_r"] = sp_r
                row["spline_peak_height"] = sp_h
                row["shell_peak_height"] = sh_h
                row["spline_ratio"] = sp_h / sh_h if sh_h and math.isfinite(sh_h) else float("nan")
        except Exception as exc:
            row.update(ok=False, reason=str(exc).splitlines()[0] if str(exc) else repr(exc))
        rows.append(row)
    return rows


# ---------------------------------------------------------- 3. synthetic test
def synthetic_mu(rec, base_result, group=None, arrays=None) -> np.ndarray:
    """mu(E) with a KNOWN chi(k): post-edge line + edge_step * chi_true(k(E)).

    chi_true is the current fit's model chi (low k tapered with a sin^2 ramp
    from k = 1 to 2.5).  Below E0 the original mu stays.  A smooth post-edge
    line can be represented exactly by any cubic spline, so this only tests how
    much of the signal the background removes."""
    if group is None or arrays is None:
        group, arrays = process_copy(rec, _own_rbkg(rec))
    kf = np.asarray(base_result.fit_k, dtype=float)
    cf = np.asarray(base_result.fit_chi, dtype=float)
    ramp = np.clip((kf - 1.0) / 1.5, 0.0, 1.0)
    cf = cf * np.sin(0.5 * np.pi * ramp) ** 2
    e = np.asarray(arrays.energy, dtype=float)
    e0 = float(getattr(group, "e0", arrays.e0))
    step = float(getattr(group, "edge_step", arrays.edge_step))
    post = np.asarray(arrays.post_edge, dtype=float)
    mu = np.asarray(arrays.mu, dtype=float).copy()
    above = e >= e0
    k = np.sqrt(np.clip(e[above] - e0, 0.0, None) / KTOE)
    chi_e = np.interp(k, kf, cf, left=0.0, right=0.0)
    mu[above] = post[above] + step * chi_e
    return mu


def synthetic_recovery(rec, fit_settings, paths, gds, base_result,
                       rbkg_list: Sequence[float] = (1.0, 1.2),
                       progress: Progress = None) -> List[Dict[str, Any]]:
    """Process a spectrum whose answer is known; report recovered / true."""
    fe = _fe()
    rows: List[Dict[str, Any]] = []
    try:
        group0, arrays0 = process_copy(rec, _own_rbkg(rec))
        mu_syn = synthetic_mu(rec, base_result, group0, arrays0)
    except Exception as exc:
        return [{"rbkg": float("nan"), "ok": False, "reason": str(exc).splitlines()[0]}]
    true_params = {k: float(v.get("value", float("nan"))) for k, v in (base_result.params or {}).items()
                   if str(v.get("kind")) == "vary"}
    true_shells = fe.shell_rows(base_result)
    n = len(rbkg_list)
    for i, rbkg in enumerate(rbkg_list):
        _say(progress, f"synthetic rbkg {rbkg:g} ({i + 1}/{n})")
        rmin = max(float(fit_settings.rmin), float(rbkg))
        row: Dict[str, Any] = {"rbkg": float(rbkg), "rmin": rmin}
        try:
            group, _a = process_copy(rec, rbkg, mu=mu_syn)
            res = _fit_once(group, fit_settings, paths, gds, rmin=rmin)
            row.update(summarize_fit(res))
            if res.ok:
                rec_rows = {}
                for name, info in row["params"].items():
                    t = true_params.get(name, float("nan"))
                    v = info["value"]
                    rec_rows[name] = {"value": v, "true": t,
                                      "ratio": v / t if t and math.isfinite(t) and t != 0 else float("nan")}
                row["recovery"] = rec_rows
                ratios = []
                for s, t in zip(row["shells"], true_shells):
                    c, c0 = _n_of(s)[0], _n_of(t)[0]
                    ratios.append(c / c0 if c0 and math.isfinite(c0) and c0 != 0 and math.isfinite(c)
                                  else float("nan"))
                row["cn_ratio"] = ratios
                idx = _first_shell_index(base_result)
                row["first_shell_ratio"] = ratios[idx] if idx < len(ratios) else float("nan")
        except Exception as exc:
            row.update(ok=False, reason=str(exc).splitlines()[0] if str(exc) else repr(exc))
        rows.append(row)
    return rows


# --------------------------------------------------------- first-shell leakage
def first_shell_leakage(base_result, fit_settings, rmax_total: float = 8.0) -> Dict[str, Any]:
    """Share of the first path's |chi(R)|^2 below 1.0 and 1.2 A (k^2 FT, fit window)."""
    fe = _fe()
    L = fe._require_larch()
    paths = [p for p in (getattr(base_result, "paths", []) or []) if p]
    if not paths:
        return {"ok": False, "reason": "no paths"}
    p = paths[_first_shell_index(base_result)]
    try:
        feff = p["feff_file"]
        fp = L["feffpath"](
            str(feff),
            s02=repr(float(p["s02"])),
            e0=repr(float(p.get("e0") or 0.0)),
            deltar=repr(float(p.get("deltar") or 0.0)),
            sigma2=repr(float(p["sigma2"])),
            degen=float(p.get("degen") or 1.0),
        )
        k = np.arange(0.0, float(fit_settings.kmax) + 3.0, 0.05)
        pg = L["param_group"](dummy=L["param"](1.0, vary=False))
        from larch.xafs import ff2chi

        out = ff2chi([fp], params=pg, k=k)
        r, mag = _ft_curve(k, np.asarray(out.chi, dtype=float), fit_settings)
        sel = r <= rmax_total
        tot = float(np.sum(mag[sel] ** 2))
        if not tot > 0:
            return {"ok": False, "reason": "empty transform"}
        return {
            "ok": True,
            "label": p.get("label", ""),
            "below_1_0": float(np.sum(mag[r <= 1.0] ** 2) / tot),
            "below_1_2": float(np.sum(mag[r <= 1.2] ** 2) / tot),
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc).splitlines()[0] if str(exc) else repr(exc)}


def _ft_curve(k, chi, fs, kweight: int = 2):
    from larch import Group

    L = _fe()._require_larch()
    g = Group()
    g.k = np.asarray(k, dtype=float)
    g.chi = np.asarray(chi, dtype=float)
    L["xftf"](g, kmin=fs.kmin, kmax=fs.kmax, dk=fs.dk, window=fs.kwindow, kweight=kweight)
    return np.asarray(g.r, dtype=float), np.asarray(g.chir_mag, dtype=float)


# ------------------------------------------------------------------ conclusion
def _n_of(shell: Dict[str, Any]) -> Tuple[float, float]:
    """(N, error) of a shell row; N*S0^2 when the amplitude has no s02* parameter."""
    cn = float(shell.get("cn", float("nan")))
    if math.isfinite(cn):
        return cn, float(shell.get("cn_err", float("nan")))
    return float(shell.get("n_s02", float("nan"))), float("nan")


def _first_cn(row) -> Tuple[float, float]:
    return _n_of(row.get("first_shell") or {})


def conclusion(scan_rows: Sequence[Dict[str, Any]], own_rbkg: float,
               synth_rows: Sequence[Dict[str, Any]] = (), leakage: Optional[Dict[str, Any]] = None
               ) -> Dict[str, List[str]]:
    """Plain statements about the numbers (no chemistry).  {'zh': [...], 'en': [...]}"""
    zh: List[str] = []
    en: List[str] = []
    good = [r for r in scan_rows if r.get("ok")]
    ref = None
    if good:
        ref = min(good, key=lambda r: abs(float(r["rbkg"]) - float(own_rbkg)))
    if ref is not None:
        c0, e0 = _first_cn(ref)
        for r in good:
            if r is ref:
                continue
            c, e = _first_cn(r)
            if not (math.isfinite(c0) and math.isfinite(c)) or c0 == 0:
                continue
            frac = (c - c0) / c0
            err = max(x for x in (e0, e, 0.0) if math.isfinite(x)) if (math.isfinite(e0) or math.isfinite(e)) else 0.0
            if abs(c - c0) > max(err, 0.10 * abs(c0)):
                zh.append(f"rbkg 从 {ref['rbkg']:g} 到 {r['rbkg']:g}：第一壳 N 变化 {frac * 100:+.0f}%，"
                          f"超过误差（{c0:.2f} → {c:.2f}）")
                en.append(f"rbkg {ref['rbkg']:g} -> {r['rbkg']:g}: first-shell N changes "
                          f"{frac * 100:+.0f}% beyond the error ({c0:.2f} -> {c:.2f})")
        if not zh:
            zh.append("rbkg 在扫描范围内变化时第一壳 N 的变化不超过 10% 或误差。")
            en.append("First-shell N changes by less than 10% or its error over the scanned rbkg range.")
    for r in synth_rows:
        if r.get("ok") and math.isfinite(float(r.get("first_shell_ratio", float("nan")))):
            ratio = float(r["first_shell_ratio"])
            zh.append(f"合成谱 rbkg {r['rbkg']:g}：第一壳 N 回收 / 真值 = {ratio:.2f}")
            en.append(f"Synthetic spectrum, rbkg {r['rbkg']:g}: first-shell N recovered / true = {ratio:.2f}")
    if leakage and leakage.get("ok"):
        zh.append(f"第一壳单路径 |χ(R)|² 在 1.0 / 1.2 Å 以下的比例：{leakage['below_1_0']:.2f} / {leakage['below_1_2']:.2f}")
        en.append(f"Share of the first-shell path |chi(R)|^2 below 1.0 / 1.2 A: "
                  f"{leakage['below_1_0']:.2f} / {leakage['below_1_2']:.2f}")
    return {"zh": zh, "en": en}


# ----------------------------------------------------------------------- text
def _cn_text(row) -> str:
    c, e = _first_cn(row)
    if not math.isfinite(c):
        return "-"
    return f"{c:.2f}" + (f" +/- {e:.2f}" if math.isfinite(e) and e > 0 else "")


def rows_to_text(title: str, rows: Sequence[Dict[str, Any]]) -> str:
    """Compact ASCII table of rows from one of the three checks."""
    lines = [f"=== {title} ==="]
    for r in rows:
        head = f"rbkg {r.get('rbkg', float('nan')):g}"
        if "nodes" in r and r.get("mode") == "refine":
            head += f" (nodes {r['nodes']})"
        if r.get("mode") == "plain":
            head = "plain fit"
        if not r.get("ok"):
            lines.append(f"  {head}: FAILED - {r.get('reason', '')}")
            continue
        if r.get("same_as") is not None:
            lines.append(f"  {head}: same knot count as row {r['same_as']}")
            continue
        s = r.get("first_shell") or {}
        extra = ""
        if "first_shell_ratio" in r:
            extra += f"  recovered/true N = {r['first_shell_ratio']:.2f}"
        if "spline_ratio" in r:
            extra += f"  spline/shell peak = {r['spline_ratio']:.2f} @ {r['spline_peak_r']:.2f} A"
        if r.get("suspect_minimum") and r.get("mode") == "refine":
            extra += "  [possible false minimum]"
        lines.append(
            f"  {head}: rmin {r.get('rmin', float('nan')):.2f}  N1 {_cn_text(r)}  "
            f"R1 {float(s.get('r', float('nan'))):.4f}  s2 {float(s.get('sigma2', float('nan'))):.5f}  "
            f"R-factor {r.get('r_factor', float('nan')):.5f}  vars {r.get('n_varys', 0)}/"
            f"{r.get('n_idp', float('nan')):.1f}  audit {r.get('verdict', '')}{extra}"
        )
    return "\n".join(lines)


def results_to_csv(sections: Dict[str, Sequence[Dict[str, Any]]]) -> str:
    """One CSV: section, rbkg, rmin, nodes, ok, N1, N1_err, R1, sigma2_1, r_factor, vars, idp, audit, note."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "rbkg", "rmin", "nodes", "ok", "N1", "N1_err", "R1", "sigma2_1",
                "r_factor", "n_vars", "n_idp", "audit", "note"])
    for name, rows in sections.items():
        for r in rows:
            s = r.get("first_shell") or {}
            c, e = _first_cn(r)
            note = r.get("reason", "")
            if "first_shell_ratio" in r:
                note = (note + f" recovered/true={r['first_shell_ratio']:.3f}").strip()
            if "spline_ratio" in r:
                note = (note + f" spline/shell={r['spline_ratio']:.3f}@{r['spline_peak_r']:.2f}").strip()
            w.writerow([name, r.get("rbkg", ""), r.get("rmin", ""), r.get("nodes", ""), int(bool(r.get("ok"))),
                        c, e, s.get("r", ""), s.get("sigma2", ""), r.get("r_factor", ""),
                        r.get("n_varys", ""), r.get("n_idp", ""), r.get("verdict", ""), note])
    return buf.getvalue()
