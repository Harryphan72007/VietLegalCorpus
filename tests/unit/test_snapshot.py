from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vietlegalcorpus.cli import app
from vietlegalcorpus.quality import CorpusBundle, read_bundle
from vietlegalcorpus.snapshot import SnapshotError, build_snapshot, validate_snapshot
from vietlegalcorpus.source_review import OfficialSourceReview, ReviewDecision, ReviewedDocument

GOLDEN = Path(__file__).parents[2] / "data" / "samples" / "golden-corpus"
PENDING_REVIEW = (
    Path(__file__).parents[2] / "data" / "source-reviews" / "land-law-pilot.pending.json"
)
CREATED_AT = datetime(2026, 8, 24, 12, tzinfo=UTC)
REVIEW_DATE = date(2026, 8, 24)
CONFIG_SHA256 = "a" * 64
runner = CliRunner()


def approved_review(bundle: CorpusBundle) -> OfficialSourceReview:
    artifacts = {artifact.artifact_id: artifact for artifact in bundle.source_artifacts}
    versions = {version.document_id: version for version in bundle.document_versions}
    documents = []
    for document in bundle.documents:
        assert document.official_number is not None
        version = versions[document.document_id]
        documents.append(
            ReviewedDocument(
                official_number=document.official_number,
                title=document.title,
                issuing_authority=document.issuing_authority or "unknown",
                source_locators=(artifacts[version.source_artifact_id].source_locator,),
                decision=ReviewDecision.APPROVED,
            )
        )
    return OfficialSourceReview(
        review_id="source-review:test:approved",
        scope="Test bundle",
        review_date=REVIEW_DATE,
        decision=ReviewDecision.APPROVED,
        storage_and_reuse_approved=True,
        reviewed_by="Test legal reviewer",
        reviewed_at=CREATED_AT,
        documents=tuple(documents),
    )


def official_bundle() -> CorpusBundle:
    bundle = read_bundle(GOLDEN)
    artifacts = tuple(
        artifact.model_copy(update={"source_locator": f"https://vbpl.vn/test/{index}"})
        for index, artifact in enumerate(bundle.source_artifacts, start=1)
    )
    return replace(bundle, source_artifacts=artifacts)


def snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def test_two_snapshot_builds_are_byte_identical_and_validate(tmp_path: Path) -> None:
    bundle = read_bundle(GOLDEN)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = build_snapshot(
        bundle,
        first_dir,
        corpus_id="corpus:pio1:vertical-slice",
        created_at=CREATED_AT,
        review_date=REVIEW_DATE,
        generator_version="vietlegalcorpus/0.1.0",
        config_sha256=CONFIG_SHA256,
    )
    second = build_snapshot(
        bundle,
        second_dir,
        corpus_id="corpus:pio1:vertical-slice",
        created_at=CREATED_AT,
        review_date=REVIEW_DATE,
        generator_version="vietlegalcorpus/0.1.0",
        config_sha256=CONFIG_SHA256,
    )

    assert first.snapshot_sha256 == second.snapshot_sha256
    assert snapshot_files(first_dir) == snapshot_files(second_dir)
    assert first.readiness.technical_checks_passed
    assert not first.readiness.ready
    assert first.readiness.blockers == ("official source review is incomplete",)
    assert first.readiness.document_count == 5
    assert first.readiness.target_document_count == 5
    assert [entry.relative_path for entry in first.manifest.entries] == sorted(
        entry.relative_path for entry in first.manifest.entries
    )
    assert validate_snapshot(first_dir).snapshot_sha256 == first.snapshot_sha256


def test_validator_rejects_tampered_snapshot_file(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_snapshot(
        read_bundle(GOLDEN),
        output,
        corpus_id="corpus:pio1:vertical-slice",
        created_at=CREATED_AT,
        review_date=REVIEW_DATE,
        generator_version="vietlegalcorpus/0.1.0",
        config_sha256=CONFIG_SHA256,
    )
    target = output / "records" / "provisions.jsonl"
    target.write_bytes(target.read_bytes() + b"tampered\n")

    with pytest.raises(SnapshotError, match="checksum"):
        validate_snapshot(output)


def test_snapshot_is_ready_only_when_review_covers_exact_bundle(tmp_path: Path) -> None:
    bundle = official_bundle()
    result = build_snapshot(
        bundle,
        tmp_path / "snapshot",
        corpus_id="corpus:pio1:vertical-slice",
        created_at=CREATED_AT,
        review_date=REVIEW_DATE,
        generator_version="vietlegalcorpus/0.1.0",
        config_sha256=CONFIG_SHA256,
        official_source_review=approved_review(bundle),
    )

    assert result.readiness.ready
    assert result.readiness.blockers == ()


def test_snapshot_cli_builds_and_validates(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build = runner.invoke(
        app,
        [
            "build-snapshot",
            str(GOLDEN),
            str(output),
            "--corpus-id",
            "corpus:pio1:vertical-slice",
            "--created-at",
            CREATED_AT.isoformat(),
            "--review-date",
            REVIEW_DATE.isoformat(),
            "--config-sha256",
            CONFIG_SHA256,
            "--source-review",
            str(PENDING_REVIEW),
        ],
    )
    validate = runner.invoke(app, ["validate-snapshot", str(output)])

    assert build.exit_code == 0
    assert '"ready": false' in build.stdout
    assert validate.exit_code == 0
    assert "snapshot_sha256" in validate.stdout
