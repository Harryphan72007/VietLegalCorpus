"""Evidence required before an official-source corpus can pass G1."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from vietlegalcorpus.quality import CorpusBundle

_ALLOWED_SOURCE_HOSTS = frozenset(
    {"congbao.chinhphu.vn", "vanban.chinhphu.vn", "vbpl.vn", "www.vbpl.vn"}
)
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


class ReviewDecision(StrEnum):
    """Explicit human decision; pending is never treated as approval."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewedDocument(BaseModel):
    """One document and every exact official locator approved for ingestion."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    official_number: NonEmptyStr
    title: NonEmptyStr
    issuing_authority: NonEmptyStr
    source_locators: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    decision: ReviewDecision = ReviewDecision.PENDING
    note: NonEmptyStr | None = None

    @field_validator("source_locators")
    @classmethod
    def locators_are_unique_official_https_urls(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("source_locators must be unique")
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in _ALLOWED_SOURCE_HOSTS
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("source locators must be credential-free official HTTPS URLs")
        return values


class OfficialSourceReview(BaseModel):
    """Portable human-review record bound to an exact corpus scope."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: str = "1.0.0"
    review_id: NonEmptyStr
    scope: NonEmptyStr
    review_date: date
    decision: ReviewDecision = ReviewDecision.PENDING
    storage_and_reuse_approved: bool = False
    reviewed_by: NonEmptyStr | None = None
    reviewed_at: AwareDatetime | None = None
    documents: Annotated[tuple[ReviewedDocument, ...], Field(min_length=1)]

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: str) -> str:
        if value != "1.0.0":
            raise ValueError("unsupported source-review schema_version")
        return value

    @model_validator(mode="after")
    def decision_has_required_evidence(self) -> Self:
        numbers = [document.official_number for document in self.documents]
        if len(numbers) != len(set(numbers)):
            raise ValueError("reviewed document official_number values must be unique")
        if self.decision is ReviewDecision.APPROVED:
            if not self.storage_and_reuse_approved:
                raise ValueError("approved review must approve storage and reuse")
            if self.reviewed_by is None or self.reviewed_at is None:
                raise ValueError("approved review requires reviewer identity and timestamp")
            if any(document.decision is not ReviewDecision.APPROVED for document in self.documents):
                raise ValueError("approved review requires every document to be approved")
        return self


def read_source_review(path: Path) -> OfficialSourceReview:
    """Read and validate one UTF-8 source-review record."""
    return OfficialSourceReview.model_validate_json(path.read_text(encoding="utf-8"))


def review_covers_bundle(
    bundle: CorpusBundle, review: OfficialSourceReview | None, *, review_date: date
) -> bool:
    """Return whether explicit approval covers every document and artifact in a bundle."""
    if (
        review is None
        or review.decision is not ReviewDecision.APPROVED
        or review.review_date != review_date
    ):
        return False

    reviewed_by_number = {document.official_number: document for document in review.documents}
    corpus_by_id = {document.document_id: document for document in bundle.documents}
    corpus_numbers: set[str] = set()
    for document in bundle.documents:
        if document.official_number is None:
            return False
        reviewed_document = reviewed_by_number.get(document.official_number)
        if (
            reviewed_document is None
            or reviewed_document.title != document.title
            or reviewed_document.issuing_authority != document.issuing_authority
        ):
            return False
        corpus_numbers.add(document.official_number)
    if corpus_numbers != set(reviewed_by_number):
        return False

    artifacts = {artifact.artifact_id: artifact for artifact in bundle.source_artifacts}
    for version in bundle.document_versions:
        corpus_document = corpus_by_id.get(version.document_id)
        artifact = artifacts.get(version.source_artifact_id)
        if corpus_document is None or corpus_document.official_number is None or artifact is None:
            return False
        reviewed = reviewed_by_number[corpus_document.official_number]
        if (
            reviewed.decision is not ReviewDecision.APPROVED
            or artifact.source_locator not in reviewed.source_locators
        ):
            return False
    return True
