"""vlc command-line interface (Typer)."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Annotated

import typer

from vietlegalcorpus import __version__
from vietlegalcorpus.config import load_settings
from vietlegalcorpus.logging import configure_logging
from vietlegalcorpus.schemas.export import export_json_schemas

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
