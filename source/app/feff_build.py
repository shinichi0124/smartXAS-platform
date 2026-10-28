"""Structure library + CIF -> FEFF6 path building for smartLarch Platform.

Qt-free (same rule as wavelet.py): the GUI imports this module, never the other
way round.

Design notes (evidence: docs/PLAN_CifFeff_MultiFit_20261026.md section 2):

* FEFF6 is run as a bare subprocess with stdin=DEVNULL and CREATE_NO_WINDOW.
  larch's feff6l() / FeffRunner do os.chdir(folder) (process-wide!) and never
  pass stdin, which can hang a GUI/worker thread.  We never chdir and never
  call larch's FEFF wrappers.
* The bundled AMCSD database must be opened read_only=True: larch's default
  path issues ALTER TABLE and the official Windows install lives under
  C:/ProgramData (read-only).  Nothing here ever downloads unless the user
  explicitly calls download_full_amcsd().
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

STRUCTURES_SCHEMA = "smartlarch-structures/v1"
SOURCE_SCHEMA = "smartlarch-feff-source/v1"

# Fallback lock used only when fit_engine is unavailable (headless unit tests of
# this module alone).  Inside the app the shared fit_engine._FEFF_LOCK is used,
# so a Model-A rebuild and a structure run can never collide.
_LOCAL_FEFF_LOCK = threading.RLock()

_AMCSD_LOCK = threading.Lock()
_AMCSD_CACHE: Any = None
_AMCSD_ERROR: str = ""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _ascii_name(text: str, fallback: str = "structure") -> str:
    """ASCII identifier safe for a file/folder name."""
    out = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    out = re.sub(r"_+", "_", out)
    return out or fallback


def _feff_lock():
    """The app-wide FEFF lock (fit_engine._FEFF_LOCK) when importable."""
    try:
        from . import fit_engine  # noqa: WPS433 (deliberate lazy import)

        return getattr(fit_engine, "_FEFF_LOCK", _LOCAL_FEFF_LOCK)
    except Exception:
        return _LOCAL_FEFF_LOCK


def _fit_engine_paths() -> Dict[str, Path]:
    from .fit_engine import project_paths

    return project_paths()


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------


@dataclass
class CifHit:
    """One AMCSD search result (display row)."""

    ams_id: int
    mineral: str = ""
    formula: str = ""
    spacegroup: str = ""
    a: float = float("nan")
    citation: str = ""


@dataclass
class SiteInfo:
    """One symmetry-distinct absorber site of a structure."""

    site_index: int  # 1-based, same convention as structure2feffinp
    n_equiv: int = 1
    first_shell: str = ""
    label: str = ""
    partial: bool = False


@dataclass
class StructureEntry:
    """One row of the structure library (Fit page "Structures (CIF)")."""

    key: str = ""
    source: str = "file"  # file | amcsd | ccdc | offline (zip/folder library) | cod
    source_id: str = ""
    cif_path: str = ""
    label: str = ""
    formula: str = ""
    spacegroup: str = ""
    absorber: str = ""
    edge: str = "K"
    site: str = "all"  # "all" or a numeric string
    cluster: float = 6.0
    absorber_as: str = ""
    runs: List[str] = field(default_factory=list)
    status: str = "pending"
    error: str = ""
    marked: bool = False

    def to_dict(self, lib: Optional[Path] = None) -> dict:
        d = asdict(self)
        d["cif_path"] = _store_path(self.cif_path, lib)
        return d

    @staticmethod
    def from_dict(d: dict, lib: Optional[Path] = None) -> "StructureEntry":
        allowed = {f for f in StructureEntry.__dataclass_fields__}  # type: ignore[attr-defined]
        kw = {k: v for k, v in (d or {}).items() if k in allowed}
        known = StructureEntry(**kw)
        known.cif_path = _load_path(known.cif_path, lib)
        if not known.key:
            known.key = _ascii_name(f"{known.label}_{known.site}", "structure")
        return known


@dataclass
class FeffPathInfo:
    """One feffNNNN.dat produced by a structure run (path picker row)."""

    run_dir: str = ""
    feff_file: str = ""
    index: int = 0
    reff: float = float("nan")
    degen: float = float("nan")
    nleg: float = float("nan")
    amp: float = float("nan")
    atoms: str = ""
    scatterers: List[str] = field(default_factory=list)
    absorber: str = ""
    source_label: str = ""

    @property
    def single_scattering(self) -> bool:
        try:
            return int(self.nleg) == 2
        except Exception:
            return False

    def to_dict(self) -> dict:
        return asdict(self)


def _store_path(path: str, lib: Optional[Path]) -> str:
    """Library-internal CIFs are stored relative, everything else absolute."""
    if not path:
        return ""
    p = Path(path)
    if lib is not None:
        try:
            return str(p.resolve().relative_to(Path(lib).resolve()))
        except Exception:
            pass
    return str(p)


def _load_path(path: str, lib: Optional[Path]) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_absolute() and lib is not None:
        return str((Path(lib) / p).resolve())
    return str(p)


# ---------------------------------------------------------------------------
# structure library
# ---------------------------------------------------------------------------


def library_dir(raw: str = "", create: bool = True) -> Path:
    """Structure library root: the configured folder, else data/structures.

    create=False only resolves the path (the window does this at start-up;
    every writer makes its own sub-folder, so nothing is created until used).
    """
    if str(raw or "").strip():
        root = Path(str(raw).strip()).expanduser()
    else:
        root = _fit_engine_paths()["data"] / "structures"
    if create:
        (root / "cif").mkdir(parents=True, exist_ok=True)
        (root / "feff").mkdir(parents=True, exist_ok=True)
    return root


def _structures_json(lib: Path) -> Path:
    return Path(lib) / "structures.json"


class StructureLibraryUnreadable(OSError):
    """structures.json exists but cannot be read (locked, OneDrive offline)."""


def load_structures(lib: Path) -> List[StructureEntry]:
    """Read structures.json; a corrupt file is renamed, never deleted.

    A file that cannot be *read* is not corrupt: it is left alone and
    StructureLibraryUnreadable is raised so the caller does not overwrite it.
    """
    fp = _structures_json(Path(lib))
    if not fp.is_file():
        return []
    try:
        raw = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = None
    except OSError as exc:
        raise StructureLibraryUnreadable(f"{fp}: {exc}") from exc
    try:
        if raw is None:
            raise ValueError("structures.json is not UTF-8")
        data = json.loads(raw)
        entries = data.get("entries") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise ValueError("entries is not a list")
        return [StructureEntry.from_dict(d, Path(lib)) for d in entries if isinstance(d, dict)]
    except Exception:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            fp.replace(fp.with_name(f"structures.json.bad-{stamp}"))
        except OSError:
            pass
        return []


def save_structures(lib: Path, entries: Sequence[StructureEntry]) -> Path:
    """Write structures.json atomically (temp file then replace)."""
    lib = Path(lib)
    lib.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": STRUCTURES_SCHEMA,
        "entries": [e.to_dict(lib) for e in entries],
    }
    fp = _structures_json(lib)
    tmp = fp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(fp))
    return fp


# ---------------------------------------------------------------------------
# AMCSD
# ---------------------------------------------------------------------------


def _amcsd_module():
    import larixite.amcsd as A  # noqa: WPS433

    return A


_AMCSD_FULL_NAME = "amcsd_cif2.db"  # larixite.amcsd.AMCSD_FULL


def full_amcsd_path() -> Path:
    # Only a path check: importing larixite pulls in scipy + sqlalchemy (~1 s)
    # and made the CIF search window and Settings slow to open (v4.11.0).
    # Same rule as larixite.utils.user_folder: <home>/.larch.
    A = sys.modules.get("larixite.amcsd")
    if A is not None:
        return Path(A.user_folder) / A.AMCSD_FULL
    return Path(Path.home(), ".larch").absolute() / _AMCSD_FULL_NAME


def has_full_amcsd() -> bool:
    try:
        return full_amcsd_path().is_file()
    except Exception:
        return False


def reset_amcsd_cache() -> None:
    global _AMCSD_CACHE, _AMCSD_ERROR
    with _AMCSD_LOCK:
        _AMCSD_CACHE = None
        _AMCSD_ERROR = ""


def open_amcsd():
    """Open AMCSD (full if present, else the bundled trimmed one) — never online.

    The trimmed database is opened read_only=True because larch's default path
    runs ALTER TABLE, which fails on a read-only install location.
    """
    global _AMCSD_CACHE, _AMCSD_ERROR
    with _AMCSD_LOCK:
        if _AMCSD_CACHE is not None:
            return _AMCSD_CACHE
        A = _amcsd_module()
        full = full_amcsd_path()
        try:
            if full.is_file():
                _AMCSD_CACHE = A.AMCSD(str(full))
            else:
                _AMCSD_CACHE = A.AMCSD(read_only=True)
            _AMCSD_ERROR = ""
            return _AMCSD_CACHE
        except Exception as exc:
            _AMCSD_ERROR = str(exc)
            raise RuntimeError(f"Cannot open the AMCSD database: {exc}") from exc


def download_full_amcsd(timeout: int = 60) -> Path:
    """Download the ~27 MB full AMCSD database (explicit user action only)."""
    A = _amcsd_module()
    try:
        A.get_amcsd(download_full=True, timeout=timeout)
    except Exception as exc:
        raise RuntimeError(f"AMCSD download failed: {exc}") from exc
    reset_amcsd_cache()
    full = full_amcsd_path()
    if not full.is_file():
        raise RuntimeError(f"AMCSD download did not produce {full}")
    return full


def _citation_of(hit) -> str:
    try:
        pub = hit.publication
        authors = ", ".join(pub.authors or ())
        head = f"{authors}, " if authors else ""
        return (
            f"{head}{pub.journalname} {pub.volume} ({pub.year}) {pub.page_first}"
            f"-{pub.page_last} [AMCSD {hit.ams_id}]"
        )
    except Exception:
        return f"AMCSD {getattr(hit, 'ams_id', '?')}"


def _mineral_of(hit) -> str:
    try:
        name = hit.get_mineralname()
        if name and name not in ("<missing>", "missing"):
            return str(name)
    except Exception:
        pass
    try:
        return str(hit.mineral.name)
    except Exception:
        return ""


def _spacegroup_of(hit) -> str:
    for attr in ("hm_notation", "spacegroup"):
        try:
            val = getattr(hit.spacegroup, attr, None)
            if val:
                return str(val)
        except Exception:
            continue
    try:
        return str(hit.hm_symbol or "")
    except Exception:
        return ""


def _is_element(tok: str) -> bool:
    if not tok or not tok[0].isupper():
        return False
    try:
        from xraydb import atomic_number

        return atomic_number(tok) is not None
    except Exception:
        return False


def search_amcsd(text: str, strict: bool = False, limit: int = 200) -> List[CifHit]:
    """Search AMCSD by mineral name, or by element list ("Ni O")."""
    am = open_amcsd()
    query = str(text or "").strip()
    if not query:
        return []
    tokens = [tok for tok in re.split(r"[\s,;]+", query) if tok]
    hits: List[Any] = []
    if tokens and all(_is_element(tok) for tok in tokens):
        elems = [tok.capitalize() for tok in tokens]
        hits = am.find_cifs(contains_elements=elems, strict_contains=bool(strict))
    else:
        hits = am.find_cifs(mineral_name=query)
    out: List[CifHit] = []
    for hit in hits[: max(0, int(limit))]:
        out.append(
            CifHit(
                ams_id=int(getattr(hit, "ams_id", 0) or 0),
                mineral=_mineral_of(hit),
                formula=str(getattr(hit, "formula", "") or "").replace(" ", ""),
                spacegroup=_spacegroup_of(hit),
                a=float(getattr(hit, "a", float("nan")) or float("nan")),
                citation=_citation_of(hit),
            )
        )
    return out


def cif_text_for_ams_id(ams_id: int) -> str:
    am = open_amcsd()
    hit = am.get_cif(int(ams_id))
    if hit is None:
        raise ValueError(f"AMCSD {ams_id} not found")
    return str(hit.ciftext)


def export_amcsd_cif(ams_id: int, lib: Path) -> Path:
    """Write cif/AMCSD_<id>_<mineral>.cif (reused when unchanged)."""
    am = open_amcsd()
    hit = am.get_cif(int(ams_id))
    if hit is None:
        raise ValueError(f"AMCSD {ams_id} not found")
    text = str(hit.ciftext)
    mineral = _ascii_name(_mineral_of(hit), "")
    name = f"AMCSD_{int(ams_id)}" + (f"_{mineral}" if mineral else "") + ".cif"
    out = Path(lib) / "cif" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file():
        try:
            if out.read_text(encoding="utf-8", errors="replace") == text:
                return out
        except OSError:
            pass
    out.write_text(text, encoding="utf-8")
    return out


def amcsd_citation(ams_id: int) -> str:
    try:
        am = open_amcsd()
        hit = am.get_cif(int(ams_id))
        return _citation_of(hit)
    except Exception:
        return f"AMCSD {ams_id}"


# ---------------------------------------------------------------------------
# CIF reading / structure info / sites
# ---------------------------------------------------------------------------


def read_cif_text(path: str | Path) -> str:
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1")
    except OSError as exc:
        raise ValueError(f"Cannot read {p.name}: {exc}") from exc


def _structure_module():
    from larch.xrd.structure2feff import read_structure, structure2feffinp

    return read_structure, structure2feffinp


def _spacegroup_analyzer(struct):
    try:
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

        return SpacegroupAnalyzer(struct)
    except Exception:
        try:
            from larixite.amcsd_utils import SpacegroupAnalyzer

            return SpacegroupAnalyzer(struct)
        except Exception:
            return None


def structure_info(cif_text: str) -> Dict[str, Any]:
    """Formula / space group / elements of a CIF text; ValueError if unreadable."""
    read_structure, _ = _structure_module()
    try:
        struct = read_structure(str(cif_text))
    except Exception as exc:
        raise ValueError(f"Cannot read this structure: {exc}") from exc
    formula = ""
    elements: List[str] = []
    try:
        formula = str(struct.composition.reduced_formula)
        elements = [str(el.symbol) for el in struct.composition.elements]
    except Exception:
        pass
    sg = ""
    ana = _spacegroup_analyzer(struct)
    if ana is not None:
        # pymatgen exposes the symbol through the symmetry dataset (there is no
        # get_spacegroup_symbol in the versions larch supports).
        try:
            ds = ana.get_symmetry_dataset()
            sg = str(getattr(ds, "international", None) or ds["international"] or "")
        except Exception:
            sg = ""
    return {"formula": formula, "spacegroup": sg, "elements": elements, "n_sites": len(struct.sites)}


def _atoms_block(text: str) -> List[Dict[str, Any]]:
    """Parse the ATOMS section of a feff.inp into rows."""
    rows: List[Dict[str, Any]] = []
    try:
        lines = text.splitlines()
    except Exception:
        return rows
    in_atoms = False
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("ATOMS"):
            in_atoms = True
            continue
        if not in_atoms:
            continue
        if not stripped or stripped.startswith("*"):
            if rows and not stripped:
                break
            continue
        toks = stripped.replace("*", " * ").split()
        if len(toks) < 6:
            continue
        try:
            x, y, z = float(toks[0]), float(toks[1]), float(toks[2])
            ipot = int(float(toks[3]))
            sym = toks[4]
            dist = float(toks[5])
        except (TypeError, ValueError):
            continue
        rows.append({"x": x, "y": y, "z": z, "ipot": ipot, "symbol": sym, "distance": dist})
    return rows


def first_shell_from_inp(text: str, tol: float = 0.02) -> Tuple[str, List[int]]:
    """Nearest-neighbour shell of a feff.inp as "6 O @ 2.084".

    Distances come from the ATOMS block - i.e. exactly what FEFF will see -
    rather than from a second pymatgen neighbour walk.
    """
    rows = _atoms_block(text)
    near = [r for r in rows if r["distance"] > 0.1]
    if not near:
        return "", []
    d1 = min(r["distance"] for r in near)
    shell = [r for r in near if abs(r["distance"] - d1) <= tol]
    if not shell:
        return "", []
    counts: Dict[str, int] = {}
    for r in shell:
        sym = str(r["symbol"]).strip()
        counts[sym] = counts.get(sym, 0) + 1
    parts = [f"{n} {sym}" for sym, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    return f"{' + '.join(parts)} @ {d1:.3f}", sorted({r["ipot"] for r in shell})


def _absorber_site_indices(struct, absorber: str) -> List[int]:
    """0-based indices of every site that contains *absorber*."""
    out: List[int] = []
    for i, site in enumerate(struct.sites):
        try:
            symbols = [el.symbol for el in site.species.elements]
        except Exception:
            symbols = str(site.species_string).replace(" ", "").split(",")
        if absorber in symbols:
            out.append(i)
    return out


def absorber_sites(cif_text: str, absorber: str, max_sites: int = 64) -> List[SiteInfo]:
    """Symmetry-distinct absorber sites, each with its FEFF first shell."""
    read_structure, structure2feffinp = _structure_module()
    try:
        struct = read_structure(str(cif_text))
    except Exception as exc:
        raise ValueError(f"Cannot read this structure: {exc}") from exc
    absorber = str(absorber or "").strip()
    if not absorber:
        raise ValueError("No absorber element given")
    all_idx = _absorber_site_indices(struct, absorber)
    if not all_idx:
        raise ValueError(f"{absorber} is not in this structure")

    classes: List[List[int]] = []
    ana = _spacegroup_analyzer(struct)
    if ana is not None:
        try:
            groups = ana.get_symmetrized_structure().equivalent_indices
            for group in groups:
                members = [int(i) for i in group if int(i) in all_idx]
                if members:
                    classes.append(sorted(members))
        except Exception:
            classes = []
    if not classes:
        # fallback: group by first-shell signature of each absorber site
        signatures: Dict[str, List[int]] = {}
        for idx in all_idx:
            try:
                text = structure2feffinp(
                    str(cif_text), absorber, cluster_size=4.0, site_index=idx + 1,
                    version8=False, rng_seed=1,
                )
                sig = first_shell_from_inp(text)[0]
            except Exception:
                sig = f"?{idx}"
            signatures.setdefault(sig, []).append(idx)
        classes = sorted(signatures.values(), key=lambda g: g[0])

    out: List[SiteInfo] = []
    for members in classes:
        if len(out) >= max(1, int(max_sites)):
            break
        rep = members[0]
        try:
            text = structure2feffinp(
                str(cif_text), absorber, cluster_size=4.0, site_index=rep + 1,
                version8=False, rng_seed=1,
            )
            shell = first_shell_from_inp(text)[0]
        except Exception:
            shell = ""
        try:
            species = str(struct.sites[rep].species_string)
        except Exception:
            species = absorber
        partial = "," in species
        label = f"{rep + 1}: {absorber} \u2014 {shell} (x{len(members)})" if shell else f"{rep + 1}: {absorber} (x{len(members)})"
        out.append(
            SiteInfo(
                site_index=rep + 1,
                n_equiv=len(members),
                first_shell=shell,
                label=label,
                partial=partial,
            )
        )
    return out


# ---------------------------------------------------------------------------
# feff.inp construction
# ---------------------------------------------------------------------------


def build_feff_inp(
    cif_text: str,
    absorber: str,
    edge: str = "K",
    site_index: int = 1,
    cluster_size: float = 6.0,
    absorber_as: str = "",
    source_title: str = "",
) -> str:
    """structure2feffinp wrapper (+ optional absorber-potential substitution)."""
    _, structure2feffinp = _structure_module()
    try:
        text = structure2feffinp(
            str(cif_text),
            str(absorber),
            edge=str(edge or "K"),
            cluster_size=float(cluster_size),
            site_index=int(site_index),
            extra_titles=[source_title] if source_title else None,
            version8=False,
            rng_seed=1,
        )
    except Exception as exc:
        raise ValueError(f"Cannot build the FEFF input: {exc}") from exc
    if isinstance(text, str) and text.lstrip().startswith("#"):
        raise ValueError("Cannot read this structure (structure2feffinp failed)")
    if str(absorber_as or "").strip():
        text = substitute_absorber(text, absorber_as)
    return text


def substitute_absorber(feff_inp_text: str, absorber_as: str) -> str:
    """Swap only the ipot-0 POTENTIALS row (doping / substitution model).

    The absorber's Z is what FEFF uses; atom labels are comments only.  Every
    other line stays byte-identical so the change is auditable.
    """
    from xraydb import atomic_number

    elem = str(absorber_as).strip().capitalize()
    z = atomic_number(elem)
    if z is None:
        raise ValueError(f"Unknown element for absorber substitution: {absorber_as}")
    lines = str(feff_inp_text).splitlines()
    out: List[str] = []
    in_pot = False
    replaced = False
    for line in lines:
        upper = line.strip().upper()
        if upper.startswith("POTENTIALS"):
            in_pot = True
            out.append(line)
            continue
        if in_pot and not replaced:
            toks = line.split()
            if toks and toks[0].lstrip("+-").isdigit() and int(float(toks[0])) == 0:
                out.append(f"   {0:4d}  {int(z):4d}   {elem:s}")
                replaced = True
                continue
        if in_pot and line.strip().upper().startswith("ATOMS"):
            in_pot = False
        out.append(line)
    if not replaced:
        raise ValueError("Could not find the absorber potential (ipot 0) in feff.inp")
    # TITLE lines are a contiguous block at the top; add one more there.
    insert_at = 0
    for i, line in enumerate(out):
        if line.upper().startswith("TITLE "):
            insert_at = i + 1
        elif insert_at and not line.upper().startswith("TITLE "):
            break
    out.insert(insert_at, f"TITLE absorber potential -> {elem} (substitution model)")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# FEFF6 execution
# ---------------------------------------------------------------------------


def feff6l_exe() -> str:
    try:
        from larch.xafs.feffrunner import find_exe

        exe = find_exe("feff6l")
    except Exception:
        exe = None
    if not exe:
        raise RuntimeError("feff6l executable not found in this larch install")
    return str(exe)


def run_feff6l(folder: str | Path, timeout: float = 300) -> Dict[str, Any]:
    """Run feff6l.exe inside *folder*; never chdir, never inherit stdin."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "feff.inp").is_file():
        raise RuntimeError(f"No feff.inp in {folder}")
    exe = feff6l_exe()
    started = datetime.datetime.now()
    with _feff_lock():
        try:
            proc = subprocess.run(
                [exe],
                cwd=str(folder),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            rc = int(proc.returncode)
            raw = proc.stdout or b""
        except subprocess.TimeoutExpired as exc:
            raw = exc.output or b""
            log = _decode(raw)
            (folder / "feffrun_feff6l.log").write_text(log, encoding="utf-8")
            raise RuntimeError(_with_tail(f"FEFF timed out after {timeout:g} s", log)) from exc
        except OSError as exc:
            raise RuntimeError(f"Could not start feff6l: {exc}") from exc
    log = _decode(raw)
    (folder / "feffrun_feff6l.log").write_text(log, encoding="utf-8")
    dats = _feff_dats(folder)
    if rc != 0:
        raise RuntimeError(_with_tail(f"FEFF exited with code {rc}", log))
    if not dats:
        listing = "\n".join(sorted(p.name for p in folder.iterdir()))[:1500]
        raise RuntimeError(
            _with_tail("FEFF produced no feffNNNN.dat files", log + "\n" + listing)
        )
    seconds = (datetime.datetime.now() - started).total_seconds()
    return {"n_paths": len(dats), "seconds": seconds, "log": log}


def _decode(raw) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _with_tail(message: str, log: str, n: int = 30) -> str:
    tail = "\n".join(str(log or "").splitlines()[-n:])
    return f"{message}\n--- FEFF log (tail) ---\n{tail}"


def _feff_dats(folder: Path) -> List[Path]:
    out = [
        p
        for p in Path(folder).glob("feff*.dat")
        if p.name.lower().startswith("feff") and p.suffix.lower() == ".dat" and p.stat().st_size > 200
    ]
    return sorted(out)


# ---------------------------------------------------------------------------
# path metadata
# ---------------------------------------------------------------------------

_FILES_ROW = re.compile(
    r"^\s*(feff\d+\.dat)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+(\d+)\s+([-+0-9.eE]+)"
)
_PATH_HEAD = re.compile(r"^\s*(\d+)\s+(\d+)\s+([-+0-9.eE]+)\s+index,\s*nleg,\s*degeneracy")
_ATOM_ROW = re.compile(r"^\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+(\d+)\s+'([^']*)'")


def _parse_paths_dat(path: Path) -> Dict[int, Dict[str, Any]]:
    """Per-path nleg / degeneracy / reff / absorber / scatterers from paths.dat."""
    out: Dict[int, Dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    i = 0
    while i < len(lines):
        head = _PATH_HEAD.match(lines[i])
        if not head:
            i += 1
            continue
        index = int(head.group(1))
        nleg = int(head.group(2))
        degen = float(head.group(3))
        reff = float("nan")
        m = re.search(r"r=\s*([-+0-9.eE]+)", lines[i])
        if m:
            try:
                reff = float(m.group(1))
            except ValueError:
                pass
        atoms: List[Tuple[int, str]] = []
        j = i + 1
        while j < len(lines) and len(atoms) < nleg:
            row = _ATOM_ROW.match(lines[j])
            if row:
                atoms.append((int(row.group(4)), row.group(5).strip()))
            elif atoms:
                break
            j += 1
        absorber = ""
        scatterers: List[str] = []
        for ipot, label in atoms:
            if ipot == 0:
                absorber = label
            else:
                scatterers.append(label)
        if not absorber and atoms:
            absorber = atoms[-1][1]
        out[index] = {
            "nleg": float(nleg),
            "degen": degen,
            "reff": reff,
            "absorber": absorber,
            "scatterers": scatterers,
        }
        i = j if j > i else i + 1
    return out


def parse_feff_paths(folder: str | Path, source_label: str = "") -> List[FeffPathInfo]:
    """Read files.dat + paths.dat into pickable path rows."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    per_path = _parse_paths_dat(folder / "paths.dat")
    rows: List[FeffPathInfo] = []
    files_dat = folder / "files.dat"
    seen: set = set()
    if files_dat.is_file():
        try:
            text = files_dat.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            m = _FILES_ROW.match(line)
            if not m:
                continue
            name = m.group(1)
            idx = int(re.sub(r"\D", "", name) or 0)
            seen.add(idx)
            meta = per_path.get(idx, {})
            scatterers = list(meta.get("scatterers") or [])
            absorber = str(meta.get("absorber") or "")
            atoms = "-".join([absorber] + scatterers) if absorber else ""
            rows.append(
                FeffPathInfo(
                    run_dir=str(folder),
                    feff_file=str((folder / name).resolve()),
                    index=idx,
                    reff=float(m.group(6)),
                    degen=float(m.group(4)),
                    nleg=float(m.group(5)),
                    amp=float(m.group(3)),
                    atoms=atoms,
                    scatterers=scatterers,
                    absorber=absorber,
                    source_label=source_label,
                )
            )
    # paths missing from files.dat (rare) -> header parser fallback
    for dat in _feff_dats(folder):
        idx = int(re.sub(r"\D", "", dat.name) or 0)
        if idx in seen:
            continue
        header = _dat_header(dat)
        meta = per_path.get(idx, {})
        scatterers = list(meta.get("scatterers") or [])
        absorber = str(meta.get("absorber") or "")
        rows.append(
            FeffPathInfo(
                run_dir=str(folder),
                feff_file=str(dat.resolve()),
                index=idx,
                reff=float(header.get("reff", float("nan"))),
                degen=float(header.get("degen", float("nan"))),
                nleg=float(header.get("nleg", float("nan"))),
                amp=float("nan"),
                atoms=(
                    "-".join([absorber] + scatterers)
                    if absorber
                    else ("?" if not scatterers else "-".join(scatterers))
                ),
                scatterers=scatterers,
                absorber=absorber,
                source_label=source_label,
            )
        )
    rows.sort(key=lambda r: (r.index, r.reff))
    return rows


def _dat_header(path: Path) -> Dict[str, float]:
    try:
        from .fit_engine import _parse_feff_dat_header

        return _parse_feff_dat_header(path)
    except Exception:
        return {"reff": float("nan"), "degen": float("nan"), "nleg": float("nan")}


# ---------------------------------------------------------------------------
# structure runs
# ---------------------------------------------------------------------------


def run_dir_name(entry: StructureEntry, site_index: int) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    label = _ascii_name(entry.label or entry.source_id, "structure")
    absorber = _ascii_name(entry.absorber, "X")
    sub = f"_as{_ascii_name(entry.absorber_as, '')}" if str(entry.absorber_as or "").strip() else ""
    return f"{label}_{absorber}{sub}_s{int(site_index)}_{stamp}"


def _unique_dir(parent: Path, name: str) -> Path:
    target = parent / name
    if not target.exists():
        return target
    for i in range(2, 1000):
        cand = parent / f"{name}_{i}"
        if not cand.exists():
            return cand
    return parent / f"{name}_{datetime.datetime.now().strftime('%f')}"


def write_provenance(folder: str | Path, meta: Dict[str, Any]) -> Path:
    """Write source.json (what this FEFF run was built from)."""
    payload = {"schema": SOURCE_SCHEMA}
    payload.update(dict(meta or {}))
    payload.setdefault("created", datetime.datetime.now().isoformat(timespec="seconds"))
    try:
        import larch

        payload.setdefault("larch_version", str(getattr(larch, "__version__", "?")))
    except Exception:
        payload.setdefault("larch_version", "?")
    fp = Path(folder) / "source.json"
    fp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return fp


def run_structure(entry: StructureEntry, lib: Path, progress=None) -> List[str]:
    """Build + run FEFF6 for one structure entry (one run dir per site)."""
    if not str(entry.absorber or "").strip():
        raise ValueError("No absorber element given")
    cif_text = read_cif_text(entry.cif_path)
    sites: List[int] = []
    site_note = ""
    if str(entry.site or "all").strip().lower() in ("", "all"):
        infos = absorber_sites(cif_text, entry.absorber)
        sites = [info.site_index for info in infos]
        if len(sites) > 1:
            site_note = f"sites {', '.join(str(s) for s in sites)}"
    else:
        sites = [int(float(entry.site))]
        # structure2feffinp puts the absorber on whatever site it is given;
        # a site of another element would silently give a wrong model.
        read_structure, _ = _structure_module()
        try:
            struct = read_structure(str(cif_text))
        except Exception as exc:
            raise ValueError(f"Cannot read this structure: {exc}") from exc
        if sites[0] - 1 not in _absorber_site_indices(struct, str(entry.absorber).strip()):
            raise ValueError(
                f"Site {sites[0]} does not hold {entry.absorber}; choose one of its sites"
            )
    if not sites:
        raise ValueError("No absorber site to run")
    feff_root = Path(lib) / "feff"
    feff_root.mkdir(parents=True, exist_ok=True)
    total = len(sites)
    made: List[str] = []
    try:
        info_map = {info.site_index: info for info in absorber_sites(cif_text, entry.absorber)}
    except Exception:
        info_map = {}
    for i, site_index in enumerate(sites):
        if progress is not None:
            try:
                progress(i, total, f"{entry.label} s{site_index}")
            except Exception:
                pass
        text = build_feff_inp(
            cif_text,
            entry.absorber,
            edge=entry.edge,
            site_index=int(site_index),
            cluster_size=float(entry.cluster or 6.0),
            absorber_as=entry.absorber_as,
            source_title=f"{entry.label} {entry.absorber} {entry.edge} site {site_index}",
        )
        folder = _unique_dir(feff_root, run_dir_name(entry, site_index))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "feff.inp").write_text(text, encoding="utf-8")
        try:
            shutil.copyfile(entry.cif_path, folder / "structure.cif")
        except OSError:
            pass
        first_shell = first_shell_from_inp(text)[0]
        run_feff6l(folder)
        info = info_map.get(int(site_index))
        write_provenance(
            folder,
            {
                "source": entry.source,
                "source_id": entry.source_id,
                "label": entry.label,
                "formula": entry.formula,
                "spacegroup": entry.spacegroup,
                "citation": (
                    amcsd_citation(int(entry.source_id))
                    if str(entry.source).lower() == "amcsd" and str(entry.source_id).isdigit()
                    else ""
                ),
                "cif_file": Path(entry.cif_path).name,
                "absorber": entry.absorber,
                "absorber_as": entry.absorber_as,
                "edge": entry.edge,
                "site_index": int(site_index),
                "first_shell": first_shell,
                "partial": bool(info.partial) if info is not None else False,
                "n_equiv": int(info.n_equiv) if info is not None else 0,
                "cluster_size": float(entry.cluster or 6.0),
                "site_note": site_note,
            },
        )
        made.append(str(folder))
    if progress is not None:
        try:
            progress(total, total, entry.label)
        except Exception:
            pass
    return made


def structure_models_for(paths: Sequence[Any]) -> List[dict]:
    """Read source.json beside each used FEFF file (one per run dir)."""
    out: List[dict] = []
    seen: set = set()
    for pd in paths or []:
        feff_file = str(pd if isinstance(pd, str) else getattr(pd, "feff_file", "") or "")
        if not feff_file:
            continue
        folder = Path(feff_file).parent
        key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        src = folder / "source.json"
        if src.is_file():
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if isinstance(data, dict):
                cleaned = {
                    k: v
                    for k, v in data.items()
                    if not str(k).lower().endswith("_path")
                }
                if str(cleaned.get("source", "")).lower() in ("ccdc", "offline"):
                    # A CSD entry is licensed: never send its CIF content or
                    # coordinates anywhere; the identifiers are enough context
                    # (v4.10.0, plan section 14.4).  An offline-library CIF is
                    # treated the same way: it is usually a CSD export (v4.11.0).
                    cleaned = {
                        key: cleaned.get(key)
                        for key in (
                            "source",
                            "source_id",
                            "label",
                            "formula",
                            "spacegroup",
                            "absorber",
                            "edge",
                            "site_index",
                            "first_shell",
                        )
                    }
                cleaned.setdefault("source", "structure run")
                cleaned["file"] = Path(feff_file).name
                out.append(cleaned)
                continue
        out.append({"source": "user feff file", "file": Path(feff_file).name})
    return out


# ---------------------------------------------------------------------------
# starting model (single scattering, N free)
# ---------------------------------------------------------------------------


def n_independent(windows) -> float:
    try:
        kmin = float(getattr(windows, "kmin"))
        kmax = float(getattr(windows, "kmax"))
        rmin = float(getattr(windows, "rmin"))
        rmax = float(getattr(windows, "rmax"))
    except Exception:
        return float("nan")
    import math

    return 1.0 + 2.0 * (rmax - rmin) * (kmax - kmin) / math.pi


def merge_shells(paths: Sequence[FeffPathInfo], tol: float = 0.05) -> List[dict]:
    """Merge single-scattering paths into shells (same run dir + scatterer)."""
    groups: Dict[Tuple[str, str], List[FeffPathInfo]] = {}
    for p in paths:
        scatterer = (p.scatterers[0] if p.scatterers else "") or ""
        groups.setdefault((str(p.run_dir), scatterer), []).append(p)
    shells: List[dict] = []
    for (run_dir, scatterer), plist in groups.items():
        plist = sorted(plist, key=lambda p: p.reff)
        current: List[FeffPathInfo] = []
        for p in plist:
            if not current:
                current = [p]
                continue
            if abs(float(p.reff) - float(current[0].reff)) <= tol:
                current.append(p)
            else:
                shells.append(_shell_from(current, run_dir, scatterer))
                current = [p]
        if current:
            shells.append(_shell_from(current, run_dir, scatterer))
    # Shell order follows distance (physical), not the element symbol: the tag
    # numbering then also follows distance for each absorber.
    shells.sort(key=lambda s: (s["absorber"], s["reff"], s["scatterer"]))
    return shells


def _shell_from(plist: List[FeffPathInfo], run_dir: str, scatterer: str) -> dict:
    first = plist[0]
    degen = 0.0
    for p in plist:
        try:
            degen += float(p.degen)
        except Exception:
            continue
    absorber = str(first.absorber or "")
    return {
        "absorber": absorber,
        "scatterer": scatterer,
        "reff": float(first.reff),
        "n0": degen,
        "run_dir": run_dir,
        "feff_file": first.feff_file,
        "sources": [Path(p.feff_file).name for p in plist],
    }


def _shell_tags(shells: Sequence[dict]) -> List[str]:
    """<X><scat><n>, numbered per (absorber, scatterer) across structures."""
    counters: Dict[Tuple[str, str], int] = {}
    tags: List[str] = []
    for shell in shells:
        key = (str(shell["absorber"]), str(shell["scatterer"]))
        counters[key] = counters.get(key, 0) + 1
        tags.append(f"{key[0]}{key[1]}{counters[key]}")
    return tags


def starting_model(
    paths: Sequence[FeffPathInfo],
    s02: float,
    windows,
    per_dataset: str = "",
) -> Tuple[List[Any], List[Any], dict]:
    """GDS + path rows for "fit the paths I picked" (N free, generic model).

    Every shell gets its own sigma^2; per-absorber s02 is fixed and e0 shared.
    v5.1.0, within one structure run (absorber + run directory):
      * only the nearest shell gets its own dR; further shells follow a
        lattice expansion alpha*reff (as in the single-phase model);
      * the first shell of each scatterer gets a free N; further shells of
        the same scatterer keep its CN ratio, N_i = n_first * N0_i / N0_first.
    A weak outer shell with its own N, dR and sigma^2 is not determined by the
    data (Pt L3 demo: N2 = 3.7 +/- 5.8 with dR2 and sigma2 at their bounds).
    Raises ValueError("multiple-scattering") for nleg != 2 and
    ValueError("budget") when the data cannot support the model.
    """
    from .fit_engine import GDSParam, PathDef

    paths = list(paths or [])
    if not paths:
        raise ValueError("No paths given")
    if any(not p.single_scattering for p in paths):
        raise ValueError("multiple-scattering")
    shells = merge_shells(paths)
    tags = _shell_tags(shells)

    absorbers: List[str] = []
    for shell in shells:
        if shell["absorber"] not in absorbers:
            absorbers.append(shell["absorber"])

    gds: List[Any] = []
    for x in absorbers:
        gds.append(GDSParam(f"s02_{x}", float(s02), "fix"))
        gds.append(GDSParam(f"e0_{x}", 0.0, "vary", min=-15.0, max=15.0))
    # v4.8.0: per_dataset="{ds}" makes N and dR one parameter per dataset (a
    # joint-fit template); sigma^2 / S0^2 / e0 stay shared.
    suffix = f"_{per_dataset}" if str(per_dataset or "").strip() else ""
    # nearest shell of each structure run owns dR; the run's alpha is added
    # once it has a second shell (shells are sorted by absorber, then Reff)
    lead_tag: Dict[Tuple[str, str], str] = {}
    deltar_of: Dict[str, str] = {}
    # first shell of each scatterer in a run owns N; later ones keep its ratio
    n_lead: Dict[Tuple[str, str, str], Tuple[str, float]] = {}
    amp_of: Dict[str, str] = {}
    tied_to: Dict[str, str] = {}
    for shell, tag in zip(shells, tags):
        key = (str(shell["absorber"]), str(shell["run_dir"]))
        x = str(shell["absorber"])
        n0 = float(shell["n0"]) if float(shell["n0"]) == float(shell["n0"]) else 1.0
        nkey = key + (str(shell["scatterer"]),)
        if nkey not in n_lead or n_lead[nkey][1] <= 0:
            n_hi = max(2.0 * n0, n0 + 4.0)
            gds.append(GDSParam(f"n_{tag}{suffix}", n0, "vary", min=0.0, max=n_hi))
            n_lead[nkey] = (tag, n0)
            amp_of[tag] = f"s02_{x}*n_{tag}{suffix}"
        else:
            lead, lead_n0 = n_lead[nkey]
            amp_of[tag] = f"s02_{x}*n_{lead}{suffix}*{n0 / lead_n0:.6g}"
            tied_to[tag] = lead
        if key not in lead_tag:
            lead_tag[key] = tag
            gds.append(GDSParam(f"dr_{tag}{suffix}", 0.0, "vary", min=-0.3, max=0.3))
            deltar_of[tag] = f"dr_{tag}{suffix}"
        else:
            alpha = f"alpha_{lead_tag[key]}{suffix}"
            if not any(g.name == alpha for g in gds):
                gds.append(GDSParam(alpha, 0.0, "vary", min=-0.05, max=0.05))
            deltar_of[tag] = f"{alpha}*reff"
        gds.append(GDSParam(f"ss_{tag}", 0.005, "vary", min=0.001, max=0.03))

    pathdefs: List[Any] = []
    for shell, tag in zip(shells, tags):
        x = shell["absorber"]
        pathdefs.append(
            PathDef(
                label=f"{x}-{shell['scatterer']} {float(shell['reff']):.2f}",
                feff_file=str(shell["feff_file"]),
                use=True,
                s02=amp_of[tag],
                e0=f"e0_{x}",
                deltar=deltar_of[tag],
                sigma2=f"ss_{tag}",
                degen=1.0,
                elem=x,
            )
        )

    nvar = sum(1 for g in gds if g.kind == "vary")
    nidp = n_independent(windows)
    if nidp == nidp and nvar >= nidp:
        raise ValueError("budget")
    report = {
        "shells": [
            {
                "tag": tag,
                "absorber": shell["absorber"],
                "scatterer": shell["scatterer"],
                "reff": float(shell["reff"]),
                "n0": float(shell["n0"]),
                "sources": list(shell["sources"]),
                "n_tied_to": tied_to.get(tag, ""),
            }
            for shell, tag in zip(shells, tags)
        ],
        "nvar": nvar,
        "nidp": float(nidp),
        "warn": bool(nidp == nidp and nvar > (2.0 / 3.0) * nidp),
    }
    return gds, pathdefs, report


# ---------------------------------------------------------------------------
# phase models (v4.9.0): single phase (N fixed) and phase fractions
# ---------------------------------------------------------------------------


def _read_source(folder: str | Path) -> dict:
    fp = Path(folder) / "source.json"
    if not fp.is_file():
        return {}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def site_weights(run_dirs: Sequence[str]) -> Tuple[Dict[str, float], str]:
    """Relative weight of each run directory's absorber site.

    w = n_equiv / sum(n_equiv over the sites actually used), so a spinel with
    8 tetrahedral and 16 octahedral sites gives 1/3 and 2/3.  A single site
    always weighs 1.  Old run directories without n_equiv are recovered from
    their structure.cif; if even that fails the sites are weighted equally and
    the note says so.
    """
    dirs = [str(d) for d in run_dirs]
    if not dirs:
        return {}, ""
    eq: Dict[str, int] = {}
    for folder in dirs:
        meta = _read_source(folder)
        n = 0
        try:
            n = int(meta.get("n_equiv") or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            try:
                text = read_cif_text(Path(folder) / "structure.cif")
                absorber = str(meta.get("absorber") or "")
                site = int(meta.get("site_index") or 0)
                for info in absorber_sites(text, absorber):
                    if int(info.site_index) == site:
                        n = int(info.n_equiv)
                        break
            except Exception:
                n = 0
        eq[folder] = max(0, n)
    total = sum(eq.values())
    if total <= 0:
        w = 1.0 / len(dirs)
        return {d: w for d in dirs}, "equal weights (no site multiplicity available)"
    weights = {d: eq[d] / total for d in dirs}
    note = ""
    if len(dirs) == 1:
        meta = _read_source(dirs[0])
        try:
            site = int(meta.get("site_index") or 0)
        except (TypeError, ValueError):
            site = 0
        note = f"only site s{site}" if site else "single site"
    return weights, note


def phase_tag(entry: StructureEntry, taken: Sequence[str] = ()) -> str:
    """Dataset-style ASCII tag for one phase (same rules as fit_engine)."""
    from .fit_engine import dataset_tag

    return dataset_tag(entry.label or entry.source_id, taken)


def _in_window(p: "FeffPathInfo", windows) -> bool:
    """quick_marks' rule for the model builders: Reff <= rmax + 0.5 and amp >= 10 %.

    An unknown amplitude (no files.dat) is not a reason to drop the path.
    """
    try:
        rmax = float(getattr(windows, "rmax"))
    except (AttributeError, TypeError, ValueError):
        return True
    try:
        if float(p.reff) > rmax + 0.5:
            return False
        amp = float(p.amp)
    except (TypeError, ValueError):
        return False
    return not (amp == amp and amp < 10.0)


def _window_ss(paths: Sequence["FeffPathInfo"], windows) -> List["FeffPathInfo"]:
    """Single-scattering paths inside the fit window (plan §13.3 general rule)."""
    ss = [p for p in paths if p.single_scattering]
    if not ss:
        raise ValueError("No single-scattering paths")
    kept = [p for p in ss if _in_window(p, windows)]
    if not kept:
        raise ValueError("No single-scattering paths inside the R window")
    return kept


def _shells_with_weights(entry_paths: Sequence["FeffPathInfo"]) -> Tuple[List[dict], str]:
    shells = merge_shells(entry_paths)
    weights, note = site_weights(sorted({s["run_dir"] for s in shells}))
    for shell in shells:
        shell["weight"] = float(weights.get(str(shell["run_dir"]), 1.0))
    return shells, note


def single_phase_model(
    entry_paths: Sequence["FeffPathInfo"],
    s02: float,
    s02_free: bool,
    windows,
    include_ms: bool = False,
    cn_free: bool = False,
) -> Tuple[List[Any], List[Any], dict]:
    """Single-phase model: N = crystallographic value x site weight (v4.9.0).

    The first shell gets its own deltaR and every further shell uses
    alpha*reff (a lattice expansion).  When the variable count reaches N_idp
    the 3rd and later shells share sigma^2; if that is still too many the
    model is refused.

    Amplitude (v5.1.0) — exactly one of three choices:
      * s02_free: S0^2 is fitted with N at the bulk value (a standard);
      * cn_free: S0^2 stays at the given value and one CN factor cnr_<X>
        scales every N (N_i = cnr * N0_i): the fitted coordination number of
        a sample (nanoparticle / disorder), reported as CN = cnr * N0;
      * neither: the whole amplitude is fixed (bulk N, given S0^2).
    S0^2 and the CN factor multiply each other, so both free is refused.
    """
    from .fit_engine import GDSParam, PathDef

    paths = list(entry_paths or [])
    if not paths:
        raise ValueError("No paths given")
    if s02_free and cn_free:
        raise ValueError("S0^2 and the CN factor cannot both be fitted (fully correlated)")
    shells, site_note = _shells_with_weights(_window_ss(paths, windows))
    absorber = str(shells[0]["absorber"])
    x = absorber or "X"
    nidp = n_independent(windows)
    cnr = f"cnr_{x}"
    amp_expr = f"s02_{x}*{cnr}" if cn_free else f"s02_{x}"
    # MS rows carry the same site weight as the SS shells of their run.
    weights = {str(s["run_dir"]): float(s.get("weight", 1.0)) for s in shells}

    ms_rows: List[dict] = []
    if include_ms:
        seen_files = {s["feff_file"] for s in shells}
        for p in paths:
            if p.single_scattering or not _in_window(p, windows):
                continue
            if p.feff_file in seen_files:
                continue
            ms_rows.append(
                {
                    "absorber": x,
                    "scatterer": "-".join(p.scatterers) or "MS",
                    "reff": float(p.reff),
                    "n0": float(p.degen),
                    "weight": float(weights.get(str(p.run_dir), 1.0)),
                    "run_dir": p.run_dir,
                    "feff_file": p.feff_file,
                    "sources": [Path(p.feff_file).name],
                    "ms": True,
                }
            )

    tags = _shell_tags(shells)
    dr1 = f"dr1_{x}"
    alpha = f"alpha_{x}"
    far = f"ss_far_{x}"
    ms_ss = f"ss_ms_{x}"

    def _assemble(shared_far: bool) -> Tuple[List[Any], List[Any], int]:
        gds: List[Any] = [
            GDSParam(
                f"s02_{x}",
                float(s02),
                "vary" if s02_free else "fix",
                min=(0.5 if s02_free else None),
                max=(1.3 if s02_free else None),
            ),
            GDSParam(f"e0_{x}", 0.0, "vary", min=-15.0, max=15.0),
            GDSParam(dr1, 0.0, "vary", min=-0.1, max=0.1),
        ]
        if cn_free:
            gds.append(GDSParam(cnr, 1.0, "vary", min=0.1, max=1.5))
        if len(shells) > 1 or ms_rows:
            gds.append(GDSParam(alpha, 0.0, "vary", min=-0.05, max=0.05))
        pathdefs: List[Any] = []
        far_added = False
        for i, (shell, tag) in enumerate(zip(shells, tags)):
            if i == 0:
                deltar = dr1
            else:
                deltar = f"{alpha}*reff"
            if shared_far and i >= 2:
                ss_name = far
                if not far_added:
                    gds.append(GDSParam(far, 0.006, "vary", min=0.001, max=0.03))
                    far_added = True
            else:
                ss_name = f"ss_{tag}"
                gds.append(GDSParam(ss_name, 0.005, "vary", min=0.001, max=0.03))
            pathdefs.append(
                PathDef(
                    label=f"{x}-{shell['scatterer']} {float(shell['reff']):.2f}",
                    feff_file=str(shell["feff_file"]),
                    use=True,
                    s02=amp_expr,
                    e0=f"e0_{x}",
                    deltar=deltar,
                    sigma2=ss_name,
                    degen=float(shell["n0"]) * float(shell.get("weight", 1.0)),
                    elem=x,
                )
            )
        if ms_rows:
            gds.append(GDSParam(ms_ss, 0.01, "vary", min=0.001, max=0.05))
            for row in ms_rows:
                pathdefs.append(
                    PathDef(
                        label=f"{x}-{row['scatterer']} {row['reff']:.2f}",
                        feff_file=str(row["feff_file"]),
                        use=True,
                        s02=amp_expr,
                        e0=f"e0_{x}",
                        deltar=f"{alpha}*reff",
                        sigma2=ms_ss,
                        degen=float(row["n0"]) * float(row.get("weight", 1.0)),
                        elem=x,
                    )
                )
        nvar = sum(1 for g in gds if g.kind == "vary")
        return gds, pathdefs, nvar

    gds, pathdefs, nvar = _assemble(False)
    if nidp == nidp and nvar >= nidp:
        gds, pathdefs, nvar = _assemble(True)
        if nvar >= nidp:
            raise ValueError("budget")
    report = {
        "mode": "single",
        "cn_free": bool(cn_free),
        "s02_free": bool(s02_free),
        "shells": [
            {
                "tag": tag,
                "absorber": shell["absorber"],
                "scatterer": shell["scatterer"],
                "reff": float(shell["reff"]),
                "n0": float(shell["n0"]),
                "weight": float(shell.get("weight", 1.0)),
                "degen": float(shell["n0"]) * float(shell.get("weight", 1.0)),
                "sources": list(shell["sources"]),
            }
            for shell, tag in zip(shells, tags)
        ],
        "ms_shells": [
            {
                "tag": f"{x}-{row['scatterer']} {row['reff']:.2f}",
                "reff": row["reff"],
                "degen": float(row["n0"]) * float(row.get("weight", 1.0)),
            }
            for row in ms_rows
        ],
        "nvar": int(nvar),
        "nidp": float(nidp),
        "warn": bool(nidp == nidp and nvar > (2.0 / 3.0) * nidp),
        "site_note": site_note,
    }
    return gds, pathdefs, report


def phase_fraction_model(
    phases: Sequence[Tuple[str, Sequence["FeffPathInfo"]]],
    s02: float,
    windows,
    cn_free: bool = False,
) -> Tuple[List[Any], List[Any], dict]:
    """Mixed-phase model: N fixed per phase, the phase fractions are fitted.

    cn_free (v5.1.0): one CN factor cnr_<X> per absorber scales every phase's
    N together (reduced coordination of a nanoparticle / disordered sample);
    the fractions still sum to 1, so they keep meaning "share of absorbers".

    phases = [(tag, paths), ...]; the first entry is the main phase, whose
    fraction is the expression 1 - x_<p2> - ...  Each phase keeps its own
    deltaR expansion (the first shell shifts, the rest follow reff) and its own
    sigma^2 (first shell vs further shells).  Multiple scattering is not used:
    inside the window it is weak, outside it cannot be separated.
    """
    from .fit_engine import GDSParam, PathDef

    phase_list = [(str(tag), list(paths or [])) for tag, paths in (phases or [])]
    if not phase_list:
        raise ValueError("No phases given")
    prepared = []
    absorbers: List[str] = []
    for tag, paths in phase_list:
        shells, site_note = _shells_with_weights(_window_ss(paths, windows))
        absorber = str(shells[0]["absorber"])
        if absorber not in absorbers:
            absorbers.append(absorber)
        prepared.append({"tag": tag, "shells": shells, "absorber": absorber, "note": site_note})

    gds: List[Any] = [GDSParam(f"s02_{a}", float(s02), "fix") for a in absorbers]
    for a in absorbers:
        gds.append(GDSParam(f"e0_{a}", 0.0, "vary", min=-15.0, max=15.0))
        if cn_free:
            gds.append(GDSParam(f"cnr_{a}", 1.0, "vary", min=0.1, max=1.5))

    main_tag = prepared[0]["tag"]
    if len(prepared) > 1:
        expr = "1 - " + " - ".join(f"x_{p['tag']}" for p in prepared[1:])
        gds.append(GDSParam(f"x_{main_tag}", 0.0, "expr", expr=expr))
        for p in prepared[1:]:
            gds.append(GDSParam(f"x_{p['tag']}", 0.2, "vary", min=0.0, max=1.0))
    else:
        gds.append(GDSParam(f"x_{main_tag}", 1.0, "fix"))

    pathdefs: List[Any] = []
    for p in prepared:
        tag = p["tag"]
        shells = p["shells"]
        dr1 = f"dr1_{tag}"
        gds.append(GDSParam(dr1, 0.0, "vary", min=-0.1, max=0.1))
        gds.append(GDSParam(f"ss1_{tag}", 0.005, "vary", min=0.001, max=0.03))
        if len(shells) > 1:
            gds.append(GDSParam(f"ss2_{tag}", 0.008, "vary", min=0.001, max=0.03))
        reff1 = float(shells[0]["reff"])
        for i, shell in enumerate(shells):
            if i == 0:
                deltar = dr1
                ss_name = f"ss1_{tag}"
            else:
                deltar = f"{dr1}*reff/{reff1:.4f}"
                ss_name = f"ss2_{tag}"
            pathdefs.append(
                PathDef(
                    label=f"{p['absorber']}-{shell['scatterer']} {float(shell['reff']):.2f}",
                    feff_file=str(shell["feff_file"]),
                    use=True,
                    s02=(
                        f"s02_{p['absorber']}*x_{tag}*cnr_{p['absorber']}"
                        if cn_free
                        else f"s02_{p['absorber']}*x_{tag}"
                    ),
                    e0=f"e0_{p['absorber']}",
                    deltar=deltar,
                    sigma2=ss_name,
                    degen=float(shell["n0"]) * float(shell.get("weight", 1.0)),
                    elem=p["absorber"],
                )
            )
    nvar = sum(1 for g in gds if g.kind == "vary")
    nidp = n_independent(windows)
    if nidp == nidp and nvar >= nidp:
        raise ValueError("budget")
    report = {
        "mode": "mixed",
        "cn_free": bool(cn_free),
        "phases": [
            {
                "tag": p["tag"],
                "absorber": p["absorber"],
                "shells": [
                    {
                        "scatterer": s["scatterer"],
                        "reff": float(s["reff"]),
                        "n0": float(s["n0"]),
                        "weight": float(s.get("weight", 1.0)),
                        "degen": float(s["n0"]) * float(s.get("weight", 1.0)),
                    }
                    for s in p["shells"]
                ],
                "site_note": p["note"],
            }
            for p in prepared
        ],
        "nvar": int(nvar),
        "nidp": float(nidp),
        "warn": bool(nidp == nidp and nvar > (2.0 / 3.0) * nidp),
        "site_note": " ".join(p["note"] for p in prepared if p["note"]).strip(),
    }
    return gds, pathdefs, report


def quick_marks(paths: Sequence[FeffPathInfo], rmax: float) -> List[bool]:
    """Default tick rule for Quick fit: SS only, in-window, non-tiny amplitude."""
    out: List[bool] = []
    for p in paths or []:
        try:
            ok = (
                int(p.nleg) == 2
                and float(p.reff) <= float(rmax) + 0.5
                and float(p.amp) >= 10.0
            )
        except Exception:
            ok = False
        out.append(bool(ok))
    return out
