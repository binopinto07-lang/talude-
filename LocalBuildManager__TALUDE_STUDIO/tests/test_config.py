from __future__ import annotations

import json
from pathlib import Path

from app.core.config import discover_repo_local_configs, find_repo_local_config
from app.core.models import ProjectConfig


ROOT = Path(__file__).resolve().parents[1]


def _talude_v2_data() -> dict:
    return json.loads((ROOT / "projects" / "talude_v2.json").read_text(encoding="utf-8"))


def test_embedded_talude_v2_profile_matches_r14_contract():
    data = _talude_v2_data()
    cfg = ProjectConfig.from_dict(data)
    assert cfg.id == "talude_v2"
    assert cfg.branch == "v2-experimental-raw-tin-mst"
    assert data["config_revision"] >= 23
    assert data["required_source_revision"]["path"] == "localbuild/SOURCE_REVISION.txt"
    assert data["required_source_revision"]["value"] == "TALUDE_V2_BUILD_SOURCE_2026-09-30_R14"


def test_embedded_profile_requires_current_geometry_modules():
    data = _talude_v2_data()
    required = set(data["required_paths"])
    assert "src/talude_v2/section_edge_tracker.py" in required
    assert "src/talude_v2/plane_edge_snap.py" in required
    assert "src/talude_v2/endpoint_continuation.py" in required
    assert "scripts/build_windows.py" in required


def test_embedded_profile_uses_managed_python_and_optional_git():
    data = _talude_v2_data()
    checks = {item["id"]: item for item in data["checks"]}
    assert data["auto_bootstrap_python"] is True
    assert checks["python"]["command"] == "@lbm:python312"
    assert checks["git"]["command"] == "@lbm:git"
    assert checks["git"]["required"] is False


def test_repo_profile_discovery_works_for_github_zip(tmp_path):
    repo = tmp_path / "talude--2-experimental-raw-tin-mst"
    localbuild = repo / "localbuild"
    localbuild.mkdir(parents=True)
    profile = localbuild / "talude_v2.json"
    profile.write_text(json.dumps(_talude_v2_data()), encoding="utf-8")

    found = discover_repo_local_configs(repo)
    assert len(found) == 1
    assert found[0][1].id == "talude_v2"
    assert find_repo_local_config(repo, "talude_v2") == profile


def test_local_build_manager_is_talude_integrated():
    marker = (ROOT / "TALUDE_INTEGRATION.txt").read_text(encoding="utf-8")
    assert "LOCAL_BUILD_MANAGER_INTEGRATED_WITH_TALUDE_STUDIO" in marker
    assert "SOURCE_GUARD=TALUDE_V2_BUILD_SOURCE_2026-09-30_R14" in marker
