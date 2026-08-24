"""Deterministic corpus JSONL I/O and evidence-backed quality evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from vietlegalcorpus.identity import (
    artifact_identifier,
    document_identifier,
    document_version_identifier,
    provision_identifier,
    provision_version_identifier,
    sha256_text,
)
from vietlegalcorpus.schemas import (
    DocumentType,
    DocumentVersion,
    LegalDocument,
    LegalStatus,
    Provision,
    ProvisionVersion,
    RelationEdge,
    SourceArtifact,
)


class SourceEvidence(BaseModel):
    """Decoded evidence stream addressed by provision source anchors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_artifact_id: str
    text: str


@dataclass(frozen=True, slots=True)
class CorpusBundle:
    """In-memory records used by invariant checks before snapshot publication."""

    source_artifacts: tuple[SourceArtifact, ...]
    documents: tuple[LegalDocument, ...]
    document_versions: tuple[DocumentVersion, ...]
    provisions: tuple[Provision, ...]
    provision_versions: tuple[ProvisionVersion, ...]
    relation_edges: tuple[RelationEdge, ...]
    source_evidence: tuple[SourceEvidence, ...]


@dataclass(frozen=True, order=True, slots=True)
class QualityIssue:
    """Stable issue suitable for machine comparison and reviewer display."""

    code: str
    severity: str
    record_id: str
    message: str


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Deterministically sorted invariant results."""

    issues: tuple[QualityIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == "warning" for issue in self.issues)

    @property
    def passed(self) -> bool:
        return self.error_count == 0

    def to_json(self) -> str:
        payload = {
            "error_count": self.error_count,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "record_id": issue.record_id,
                    "severity": issue.severity,
                }
                for issue in self.issues
            ],
            "passed": self.passed,
            "warning_count": self.warning_count,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


_RECORD_FILES: tuple[tuple[str, str, type[BaseModel]], ...] = (
    ("source_artifacts", "source_artifacts.jsonl", SourceArtifact),
    ("documents", "legal_documents.jsonl", LegalDocument),
    ("document_versions", "document_versions.jsonl", DocumentVersion),
    ("provisions", "provisions.jsonl", Provision),
    ("provision_versions", "provision_versions.jsonl", ProvisionVersion),
    ("relation_edges", "relation_edges.jsonl", RelationEdge),
    ("source_evidence", "source_evidence.jsonl", SourceEvidence),
)


def read_bundle(directory: Path) -> CorpusBundle:
    """Load the closed set of versioned JSONL files in a corpus bundle."""
    loaded: dict[str, tuple[BaseModel, ...]] = {}
    for field_name, file_name, model in _RECORD_FILES:
        path = directory / file_name
        records: list[BaseModel] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                try:
                    records.append(model.model_validate_json(line))
                except ValueError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid record") from exc
        loaded[field_name] = tuple(records)
    return CorpusBundle(**loaded)  # type: ignore[arg-type]


def write_bundle(bundle: CorpusBundle, directory: Path) -> tuple[Path, ...]:
    """Write sorted compact JSONL records with stable UTF-8/LF bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for field_name, file_name, _model in _RECORD_FILES:
        records = getattr(bundle, field_name)
        serialized = sorted(
            json.dumps(
                record.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for record in records
        )
        path = directory / file_name
        text = "\n".join(serialized)
        path.write_text(f"{text}\n" if text else "", encoding="utf-8", newline="\n")
        written.append(path)
    return tuple(written)


def evaluate_bundle(bundle: CorpusBundle) -> QualityReport:
    """Evaluate identity, provenance, hierarchy, anchor, and metadata invariants."""
    issues: list[QualityIssue] = []
    _check_duplicates(issues, "artifact", (item.artifact_id for item in bundle.source_artifacts))
    _check_duplicates(issues, "document", (item.document_id for item in bundle.documents))
    _check_duplicates(
        issues, "document_version", (item.document_version_id for item in bundle.document_versions)
    )
    _check_duplicates(issues, "provision", (item.provision_id for item in bundle.provisions))
    _check_duplicates(
        issues,
        "provision_version",
        (item.provision_version_id for item in bundle.provision_versions),
    )
    _check_duplicates(issues, "relation", (item.relation_id for item in bundle.relation_edges))

    artifacts = {item.artifact_id: item for item in bundle.source_artifacts}
    documents = {item.document_id: item for item in bundle.documents}
    document_versions = {item.document_version_id: item for item in bundle.document_versions}
    provisions = {item.provision_id: item for item in bundle.provisions}
    provision_versions = {item.provision_version_id: item for item in bundle.provision_versions}
    evidence = {item.source_artifact_id: item.text for item in bundle.source_evidence}

    for source_artifact in bundle.source_artifacts:
        if source_artifact.artifact_id != artifact_identifier(source_artifact.sha256):
            _error(
                issues,
                "artifact_id_mismatch",
                source_artifact.artifact_id,
                "artifact ID must match SHA-256",
            )
    for document in bundle.documents:
        if document.official_number:
            expected = document_identifier(
                document.document_type,
                document.official_number,
                jurisdiction=document.jurisdiction,
            )
            if document.document_id != expected:
                _error(
                    issues, "document_id_mismatch", document.document_id, "logical ID is unstable"
                )
        else:
            _warning(
                issues,
                "missing_official_number",
                document.document_id,
                "official number is unknown",
            )
        if not document.issuing_authority:
            _warning(
                issues, "missing_issuing_authority", document.document_id, "authority is unknown"
            )
        if document.document_type == DocumentType.UNKNOWN:
            _warning(
                issues, "unknown_document_type", document.document_id, "document type is unknown"
            )

    for document_version in bundle.document_versions:
        referenced_artifact = artifacts.get(document_version.source_artifact_id)
        if document_version.document_id not in documents:
            _error(
                issues,
                "missing_document",
                document_version.document_version_id,
                "document reference is missing",
            )
        if referenced_artifact is None:
            _error(
                issues,
                "missing_source_artifact",
                document_version.document_version_id,
                "artifact is missing",
            )
        else:
            expected = document_version_identifier(
                document_version.document_id, referenced_artifact.sha256
            )
            if document_version.document_version_id != expected:
                _error(
                    issues,
                    "document_version_id_mismatch",
                    document_version.document_version_id,
                    "version ID does not match document and artifact",
                )
        if document_version.status == LegalStatus.UNKNOWN:
            _warning(
                issues,
                "unknown_legal_status",
                document_version.document_version_id,
                "status is unknown",
            )
        if document_version.effective_from is None:
            _warning(
                issues,
                "unknown_effective_from",
                document_version.document_version_id,
                "date is unknown",
            )
        if (
            document_version.effective_from
            and document_version.effective_to
            and document_version.effective_to < document_version.effective_from
        ):
            _error(
                issues,
                "invalid_effective_interval",
                document_version.document_version_id,
                "interval is reversed",
            )

    for provision in bundle.provisions:
        if provision.document_id not in documents:
            _error(
                issues, "missing_provision_document", provision.provision_id, "document is missing"
            )
        if provision.provision_id != provision_identifier(
            provision.document_id, provision.canonical_path
        ):
            _error(
                issues, "provision_id_mismatch", provision.provision_id, "logical ID is unstable"
            )
        if provision.parent_provision_id:
            parent = provisions.get(provision.parent_provision_id)
            if parent is None:
                _error(
                    issues, "missing_parent_provision", provision.provision_id, "parent is missing"
                )
            elif (
                parent.document_id != provision.document_id
                or parent.canonical_path != provision.canonical_path[:-1]
            ):
                _error(
                    issues,
                    "invalid_parent_path",
                    provision.provision_id,
                    "parent path is inconsistent",
                )

    for provision_version in bundle.provision_versions:
        if provision_version.provision_id not in provisions:
            _error(
                issues,
                "missing_logical_provision",
                provision_version.provision_version_id,
                "provision is missing",
            )
        if provision_version.document_version_id not in document_versions:
            _error(
                issues,
                "missing_document_version",
                provision_version.provision_version_id,
                "document version is missing",
            )
        expected_text_sha = sha256_text(provision_version.text)
        if provision_version.text_sha256 != expected_text_sha:
            _error(
                issues,
                "provision_text_checksum_mismatch",
                provision_version.provision_version_id,
                "text checksum does not match exact UTF-8 text",
            )
        expected_id = provision_version_identifier(
            provision_version.provision_id,
            provision_version.document_version_id,
            expected_text_sha,
        )
        if provision_version.provision_version_id != expected_id:
            _error(
                issues,
                "provision_version_id_mismatch",
                provision_version.provision_version_id,
                "version ID does not match exact text",
            )
        source = evidence.get(provision_version.source_anchor.source_artifact_id)
        if source is None:
            _error(
                issues,
                "missing_anchor_evidence",
                provision_version.provision_version_id,
                "evidence is missing",
            )
        elif provision_version.source_anchor.end_offset > len(source):
            _error(
                issues,
                "anchor_out_of_bounds",
                provision_version.provision_version_id,
                "anchor exceeds evidence",
            )

    known_refs = set(document_versions).union(provision_versions)
    for edge in bundle.relation_edges:
        if edge.source.record_id not in known_refs or edge.target.record_id not in known_refs:
            _error(
                issues,
                "missing_relation_endpoint",
                edge.relation_id,
                "relation endpoint is missing",
            )
        if any(item not in provision_versions for item in edge.evidence_provision_version_ids):
            _error(
                issues,
                "missing_relation_evidence",
                edge.relation_id,
                "relation evidence is missing",
            )

    return QualityReport(tuple(sorted(issues)))


def _check_duplicates(issues: list[QualityIssue], kind: str, identifiers: Any) -> None:
    seen: set[str] = set()
    for identifier in identifiers:
        if identifier in seen:
            _error(issues, f"duplicate_{kind}_id", identifier, f"duplicate {kind} ID")
        seen.add(identifier)


def _error(issues: list[QualityIssue], code: str, record_id: str, message: str) -> None:
    issues.append(QualityIssue(code, "error", record_id, message))


def _warning(issues: list[QualityIssue], code: str, record_id: str, message: str) -> None:
    issues.append(QualityIssue(code, "warning", record_id, message))
