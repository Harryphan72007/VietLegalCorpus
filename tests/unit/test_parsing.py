from __future__ import annotations

from pathlib import Path

import pytest

from vietlegalcorpus.identity import sha256_bytes
from vietlegalcorpus.parsing import (
    HtmlParser,
    ParserError,
    ParserRegistry,
    PlainTextParser,
    default_parser_registry,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "parsing"


def test_plain_text_parser_preserves_source_and_paragraphs() -> None:
    content = (FIXTURES / "sample.txt").read_bytes()

    first = PlainTextParser().parse(content)
    second = PlainTextParser().parse(content)

    assert first == second
    assert first.media_type == "text/plain"
    assert first.raw_sha256 == sha256_bytes(content)
    assert "\n\n" in first.raw_text.replace("\r\n", "\n")
    assert [block.text for block in first.blocks] == [
        "LUẬT MẪU",
        "Điều 1. Phạm vi điều chỉnh.\nNội dung tiếp theo.",
        "Điều 2. Hiệu lực.",
    ]
    assert first.blocks[1].raw_text.replace("\r\n", "\n") == (
        "Điều 1.  Phạm vi điều chỉnh.\nNội dung tiếp theo."
    )
    for block in first.blocks:
        assert first.raw_text[block.source_start : block.source_end] == block.raw_text
    assert first.warnings == ()


def test_html_parser_extracts_block_content_and_ignores_script_style() -> None:
    content = (FIXTURES / "sample.html").read_bytes()

    result = HtmlParser().parse(content)

    assert result.raw_sha256 == sha256_bytes(content)
    assert result.raw_text.startswith("<!doctype html>")
    assert [block.text for block in result.blocks] == [
        "LUẬT MẪU",
        "Điều 1. Phạm vi điều chỉnh.",
        "Điểm a",
        "Điểm b",
    ]
    assert "ignore_me" not in " ".join(block.text for block in result.blocks)
    assert result.raw_text[result.blocks[1].source_start : result.blocks[1].source_end] == (
        "Điều 1. <strong>Phạm vi</strong> điều chỉnh."
    )
    assert result.warnings == ()


def test_html_parser_reports_text_outside_supported_blocks() -> None:
    result = HtmlParser().parse(b"<html><body>orphan<p>kept</p></body></html>")

    assert [block.text for block in result.blocks] == ["kept"]
    assert result.warnings == ("ignored visible text outside supported block elements",)


@pytest.mark.parametrize("parser", [PlainTextParser(), HtmlParser()])
def test_parsers_reject_empty_invalid_or_binary_text(parser: object) -> None:
    with pytest.raises(ParserError):
        parser.parse(b"")  # type: ignore[attr-defined]
    with pytest.raises(ParserError, match="UTF-8"):
        parser.parse(b"\xff")  # type: ignore[attr-defined]


def test_registry_has_explicit_media_type_dispatch() -> None:
    registry = default_parser_registry()

    assert isinstance(registry.get("text/plain"), PlainTextParser)
    assert isinstance(registry.get("text/html"), HtmlParser)
    with pytest.raises(ParserError, match="no parser"):
        registry.get("application/pdf")
    with pytest.raises(ParserError, match="already registered"):
        registry.register(PlainTextParser())


def test_registry_rejects_parser_without_media_types() -> None:
    class EmptyParser:
        media_types: frozenset[str] = frozenset()

        def parse(self, content: bytes):  # type: ignore[no-untyped-def]
            raise AssertionError

    with pytest.raises(ParserError, match="media type"):
        ParserRegistry().register(EmptyParser())
