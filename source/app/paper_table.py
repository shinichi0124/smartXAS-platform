"""EXAFS paper table: the one place where the table format lives.

Source of truth: AI projects/AI-Skills-Reference/shared-skills/xas-processing/scripts/exafs_paper_table.py
Spec: references/exafs-paper-table.md  (read it before changing anything here).
Qt-free; depends only on openpyxl (write_sheet / write_xlsx).  smartLarch Platform keeps a
byte-identical copy as source/app/paper_table.py -- DO NOT EDIT THE COPY: change the skill file,
then copy it over (a smartLarch test compares the two byte for byte).

Input is a list of blocks (one per absorption edge)::

    Row = {
        "sample": str, "path": str,
        "cn": float, "cn_err": float | None, "cn_fixed": bool, "cn_lower_bound": bool,
        "amp": float | None,                 # N*S0^2, used when cn is NaN (mark §)
        "r": float, "r_err": float | None,
        "ss": float, "ss_err": float | None, "ss_at_bound": bool,   # ss in A^2
        "de0": float | None, "de0_err": float | None,
        "s02": float | None, "s02_err": float | None,  # s02_err only when S0^2 was fitted
        "rfactor": float | None,
    }
    Block = {"title": "Ni K-edge", "rows": [Row, ...]}   # rows of one sample are consecutive

Nothing here computes a physical quantity; it only formats numbers that the fit produced.
"""

from __future__ import annotations

import math
import os
import re
import zipfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

HEADER = ["Sample", "Path", "CN", "R (Å)", "σ² (10⁻³ Å²)", "ΔE₀ (eV)", "S₀²", "R-factor"]
COLUMN_WIDTHS = (12, 9, 15, 16, 16, 13, 12, 10)
DAGGER, DDAGGER, SECTION = "†", "‡", "§"
DEFAULT_CN_REL_ERR_LIMIT = 0.45
DEFAULT_INTRO = (
    "Select from the Sample row to the last row of the table, copy and paste into Word; "
    "the notes below the table are the table notes."
)
_SUP = {"1": "¹", "2": "²", "3": "³", "0": "⁰", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def pm(x: Any, err: Any, digits: int, scale: float = 1.0) -> str:
    """'5.2 ± 1.4' with a typographic minus (U+2212); the value alone when there is no error."""
    v = _num(x)
    if v is None:
        return ""
    e = _num(err)
    text = f"{v * scale:.{digits}f}" + ("" if e is None else f" ± {e * scale:.{digits}f}")
    return text.replace("-", "−")


def rfactor_text(x: Any) -> str:
    """R-factor: 3 decimals, 4 when it is below 0.01."""
    v = _num(x)
    if v is None:
        return ""
    return f"{v:.3f}" if v >= 0.01 else f"{v:.4f}"


def _cn_cell(row: Dict[str, Any], limit: float, marks: Dict[str, List[str]]) -> str:
    cn = _num(row.get("cn"))
    if cn is None:
        amp = _num(row.get("amp"))
        detail = f"{row.get('sample', '')} {row.get('path', '')}: N·S₀² = {amp:.2f}" if amp is not None else ""
        marks.setdefault(SECTION, [])
        if detail:
            marks[SECTION].append(detail)
        return "—" + SECTION
    if row.get("cn_fixed"):
        return f"{round(cn, 3):g} (fixed)"  # 6.00001 from float round-off prints as 6
    err = _num(row.get("cn_err"))
    text = pm(cn, err, 1)
    if err is not None and err / max(abs(cn), 1e-6) > limit:
        text += DAGGER
        marks.setdefault(DAGGER, [])
    return text


def _ss_cell(row: Dict[str, Any], marks: Dict[str, List[str]]) -> str:
    ss = _num(row.get("ss"))
    if ss is None:
        return ""
    if row.get("ss_at_bound") or row.get("cn_lower_bound"):
        marks.setdefault(DDAGGER, [])
        return f"{ss * 1e3:.1f}{DDAGGER}"  # on a bound the error bar is meaningless: value only
    return pm(ss, row.get("ss_err"), 1, 1e3)


def format_table(
    blocks: Sequence[Dict[str, Any]], cn_rel_err_limit: float = DEFAULT_CN_REL_ERR_LIMIT
) -> Tuple[List[str], List[Tuple[List[str], str]], Dict[str, List[str]]]:
    """Return (header, body, marks).

    ``body`` is a list of (cells, kind), kind = "block" (the edge title, one cell) or "row".
    ``marks`` maps each mark symbol that was used to extra detail strings (§ lists N·S₀²).
    Sample-level cells (sample name, ΔE₀, S₀², R-factor) are written on the first row of a sample
    and again on a later row only when their text differs from what was last written.
    """
    body: List[Tuple[List[str], str]] = []
    marks: Dict[str, List[str]] = {}
    for block in blocks:
        body.append(([str(block.get("title", ""))], "block"))
        prev_sample: Any = object()
        last = {"de0": None, "s02": None, "rf": None}
        for row in block.get("rows", []):
            sample = str(row.get("sample", ""))
            first = sample != prev_sample
            de0 = pm(row.get("de0"), row.get("de0_err"), 1)
            s02 = pm(row.get("s02"), row.get("s02_err"), 2)
            rf = rfactor_text(row.get("rfactor"))
            cells = [
                sample if first else "",
                str(row.get("path", "")),
                _cn_cell(row, cn_rel_err_limit, marks),
                pm(row.get("r"), row.get("r_err"), 3),
                _ss_cell(row, marks),
            ]
            for key, text in (("de0", de0), ("s02", s02), ("rf", rf)):
                if text and (first or text != last[key]):
                    cells.append(text)
                    last[key] = text
                else:
                    cells.append("")
            body.append((cells, "row"))
            prev_sample = sample
    return list(HEADER), body, marks


def _sup(n: Any) -> str:
    return "".join(_SUP.get(c, c) for c in str(n))


def note_definitions(kweights: Sequence[int] = (1, 2, 3)) -> str:
    ks = [str(k) for k in kweights]
    kw = ks[0] if len(ks) == 1 else ", ".join(ks[:-1]) + " and " + ks[-1]
    return (
        "CN, coordination number; R, interatomic distance; σ², Debye–Waller factor; ΔE₀, energy-shift "
        "correction; S₀², amplitude reduction factor; R-factor, misfit of each spectrum (larch definition: "
        f"k weights {kw}, within the fit window)."
    )


def note_windows(
    windows: Sequence[Dict[str, Any]],
    kweights: Sequence[int] = (1, 2, 3),
    window: str = "Hanning",
    dk: float = 1.0,
) -> str:
    """'Fits in R space with k¹, k² and k³ weighting ...: Ni K, k = 3–9.8 Å⁻¹, R = 1.2–3.2 Å; ...'.

    ``windows`` = [{"label": "Ni K", "kmin": 3, "kmax": 9.8, "rmin": 1.2, "rmax": 3.2}, ...]"""
    kws = [_sup(k) for k in kweights]
    kw_text = "k" + (kws[0] if len(kws) == 1 else ", k".join(kws[:-1]) + " and k" + kws[-1])
    parts = [
        f"{w['label']}, k = {w['kmin']:g}–{w['kmax']:g} Å⁻¹, R = {w['rmin']:g}–{w['rmax']:g} Å" for w in windows
    ]
    return (
        f"Fits in R space with {kw_text} weighting simultaneously ({window} window, dk = {dk:g} Å⁻¹): "
        + "; ".join(parts)
        + ". Uncertainties are 1σ standard errors from the fit."
    )


def default_notes(
    windows: Sequence[Dict[str, Any]],
    *,
    kweights: Sequence[int] = (1, 2, 3),
    window: str = "Hanning",
    dk: float = 1.0,
    s02_lines: Sequence[str] = (),
    e0_line: str = "",
    shared_line: str = "",
) -> List[str]:
    """Standard note lines: definitions, windows, then the caller's S₀² / E₀ / shared-parameter lines."""
    notes = [note_definitions(kweights), note_windows(windows, kweights, window, dk)]
    notes += [str(x) for x in s02_lines if str(x).strip()]
    if e0_line:
        notes.append(e0_line)
    if shared_line:
        notes.append(shared_line)
    return notes


def mark_note_lines(
    marks: Dict[str, List[str]],
    cn_rel_err_limit: float = DEFAULT_CN_REL_ERR_LIMIT,
    mark_notes: Optional[Dict[str, str]] = None,
) -> List[str]:
    """One footnote per mark that is used in the table; ``mark_notes`` overrides the default text."""
    over = mark_notes or {}
    lines: List[str] = []
    if DAGGER in marks:
        lines.append(over.get(DAGGER) or f"{DAGGER} Relative uncertainty of CN > {cn_rel_err_limit * 100:.0f} %.")
    if DDAGGER in marks:
        lines.append(
            over.get(DDAGGER)
            or f"{DDAGGER} σ² reached its lower bound; the CN is therefore a lower limit."
        )
    if SECTION in marks:
        detail = "; ".join(marks[SECTION])
        lines.append(
            over.get(SECTION)
            or f"{SECTION} N and S₀² cannot be separated for this path (amplitude N·S₀² only)"
            + (f": {detail}." if detail else ".")
        )
    return lines


def write_sheet(
    ws,
    blocks: Sequence[Dict[str, Any]],
    notes: Sequence[str],
    intro: Optional[str] = None,
    cn_rel_err_limit: float = DEFAULT_CN_REL_ERR_LIMIT,
    mark_notes: Optional[Dict[str, str]] = None,
) -> int:
    """Write the paper table on worksheet ``ws`` (intro in row 1, header in row 3); return the last row.

    Three-line table: thick rule above the header, thin below it, thick below the last row; no fill,
    no frozen panes.  Notes follow two rows below the table, then one footnote per mark used."""
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    header, body, marks = format_table(blocks, cn_rel_err_limit)
    cell = ws.cell(row=1, column=1, value=intro if intro is not None else DEFAULT_INTRO)
    cell.font = Font(bold=True)
    thick, thin = Side(style="medium"), Side(style="thin")
    top = 3
    for j, h in enumerate(header, 1):
        c = ws.cell(row=top, column=j, value=h)
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="left" if j <= 2 else "center")
        c.border = Border(top=thick, bottom=thin)
    for i, (cells, kind) in enumerate(body, top + 1):
        for j, val in enumerate(cells, 1):
            c = ws.cell(row=i, column=j, value=val)
            c.alignment = Alignment(horizontal="left" if j <= 2 else "center")
            if kind == "block":
                c.font = Font(italic=True, bold=True)
    last = top + len(body)
    for j in range(1, len(header) + 1):
        ws.cell(row=last, column=j).border = Border(bottom=thick)
    lines = list(notes) + mark_note_lines(marks, cn_rel_err_limit, mark_notes)
    for i, text in enumerate(lines, last + 2):
        ws.cell(row=i, column=1, value=text)
    for j, w in enumerate(COLUMN_WIDTHS, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    return last + 1 + len(lines)


def check_workbook(path: str) -> List[str]:
    """Problems Excel would 'repair': formulas, or a selection pane without a matching <pane>."""
    problems: List[str] = []
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet")):
            xml = z.read(name).decode("utf-8")
            if "<f>" in xml or "<f " in xml:
                problems.append(f"{name}: contains a formula")
            panes = set(re.findall(r'<pane [^>]*activePane="(\w+)"', xml))
            for sel in re.findall(r'<selection [^>]*pane="(\w+)"', xml):
                if sel not in panes:
                    problems.append(f"{name}: selection pane={sel!r} without a matching <pane>")
    return problems


def write_xlsx(
    path: str,
    blocks: Sequence[Dict[str, Any]],
    notes: Sequence[str],
    intro: Optional[str] = None,
    cn_rel_err_limit: float = DEFAULT_CN_REL_ERR_LIMIT,
    mark_notes: Optional[Dict[str, str]] = None,
) -> str:
    """One-sheet workbook (sheet 'Paper_table').  Read back after saving; any problem raises and no
    file is left behind."""
    from openpyxl import Workbook

    path = str(path)
    tmp = path + ".tmp"
    wb = Workbook()
    ws = wb.active
    ws.title = "Paper_table"
    write_sheet(ws, blocks, notes, intro, cn_rel_err_limit, mark_notes)
    try:
        wb.save(tmp)
        problems = check_workbook(tmp)
        if problems:
            raise ValueError("paper table workbook would trigger Excel repair: " + "; ".join(problems))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


def to_markdown(
    blocks: Sequence[Dict[str, Any]],
    notes: Sequence[str],
    cn_rel_err_limit: float = DEFAULT_CN_REL_ERR_LIMIT,
    mark_notes: Optional[Dict[str, str]] = None,
) -> str:
    """The same table as a Markdown pipe table plus the notes."""
    header, body, marks = format_table(blocks, cn_rel_err_limit)
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for cells, kind in body:
        if kind == "block":
            out.append("| " + f"*{cells[0]}*" + " |" + " |" * (len(header) - 1))
        else:
            out.append("| " + " | ".join(cells) + " |")
    out.append("")
    out += list(notes) + mark_note_lines(marks, cn_rel_err_limit, mark_notes)
    return "\n".join(out) + "\n"
