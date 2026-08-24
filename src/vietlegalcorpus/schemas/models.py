"""Immutable, versioned records in the CorpusSnapshot v1 contract."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

CORPUS_SCHEMA_VERSION = "1.0.0"

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
StableId = Annotated[
    str,
    StringConstraints(min_length=3, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$"),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MediaType = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]+$"
    ),
]


class ContractModel(BaseModel):
    """Base behavior shared by every top-level contract record."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["1.0.0"] = "1.0.0"


class RetrievalMethod(StrEnum):
    """Supported ways a source artifact can enter immutable storage."""

    LOCAL_FILE = "local_file"
    OFFICIAL_HTTP = "official_http"


class DocumentType(StrEnum):
    """Declared document type without inferring legal authority."""

    CONSTITUTION = "constitution"
    CODE = "code"
    LAW = "law"
    ORDINANCE = "ordinance"
    RESOLUTION = "resolution"
    DECREE = "decree"
    DECISION = "decision"
    CIRCULAR = "circular"
    JOINT_CIRCULAR = "joint_circular"
    DIRECTIVE = "directive"
    OTHER = "other"
    UNKNOWN = "unknown"


class LegalStatus(StrEnum):
    """Explicitly sourced status; ``unknown`` is never inferred as active."""

    UNKNOWN = "unknown"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    IN_FORCE = "in_force"
    PARTIALLY_IN_FORCE = "partially_in_force"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REPEALED = "repealed"


class ProvisionKind(StrEnum):
    """Structural levels preserved from a legal source."""

    PART = "part"
    CHAPTER = "chapter"
    SECTION = "section"
    ARTICLE = "article"
    CLAUSE = "clause"
    POINT = "point"
    ANNEX = "annex"
    OTHER = "other"


class RelationType(StrEnum):
    """Relations explicitly asserted by source evidence."""

    AMENDS = "amends"
    REPEALS = "repeals"
    REPLACES = "replaces"
    IMPLEMENTS = "implements"
    REFERENCES = "references"
    OTHER = "other"


class RecordType(StrEnum):
    """Record families stored in versioned JSONL files."""

    SOURCE_ARTIFACT = "source_artifact"
    LEGAL_DOCUMENT = "legal_document"
    DOCUMENT_VERSION = "document_version"
    PROVISION = "provision"
    PROVISION_VERSION = "provision_version"
    RELATION_EDGE = "relation_edge"


def _validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        "\\" in value
        or path.is_absolute()
        or PureWindowsPath(value).drive
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("relative_path must be a safe POSIX path beneath the corpus root")
    return value


class SourceArtifact(ContractModel):
    """Exact bytes acquired from an explicit local or official source."""

    artifact_id: StableId
    source_locator: NonEmptyStr
    retrieval_method: RetrievalMethod
    retrieved_at: AwareDatetime
    media_type: MediaType
    byte_length: NonNegativeInt
    sha256: Sha256
    storage_path: NonEmptyStr
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    etag: NonEmptyStr | None = None
    last_modified: NonEmptyStr | None = None

    @field_validator("storage_path")
    @classmethod
    def storage_path_is_safe(cls, value: str) -> str:
        return _validate_relative_path(value)


class LegalDocument(ContractModel):
    """Stable logical identity shared by all versions of one legal document."""

    document_id: StableId
    title: NonEmptyStr
    document_type: DocumentType
    jurisdiction: Literal["VN"] = "VN"
    official_number: NonEmptyStr | None = None
    issuing_authority: NonEmptyStr | None = None


class DocumentVersion(ContractModel):
    """One exact published version of a legal document."""

    document_version_id: StableId
    document_id: StableId
    source_artifact_id: StableId
    version_label: NonEmptyStr
    text_sha256: Sha256
    promulgated_on: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: LegalStatus = LegalStatus.UNKNOWN

    @model_validator(mode="after")
    def validity_interval_is_ordered(self) -> Self:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to cannot precede effective_from")
        return self


class Provision(ContractModel):
    """Stable logical identity for a structural provision."""

    provision_id: StableId
    document_id: StableId
    kind: ProvisionKind
    canonical_path: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    label: NonEmptyStr
    parent_provision_id: StableId | None = None

    @model_validator(mode="after")
    def parent_is_not_self(self) -> Self:
        if self.parent_provision_id == self.provision_id:
            raise ValueError("parent_provision_id cannot reference the provision itself")
        return self


class SourceAnchor(BaseModel):
    """Exact source span supporting a provision version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_artifact_id: StableId
    start_offset: NonNegativeInt
    end_offset: PositiveInt
    page_start: PositiveInt | None = None
    page_end: PositiveInt | None = None

    @model_validator(mode="after")
    def ranges_are_ordered(self) -> Self:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("page_start and page_end must either both be set or both be absent")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("page_end cannot precede page_start")
        return self


class ProvisionVersion(ContractModel):
    """Text and evidence for one provision in one document version."""

    provision_version_id: StableId
    provision_id: StableId
    document_version_id: StableId
    text: NonEmptyStr
    text_sha256: Sha256
    parser_version: NonEmptyStr
    source_anchor: SourceAnchor


class RecordRef(BaseModel):
    """Reference to a versioned document or provision record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_type: Literal[RecordType.DOCUMENT_VERSION, RecordType.PROVISION_VERSION]
    record_id: StableId


class RelationEdge(ContractModel):
    """Typed, evidence-backed relation between two versioned records."""

    relation_id: StableId
    relation_type: RelationType
    source: RecordRef
    target: RecordRef
    effective_from: date | None = None
    evidence_provision_version_ids: tuple[StableId, ...] = ()
    note: NonEmptyStr | None = None

    @model_validator(mode="after")
    def relation_is_not_self_referential(self) -> Self:
        if self.source == self.target:
            raise ValueError("source and target must be different records")
        return self


class ManifestEntry(BaseModel):
    """Integrity metadata for one record file in a corpus package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_type: RecordType
    relative_path: NonEmptyStr
    sha256: Sha256
    record_count: NonNegativeInt

    @field_validator("relative_path")
    @classmethod
    def relative_path_is_safe(cls, value: str) -> str:
        return _validate_relative_path(value)


class CorpusManifest(ContractModel):
    """Versioned inventory contract; snapshot hashing is implemented separately."""

    corpus_id: StableId
    created_at: AwareDatetime
    review_date: date
    generator_version: NonEmptyStr
    config_sha256: Sha256
    entries: Annotated[tuple[ManifestEntry, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def entry_paths_are_unique(self) -> Self:
        paths = [entry.relative_path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest entry relative_path values must be unique")
        return self
