"""
Athena-style XAFS preprocessing pipeline (xraylarch).

Unified API so the UI is one place for:
  μ(E) → pre-edge / normalize → autobk χ(k) → FT |χ(R)|
instead of Larch's scattered widgets / scripts.
"""

from __future__ import annotations

import copy
import sys
import threading
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class AthenaSettings:
    """Controls matching the usual Athena / larch pre_edge + autobk knobs."""

    # --- pre_edge / normalization ---
    e0: Optional[float] = None  # None = auto
    pre1: float = -150.0  # eV relative to E0
    pre2: float = -30.0
    norm1: float = 50.0
    norm2: float = 400.0  # or large enough for edge
    nvict: int = 0  # Victoreen exponent for pre-edge fit (pre-edge ~ E^-nvict); the pre-edge poly degree is npre in larch
    nnorm: int = 2  # post-edge poly order for norm
    make_flat: bool = True

    # --- background / chi ---
    rbkg: float = 1.0
    bkg_kweight: int = 2  # spline k-weight (UI default 2)
    bkg_kmin: float = 0.0
    bkg_kmax: float = 15.0
    bkg_dk: float = 0.0
    bkg_clamp_lo: int = 0
    bkg_clamp_hi: int = 1  # larch default (was 24 = "Strong", a misleading UI default)

    # --- FT preview (also used as default for fit) ---
    kmin: float = 2.0
    kmax: float = 10.0
    dk: float = 1.0
    kwindow: str = "hanning"
    kweight_plot: int = 2
    rmin: float = 1.0
    rmax: float = 4.0
    dr: float = 0.0  # R-window taper for back-transform (q-space)
    rwindow: str = "hanning"
    # Athena-style path phase correction of χ(R), not larch xftf's with_phase
    # (that flag only writes chir_pha). Needs a feffNNNN.dat in phase_feff.
    with_phase: bool = False
    phase_feff: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SpectrumArrays:
    """Numpy views for plotting / export (copies, safe for UI)."""

    energy: Optional[np.ndarray] = None
    mu: Optional[np.ndarray] = None
    pre_edge: Optional[np.ndarray] = None
    post_edge: Optional[np.ndarray] = None
    norm: Optional[np.ndarray] = None
    flat: Optional[np.ndarray] = None
    bkg: Optional[np.ndarray] = None
    dmude: Optional[np.ndarray] = None  # dμ/dE
    d2mude: Optional[np.ndarray] = None  # d²μ/dE²
    k: Optional[np.ndarray] = None
    chi: Optional[np.ndarray] = None
    r: Optional[np.ndarray] = None
    chir_mag: Optional[np.ndarray] = None
    chir_re: Optional[np.ndarray] = None
    chir_im: Optional[np.ndarray] = None
    chir_pha: Optional[np.ndarray] = None
    phase_corrected: bool = False
    phase_feff: str = ""
    q: Optional[np.ndarray] = None
    chiq_mag: Optional[np.ndarray] = None
    chiq_re: Optional[np.ndarray] = None
    chiq_im: Optional[np.ndarray] = None
    chiq_pha: Optional[np.ndarray] = None
    e0: float = float("nan")
    edge_step: float = float("nan")
    group_name: str = ""
    filename: str = ""


@dataclass
class ProcessResult:
    ok: bool
    message: str = ""
    arrays: Optional[SpectrumArrays] = None
    info: Dict[str, Any] = field(default_factory=dict)


def _require_larch():
    from .fit_engine import _require_larch as _rl

    return _rl()


_AUTOBK_LOCK = threading.Lock()


def _run_autobk(autobk, g, **kw) -> bool:
    """larch autobk with exactly these settings; False = no spline covariance.

    larch's autobk takes coefs_std from leastsq's covariance without checking
    it (larch/xafs/autobk.py ``covar[i, i]``), so a singular spline fit raises
    TypeError although the best-fit knots — and so bkg and chi(k) — are fine.
    For this call only, a missing covariance reaches autobk as NaN (coefs_std
    = NaN, i.e. unavailable) and is set back to larch's own None afterwards.
    Every other error propagates.  Never retry with other parameters: the old
    ``except TypeError: autobk(g, rbkg=…)`` silently swapped in larch's default
    spline kweight / dk / k range and still reported "Processed" (v4.4.1).
    """
    mod = sys.modules.get("larch.xafs.autobk")
    singular: List[int] = []
    with _AUTOBK_LOCK:
        real = getattr(mod, "leastsq", None)
        if real is None:
            autobk(g, **kw)
            return True

        def leastsq(*args, **kwargs):
            out = real(*args, **kwargs)
            if out[1] is None:
                n = len(out[0])
                singular.append(n)
                out = (out[0], np.full((n, n), np.nan)) + tuple(out[2:])
            return out

        mod.leastsq = leastsq
        try:
            autobk(g, **kw)
        finally:
            mod.leastsq = real
    if singular:
        details = getattr(g, "autobk_details", None)
        if details is not None:
            details.covar = None
    return not singular


def _usable_phase_feff(path: str | Path | None) -> Optional[Path]:
    if not path:
        return None
    p = Path(path)
    return p if p.is_file() else None


def _path_phase_on_k(k: np.ndarray, feff_path: Path) -> np.ndarray:
    """FEFF path phase φ(k) on the data k grid (unwrapped, no 2kR term)."""
    from larch.xafs.feffdat import FeffDatFile

    fd = FeffDatFile(filename=str(feff_path))
    fk = np.asarray(fd.k, dtype=float)
    pha = np.asarray(fd.pha, dtype=float)
    if fk.size < 4 or pha.size < 4:
        raise ValueError(f"feff file has no phase array: {feff_path}")
    return np.interp(k, fk, np.unwrap(pha))


def resolve_phase_feff_file(
    explicit: str = "",
    absorber: str = "",
    extra: Optional[List[str]] = None,
) -> Optional[Path]:
    """Pick a feffNNNN.dat for path phase correction.

    Preference: explicit path → extra Fit-page files → bundled data/feff_paths.
    When *absorber* is set (e.g. Pt), skip files whose FEFF absorber disagrees.
    """
    from .fit_engine import DEFAULT_FEFF_OUT

    want = str(absorber or "").strip()
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    for item in extra or []:
        if item:
            candidates.append(Path(item))
    if DEFAULT_FEFF_OUT.is_dir():
        candidates.extend(sorted(DEFAULT_FEFF_OUT.glob("path1*.dat")))
        candidates.extend(sorted(DEFAULT_FEFF_OUT.glob("feff*.dat")))
    seen = set()
    for cand in candidates:
        try:
            key = cand.resolve()
        except OSError:
            continue
        if key in seen or not cand.is_file():
            continue
        seen.add(key)
        if want:
            got = _feff_absorber_symbol(cand)
            if got and got.lower() != want.lower():
                continue
        return cand
    return None


def _feff_absorber_symbol(path: Path) -> str:
    try:
        from larch.xafs.feffdat import FeffDatFile

        return str(getattr(FeffDatFile(filename=str(path)), "absorber", "") or "").strip()
    except Exception:
        return ""


class AthenaSession:
    """
    One spectrum session: raw μ(E) + processing settings + last processed group.
    """

    def __init__(self):
        self.settings = AthenaSettings()
        self.raw_path: Optional[str] = None
        self.group_name: Optional[str] = None
        self._raw_energy: Optional[np.ndarray] = None
        self._raw_mu: Optional[np.ndarray] = None
        self._group = None  # last processed larch Group
        self.arrays = SpectrumArrays()
        self.last_error: str = ""

    @property
    def has_data(self) -> bool:
        return self._raw_energy is not None and self._raw_mu is not None

    @property
    def group(self):
        return self._group

    def load_file(self, path: str | Path, group_name: Optional[str] = None) -> ProcessResult:
        """Load .xdi / .prj / column data into raw energy/mu, then process."""
        from .fit_engine import load_spectrum, list_athena_groups, _load_xdi_manual

        path = Path(path)
        L = _require_larch()
        self.raw_path = str(path)
        self.group_name = group_name
        try:
            if path.suffix.lower() == ".prj":
                # Prefer Athena-stored chi if present, but still keep energy/mu for Athena UI
                g = load_spectrum(path, group_name=group_name, rbkg=self.settings.rbkg)
                energy = np.asarray(getattr(g, "energy", []), dtype=float)
                mu = np.asarray(getattr(g, "mu", []), dtype=float)
                if energy.size < 5 or mu.size < 5:
                    # some prj groups only have chi
                    self._group = g
                    self._raw_energy = np.asarray(getattr(g, "energy", []), dtype=float) if hasattr(g, "energy") else None
                    self._raw_mu = np.asarray(getattr(g, "mu", []), dtype=float) if hasattr(g, "mu") else None
                    # if only chi, still allow FT/fit path
                    if hasattr(g, "chi") and g.chi is not None:
                        self._extract_arrays(g)
                        return ProcessResult(ok=True, message="Loaded prj group (chi present)", arrays=self.arrays)
                    return ProcessResult(ok=False, message="prj group has no usable energy/mu or chi")
                self._raw_energy = energy
                self._raw_mu = mu
                self.group_name = getattr(g, "group_name", group_name) or path.stem
                # seed e0 from Athena if available
                if getattr(g, "e0", None) is not None and self.settings.e0 is None:
                    try:
                        self.settings.e0 = float(g.e0)
                    except Exception:
                        pass
            else:
                # XDI / ASCII
                g_manual = _load_xdi_manual(path, L)
                self._raw_energy = np.asarray(g_manual.energy, dtype=float)
                self._raw_mu = np.asarray(g_manual.mu, dtype=float)
                self.group_name = path.stem

            return self.reprocess()
        except Exception as exc:
            self.last_error = traceback.format_exc()
            return ProcessResult(ok=False, message=f"{exc}\n{self.last_error}")

    def list_groups(self, path: str | Path) -> List[str]:
        from .fit_engine import list_athena_groups

        return list_athena_groups(path)

    def reprocess(self) -> ProcessResult:
        """Run pre_edge → autobk → xftf with current settings."""
        if not self.has_data:
            # allow chi-only group
            if self._group is not None and hasattr(self._group, "chi"):
                try:
                    self._ft_only(self._group)
                    self._extract_arrays(self._group)
                    return ProcessResult(ok=True, message="FT updated (chi-only group)", arrays=self.arrays)
                except Exception as exc:
                    return ProcessResult(ok=False, message=str(exc))
            return ProcessResult(ok=False, message="No spectrum loaded")

        L = _require_larch()
        s = self.settings
        try:
            g = L["Group"]()
            g.energy = np.asarray(self._raw_energy, dtype=float).copy()
            g.mu = np.asarray(self._raw_mu, dtype=float).copy()
            g.filename = self.raw_path or ""
            g.group_name = self.group_name or "spectrum"

            pe_kw: Dict[str, Any] = {
                "pre1": s.pre1,
                "pre2": s.pre2,
                "norm1": s.norm1,
                "norm2": s.norm2,
                "nvict": s.nvict,
                "nnorm": s.nnorm,
                "make_flat": bool(s.make_flat),
            }
            if s.e0 is not None and np.isfinite(s.e0):
                pe_kw["e0"] = float(s.e0)
            # no fallback call: a retry with fewer keywords would normalize
            # with windows the panel does not show (v4.4.1)
            L["pre_edge"](g, **pe_kw)

            ab_kw: Dict[str, Any] = {
                "rbkg": s.rbkg,
                "kweight": s.bkg_kweight,
                "clamp_lo": int(getattr(s, "bkg_clamp_lo", 0)),
                "clamp_hi": int(getattr(s, "bkg_clamp_hi", 1)),
            }
            for key, val in (
                ("kmin", s.bkg_kmin),
                ("kmax", s.bkg_kmax),
                ("dk", s.bkg_dk),
            ):
                ab_kw[key] = val
            bkg_covariance = _run_autobk(L["autobk"], g, **ab_kw)

            self._forward_transform(g)
            self._back_transform(g)

            self._group = g
            self._extract_arrays(g)
            k_arr = getattr(g, "k", None)
            n_k = int(len(k_arr)) if k_arr is not None else 0
            info = {
                "e0": float(getattr(g, "e0", float("nan"))),
                "edge_step": float(getattr(g, "edge_step", float("nan"))),
                "rbkg": s.rbkg,
                "n_energy": int(len(g.energy)),
                "n_k": n_k,
                "phase_corrected": bool(getattr(g, "phase_corrected", False)),
                "phase_feff": str(getattr(g, "phase_feff", "") or ""),
                "phase_note": str(getattr(g, "phase_note", "") or ""),
                # False = singular AUTOBK spline fit: chi(k) is the fit with these
                # settings, only its uncertainties (coefs_std) are unavailable
                "bkg_covariance": bkg_covariance,
            }
            # write back auto e0 into settings display (don't lock if user set None)
            if s.e0 is None and np.isfinite(info["e0"]):
                # keep None as auto, but arrays.e0 holds value
                pass
            message = "Processed" if bkg_covariance else (
                "Processed (AUTOBK spline covariance unavailable: uncertainties not computed)"
            )
            return ProcessResult(ok=True, message=message, arrays=self.arrays, info=info)
        except Exception as exc:
            self.last_error = traceback.format_exc()
            return ProcessResult(ok=False, message=f"{exc}\n{self.last_error}")

    def _forward_transform(self, g) -> None:
        """k → R FT. Optional Athena-style path phase correction of |χ(R)|."""
        s = self.settings
        g.phase_corrected = False
        g.phase_feff = ""
        g.phase_note = ""
        if bool(getattr(s, "with_phase", False)):
            feff = _usable_phase_feff(getattr(s, "phase_feff", ""))
            if feff is None:
                g.phase_note = "no feffNNNN.dat"
                self._xftf_plain(g, s)
                return
            try:
                self._xftf_phase_corrected(g, s, feff)
                g.phase_corrected = True
                g.phase_feff = str(feff)
                g.phase_note = ""
                return
            except Exception as exc:
                g.phase_note = f"phase FT failed: {exc}"
                self._xftf_plain(g, s)
                return
        self._xftf_plain(g, s)

    def _xftf_plain(self, g, s: AthenaSettings) -> None:
        """Ordinary larch xftf. with_phase=True only fills chir_pha for export."""
        L = _require_larch()
        kw = dict(
            kmin=s.kmin,
            kmax=s.kmax,
            dk=s.dk,
            window=s.kwindow,
            kweight=s.kweight_plot,
            with_phase=True,
        )
        try:
            L["xftf"](g, **kw)
        except TypeError:
            kw.pop("with_phase", None)
            L["xftf"](g, **kw)

    def _xftf_phase_corrected(self, g, s: AthenaSettings, feff_path: Path) -> None:
        """FT of χ(k)·exp(−i φ(k)) using the FEFF path phase (not 2kR).

        Public XAFS convention (Newville/Ravel): the remaining oscillation is
        approximately sin(2kR), so |χ(R)| peaks near the geometric distance
        instead of ~0.3–0.5 Å short. φ is FeffDatFile.pha = central-atom +
        scatterer phase from feffNNNN.dat. larch xftf(with_phase=True) does
        not do this — it only writes chir_pha.
        """
        from larch.math import complex_phase
        from larch.xafs.xafsft import xftf_fast, xftf_prep

        k = np.asarray(g.k, dtype=float)
        chi = np.asarray(g.chi, dtype=float)
        if k.size < 8 or chi.size < 8:
            raise ValueError("chi(k) too short for FT")
        phi = _path_phase_on_k(k, feff_path)
        kstep = float(k[1] - k[0])
        if not np.isfinite(kstep) or kstep <= 0:
            kstep = 0.05
        weighted, win = xftf_prep(
            k,
            chi,
            kmin=s.kmin,
            kmax=s.kmax,
            kweight=s.kweight_plot,
            dk=s.dk,
            window=s.kwindow,
            kstep=kstep,
        )
        npts = len(weighted)
        k_grid = kstep * np.arange(npts, dtype=float)
        phi_grid = np.interp(k_grid, k, phi)
        nfft = 2048
        out = xftf_fast(
            weighted * win * np.exp(-1j * phi_grid),
            nfft=nfft,
            kstep=kstep,
        )
        rmax_out = 10.0
        rstep = np.pi / (kstep * nfft)
        irmax = int(min(nfft / 2, 1.01 + rmax_out / rstep))
        r = rstep * np.arange(irmax)
        mag = np.sqrt(out.real[:irmax] ** 2 + out.imag[:irmax] ** 2)
        g.kwin = win[: len(chi)]
        g.r = r[:irmax]
        g.chir = out[:irmax]
        g.chir_mag = mag[:irmax]
        g.chir_re = np.asarray(out.real[:irmax], dtype=float)
        g.chir_im = np.asarray(out.imag[:irmax], dtype=float)
        g.chir_pha = complex_phase(out[:irmax])

    def _ft_only(self, g) -> None:
        self._forward_transform(g)
        self._back_transform(g)

    def _back_transform(self, g) -> None:
        """χ(R) → χ(q) so the q panel shows a real back-transform, not χ(k)."""
        s = self.settings
        try:
            chir = np.asarray(g.chir_re, dtype=float) + 1j * np.asarray(g.chir_im, dtype=float)
            kw = dict(
                rmin=s.rmin,
                rmax=s.rmax,
                dr=s.dr,
                window=s.rwindow,
                with_phase=True,  # chiq_re / chiq_im / chiq_pha for export
            )
            try:
                _require_larch()["xftr"](
                    np.asarray(g.r, dtype=float),
                    chir,
                    group=g,
                    **kw,
                )
            except TypeError:
                kw.pop("with_phase", None)
                _require_larch()["xftr"](
                    np.asarray(g.r, dtype=float),
                    chir,
                    group=g,
                    **kw,
                )
        except Exception:
            pass  # q-space is a nice-to-have; never fail processing over it

    def _extract_arrays(self, g) -> None:
        def arr(name: str) -> Optional[np.ndarray]:
            if not hasattr(g, name):
                return None
            v = getattr(g, name)
            if v is None:
                return None
            a = np.asarray(v, dtype=float)
            return a.copy() if a.size else None

        self.arrays = SpectrumArrays(
            energy=arr("energy"),
            mu=arr("mu"),
            pre_edge=arr("pre_edge"),
            post_edge=arr("post_edge"),
            norm=arr("norm"),
            flat=arr("flat"),
            bkg=arr("bkg"),
            dmude=arr("dmude"),
            d2mude=arr("d2mude"),
            k=arr("k"),
            chi=arr("chi"),
            r=arr("r"),
            chir_mag=arr("chir_mag"),
            chir_re=arr("chir_re"),
            chir_im=arr("chir_im"),
            chir_pha=arr("chir_pha"),
            phase_corrected=bool(getattr(g, "phase_corrected", False)),
            phase_feff=str(getattr(g, "phase_feff", "") or ""),
            q=arr("q"),
            chiq_mag=arr("chiq_mag"),
            chiq_re=arr("chiq_re"),
            chiq_im=arr("chiq_im"),
            chiq_pha=arr("chiq_pha"),
            e0=float(getattr(g, "e0", float("nan"))),
            edge_step=float(getattr(g, "edge_step", float("nan"))),
            group_name=str(getattr(g, "group_name", self.group_name or "")),
            filename=str(getattr(g, "filename", self.raw_path or "")),
        )

    def snapshot_group_for_fit(self):
        """Return the processed larch Group to feed into feffit (same object)."""
        if self._group is None:
            raise RuntimeError("No processed spectrum — load and process first")
        return self._group
