"""Deterministic parser protocol and born-digital HTML/plain-text parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol

from vietlegalcorpus.identity import sha256_bytes

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\r\n]+")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")
_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"})
_SKIP_TAGS = frozenset({"head", "script", "style", "template"})


class ParserError(ValueError):
    """A source cannot be handled safely by the selected parser."""


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """One deterministic parser block with its unnormalized source text."""

    ordinal: int
    text: str
    raw_text: str
    source_kind: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Parser output retaining the complete decoded source as evidence."""

    parser_version: str
    media_type: str
    raw_sha256: str
    raw_text: str
    blocks: tuple[ParsedBlock, ...]
    warnings: tuple[str, ...] = ()


class SourceParser(Protocol):
    """Minimal protocol implemented by deterministic source parsers."""

    media_types: frozenset[str]

    def parse(self, content: bytes) -> ParseResult: ...


class ParserRegistry:
    """Explicit media-type dispatch without implicit parser fallback."""

    def __init__(self) -> None:
        self._parsers: dict[str, SourceParser] = {}

    def register(self, parser: SourceParser) -> None:
        if not parser.media_types:
            raise ParserError("parser must declare at least one media type")
        duplicates = sorted(parser.media_types.intersection(self._parsers))
        if duplicates:
            raise ParserError(f"parser already registered for: {', '.join(duplicates)}")
        for media_type in sorted(parser.media_types):
            self._parsers[media_type] = parser

    def get(self, media_type: str) -> SourceParser:
        try:
            return self._parsers[media_type]
        except KeyError as exc:
            raise ParserError(f"no parser registered for media type: {media_type}") from exc


class PlainTextParser:
    """Parse UTF-8 text into blank-line-delimited blocks."""

    media_types = frozenset({"text/plain"})
    version = "plain-text/1.0.0"

    def parse(self, content: bytes) -> ParseResult:
        raw_text = _decode_utf8(content)
        normalized_newlines = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        blocks: list[ParsedBlock] = []
        for raw_block in _PARAGRAPH_BREAK.split(normalized_newlines):
            raw_block = raw_block.strip("\n")
            text = _normalize_visible_text(raw_block)
            if text:
                blocks.append(
                    ParsedBlock(
                        ordinal=len(blocks),
                        text=text,
                        raw_text=raw_block,
                        source_kind="paragraph",
                    )
                )
        return ParseResult(
            parser_version=self.version,
            media_type="text/plain",
            raw_sha256=sha256_bytes(content),
            raw_text=raw_text,
            blocks=tuple(blocks),
        )


class HtmlParser:
    """Extract supported visible HTML blocks using the standard library parser."""

    media_types = frozenset({"text/html"})
    version = "html/1.0.0"

    def parse(self, content: bytes) -> ParseResult:
        raw_text = _decode_utf8(content)
        collector = _HtmlBlockCollector()
        try:
            collector.feed(raw_text)
            collector.close()
        except Exception as exc:
            raise ParserError(f"HTML parsing failed: {exc}") from exc
        warnings = (
            ("ignored visible text outside supported block elements",)
            if collector.ignored_visible_text
            else ()
        )
        return ParseResult(
            parser_version=self.version,
            media_type="text/html",
            raw_sha256=sha256_bytes(content),
            raw_text=raw_text,
            blocks=collector.blocks,
            warnings=warnings,
        )


class _HtmlBlockCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[ParsedBlock] = []
        self._current_tag: str | None = None
        self._current_data: list[str] = []
        self._skip_depth = 0
        self._body_depth = 0
        self.ignored_visible_text = False

    @property
    def blocks(self) -> tuple[ParsedBlock, ...]:
        return tuple(self._blocks)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "body":
            self._body_depth += 1
        if self._skip_depth:
            self._skip_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = 1
            return
        if tag in _BLOCK_TAGS and self._current_tag is None:
            self._current_tag = tag
            self._current_data = []
        elif tag == "br" and self._current_tag is not None:
            self._current_data.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == self._current_tag:
            raw_text = "".join(self._current_data)
            text = _normalize_visible_text(raw_text)
            if text:
                self._blocks.append(
                    ParsedBlock(
                        ordinal=len(self._blocks),
                        text=text,
                        raw_text=raw_text,
                        source_kind=tag,
                    )
                )
            self._current_tag = None
            self._current_data = []
        if tag == "body" and self._body_depth:
            self._body_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._current_tag is not None:
            self._current_data.append(data)
        elif self._body_depth and data.strip():
            self.ignored_visible_text = True


def default_parser_registry() -> ParserRegistry:
    """Return the explicit born-digital parser set supported at this gate."""
    registry = ParserRegistry()
    registry.register(HtmlParser())
    registry.register(PlainTextParser())
    return registry


def _decode_utf8(content: bytes) -> str:
    if not content:
        raise ParserError("source content is empty")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParserError("source content is not valid UTF-8") from exc


def _normalize_visible_text(value: str) -> str:
    lines = [_HORIZONTAL_WHITESPACE.sub(" ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)
