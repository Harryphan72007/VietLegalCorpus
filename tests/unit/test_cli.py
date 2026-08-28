from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from vietlegalcorpus import __version__
from vietlegalcorpus.cli import _check_writable, app

runner = CliRunner()


def test_version_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_doctor_runs_and_reports_environment() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "python:" in result.stdout
    assert "vietlegalcorpus" in result.stdout


def test_writable_check_preserves_existing_probe_file(tmp_path: Path) -> None:
    probe = tmp_path / ".write_probe"
    probe.write_text("keep me", encoding="utf-8")

    assert _check_writable(tmp_path)
    assert probe.read_text(encoding="utf-8") == "keep me"
    assert list(tmp_path.iterdir()) == [probe]
