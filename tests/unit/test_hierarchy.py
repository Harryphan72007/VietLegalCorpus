from __future__ import annotations

from pathlib import Path

from vietlegalcorpus.hierarchy import segment_legal_hierarchy
from vietlegalcorpus.identity import document_version_identifier, sha256_bytes
from vietlegalcorpus.parsing import PlainTextParser
from vietlegalcorpus.schemas import ProvisionKind

FIXTURE = Path(__file__).parents[1] / "fixtures" / "parsing" / "hierarchy.txt"
ARTIFACT_ID = "artifact:sha256:" + "a" * 64
DOCUMENT_ID = "document:sha256:" + "b" * 64


def test_segmentation_preserves_hierarchy_ids_and_exact_anchors() -> None:
    content = FIXTURE.read_bytes()
    parsed = PlainTextParser().parse(content)
    document_version_id = document_version_identifier(DOCUMENT_ID, sha256_bytes(content))

    first = segment_legal_hierarchy(
        parsed,
        document_id=DOCUMENT_ID,
        document_version_id=document_version_id,
        source_artifact_id=ARTIFACT_ID,
    )
    second = segment_legal_hierarchy(
        parsed,
        document_id=DOCUMENT_ID,
        document_version_id=document_version_id,
        source_artifact_id=ARTIFACT_ID,
    )

    assert first == second
    assert [provision.kind for provision in first.provisions] == [
        ProvisionKind.ARTICLE,
        ProvisionKind.CLAUSE,
        ProvisionKind.POINT,
        ProvisionKind.ARTICLE,
    ]
    assert [provision.canonical_path for provision in first.provisions] == [
        ("article:1",),
        ("article:1", "clause:1"),
        ("article:1", "clause:1", "point:a"),
        ("article:2",),
    ]
    assert first.provisions[1].parent_provision_id == first.provisions[0].provision_id
    assert first.provisions[2].parent_provision_id == first.provisions[1].provision_id
    assert first.provisions[3].parent_provision_id is None
    assert first.warnings == ("ignored block 0 before the first article",)

    for provision_version in first.provision_versions:
        anchor = provision_version.source_anchor
        evidence = parsed.raw_text[anchor.start_offset : anchor.end_offset]
        assert evidence == provision_version.text
        assert provision_version.text_sha256 == sha256_bytes(evidence.encode("utf-8"))


def test_clause_and_point_without_required_parent_are_not_guessed() -> None:
    parsed = PlainTextParser().parse(b"1. Orphan clause\n\na) Orphan point")
    result = segment_legal_hierarchy(
        parsed,
        document_id=DOCUMENT_ID,
        document_version_id="document-version:sha256:" + "c" * 64,
        source_artifact_id=ARTIFACT_ID,
    )

    assert result.provisions == ()
    assert result.provision_versions == ()
    assert result.warnings == (
        "ignored clause block 0 without a parent article",
        "ignored point block 1 without a parent article",
    )
