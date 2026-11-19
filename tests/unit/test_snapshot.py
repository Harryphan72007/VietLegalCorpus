from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vietlegalcorpus.cli import app
from vietlegalcorpus.identity import sha256_file
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


def test_snapshot_can_be_pinned_to_an_external_digest(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    trusted = sha256_file(output / "manifest.json")
    assert validate_snapshot(output, expected_sha256=trusted).snapshot_sha256 == trusted
    # A different, internally consistent package must not satisfy the original pin.
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["config_sha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    refresh_checksums(output)
    validate_snapshot(output)
    with pytest.raises(SnapshotError, match="expected SHA-256"):
        validate_snapshot(output, expected_sha256=trusted)


@pytest.mark.parametrize("digest", ["", "abc", "g" * 64, "A" * 64, "a" * 64 + "\n"])
def test_invalid_external_digest_is_rejected(tmp_path: Path, digest: str) -> None:
    with pytest.raises(SnapshotError, match="lowercase hexadecimal"):
        validate_snapshot(tmp_path, expected_sha256=digest)


def test_matching_manifest_pin_still_checks_record_integrity(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    trusted = sha256_file(output / "manifest.json")
    (output / "records/provisions.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="checksum"):
        validate_snapshot(output, expected_sha256=trusted)


def build_test_snapshot(output: Path) -> None:
    build_snapshot(
        read_bundle(GOLDEN),
        output,
        corpus_id="corpus:test:snapshot",
        created_at=CREATED_AT,
        review_date=REVIEW_DATE,
        generator_version="vietlegalcorpus/0.1.0",
        config_sha256=CONFIG_SHA256,
    )


def refresh_checksums(output: Path) -> None:
    """Simulate a producer publishing internally hashed but invalid content."""
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        path = output / entry["relative_path"]
        entry["sha256"] = sha256_file(path)
        if entry["relative_path"].startswith("records/"):
            entry["record_count"] = len(path.read_text(encoding="utf-8").splitlines())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (output / "snapshot.sha256").write_text(sha256_file(manifest_path), encoding="ascii")


@pytest.mark.parametrize("defect", ["schema", "duplicate", "order", "inventory", "type"])
def test_rehashed_invalid_record_inventory_is_rejected(tmp_path: Path, defect: str) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    path = output / "records/legal_documents.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    if defect == "schema":
        lines[0] = '{"unexpected":true}'
    elif defect == "duplicate":
        lines.append(lines[0])
    elif defect == "order":
        lines.reverse()
    elif defect in {"inventory", "type"}:
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if defect == "inventory":
            manifest["entries"] = [
                entry
                for entry in manifest["entries"]
                if entry["relative_path"] != "records/legal_documents.jsonl"
            ]
        else:
            for entry in manifest["entries"]:
                if entry["relative_path"] == "records/legal_documents.jsonl":
                    entry["record_type"] = "provision"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    refresh_checksums(output)
    with pytest.raises(SnapshotError):
        validate_snapshot(output)


def test_snapshot_preserves_evidence_for_quality_validation(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    assert read_bundle(output / "records").source_evidence == read_bundle(GOLDEN).source_evidence
    path = output / "records/provision_versions.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["document_version_id"] = "document-version:missing"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    refresh_checksums(output)
    with pytest.raises(SnapshotError, match="quality"):
        validate_snapshot(output)


def test_snapshot_rejects_changed_source_evidence(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    (output / "records/source_evidence.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(SnapshotError, match="evidence checksum"):
        validate_snapshot(output)


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
    assert validate_snapshot(tmp_path / "snapshot").readiness == result.readiness
    saved = OfficialSourceReview.model_validate_json(
        (tmp_path / "snapshot/source_review.json").read_text(encoding="utf-8")
    )
    assert saved == approved_review(bundle)


@pytest.mark.parametrize("rehash", [False, True])
def test_readiness_claim_cannot_be_forged(tmp_path: Path, rehash: bool) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    path = output / "g1_readiness.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["ready"] = True
    report["blockers"] = []
    path.write_text(json.dumps(report), encoding="utf-8")
    if rehash:
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["readiness_sha256"] = sha256_file(path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        refresh_checksums(output)
    with pytest.raises(SnapshotError, match="readiness"):
        validate_snapshot(output)


def test_review_tampering_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    bundle = official_bundle()
    build_snapshot(
        bundle,
        output,
        corpus_id="corpus:test:review",
        created_at=CREATED_AT,
        review_date=REVIEW_DATE,
        generator_version="test",
        config_sha256=CONFIG_SHA256,
        official_source_review=approved_review(bundle),
    )
    (output / "source_review.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SnapshotError, match="review checksum"):
        validate_snapshot(output)


@pytest.mark.parametrize("existing_empty", [False, True])
def test_failed_build_leaves_destination_untouched(tmp_path: Path, existing_empty: bool) -> None:
    output = tmp_path / "snapshot"
    if existing_empty:
        output.mkdir()
    with pytest.raises(SnapshotError, match="build failed"):
        build_snapshot(
            read_bundle(GOLDEN),
            output,
            corpus_id="corpus:test:atomic",
            created_at=CREATED_AT,
            review_date=REVIEW_DATE,
            generator_version="test",
            config_sha256="invalid",
        )
    assert output.exists() == existing_empty
    if existing_empty:
        assert list(output.iterdir()) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == (
        ["snapshot"] if existing_empty else []
    )
    build_test_snapshot(output)
    validate_snapshot(output)


def test_existing_snapshot_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    original = snapshot_files(output)
    with pytest.raises(SnapshotError, match="empty"):
        build_test_snapshot(output)
    assert snapshot_files(output) == original


def test_artifacts_cannot_overwrite_snapshot_metadata(tmp_path: Path) -> None:
    bundle = read_bundle(GOLDEN)
    artifact = bundle.source_artifacts[0].model_copy(update={"storage_path": "manifest.json"})
    with pytest.raises(SnapshotError, match="reserved path"):
        build_snapshot(
            replace(bundle, source_artifacts=(artifact, *bundle.source_artifacts[1:])),
            tmp_path / "snapshot",
            corpus_id="corpus:test:collision",
            created_at=CREATED_AT,
            review_date=REVIEW_DATE,
            generator_version="test",
            config_sha256=CONFIG_SHA256,
        )
    assert not (tmp_path / "snapshot").exists()


def test_missing_hash_is_a_snapshot_error(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    (output / "snapshot.sha256").unlink()
    with pytest.raises(SnapshotError):
        validate_snapshot(output)


def test_external_symlink_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    target = output / "records/legal_documents.jsonl"
    external = tmp_path / "documents.jsonl"
    external.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError:
        pytest.skip("creating symlinks requires platform permission")
    with pytest.raises(SnapshotError, match="escapes root"):
        validate_snapshot(output)


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


def test_cli_readiness_gate_reports_pending_review(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    build_test_snapshot(output)
    result = runner.invoke(app, ["validate-snapshot", str(output), "--require-ready"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["blockers"] == ["official source review is incomplete"]


def test_cli_readiness_gate_accepts_approved_snapshot(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    bundle = official_bundle()
    build_snapshot(
        bundle,
        output,
        corpus_id="corpus:test:gate",
        created_at=CREATED_AT,
        review_date=REVIEW_DATE,
        generator_version="test",
        config_sha256=CONFIG_SHA256,
        official_source_review=approved_review(bundle),
    )
    result = runner.invoke(app, ["validate-snapshot", str(output), "--require-ready"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["ready"] is True


def test_cli_invalid_snapshot_has_readable_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate-snapshot", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error: manifest is missing or invalid" in result.stderr


def test_cli_build_exposes_target_and_original_bytes(tmp_path: Path) -> None:
    bundle = read_bundle(GOLDEN)
    source = tmp_path / "source"
    evidence = {item.source_artifact_id: item.text for item in bundle.source_evidence}
    for artifact in bundle.source_artifacts:
        path = source / artifact.storage_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(evidence[artifact.artifact_id].encode("utf-8"))
    output = tmp_path / "snapshot"
    result = runner.invoke(
        app,
        [
            "build-snapshot",
            str(GOLDEN),
            str(output),
            "--corpus-id",
            "corpus:test:cli",
            "--created-at",
            CREATED_AT.isoformat(),
            "--review-date",
            REVIEW_DATE.isoformat(),
            "--config-sha256",
            CONFIG_SHA256,
            "--target-document-count",
            "6",
            "--artifact-source-root",
            str(source),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["technical_checks_passed"] is False
    assert validate_snapshot(output).readiness.target_document_count == 6


def test_cli_build_failure_has_no_partial_output(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    result = runner.invoke(
        app,
        [
            "build-snapshot",
            str(GOLDEN),
            str(output),
            "--corpus-id",
            "corpus:test:cli",
            "--created-at",
            "invalid",
            "--review-date",
            REVIEW_DATE.isoformat(),
            "--config-sha256",
            CONFIG_SHA256,
        ],
    )
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert not output.exists()
