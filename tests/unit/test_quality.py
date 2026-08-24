from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from vietlegalcorpus.cli import app
from vietlegalcorpus.quality import evaluate_bundle, read_bundle, write_bundle
from vietlegalcorpus.schemas import SourceAnchor

GOLDEN = Path(__file__).parents[2] / "data" / "samples" / "golden-corpus"
runner = CliRunner()


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

    report = evaluate_bundle(replace(bundle, documents=(document,), document_versions=(version,)))

    assert report.error_count == 0
    assert report.warning_count >= 4
    assert report.passed


def test_evaluate_cli_emits_deterministic_json() -> None:
    first = runner.invoke(app, ["evaluate", str(GOLDEN)])
    second = runner.invoke(app, ["evaluate", str(GOLDEN)])

    assert first.exit_code == 0
    assert first.stdout == second.stdout
    assert '"passed": true' in first.stdout
