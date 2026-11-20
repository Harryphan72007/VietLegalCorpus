"""Explicit source plan to immutable snapshot, with visible failures and review state."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from vietlegalcorpus.hierarchy import segment_legal_hierarchy
from vietlegalcorpus.identity import document_identifier, document_version_identifier, sha256_text
from vietlegalcorpus.ingestion import IngestionPolicy, ingest_local_file, ingest_official_url
from vietlegalcorpus.parsing import DocxParser, HtmlParser, ParseStatus, PdfParser, PlainTextParser
from vietlegalcorpus.quality import CorpusBundle, SourceEvidence
from vietlegalcorpus.schemas import (
    DocumentType,
    DocumentVersion,
    LegalDocument,
    LegalStatus,
    Provision,
    ProvisionVersion,
    SourceArtifact,
)
from vietlegalcorpus.snapshot import build_snapshot
from vietlegalcorpus.source_review import read_source_review


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    title: str = Field(min_length=1)
    document_type: DocumentType
    official_number: str = Field(min_length=1)
    issuing_authority: str | None = None
    version_label: str = "original"
    promulgated_on: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: LegalStatus = LegalStatus.UNKNOWN


class BatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    corpus_id: str
    created_at: AwareDatetime
    review_date: date
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_hosts: tuple[str, ...] = (
        "vbpl.vn",
        "www.vbpl.vn",
        "vanban.chinhphu.vn",
        "congbao.chinhphu.vn",
    )
    target_document_count: int = Field(default=5, ge=1)
    source_review: str | None = None
    sources: tuple[SourceSpec, ...] = Field(min_length=1)


def run_batch(plan_path: Path, output_dir: Path, report_path: Path) -> dict[str, object]:
    """Continue past per-source failures; publish only a fully built validated snapshot."""
    plan = BatchPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    storage_root = report_path.parent / (report_path.stem + "-artifacts")
    policy = IngestionPolicy(
        frozenset(plan.allowed_hosts),
        frozenset(
            {
                "text/plain",
                "text/html",
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        ),
    )
    parsers = {
        media: parser
        for parser in (PlainTextParser(), HtmlParser(), DocxParser(), PdfParser())
        for media in parser.media_types
    }
    artifacts: list[SourceArtifact] = []
    documents: list[LegalDocument] = []
    versions: list[DocumentVersion] = []
    provisions: list[Provision] = []
    provision_versions: list[ProvisionVersion] = []
    evidence: list[SourceEvidence] = []
    failures: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    for spec in plan.sources:
        try:
            if spec.source.startswith("https://"):
                artifact = ingest_official_url(
                    spec.source, storage_root, policy, retrieved_at=plan.created_at
                )
            else:
                source = Path(spec.source)
                if not source.is_absolute():
                    source = plan_path.parent / source
                artifact = ingest_local_file(
                    source, storage_root, policy, retrieved_at=plan.created_at
                )
            parsed = parsers[artifact.media_type].parse(
                (storage_root / artifact.storage_path).read_bytes()
            )
            if parsed.status != ParseStatus.PARSED:
                pending.append({"source": spec.source, "reason": parsed.status.value})
                continue
            document_id = document_identifier(spec.document_type, spec.official_number)
            version_id = document_version_identifier(document_id, artifact.sha256)
            hierarchy = segment_legal_hierarchy(
                parsed,
                document_id=document_id,
                document_version_id=version_id,
                source_artifact_id=artifact.artifact_id,
            )
            if not hierarchy.provision_versions:
                pending.append(
                    {
                        "source": spec.source,
                        "reason": "no recognized provisions; manual parsing review required",
                    }
                )
                continue
            artifacts.append(artifact)
            documents.append(
                LegalDocument(
                    document_id=document_id,
                    title=spec.title,
                    document_type=spec.document_type,
                    official_number=spec.official_number,
                    issuing_authority=spec.issuing_authority,
                )
            )
            versions.append(
                DocumentVersion(
                    document_version_id=version_id,
                    document_id=document_id,
                    source_artifact_id=artifact.artifact_id,
                    version_label=spec.version_label,
                    text_sha256=sha256_text(parsed.raw_text),
                    promulgated_on=spec.promulgated_on,
                    effective_from=spec.effective_from,
                    effective_to=spec.effective_to,
                    status=spec.status,
                )
            )
            provisions.extend(hierarchy.provisions)
            provision_versions.extend(hierarchy.provision_versions)
            evidence.append(
                SourceEvidence(source_artifact_id=artifact.artifact_id, text=parsed.raw_text)
            )
            pending.extend(
                {"source": spec.source, "reason": warning}
                for warning in hierarchy.warnings + parsed.warnings
            )
        except (OSError, ValueError, KeyError) as exc:
            failures.append({"source": spec.source, "reason": str(exc)})
    result: dict[str, object] = {
        "status": "failed",
        "failures": failures,
        "pending_review": pending,
        "processed_documents": len(documents),
        "source_review_ready": False,
    }
    try:
        if not failures and not any(
            item["reason"]
            in (
                ParseStatus.OCR_REQUIRED.value,
                "no recognized provisions; manual parsing review required",
            )
            for item in pending
        ):
            bundle = CorpusBundle(
                tuple(artifacts),
                tuple(documents),
                tuple(versions),
                tuple(provisions),
                tuple(provision_versions),
                (),
                tuple(evidence),
            )
            review = (
                read_source_review(plan_path.parent / plan.source_review)
                if plan.source_review
                else None
            )
            built = build_snapshot(
                bundle,
                output_dir,
                corpus_id=plan.corpus_id,
                created_at=plan.created_at,
                review_date=plan.review_date,
                generator_version="vietlegalcorpus/batch-1.0.0",
                config_sha256=plan.config_sha256,
                target_document_count=plan.target_document_count,
                official_source_review=review,
                artifact_source_root=storage_root,
            )
            result.update(
                status="built",
                snapshot_sha256=built.snapshot_sha256,
                source_review_ready=built.readiness.ready,
                readiness_blockers=list(built.readiness.blockers),
            )
    except (OSError, ValueError) as exc:
        failures.append({"source": "snapshot", "reason": str(exc)})
    report_path.write_text(
        json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
