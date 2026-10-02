"""Manual, bounded LAS/LAZ preview of R20 evidence-only breaklines.

Run from project root with PYTHONPATH=src; the production AUTO is not called.
Output DXF is in EPSG:3763 metres (DXF does not encode this CRS reliably).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from talude_v2.independent_breakline_tracker import trace_independent_breaklines
from talude_v2.multiscale_discovery import discover_multiscale_ground


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Talude Studio R20 — análise isolada (Ground real).")
    parser.add_argument("--source", required=True, type=Path, help="Nuvem LAS/LAZ em EPSG:3763.")
    parser.add_argument("--bbox", nargs=4, required=True, type=float,
                        metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                        help="Recorte pequeno numa zona com linhas em falta, em metros EPSG:3763.")
    parser.add_argument("--classes", default="2", help="Classes Ground; por defeito apenas 2.")
    parser.add_argument("--output", type=Path, default=Path("R20_GROUND_PREVIEW"))
    parser.add_argument("--max-points", type=int, default=250_000)
    return parser.parse_args()


def stream_ground_roi(source: Path, bbox: tuple[float, float, float, float],
                      classes: frozenset[int], max_points: int) -> tuple[np.ndarray, dict]:
    """Priority-reservoir of all observed Ground points in a bounded ROI.

    This bounds RAM and avoids favouring points in earlier LAS chunks. The
    resulting observations remain real XYZ; sampling never generates ground.
    """
    try:
        import laspy
    except ImportError as exc:
        raise RuntimeError("Falta 'laspy' no ambiente. Instalar dependências do projeto.") from exc
    xmin, ymin, xmax, ymax = bbox
    if not (xmin < xmax and ymin < ymax and xmax - xmin <= 50 and ymax - ymin <= 50):
        raise ValueError("BBox inválida ou superior a 50x50m; analisar tiles pequenos.")
    if max_points < 1000:
        raise ValueError("--max-points deve ser >= 1000.")
    rng = np.random.default_rng(20261001)
    retained = np.empty((0, 3), dtype=np.float64)
    priorities = np.empty(0, dtype=np.float64)
    total_roi = observed_ground = scanned = 0
    next_progress = 10_000_000
    with laspy.open(source) as reader:
        for chunk in reader.chunk_iterator(250_000):
            scanned += len(chunk)
            if scanned >= next_progress:
                print(f"LAS analisado: {scanned:,} / {reader.header.point_count:,} pontos; "
                      f"Ground no recorte: {observed_ground:,}", flush=True)
                next_progress += 10_000_000
            x = np.asarray(chunk.x, dtype=np.float64)
            y = np.asarray(chunk.y, dtype=np.float64)
            in_roi = ((x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax))
            total_roi += int(in_roi.sum())
            selected = in_roi & np.isin(np.asarray(chunk.classification), tuple(classes))
            observed_ground += int(selected.sum())
            if not selected.any():
                continue
            new_xyz = np.column_stack((x[selected], y[selected],
                                       np.asarray(chunk.z, dtype=np.float64)[selected]))
            finite = np.isfinite(new_xyz).all(axis=1)
            new_xyz = new_xyz[finite]
            if not len(new_xyz):
                continue
            new_priorities = rng.random(len(new_xyz))
            joined = np.concatenate((retained, new_xyz), axis=0)
            scores = np.concatenate((priorities, new_priorities))
            if len(joined) > max_points:
                indices = np.argpartition(scores, -max_points)[-max_points:]
                retained, priorities = joined[indices], scores[indices]
            else:
                retained, priorities = joined, scores
    return retained, {
        "total_points_inside_bbox": total_roi,
        "classified_ground_inside_bbox": observed_ground,
        "ground_points_after_reservoir": len(retained),
        "sampling_applied": observed_ground > max_points,
        "class_filter": sorted(classes),
        "scanned_las_points": scanned,
    }


def export_dxf_3d(path: Path, tracks) -> None:
    """Write simple R12 DXF 3D polylines. All vertices are observed samples."""
    rows = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009",
            "9", "$INSUNITS", "70", "6", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES"]
    for trace in tracks:
        layer = "R20_CRISTA_REVISAO" if trace.kind == "CREST" else "R20_PE_REVISAO"
        rows += ["0", "POLYLINE", "8", layer, "66", "1", "70", "8",
                 "10", "0", "20", "0", "30", "0"]
        for x, y, z in trace.xyz:
            rows += ["0", "VERTEX", "8", layer, "10", f"{x:.6f}",
                     "20", f"{y:.6f}", "30", f"{z:.6f}", "70", "32"]
        rows += ["0", "SEQEND", "8", layer]
    rows += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(rows) + "\n", encoding="ascii")


def main() -> None:
    args = parse_args()
    if not args.source.is_file():
        raise FileNotFoundError(args.source)
    classes = frozenset(int(part.strip()) for part in args.classes.split(","))
    points, cloud_report = stream_ground_roi(args.source, tuple(args.bbox),
                                             classes, args.max_points)
    observations, discovery = discover_multiscale_ground(points)
    tracks, tracking = trace_independent_breaklines(observations)
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    export_dxf_3d(folder / "R20_REVIEW_ONLY_EPSG3763.dxf", tracks)
    report = {
        "project": "Talude Studio", "version": "R20 isolated preview / NOT production AUTO",
        "source_name": args.source.name, "epsg": 3763, "bbox": args.bbox,
        "cloud": cloud_report,
        "discovery": {
            "by_scale_cell_m_crest_toe": discovery.observations_by_scale,
            "merged_crest": discovery.merged_crest, "merged_toe": discovery.merged_toe,
            "failed_scales": discovery.failed_scales,
        },
        "tracking": vars_tracking(tracking),
        "tracks": [
            {"kind": t.kind, "length_m": t.length_m, "observed_vertices": t.evidence_count,
             "status": t.status, "stop_minus": t.stop_negative, "stop_plus": t.stop_positive}
            for t in tracks
        ],
        "warning": ("Classe 0/1 pode conter vegetação; não confundir Ground real "
                    "com superfície não classificada. Não fazer publicação automática."),
    }
    (folder / "R20_DIAGNOSTICO.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("DXF/diagnóstico:", folder)
    print("Pontos Ground usados:", len(points), "| CRISTA/PÉ:",
          discovery.merged_crest, discovery.merged_toe, "| linhas:", len(tracks))


def vars_tracking(report) -> dict:
    from dataclasses import asdict
    return asdict(report)


if __name__ == "__main__":
    main()
