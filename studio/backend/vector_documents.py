from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from talude_v2.vector_document import (
    create_vector_document_from_geojson,
    load_vector_document,
    save_vector_document,
    vector_document_summary,
)


ACTIVE_RELATIVE = Path("vectors") / "vector_document.json"


def active_document_path(project_path: str | Path) -> Path:
    return Path(project_path) / ACTIVE_RELATIVE


def write_documents_from_geojson(
    project_path: str | Path,
    run_output_dir: str | Path,
    geojson_payload: dict[str, Any],
    *,
    source: dict[str, Any],
) -> dict[str, Any]:
    document = create_vector_document_from_geojson(
        geojson_payload,
        source=source,
    )
    run_path = Path(run_output_dir) / "vector_document.json"
    active_path = active_document_path(project_path)

    save_vector_document(run_path, document)
    save_vector_document(active_path, document)

    return {
        "document": document,
        "run_path": str(run_path),
        "active_path": str(active_path),
        "summary": vector_document_summary(document),
    }


def read_active_document(project_path: str | Path) -> dict[str, Any]:
    path = active_document_path(project_path)
    if not path.exists():
        raise FileNotFoundError("O projeto ainda não possui Vector Document ativo.")
    return load_vector_document(path)


def active_document_info(project_path: str | Path) -> dict[str, Any]:
    path = active_document_path(project_path)
    if not path.exists():
        return {
            "exists": False,
            "path": str(path),
            "summary": None,
        }

    document = load_vector_document(path)
    return {
        "exists": True,
        "path": str(path),
        "summary": vector_document_summary(document),
    }
