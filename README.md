# VietLegalCorpus

Ingestion, parsing, and structuring of Vietnamese legal documents into clause-level JSONL.

> Status: **development (v0.1.0)** — local ingestion, text/HTML/DOCX/PDF parsing,
> provision hierarchy, temporal eligibility, quality evaluation, and reproducible snapshots.
> The committed corpus is synthetic; official-source G1 approval remains pending.

## Quickstart

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Unix: source .venv/bin/activate
pip install -e ".[dev]"

vlc version      # -> 0.1.0
vlc doctor       # environment + writable data dirs
vlc export-schemas --output-dir schemas/v1
vlc evaluate data/samples/golden-corpus
```

See [Snapshot workflow](docs/snapshots.md) for building, validating, and checking
G1 readiness before passing a snapshot to downstream retrieval.

## Tasks

`make` is the canonical runner (used in CI). Equivalent direct commands:

| Task | make | direct |
|---|---|---|
| tests | `make test` | `python -m pytest -q` |
| lint | `make lint` | `python -m ruff check src tests` |
| typecheck | `make typecheck` | `python -m mypy src` |
| gate | `make check` | run the three above |
| demo | `make demo` | `python -m vietlegalcorpus.cli doctor` |

## Layout

```
src/vietlegalcorpus/   package (cli, config, logging, schemas/)
tests/            unit / fixtures / golden
data/             raw (gitignored) · samples (committed) · processed (gitignored)
```

## License

Licensed under the [Apache License 2.0](LICENSE), matching the package metadata.
