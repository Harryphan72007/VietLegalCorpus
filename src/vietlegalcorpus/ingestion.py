"""Conservative ingestion of explicit source artifacts into immutable storage."""

from __future__ import annotations

import mimetypes
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Protocol, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from vietlegalcorpus.identity import artifact_identifier, sha256_bytes, sha256_file
from vietlegalcorpus.schemas import RetrievalMethod, SourceArtifact

_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/html": ".html",
    "text/plain": ".txt",
}


class IngestionError(ValueError):
    """A source failed an explicit ingestion safety rule."""


@dataclass(frozen=True, slots=True)
class IngestionPolicy:
    """Closed allowlists and resource bounds for one ingestion run."""

    allowed_hosts: frozenset[str]
    allowed_media_types: frozenset[str]
    max_bytes: int = 25 * 1024 * 1024
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.allowed_hosts:
            raise ValueError("allowed_hosts must not be empty")
        if not self.allowed_media_types:
            raise ValueError("allowed_media_types must not be empty")
        if self.max_bytes < 1 or self.timeout_seconds <= 0:
            raise ValueError("resource limits must be positive")


class HttpResponse(Protocol):
    """Small response surface used by the offline-testable downloader."""

    status: int
    headers: Mapping[str, str]

    def read(self, size: int = -1) -> bytes: ...

    def geturl(self) -> str: ...

    def close(self) -> None: ...


UrlOpener = Callable[[Request, float], HttpResponse]


def ingest_local_file(
    source_path: Path,
    storage_root: Path,
    policy: IngestionPolicy,
    *,
    retrieved_at: datetime | None = None,
) -> SourceArtifact:
    """Validate and store one explicitly named local file."""
    if not source_path.is_file():
        raise IngestionError(f"local source is not a file: {source_path}")
    media_type = mimetypes.guess_type(source_path.name, strict=True)[0]
    if media_type is None:
        raise IngestionError("local source media type is unknown")
    _require_allowed_media_type(media_type, policy)
    if source_path.stat().st_size > policy.max_bytes:
        raise IngestionError("source exceeds maximum allowed bytes")
    content = source_path.read_bytes()
    _validate_content(content, media_type)
    artifact_id, relative_path = _store_immutable(content, media_type, storage_root)
    return SourceArtifact(
        artifact_id=artifact_id,
        source_locator=source_path.resolve().as_uri(),
        retrieval_method=RetrievalMethod.LOCAL_FILE,
        retrieved_at=_aware_timestamp(retrieved_at),
        media_type=media_type,
        byte_length=len(content),
        sha256=sha256_bytes(content),
        storage_path=relative_path,
    )


def ingest_official_url(
    url: str,
    storage_root: Path,
    policy: IngestionPolicy,
    *,
    retrieved_at: datetime | None = None,
    opener: UrlOpener | None = None,
) -> SourceArtifact:
    """Download one explicit allowlisted HTTPS URL; this is not a crawler."""
    _validate_official_url(url, policy)
    request = Request(url, headers={"User-Agent": "VietLegalCorpus/0.1"})
    active_opener = opener or _default_opener
    response = active_opener(request, policy.timeout_seconds)
    try:
        final_url = response.geturl()
        _validate_official_url(final_url, policy)
        media_type = _response_media_type(response.headers)
        _require_allowed_media_type(media_type, policy)
        content = response.read(policy.max_bytes + 1)
        if len(content) > policy.max_bytes:
            raise IngestionError("source exceeds maximum allowed bytes")
        _validate_content(content, media_type)
        artifact_id, relative_path = _store_immutable(content, media_type, storage_root)
        return SourceArtifact(
            artifact_id=artifact_id,
            source_locator=final_url,
            retrieval_method=RetrievalMethod.OFFICIAL_HTTP,
            retrieved_at=_aware_timestamp(retrieved_at),
            media_type=media_type,
            byte_length=len(content),
            sha256=sha256_bytes(content),
            storage_path=relative_path,
            http_status=response.status,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )
    finally:
        response.close()


def _default_opener(request: Request, timeout: float) -> HttpResponse:
    return cast(HttpResponse, urlopen(request, timeout=timeout))  # noqa: S310


def _validate_official_url(url: str, policy: IngestionPolicy) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise IngestionError("official source URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise IngestionError("official source URL must not contain credentials")
    host = (parsed.hostname or "").casefold()
    if host not in {allowed.casefold() for allowed in policy.allowed_hosts}:
        raise IngestionError("official source host is not allowlisted")


def _response_media_type(headers: Mapping[str, str]) -> str:
    content_type = headers.get("Content-Type", "")
    media_type = content_type.split(";", maxsplit=1)[0].strip().casefold()
    if not media_type:
        raise IngestionError("response has no Content-Type media type")
    return media_type


def _require_allowed_media_type(media_type: str, policy: IngestionPolicy) -> None:
    if media_type not in policy.allowed_media_types or media_type not in _EXTENSIONS:
        raise IngestionError(f"media type is not allowed: {media_type}")


def _validate_content(content: bytes, media_type: str) -> None:
    if not content:
        raise IngestionError("source artifact is empty")
    if media_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise IngestionError("content does not have a PDF signature")
    if media_type.endswith("wordprocessingml.document"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as exc:
            raise IngestionError("DOCX content is not a valid ZIP package") from exc
        if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
            raise IngestionError("DOCX package is missing required parts")
    if media_type in {"text/html", "text/plain"}:
        if b"\x00" in content:
            raise IngestionError("text source contains a NUL byte")
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestionError("text source is not valid UTF-8") from exc
        if media_type == "text/html" and not any(
            marker in decoded.casefold() for marker in ("<!doctype html", "<html")
        ):
            raise IngestionError("HTML source has no document signature")


def _store_immutable(content: bytes, media_type: str, storage_root: Path) -> tuple[str, str]:
    digest = sha256_bytes(content)
    relative = PurePosixPath("artifacts", digest[:2], digest, f"source{_EXTENSIONS[media_type]}")
    destination = storage_root.joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or sha256_file(destination) != digest:
            raise IngestionError("immutable artifact path contains different bytes")
    else:
        try:
            with destination.open("xb") as stored:
                stored.write(content)
        except FileExistsError:
            if sha256_file(destination) != digest:
                raise IngestionError("concurrent immutable artifact write disagreed") from None
    return artifact_identifier(digest), relative.as_posix()


def _aware_timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.utcoffset() is None:
        raise IngestionError("retrieved_at must include a timezone")
    return timestamp
