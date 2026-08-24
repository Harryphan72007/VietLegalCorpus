"""Conservative as-of eligibility over explicit validity evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from vietlegalcorpus.schemas import (
    DocumentVersion,
    LegalStatus,
    RecordRef,
    RecordType,
    RelationEdge,
    RelationType,
)


class EligibilityDecision(StrEnum):
    """Three-valued decision that preserves insufficient evidence."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EligibilityAssessment:
    """Decision plus auditable reasons and consequential relation IDs."""

    decision: EligibilityDecision
    reasons: tuple[str, ...]
    decisive_relation_ids: tuple[str, ...] = ()


_NON_OPERATIONAL = frozenset(
    {
        LegalStatus.NOT_YET_EFFECTIVE,
        LegalStatus.SUSPENDED,
        LegalStatus.EXPIRED,
        LegalStatus.REPEALED,
    }
)
_UNCERTAIN = frozenset({LegalStatus.UNKNOWN, LegalStatus.PARTIALLY_IN_FORCE})
_CONSEQUENTIAL_RELATIONS = frozenset({RelationType.REPEALS, RelationType.REPLACES})


def assess_temporal_eligibility(
    version: DocumentVersion,
    *,
    review_date: date,
    relations: tuple[RelationEdge, ...] = (),
) -> EligibilityAssessment:
    """Assess one document version at ``review_date`` without inferring missing dates."""
    target = RecordRef(
        record_type=RecordType.DOCUMENT_VERSION,
        record_id=version.document_version_id,
    )
    applicable = tuple(
        edge
        for edge in relations
        if edge.target == target and edge.relation_type in _CONSEQUENTIAL_RELATIONS
    )
    dated = tuple(
        edge
        for edge in applicable
        if edge.effective_from is not None and edge.effective_from <= review_date
    )
    if dated:
        return EligibilityAssessment(
            EligibilityDecision.INELIGIBLE,
            tuple(
                f"{edge.relation_type.value} relation is effective on {edge.effective_from}"
                for edge in dated
            ),
            tuple(edge.relation_id for edge in dated),
        )

    undated = tuple(edge for edge in applicable if edge.effective_from is None)
    if undated:
        return EligibilityAssessment(
            EligibilityDecision.UNKNOWN,
            ("a consequential relation has no effective_from date",),
            tuple(edge.relation_id for edge in undated),
        )

    if version.effective_from is None:
        return EligibilityAssessment(
            EligibilityDecision.UNKNOWN,
            ("effective_from is unknown",),
        )
    if review_date < version.effective_from:
        return EligibilityAssessment(
            EligibilityDecision.INELIGIBLE,
            (f"review_date precedes effective_from {version.effective_from}",),
        )
    if version.effective_to is not None and review_date >= version.effective_to:
        return EligibilityAssessment(
            EligibilityDecision.INELIGIBLE,
            (f"review_date is on or after effective_to {version.effective_to}",),
        )
    if version.status in _NON_OPERATIONAL:
        return EligibilityAssessment(
            EligibilityDecision.INELIGIBLE,
            (f"explicit legal status is {version.status.value}",),
        )
    if version.status in _UNCERTAIN:
        return EligibilityAssessment(
            EligibilityDecision.UNKNOWN,
            (f"legal status {version.status.value} cannot establish full eligibility",),
        )
    return EligibilityAssessment(
        EligibilityDecision.ELIGIBLE,
        ("explicit status and validity interval include the review_date",),
    )
