from __future__ import annotations

from datetime import date

import pytest

from vietlegalcorpus.schemas import (
    DocumentVersion,
    LegalStatus,
    RecordRef,
    RecordType,
    RelationEdge,
    RelationType,
)
from vietlegalcorpus.temporal import EligibilityDecision, assess_temporal_eligibility

VERSION_ID = "document-version:sha256:" + "a" * 64


def version(**overrides: object) -> DocumentVersion:
    values: dict[str, object] = {
        "document_version_id": VERSION_ID,
        "document_id": "document:sha256:" + "b" * 64,
        "source_artifact_id": "artifact:sha256:" + "c" * 64,
        "version_label": "promulgated",
        "text_sha256": "d" * 64,
        "status": LegalStatus.IN_FORCE,
        "effective_from": date(2026, 1, 1),
    }
    values.update(overrides)
    return DocumentVersion.model_validate(values)


def relation(
    relation_type: RelationType,
    *,
    effective_from: date | None,
    target_id: str = VERSION_ID,
) -> RelationEdge:
    return RelationEdge(
        relation_id=f"relation:{relation_type.value}:{effective_from or 'unknown'}",
        relation_type=relation_type,
        source=RecordRef(
            record_type=RecordType.DOCUMENT_VERSION,
            record_id="document-version:sha256:" + "e" * 64,
        ),
        target=RecordRef(record_type=RecordType.DOCUMENT_VERSION, record_id=target_id),
        effective_from=effective_from,
    )


@pytest.mark.parametrize(
    ("review_date", "expected"),
    [
        (date(2025, 12, 31), EligibilityDecision.INELIGIBLE),
        (date(2026, 1, 1), EligibilityDecision.ELIGIBLE),
        (date(2026, 12, 31), EligibilityDecision.ELIGIBLE),
        (date(2027, 1, 1), EligibilityDecision.INELIGIBLE),
    ],
)
def test_effective_interval_is_start_inclusive_end_exclusive(
    review_date: date, expected: EligibilityDecision
) -> None:
    result = assess_temporal_eligibility(
        version(effective_to=date(2027, 1, 1)), review_date=review_date
    )
    assert result.decision is expected


@pytest.mark.parametrize(
    "status",
    [
        LegalStatus.NOT_YET_EFFECTIVE,
        LegalStatus.SUSPENDED,
        LegalStatus.EXPIRED,
        LegalStatus.REPEALED,
    ],
)
def test_explicit_non_operational_status_is_ineligible(status: LegalStatus) -> None:
    result = assess_temporal_eligibility(version(status=status), review_date=date(2026, 8, 24))
    assert result.decision is EligibilityDecision.INELIGIBLE


@pytest.mark.parametrize("status", [LegalStatus.UNKNOWN, LegalStatus.PARTIALLY_IN_FORCE])
def test_unknown_or_partial_status_abstains(status: LegalStatus) -> None:
    result = assess_temporal_eligibility(version(status=status), review_date=date(2026, 8, 24))
    assert result.decision is EligibilityDecision.UNKNOWN


def test_missing_effective_date_is_not_inferred_from_promulgation() -> None:
    result = assess_temporal_eligibility(
        version(effective_from=None, promulgated_on=date(2020, 1, 1)),
        review_date=date(2026, 8, 24),
    )
    assert result.decision is EligibilityDecision.UNKNOWN
    assert "effective_from is unknown" in result.reasons


def test_dated_repeal_or_replacement_makes_target_ineligible() -> None:
    for relation_type in (RelationType.REPEALS, RelationType.REPLACES):
        edge = relation(relation_type, effective_from=date(2026, 6, 1))
        result = assess_temporal_eligibility(
            version(), review_date=date(2026, 8, 24), relations=(edge,)
        )
        assert result.decision is EligibilityDecision.INELIGIBLE
        assert result.decisive_relation_ids == (edge.relation_id,)


def test_future_or_unrelated_relation_does_not_remove_eligibility() -> None:
    result = assess_temporal_eligibility(
        version(),
        review_date=date(2026, 8, 24),
        relations=(
            relation(RelationType.REPEALS, effective_from=date(2027, 1, 1)),
            relation(
                RelationType.REPEALS,
                effective_from=date(2026, 1, 1),
                target_id="document-version:sha256:" + "f" * 64,
            ),
        ),
    )
    assert result.decision is EligibilityDecision.ELIGIBLE


def test_undated_consequential_relation_forces_abstention() -> None:
    edge = relation(RelationType.REPEALS, effective_from=None)
    result = assess_temporal_eligibility(
        version(), review_date=date(2026, 8, 24), relations=(edge,)
    )
    assert result.decision is EligibilityDecision.UNKNOWN
    assert result.decisive_relation_ids == (edge.relation_id,)
