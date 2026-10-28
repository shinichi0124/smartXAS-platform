"""Offline CIF library, COD online search and structure comparison — Qt-free (v4.11.0).

* Offline library: a zip archive (or a folder) of CIF files.  The first search
  builds an index of every CIF's header (id, names, formula, space group, cell,
  year, R factor); the index is cached as JSON next to the structure library and
  rebuilt when the archive changes.  Searching the index needs no network.
* CSD pack: a folder with ``CIF/<Letter>.zip`` (``<REFCODE>.cif`` members) and
  ``CSD_index/csd_index.sqlite`` (one row per CSD entry, exported once from the
  licensed CSD).  It is searched through its own SQLite index — nothing is
  built — and a CIF is read from the zip of its refcode's first letter.
* COD (Crystallography Open Database): the public REST interface
  ``https://www.crystallography.net/cod/result`` (format=json / count) and
  ``https://www.crystallography.net/cod/<id>.cif``.  Called only when the user
  ticks "online search".
* compare_cifs: two CIFs side by side — formula, space group, cell, volume,
  year, R factor — plus the atomic positions of B against A (nearest site of the
  same element under periodic boundaries, in A's cell).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

INDEX_SCHEMA = "smartlarch-cif-index/v1"
COD_BASE = "https://www.crystallography.net/cod"
COD_MAX_RESULTS = 2000  # above this the user is asked to narrow the query
_MAX_CIF_BYTES = 20 * 1024 * 1024
_SITE_MATCH_A = 0.5  # Å: farther than this = "no counterpart"

_ID_TAGS = (
    "_database_code_csd",
    "_cod_database_code",
    "_database_code_icsd",
    "_database_code_amcsd",
)
_NAME_TAGS = ("_chemical_name_common", "_chemical_name_mineral", "_chemical_name_systematic")
_SG_TAGS = (
    "_space_group_name_h-m_alt",
    "_symmetry_space_group_name_h-m",
    "_space_group_name_hall",
    "_symmetry_space_group_name_hall",
)
_NUM_TAGS = {
    "a": "_cell_length_a",
    "b": "_cell_length_b",
    "c": "_cell_length_c",
    "alpha": "_cell_angle_alpha",
    "beta": "_cell_angle_beta",
    "gamma": "_cell_angle_gamma",
    "volume": "_cell_volume",
}
_R_TAGS = ("_refine_ls_r_factor_gt", "_refine_ls_r_factor_obs", "_refine_ls_r_factor_all")
_YEAR_TAGS = ("_journal_year", "_citation_year")
_ELEMENT_RE = re.compile(r"([A-Z][a-z]?)")


class IndexCancelled(Exception):
    """The user cancelled while the offline index was being built."""


class TooManyResults(ValueError):
    """COD matched more entries than COD_MAX_RESULTS."""

    def __init__(self, count: int):
        super().__init__(f"too many results: {count}")
        self.count = int(count)


# ---------------------------------------------------------------------------
# CIF header
# ---------------------------------------------------------------------------


def _clean(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value.strip()


def _number(value: Any) -> float:
    """CIF number: '4.1684(3)' -> 4.1684; '?' / '.' / junk -> NaN."""
    text = re.sub(r"\(.*?\)", "", str(value or "")).strip()
    try:
        return float(text)
    except ValueError:
        return float("nan")


def _tag_values(text: str) -> Dict[str, str]:
    """First value of every simple tag in the first data block (loops skipped)."""
    out: Dict[str, str] = {}
    block = ""
    lines = str(text or "").splitlines()
    i = 0
    in_loop = False
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        low = line.lower()
        if low.startswith("data_"):
            if block:
                break  # only the first block
            block = line[5:].strip()
            out["__block__"] = block
            i += 1
            continue
        if low.startswith("loop_"):
            in_loop = True
            i += 1
            continue
        if line.startswith("_"):
            parts = line.split(None, 1)
            tag = parts[0].lower()
            if in_loop and len(parts) == 1:
                i += 1  # a loop header column
                continue
            in_loop = False
            if len(parts) == 2:
                value = parts[1]
            else:
                value = ""
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and lines[j].startswith(";"):
                    chunk = [lines[j][1:]]
                    j += 1
                    while j < len(lines) and not lines[j].startswith(";"):
                        chunk.append(lines[j])
                        j += 1
                    value = " ".join(s.strip() for s in chunk if s.strip())
                    i = j
                elif j < len(lines):
                    value = lines[j].strip()
                    i = j
            out.setdefault(tag, _clean(value))
            i += 1
            continue
        if in_loop and line and not line.startswith("_"):
            pass  # loop data rows are not header fields
        i += 1
    return out


def _first(tags: Dict[str, str], names: Sequence[str]) -> str:
    for name in names:
        value = tags.get(name, "")
        if value and value not in ("?", "."):
            return value
    return ""


def formula_elements(formula: str) -> List[str]:
    """Element symbols in a formula ('C6 H6 Ni', 'NiO', '- Ni O -')."""
    try:
        from xraydb import atomic_number
    except Exception:  # pragma: no cover
        atomic_number = None
    out: List[str] = []
    for sym in _ELEMENT_RE.findall(str(formula or "")):
        if sym in out:
            continue
        if atomic_number is not None:
            try:
                if not atomic_number(sym):
                    continue
            except Exception:
                continue
        out.append(sym)
    return out


def parse_cif_header(text: str) -> Dict[str, Any]:
    """Searchable header of one CIF (no structure parsing, fast)."""
    tags = _tag_values(text)
    formula = _first(tags, ("_chemical_formula_sum", "_chemical_formula_moiety"))
    head: Dict[str, Any] = {
        "id": _first(tags, _ID_TAGS) or tags.get("__block__", ""),
        "block": tags.get("__block__", ""),
        "name": _first(tags, _NAME_TAGS),
        "formula": formula,
        "elements": formula_elements(formula),
        "spacegroup": _first(tags, _SG_TAGS),
        "doi": _first(tags, ("_journal_doi", "_journal_paper_doi", "_citation_doi")),
    }
    for key, tag in _NUM_TAGS.items():
        head[key] = _number(tags.get(tag))
    year = _number(_first(tags, _YEAR_TAGS))
    head["year"] = int(year) if math.isfinite(year) else None
    r = _number(_first(tags, _R_TAGS))
    # CIF R factors are fractions; the CSD reports percent — keep percent
    head["r_factor"] = (r * 100.0 if r < 1.0 else r) if math.isfinite(r) else None
    return head


# ---------------------------------------------------------------------------
# Offline library index
# ---------------------------------------------------------------------------


def _is_cif(name: str) -> bool:
    return str(name).lower().endswith(".cif")


def library_signature(source: str | Path) -> str:
    """Changes whenever the zip (or the folder's CIF set) changes."""
    path = Path(source)
    st = path.stat()
    if path.is_dir():
        newest, count = st.st_mtime, 0
        for dirpath, _dirs, files in os.walk(path):
            for name in files:
                if _is_cif(name):
                    count += 1
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
                    except OSError:
                        pass
        token = f"dir|{path.resolve()}|{count}|{newest:.3f}"
    else:
        token = f"file|{path.resolve()}|{st.st_size}|{st.st_mtime:.3f}"
    return hashlib.sha1(token.encode("utf-8")).hexdigest()


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def build_index(
    source: str | Path,
    cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Read every CIF header of a zip archive or a folder."""
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(str(path))
    entries: List[Dict[str, Any]] = []
    bad = 0

    def _check(n: int) -> None:
        if cancel is not None and n % 200 == 0 and cancel():
            raise IndexCancelled()

    if path.is_dir():
        n = 0
        for dirpath, _dirs, files in os.walk(path):
            for name in sorted(files):
                if not _is_cif(name):
                    continue
                n += 1
                _check(n)
                full = Path(dirpath) / name
                try:
                    if full.stat().st_size > _MAX_CIF_BYTES:
                        bad += 1
                        continue
                    head = parse_cif_header(_decode(full.read_bytes()))
                except Exception:
                    bad += 1
                    continue
                head["member"] = str(full.relative_to(path)).replace("\\", "/")
                entries.append(head)
    else:
        if not zipfile.is_zipfile(path):
            raise ValueError(f"{path.name} is neither a zip archive nor a folder")
        with zipfile.ZipFile(path) as zf:
            n = 0
            for info in zf.infolist():
                if info.is_dir() or not _is_cif(info.filename):
                    continue
                n += 1
                _check(n)
                if info.file_size > _MAX_CIF_BYTES:
                    bad += 1
                    continue
                try:
                    head = parse_cif_header(_decode(zf.read(info)))
                except Exception:
                    bad += 1
                    continue
                head["member"] = info.filename
                entries.append(head)
    for head in entries:
        if not head.get("id"):
            head["id"] = Path(head["member"]).stem
    return {
        "schema": INDEX_SCHEMA,
        "source": str(path.resolve()),
        "signature": library_signature(path),
        "entries": entries,
        "unreadable": bad,
    }


def index_file(index_dir: str | Path, source: str | Path) -> Path:
    key = hashlib.sha1(str(Path(source).resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return Path(index_dir) / f"cif_index_{key}.json"


def load_or_build_index(
    source: str | Path,
    index_dir: str | Path,
    cancel: Optional[Callable[[], bool]] = None,
    rebuild: bool = False,
) -> Dict[str, Any]:
    """Cached index when the archive is unchanged, else a fresh one (saved).

    A CSD pack brings its own index: nothing is built or cached.
    """
    pack = csd_pack(source)
    if pack is not None:
        return pack_index(pack)
    target = index_file(index_dir, source)
    if not rebuild and target.is_file():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if (
                isinstance(data, dict)
                and data.get("schema") == INDEX_SCHEMA
                and data.get("signature") == library_signature(source)
            ):
                return data
        except (OSError, ValueError):
            pass
    data = build_index(source, cancel=cancel)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(target))
    except OSError:
        pass  # an unwritable cache only costs a rebuild next time
    return data


def _squash(text: str) -> str:
    return re.sub(r"[\s\-]+", "", str(text or "")).lower()


_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*\.?\d*)")


def _formula_tokens(formula: str) -> set:
    """{'Ni', 'O5', ...}: element + count ('Ni' == 'Ni1')."""
    out = set()
    for el, n in _TOKEN_RE.findall(str(formula or "")):
        n = n.rstrip(".")
        out.add(el + ("" if n in ("", "1") else n))
    return out


def search_index(
    entries: Sequence[Dict[str, Any]],
    text: str = "",
    field: str = "all",
    elements: Sequence[str] = (),
    strict: bool = False,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Filter index entries; text is a case-insensitive substring."""
    query = str(text or "").strip().lower()
    need = {str(e).strip() for e in elements if str(e).strip()}
    out: List[Dict[str, Any]] = []
    for e in entries:
        if need:
            have = set(e.get("elements") or [])
            if not need <= have or (strict and have != need):
                continue
        if query:
            if field == "formula":
                # token match: "NiO" finds "Ni O", not "C8 H4 Ni O5"
                want = _formula_tokens(text)
                ok = bool(want) and want <= _formula_tokens(e.get("formula", ""))
            elif field == "id":
                ok = query in str(e.get("id", "")).lower() or query in str(e.get("block", "")).lower()
            elif field == "name":
                ok = query in str(e.get("name", "")).lower()
            else:
                hay = " ".join(
                    str(e.get(k, "")) for k in ("id", "block", "name", "formula", "spacegroup", "member", "doi")
                ).lower()
                want = _formula_tokens(text)
                ok = query in hay or (bool(want) and want <= _formula_tokens(e.get("formula", "")))
            if not ok:
                continue
        out.append(e)
        if len(out) >= int(limit):
            break
    return out


# ---------------------------------------------------------------------------
# CSD pack (CIF/<Letter>.zip + CSD_index/csd_index.sqlite)
# ---------------------------------------------------------------------------

PACK_DB = ("CSD_index", "csd_index.sqlite")
PACK_CIF = "CIF"
_PACK_MEMBER_RE = re.compile(r"^([A-Za-z0-9])\.zip/([A-Za-z0-9]+)\.cif$")


def csd_pack(source: str | Path) -> Optional[Dict[str, str]]:
    """{"root", "db", "cif_dir"} when *source* is a CSD pack, else None.

    Accepted: the pack folder, its CIF folder, or the csd_index.sqlite file.
    """
    try:
        path = Path(source)
        if path.is_file() and path.name.lower() == PACK_DB[1]:
            root = path.parent.parent
        elif path.is_dir() and path.name.upper() == PACK_CIF and (path.parent / PACK_DB[0] / PACK_DB[1]).is_file():
            root = path.parent
        elif path.is_dir():
            root = path
        else:
            return None
        db = root / PACK_DB[0] / PACK_DB[1]
        cif_dir = root / PACK_CIF
        if not (db.is_file() and cif_dir.is_dir() and any(cif_dir.glob("?.zip"))):
            return None
    except OSError:
        return None
    return {"root": str(root), "db": str(db), "cif_dir": str(cif_dir)}


def _pack_connect(db: str):
    import sqlite3

    # read-only: the pack is never written by smartLarch
    return sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True)


def pack_index(pack: Dict[str, str]) -> Dict[str, Any]:
    """Index handle of a CSD pack: the SQLite file, counted, not loaded."""
    con = _pack_connect(pack["db"])
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(csd)")}
        if "refcode" not in cols:
            raise ValueError(f"{pack['db']} has no CSD table")
        count = int(con.execute("SELECT COUNT(*) FROM csd WHERE has_3d = 1").fetchone()[0])
    finally:
        con.close()
    return {
        "schema": INDEX_SCHEMA,
        "kind": "csd_pack",
        "source": pack["root"],
        "db": pack["db"],
        "cif_dir": pack["cif_dir"],
        "has_elements": "elements" in cols,
        "count": count,
        "entries": [],
        "unreadable": 0,
    }


def index_count(index: Dict[str, Any]) -> int:
    """Number of CIFs an index covers (a CSD pack is counted, not listed)."""
    if index.get("kind") == "csd_pack":
        return int(index.get("count") or 0)
    return len(index.get("entries") or [])


def _like(text: str) -> str:
    """Substring pattern for LIKE ... ESCAPE '\\'."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", str(text)) + "%"


def _pack_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _pack_entry(row: Dict[str, Any], has_elements: bool) -> Dict[str, Any]:
    ref = str(row.get("refcode") or "")
    formula = str(row.get("formula") or "")
    if has_elements:
        elements = str(row.get("elements") or "").split()
    else:
        elements = formula_elements(formula)
    year = row.get("year")
    r = _pack_float(row.get("r_factor"))
    entry: Dict[str, Any] = {
        "id": ref,
        "block": ref,
        "name": str(row.get("chemical_name") or ""),
        "formula": formula,
        "elements": elements,
        "spacegroup": str(row.get("spacegroup") or ""),
        "year": int(year) if isinstance(year, (int, float)) and math.isfinite(year) else None,
        "r_factor": r if math.isfinite(r) else None,  # the CSD stores percent
        "doi": str(row.get("doi") or ""),
        "ccdc_number": row.get("ccdc_number"),
        "member": f"{ref[:1].upper()}.zip/{ref}.cif",
    }
    for key in ("a", "b", "c", "alpha", "beta", "gamma"):
        entry[key] = _pack_float(row.get(key))
    entry["volume"] = float("nan")
    return entry


def search_csd_pack(
    index: Dict[str, Any],
    text: str = "",
    field: str = "all",
    elements: Sequence[str] = (),
    strict: bool = False,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """search_index semantics on a CSD pack's SQLite index (only entries with a CIF).

    SQL narrows the rows; the formula-token and strict-element tests are the
    same Python checks as search_index.  The ID field also takes a CCDC number.
    """
    query = str(text or "").strip()
    low = query.lower()
    need = {str(e).strip() for e in elements if str(e).strip()}
    want = _formula_tokens(query) if field in ("formula", "all") else set()
    has_el = bool(index.get("has_elements"))
    ccdc = int(query) if query.isdigit() else None
    where = ["has_3d = 1"]
    params: List[Any] = []
    if has_el:
        for el in sorted(need):
            where.append("elements LIKE ?")
            params.append(f"% {el} %")
    if query:
        if field == "name":
            where.append("chemical_name LIKE ? ESCAPE '\\'")
            params.append(_like(query))
        elif field == "id":
            clause = "refcode LIKE ? ESCAPE '\\'"
            params.append(_like(query))
            if ccdc is not None:
                clause += " OR ccdc_number = ?"
                params.append(ccdc)
            where.append(f"({clause})")
        elif field == "formula":
            if not want:
                return []
            if has_el:
                for el in sorted({_TOKEN_RE.match(t).group(1) for t in want}):
                    where.append("elements LIKE ?")
                    params.append(f"% {el} %")
        else:
            parts = [f"{col} LIKE ? ESCAPE '\\'" for col in ("refcode", "chemical_name", "formula", "spacegroup", "doi")]
            params.extend([_like(query)] * len(parts))
            if ccdc is not None:
                parts.append("ccdc_number = ?")
                params.append(ccdc)
            if want and has_el:
                els = sorted({_TOKEN_RE.match(t).group(1) for t in want})
                parts.append("(" + " AND ".join("elements LIKE ?" for _ in els) + ")")
                params.extend(f"% {el} %" for el in els)
            where.append("(" + " OR ".join(parts) + ")")
    order = "ORDER BY (refcode = ?) DESC, refcode" if field == "id" and query else ""
    if order:
        params.append(query.upper())
    sql = f"SELECT * FROM csd WHERE {' AND '.join(where)} {order}"
    out: List[Dict[str, Any]] = []
    con = _pack_connect(index["db"])
    try:
        cur = con.execute(sql, params)
        names = [d[0] for d in cur.description]
        for values in cur:
            e = _pack_entry(dict(zip(names, values)), has_el)
            have = set(e["elements"])
            if need and (not need <= have or (strict and have != need)):
                continue
            if query and field == "formula":
                if not want <= _formula_tokens(e["formula"]):
                    continue
            elif query and field == "all":
                hay = " ".join(str(e.get(k, "")) for k in ("id", "name", "formula", "spacegroup", "doi")).lower()
                ok = low in hay or (ccdc is not None and e.get("ccdc_number") == ccdc)
                if not ok and not (want and want <= _formula_tokens(e["formula"])):
                    continue
            out.append(e)
            if len(out) >= int(limit):
                break
    finally:
        con.close()
    return out


def search_library(
    index: Dict[str, Any],
    text: str = "",
    field: str = "all",
    elements: Sequence[str] = (),
    strict: bool = False,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Search whichever offline index *index* is (zip/folder list or CSD pack)."""
    if index.get("kind") == "csd_pack":
        return search_csd_pack(index, text, field, elements, strict, limit)
    return search_index(index.get("entries") or [], text, field, elements, strict, limit)


def _read_pack_member(pack: Dict[str, str], member: str) -> str:
    m = _PACK_MEMBER_RE.match(str(member))
    if not m:
        raise ValueError(f"not a CSD pack member: {member}")
    letter, ref = m.group(1).upper(), m.group(2)
    archive = Path(pack["cif_dir"]) / f"{letter}.zip"
    if not archive.is_file():
        raise FileNotFoundError(str(archive))
    with zipfile.ZipFile(archive) as zf:
        try:
            return _decode(zf.read(f"{ref}.cif"))
        except KeyError:
            raise FileNotFoundError(f"{ref}.cif is not in {archive.name}") from None


def read_member(source: str | Path, member: str) -> str:
    path = Path(source)
    pack = csd_pack(path)
    if pack is not None:
        return _read_pack_member(pack, member)
    if path.is_dir():
        full = (path / member).resolve()
        if path.resolve() not in full.parents:
            raise ValueError("member outside the library folder")
        return _decode(full.read_bytes())
    with zipfile.ZipFile(path) as zf:
        return _decode(zf.read(member))


def _ascii(text: str, fallback: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]+", "_", str(text or "")).strip("_")
    return out[:60] or fallback


def save_cif(text: str, out_dir: str | Path, prefix: str, cif_id: str) -> Path:
    """Write <out_dir>/<prefix>_<id>.cif; reuse an identical file, never overwrite a different one."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{prefix}_{_ascii(cif_id, 'cif')}"
    for n in range(1, 1000):
        target = out_dir / (f"{base}.cif" if n == 1 else f"{base}_{n}.cif")
        if not target.exists():
            target.write_text(text, encoding="utf-8", newline="")  # bytes as given
            return target
        try:
            if target.read_text(encoding="utf-8", errors="replace") == text:
                return target
        except OSError:
            continue
    raise OSError(f"no free file name for {base}")


def extract_member(source: str | Path, member: str, out_dir: str | Path, cif_id: str) -> Path:
    return save_cif(read_member(source, member), out_dir, "OFF", cif_id or Path(member).stem)


# ---------------------------------------------------------------------------
# COD (online)
# ---------------------------------------------------------------------------


def _http_get(url: str, timeout: float) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "smartLarch-Platform (COD search)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return resp.read()


def cod_query(text: str = "", field: str = "all", elements: Sequence[str] = (), strict: bool = False) -> Dict[str, str]:
    """COD result-page parameters for one search."""
    params: Dict[str, str] = {}
    query = str(text or "").strip()
    if query:
        if field == "id":
            params["id"] = query
        elif field == "formula":
            params["formula"] = " ".join(_ELEMENT_RE.sub(r" \1", query).split())
        else:
            params["text"] = query
    els = [str(e).strip() for e in elements if str(e).strip()][:8]
    for i, el in enumerate(els, start=1):
        params[f"el{i}"] = el
    if strict and els:
        params["strictmin"] = str(len(els))
        params["strictmax"] = str(len(els))
    return params


def _cod_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def cod_hit(row: Dict[str, Any]) -> Dict[str, Any]:
    """One COD JSON row -> the common hit dict."""
    formula = str(row.get("formula") or "").strip().strip("-").strip()
    r = _cod_float(row.get("Robs") if row.get("Robs") is not None else row.get("Rall"))
    year = _cod_float(row.get("year"))
    return {
        "source": "cod",
        "id": str(row.get("file") or ""),
        "name": str(row.get("chemname") or row.get("mineral") or row.get("commonname") or ""),
        "formula": formula,
        "elements": formula_elements(formula),
        "spacegroup": str(row.get("sg") or ""),
        "a": _cod_float(row.get("a")),
        "b": _cod_float(row.get("b")),
        "c": _cod_float(row.get("c")),
        "year": int(year) if math.isfinite(year) else None,
        "r_factor": (r * 100.0 if r < 1.0 else r) if math.isfinite(r) else None,
        "doi": str(row.get("doi") or ""),
        "date": str(row.get("date") or ""),
    }


def cod_search(
    text: str = "",
    field: str = "all",
    elements: Sequence[str] = (),
    strict: bool = False,
    limit: int = 200,
    timeout: float = 30.0,
    get: Optional[Callable[[str, float], bytes]] = None,
) -> List[Dict[str, Any]]:
    """Search COD; refuses (TooManyResults) above COD_MAX_RESULTS matches."""
    from urllib.parse import urlencode

    params = cod_query(text, field, elements, strict)
    if not params:
        raise ValueError("empty query")
    fetch = get or _http_get
    count_raw = fetch(f"{COD_BASE}/result?{urlencode({**params, 'format': 'count'})}", timeout)
    try:
        count = int(_decode(count_raw).strip() or 0)
    except ValueError as exc:
        raise RuntimeError(f"unexpected COD answer: {_decode(count_raw)[:200]}") from exc
    if count > COD_MAX_RESULTS:
        raise TooManyResults(count)
    if count == 0:
        return []
    raw = fetch(f"{COD_BASE}/result?{urlencode({**params, 'format': 'json'})}", timeout)
    try:
        rows = json.loads(_decode(raw))
    except ValueError as exc:
        raise RuntimeError(f"unexpected COD answer: {_decode(raw)[:200]}") from exc
    hits = [cod_hit(r) for r in rows if isinstance(r, dict)]
    return hits[: int(limit)]


def cod_cif_text(cod_id: str, timeout: float = 30.0, get: Optional[Callable[[str, float], bytes]] = None) -> str:
    cod_id = str(cod_id or "").strip()
    if not cod_id.isdigit():
        raise ValueError(f"not a COD id: {cod_id}")
    text = _decode((get or _http_get)(f"{COD_BASE}/{cod_id}.cif", timeout))
    if "data_" not in text:
        raise RuntimeError(f"COD {cod_id}: no CIF returned")
    return text


def cod_download(cod_id: str, out_dir: str | Path, timeout: float = 30.0, get=None) -> Path:
    return save_cif(cod_cif_text(cod_id, timeout, get), out_dir, "COD", cod_id)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

COMPARE_FIELDS = ("id", "name", "formula", "spacegroup", "a", "b", "c", "alpha", "beta", "gamma", "volume", "year", "r_factor", "doi")
_NUMERIC = {"a", "b", "c", "alpha", "beta", "gamma", "volume", "year", "r_factor"}


def _cell_volume(h: Dict[str, Any]) -> float:
    a, b, c = (float(h.get(k, float("nan"))) for k in ("a", "b", "c"))
    al, be, ga = (math.radians(float(h.get(k, float("nan")))) for k in ("alpha", "beta", "gamma"))
    term = 1 - math.cos(al) ** 2 - math.cos(be) ** 2 - math.cos(ga) ** 2 + 2 * math.cos(al) * math.cos(be) * math.cos(ga)
    return a * b * c * math.sqrt(term) if term > 0 else float("nan")


def _symbol(site) -> str:
    try:
        return site.specie.symbol
    except Exception:  # disordered site: its first element
        try:
            return site.species.elements[0].symbol
        except Exception:
            return str(site.species_string)


def _match_sites(lat, fa, sym_a, fb, sym_b, shift):
    """Distances (Å) of every A site (+shift) to the nearest same-element B site."""
    import numpy as np

    dists, unmatched = [], 0
    for i, sym in enumerate(sym_a):
        idx = [j for j, s in enumerate(sym_b) if s == sym]
        if not idx:
            unmatched += 1
            continue
        d = fb[idx] - (fa[i] + shift)
        d -= np.round(d)
        best = float(np.min(np.linalg.norm(lat.get_cartesian_coords(d), axis=1)))
        if best > _SITE_MATCH_A:
            unmatched += 1
        else:
            dists.append(best)
    return dists, unmatched


def _compare_coords(text_a: str, text_b: str) -> Dict[str, Any]:
    """Nearest same-element site of B for every site of A, in A's cell (Å).

    The origin may differ between two CIFs of one compound (e.g. the metal at
    0,0,0 or at 1/2,1/2,1/2), so every translation that puts A's first site on
    a B site of the same element is tried and the best one is kept.  A
    different axis order or a different cell is not searched.
    """
    import numpy as np

    from .feff_build import _structure_module

    read_structure, _ = _structure_module()
    sa = read_structure(str(text_a))
    sb = read_structure(str(text_b))
    lat = sa.lattice
    fa = np.array([s.frac_coords for s in sa.sites], dtype=float)
    fb = np.array([s.frac_coords for s in sb.sites], dtype=float)
    sym_a = [_symbol(s) for s in sa.sites]
    sym_b = [_symbol(s) for s in sb.sites]
    shifts = [np.zeros(3)]
    if len(sym_a):
        shifts += [fb[j] - fa[0] for j, s in enumerate(sym_b) if s == sym_a[0]]
    best = None
    for shift in shifts:
        dists, unmatched = _match_sites(lat, fa, sym_a, fb, sym_b, shift)
        mean = sum(dists) / len(dists) if dists else float("inf")
        key = (unmatched, mean)
        if best is None or key < best[0]:
            best = (key, dists, unmatched, shift)
    _key, dists, unmatched, shift = best
    shift = np.asarray(shift, dtype=float) - np.round(shift)
    return {
        "ok": True,
        "n_a": len(sa.sites),
        "n_b": len(sb.sites),
        "max": max(dists) if dists else float("nan"),
        "mean": (sum(dists) / len(dists)) if dists else float("nan"),
        "unmatched": unmatched,
        "origin_shift": [round(float(v), 4) for v in shift],
    }


def compare_cifs(text_a: str, text_b: str, meta_a: Optional[dict] = None, meta_b: Optional[dict] = None) -> Dict[str, Any]:
    """Field-by-field comparison of two CIFs (meta_* may add source fields)."""
    ha = {**parse_cif_header(text_a), **{k: v for k, v in (meta_a or {}).items() if v not in (None, "")}}
    hb = {**parse_cif_header(text_b), **{k: v for k, v in (meta_b or {}).items() if v not in (None, "")}}
    for h in (ha, hb):
        if not math.isfinite(_number(h.get("volume"))):
            h["volume"] = _cell_volume(h)
    rows = []
    for key in COMPARE_FIELDS:
        va, vb = ha.get(key), hb.get(key)
        if key in _NUMERIC:
            fa, fb = _number(va), _number(vb)
            delta = fb - fa if math.isfinite(fa) and math.isfinite(fb) else float("nan")
            same = math.isfinite(delta) and abs(delta) <= (0.01 if key in ("a", "b", "c") else 0.1 if key != "volume" else 0.5)
            rows.append({"field": key, "a": fa, "b": fb, "delta": delta, "same": bool(same)})
        else:
            sa, sb = str(va or ""), str(vb or "")
            same = _squash(sa) == _squash(sb)
            rows.append({"field": key, "a": sa, "b": sb, "delta": None, "same": bool(same)})
    try:
        coords = _compare_coords(text_a, text_b)
    except Exception as exc:
        coords = {"ok": False, "error": str(exc)[:300]}
    return {
        "rows": rows,
        "coords": coords,
        "same_formula": _squash(ha.get("formula")) == _squash(hb.get("formula")),
        "same_spacegroup": _squash(ha.get("spacegroup")) == _squash(hb.get("spacegroup")),
    }
