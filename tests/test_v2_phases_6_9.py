from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

import ezdxf
import pytest

from studio.backend.vector_documents import (
    active_document_path,
    list_revisions,
    recover_revision,
    save_active_document,
)
from studio.backend.vector_export import export_vector_document
from talude_v2.vector_document import create_vector_document, save_vector_document


def _document() -> dict:
    crest = [[0.0, 0.0, 10.0], [5.0, 0.5, 10.2], [10.0, 1.0, 10.4]]
    toe = [[0.0, 5.0, 5.0], [5.0, 5.5, 5.1], [10.0, 6.0, 5.2]]
    return create_vector_document(
        [
            {
                "line_id": 1,
                "face_id": 1,
                "type": "CREST",
                "xyz": crest,
                "confidence": 0.91,
                "quality_score": 0.88,
                "source": "TEST",
            },
            {
                "line_id": 2,
                "face_id": 1,
                "type": "TOE",
                "xyz": toe,
                "confidence": 0.89,
                "quality_score": 0.86,
                "source": "TEST",
            },
        ],
        crs_wkt=None,
        source={"engine": "test"},
    )


def test_phase6_vector_document_has_four_editor_layers():
    doc = _document()
    layers = {item["id"]: item for item in doc["layers"]}

    assert {"CRISTA", "PE_TALUDE", "FACES", "DEBUG"}.issubset(layers)
    assert layers["CRISTA"]["visible"] is True
    assert layers["PE_TALUDE"]["visible"] is True
    assert layers["FACES"]["locked"] is True
    assert layers["DEBUG"]["locked"] is True


def test_phase8_save_autosave_revision_and_recovery(tmp_path):
    project = tmp_path / "project"
    (project / "vectors").mkdir(parents=True)
    doc = _document()
    save_vector_document(active_document_path(project), doc)

    edited = json.loads(json.dumps(doc))
    edited["features"][0]["geometry"]["coordinates"][1][0] = 5.25

    saved = save_active_document(
        project,
        edited,
        expected_revision=1,
        reason="move_vertex",
    )
    assert saved["summary"]["revision"] == 2
    assert len(list_revisions(project)) >= 1

    edited2 = json.loads(json.dumps(saved["document"]))
    edited2["features"][0]["geometry"]["coordinates"][1][0] = 5.50
    saved2 = save_active_document(
        project,
        edited2,
        expected_revision=2,
        reason="move_vertex_again",
    )
    assert saved2["summary"]["revision"] == 3

    recovered = recover_revision(project, revision=2)
    assert recovered["summary"]["revision"] == 4
    assert recovered["document"]["features"][0]["geometry"]["coordinates"][1][0] == pytest.approx(5.25)

    with pytest.raises(RuntimeError, match="REVISION_CONFLICT"):
        save_active_document(
            project,
            recovered["document"],
            expected_revision=2,
            reason="stale",
        )


def test_phase9_exports_valid_dxf_shp_and_gpkg_without_heavy_gis_dependencies(tmp_path):
    project = tmp_path / "project"
    (project / "exports").mkdir(parents=True)
    doc = _document()

    result = export_vector_document(
        project,
        doc,
        formats=["dxf", "shp", "gpkg", "geojson"],
    )

    folder = Path(result["output_dir"])
    assert folder.exists()
    assert result["feature_count"] == 2

    dxf = folder / "talude_breaklines_3d.dxf"
    drawing = ezdxf.readfile(dxf)
    entities = list(drawing.modelspace().query("POLYLINE"))
    assert len(entities) == 2

    crest_shp = folder / "CRISTA.shp"
    crest_shx = folder / "CRISTA.shx"
    crest_dbf = folder / "CRISTA.dbf"
    assert crest_shp.exists() and crest_shx.exists() and crest_dbf.exists()

    raw = crest_shp.read_bytes()
    assert struct.unpack(">i", raw[:4])[0] == 9994
    assert struct.unpack("<i", raw[32:36])[0] == 13

    gpkg = folder / "talude_breaklines_3d.gpkg"
    with sqlite3.connect(gpkg) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "gpkg_contents" in tables
        assert "gpkg_geometry_columns" in tables
        assert "crista" in tables
        assert "pe_talude" in tables

        blob = connection.execute('SELECT geom FROM "crista" LIMIT 1').fetchone()[0]
        assert bytes(blob[:2]) == b"GP"


def test_phase6_9_viewer_contract_is_present():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    js = Path("studio/viewer/vector_editor.js").read_text(encoding="utf-8")
    app = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")

    for control in (
        'id="vectorLayers"',
        'id="vectorMoveVertex"',
        'id="vectorInsertVertex"',
        'id="vectorDeleteVertex"',
        'id="vectorDeleteLine"',
        'id="vectorNewLine"',
        'id="vectorUndo"',
        'id="vectorRedo"',
        'id="vectorRecover"',
        'id="vectorExport"',
    ):
        assert control in html

    assert '<script src="./vector_editor.js"></script>' in html
    assert "talude:editor-pick" in js
    assert "armEditorPick" in app
    assert "talude:project-activated" in app

    assert '@app.put("/api/projects/{project_id}/vector-document")' in server
    assert '@app.put("/api/projects/{project_id}/vector-document/autosave")' in server
    assert '@app.post("/api/projects/{project_id}/vector-document/recover")' in server
    assert '@app.post("/api/projects/{project_id}/vector-document/export")' in server
