from pathlib import Path


def test_embedded_classify_las_r20_4_module_contract():
    root = Path("ALGORITM/CLASSIFY_LAS")
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "R20.4"
    assert (root / "api.py").is_file()
    assert (root / "las_classifier/classifiers/universal_ground.py").is_file()
    assert (root / "las_classifier/terrain/mdt_export.py").is_file()
    assert (root / "las_classifier/terrain/inverted_mantle.py").is_file()
    assert (root / "las_classifier/terrain/ground_continuity.py").is_file()
    assert (root / "las_classifier/terrain/mantle_veto.py").is_file()


def test_no_simplified_classifier_is_used_by_talude_backend():
    service = Path("studio/backend/classify_las_service.py").read_text(encoding="utf-8")
    api = Path("ALGORITM/CLASSIFY_LAS/api.py").read_text(encoding="utf-8")
    assert "classify_and_create_mdt" in service
    assert "run_universal_ground" in api
    assert "export_classified" in api
    assert "export_ground_only" in api
    assert "export_ground_mdt" in api
