from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from vietlegalcorpus.cli import app
from vietlegalcorpus.schemas.export import export_json_schemas

runner = CliRunner()
COMMITTED_SCHEMA_DIR = Path(__file__).parents[2] / "schemas" / "v1"


def test_export_is_deterministic_and_matches_committed_contract(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_paths = export_json_schemas(first)
    second_paths = export_json_schemas(second)

    assert [path.name for path in first_paths] == [path.name for path in second_paths]
    for first_path, second_path in zip(first_paths, second_paths, strict=True):
        assert first_path.read_bytes() == second_path.read_bytes()
        assert first_path.read_bytes() == (COMMITTED_SCHEMA_DIR / first_path.name).read_bytes()
        schema = json.loads(first_path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].startswith("urn:pio1:vietlegalcorpus:1.0.0:")
        assert schema["additionalProperties"] is False


def test_export_schemas_cli_writes_versioned_contract(tmp_path: Path) -> None:
    output_dir = tmp_path / "contract"

    result = runner.invoke(app, ["export-schemas", "--output-dir", str(output_dir)])

    assert result.exit_code == 0
    assert "Exported 7 JSON schemas" in result.stdout
    assert len(list(output_dir.glob("*.schema.json"))) == 7
