from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from vietlegalcorpus.identity import (
    HierarchySegment,
    artifact_identifier,
    canonical_hierarchy_path,
    canonical_path_string,
    document_identifier,
    document_version_identifier,
    provision_identifier,
    provision_version_identifier,
    sha256_bytes,
    sha256_canonical_json,
    sha256_file,
    sha256_text,
    stable_identifier,
)
from vietlegalcorpus.schemas import DocumentType, ProvisionKind

VECTOR_PATH = Path(__file__).parents[1] / "fixtures" / "identity" / "v1.json"


def test_checksum_helpers_preserve_exact_content(tmp_path: Path) -> None:
    content = b"abc\r\n"
    source = tmp_path / "source.bin"
    source.write_bytes(content)

    assert (
        sha256_bytes(content) == "552bab6864c7a7b69a502ed1854b9245c0e1a30f008aaa0b281da62585fdb025"
    )
    assert sha256_file(source) == sha256_bytes(content)
    assert sha256_text("abc\r\n") == sha256_bytes(content)
    assert sha256_text("abc\n") != sha256_text("abc\r\n")


def test_canonical_json_checksum_is_key_order_independent() -> None:
    first = {"schema_version": "1.0.0", "items": [1, True, None], "name": "Luật"}
    second = {"name": "Luật", "items": [1, True, None], "schema_version": "1.0.0"}

    assert sha256_canonical_json(first) == sha256_canonical_json(second)


def test_hierarchy_paths_are_unicode_and_whitespace_stable() -> None:
    composed = "Á"
    decomposed = unicodedata.normalize("NFD", composed)

    first = canonical_hierarchy_path(
        HierarchySegment(ProvisionKind.ARTICLE, " １ "),
        HierarchySegment(ProvisionKind.POINT, f"  {composed} "),
    )
    second = canonical_hierarchy_path(
        HierarchySegment(ProvisionKind.ARTICLE, "1"),
        HierarchySegment(ProvisionKind.POINT, decomposed.lower()),
    )

    assert first == second == ("article:1", "point:%C3%A1")
    assert canonical_path_string(first) == "article:1/point:%C3%A1"


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        ((), "at least one"),
        ((HierarchySegment(ProvisionKind.ARTICLE, "  "),), "ordinal"),
    ],
)
def test_hierarchy_paths_reject_missing_identity(
    segments: tuple[HierarchySegment, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        canonical_hierarchy_path(*segments)


def test_stable_identifier_uses_unambiguous_components() -> None:
    assert stable_identifier("test", "ab", "c") != stable_identifier("test", "a", "bc")
    with pytest.raises(ValueError, match="namespace"):
        stable_identifier("Not Safe", "value")


def test_identity_golden_vectors_are_stable_across_reruns() -> None:
    vector = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    path = canonical_hierarchy_path(
        HierarchySegment(ProvisionKind.ARTICLE, vector["article_ordinal"]),
        HierarchySegment(ProvisionKind.CLAUSE, vector["clause_ordinal"]),
    )
    source_sha256 = sha256_text(vector["source_text"])
    document_id = document_identifier(
        DocumentType(vector["document_type"]),
        vector["official_number"],
        jurisdiction=vector["jurisdiction"],
    )
    document_version_id = document_version_identifier(document_id, source_sha256)
    provision_id = provision_identifier(document_id, path)
    provision_version_id = provision_version_identifier(
        provision_id,
        document_version_id,
        sha256_text(vector["provision_text"]),
    )

    assert {
        "canonical_path": list(path),
        "source_sha256": source_sha256,
        "artifact_id": artifact_identifier(source_sha256),
        "document_id": document_id,
        "document_version_id": document_version_id,
        "provision_id": provision_id,
        "provision_version_id": provision_version_id,
    } == vector["expected"]
