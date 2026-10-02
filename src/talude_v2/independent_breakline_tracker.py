"""Evidence-constrained, independent 3D CRISTA/PÉ tracing (research only).

The tracker *only* connects observed, individually-supported Ground candidate
coordinates. Baseline and manual seeds are hints, never output vertices. A
missing/ambiguous next station stops the line: no endpoint extrapolation, no
interpolation across unobserved gaps, no automatic CREST/TOE pairing.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
from scipy.spatial import cKDTree

from .breakline_seed import BreaklineSeed, SeedEvidence


@dataclass(frozen=True, slots=True)
class IndependentTrackerConfig:
    station_step_m: float = 1.0
    search_radius_m: float = 0.80
    seed_snap_radius_m: float = 1.0
    min_forward_m: float = 0.32
    max_lateral_m: float = 0.65
    max_step_m: float = 1.65
    max_dz_per_step_m: float = 1.0
    min_tangent_cos: float = 0.70
    ambiguity_margin: float = 0.055
    ambiguity_separation_m: float = 0.20
    heading_inertia: float = 0.55
    min_vertices: int = 3
    min_length_m: float = 1.25

    def __post_init__(self) -> None:
        positive = (self.station_step_m, self.search_radius_m, self.seed_snap_radius_m,
                    self.min_forward_m, self.max_lateral_m, self.max_step_m,
                    self.max_dz_per_step_m, self.ambiguity_separation_m)
        if (any(not math.isfinite(v) or v <= 0 for v in positive)
            or self.min_vertices < 2 or self.min_length_m <= 0
            or not 0 <= self.min_tangent_cos <= 1
            or not 0 <= self.ambiguity_margin <= 1
            or not 0 <= self.heading_inertia <= 1):
            raise ValueError("Configuração inválida para independent tracking.")
        if self.max_step_m < self.min_forward_m:
            raise ValueError("max_step_m não pode ser inferior a min_forward_m.")


@dataclass(frozen=True, slots=True)
class TrackedBreakline:
    kind: str
    xyz: np.ndarray
    seed_source: str
    evidence_count: int
    length_m: float
    confidence_median: float
    status: str = "EXPERIMENTAL_REVIEW"
    stop_negative: str = ""
    stop_positive: str = ""


@dataclass(frozen=True, slots=True)
class TrackingReport:
    observations_crest: int
    observations_toe: int
    tracks_crest: int
    tracks_toe: int
    vertices_reused: int
    unsnapped_hints: int
    short_tracks: int
    ambiguous_sides: int


def _unit(vector: np.ndarray) -> np.ndarray | None:
    length = float(np.linalg.norm(vector))
    return np.asarray(vector, dtype=np.float64) / length if length > 1e-10 else None


def _trace_direction(
    start: int,
    heading: np.ndarray,
    items: list[SeedEvidence],
    tree: cKDTree,
    claimed: set[int],
    cfg: IndependentTrackerConfig,
) -> tuple[list[int], str]:
    """Walk locally observed stations. Absence always terminates the segment."""
    selected: list[int] = []
    current = start
    direction = heading.copy()
    for _ in range(len(items)):
        current_xyz = items[current].xyz
        predicted_xy = current_xyz[:2] + direction * cfg.station_step_m
        nearby = tree.query_ball_point(predicted_xy, cfg.search_radius_m)
        choices: list[tuple[float, int]] = []
        for index in nearby:
            if index == start or index in claimed or index in selected:
                continue
            point = items[index].xyz
            delta = point[:2] - current_xyz[:2]
            distance = float(np.linalg.norm(delta))
            progress = float(np.dot(delta, direction))
            lateral = abs(float(np.linalg.det(np.stack((direction, delta)))))
            if not (cfg.min_forward_m <= progress and distance <= cfg.max_step_m
                    and lateral <= cfg.max_lateral_m):
                continue
            if abs(float(point[2] - current_xyz[2])) > cfg.max_dz_per_step_m:
                continue
            tangent = items[index].direction_xy
            if tangent is not None and abs(float(np.dot(direction, tangent))) < cfg.min_tangent_cos:
                continue
            prediction_error = float(np.linalg.norm(point[:2] - predicted_xy))
            score = (0.60 * prediction_error / cfg.search_radius_m
                     + 0.18 * lateral / cfg.max_lateral_m
                     + 0.12 * (1.0 - items[index].confidence)
                     + 0.10 * abs(progress - cfg.station_step_m) / cfg.station_step_m)
            choices.append((score, int(index)))
        choices.sort(key=lambda item: (item[0], item[1]))
        if not choices:
            return selected, "NO_LOCAL_EVIDENCE"
        best_score, next_index = choices[0]
        if len(choices) > 1:
            second_score, second_index = choices[1]
            separation = float(np.linalg.norm(
                items[next_index].xyz[:2] - items[second_index].xyz[:2]
            ))
            if (second_score - best_score <= cfg.ambiguity_margin
                and separation >= cfg.ambiguity_separation_m):
                return selected, "AMBIGUOUS_PARALLEL_EVIDENCE"
        new_direction = _unit(items[next_index].xyz[:2] - current_xyz[:2])
        if new_direction is None:
            return selected, "ZERO_STEP"
        blended = _unit(cfg.heading_inertia * direction
                        + (1.0 - cfg.heading_inertia) * new_direction)
        if blended is None:
            return selected, "UNSTABLE_DIRECTION"
        selected.append(next_index)
        direction = blended
        current = next_index
    return selected, "OBSERVATION_LIMIT"


def trace_independent_breaklines(
    observations: Iterable[SeedEvidence],
    *,
    seeds: Iterable[BreaklineSeed] = (),
    config: IndependentTrackerConfig | None = None,
) -> tuple[list[TrackedBreakline], TrackingReport]:
    """Create review-only polylines exclusively from observed Ground evidence.

    Observations MUST be computed from real Ground samples (curvature/normal).
    CREST and TOE are processed independently. Face pairing is not attempted.
    A seed without a nearby observation is reported but never projected as XYZ.
    """
    cfg = config or IndependentTrackerConfig()
    valid: list[SeedEvidence] = []
    for evidence in observations:
        if not isinstance(evidence, SeedEvidence):
            raise TypeError("observations requer SeedEvidence.")
        if evidence.kind in {"CREST", "TOE"} and evidence.source in {"curvature", "normal"}:
            valid.append(evidence)
    hints = list(seeds)
    if any(not isinstance(hint, BreaklineSeed) for hint in hints):
        raise TypeError("seeds requer BreaklineSeed.")
    tracks: list[TrackedBreakline] = []
    unsnapped = short = ambiguous = reused = 0
    for kind in ("CREST", "TOE"):
        points = [item for item in valid if item.kind == kind]
        if not points:
            unsnapped += sum(hint.kind == kind for hint in hints)
            continue
        tree = cKDTree(np.asarray([item.xyz[:2] for item in points]))
        # Explicit baseline/manual hints are tried first. Remaining observed
        # evidence becomes its own independent seed, even with no baseline.
        queued: list[tuple[int, np.ndarray | None, str]] = []
        for hint in hints:
            if hint.kind != kind:
                continue
            distance, nearest = tree.query(hint.xyz[:2], k=1)
            if distance > cfg.seed_snap_radius_m:
                unsnapped += 1
                continue
            queued.append((int(nearest), hint.direction_xy, hint.source))
        queued.extend((index, item.direction_xy, item.source)
                      for index, item in sorted(enumerate(points),
                                                key=lambda pair: -pair[1].confidence))
        claimed: set[int] = set()
        for start, preferred_direction, origin in queued:
            if start in claimed:
                reused += 1
                continue
            orientation = _unit(preferred_direction) if preferred_direction is not None else None
            if orientation is None:
                orientation = points[start].direction_xy
            if orientation is None:
                short += 1
                continue
            forward, end_positive = _trace_direction(start, orientation, points, tree, claimed, cfg)
            # Prevent both sides from recycling one another's vertices.
            backward, end_negative = _trace_direction(
                start, -orientation, points, tree, claimed.union(forward), cfg,
            )
            if end_positive.startswith("AMBIGUOUS"):
                ambiguous += 1
            if end_negative.startswith("AMBIGUOUS"):
                ambiguous += 1
            ordered = list(reversed(backward)) + [start] + forward
            geometry = np.asarray([points[index].xyz for index in ordered], dtype=np.float64)
            length = float(np.linalg.norm(np.diff(geometry[:, :2], axis=0), axis=1).sum())
            # Claim vertices of a short component too: duplicates must not be
            # reported as multiple short lines. They are not published as tracks.
            claimed.update(ordered)
            if len(ordered) < cfg.min_vertices or length < cfg.min_length_m:
                short += 1
                continue
            geometry.setflags(write=False)
            tracks.append(TrackedBreakline(
                kind=kind, xyz=geometry, seed_source=origin,
                evidence_count=len(ordered), length_m=length,
                confidence_median=float(np.median([points[i].confidence for i in ordered])),
                stop_negative=end_negative, stop_positive=end_positive,
            ))
    report = TrackingReport(
        sum(item.kind == "CREST" for item in valid),
        sum(item.kind == "TOE" for item in valid),
        sum(track.kind == "CREST" for track in tracks),
        sum(track.kind == "TOE" for track in tracks),
        reused, unsnapped, short, ambiguous,
    )
    return tracks, report
