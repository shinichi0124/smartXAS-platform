"""Stepwise phase search for EXAFS models (v4.9.0) — Qt-free.

The user chooses the mode (single phase / mixed); this module never decides it.
For a mixed model it starts from the best single phase and tries to add one
phase at a time.  A phase is kept only when BOTH hold:

  * the parameter audit is not "fail", and
  * the Hamilton F-test says the extra parameters are significant (p < 0.05).

Rejected phases get a 2-sigma upper limit from a fraction scan.  Everything is
driven by callables so the whole search runs headless in tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .fit_audit import AuditFlag, audit_fit, verdict as audit_verdict

FitFn = Callable[[Any, Any], Any]  # (gds, paths) -> FitResult
BuildFn = Callable[[Sequence[Any]], tuple]  # phases -> (gds, paths, report)


@dataclass
class SearchResult:
    """What the search did, step by step (also what the history keeps)."""

    mode: str = "mixed"
    user_notes: str = ""
    cancelled: bool = False
    all_failed: bool = False
    error: str = ""  # the model could not even be built (e.g. "budget")
    steps: List[Dict[str, Any]] = field(default_factory=list)
    final_gds: List[Any] = field(default_factory=list)
    final_paths: List[Any] = field(default_factory=list)
    final_result: Any = None
    upper_limits: Dict[str, Optional[float]] = field(default_factory=dict)
    # why a limit is missing when it was not computed ("budget", "build", "fit");
    # a tag absent here with a None limit means the grid never reached 2 sigma
    limit_notes: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-safe summary (no curves, no numpy arrays)."""
        steps = []
        for step in self.steps:
            tried = []
            for row in step.get("tried") or []:
                tried.append(
                    {
                        k: row.get(k)
                        for k in (
                            "tag",
                            "formula",
                            "chi_square",
                            "r_factor",
                            "n_varys",
                            "p",
                            "F",
                            "verdict",
                            "audit_verdict",
                            "accepted",
                            "error",
                        )
                    }
                    | {"flags": list(row.get("flags") or [])}
                )
            steps.append({"step": step.get("step"), "tried": tried, "chosen": step.get("chosen")})
        return {
            "mode": self.mode,
            "user_notes": self.user_notes,
            "cancelled": bool(self.cancelled),
            "all_failed": bool(self.all_failed),
            "error": self.error,
            "steps": steps,
            "upper_limits": dict(self.upper_limits),
            "limit_notes": dict(self.limit_notes),
        }


def _finite(value, default=float("nan")) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out


def profile_parameter(
    fit_fn: FitFn,
    gds: Sequence[Any],
    name: str,
    grid: Iterable[float],
    stop: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    """Fix one parameter to each grid value and re-fit everything else.

    *stop* is asked before every grid fit; True ends the scan early.
    """
    from dataclasses import replace

    out: List[Dict[str, Any]] = []
    for value in grid:
        if stop is not None and stop():
            break
        rows = []
        for g in gds:
            if str(getattr(g, "name", "")).strip() == str(name):
                rows.append(replace(g, value=float(value), kind="fix", expr=""))
            else:
                rows.append(g)
        try:
            result = fit_fn(rows, None)
        except Exception as exc:  # a failed point is just "not ok"
            out.append(
                {
                    "value": float(value),
                    "chi_square": float("nan"),
                    "n_varys": 0,
                    "n_independent": float("nan"),
                    "ok": False,
                    "error": str(exc)[:200],
                }
            )
            continue
        st = dict(getattr(result, "statistics", {}) or {})
        ok = bool(getattr(result, "ok", False))
        out.append(
            {
                "value": float(value),
                "chi_square": _finite(st.get("chi_square")),
                "n_varys": int(_finite(st.get("n_varys"), 0)),
                "n_independent": _finite(st.get("n_independent")),
                "ok": ok,
            }
        )
    return out


def upper_limit(
    profile: Sequence[Dict[str, Any]],
    chi_ref: float,
    redchi_ref: float,
    level: float = 4.0,
) -> Optional[float]:
    """Smallest value above the profile minimum whose (chi^2 - chi_ref)/chi2_nu_ref
    reaches *level*.

    level = 4 is about 2 sigma (larch scales errors by sqrt(chi2_nu), so
    Delta chi^2 = chi2_nu is 1 sigma).  The scan starts at the grid point with
    the lowest chi^2: a phase can be rejected by the Hamilton test and still
    have its minimum inside the grid, and the points below that minimum are not
    an upper limit.  None = the grid never got there.
    """
    if not profile or not math.isfinite(chi_ref) or not math.isfinite(redchi_ref):
        return None
    if redchi_ref <= 0:
        return None
    rows = sorted(
        (
            r
            for r in profile
            if r.get("ok") and math.isfinite(_finite(r.get("chi_square")))
        ),
        key=lambda r: _finite(r.get("value"), 0.0),
    )
    if not rows:
        return None
    best = min(rows, key=lambda r: _finite(r.get("chi_square")))
    x_best = _finite(best.get("value"), 0.0)
    for row in rows:
        if _finite(row.get("value"), 0.0) < x_best:
            continue
        if (_finite(row.get("chi_square")) - chi_ref) / redchi_ref >= level:
            return float(row["value"])
    return None


def _flags(result) -> List[dict]:
    return [
        f.to_dict() if hasattr(f, "to_dict") else dict(f)
        for f in (getattr(result, "audit", None) or [])
    ]


def _audit(result, gds, mode: str):
    try:
        flags = audit_fit(result, gds, mode=mode)
    except Exception:
        flags = []
    text = audit_verdict(flags)
    return [f.to_dict() if hasattr(f, "to_dict") else dict(f) for f in flags], text


def _try_candidate(fit_fn, build_fn, phases, mode: str = "mixed") -> Dict[str, Any]:
    """Build + fit + audit one candidate model."""
    row: Dict[str, Any] = {"ok": False}
    try:
        gds, paths, report = build_fn(phases)
    except Exception as exc:
        row["error"] = str(exc)[:200]
        return row
    try:
        result = fit_fn(gds, paths)
    except Exception as exc:
        row["error"] = str(exc)[:200]
        return row
    flags, verdict = _audit(result, gds, mode)
    st = dict(getattr(result, "statistics", {}) or {})
    row.update(
        {
            "ok": bool(getattr(result, "ok", False)),
            "gds": gds,
            "paths": paths,
            "report": report,
            "result": result,
            "chi_square": _finite(st.get("chi_square")),
            "r_factor": _finite(st.get("r_factor")),
            "n_varys": int(_finite(st.get("n_varys"), 0)),
            "n_independent": _finite(st.get("n_independent")),
            "audit_flags": flags,
            "audit_verdict": verdict,
            "flags": [f.get("code", "") for f in flags],
        }
    )
    return row


def _candidate_tag(candidate, index: int) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("tag") or candidate.get("label") or f"p{index}")
    return str(getattr(candidate, "tag", "") or getattr(candidate, "label", "") or f"p{index}")


def _candidate_paths(candidate):
    if isinstance(candidate, dict):
        return list(candidate.get("paths") or [])
    return list(getattr(candidate, "paths", []) or [])


def _candidate_formula(candidate) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("formula") or "")
    return str(getattr(candidate, "formula", "") or "")


def stepwise_phases(
    fit_fn: FitFn,
    build_fn: BuildFn,
    candidates: Sequence[Any],
    max_phases: int = 2,
    pinned: Optional[Any] = None,
    want_limits: bool = True,
    progress: Optional[Callable[[str, int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> SearchResult:
    """Add one phase at a time; keep it only if Hamilton AND the audit agree."""
    from .fit_engine import hamilton_test

    result = SearchResult(mode="mixed")
    cand_list = list(candidates or [])
    if not cand_list:
        return result
    resolved = [
        {
            "tag": _candidate_tag(c, i),
            "paths": _candidate_paths(c),
            "formula": _candidate_formula(c),
            "raw": c,
        }
        for i, c in enumerate(cand_list, start=1)
    ]
    by_tag = {row["tag"]: row for row in resolved}
    max_phases = max(1, int(max_phases))

    def _check() -> bool:
        if cancelled is not None:
            try:
                if cancelled():
                    result.cancelled = True
                    return False
            except Exception:
                return True
        return True

    # ---- step 1: the best single phase, or the main phase the user pinned ----
    pinned_tag = ""
    if pinned is not None:
        pinned_tag = pinned if isinstance(pinned, str) else _candidate_tag(pinned, 0)
    step1_rows = [by_tag[pinned_tag]] if pinned_tag in by_tag else resolved
    step1 = {"step": 1, "tried": [], "chosen": ""}
    best = None
    for i, row in enumerate(step1_rows, start=1):
        if not _check():
            result.steps.append(step1)
            return result
        if progress is not None:
            try:
                progress(row["tag"], i, len(step1_rows))
            except Exception:
                pass
        trial = _try_candidate(fit_fn, build_fn, [(row["tag"], row["paths"])])
        trial.update({"tag": row["tag"], "formula": row["formula"], "p": None, "F": None, "verdict": ""})
        step1["tried"].append(trial)
        if trial.get("ok") and trial.get("audit_verdict") != "fail":
            if best is None or trial.get("chi_square", float("inf")) < best.get("chi_square", float("inf")):
                best = trial
    for trial in step1["tried"]:
        trial["accepted"] = trial is best
    if best is None:
        # every single phase failed the audit: report the smallest chi^2, stop
        ok_rows = [t for t in step1["tried"] if t.get("ok")]
        if not ok_rows:
            result.steps.append(step1)
            result.all_failed = True
            return result
        best = min(ok_rows, key=lambda t: t.get("chi_square", float("inf")))
        for trial in step1["tried"]:
            trial["accepted"] = trial is best
        result.all_failed = True
    step1["chosen"] = str(best["tag"])
    result.steps.append(step1)
    result.final_gds = list(best.get("gds") or [])
    result.final_paths = list(best.get("paths") or [])
    result.final_result = best.get("result")
    chosen: List[str] = [str(best["tag"])]
    previous = best
    if result.all_failed or max_phases <= 1:
        if want_limits and not result.all_failed:
            _limits(fit_fn, build_fn, chosen, by_tag, resolved, result, previous, _check)
        return result

    # ---- steps 2..max: try adding one phase ----
    for step_no in range(2, max_phases + 1):
        step = {"step": step_no, "tried": [], "chosen": ""}
        picked = None
        for row in resolved:
            if row["tag"] in chosen:
                continue
            if not _check():
                result.steps.append(step)
                return result
            phases = [(t, by_tag[t]["paths"]) for t in chosen] + [(row["tag"], row["paths"])]
            trial = _try_candidate(fit_fn, build_fn, phases)
            trial.update({"tag": row["tag"], "formula": row["formula"], "p": None, "F": None, "verdict": ""})
            if trial.get("ok") and previous.get("result") is not None:
                try:
                    h = hamilton_test(
                        dict(previous["result"].statistics or {}),
                        dict(trial["result"].statistics or {}),
                    )
                    trial["p"] = float(h["p"])
                    trial["F"] = float(h["F"])
                    trial["verdict"] = str(h["verdict"])
                except Exception as exc:
                    trial["error"] = str(exc)[:200]
            accepted = bool(
                trial.get("ok")
                and trial.get("audit_verdict") != "fail"
                and trial.get("p") is not None
                and float(trial["p"]) < 0.05
            )
            trial["accepted"] = accepted
            step["tried"].append(trial)
            if accepted and (
                picked is None
                or trial.get("chi_square", float("inf")) < picked.get("chi_square", float("inf"))
            ):
                picked = trial
        if picked is None:
            result.steps.append(step)
            break
        step["chosen"] = str(picked["tag"])
        result.steps.append(step)
        chosen.append(str(picked["tag"]))
        previous = picked
        result.final_gds = list(picked.get("gds") or [])
        result.final_paths = list(picked.get("paths") or [])
        result.final_result = picked.get("result")

    if want_limits:
        _limits(fit_fn, build_fn, chosen, by_tag, resolved, result, previous, _check)
    return result


def _limits(fit_fn, build_fn, chosen, by_tag, resolved, result, final_trial, check) -> None:
    """2-sigma upper limit for every phase that was not adopted.

    Writes result.upper_limits; a limit that could not be computed gets a
    reason in result.limit_notes ("budget" = the model with that phase has
    more variables than independent points), so the UI never reports such a
    phase as "> 0.50".
    """
    for row in resolved:
        tag = row["tag"]
        if tag in chosen:
            continue
        if not check():
            break
        phases = [(t, by_tag[t]["paths"]) for t in chosen] + [(tag, row["paths"])]
        try:
            gds, paths, _report = build_fn(phases)
        except Exception as exc:
            result.upper_limits[tag] = None
            result.limit_notes[tag] = "budget" if str(exc) == "budget" else "build"
            continue
        name = f"x_{tag}"
        if not any(str(getattr(g, "name", "")) == name for g in gds):
            result.upper_limits[tag] = None
            result.limit_notes[tag] = "build"
            continue
        grid = [round(0.05 * i, 2) for i in range(0, 11)]

        def _fit_at(g, _p):
            return _fit_with_paths(fit_fn, g, paths)

        profile = profile_parameter(_fit_at, gds, name, grid, stop=lambda: not check())
        if result.cancelled:
            break
        good = [
            p
            for p in profile
            if p.get("ok") and math.isfinite(_finite(p.get("chi_square")))
        ]
        if not good:
            result.upper_limits[tag] = None
            result.limit_notes[tag] = "fit"
            continue
        best = min(good, key=lambda p: _finite(p["chi_square"]))
        chi_ref = _finite(best["chi_square"])
        # chi2_nu of the best grid point (that model, that variable count);
        # the adopted model's chi2_nu only if the grid point lacks the counts
        dof = _finite(best.get("n_independent")) - _finite(best.get("n_varys"), 0.0)
        redchi = chi_ref / dof if math.isfinite(dof) and dof > 0 else float("nan")
        if not math.isfinite(redchi):
            try:
                st = dict(getattr(final_trial.get("result"), "statistics", {}) or {})
                redchi = _finite(st.get("redchi"))
            except Exception:
                redchi = float("nan")
        result.upper_limits[tag] = upper_limit(profile, chi_ref, redchi, level=4.0)


def _fit_with_paths(fit_fn, gds, paths):
    """fit_fn takes (gds, paths); the profile only varies the parameters."""
    return fit_fn(gds, paths)


def single_phase_search(
    fit_fn: FitFn,
    entry_paths,
    s02: float,
    s02_free: bool,
    try_ms: bool,
    windows=None,
) -> SearchResult:
    """Single-phase model, optionally testing multiple scattering."""
    from . import feff_build
    from .fit_audit import audit_fit, verdict as _verdict
    from .fit_engine import hamilton_test

    result = SearchResult(mode="single")
    if windows is None:
        raise ValueError("windows are required to build the model")
    try:
        gds, paths, report = feff_build.single_phase_model(
            entry_paths, s02, s02_free, windows, include_ms=False
        )
    except Exception as exc:
        result.all_failed = True
        result.error = str(exc)[:200]
        result.steps.append({"step": 1, "tried": [{"tag": "SS", "error": str(exc)}], "chosen": ""})
        return result
    rows = []
    lead = _try_candidate(fit_fn, lambda _p: (gds, paths, report), [("SS", [])], mode="single")
    lead.update({"tag": "SS", "formula": "", "p": None, "F": None, "verdict": ""})
    lead["accepted"] = True
    rows.append(lead)
    result.final_gds, result.final_paths, result.final_result = gds, paths, lead.get("result")
    result.steps.append({"step": 1, "tried": rows, "chosen": "SS"})
    if try_ms:
        try:
            gds_ms, paths_ms, report_ms = feff_build.single_phase_model(
                entry_paths, s02, s02_free, windows, include_ms=True
            )
        except Exception as exc:
            result.steps.append({"step": 2, "tried": [{"tag": "MS", "error": str(exc)}], "chosen": ""})
            return result
        if len(paths_ms) > len(paths):
            trial = _try_candidate(
                fit_fn, lambda _p: (gds_ms, paths_ms, report_ms), [("MS", [])], mode="single"
            )
            trial.update({"tag": "MS", "formula": "", "p": None, "F": None, "verdict": ""})
            if trial.get("ok") and lead.get("result") is not None:
                try:
                    h = hamilton_test(
                        dict(lead["result"].statistics or {}),
                        dict(trial["result"].statistics or {}),
                    )
                    trial["p"] = float(h["p"])
                    trial["F"] = float(h["F"])
                    trial["verdict"] = str(h["verdict"])
                except Exception as exc:
                    trial["error"] = str(exc)[:200]
            accepted = bool(
                trial.get("ok")
                and trial.get("audit_verdict") != "fail"
                and trial.get("p") is not None
                and float(trial["p"]) < 0.05
            )
            trial["accepted"] = accepted
            result.steps.append({"step": 2, "tried": [trial], "chosen": "MS" if accepted else ""})
            if accepted:
                result.final_gds, result.final_paths, result.final_result = (
                    gds_ms,
                    paths_ms,
                    trial.get("result"),
                )
    return result
