from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vietlegalcorpus.cli import app
from vietlegalcorpus.quality import SourceEvidence, evaluate_bundle, read_bundle, write_bundle
from vietlegalcorpus.schemas import RecordRef, RecordType, RelationEdge, RelationType, SourceAnchor

GOLDEN = Path(__file__).parents[2] / "data" / "samples" / "golden-corpus"
runner = CliRunner()


@pytest.mark.parametrize("endpoint", ["source", "target"])
def test_relation_endpoints_must_match_their_declared_family(endpoint: str) -> None:
    bundle = read_bundle(GOLDEN)
    edge = RelationEdge(
        relation_id="relation:test:reference",
        relation_type=RelationType.REFERENCES,
        source=RecordRef(
            record_type=RecordType.DOCUMENT_VERSION,
            record_id=bundle.document_versions[0].document_version_id,
        ),
        target=RecordRef(
            record_type=RecordType.PROVISION_VERSION,
            record_id=bundle.provision_versions[0].provision_version_id,
        ),
    )
    assert evaluate_bundle(replace(bundle, relation_edges=(edge,))).passed
    original = getattr(edge, endpoint)
    wrong_type = (
        RecordType.PROVISION_VERSION if endpoint == "source" else RecordType.DOCUMENT_VERSION
    )
    broken = edge.model_copy(
        update={endpoint: original.model_copy(update={"record_type": wrong_type})}
    )
    report = evaluate_bundle(replace(bundle, relation_edges=(broken,)))
    assert "missing_relation_endpoint" in {issue.code for issue in report.issues}


def test_document_evidence_must_exist_and_match_declared_text_hash() -> None:
    bundle = read_bundle(GOLDEN)
    first = bundle.source_evidence[0]
    changed = first.model_copy(update={"text": first.text + " altered"})
    report = evaluate_bundle(
        replace(bundle, source_evidence=(changed, *bundle.source_evidence[1:]))
    )
    assert "document_text_checksum_mismatch" in {issue.code for issue in report.issues}
    report = evaluate_bundle(replace(bundle, source_evidence=bundle.source_evidence[1:]))
    assert "missing_document_evidence" in {issue.code for issue in report.issues}


def test_unknown_artifact_evidence_is_rejected() -> None:
    bundle = read_bundle(GOLDEN)
    orphan = SourceEvidence(source_artifact_id="artifact:missing", text="untracked")
    report = evaluate_bundle(replace(bundle, source_evidence=(*bundle.source_evidence, orphan)))
    assert "orphan_source_evidence" in {issue.code for issue in report.issues}


def test_golden_bundle_round_trips_byte_for_byte_and_passes(tmp_path: Path) -> None:
    bundle = read_bundle(GOLDEN)
    output = tmp_path / "roundtrip"

    written = write_bundle(bundle, output)
    report = evaluate_bundle(bundle)

    assert report.error_count == 0
    assert report.warning_count == 0
    assert report.passed
    for path in written:
        assert path.read_bytes() == (GOLDEN / path.name).read_bytes()


def test_seeded_duplicate_checksum_and_anchor_defects_fail() -> None:
    bundle = read_bundle(GOLDEN)
    original = bundle.provision_versions[0]
    broken = original.model_copy(
        update={
            "provision_version_id": "provision-version:sha256:" + "e" * 64,
            "text_sha256": "f" * 64,
            "source_anchor": SourceAnchor(
                source_artifact_id=original.source_anchor.source_artifact_id,
                start_offset=0,
                end_offset=9999,
            ),
        }
    )
    defective = replace(
        bundle,
        provisions=(*bundle.provisions, bundle.provisions[0]),
        provision_versions=(broken,),
    )

    report = evaluate_bundle(defective)
    codes = [issue.code for issue in report.issues]

    assert not report.passed
    assert codes == sorted(codes)
    assert "anchor_out_of_bounds" in codes
    assert "duplicate_provision_id" in codes
    assert "provision_text_checksum_mismatch" in codes
    assert "provision_version_id_mismatch" in codes


def test_unknown_metadata_is_reported_without_becoming_an_error() -> None:
    bundle = read_bundle(GOLDEN)
    document = bundle.documents[0].model_copy(
        update={"document_type": "unknown", "official_number": None, "issuing_authority": None}
    )
    version = bundle.document_versions[0].model_copy(
        update={"status": "unknown", "effective_from": None}
    )

    report = evaluate_bundle(
        replace(
            bundle,
            documents=(document, *bundle.documents[1:]),
            document_versions=(version, *bundle.document_versions[1:]),
        )
    )

    assert report.error_count == 0
    assert report.warning_count >= 4
    assert report.passed


def test_evaluate_cli_emits_deterministic_json() -> None:
    first = runner.invoke(app, ["evaluate", str(GOLDEN)])
    second = runner.invoke(app, ["evaluate", str(GOLDEN)])

    assert first.exit_code == 0
    assert first.stdout == second.stdout
    assert '"passed": true' in first.stdout


def test_cross_document_provision_and_duplicate_evidence_are_rejected() -> None:
    bundle = read_bundle(GOLDEN)
    original = bundle.provision_versions[0]
    other = next(
        version
        for version in bundle.document_versions
        if version.document_version_id != original.document_version_id
    )
    broken = original.model_copy(update={"document_version_id": other.document_version_id})
    report = evaluate_bundle(
        replace(
            bundle,
            provision_versions=(broken, *bundle.provision_versions[1:]),
            source_evidence=(*bundle.source_evidence, bundle.source_evidence[0]),
        )
    )
    codes = {issue.code for issue in report.issues}
    assert {
        "provision_document_mismatch",
        "anchor_artifact_mismatch",
        "duplicate_source_evidence_id",
    } <= codes
