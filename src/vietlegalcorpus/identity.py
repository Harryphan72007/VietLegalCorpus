"""Deterministic canonical paths, identifiers, and content checksums."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias
from urllib.parse import quote

from vietlegalcorpus.schemas import DocumentType, ProvisionKind

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_CANONICAL_SEGMENT_PATTERN = re.compile(r"^[a-z][a-z_]*:[A-Za-z0-9%._~-]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class HierarchySegment:
    """A declared structural kind and its source ordinal."""

    kind: ProvisionKind
    ordinal: str


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 digest of exact bytes."""
    return hashlib.sha256(content).hexdigest()


def sha256_text(text: str) -> str:
    """Hash exact UTF-8 text without newline or Unicode normalization."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Stream a file into SHA-256 without changing its bytes."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_canonical_json(value: JsonValue) -> str:
    """Hash compact UTF-8 JSON with recursively sorted object keys."""
    serialized = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256_text(serialized)


def canonical_hierarchy_path(*segments: HierarchySegment) -> tuple[str, ...]:
    """Normalize declared hierarchy segments into safe canonical path components."""
    if not segments:
        raise ValueError("canonical hierarchy path requires at least one segment")
    canonical: list[str] = []
    for segment in segments:
        ordinal = _normalize_identity_component(segment.ordinal)
        if not ordinal:
            raise ValueError("hierarchy ordinal must not be blank")
        canonical.append(f"{segment.kind.value}:{quote(ordinal, safe='-._~')}")
    return tuple(canonical)


def canonical_path_string(path: tuple[str, ...]) -> str:
    """Serialize a validated canonical hierarchy path for hashing and storage."""
    if not path:
        raise ValueError("canonical hierarchy path requires at least one segment")
    if any(_CANONICAL_SEGMENT_PATTERN.fullmatch(segment) is None for segment in path):
        raise ValueError("path contains a non-canonical hierarchy segment")
    return "/".join(path)


def stable_identifier(namespace: str, *components: str) -> str:
    """Hash length-prefixed UTF-8 components into a namespaced stable identifier."""
    if _NAMESPACE_PATTERN.fullmatch(namespace) is None:
        raise ValueError("namespace must contain lowercase ASCII letters, digits, or hyphens")
    if not components or any(not component for component in components):
        raise ValueError("identifier components must not be empty")

    digest = hashlib.sha256()
    for component in (namespace, *components):
        encoded = component.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big", signed=False))
        digest.update(encoded)
    return f"{namespace}:sha256:{digest.hexdigest()}"


def artifact_identifier(content_sha256: str) -> str:
    """Build the content-addressed identity of immutable source bytes."""
    _require_sha256(content_sha256)
    return f"artifact:sha256:{content_sha256}"


def document_identifier(
    document_type: DocumentType,
    official_number: str,
    *,
    jurisdiction: str = "VN",
) -> str:
    """Build a logical document ID from explicit official identity fields."""
    normalized_number = _normalize_identity_component(official_number)
    normalized_jurisdiction = _normalize_identity_component(jurisdiction)
    if not normalized_number or not normalized_jurisdiction:
        raise ValueError("jurisdiction and official_number must not be blank")
    return stable_identifier(
        "document", normalized_jurisdiction, document_type.value, normalized_number
    )


def document_version_identifier(document_id: str, source_sha256: str) -> str:
    """Build an exact document-version ID from its logical ID and source bytes."""
    _require_sha256(source_sha256)
    return stable_identifier("document-version", document_id, source_sha256)


def provision_identifier(document_id: str, path: tuple[str, ...]) -> str:
    """Build a logical provision ID from a document and canonical hierarchy path."""
    return stable_identifier("provision", document_id, canonical_path_string(path))


def provision_version_identifier(
    provision_id: str,
    document_version_id: str,
    text_sha256: str,
) -> str:
    """Build an exact provision-version ID from document version and exact text."""
    _require_sha256(text_sha256)
    return stable_identifier("provision-version", provision_id, document_version_id, text_sha256)


def _normalize_identity_component(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized.strip()).casefold()
    return unicodedata.normalize("NFC", normalized)


def _require_sha256(value: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("expected a lowercase 64-character SHA-256 digest")
