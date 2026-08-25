"""Deterministic CorpusSnapshot v1 construction, validation, and readiness reporting."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from vietlegalcorpus.identity import sha256_bytes, sha256_file
from vietlegalcorpus.quality import CorpusBundle, QualityReport, evaluate_bundle
from vietlegalcorpus.schemas import (
    CorpusManifest,
    ManifestEntry,
    RecordType,
)


class SnapshotError(ValueError):
    """A snapshot cannot be built or fails integrity validation."""


@dataclass(frozen=True, slots=True)
class G1Readiness:
    """Technical readiness separated from external official-source review."""

    ready: bool
    technical_checks_passed: bool
    document_count: int
    target_document_count: int
    experiment_target_document_count: int
    experiment_scale_complete: bool
    quality_error_count: int
    quality_warning_count: int
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SnapshotBuild:
    manifest: CorpusManifest
    snapshot_sha256: str
    readiness: G1Readiness


@dataclass(frozen=True, slots=True)
class SnapshotValidation:
    manifest: CorpusManifest
    snapshot_sha256: str


_RECORDS: tuple[tuple[str, str, RecordType, str], ...] = (
    ("source_artifacts", "source_artifacts.jsonl", RecordType.SOURCE_ARTIFACT, "artifact_id"),
    ("documents", "legal_documents.jsonl", RecordType.LEGAL_DOCUMENT, "document_id"),
    (
        "document_versions",
        "document_versions.jsonl",
        RecordType.DOCUMENT_VERSION,
        "document_version_id",
    ),
    ("provisions", "provisions.jsonl", RecordType.PROVISION, "provision_id"),
    (
        "provision_versions",
        "provision_versions.jsonl",
        RecordType.PROVISION_VERSION,
        "provision_version_id",
    ),
    ("relation_edges", "relation_edges.jsonl", RecordType.RELATION_EDGE, "relation_id"),
)


def build_snapshot(
    bundle: CorpusBundle,
    output_dir: Path,
    *,
    corpus_id: str,
    created_at: datetime,
    review_date: date,
    generator_version: str,
    config_sha256: str,
    target_document_count: int = 5,
    official_source_review_complete: bool = False,
    artifact_source_root: Path | None = None,
) -> SnapshotBuild:
    """Build a byte-reproducible snapshot without overwriting an existing build."""
    if target_document_count < 1:
        raise SnapshotError("target_document_count must be positive")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SnapshotError("snapshot output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    records_dir = output_dir / "records"
    records_dir.mkdir()

    quality = evaluate_bundle(bundle)
    if not quality.passed:
        raise SnapshotError("corpus quality errors must be resolved before snapshot construction")

    entries: list[ManifestEntry] = []
    for field_name, file_name, record_type, identifier_field in _RECORDS:
        records = getattr(bundle, field_name)
        path = records_dir / file_name
        _write_jsonl(records, path, identifier_field)
        entries.append(
            ManifestEntry(
                record_type=record_type,
                relative_path=path.relative_to(output_dir).as_posix(),
                sha256=sha256_file(path),
                record_count=len(records),
            )
        )

    evidence = {item.source_artifact_id: item.text for item in bundle.source_evidence}
    for artifact in sorted(bundle.source_artifacts, key=lambda item: item.artifact_id):
        destination = output_dir.joinpath(*Path(artifact.storage_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if artifact_source_root is not None:
            content = artifact_source_root.joinpath(*Path(artifact.storage_path).parts).read_bytes()
        else:
            text = evidence.get(artifact.artifact_id)
            if text is None:
                raise SnapshotError(f"source bytes unavailable for {artifact.artifact_id}")
            content = text.encode("utf-8")
        if sha256_bytes(content) != artifact.sha256:
            raise SnapshotError(f"source bytes disagree with {artifact.artifact_id}")
        destination.write_bytes(content)
        entries.append(
            ManifestEntry(
                record_type=RecordType.SOURCE_ARTIFACT,
                relative_path=destination.relative_to(output_dir).as_posix(),
                sha256=artifact.sha256,
                record_count=1,
            )
        )

    manifest = CorpusManifest(
        corpus_id=corpus_id,
        created_at=created_at,
        review_date=review_date,
        generator_version=generator_version,
        config_sha256=config_sha256,
        entries=tuple(sorted(entries, key=lambda item: item.relative_path)),
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_bytes(_model_json_bytes(manifest))
    snapshot_sha256 = sha256_file(manifest_path)
    (output_dir / "snapshot.sha256").write_text(
        f"{snapshot_sha256}\n", encoding="ascii", newline="\n"
    )

    readiness = _readiness(
        bundle,
        quality,
        target_document_count=target_document_count,
        official_source_review_complete=official_source_review_complete,
    )
    (output_dir / "g1_readiness.json").write_text(
        json.dumps(asdict(readiness), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return SnapshotBuild(manifest, snapshot_sha256, readiness)


def validate_snapshot(snapshot_dir: Path) -> SnapshotValidation:
    """Validate manifest schema, ordering, counts, file hashes, and snapshot hash."""
    try:
        manifest = CorpusManifest.model_validate_json(
            (snapshot_dir / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise SnapshotError("manifest is missing or invalid") from exc
    paths = [entry.relative_path for entry in manifest.entries]
    if paths != sorted(paths):
        raise SnapshotError("manifest entries are not sorted")
    for entry in manifest.entries:
        path = snapshot_dir.joinpath(*Path(entry.relative_path).parts)
        if not path.is_file():
            raise SnapshotError(f"manifest file is missing: {entry.relative_path}")
        if sha256_file(path) != entry.sha256:
            raise SnapshotError(f"checksum mismatch: {entry.relative_path}")
        count = _record_count(path) if path.suffix == ".jsonl" else 1
        if count != entry.record_count:
            raise SnapshotError(f"record count mismatch: {entry.relative_path}")

    expected_files = set(paths).union({"manifest.json", "snapshot.sha256", "g1_readiness.json"})
    actual_files = {
        path.relative_to(snapshot_dir).as_posix()
        for path in snapshot_dir.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise SnapshotError("snapshot contains missing or unlisted files")
    snapshot_sha256 = sha256_file(snapshot_dir / "manifest.json")
    recorded_hash = (snapshot_dir / "snapshot.sha256").read_text(encoding="ascii").strip()
    if recorded_hash != snapshot_sha256:
        raise SnapshotError("snapshot hash does not match the manifest")
    return SnapshotValidation(manifest, snapshot_sha256)


def _write_jsonl(records: tuple[object, ...], path: Path, identifier_field: str) -> None:
    ordered = sorted(records, key=lambda item: getattr(item, identifier_field))
    lines = [
        json.dumps(
            record.model_dump(mode="json"),  # type: ignore[attr-defined]
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        for record in ordered
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")


def _model_json_bytes(model: CorpusManifest) -> bytes:
    serialized = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{serialized}\n".encode()


def _record_count(path: Path) -> int:
    return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines())


def _readiness(
    bundle: CorpusBundle,
    quality: QualityReport,
    *,
    target_document_count: int,
    official_source_review_complete: bool,
) -> G1Readiness:
    technical = (
        quality.error_count == 0
        and quality.warning_count == 0
        and len(bundle.documents) >= target_document_count
    )
    blockers: list[str] = []
    if not technical:
        blockers.append("technical corpus checks are incomplete")
    if not official_source_review_complete:
        blockers.append("official source review is incomplete")
    return G1Readiness(
        ready=technical and official_source_review_complete,
        technical_checks_passed=technical,
        document_count=len(bundle.documents),
        target_document_count=target_document_count,
        experiment_target_document_count=100,
        experiment_scale_complete=len(bundle.documents) >= 100,
        quality_error_count=quality.error_count,
        quality_warning_count=quality.warning_count,
        blockers=tuple(blockers),
    )
