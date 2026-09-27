from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from talude_v2.vector_document import (
    create_vector_document_from_geojson,
    load_vector_document,
    save_vector_document,
    validate_vector_document,
    vector_document_summary,
)


ACTIVE_RELATIVE = Path("vectors") / "vector_document.json"
AUTOSAVE_RELATIVE = Path("vectors") / "vector_document.autosave.json"
REVISIONS_RELATIVE = Path("vectors") / "revisions"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def active_document_path(project_path: str | Path) -> Path:
    return Path(project_path) / ACTIVE_RELATIVE


def autosave_document_path(project_path: str | Path) -> Path:
    return Path(project_path) / AUTOSAVE_RELATIVE


def revisions_dir(project_path: str | Path) -> Path:
    path = Path(project_path) / REVISIONS_RELATIVE
    path.mkdir(parents=True, exist_ok=True)
    return path


def _revision_path(project_path: str | Path, revision: int) -> Path:
    return revisions_dir(project_path) / f"rev_{int(revision):06d}.json"


def _checkpoint(project_path: str | Path, document: dict[str, Any]) -> Path:
    revision = int(document.get("revision", 1))
    path = _revision_path(project_path, revision)
    save_vector_document(path, document)

    files = sorted(revisions_dir(project_path).glob("rev_*.json"))
    # Keep the latest revisions bounded so autosave cannot grow indefinitely.
    for stale in files[:-40]:
        try:
            stale.unlink()
        except OSError:
            pass
    return path


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
    autosave_path = autosave_document_path(project_path)

    save_vector_document(run_path, document)
    save_vector_document(active_path, document)
    save_vector_document(autosave_path, document)
    revision_path = _checkpoint(project_path, document)

    return {
        "document": document,
        "run_path": str(run_path),
        "active_path": str(active_path),
        "autosave_path": str(autosave_path),
        "revision_path": str(revision_path),
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
            "autosave_exists": autosave_document_path(project_path).exists(),
        }

    document = load_vector_document(path)
    return {
        "exists": True,
        "path": str(path),
        "summary": vector_document_summary(document),
        "autosave_exists": autosave_document_path(project_path).exists(),
    }


def save_active_document(
    project_path: str | Path,
    document: dict[str, Any],
    *,
    expected_revision: int | None,
    reason: str = "edit",
) -> dict[str, Any]:
    project_path = Path(project_path)
    active_path = active_document_path(project_path)

    if active_path.exists():
        current = load_vector_document(active_path)
        current_revision = int(current.get("revision", 1))
    else:
        current = None
        current_revision = 0

    if expected_revision is not None and int(expected_revision) != current_revision:
        raise RuntimeError(
            f"REVISION_CONFLICT:{current_revision}:{int(expected_revision)}"
        )

    doc = validate_vector_document(copy.deepcopy(document))
    new_revision = current_revision + 1
    doc["revision"] = new_revision
    doc["updated_at"] = _now()

    history = list(doc.get("history") or [])
    history.append(
        {
            "revision": new_revision,
            "saved_at": doc["updated_at"],
            "reason": str(reason or "edit"),
        }
    )
    doc["history"] = history[-100:]

    save_vector_document(active_path, doc)
    save_vector_document(autosave_document_path(project_path), doc)
    revision_path = _checkpoint(project_path, doc)

    return {
        "document": doc,
        "path": str(active_path),
        "revision_path": str(revision_path),
        "summary": vector_document_summary(doc),
    }


def autosave_active_document(
    project_path: str | Path,
    document: dict[str, Any],
    *,
    expected_revision: int | None,
    reason: str = "autosave",
) -> dict[str, Any]:
    # Autosave is intentionally a real revision. This makes crash recovery and
    # undo across restarts deterministic instead of maintaining a second hidden
    # state machine.
    return save_active_document(
        project_path,
        document,
        expected_revision=expected_revision,
        reason=reason,
    )


def list_revisions(project_path: str | Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(revisions_dir(project_path).glob("rev_*.json"), reverse=True):
        try:
            doc = load_vector_document(path)
        except Exception:
            continue
        result.append(
            {
                "revision": int(doc.get("revision", 0)),
                "updated_at": doc.get("updated_at"),
                "feature_count": len(doc.get("features") or []),
                "path": str(path),
            }
        )
    return result


def recover_revision(
    project_path: str | Path,
    revision: int | None = None,
) -> dict[str, Any]:
    project_path = Path(project_path)
    active = read_active_document(project_path)
    current_revision = int(active.get("revision", 1))

    if revision is None:
        revisions = list_revisions(project_path)
        older = [item for item in revisions if int(item["revision"]) < current_revision]
        if not older:
            raise FileNotFoundError("Não existe revisão anterior para recuperar.")
        revision = int(older[0]["revision"])

    source_path = _revision_path(project_path, int(revision))
    if not source_path.exists():
        raise FileNotFoundError(f"Revisão {revision} não encontrada.")

    recovered = load_vector_document(source_path)
    recovered["revision"] = current_revision
    return save_active_document(
        project_path,
        recovered,
        expected_revision=current_revision,
        reason=f"recover:{int(revision)}",
    )


def recover_autosave(project_path: str | Path) -> dict[str, Any]:
    project_path = Path(project_path)
    autosave = autosave_document_path(project_path)
    if not autosave.exists():
        raise FileNotFoundError("Autosave não encontrado.")

    current = read_active_document(project_path)
    current_revision = int(current.get("revision", 1))
    recovered = load_vector_document(autosave)
    recovered["revision"] = current_revision

    return save_active_document(
        project_path,
        recovered,
        expected_revision=current_revision,
        reason="recover:autosave",
    )
