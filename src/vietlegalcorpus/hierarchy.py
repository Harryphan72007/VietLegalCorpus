"""Conservative Vietnamese legal hierarchy segmentation with exact source anchors."""

from __future__ import annotations

import re
from dataclasses import dataclass

from vietlegalcorpus.identity import (
    HierarchySegment,
    canonical_hierarchy_path,
    provision_identifier,
    provision_version_identifier,
    sha256_text,
)
from vietlegalcorpus.parsing import ParsedBlock, ParseResult
from vietlegalcorpus.schemas import (
    Provision,
    ProvisionKind,
    ProvisionVersion,
    SourceAnchor,
)

_ARTICLE = re.compile(r"^Điều\s+([0-9]+[A-Za-z]?)\s*[.:]?", re.IGNORECASE)
_CLAUSE = re.compile(r"^([0-9]+)\.\s+")
_POINT = re.compile(r"^([A-Za-zĐđ])\)\s+")


@dataclass(frozen=True, slots=True)
class HierarchyResult:
    """Aligned logical provisions, exact versions, and conservative warnings."""

    provisions: tuple[Provision, ...]
    provision_versions: tuple[ProvisionVersion, ...]
    warnings: tuple[str, ...]


def segment_legal_hierarchy(
    parsed: ParseResult,
    *,
    document_id: str,
    document_version_id: str,
    source_artifact_id: str,
) -> HierarchyResult:
    """Recognize explicit Article/Clause/Point labels without guessing missing parents."""
    provisions: list[Provision] = []
    versions: list[ProvisionVersion] = []
    warnings = list(parsed.warnings)
    article: Provision | None = None
    clause: Provision | None = None

    for block in parsed.blocks:
        article_match = _ARTICLE.match(block.text)
        clause_match = _CLAUSE.match(block.text)
        point_match = _POINT.match(block.text)
        if article_match:
            path = canonical_hierarchy_path(
                HierarchySegment(ProvisionKind.ARTICLE, article_match.group(1))
            )
            article = _build_provision(
                block,
                kind=ProvisionKind.ARTICLE,
                label=f"Điều {article_match.group(1)}",
                path=path,
                parent=None,
                document_id=document_id,
                document_version_id=document_version_id,
                source_artifact_id=source_artifact_id,
                parser_version=parsed.parser_version,
                provisions=provisions,
                versions=versions,
            )
            clause = None
        elif clause_match:
            if article is None:
                warnings.append(f"ignored clause block {block.ordinal} without a parent article")
                continue
            path = (*article.canonical_path, f"clause:{clause_match.group(1).casefold()}")
            clause = _build_provision(
                block,
                kind=ProvisionKind.CLAUSE,
                label=f"Khoản {clause_match.group(1)}",
                path=path,
                parent=article,
                document_id=document_id,
                document_version_id=document_version_id,
                source_artifact_id=source_artifact_id,
                parser_version=parsed.parser_version,
                provisions=provisions,
                versions=versions,
            )
        elif point_match:
            if article is None:
                warnings.append(f"ignored point block {block.ordinal} without a parent article")
                continue
            if clause is None:
                warnings.append(f"ignored point block {block.ordinal} without a parent clause")
                continue
            ordinal = point_match.group(1).casefold()
            path = (*clause.canonical_path, f"point:{ordinal}")
            _build_provision(
                block,
                kind=ProvisionKind.POINT,
                label=f"Điểm {ordinal}",
                path=path,
                parent=clause,
                document_id=document_id,
                document_version_id=document_version_id,
                source_artifact_id=source_artifact_id,
                parser_version=parsed.parser_version,
                provisions=provisions,
                versions=versions,
            )
        elif article is None:
            warnings.append(f"ignored block {block.ordinal} before the first article")
        else:
            warnings.append(f"ignored unrecognized block {block.ordinal}")

    return HierarchyResult(tuple(provisions), tuple(versions), tuple(warnings))


def _build_provision(
    block: ParsedBlock,
    *,
    kind: ProvisionKind,
    label: str,
    path: tuple[str, ...],
    parent: Provision | None,
    document_id: str,
    document_version_id: str,
    source_artifact_id: str,
    parser_version: str,
    provisions: list[Provision],
    versions: list[ProvisionVersion],
) -> Provision:
    provision_id = provision_identifier(document_id, path)
    provision = Provision(
        provision_id=provision_id,
        document_id=document_id,
        kind=kind,
        canonical_path=path,
        label=label,
        parent_provision_id=parent.provision_id if parent else None,
    )
    text_sha256 = sha256_text(block.text)
    version = ProvisionVersion(
        provision_version_id=provision_version_identifier(
            provision_id, document_version_id, text_sha256
        ),
        provision_id=provision_id,
        document_version_id=document_version_id,
        text=block.text,
        text_sha256=text_sha256,
        parser_version=parser_version,
        source_anchor=SourceAnchor(
            source_artifact_id=source_artifact_id,
            start_offset=block.source_start,
            end_offset=block.source_end,
        ),
    )
    provisions.append(provision)
    versions.append(version)
    return provision
