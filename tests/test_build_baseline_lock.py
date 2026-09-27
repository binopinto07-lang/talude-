import json
from copy import deepcopy
from pathlib import Path


def _normalise_v2(cfg):
    cfg = deepcopy(cfg)
    cfg["branch"] = "main"
    extra = " --hidden-import talude_v2 --hidden-import talude_v2.engine"
    for pipeline in cfg.get("pipelines", {}).values():
        for step in pipeline:
            if "command" in step:
                step["command"] = step["command"].replace(extra, "")
    return cfg


def test_v2_build_pipeline_is_locked_to_known_good_117():
    current = json.loads(
        Path("localbuild/talude_v1.json").read_text(encoding="utf-8")
    )
    baseline = json.loads(
        Path("localbuild/talude_v1_baseline_117.json").read_text(encoding="utf-8")
    )

    assert current["name"] == baseline["name"]
    assert current["id"] == baseline["id"]
    assert current["dist_dir_name"] == baseline["dist_dir_name"]
    assert current["branch"] == "v2"

    # V2 may add only its two explicit hidden imports and its branch name.
    # Any other build-pipeline drift must fail here before PyInstaller runs.
    assert _normalise_v2(current) == baseline
