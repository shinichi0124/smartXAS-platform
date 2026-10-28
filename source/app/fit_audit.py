"""Parameter audit for EXAFS fits (v4.9.0) — Qt-free.

Everything here is a *diagnostic*, never a proof that a model is right.  The
thresholds are empirical judgement calls collected in one place so the UI, the
report and the AI evidence cannot disagree; they are not physical constants.

Sources: Calvin, XAFS for Everyone (2013); Stern, Phys. Rev. B 48, 9825 (1993);
the 2-sigma / chi-square-nu convention follows the usual EXAFS practice of
scaling errors by sqrt(chi2_reduced).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

# One place for every threshold.  warn / fail are the boundaries the UI colours.
DEFAULT_LIMITS: Dict[str, Any] = {
    # total R-factor
    "rfactor": {"warn": 0.05, "fail": 0.10},
    # variables / independent points
    "budget": {"warn": 2.0 / 3.0, "fail": 1.0},
    # a varying S0^2 (usually it should be fixed, from a standard)
    "s02": {"warn": (0.70, 1.00), "fail": (0.50, 1.20)},
    # every path's sigma^2 (A^2)
    "sigma2": {"warn": (0.002, 0.020), "fail": (0.0, 0.030)},
    # e0 shift (eV)
    "e0": {"warn": 10.0, "fail": 15.0},
    # every path's deltaR (A); mixed-phase models have larger sigma^2 / dR spread
    "deltar": {"warn": 0.05, "fail": 0.15, "fail_mixed": 0.20},
    # a coordination number above this usually trades against sigma^2
    "n_high": {"warn": 12.0},
    # a parameter sitting on its bound: its error bar is meaningless
    "at_bound": {"fraction": 0.01},
    # parameter correlation
    "corr": {"warn": 0.90, "fail": 0.95},
    # chi2_nu large while R is small: the noise estimate is probably wrong
    "noise_suspect": {"chi2nu": 50.0, "rfactor": 0.02},
}


@dataclass
class AuditFlag:
    """One audit finding."""

    code: str
    level: str  # "warn" | "fail"
    param: str = ""
    value: float = float("nan")
    limit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "AuditFlag":
        return AuditFlag(
            code=str(d.get("code", "")),
            level=str(d.get("level", "warn")),
            param=str(d.get("param", "")),
            value=float(d.get("value", float("nan"))),
            limit=str(d.get("limit", "")),
        )


def _num(value, default=float("nan")) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out


def _verdict_pair(flags: Iterable[AuditFlag]) -> tuple:
    warned = fails = 0
    for f in flags:
        if f.level == "fail":
            fails += 1
        else:
            warned += 1
    return warned, fails


def verdict(flags: Sequence[AuditFlag]) -> str:
    """pass | warn | fail — the worst level present."""
    _warned, fails = _verdict_pair(flags or [])
    if fails:
        return "fail"
    if flags:
        return "warn"
    return "pass"


def format_flags(flags: Sequence[AuditFlag]) -> str:
    """One English ASCII line per flag (log / report / tooltip)."""
    lines: List[str] = []
    for f in flags or []:
        tag = "FAIL" if f.level == "fail" else "warn"
        name = f" {f.param}" if f.param else ""
        try:
            value = f" = {float(f.value):.4g}"
        except (TypeError, ValueError):
            value = ""
        limit = f" (limit {f.limit})" if f.limit else ""
        lines.append(f"[audit] {tag} {f.code}{name}{value}{limit}")
    return "\n".join(lines)


def _limits(limits: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict(DEFAULT_LIMITS if limits is None else limits)


def audit_fit(
    result,
    gds: Sequence[Any] = (),
    mode: str = "generic",
    limits: Optional[Dict[str, Any]] = None,
) -> List[AuditFlag]:
    """Check one finished fit and return every finding (worst first)."""
    lim = _limits(limits)
    flags: List[AuditFlag] = []
    stats = dict(getattr(result, "statistics", {}) or {})
    params = dict(getattr(result, "params", {}) or {})
    paths = list(getattr(result, "paths", []) or [])
    bounds = {
        str(getattr(g, "name", "")).strip(): (
            getattr(g, "min", None),
            getattr(g, "max", None),
            str(getattr(g, "kind", "")),
        )
        for g in (gds or [])
    }

    # R-factor
    rfac = _num(stats.get("r_factor"))
    if math.isfinite(rfac):
        warn, fail = lim["rfactor"]["warn"], lim["rfactor"]["fail"]
        if rfac > fail:
            flags.append(AuditFlag("rfactor", "fail", "", rfac, f"< {fail:g}"))
        elif rfac > warn:
            flags.append(AuditFlag("rfactor", "warn", "", rfac, f"< {warn:g}"))

    # variable budget
    nvar = _num(stats.get("n_varys"), 0.0)
    nidp = _num(stats.get("n_independent"))
    if math.isfinite(nidp) and nidp > 0:
        ratio = nvar / nidp
        warn, fail = lim["budget"]["warn"], lim["budget"]["fail"]
        if ratio >= fail:
            flags.append(AuditFlag("budget", "fail", "", ratio, f"< {fail:.2f}"))
        elif ratio > warn:
            flags.append(AuditFlag("budget", "warn", "", ratio, f"<= {warn:.2f}"))

    # parameters
    for name, info in params.items():
        info = info if isinstance(info, dict) else {}
        value = _num(info.get("value"))
        stderr = _num(info.get("stderr"))
        kind = str(info.get("kind", ""))
        lname = str(name)
        if not math.isfinite(value):
            continue
        if lname.startswith("s02") and kind == "vary":  # a fixed S0² is the user's input
            warn_lo, warn_hi = lim["s02"]["warn"]
            fail_lo, fail_hi = lim["s02"]["fail"]
            if not (fail_lo <= value <= fail_hi):
                flags.append(
                    AuditFlag("s02", "fail", lname, value, f"{fail_lo:.2f}-{fail_hi:.2f}")
                )
            elif not (warn_lo <= value <= warn_hi):
                flags.append(
                    AuditFlag("s02", "warn", lname, value, f"{warn_lo:.2f}-{warn_hi:.2f}")
                )
        if lname.startswith("e0"):
            warn, fail = lim["e0"]["warn"], lim["e0"]["fail"]
            if abs(value) > fail:
                flags.append(AuditFlag("e0", "fail", lname, value, f"|e0| <= {fail:g} eV"))
            elif abs(value) > warn:
                flags.append(AuditFlag("e0", "warn", lname, value, f"|e0| <= {warn:g} eV"))
        if lname.startswith("n_") and kind == "vary":
            high = lim["n_high"]["warn"]
            if value > high:
                flags.append(AuditFlag("n_high", "warn", lname, value, f"<= {high:g}"))
        if lname.startswith("x_") and value < 0:
            flags.append(AuditFlag("fraction_negative", "fail", lname, value, ">= 0"))
        if kind == "vary":
            if not math.isfinite(stderr):
                flags.append(AuditFlag("stderr_missing", "warn", lname, value, "stderr present"))
            lo, hi, _k = bounds.get(lname, (None, None, ""))
            if lo is not None and hi is not None:
                lo_f, hi_f = float(lo), float(hi)
                span = hi_f - lo_f
                if span > 0:
                    frac = lim["at_bound"]["fraction"]
                    if (value - lo_f) / span < frac or (hi_f - value) / span < frac:
                        flags.append(
                            AuditFlag(
                                "at_bound",
                                "fail",
                                lname,
                                value,
                                f"{lo_f:.4g}-{hi_f:.4g}",
                            )
                        )

    # correlations
    for row in getattr(result, "correlations", []) or []:
        try:
            a, b, c = row[0], row[1], float(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        warn, fail = lim["corr"]["warn"], lim["corr"]["fail"]
        if abs(c) >= fail:
            flags.append(AuditFlag("corr", "fail", f"{a}/{b}", c, f"|r| < {fail:.2f}"))
        elif abs(c) >= warn:
            flags.append(AuditFlag("corr", "warn", f"{a}/{b}", c, f"|r| < {warn:.2f}"))

    # per-path sigma^2 / deltaR
    fail_dr = lim["deltar"].get("fail_mixed" if mode == "mixed" else "fail", lim["deltar"]["fail"])
    for path in paths:
        label = str((path or {}).get("label", "")) or "path"
        s2 = _num((path or {}).get("sigma2"))
        if math.isfinite(s2):
            lo_w, hi_w = lim["sigma2"]["warn"]
            lo_f, hi_f = lim["sigma2"]["fail"]
            if s2 <= lo_f or s2 > hi_f:
                flags.append(
                    AuditFlag("sigma2", "fail", label, s2, f"{lo_f:g}-{hi_f:g} A^2")
                )
            elif s2 < lo_w or s2 > hi_w:
                flags.append(
                    AuditFlag("sigma2", "warn", label, s2, f"{lo_w:g}-{hi_w:g} A^2")
                )
        dr = _num((path or {}).get("deltar"))
        if math.isfinite(dr):
            if abs(dr) > fail_dr:
                flags.append(
                    AuditFlag("deltar", "fail", label, dr, f"|dR| <= {fail_dr:g} A")
                )
            elif abs(dr) > lim["deltar"]["warn"]:
                flags.append(
                    AuditFlag(
                        "deltar", "warn", label, dr, f"|dR| <= {lim['deltar']['warn']:g} A"
                    )
                )

    # chi2_nu large while R small: the noise estimate is probably wrong
    redchi = _num(stats.get("redchi"))
    if math.isfinite(redchi) and math.isfinite(rfac):
        if (
            redchi > lim["noise_suspect"]["chi2nu"]
            and rfac < lim["noise_suspect"]["rfactor"]
        ):
            flags.append(
                AuditFlag(
                    "noise_suspect",
                    "warn",
                    "",
                    redchi,
                    f"chi2nu <= {lim['noise_suspect']['chi2nu']:g}",
                )
            )

    order = {"fail": 0, "warn": 1}
    return sorted(flags, key=lambda f: (order.get(f.level, 2), f.code, f.param))
