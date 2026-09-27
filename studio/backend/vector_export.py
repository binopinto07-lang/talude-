from __future__ import annotations

import json
import math
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

import ezdxf
from pyproj import CRS

from talude_v2.vector_document import validate_vector_document


def _safe_name(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(value))
    text = text.strip("_")
    return text or "LAYER"


def _line_length(coords: list[list[float]]) -> float:
    total = 0.0
    for a, b in zip(coords, coords[1:]):
        dx = float(b[0]) - float(a[0])
        dy = float(b[1]) - float(a[1])
        dz = float(b[2]) - float(a[2])
        total += math.sqrt(dx * dx + dy * dy + dz * dz)
    return float(total)


def select_features(
    document: dict[str, Any],
    *,
    layer_ids: list[str] | None = None,
    feature_ids: list[str] | None = None,
    visible_only: bool = False,
    selected_only: bool = False,
) -> list[dict[str, Any]]:
    doc = validate_vector_document(document)
    allowed_layers = {str(v) for v in layer_ids} if layer_ids else None
    allowed_features = {str(v) for v in feature_ids} if feature_ids else None
    layer_visibility = {
        str(layer.get("id")): bool(layer.get("visible", True))
        for layer in doc.get("layers", [])
    }

    out = []
    for feature in doc.get("features", []):
        layer_id = str(feature.get("layer_id"))
        feature_id = str(feature.get("id"))
        if allowed_layers is not None and layer_id not in allowed_layers:
            continue
        if allowed_features is not None and feature_id not in allowed_features:
            continue
        if visible_only and (
            not bool(feature.get("visible", True))
            or not layer_visibility.get(layer_id, True)
        ):
            continue
        if selected_only and not bool(feature.get("selected", False)):
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        coords = geometry.get("coordinates") or []
        if len(coords) < 2:
            continue
        out.append(feature)
    return out


def _feature_props(feature: dict[str, Any]) -> dict[str, Any]:
    props = dict(feature.get("properties") or {})
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    return {
        "feature_id": str(feature.get("id") or ""),
        "layer_id": str(feature.get("layer_id") or ""),
        "face_id": props.get("face_id"),
        "type": props.get("type"),
        "confidence": props.get("confidence"),
        "quality_score": props.get("quality_score"),
        "length_m": props.get("length_m") or _line_length(coords),
        "source": props.get("source"),
        "properties": props,
    }


def export_geojson(
    path: Path,
    document: dict[str, Any],
    features: list[dict[str, Any]],
) -> Path:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": feature.get("id"),
                "properties": {
                    **dict(feature.get("properties") or {}),
                    "_layer": feature.get("layer_id"),
                    "_visible": bool(feature.get("visible", True)),
                    "_locked": bool(feature.get("locked", False)),
                },
                "geometry": feature.get("geometry"),
            }
            for feature in features
        ],
    }
    if document.get("crs_wkt"):
        payload["talude_crs_wkt"] = document["crs_wkt"]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_dxf(
    path: Path,
    document: dict[str, Any],
    features: list[dict[str, Any]],
) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    layers = {
        str(feature.get("layer_id") or "FEATURES")
        for feature in features
    }
    for layer in sorted(layers):
        if layer not in doc.layers:
            doc.layers.add(layer)

    for feature in features:
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        layer = str(feature.get("layer_id") or "FEATURES")
        entity = msp.add_polyline3d(
            [tuple(float(v) for v in point[:3]) for point in coords],
            dxfattribs={"layer": layer},
        )
        try:
            entity.dxf.color = 2 if layer == "CRISTA" else 4 if layer == "PE_TALUDE" else 7
        except Exception:
            pass

    doc.saveas(path)
    return path


def _bbox(features: list[dict[str, Any]]) -> tuple[float, float, float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for feature in features:
        for point in (feature.get("geometry") or {}).get("coordinates") or []:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
            zs.append(float(point[2]))
    if not xs:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return min(xs), min(ys), max(xs), max(ys), min(zs), max(zs)


def _shp_header(
    *,
    file_length_words: int,
    shape_type: int,
    bbox: tuple[float, float, float, float, float, float],
) -> bytes:
    xmin, ymin, xmax, ymax, zmin, zmax = bbox
    return (
        struct.pack(">7i", 9994, 0, 0, 0, 0, 0, int(file_length_words))
        + struct.pack("<2i", 1000, int(shape_type))
        + struct.pack("<8d", xmin, ymin, xmax, ymax, zmin, zmax, 0.0, 0.0)
    )


def _polylinez_record(coords: list[list[float]]) -> bytes:
    points = [(float(p[0]), float(p[1]), float(p[2])) for p in coords]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    body = bytearray()
    body.extend(struct.pack("<i", 13))
    body.extend(struct.pack("<4d", min(xs), min(ys), max(xs), max(ys)))
    body.extend(struct.pack("<2i", 1, len(points)))
    body.extend(struct.pack("<i", 0))
    for x, y, _ in points:
        body.extend(struct.pack("<2d", x, y))
    body.extend(struct.pack("<2d", min(zs), max(zs)))
    for z in zs:
        body.extend(struct.pack("<d", z))
    body.extend(struct.pack("<2d", 0.0, 0.0))
    for _ in points:
        body.extend(struct.pack("<d", 0.0))
    return bytes(body)


def _write_dbf(path: Path, features: list[dict[str, Any]]) -> None:
    # dBase III, deliberately tiny and dependency-free.
    fields = [
        ("FID", "C", 36, 0),
        ("FACE_ID", "N", 12, 0),
        ("TYPE", "C", 12, 0),
        ("CONF", "N", 10, 4),
        ("QUALITY", "N", 10, 4),
        ("LENGTH_M", "N", 14, 3),
        ("SOURCE", "C", 40, 0),
    ]
    now = datetime.now()
    record_count = len(features)
    header_len = 32 + 32 * len(fields) + 1
    record_len = 1 + sum(field[2] for field in fields)

    with path.open("wb") as fh:
        fh.write(
            struct.pack(
                "<BBBBIHH20x",
                3,
                max(0, now.year - 1900),
                now.month,
                now.day,
                record_count,
                header_len,
                record_len,
            )
        )
        offset = 1
        for name, field_type, length, decimals in fields:
            raw_name = name.encode("ascii", "ignore")[:10]
            fh.write(raw_name + b"\x00" * (11 - len(raw_name)))
            fh.write(field_type.encode("ascii"))
            fh.write(struct.pack("<I", offset))
            fh.write(struct.pack("BB", length, decimals))
            fh.write(b"\x00" * 14)
            offset += length
        fh.write(b"\x0d")

        for feature in features:
            props = _feature_props(feature)
            values = [
                props["feature_id"],
                props["face_id"],
                props["type"],
                props["confidence"],
                props["quality_score"],
                props["length_m"],
                props["source"],
            ]
            fh.write(b" ")
            for (name, field_type, length, decimals), value in zip(fields, values):
                if field_type == "C":
                    raw = ("" if value is None else str(value)).encode("utf-8")[:length]
                    fh.write(raw.ljust(length, b" "))
                else:
                    if value is None or value == "":
                        text = ""
                    elif decimals:
                        text = f"{float(value):.{decimals}f}"
                    else:
                        text = str(int(value))
                    raw = text.encode("ascii", "ignore")[-length:]
                    fh.write(raw.rjust(length, b" "))
        fh.write(b"\x1a")


def export_shapefile_layer(
    base_path: Path,
    document: dict[str, Any],
    features: list[dict[str, Any]],
) -> list[Path]:
    if not features:
        return []

    records = [
        _polylinez_record((feature.get("geometry") or {}).get("coordinates") or [])
        for feature in features
    ]
    overall_bbox = _bbox(features)

    shp_record_chunks = []
    shx_record_chunks = []
    offset_words = 50  # 100-byte SHP header.
    for index, body in enumerate(records, start=1):
        content_words = len(body) // 2
        shp_record_chunks.append(struct.pack(">2i", index, content_words) + body)
        shx_record_chunks.append(struct.pack(">2i", offset_words, content_words))
        offset_words += 4 + content_words  # 8-byte record header + content.

    shp_path = base_path.with_suffix(".shp")
    shx_path = base_path.with_suffix(".shx")
    dbf_path = base_path.with_suffix(".dbf")
    prj_path = base_path.with_suffix(".prj")
    cpg_path = base_path.with_suffix(".cpg")

    shp_length_words = 50 + sum(len(chunk) // 2 for chunk in shp_record_chunks)
    shx_length_words = 50 + sum(len(chunk) // 2 for chunk in shx_record_chunks)

    with shp_path.open("wb") as fh:
        fh.write(
            _shp_header(
                file_length_words=shp_length_words,
                shape_type=13,
                bbox=overall_bbox,
            )
        )
        for chunk in shp_record_chunks:
            fh.write(chunk)

    with shx_path.open("wb") as fh:
        fh.write(
            _shp_header(
                file_length_words=shx_length_words,
                shape_type=13,
                bbox=overall_bbox,
            )
        )
        for chunk in shx_record_chunks:
            fh.write(chunk)

    _write_dbf(dbf_path, features)
    prj_path.write_text(str(document.get("crs_wkt") or ""), encoding="utf-8")
    cpg_path.write_text("UTF-8\n", encoding="ascii")
    return [shp_path, shx_path, dbf_path, prj_path, cpg_path]


def export_shapefiles(
    folder: Path,
    document: dict[str, Any],
    features: list[dict[str, Any]],
) -> list[Path]:
    paths: list[Path] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        grouped.setdefault(str(feature.get("layer_id") or "FEATURES"), []).append(feature)
    for layer_id, items in sorted(grouped.items()):
        paths.extend(
            export_shapefile_layer(
                folder / _safe_name(layer_id),
                document,
                items,
            )
        )
    return paths


def _wkb_linestring_z(coords: list[list[float]]) -> bytes:
    body = bytearray()
    body.extend(struct.pack("<BI", 1, 1002))
    body.extend(struct.pack("<I", len(coords)))
    for point in coords:
        body.extend(struct.pack("<3d", float(point[0]), float(point[1]), float(point[2])))
    return bytes(body)


def _gpkg_geom(coords: list[list[float]], srs_id: int) -> bytes:
    # GeoPackageBinary header, little endian, no envelope + ISO WKB LineStringZ.
    return b"GP" + bytes((0, 1)) + struct.pack("<i", int(srs_id)) + _wkb_linestring_z(coords)


def _resolve_srs(crs_wkt: str | None) -> tuple[int, str, int, str]:
    if not crs_wkt:
        return 0, "NONE", 0, "undefined"
    try:
        crs = CRS.from_wkt(crs_wkt)
        authority = crs.to_authority()
        if authority and authority[0].upper() == "EPSG":
            code = int(authority[1])
            return code, "EPSG", code, crs.to_wkt()
    except Exception:
        pass
    return 99999, "NONE", 99999, str(crs_wkt)


def export_gpkg(
    path: Path,
    document: dict[str, Any],
    features: list[dict[str, Any]],
) -> Path:
    if path.exists():
        path.unlink()

    srs_id, organization, org_id, definition = _resolve_srs(document.get("crs_wkt"))
    connection = sqlite3.connect(path)
    try:
        cur = connection.cursor()
        cur.execute("PRAGMA application_id=1196444487")
        cur.execute("PRAGMA user_version=10300")
        cur.executescript(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
              srs_name TEXT NOT NULL,
              srs_id INTEGER NOT NULL PRIMARY KEY,
              organization TEXT NOT NULL,
              organization_coordsys_id INTEGER NOT NULL,
              definition TEXT NOT NULL,
              description TEXT
            );
            CREATE TABLE gpkg_contents (
              table_name TEXT NOT NULL PRIMARY KEY,
              data_type TEXT NOT NULL,
              identifier TEXT UNIQUE,
              description TEXT DEFAULT '',
              last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
              min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
              srs_id INTEGER
            );
            CREATE TABLE gpkg_geometry_columns (
              table_name TEXT NOT NULL,
              column_name TEXT NOT NULL,
              geometry_type_name TEXT NOT NULL,
              srs_id INTEGER NOT NULL,
              z TINYINT NOT NULL,
              m TINYINT NOT NULL,
              PRIMARY KEY (table_name, column_name)
            );
            """
        )
        cur.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            (
                "Undefined Cartesian" if srs_id == 0 else f"CRS {srs_id}",
                srs_id,
                organization,
                org_id,
                definition,
                "Talude Studio export",
            ),
        )

        grouped: dict[str, list[dict[str, Any]]] = {}
        for feature in features:
            grouped.setdefault(str(feature.get("layer_id") or "FEATURES"), []).append(feature)

        for layer_id, items in sorted(grouped.items()):
            table = _safe_name(layer_id).lower()
            cur.execute(
                f"""
                CREATE TABLE "{table}" (
                  fid INTEGER PRIMARY KEY AUTOINCREMENT,
                  geom BLOB NOT NULL,
                  feature_id TEXT,
                  face_id INTEGER,
                  type TEXT,
                  confidence REAL,
                  quality REAL,
                  length_m REAL,
                  source TEXT,
                  properties TEXT
                )
                """
            )
            bbox = _bbox(items)
            cur.execute(
                "INSERT INTO gpkg_contents "
                "(table_name,data_type,identifier,description,min_x,min_y,max_x,max_y,srs_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    table,
                    "features",
                    layer_id,
                    "Talude Studio 3D breaklines",
                    bbox[0],
                    bbox[1],
                    bbox[2],
                    bbox[3],
                    srs_id,
                ),
            )
            cur.execute(
                "INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
                (table, "geom", "LINESTRING", srs_id, 1, 0),
            )

            for feature in items:
                coords = (feature.get("geometry") or {}).get("coordinates") or []
                props = _feature_props(feature)
                cur.execute(
                    f'INSERT INTO "{table}" '
                    "(geom,feature_id,face_id,type,confidence,quality,length_m,source,properties) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        sqlite3.Binary(_gpkg_geom(coords, srs_id)),
                        props["feature_id"],
                        props["face_id"],
                        props["type"],
                        props["confidence"],
                        props["quality_score"],
                        props["length_m"],
                        props["source"],
                        json.dumps(props["properties"], ensure_ascii=False),
                    ),
                )

        connection.commit()
    finally:
        connection.close()
    return path


def export_vector_document(
    project_path: str | Path,
    document: dict[str, Any],
    *,
    formats: list[str] | None = None,
    layer_ids: list[str] | None = None,
    feature_ids: list[str] | None = None,
    visible_only: bool = False,
    selected_only: bool = False,
) -> dict[str, Any]:
    document = validate_vector_document(document)
    formats = [str(v).lower() for v in (formats or ["dxf", "shp", "gpkg"])]
    features = select_features(
        document,
        layer_ids=layer_ids,
        feature_ids=feature_ids,
        visible_only=visible_only,
        selected_only=selected_only,
    )
    if not features:
        raise ValueError("Nenhuma feature cumpre os filtros de exportação.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = Path(project_path) / "exports" / f"vector_export_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    if "dxf" in formats:
        paths.append(export_dxf(folder / "talude_breaklines_3d.dxf", document, features))
    if "geojson" in formats:
        paths.append(export_geojson(folder / "talude_breaklines_3d.geojson", document, features))
    if "shp" in formats:
        paths.extend(export_shapefiles(folder, document, features))
    if "gpkg" in formats:
        paths.append(export_gpkg(folder / "talude_breaklines_3d.gpkg", document, features))

    manifest = {
        "schema": "talude-vector-export/v1",
        "document_id": document.get("document_id"),
        "document_revision": document.get("revision"),
        "feature_count": len(features),
        "formats": formats,
        "layer_ids": layer_ids,
        "feature_ids": feature_ids,
        "visible_only": bool(visible_only),
        "selected_only": bool(selected_only),
        "files": [str(path) for path in paths],
    }
    manifest_path = folder / "export_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths.append(manifest_path)

    return {
        "output_dir": str(folder),
        "feature_count": len(features),
        "files": [str(path) for path in paths],
        "manifest": manifest,
    }
