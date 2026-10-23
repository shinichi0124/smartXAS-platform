"""
Multi-group project model mirroring Athena's group list + per-group parameters.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

import numpy as np

from .athena_pipeline import AthenaSettings, AthenaSession, ProcessResult


@dataclass
class GroupRecord:
    id: str
    name: str
    path: str = ""
    datatype: str = "xmu"  # xmu | chi | norm | dmude (derived arrays keep native semantics)
    element: str = ""
    edge: str = "K"
    energy_shift: float = 0.0
    importance: float = 1.0
    frozen: bool = False
    marked: bool = False  # checkbox in list
    settings: AthenaSettings = field(default_factory=AthenaSettings)
    # raw
    energy: Optional[np.ndarray] = None
    mu: Optional[np.ndarray] = None
    # processed cache arrays (optional dict for JSON-unfriendly)
    session: Optional[AthenaSession] = field(default=None, repr=False)
    note: str = ""
    is_derived: bool = False
    _initial_state: Optional[Dict[str, Any]] = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.is_derived:
            self.commit_initial_state()

    def commit_initial_state(self) -> None:
        """Record the post-import state used by Reset to initial."""
        self._initial_state = {
            "name": self.name, "path": self.path, "datatype": self.datatype,
            "element": self.element, "edge": self.edge,
            "energy_shift": float(self.energy_shift),
            "importance": float(self.importance), "frozen": bool(self.frozen),
            "marked": bool(self.marked), "settings": deepcopy(self.settings),
            "energy": np.array(self.energy, copy=True) if self.energy is not None else None,
            "mu": np.array(self.mu, copy=True) if self.mu is not None else None,
            "note": self.note,
        }

    def restore_initial_state(self) -> bool:
        """Restore the last committed post-import state."""
        if self._initial_state is None:
            return False
        state = self._initial_state
        for key in ("name", "path", "datatype", "element", "edge", "note"):
            setattr(self, key, str(state[key]))
        self.energy_shift = float(state["energy_shift"])
        self.importance = float(state["importance"])
        self.frozen = bool(state["frozen"])
        self.marked = bool(state["marked"])
        self.settings = deepcopy(state["settings"])
        self.energy = np.array(state["energy"], copy=True) if state["energy"] is not None else None
        self.mu = np.array(state["mu"], copy=True) if state["mu"] is not None else None
        self.session = None
        return True

    def ensure_session(self) -> AthenaSession:
        if self.session is None:
            self.session = AthenaSession()
            self.session.settings = deepcopy(self.settings)
            if self.energy is not None and self.mu is not None:
                self.session._raw_energy = np.asarray(self.energy, dtype=float)
                self.session._raw_mu = np.asarray(self.mu, dtype=float)
                self.session.group_name = self.name
                self.session.raw_path = self.path
        return self.session

    def _process_signature(self) -> Optional[tuple]:
        """Everything a reprocess depends on: raw data bytes, energy shift,
        settings (incl. the phase-FEFF file's mtime).  None = not cacheable."""
        if self.energy is None or self.mu is None:
            return None
        try:
            e = np.ascontiguousarray(self.energy, dtype=float)
            m = np.ascontiguousarray(self.mu, dtype=float)
            feff_mtime = None
            pf = str(getattr(self.settings, "phase_feff", "") or "")
            if getattr(self.settings, "with_phase", False) and pf:
                try:
                    feff_mtime = Path(pf).stat().st_mtime
                except OSError:
                    feff_mtime = -1.0
            return (
                e.shape, hash(e.tobytes()), m.shape, hash(m.tobytes()),
                float(self.energy_shift), repr(self.settings), feff_mtime,
            )
        except Exception:
            return None

    def process(self, force: bool = False) -> ProcessResult:
        """pre_edge → autobk → FT for this group.

        Memoised (v2.18.1): when the raw data, energy shift and settings are
        identical to the last successful run AND the session still holds the
        arrays that run produced, the cached result is returned.  autobk on
        a 6320-point MEX1 slew scan costs 0.1–2.7 s, and every row click
        with auto-apply on used to redo it for unchanged curves.  Any edit
        (settings, ΔE, deglitch/merge on mu, a replaced session) changes the
        signature or the array identity → full reprocess.  ``force=True``
        always reprocesses.
        """
        sig = self._process_signature()
        sess = self.ensure_session()
        cached = getattr(self, "_proc_cache", None)
        if (
            not force
            and sig is not None
            and cached is not None
            and cached[0] == sig
            and cached[1] is sess
            and sess.arrays is not None
            and sess.arrays is cached[2]
        ):
            return cached[3]
        # inject shifted energy
        if self.energy is not None and self.mu is not None:
            sess._raw_energy = np.asarray(self.energy, dtype=float) + float(self.energy_shift)
            sess._raw_mu = np.asarray(self.mu, dtype=float)
            sess.group_name = self.name
            sess.raw_path = self.path
        sess.settings = deepcopy(self.settings)
        res = sess.reprocess()
        if res.ok and sig is not None and sess.arrays is not None:
            self._proc_cache = (sig, sess, sess.arrays, res)
        else:
            self._proc_cache = None
        return res


class AthenaProject:
    def __init__(self, title: str = "Untitled project"):
        self.title = title
        self.source_path: str = ""
        self.groups: List[GroupRecord] = []
        self.current_id: Optional[str] = None
        # v3.19.5: the batch/processing record (pipeline_rules.ProcessingRecord)
        # that becomes the Notes sheet of an Excel export.  None = no record.
        self.processing_record = None

    @property
    def current(self) -> Optional[GroupRecord]:
        if not self.current_id:
            return None
        for g in self.groups:
            if g.id == self.current_id:
                return g
        return None

    def set_current(self, group_id: str) -> Optional[GroupRecord]:
        for g in self.groups:
            if g.id == group_id:
                self.current_id = group_id
                return g
        return None

    def add_group(
        self,
        name: str,
        energy: np.ndarray,
        mu: np.ndarray,
        path: str = "",
        after_id: Optional[str] = None,
        **kwargs,
    ) -> GroupRecord:
        gid = uuid.uuid4().hex[:10]
        rec = GroupRecord(
            id=gid,
            name=name,
            path=path,
            energy=np.asarray(energy, dtype=float),
            mu=np.asarray(mu, dtype=float),
            **kwargs,
        )
        # insert right after the source group when requested (smooth/rebin
        # children live next to their parent, not at the list bottom)
        if after_id:
            for i, g in enumerate(self.groups):
                if g.id == after_id:
                    self.groups.insert(i + 1, rec)
                    break
            else:
                self.groups.append(rec)
        else:
            self.groups.append(rec)
        if self.current_id is None:
            self.current_id = gid
        return rec

    def remove_marked(self) -> int:
        before = len(self.groups)
        self.groups = [g for g in self.groups if not g.marked]
        # current may have been removed — self.current is then None and the id dangles
        if self.current is None:
            self.current_id = self.groups[0].id if self.groups else None
        return before - len(self.groups)

    def mark_all(self, marked: bool = True) -> None:
        for g in self.groups:
            if not g.frozen:
                g.marked = marked

    def invert_marks(self) -> None:
        for g in self.groups:
            if not g.frozen:
                g.marked = not g.marked

    def marked_groups(self) -> List[GroupRecord]:
        return [g for g in self.groups if g.marked]

    def apply_settings_to_marked(self, settings: AthenaSettings, also_eshift: Optional[float] = None) -> int:
        n = 0
        for g in self.marked_groups():
            if g.frozen:
                continue
            g.settings = deepcopy(settings)
            if also_eshift is not None:
                g.energy_shift = also_eshift
            g.process()
            n += 1
        return n

    def names(self) -> List[str]:
        return [g.name for g in self.groups]
