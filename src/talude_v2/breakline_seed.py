"""Independent discovery seeds for V2 breaklines (experimental, not routed to AUTO).

Seeds carry *relative* evidence, not statistically calibrated probabilities.  A
CREST does not require a corresponding TOE, and vice versa.  Baseline geometry
is only an approximate starting hint; final coordinates require cloud support.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np

_KINDS = frozenset({"CREST", "TOE", "UNKNOWN"})
_SOURCES = frozenset({"baseline", "curvature", "normal", "manual"})


def _xyz(value: object) -> np.ndarray:
    p = np.array(value, dtype=np.float64, copy=True)
    if p.shape != (3,) or not np.isfinite(p).all():
        raise ValueError("Seed XYZ deve conter exatamente três coordenadas finitas.")
    p.setflags(write=False)
    return p


def _direction(value: object | None) -> np.ndarray | None:
    if value is None:
        return None
    vec = np.array(value, dtype=np.float64, copy=True)
    if vec.shape != (2,) or not np.isfinite(vec).all():
        raise ValueError("A direção XY deve conter exatamente dois valores finitos.")
    length = float(np.linalg.norm(vec))
    if length <= 1e-10:
        return None
    vec /= length
    vec.setflags(write=False)
    return vec


@dataclass(frozen=True, slots=True)
class SeedEvidence:
    kind: str
    xyz: np.ndarray
    direction_xy: np.ndarray | None
    confidence: float
    source: str
    face_hint: int | None = None

    def __post_init__(self) -> None:
        kind = str(self.kind).upper()
        source = str(self.source).lower()
        score = float(self.confidence)
        if kind not in _KINDS or source not in _SOURCES:
            raise ValueError(f"Tipo/origem de seed inválido: {kind}/{source}.")
        if not np.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("A evidência relativa deve estar entre 0 e 1.")
        if self.face_hint is not None and (not isinstance(self.face_hint, (int, np.integer)) or self.face_hint < 0):
            raise ValueError("face_hint deve ser um inteiro não negativo ou None.")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "confidence", score)
        object.__setattr__(self, "xyz", _xyz(self.xyz))
        object.__setattr__(self, "direction_xy", _direction(self.direction_xy))


@dataclass(frozen=True, slots=True)
class BreaklineSeed(SeedEvidence):
    seed_id: int = 0

    def __post_init__(self) -> None:
        SeedEvidence.__post_init__(self)
        if not isinstance(self.seed_id, (int, np.integer)) or self.seed_id < 0:
            raise ValueError("seed_id deve ser um inteiro não negativo.")


def _baseline_observation(line: Mapping[str, object], confidence: float) -> SeedEvidence | None:
    kind = str(line.get("type", "")).upper()
    if kind not in {"CREST", "TOE"}:
        return None
    xyz = np.asarray(line.get("xyz"), dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] < 3 or len(xyz) < 2:
        return None
    xyz = xyz[:, :3]
    if not np.isfinite(xyz).all():
        return None
    step = np.linalg.norm(np.diff(xyz[:, :2], axis=0), axis=1)
    total = float(step.sum())
    if total <= 1e-7:
        return None
    mids = np.r_[0.0, np.cumsum(step)]
    middle = total * 0.5
    idx = min(int(np.searchsorted(mids, middle, side="right") - 1), len(step) - 1)
    if step[idx] <= 1e-10:
        nonzero = np.flatnonzero(step > 1e-10)
        if not len(nonzero):
            return None
        idx = int(nonzero[np.argmin(np.abs(mids[nonzero] - middle))])
    alpha = float(np.clip((middle - mids[idx]) / step[idx], 0.0, 1.0))
    point = (1.0 - alpha) * xyz[idx] + alpha * xyz[idx + 1]
    direction = xyz[idx + 1, :2] - xyz[idx, :2]
    hint = line.get("face_id")
    try:
        hint = int(hint) if hint is not None else None
    except (ValueError, TypeError):
        hint = None
    if hint is not None and hint < 0:
        hint = None
    return SeedEvidence(kind, point, direction, confidence, "baseline", hint)


def build_breakline_seeds(
    baseline_lines: Iterable[Mapping[str, object]] = (),
    *,
    independent_evidence: Iterable[SeedEvidence] = (),
    start_id: int = 1,
    baseline_confidence: float = 0.50,
    dedupe_radius_m: float = 0.20,
    parallel_cosine: float = 0.90,
) -> list[BreaklineSeed]:
    """Collect single-edge baseline and independent evidence without face pairing.

    Dedupe is kind-specific, near-coincident and orientation-aware. It never
    merges CREST with TOE or joins distant/parallel neighbouring breaklines.
    This function does NOT change the production V2 candidate path.
    """
    if start_id < 0 or dedupe_radius_m < 0 or not 0 <= parallel_cosine <= 1:
        raise ValueError("Parâmetros de identificação/deduplicação inválidos.")
    if not 0 <= float(baseline_confidence) <= 1:
        raise ValueError("baseline_confidence inválido.")
    observations: list[SeedEvidence] = []
    for line in baseline_lines:
        obs = _baseline_observation(line, float(baseline_confidence))
        if obs is not None:
            observations.append(obs)
    observations.extend(independent_evidence)
    selected: list[SeedEvidence] = []
    for obs in observations:
        if not isinstance(obs, SeedEvidence):
            raise TypeError("independent_evidence deve conter SeedEvidence.")
        duplicate = None
        for index, existing in enumerate(selected):
            if obs.kind != existing.kind:
                continue
            if (obs.face_hint is not None and existing.face_hint is not None
                and obs.face_hint != existing.face_hint):
                continue
            if np.linalg.norm(obs.xyz[:2] - existing.xyz[:2]) > dedupe_radius_m:
                continue
            a, b = obs.direction_xy, existing.direction_xy
            if a is not None and b is not None and abs(float(np.dot(a, b))) < parallel_cosine:
                continue
            duplicate = index
            break
        if duplicate is None:
            selected.append(obs)
        elif obs.confidence > selected[duplicate].confidence:
            selected[duplicate] = obs
    return [
        BreaklineSeed(obs.kind, obs.xyz, obs.direction_xy, obs.confidence,
                      obs.source, obs.face_hint, start_id + i)
        for i, obs in enumerate(selected)
    ]
