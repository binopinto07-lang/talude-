from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass(slots=True)
class PointCloud:
    xyz: np.ndarray
    classification: np.ndarray | None = None
    crs_wkt: str | None = None
    source: str = ""


@dataclass(slots=True)
class PointCloudInfo:
    path: Path
    point_count: int
    mins: tuple[float, float, float]
    maxs: tuple[float, float, float]
    has_classification: bool
    crs_wkt: str | None = None

    @property
    def width(self) -> float:
        return max(0.0, self.maxs[0] - self.mins[0])

    @property
    def height(self) -> float:
        return max(0.0, self.maxs[1] - self.mins[1])


def inspect_point_cloud(path: str | Path) -> PointCloudInfo:
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in {".las", ".laz"}:
        raise ValueError("A inspeção streaming V1 aplica-se a LAS/LAZ.")

    import laspy

    with laspy.open(path) as reader:
        header = reader.header
        dims = {str(name).lower() for name in header.point_format.dimension_names}
        crs = header.parse_crs()
        mins = tuple(float(v) for v in header.mins)
        maxs = tuple(float(v) for v in header.maxs)
        return PointCloudInfo(
            path=path,
            point_count=int(header.point_count),
            mins=(mins[0], mins[1], mins[2]),
            maxs=(maxs[0], maxs[1], maxs[2]),
            has_classification="classification" in dims,
            crs_wkt=crs.to_wkt() if crs else None,
        )


def iter_point_chunks(
    path: str | Path,
    *,
    chunk_size: int = 1_000_000,
) -> Iterator[tuple[np.ndarray, np.ndarray | None]]:
    """Lê LAS/LAZ por blocos sem materializar a nuvem inteira em RAM."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in {".las", ".laz"}:
        raise ValueError("iter_point_chunks suporta LAS/LAZ.")

    import laspy

    with laspy.open(path) as reader:
        has_cls = "classification" in {
            str(name).lower() for name in reader.header.point_format.dimension_names
        }
        for points in reader.chunk_iterator(int(chunk_size)):
            n = len(points)
            if n == 0:
                continue
            xyz = np.empty((n, 3), dtype=np.float64)
            xyz[:, 0] = np.asarray(points.x, dtype=np.float64)
            xyz[:, 1] = np.asarray(points.y, dtype=np.float64)
            xyz[:, 2] = np.asarray(points.z, dtype=np.float64)
            cls = (
                np.asarray(points.classification, dtype=np.uint8)
                if has_cls
                else None
            )
            yield xyz, cls


def load_point_cloud(path: str | Path) -> PointCloud:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in {".las", ".laz"}:
        import laspy

        las = laspy.read(path)
        xyz = np.column_stack((las.x, las.y, las.z)).astype(np.float64, copy=False)
        cls = np.asarray(las.classification, dtype=np.uint8) if hasattr(las, "classification") else None
        crs = las.header.parse_crs()
        return PointCloud(
            xyz=xyz,
            classification=cls,
            crs_wkt=crs.to_wkt() if crs else None,
            source=str(path),
        )

    if ext in {".xyz", ".txt", ".csv"}:
        delimiter = "," if ext == ".csv" else None
        data = np.genfromtxt(path, delimiter=delimiter, comments="#", dtype=float)
        if data.ndim == 1:
            data = data.reshape(1, -1)
        if data.shape[1] < 3:
            raise ValueError("O ficheiro XYZ/CSV precisa de pelo menos três colunas: X,Y,Z.")
        mask = np.isfinite(data[:, :3]).all(axis=1)
        return PointCloud(
            xyz=np.asarray(data[mask, :3], dtype=np.float64),
            source=str(path),
        )

    raise ValueError(
        f"Formato ainda não suportado na V1: {ext}. Use LAS, LAZ, XYZ, TXT ou CSV."
    )


def save_geojson(path: Path, lines: list[dict], crs_wkt: str | None = None) -> None:
    features = []
    for line in lines:
        coords = [[float(x), float(y), float(z)] for x, y, z in line["xyz"]]
        props = {k: v for k, v in line.items() if k not in {"xyz", "vertex_rmse"}}
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        )
    payload = {"type": "FeatureCollection", "features": features}
    if crs_wkt:
        payload["talude_v1_crs_wkt"] = crs_wkt
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_vertices_csv(path: Path, lines: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("line_id,face_id,type,vertex,x,y,z,confidence,rmse\n")
        for line in lines:
            rmses = line.get("vertex_rmse", [""] * len(line["xyz"]))
            for idx, ((x, y, z), rmse) in enumerate(
                zip(line["xyz"], rmses),
                start=1,
            ):
                rmse_text = "" if rmse == "" else f"{rmse:.4f}"
                f.write(
                    f'{line["line_id"]},{line["face_id"]},{line["type"]},{idx},'
                    f'{x:.4f},{y:.4f},{z:.4f},{line["confidence"]:.4f},{rmse_text}\n'
                )


def save_dxf(path: Path, lines: list[dict]) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for layer in ("CRISTA", "PE_TALUDE"):
        if layer not in doc.layers:
            doc.layers.add(layer)

    for line in lines:
        layer = "CRISTA" if line["type"] == "CREST" else "PE_TALUDE"
        msp.add_polyline3d(
            [tuple(map(float, p)) for p in line["xyz"]],
            dxfattribs={"layer": layer},
        )

    doc.saveas(path)
