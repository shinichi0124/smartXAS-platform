# -*- coding: utf-8 -*-
"""Continuous wavelet transform (CWT) of EXAFS chi(k) -- clean-room module.

Written from the published literature only; no existing wavelet-EXAFS program
(source or binary) was consulted.

References
----------
[F05] H. Funke, A. C. Scheinost, M. Chukalina, Phys. Rev. B 71, 094110 (2005).
      Morlet wavelet for EXAFS; parameters eta (frequency) and sigma
      (half-width of the Gaussian envelope).
[M03] M. Munoz, P. Argoul, F. Farges, Am. Mineral. 88, 694 (2003).
      Cauchy wavelet of order n for EXAFS.
[T09] J. Timoshenko, A. Kuzmin, Comput. Phys. Commun. 180, 920 (2009).
      Review of wavelet EXAFS analysis and its conventions.

The full derivation (units, k <-> R mapping, normalization, edges,
discretization, defaults) is in DERIVATION.md next to this file.  Summary:

Units: k in A^-1, R in A.  The signal is x(k) = k**kw * chi(k), restricted to
the window [kmin, kmax] (zero outside it), and

    W(k, R) = 2 * Integral x(k') * conj(h_R(k' - k)) dk'

with the dilated wavelet h_R chosen so that a component A*sin(2*k*R0 + phi)
gives a ridge exactly at R = R0 with |W(k, R0)| = A (away from the window
edges).  Both wavelets satisfy conj(h_R(-u)) = h_R(u), so W is the linear
convolution (x * h_R)(k) times 2:

    Morlet [F05]:  h_R(u) = exp(2iRu - u^2 / (2 s^2)) / (sqrt(2 pi) s),
                   s = eta * sigma / (2 R)                       [A^-1]
    Cauchy [M03]:  h_R(u) = (1 - i u / a)^-(n+1) / (a * C_n),
                   a = n / (2 R),  C_n = 2 pi n^n e^-n / Gamma(n + 1)

R-resolution (1 std): Morlet R / (eta*sigma); Cauchy ~ R / sqrt(n).
k-resolution (1 std): Morlet eta*sigma / (2R); Cauchy ~ sqrt(n) / (2R).

The public API (names and signatures) is fixed by the host application.
No Qt, no network, no inverse transform.  matplotlib and openpyxl are only
imported inside the functions that need them.
"""

from __future__ import annotations

import math
import unicodedata

import numpy as np
from scipy import fft as _sp_fft

__all__ = [
    "MORLET",
    "CAUCHY",
    "auto_params",
    "default_dR",
    "cwt",
    "write_wt_csv",
    "write_wt_xlsx",
    "draw_wt",
    "save_wt_png",
]

MORLET = "morlet"
CAUCHY = "cauchy"
_WAVELETS = (MORLET, CAUCHY)

# ---- defaults (rationale in DERIVATION.md, section "Default parameters") ----
_DEFAULT_KW = 2
_DEFAULT_ETA = 12.0     # Morlet frequency parameter (dimensionless); Q = eta*sigma = 12
_DEFAULT_SIGMA = 1.0    # Morlet half-width parameter (dimensionless)
_DEFAULT_NORDER = 144   # Cauchy order; n = Q^2 gives the same R- and k-resolution as Morlet
_DEFAULT_RMIN = 0.0     # A
_DEFAULT_RMAX = 6.0     # A

# ---- guards against accidental huge grids (memory/time) ----
_MAX_AXIS_POINTS = 20001          # per axis (k or R)
_MAX_CELLS = 20_000_000           # len(R_grid) * len(k_grid)
_CHUNK_ELEMS = 1 << 21            # complex elements per FFT work chunk (~32 MB)
_XLSX_MAX_ROWS = 1_048_576        # Excel sheet row limit (incl. header)
_XLSX_MAX_COLS = 16_384


# =============================================================================
# validation helpers
# =============================================================================
def _as_float(name, value):
    """Convert a scalar parameter to a finite float or raise ValueError."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got a bool ({value!r})")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a real number, got {value!r}") from None
    if not math.isfinite(v):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return v


def _validate_k(k):
    """Return k as a 1-D float array; it must be finite, strictly increasing, >= 2 points."""
    try:
        arr = np.asarray(k, dtype=float)
    except (TypeError, ValueError):
        raise ValueError("k must be a 1-D array of real numbers") from None
    if arr.ndim != 1:
        raise ValueError(f"k must be 1-D, got an array of shape {arr.shape}")
    if arr.size < 2:
        raise ValueError(f"k needs at least 2 points, got {arr.size}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("k contains non-finite values (nan or inf)")
    if not np.all(np.diff(arr) > 0):
        raise ValueError("k must be strictly increasing")
    return arr


def _validate_kw(kw):
    if isinstance(kw, bool):
        raise ValueError(f"kw must be an integer 0..3, got {kw!r}")
    try:
        v = float(kw)
    except (TypeError, ValueError):
        raise ValueError(f"kw must be an integer 0..3, got {kw!r}") from None
    if not math.isfinite(v) or v != round(v) or not 0 <= v <= 3:
        raise ValueError(f"kw must be an integer 0..3, got {kw!r}")
    return int(round(v))


def _validate_wavelet(wavelet):
    if not isinstance(wavelet, str):
        raise ValueError(f"wavelet must be {MORLET!r} or {CAUCHY!r}, got {wavelet!r}")
    w = wavelet.strip().lower()
    if w not in _WAVELETS:
        raise ValueError(f"unknown wavelet {wavelet!r}; expected {MORLET!r} or {CAUCHY!r}")
    return w


def _uniform_grid(start, stop, step):
    """start, start+step, ... up to stop (inclusive within float tolerance)."""
    ratio = (stop - start) / step
    n = int(math.floor(ratio + 1e-9 * max(1.0, abs(ratio)))) + 1
    grid = start + step * np.arange(n, dtype=float)
    # remove float noise such as 0.15000000000000002 (harmless at 1e-10 level)
    return np.round(grid, 10)


def _nice(x, digits=6):
    """Round to `digits` significant figures (removes float noise of a data step)."""
    return float(f"{float(x):.{digits}g}")


def _nice_up(x, digits=3):
    """Round up to `digits` significant figures."""
    x = float(x)
    if x <= 0:
        return x
    e = math.floor(math.log10(x)) - (digits - 1)
    return math.ceil(x / 10.0 ** e) * 10.0 ** e


# =============================================================================
# public: defaults
# =============================================================================
def default_dR(kmin, kmax):
    """Default R step (A) for the k window [kmin, kmax] (A^-1).

    The natural R-resolution element of a window of length Dk = kmax - kmin is
    pi / (2 * Dk) (spacing of independent Fourier points for a sin(2kR)
    signal).  We oversample it 5x so a ridge can be read to ~0.02 A, round to
    0.01 A, and clip to [0.01, 0.05] A (keeps a 0..6 A map between 121 and
    601 rows).  Dk = 15 A^-1 -> 0.02 A; Dk = 10 -> 0.03 A; Dk <= 6.9 -> 0.05 A;
    Dk >= 21 -> 0.01 A.
    """
    kmin = _as_float("kmin", kmin)
    kmax = _as_float("kmax", kmax)
    if not kmax > kmin:
        raise ValueError(f"kmax must be greater than kmin (got kmin={kmin}, kmax={kmax})")
    raw = math.pi / (10.0 * (kmax - kmin))
    step = round(raw, 2)
    return float(min(max(step, 0.01), 0.05))


def auto_params(k):
    """Sensible default transform parameters for data sampled at `k` (A^-1).

    Returns a dict with exactly the keys
    kw, wavelet, kmin, kmax, dk, Rmin, Rmax, dR, sigma, eta, norder.
    The k window is the full data range, dk is the median data step (rounded
    to 6 significant figures), dR = default_dR(kmin, kmax), Rmax = 6 A unless
    the Nyquist limit pi / (2 dk) is lower.  Raises ValueError for unusable k
    (< 2 points, non-finite, not strictly increasing, not 1-D).
    """
    k = _validate_k(k)
    kmin = float(k[0])
    kmax = float(k[-1])
    span = kmax - kmin
    dk = min(_nice(np.median(np.diff(k))), span)
    dR = default_dR(kmin, kmax)

    r_nyq = math.pi / (2.0 * dk)
    rmax = _DEFAULT_RMAX if r_nyq >= _DEFAULT_RMAX else math.floor(r_nyq * 1000.0) / 1000.0
    if not rmax > _DEFAULT_RMIN:
        raise ValueError(f"k step {dk} A^-1 is too coarse for a wavelet map (Nyquist R = {r_nyq:.4g} A)")

    # keep the default grid inside the size guards (only matters for >20000-point data)
    n_r = int(math.floor((rmax - _DEFAULT_RMIN) / dR + 1e-9)) + 1
    max_nk = min(_MAX_AXIS_POINTS, _MAX_CELLS // max(n_r, 1))
    if span / dk + 1 > max_nk:
        dk = _nice_up(span / (max_nk - 1))

    return {
        "kw": _DEFAULT_KW,
        "wavelet": MORLET,
        "kmin": kmin,
        "kmax": kmax,
        "dk": dk,
        "Rmin": _DEFAULT_RMIN,
        "Rmax": rmax,
        "dR": dR,
        "sigma": _DEFAULT_SIGMA,
        "eta": _DEFAULT_ETA,
        "norder": _DEFAULT_NORDER,
    }


# =============================================================================
# wavelet kernels
# =============================================================================
def _kernel_rows(R, u, wavelet, q, norder):
    """Dilated wavelet h_R(u) for each R (> 0) on offsets u = k' - k (A^-1).

    Returns a complex array of shape (len(R), len(u)).  Normalized so that the
    Fourier transform  H_R(w) = Integral h_R(u) exp(-i w u) du  equals 1 at
    w = 2R (the frequency of sin(2 k R)), see DERIVATION.md sections 2-3.
    """
    R = np.asarray(R, dtype=float)[:, None]
    u = np.asarray(u, dtype=float)[None, :]
    if wavelet == MORLET:
        s = q / (2.0 * R)                                   # envelope std in k, A^-1
        arg = -(u * u) / (2.0 * s * s) + 1j * (2.0 * R) * u
        return np.exp(arg) / (math.sqrt(2.0 * math.pi) * s)
    # Cauchy: psi_n(t) = (1 - i t)^-(n+1) = (1+t^2)^-(n+1)/2 * exp(i (n+1) atan t)
    n = norder
    a = n / (2.0 * R)                                       # scale, A^-1
    t = u / a
    log_c = math.log(2.0 * math.pi) + n * math.log(n) - n - math.lgamma(n + 1.0)
    arg = -0.5 * (n + 1.0) * np.log1p(t * t) + 1j * (n + 1.0) * np.arctan(t) - log_c
    return np.exp(arg) / a


def _cwt_core(x, dk, R_grid, wavelet, q, norder):
    """|W| on the uniform grid: x sampled at step dk, zero outside the window.

    Exact discrete linear convolution  W_i = 2 dk |sum_j x_j h_R((i - j) dk)|
    evaluated with FFTs of length >= 3N - 2 (no circular wrap-around).
    """
    n_k = x.size
    W = np.zeros((R_grid.size, n_k), dtype=float)
    rows = np.nonzero(R_grid > 0.0)[0]        # R = 0: limit of the transform is 0
    if rows.size == 0:
        return W
    u = dk * np.arange(-(n_k - 1), n_k, dtype=float)       # 2N-1 offsets
    n_fft = _sp_fft.next_fast_len(3 * n_k - 2)
    X = _sp_fft.fft(x, n_fft)
    chunk = max(1, _CHUNK_ELEMS // n_fft)
    for start in range(0, rows.size, chunk):
        idx = rows[start:start + chunk]
        h = _kernel_rows(R_grid[idx], u, wavelet, q, norder)
        H = _sp_fft.fft(h, n_fft, axis=1, overwrite_x=True)
        H *= X[None, :]
        conv = _sp_fft.ifft(H, axis=1, overwrite_x=True)
        W[idx] = np.abs(conv[:, n_k - 1:2 * n_k - 1])
    W *= 2.0 * dk
    return W


# =============================================================================
# public: transform
# =============================================================================
def cwt(k, chi, *, kw, kmin, kmax, dk, Rmin, Rmax, dR, wavelet, sigma, eta, norder):
    """Continuous wavelet transform magnitude |W(k, R)| of EXAFS chi(k).

    Parameters (all keyword-only after chi; units A^-1 for k, A for R)
    ----------
    k, chi : 1-D arrays of equal length (>= 2), k strictly increasing.
    kw     : int 0..3; chi is multiplied by k**kw first (0: chi as given).
    kmin, kmax : analysis window, must lie inside [k[0], k[-1]].  Data outside
             the window is not used (zero padding, see DERIVATION.md "Edges").
    dk     : output/analysis k step (> 0); data are linearly interpolated.
    Rmin, Rmax, dR : output R grid, 0 <= Rmin < Rmax, dR > 0, Rmax <= pi/(2 dk).
    wavelet: MORLET or CAUCHY (case-insensitive).
    sigma, eta : Morlet half-width and frequency (> 0); only eta*sigma matters.
    norder : Cauchy order n (> 0, normally an integer).
    sigma, eta and norder are validated whichever wavelet is selected.

    Returns
    -------
    k_grid : kmin..kmax step dk
    R_grid : Rmin..Rmax step dR
    W      : float array (len(R_grid), len(k_grid)), |W| >= 0.  For
             x = A sin(2 k R0 + phi) the ridge is at R0 and |W| = A there
             (away from the window edges, where it drops to ~A/2).

    Raises ValueError (before any computation) for invalid input.
    """
    # ---- data ----
    k = _validate_k(k)
    try:
        chi = np.asarray(chi, dtype=float)
    except (TypeError, ValueError):
        raise ValueError("chi must be a 1-D array of real numbers") from None
    if chi.ndim != 1:
        raise ValueError(f"chi must be 1-D, got an array of shape {chi.shape}")
    if chi.size != k.size:
        raise ValueError(f"k and chi must have the same length (got {k.size} and {chi.size})")
    if not np.all(np.isfinite(chi)):
        raise ValueError("chi contains non-finite values (nan or inf)")

    # ---- parameters ----
    kw = _validate_kw(kw)
    wavelet = _validate_wavelet(wavelet)
    kmin = _as_float("kmin", kmin)
    kmax = _as_float("kmax", kmax)
    dk = _as_float("dk", dk)
    Rmin = _as_float("Rmin", Rmin)
    Rmax = _as_float("Rmax", Rmax)
    dR = _as_float("dR", dR)
    sigma = _as_float("sigma", sigma)
    eta = _as_float("eta", eta)
    norder = _as_float("norder", norder)

    if not kmin < kmax:
        raise ValueError(f"kmin must be smaller than kmax (got kmin={kmin}, kmax={kmax})")
    tol = 1e-6 * max(1.0, float(k[-1] - k[0]))
    if kmin < k[0] - tol or kmax > k[-1] + tol:
        raise ValueError(
            f"k window [{kmin:g}, {kmax:g}] lies outside the data range "
            f"[{k[0]:g}, {k[-1]:g}] A^-1")
    if not dk > 0:
        raise ValueError(f"dk must be > 0 (got {dk})")
    if not dR > 0:
        raise ValueError(f"dR must be > 0 (got {dR})")
    if Rmin < 0:
        raise ValueError(f"Rmin must be >= 0 (got {Rmin})")
    if not Rmin < Rmax:
        raise ValueError(f"Rmin must be smaller than Rmax (got Rmin={Rmin}, Rmax={Rmax})")
    r_nyq = math.pi / (2.0 * dk)
    if Rmax > r_nyq * (1.0 + 1e-9):
        raise ValueError(
            f"Rmax = {Rmax:g} A exceeds the Nyquist limit pi/(2*dk) = {r_nyq:.4g} A "
            f"for dk = {dk:g} A^-1; lower Rmax or dk")
    if not sigma > 0:
        raise ValueError(f"sigma must be > 0 (got {sigma})")
    if not eta > 0:
        raise ValueError(f"eta must be > 0 (got {eta})")
    if not norder > 0:
        raise ValueError(f"norder must be > 0 (got {norder})")

    n_k = int(math.floor((kmax - kmin) / dk + 1e-9 * max(1.0, (kmax - kmin) / dk))) + 1
    if n_k < 2:
        raise ValueError(f"dk = {dk:g} is larger than the k window {kmax - kmin:g}; need >= 2 k points")
    n_r = int(math.floor((Rmax - Rmin) / dR + 1e-9 * max(1.0, (Rmax - Rmin) / dR))) + 1
    if n_k > _MAX_AXIS_POINTS or n_r > _MAX_AXIS_POINTS or n_k * n_r > _MAX_CELLS:
        raise ValueError(
            f"grid too large ({n_r} R x {n_k} k points); increase dk or dR "
            f"(limits: {_MAX_AXIS_POINTS} per axis, {_MAX_CELLS} cells)")

    # ---- grids and weighted, resampled signal ----
    k_grid = _uniform_grid(kmin, kmax, dk)
    R_grid = _uniform_grid(Rmin, Rmax, dR)
    y = chi if kw == 0 else chi * k ** kw
    x = np.interp(k_grid, k, y)

    W = _cwt_core(x, dk, R_grid, wavelet, eta * sigma, norder)
    return k_grid, R_grid, W


# =============================================================================
# public: export
# =============================================================================
def _check_map(k_grid, R_grid, W, finite=True):
    k_grid = np.asarray(k_grid, dtype=float)
    R_grid = np.asarray(R_grid, dtype=float)
    W = np.asarray(W, dtype=float)
    if k_grid.ndim != 1 or R_grid.ndim != 1 or k_grid.size < 1 or R_grid.size < 1:
        raise ValueError("k_grid and R_grid must be non-empty 1-D arrays")
    if W.shape != (R_grid.size, k_grid.size):
        raise ValueError(
            f"W must have shape (len(R_grid), len(k_grid)) = ({R_grid.size}, {k_grid.size}), "
            f"got {W.shape}")
    if finite and not (np.all(np.isfinite(k_grid)) and np.all(np.isfinite(R_grid))
                       and np.all(np.isfinite(W))):
        raise ValueError("k_grid, R_grid and W must be finite")
    return k_grid, R_grid, W


def _long_columns(k_grid, R_grid, W):
    """K, R, coef columns with R in the outer loop and k varying fastest."""
    n_r, n_k = W.shape
    return np.tile(k_grid, n_r), np.repeat(R_grid, n_k), W.reshape(-1)


def write_wt_csv(path, k_grid, R_grid, W_mag):
    """Write the map as text CSV: header "K,R,coef", then one "%.6f" row per
    (R, k) pair, R outer loop, k fastest (len(R_grid)*len(k_grid) rows)."""
    k_grid, R_grid, W_mag = _check_map(k_grid, R_grid, W_mag)
    kk, rr, cc = _long_columns(k_grid, R_grid, W_mag)
    cols = [(c + 0.0).tolist() for c in (kk, rr, cc)]     # + 0.0 turns -0.0 into 0.0
    row = "%.6f,%.6f,%.6f\n"
    # explicit LF line endings on every platform (text mode would give CRLF on Windows)
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("K,R,coef\n")
        fh.writelines(row % t for t in zip(*cols))


_BAD_SHEET_CHARS = set('[]:*?/\\')


def _sheet_title(name, used):
    """Make a valid, unique Excel sheet title (<= 31 chars, no []:*?/\\)."""
    title = "".join("_" if c in _BAD_SHEET_CHARS else c for c in str(name)).strip("'").strip()
    if not title:
        title = f"Sheet{len(used) + 1}"
    title = title[:31]
    base, i = title, 2
    while title.lower() in used:
        suffix = f"_{i}"
        title = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(title.lower())
    return title


def write_wt_xlsx(path, sheets):
    """Write several maps to an .xlsx workbook.

    sheets: list of (sheet_name, [(group_name, k_grid, R_grid, W_mag), ...]).
    One worksheet per sheet_name, in order (invalid sheet-name characters are
    replaced by '_', names truncated to 31 chars and made unique).  Each group
    occupies three adjacent columns headed "K__<g>", "R__<g>", "coef__<g>",
    rows ordered like the CSV (R outer, k fastest), values rounded to 6
    decimals; one empty column separates consecutive groups.
    """
    import openpyxl  # imported here on purpose (host requirement)

    sheets = list(sheets)
    if not sheets:
        raise ValueError("sheets is empty; nothing to write")
    prepared = []
    for entry in sheets:
        try:
            sheet_name, groups = entry
        except (TypeError, ValueError):
            raise ValueError("each sheet must be a (sheet_name, groups) pair") from None
        cols, header = [], []
        for gi, group in enumerate(list(groups)):
            try:
                gname, kg, rg, wg = group
            except (TypeError, ValueError):
                raise ValueError(
                    "each group must be (group_name, k_grid, R_grid, W_mag)") from None
            kg, rg, wg = _check_map(kg, rg, wg)
            if gi:
                header.append(None)
            header += [f"K__{gname}", f"R__{gname}", f"coef__{gname}"]
            cols.append(tuple([round(v, 6) for v in c.tolist()]
                              for c in _long_columns(kg, rg, wg)))
        n_rows = max((len(c[0]) for c in cols), default=0)
        if n_rows + 1 > _XLSX_MAX_ROWS:
            raise ValueError(f"sheet {sheet_name!r}: {n_rows} rows exceed the Excel limit")
        if len(header) > _XLSX_MAX_COLS:
            raise ValueError(f"sheet {sheet_name!r}: too many groups for one worksheet")
        prepared.append((sheet_name, header, cols, n_rows))

    wb = openpyxl.Workbook(write_only=True)
    used = set()
    for sheet_name, header, cols, n_rows in prepared:
        ws = wb.create_sheet(title=_sheet_title(sheet_name, used))
        if header:
            ws.append(header)
        for r in range(n_rows):
            row = []
            for gi, (kk, rr, cc) in enumerate(cols):
                if gi:
                    row.append(None)
                if r < len(kk):
                    row += [kk[r], rr[r], cc[r]]
                else:
                    row += [None, None, None]
            ws.append(row)
    wb.save(path)


# =============================================================================
# public: drawing (matplotlib imported lazily; no GUI backend is touched)
# =============================================================================
def _ascii(text):
    """Plain-ASCII version of a label: accents stripped, other non-ASCII -> '?',
    '$' escaped so matplotlib never switches to mathtext."""
    out = []
    for ch in unicodedata.normalize("NFKD", str(text)):
        if ord(ch) < 128:
            out.append(ch)
        elif not unicodedata.combining(ch):
            out.append("?")
    return "".join(out).replace("$", r"\$")


_ASCII_FORMATTER_CLS = None


def _ascii_formatter():
    """ScalarFormatter that never emits the Unicode minus sign (U+2212)."""
    global _ASCII_FORMATTER_CLS
    if _ASCII_FORMATTER_CLS is None:
        from matplotlib import ticker

        class _AsciiScalarFormatter(ticker.ScalarFormatter):
            def __call__(self, x, pos=None):
                return super().__call__(x, pos).replace("\u2212", "-")

            def get_offset(self):
                return super().get_offset().replace("\u2212", "-")

        _ASCII_FORMATTER_CLS = _AsciiScalarFormatter
    return _ASCII_FORMATTER_CLS(useMathText=False)


def _is_uniform(grid):
    if grid.size < 3:
        return True
    d = np.diff(grid)
    return bool(np.all(d > 0) and np.ptp(d) <= 1e-6 * abs(d.mean()))


def _half_step(grid):
    return 0.5 * float(grid[1] - grid[0]) if grid.size > 1 else 0.5


def _undo_previous_draw(ax):
    """Undo what an earlier draw_wt call did to `ax`: its colour-bar axes, the
    space that colour bar took from `ax`, and its own artists if still attached.

    Deliberately not Colorbar.remove(): once the host has called ax.clear(),
    the colour bar's image is detached and Colorbar.remove() fails before it
    gives the space back, so every redraw would shrink `ax` again.
    """
    state = getattr(ax, "_wavelet_cr_state", None)
    if state is None:
        return
    ax._wavelet_cr_state = None
    for art in state["artists"]:          # still attached only if ax was not cleared
        if art.axes is ax:
            try:
                art.remove()
            except (NotImplementedError, ValueError):
                pass
    fig = ax.figure
    cax = state["cax"]
    if cax in fig.axes:
        fig.delaxes(cax)
    parents_cbars = getattr(ax, "_colorbars", None)   # used by layout engines
    if isinstance(parents_cbars, list) and cax in parents_cbars:
        parents_cbars.remove(cax)
    # restore the geometry recorded before the colour bar took its space,
    # unless the host has re-laid out the axes since then
    if state["spec_before"] is not None:
        if ax.get_subplotspec() is state["spec_after"]:
            ax.set_subplotspec(state["spec_before"])
            ax.set_anchor(state["anchor_before"])
    else:
        pos = ax.get_position(original=True)
        if np.allclose(pos.bounds, state["pos_after"].bounds, rtol=0.0, atol=1e-12):
            ax._set_position(state["pos_before"], which="both")
            ax.set_anchor(state["anchor_before"])


def draw_wt(ax, k_grid, R_grid, W, kmin=None, kmax=None, title="", fontsize=10):
    """Draw |W| as a heat map on a matplotlib Axes (x = k in A^-1, y = R in A).

    Adds a colour bar labelled "|W|" to ax.figure, axis labels, the title, and
    dashed vertical lines at kmin / kmax when given.  All text is plain ASCII
    (non-ASCII title characters become '?').

    Redraws: any number of calls on the same axes, with or without ax.clear()
    in between, leave exactly one colour bar and the same axes geometry as
    after the first call (the previous colour bar, the space it took, and the
    previous image / window lines are removed first).
    """
    k_grid, R_grid, W = _check_map(k_grid, R_grid, W, finite=False)
    _undo_previous_draw(ax)
    fig = ax.figure

    finite = W[np.isfinite(W)]
    vmax = float(finite.max()) if finite.size else 1.0
    if not vmax > 0:
        vmax = 1.0
    x0, x1 = float(k_grid[0]) - _half_step(k_grid), float(k_grid[-1]) + _half_step(k_grid)
    y0, y1 = float(R_grid[0]) - _half_step(R_grid), float(R_grid[-1]) + _half_step(R_grid)
    if _is_uniform(k_grid) and _is_uniform(R_grid):
        im = ax.imshow(W, origin="lower", aspect="auto", extent=(x0, x1, y0, y1),
                       interpolation="nearest", cmap="viridis", vmin=0.0, vmax=vmax)
    else:
        im = ax.pcolormesh(k_grid, R_grid, W, shading="nearest", cmap="viridis",
                           vmin=0.0, vmax=vmax)
    spec_before = ax.get_subplotspec()                 # None for fig.add_axes axes
    pos_before = ax.get_position(original=True).frozen()
    anchor_before = ax.get_anchor()
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("|W|", fontsize=fontsize)
    cb.ax.yaxis.set_major_formatter(_ascii_formatter())
    cb.ax.tick_params(labelsize=fontsize)

    artists = [im]
    for kv in (kmin, kmax):
        if kv is not None:
            artists.append(ax.axvline(float(kv), color="white", linestyle="--",
                                      linewidth=1.0, alpha=0.9))
    ax._wavelet_cr_state = {
        "cax": cb.ax,
        "artists": artists,
        "spec_before": spec_before,
        "spec_after": ax.get_subplotspec(),
        "pos_before": pos_before,
        "pos_after": ax.get_position(original=True).frozen(),
        "anchor_before": anchor_before,
    }
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.xaxis.set_major_formatter(_ascii_formatter())
    ax.yaxis.set_major_formatter(_ascii_formatter())
    ax.set_xlabel("k (A^-1)", fontsize=fontsize)
    ax.set_ylabel("R (A)", fontsize=fontsize)
    ax.set_title(_ascii(title), fontsize=fontsize)
    ax.tick_params(labelsize=fontsize)


def save_wt_png(path, k_grid, R_grid, W, kmin, kmax, title=""):
    """Save the draw_wt picture to a PNG using matplotlib's Agg canvas (no GUI)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(7.0, 5.0), dpi=120, layout="constrained")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    draw_wt(ax, k_grid, R_grid, W, kmin=kmin, kmax=kmax, title=title)
    fig.savefig(path, format="png", dpi=120)
