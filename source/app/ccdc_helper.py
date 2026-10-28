"""CCDC / CSD helper — runs ONLY inside the CSD Software Portfolio's Python.

smartLarch never imports this file: it starts it as a subprocess with the CSD
Python executable (see ccdc_bridge.py).  The ccdc package is tied to the CSD
release and carries its own local data, so it must not enter the smartLarch
process or its runtime environment.

Usage:
    python ccdc_helper.py <cmd> <json-payload> [--extra-path DIR]

One JSON object is written to stdout.  The test-only --extra-path puts a fake
ccdc package on sys.path (python -I otherwise ignores PYTHONPATH).
"""

from __future__ import annotations

import json
import sys


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def _fail(kind: str, message: str) -> None:
    _emit({"ok": False, "kind": kind, "message": str(message)[:800]})
    raise SystemExit(0)


def _classify(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "licen" in text or "activ" in text:
        return "license"
    if "not found" in text or "no such file" in text or "csd.ini" in text or "data" in text and "csd" in text:
        return "no_data"
    return "other"


def _verify(_payload: dict) -> dict:
    try:
        import ccdc
        from ccdc import io
    except ImportError as exc:
        _fail("import", exc)
    except Exception as exc:  # e.g. a licence or data error raised on import
        _fail(_classify(exc), exc)
    info = {"ok": True}
    try:
        # the API version lives on the package (ccdc.__version__), not on io
        info["api_version"] = str(
            getattr(ccdc, "__version__", "") or getattr(io, "__version__", "") or ""
        )
    except Exception:
        info["api_version"] = ""
    try:
        info["csd_version"] = str(io.csd_version())
    except Exception as exc:
        _fail(_classify(exc), exc)
    try:
        info["csd_dir"] = str(io.csd_directory())
    except Exception:
        info["csd_dir"] = ""
    try:
        reader = io.EntryReader("CSD")
        try:
            info["n_entries"] = int(len(reader))
        except Exception:
            try:
                info["n_entries"] = int(reader.entry_count())
            except Exception:
                info["n_entries"] = -1
        probe = True
        try:
            reader.entry("AABHTZ")
        except Exception:
            probe = False
        info["probe"] = bool(probe)
    except Exception as exc:
        _fail(_classify(exc), exc)
    return info


def _entry_row(entry) -> dict:
    def g(name, default=""):
        try:
            value = getattr(entry, name)
        except Exception:
            return default
        if callable(value):
            try:
                value = value()
            except Exception:
                return default
        return default if value is None else value

    def _float(value):
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _nested(outer: str, inner: str, flat: str) -> str:
        # public API: entry.crystal.spacegroup_symbol / entry.publication.doi;
        # the flat name is a fallback for other API versions
        holder = g(outer, None)
        if holder is not None:
            try:
                value = getattr(holder, inner)
                if value:
                    return str(value)
            except Exception:
                pass
        return str(g(flat))

    return {
        "refcode": str(g("identifier")),
        "ccdc_number": g("ccdc_number", None),
        "name": str(g("chemical_name")),
        "formula": str(g("formula")),
        "spacegroup": _nested("crystal", "spacegroup_symbol", "spacegroup"),
        "r_factor": _float(g("r_factor", None)),
        "disorder": bool(g("has_disorder", False)),
        "polymeric": bool(g("is_polymeric", False)),
        "doi": _nested("publication", "doi", "doi"),
    }


def _search(payload: dict) -> dict:
    try:
        from ccdc.search import TextNumericSearch
    except Exception as exc:
        _fail("import", exc)
    field = str(payload.get("field", "name") or "name").lower()
    text = str(payload.get("text", "") or "")
    query = TextNumericSearch()
    try:
        if field == "refcode":
            query.add_identifier(text)
        elif field == "ccdc":
            query.add_ccdc_number(int(float(text)))
        elif field == "doi":
            query.add_doi(text)
        elif field == "all":
            query.add_all_text(text)
        else:
            query.add_compound_name(text)
        settings = query.settings
        settings.has_3d_coordinates = True
        settings.no_errors = True
        settings.no_disorder = bool(payload.get("no_disorder"))
        try:
            settings.max_r_factor = float(payload.get("max_r", 10) or 10)
        except Exception:
            pass
        must = [str(x) for x in (payload.get("must_have") or []) if str(x).strip()]
        if must:
            try:
                settings.must_have_elements = must
            except Exception:
                pass
        max_hits = int(payload.get("max_hits", 200) or 200)
        query.settings.max_hit_structures = max_hits
        hits = query.search(max_hit_structures=max_hits)
    except Exception as exc:
        _fail(_classify(exc), exc)
    rows = []
    for hit in hits[:max_hits]:
        try:
            rows.append(_entry_row(hit.entry))
        except Exception:
            continue
    return {"ok": True, "hits": rows}


def _export(payload: dict) -> dict:
    try:
        from ccdc.io import EntryReader, EntryWriter
    except Exception as exc:
        _fail("import", exc)
    refcode = str(payload.get("refcode", "") or "")
    out = str(payload.get("out", "") or "")
    if not refcode or not out:
        _fail("other", "refcode and out are required")
    try:
        reader = EntryReader("CSD")
        try:
            # the real reader raises for an unknown refcode (it does not return None)
            entry = reader.entry(refcode)
        except Exception as exc:
            _fail("not_found", f"{refcode} is not in this CSD ({exc})")
        if entry is None:
            _fail("not_found", f"{refcode} is not in this CSD")
        writer = EntryWriter(out, format="cif")
        try:
            writer.write(entry)
        finally:
            try:
                writer.close()
            except Exception:
                pass
    except SystemExit:
        raise
    except Exception as exc:
        _fail(_classify(exc), exc)
    return {"ok": True, "out": out}


def main(argv) -> int:
    args = list(argv[1:])
    if "--extra-path" in args:
        idx = args.index("--extra-path")
        try:
            extra = args[idx + 1]
        except IndexError:
            extra = ""
        del args[idx:idx + 2]
        if extra:
            sys.path.insert(0, extra)
    if not args:
        _emit({"ok": False, "kind": "other", "message": "no command"})
        return 0
    cmd = args[0]
    try:
        payload = json.loads(args[1]) if len(args) > 1 else {}
    except Exception as exc:
        _emit({"ok": False, "kind": "other", "message": f"bad payload: {exc}"})
        return 0
    if not isinstance(payload, dict):
        payload = {}
    try:
        if cmd == "verify":
            result = _verify(payload)
        elif cmd == "search":
            result = _search(payload)
        elif cmd == "export":
            result = _export(payload)
        else:
            _emit({"ok": False, "kind": "other", "message": f"unknown command {cmd}"})
            return 0
    except SystemExit:
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        _emit({"ok": False, "kind": _classify(exc), "message": str(exc)[:800]})
        return 0
    _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
