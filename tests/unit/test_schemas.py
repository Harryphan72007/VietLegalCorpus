from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from vietlegalcorpus.schemas import (
    CorpusManifest,
    DocumentType,
    DocumentVersion,
    LegalDocument,
    LegalStatus,
    ManifestEntry,
    Provision,
    ProvisionKind,
    ProvisionVersion,
    RecordRef,
    RecordType,
    RelationEdge,
    RelationType,
    RetrievalMethod,
    SourceAnchor,
    SourceArtifact,
)
from vietlegalcorpus.schemas.export import SCHEMA_MODELS

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "schema" / "invalid"


def valid_records() -> tuple[BaseModel, ...]:
    artifact = SourceArtifact(
        artifact_id="artifact:sha256:aaaaaaaaaaaaaaaa",
        source_locator="https://example.gov.vn/law/1",
        retrieval_method=RetrievalMethod.OFFICIAL_HTTP,
        retrieved_at="2026-08-24T12:00:00Z",
        media_type="text/html",
        byte_length=120,
        sha256="a" * 64,
        storage_path="artifacts/aa/source.html",
    )
    document = LegalDocument(
        document_id="document:law-01",
        title="Luật mẫu",
        document_type=DocumentType.LAW,
        official_number="01/2026/QH",
        issuing_authority="Quốc hội",
    )
    document_version = DocumentVersion(
        document_version_id="document-version:law-01:v1",
        document_id=document.document_id,
        source_artifact_id=artifact.artifact_id,
        version_label="promulgated",
        text_sha256="b" * 64,
        status=LegalStatus.UNKNOWN,
    )
    provision = Provision(
        provision_id="provision:law-01:article-1",
        document_id=document.document_id,
        kind=ProvisionKind.ARTICLE,
        canonical_path=("article:1",),
        label="Điều 1",
    )
    provision_version = ProvisionVersion(
        provision_version_id="provision-version:law-01:article-1:v1",
        provision_id=provision.provision_id,
        document_version_id=document_version.document_version_id,
        text="Phạm vi điều chỉnh.",
        text_sha256="c" * 64,
        parser_version="html-text/1.0.0",
        source_anchor=SourceAnchor(
            source_artifact_id=artifact.artifact_id,
            start_offset=10,
            end_offset=30,
        ),
    )
    relation = RelationEdge(
        relation_id="relation:law-01:amends:law-00",
        relation_type=RelationType.AMENDS,
        source=RecordRef(
            record_type=RecordType.DOCUMENT_VERSION,
            record_id=document_version.document_version_id,
        ),
        target=RecordRef(
            record_type=RecordType.DOCUMENT_VERSION,
            record_id="document-version:law-00:v1",
        ),
        evidence_provision_version_ids=(provision_version.provision_version_id,),
    )
    manifest = CorpusManifest(
        corpus_id="corpus:pio1:seed",
        created_at="2026-08-24T12:00:00Z",
        review_date="2026-08-24",
        generator_version="vietlegalcorpus/0.1.0",
        config_sha256="d" * 64,
        entries=(
            ManifestEntry(
                record_type=RecordType.SOURCE_ARTIFACT,
                relative_path="records/source_artifacts.jsonl",
                sha256="e" * 64,
                record_count=1,
            ),
        ),
    )
    return (
        artifact,
        document,
        document_version,
        provision,
        provision_version,
        relation,
        manifest,
    )


def test_all_contract_records_validate_and_are_immutable() -> None:
    records = valid_records()

    assert len(records) == len(SCHEMA_MODELS) == 7
    for record in records:
        with pytest.raises(ValidationError):
            record.__class__.model_validate({**record.model_dump(), "unexpected": True})
        with pytest.raises(ValidationError):
            record.__class__.model_validate({**record.model_dump(), "schema_version": "2.0.0"})
        with pytest.raises(ValidationError):
            record.schema_version = "1.0.0"  # type: ignore[attr-defined]


@pytest.mark.parametrize("fixture_path", sorted(FIXTURE_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_invalid_schema_fixtures_are_rejected(fixture_path: Path) -> None:
    fixture: dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
    model = SCHEMA_MODELS[fixture["model"]]

    with pytest.raises(ValidationError):
        model.model_validate(fixture["payload"])


def test_source_anchor_requires_non_empty_ordered_offsets() -> None:
    with pytest.raises(ValidationError, match="end_offset"):
        SourceAnchor(
            source_artifact_id="artifact:sha256:aaaaaaaaaaaaaaaa",
            start_offset=30,
            end_offset=30,
        )


def test_manifest_paths_are_relative_safe_and_unique() -> None:
    base: dict[str, Any] = {
        "corpus_id": "corpus:pio1:seed",
        "created_at": "2026-08-24T12:00:00Z",
        "review_date": "2026-08-24",
        "generator_version": "vietlegalcorpus/0.1.0",
        "config_sha256": "d" * 64,
    }
    entry = {
        "record_type": "source_artifact",
        "relative_path": "records/source_artifacts.jsonl",
        "sha256": "e" * 64,
        "record_count": 1,
    }

    with pytest.raises(ValidationError, match="unique"):
        CorpusManifest.model_validate({**base, "entries": [entry, entry]})
    with pytest.raises(ValidationError, match="relative_path"):
        ManifestEntry.model_validate({**entry, "relative_path": "../outside.jsonl"})
    with pytest.raises(ValidationError, match="relative_path"):
        ManifestEntry.model_validate({**entry, "relative_path": "C:/outside.jsonl"})
