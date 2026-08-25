"""vlc command-line interface (Typer)."""

from __future__ import annotations

import json
import platform
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer

from vietlegalcorpus import __version__
from vietlegalcorpus.config import load_settings
from vietlegalcorpus.logging import configure_logging
from vietlegalcorpus.quality import evaluate_bundle, read_bundle
from vietlegalcorpus.schemas.export import export_json_schemas
from vietlegalcorpus.snapshot import build_snapshot as build_corpus_snapshot
from vietlegalcorpus.snapshot import validate_snapshot as validate_corpus_snapshot
from vietlegalcorpus.source_review import read_source_review

app = typer.Typer(add_completion=False, help="VietLegalCorpus CLI.")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Report the runtime environment and check that data directories are writable."""
    settings = load_settings()
    configure_logging(settings.log_level)
    typer.echo(f"package:  vietlegalcorpus {__version__}")
    typer.echo(f"python:   {platform.python_version()} ({platform.platform()})")
    typer.echo(f"data_dir: {settings.data_dir.resolve()}")
    for label, path in (
        ("raw", settings.raw_dir),
        ("processed", settings.processed_dir),
        ("samples", settings.samples_dir),
        ("out", settings.out_dir),
    ):
        status = "ok" if _check_writable(path) else "NOT writable"
        typer.echo(f"  {label:<10} {path} -> {status}")


@app.command("export-schemas")
def export_schemas(
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Directory for deterministic CorpusSnapshot v1 JSON Schemas.",
        ),
    ] = Path("schemas/v1"),
) -> None:
    """Export the versioned corpus contract as JSON Schema files."""
    exported = export_json_schemas(output_dir)
    typer.echo(f"Exported {len(exported)} JSON schemas to {output_dir}")


@app.command()
def evaluate(corpus_dir: Path) -> None:
    """Run real corpus invariant checks and emit a deterministic JSON report."""
    report = evaluate_bundle(read_bundle(corpus_dir))
    typer.echo(report.to_json(), nl=False)
    if not report.passed:
        raise typer.Exit(code=1)


@app.command("build-snapshot")
def build_snapshot_command(
    corpus_dir: Path,
    output_dir: Path,
    corpus_id: Annotated[str, typer.Option("--corpus-id")],
    created_at: Annotated[str, typer.Option("--created-at")],
    review_date: Annotated[str, typer.Option("--review-date")],
    config_sha256: Annotated[str, typer.Option("--config-sha256")],
    generator_version: Annotated[str, typer.Option("--generator-version")] = (
        "vietlegalcorpus/0.1.0"
    ),
    source_review: Annotated[
        Path | None,
        typer.Option(
            "--source-review",
            help="Validated official-source review record required for G1 readiness.",
        ),
    ] = None,
) -> None:
    """Build a deterministic CorpusSnapshot v1 from a validated bundle."""
    result = build_corpus_snapshot(
        read_bundle(corpus_dir),
        output_dir,
        corpus_id=corpus_id,
        created_at=datetime.fromisoformat(created_at),
        review_date=date.fromisoformat(review_date),
        generator_version=generator_version,
        config_sha256=config_sha256,
        official_source_review=(read_source_review(source_review) if source_review else None),
    )
    typer.echo(
        json.dumps(
            {
                "ready": result.readiness.ready,
                "snapshot_sha256": result.snapshot_sha256,
                "technical_checks_passed": result.readiness.technical_checks_passed,
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("validate-snapshot")
def validate_snapshot_command(snapshot_dir: Path) -> None:
    """Validate all files and hashes in a CorpusSnapshot v1."""
    result = validate_corpus_snapshot(snapshot_dir)
    typer.echo(json.dumps({"snapshot_sha256": result.snapshot_sha256}, indent=2, sort_keys=True))


def _check_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    app()
