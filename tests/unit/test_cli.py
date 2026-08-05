from __future__ import annotations

from typer.testing import CliRunner

from vietlegalcorpus import __version__
from vietlegalcorpus.cli import app

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
