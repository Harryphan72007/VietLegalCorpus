from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from vietlegalcorpus.ingestion import (
    IngestionError,
    IngestionPolicy,
    ingest_local_file,
    ingest_official_url,
)
from vietlegalcorpus.schemas import RetrievalMethod

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        url: str = "https://example.gov.vn/law/1",
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self._content = content
        self._url = url
        self.status = 200
        self.headers = {
            "Content-Type": content_type,
            "ETag": '"v1"',
            "Last-Modified": "Mon, 24 Aug 2026 12:00:00 GMT",
        }
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._content if size < 0 else self._content[:size]

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self.closed = True


def policy(*, max_bytes: int = 1024) -> IngestionPolicy:
    return IngestionPolicy(
        allowed_hosts=frozenset({"example.gov.vn"}),
        allowed_media_types=frozenset({"text/html", "text/plain", "application/pdf"}),
        max_bytes=max_bytes,
    )


def test_local_ingestion_is_content_addressed_and_rerun_safe(tmp_path: Path) -> None:
    source = tmp_path / "law.txt"
    source.write_text("Điều 1. Phạm vi điều chỉnh.", encoding="utf-8", newline="\n")
    storage = tmp_path / "store"

    first = ingest_local_file(source, storage, policy(), retrieved_at=NOW)
    stored = storage / Path(first.storage_path)
    before = stored.stat().st_mtime_ns
    second = ingest_local_file(source, storage, policy(), retrieved_at=NOW)

    assert first == second
    assert first.retrieval_method is RetrievalMethod.LOCAL_FILE
    assert first.source_locator == source.resolve().as_uri()
    assert stored.read_bytes() == source.read_bytes()
    assert stored.stat().st_mtime_ns == before


def test_official_url_records_http_provenance_and_final_url(tmp_path: Path) -> None:
    response = FakeResponse(b"<!doctype html><html>law</html>")

    artifact = ingest_official_url(
        "https://example.gov.vn/law/1",
        tmp_path,
        policy(),
        retrieved_at=NOW,
        opener=lambda request, timeout: response,
    )

    assert artifact.retrieval_method is RetrievalMethod.OFFICIAL_HTTP
    assert artifact.media_type == "text/html"
    assert artifact.http_status == 200
    assert artifact.etag == '"v1"'
    assert artifact.last_modified == "Mon, 24 Aug 2026 12:00:00 GMT"
    assert response.closed


@pytest.mark.parametrize(
    "url",
    [
        "http://example.gov.vn/law/1",
        "https://evil.example/law/1",
        "https://user:password@example.gov.vn/law/1",
    ],
)
def test_official_url_rejects_non_https_non_allowlisted_or_credentialed_url(
    tmp_path: Path, url: str
) -> None:
    called = False

    def opener(request: Any, timeout: float) -> FakeResponse:
        nonlocal called
        called = True
        return FakeResponse(b"<!doctype html><html>law</html>")

    with pytest.raises(IngestionError):
        ingest_official_url(url, tmp_path, policy(), retrieved_at=NOW, opener=opener)
    assert not called


def test_redirect_to_non_allowlisted_host_is_rejected_without_storage(tmp_path: Path) -> None:
    response = FakeResponse(
        b"<!doctype html><html>law</html>", url="https://evil.example/redirected"
    )

    with pytest.raises(IngestionError, match="allowlisted"):
        ingest_official_url(
            "https://example.gov.vn/law/1",
            tmp_path,
            policy(),
            retrieved_at=NOW,
            opener=lambda request, timeout: response,
        )

    assert response.closed
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize(
    ("content", "content_type", "message"),
    [
        (b"not a pdf", "application/pdf", "PDF signature"),
        (b"plain\x00binary", "text/plain", "NUL"),
        (b"<!doctype html><html>x</html>", "image/png", "media type"),
    ],
)
def test_mime_and_signature_mismatches_are_rejected(
    tmp_path: Path, content: bytes, content_type: str, message: str
) -> None:
    response = FakeResponse(content, content_type=content_type)

    with pytest.raises(IngestionError, match=message):
        ingest_official_url(
            "https://example.gov.vn/law/1",
            tmp_path,
            policy(),
            retrieved_at=NOW,
            opener=lambda request, timeout: response,
        )


def test_oversized_response_is_rejected_before_storage(tmp_path: Path) -> None:
    response = FakeResponse(b"<!doctype html><html>too large</html>")

    with pytest.raises(IngestionError, match="maximum"):
        ingest_official_url(
            "https://example.gov.vn/law/1",
            tmp_path,
            policy(max_bytes=10),
            retrieved_at=NOW,
            opener=lambda request, timeout: response,
        )

    assert not (tmp_path / "artifacts").exists()
