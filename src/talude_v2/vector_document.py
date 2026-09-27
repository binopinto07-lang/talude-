from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA = "talude-vector-document/v1"
DEFAULT_LAYERS = (
    {
        "id": "CRISTA",
        "name": "CRISTA",
        "geometry_type": "LineStringZ",
        "visible": True,
        "locked": False,
        "role": "breakline_crest",
    },
    {
        "id": "PE_TALUDE",
        "name": "PÉ DE TALUDE",
        "geometry_type": "LineStringZ",
        "visible": True,
        "locked": False,
        "role": "breakline_toe",
    },
    {
        "id": "FACES",
        "name": "FACES",
        "geometry_type": "PolygonZ",
        "visible": False,
        "locked": True,
        "role": "slope_faces",
    },
    {
        "id": "DEBUG",
        "name": "DEBUG",
        "geometry_type": "MixedZ",
        "visible": False,
        "locked": True,
        "role": "diagnostics",
    },
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _layer_for_type(feature_type: str) -> str:
    kind = str(feature_type or "").upper()
    return "CRISTA" if kind == "CREST" else "PE_TALUDE"


def _feature_id(line: dict[str, Any], index: int) -> str:
    face = line.get("face_id")
    kind = str(line.get("type") or "LINE").upper()
    line_id = line.get("line_id")
    # Human-readable prefix + UUID prevents collisions after editing/splitting.
    prefix = f"{kind}-{face if face is not None else 'X'}-{line_id if line_id is not None else index}"
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def feature_from_line(line: dict[str, Any], index: int) -> dict[str, Any]:
    xyz = line.get("xyz")
    if xyz is None:
        xyz = line.get("vertices")
    coords = np.asarray(xyz, dtype=np.float64)
    if coords.ndim != 2 or coords.shape[1] < 3 or len(coords) < 2:
        raise ValueError("Feature line precisa de pelo menos dois vértices XYZ.")

    props = {
        key: _jsonable(value)
        for key, value in line.items()
        if key not in {"xyz", "vertices", "vertex_rmse"}
    }
    kind = str(props.get("type") or "").upper()
    if kind not in {"CREST", "TOE"}:
        raise ValueError(f"Tipo de feature não suportado no documento: {kind!r}")

    return {
        "id": _feature_id(line, index),
        "layer_id": _layer_for_type(kind),
        "geometry": {
            "type": "LineString",
            "coordinates": _jsonable(coords[:, :3]),
            "has_z": True,
        },
        "properties": props,
        "visible": True,
        "locked": False,
        "selected": False,
        "revision": 1,
        "created_at": _now(),
        "updated_at": _now(),
    }


def create_vector_document(
    lines: list[dict[str, Any]],
    *,
    crs_wkt: str | None = None,
    source: dict[str, Any] | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    created = _now()
    features = [
        feature_from_line(line, index)
        for index, line in enumerate(lines, start=1)
    ]

    return {
        "schema": SCHEMA,
        "document_id": document_id or uuid.uuid4().hex,
        "revision": 1,
        "created_at": created,
        "updated_at": created,
        "crs_wkt": crs_wkt,
        "source": _jsonable(source or {}),
        "layers": [copy.deepcopy(layer) for layer in DEFAULT_LAYERS],
        "features": features,
        "history": [],
    }


def create_vector_document_from_geojson(
    payload: dict[str, Any],
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        props = dict(feature.get("properties") or {})
        props["vertices"] = coordinates
        lines.append(props)

    return create_vector_document(
        lines,
        crs_wkt=payload.get("talude_v1_crs_wkt"),
        source=source,
    )


def validate_vector_document(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("Vector document inválido.")
    if document.get("schema") != SCHEMA:
        raise ValueError(f"Schema vector document inválido: {document.get('schema')!r}")
    if not isinstance(document.get("layers"), list):
        raise ValueError("Vector document sem layers.")
    if not isinstance(document.get("features"), list):
        raise ValueError("Vector document sem features.")

    layer_ids = {
        str(layer.get("id"))
        for layer in document["layers"]
        if isinstance(layer, dict) and layer.get("id")
    }

    # Forward-compatible migration: documents created by phase 5 only contained
    # CRISTA and PE_TALUDE. Opening them in phases 6-9 must never fail merely
    # because the UI introduced FACES/DEBUG layers later.
    for default_layer in DEFAULT_LAYERS:
        layer_id = str(default_layer["id"])
        if layer_id not in layer_ids:
            document["layers"].append(copy.deepcopy(default_layer))
            layer_ids.add(layer_id)

    if not {"CRISTA", "PE_TALUDE", "FACES", "DEBUG"}.issubset(layer_ids):
        raise ValueError(
            "Vector document precisa das layers CRISTA, PE_TALUDE, FACES e DEBUG."
        )

    seen: set[str] = set()
    for feature in document["features"]:
        if not isinstance(feature, dict):
            raise ValueError("Feature vectorial inválida.")
        feature_id = str(feature.get("id") or "")
        if not feature_id or feature_id in seen:
            raise ValueError("IDs de features vazios ou duplicados.")
        seen.add(feature_id)

        if str(feature.get("layer_id")) not in layer_ids:
            raise ValueError(f"Feature {feature_id} aponta para layer inexistente.")

        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            raise ValueError(f"Feature {feature_id} não é LineString.")
        coords = np.asarray(geometry.get("coordinates") or [], dtype=np.float64)
        if coords.ndim != 2 or coords.shape[1] < 3 or len(coords) < 2:
            raise ValueError(f"Feature {feature_id} precisa de LineStringZ válida.")
        if not np.all(np.isfinite(coords[:, :3])):
            raise ValueError(f"Feature {feature_id} contém coordenadas não finitas.")

    return document


def save_vector_document(path: str | Path, document: dict[str, Any]) -> Path:
    doc = validate_vector_document(copy.deepcopy(document))
    doc["updated_at"] = _now()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(_jsonable(doc), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def load_vector_document(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    return validate_vector_document(document)


def vector_document_summary(document: dict[str, Any]) -> dict[str, Any]:
    doc = validate_vector_document(document)
    counts: dict[str, int] = {}
    for feature in doc["features"]:
        layer = str(feature["layer_id"])
        counts[layer] = counts.get(layer, 0) + 1
    return {
        "schema": doc["schema"],
        "document_id": doc["document_id"],
        "revision": int(doc.get("revision", 1)),
        "feature_count": int(len(doc["features"])),
        "layer_counts": counts,
        "crs_wkt": doc.get("crs_wkt"),
    }
