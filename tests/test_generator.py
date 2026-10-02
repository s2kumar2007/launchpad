import json
from pathlib import Path
import pytest
from generator.cli import build, validate_addon, main

@pytest.mark.parametrize("name", ["salon", "clinic", "restaurant"])
def test_build_all_examples(tmp_path, name):
    r = build(f"examples/{name}.yaml", tmp_path)
    a = json.loads(Path(r["addon"]).read_text(encoding="utf-8")); validate_addon(a)
    assert (Path(r["skill"]) / "SKILL.md").exists() and (Path(r["server"]) / "Dockerfile").exists()
    assert len(list((Path(r["addon"]).parent / "assets").glob("icon-*.png"))) == 6

def test_addon_limits_enforced():
    with pytest.raises(ValueError): validate_addon({"storeListing": {"name": "x" * 31, "shortDescription": "s", "fullDescription": "f", "examplePhrases": ["a"] * 3, "distributionCountries": ["US"]}, "integrations": [{"type": "MCP"}]})

def test_cli_build_and_bad_yaml(tmp_path):
    assert main(["build", "examples/salon.yaml", "-o", str(tmp_path / "o")]) == 0
    bad = tmp_path / "bad.yaml"; bad.write_text("name: x\n", encoding="utf-8")
    assert main(["build", str(bad), "-o", str(tmp_path / "o2")]) == 1
