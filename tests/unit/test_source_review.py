from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from vietlegalcorpus.quality import CorpusBundle, read_bundle
from vietlegalcorpus.source_review import (
    OfficialSourceReview,
    ReviewDecision,
    ReviewedDocument,
    read_source_review,
    review_covers_bundle,
)

GOLDEN = Path(__file__).parents[2] / "data" / "samples" / "golden-corpus"
PENDING_REVIEW = (
    Path(__file__).parents[2] / "data" / "source-reviews" / "land-law-pilot.pending.json"
)


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
        review_date="2026-08-24",
        decision=ReviewDecision.APPROVED,
        storage_and_reuse_approved=True,
        reviewed_by="Test legal reviewer",
        reviewed_at=datetime(2026, 8, 24, 13, tzinfo=UTC),
        documents=tuple(documents),
    )


def official_bundle() -> CorpusBundle:
    bundle = read_bundle(GOLDEN)
    artifacts = tuple(
        artifact.model_copy(update={"source_locator": f"https://vbpl.vn/test/{index}"})
        for index, artifact in enumerate(bundle.source_artifacts, start=1)
    )
    return replace(bundle, source_artifacts=artifacts)


def test_pending_land_law_register_is_valid_but_not_approval() -> None:
    review = read_source_review(PENDING_REVIEW)

    assert review.decision is ReviewDecision.PENDING
    assert review.review_date.isoformat() == "2026-08-25"
    assert [document.official_number for document in review.documents] == [
        "31/2024/QH15",
        "71/2024/NĐ-CP",
        "101/2024/NĐ-CP",
        "102/2024/NĐ-CP",
        "103/2024/NĐ-CP",
    ]
    assert not review_covers_bundle(read_bundle(GOLDEN), review, review_date=review.review_date)


def test_approval_requires_identity_timestamp_reuse_and_document_decisions() -> None:
    pending = read_source_review(PENDING_REVIEW)
    payload: dict[str, Any] = pending.model_dump(mode="json")
    payload["decision"] = "approved"

    with pytest.raises(ValidationError, match="storage and reuse"):
        OfficialSourceReview.model_validate(payload)

    payload["storage_and_reuse_approved"] = True
    with pytest.raises(ValidationError, match="reviewer identity"):
        OfficialSourceReview.model_validate(payload)

    payload["reviewed_by"] = "Legal reviewer"
    payload["reviewed_at"] = "2026-08-25T12:00:00Z"
    with pytest.raises(ValidationError, match="every document"):
        OfficialSourceReview.model_validate(payload)


def test_approved_review_must_cover_every_bundle_document_and_artifact() -> None:
    bundle = official_bundle()
    review = approved_review(bundle)

    assert review_covers_bundle(bundle, review, review_date=review.review_date)
    assert not review_covers_bundle(bundle, review, review_date=review.review_date.replace(day=23))

    first = review.documents[0]
    uncovered = review.model_copy(
        update={
            "documents": (
                first.model_copy(update={"source_locators": ("https://vbpl.vn/mismatch",)}),
                *review.documents[1:],
            )
        }
    )
    assert not review_covers_bundle(bundle, uncovered, review_date=review.review_date)


def test_source_locator_rejects_non_official_or_credentialed_urls() -> None:
    for locator in ("http://vbpl.vn/law", "https://example.com/law", "https://user@vbpl.vn/law"):
        with pytest.raises(ValidationError, match="official HTTPS"):
            ReviewedDocument(
                official_number="01/2026/QH",
                title="Luật mẫu",
                issuing_authority="Quốc hội",
                source_locators=(locator,),
            )
