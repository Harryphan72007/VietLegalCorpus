"""Deterministic parser protocol and born-digital HTML/plain-text parsers."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from io import BytesIO
from typing import Protocol
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from vietlegalcorpus.identity import sha256_bytes

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\r\n]+")
_DOCX_SPACES = re.compile(r"[ \f\v]+")
_PARAGRAPH_BREAK = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)+")
_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"})
_SKIP_TAGS = frozenset({"head", "script", "style", "template"})
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD = {"w": _WORD_NAMESPACE}
_WORD_TAG = f"{{{_WORD_NAMESPACE}}}"


class ParserError(ValueError):
    """A source cannot be handled safely by the selected parser."""


class ParseStatus(StrEnum):
    """Whether text was parsed or must be routed to an OCR stage."""

    PARSED = "parsed"
    OCR_REQUIRED = "ocr_required"


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """One deterministic parser block with its unnormalized source text."""

    ordinal: int
    text: str
    raw_text: str
    source_kind: str
    source_start: int
    source_end: int
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Parser output retaining the complete decoded source as evidence."""

    parser_version: str
    media_type: str
    raw_sha256: str
    raw_text: str
    blocks: tuple[ParsedBlock, ...]
    status: ParseStatus = ParseStatus.PARSED
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
        blocks: list[ParsedBlock] = []
        for source_start, source_end in _paragraph_spans(raw_text):
            raw_block = raw_text[source_start:source_end]
            text = _normalize_visible_text(raw_block)
            if text:
                blocks.append(
                    ParsedBlock(
                        ordinal=len(blocks),
                        text=text,
                        raw_text=raw_block,
                        source_kind="paragraph",
                        source_start=source_start,
                        source_end=source_end,
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
        collector = _HtmlBlockCollector(raw_text)
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


class DocxParser:
    """Parse paragraphs and simple tables from a born-digital DOCX package."""

    media_types = frozenset({_DOCX_MEDIA_TYPE})
    version = "docx/1.0.0"
    max_document_xml_bytes = 10 * 1024 * 1024

    def parse(self, content: bytes) -> ParseResult:
        if not content:
            raise ParserError("source content is empty")
        document_xml = self._read_document_xml(content)
        if b"<!DOCTYPE" in document_xml.upper():
            raise ParserError("DOCX document XML must not contain a DTD")
        try:
            root = ElementTree.fromstring(document_xml)
        except ElementTree.ParseError as exc:
            raise ParserError("DOCX document XML is not well formed") from exc
        body = root.find("w:body", _WORD)
        if body is None:
            raise ParserError("DOCX document has no WordprocessingML body")

        extracted: list[tuple[str, str]] = []
        warnings: list[str] = []
        for child in body:
            if child.tag == f"{_WORD_TAG}p":
                text = _docx_visible_text(child)
                if text:
                    style = child.find("w:pPr/w:pStyle", _WORD)
                    style_name = style.get(f"{_WORD_TAG}val", "") if style is not None else ""
                    kind = "heading" if style_name.casefold().startswith("heading") else "paragraph"
                    extracted.append((kind, text))
            elif child.tag == f"{_WORD_TAG}tbl":
                if (
                    child.find(".//w:gridSpan", _WORD) is not None
                    or child.find(".//w:vMerge", _WORD) is not None
                ):
                    _append_warning(warnings, "table contains merged cells")
                if child.find(".//w:tc/w:tbl", _WORD) is not None:
                    _append_warning(warnings, "table contains nested tables")
                for cell in child.findall("./w:tr/w:tc", _WORD):
                    text = _docx_visible_text(cell)
                    if text:
                        extracted.append(("table_cell", text))
            elif child.tag != f"{_WORD_TAG}sectPr":
                _append_warning(warnings, f"ignored unsupported DOCX element {child.tag}")

        raw_text, blocks = _build_extracted_evidence(extracted)
        if not blocks:
            _append_warning(warnings, "document contains no supported text blocks")
        return ParseResult(
            parser_version=self.version,
            media_type=_DOCX_MEDIA_TYPE,
            raw_sha256=sha256_bytes(content),
            raw_text=raw_text,
            blocks=blocks,
            warnings=tuple(warnings),
        )

    def _read_document_xml(self, content: bytes) -> bytes:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > self.max_document_xml_bytes:
                    raise ParserError("DOCX document XML exceeds the safety limit")
                return archive.read(info)
        except (KeyError, zipfile.BadZipFile) as exc:
            raise ParserError("source is not a valid DOCX package") from exc


class PdfParser:
    """Extract text-layer PDF pages and explicitly route scan-only files to OCR."""

    media_types = frozenset({"application/pdf"})
    version = "pdf-text/1.0.0"

    def __init__(self, *, minimum_alphanumeric_characters: int = 20) -> None:
        if minimum_alphanumeric_characters < 1:
            raise ValueError("minimum_alphanumeric_characters must be positive")
        self.minimum_alphanumeric_characters = minimum_alphanumeric_characters

    def parse(self, content: bytes) -> ParseResult:
        if not content.startswith(b"%PDF-"):
            raise ParserError("source is not a valid PDF")
        try:
            reader = PdfReader(BytesIO(content), strict=False)
        except (PdfReadError, ValueError) as exc:
            raise ParserError("source is not a valid PDF") from exc
        if reader.is_encrypted:
            raise ParserError("encrypted PDFs are not supported")

        pages: list[tuple[int, str]] = []
        empty_pages: list[int] = []
        try:
            for page_number, page in enumerate(reader.pages, start=1):
                text = _normalize_visible_text(page.extract_text() or "")
                if text:
                    pages.append((page_number, text))
                else:
                    empty_pages.append(page_number)
        except (PdfReadError, ValueError) as exc:
            raise ParserError("PDF text extraction failed") from exc

        warnings: list[str] = []
        if empty_pages:
            warnings.append(f"pages without usable text: {', '.join(map(str, empty_pages))}")
        alphanumeric_count = sum(character.isalnum() for _, text in pages for character in text)
        if alphanumeric_count < self.minimum_alphanumeric_characters:
            warnings.append("PDF has no usable text layer; OCR is required")
            return ParseResult(
                parser_version=self.version,
                media_type="application/pdf",
                raw_sha256=sha256_bytes(content),
                raw_text="",
                blocks=(),
                status=ParseStatus.OCR_REQUIRED,
                warnings=tuple(warnings),
            )

        raw_text, blocks = _build_pdf_evidence(pages)
        return ParseResult(
            parser_version=self.version,
            media_type="application/pdf",
            raw_sha256=sha256_bytes(content),
            raw_text=raw_text,
            blocks=blocks,
            warnings=tuple(warnings),
        )


class _HtmlBlockCollector(HTMLParser):
    def __init__(self, raw_text: str) -> None:
        super().__init__(convert_charrefs=True)
        self._raw_text = raw_text
        self._line_starts = [0]
        self._line_starts.extend(match.end() for match in re.finditer("\n", raw_text))
        self._blocks: list[ParsedBlock] = []
        self._current_tag: str | None = None
        self._current_data: list[str] = []
        self._current_start = 0
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
            start_tag = self.get_starttag_text() or ""
            self._current_start = self._absolute_position() + len(start_tag)
        elif tag == "br" and self._current_tag is not None:
            self._current_data.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == self._current_tag:
            source_end = self._absolute_position()
            raw_text = "".join(self._current_data)
            text = _normalize_visible_text(raw_text)
            if text:
                self._blocks.append(
                    ParsedBlock(
                        ordinal=len(self._blocks),
                        text=text,
                        raw_text=raw_text,
                        source_kind=tag,
                        source_start=self._current_start,
                        source_end=source_end,
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

    def _absolute_position(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column


def default_parser_registry() -> ParserRegistry:
    """Return the explicit born-digital parser set supported at this gate."""
    registry = ParserRegistry()
    registry.register(DocxParser())
    registry.register(HtmlParser())
    registry.register(PdfParser())
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


def _paragraph_spans(raw_text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    start = 0
    for separator in _PARAGRAPH_BREAK.finditer(raw_text):
        spans.append((start, separator.start()))
        start = separator.end()
    spans.append((start, len(raw_text)))
    trimmed: list[tuple[int, int]] = []
    for left, right in spans:
        while left < right and raw_text[left] in "\r\n":
            left += 1
        while right > left and raw_text[right - 1] in "\r\n":
            right -= 1
        if raw_text[left:right].strip():
            trimmed.append((left, right))
    return tuple(trimmed)


def _docx_visible_text(element: ElementTree.Element) -> str:
    fragments: list[str] = []
    for node in element.iter():
        if node.tag == f"{_WORD_TAG}t" and node.text:
            fragments.append(node.text)
        elif node.tag == f"{_WORD_TAG}tab":
            fragments.append("\t")
        elif node.tag in {f"{_WORD_TAG}br", f"{_WORD_TAG}cr"}:
            fragments.append("\n")
    lines = [_DOCX_SPACES.sub(" ", line).strip() for line in "".join(fragments).splitlines()]
    return "\n".join(line for line in lines if line)


def _build_extracted_evidence(
    extracted: list[tuple[str, str]],
) -> tuple[str, tuple[ParsedBlock, ...]]:
    evidence_parts: list[str] = []
    blocks: list[ParsedBlock] = []
    cursor = 0
    for kind, text in extracted:
        if evidence_parts:
            evidence_parts.append("\n\n")
            cursor += 2
        start = cursor
        evidence_parts.append(text)
        cursor += len(text)
        blocks.append(
            ParsedBlock(
                ordinal=len(blocks),
                text=text,
                raw_text=text,
                source_kind=kind,
                source_start=start,
                source_end=cursor,
            )
        )
    return "".join(evidence_parts), tuple(blocks)


def _append_warning(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _build_pdf_evidence(
    pages: list[tuple[int, str]],
) -> tuple[str, tuple[ParsedBlock, ...]]:
    evidence_parts: list[str] = []
    blocks: list[ParsedBlock] = []
    cursor = 0
    for page_number, text in pages:
        if evidence_parts:
            evidence_parts.append("\n\f\n")
            cursor += 3
        start = cursor
        evidence_parts.append(text)
        cursor += len(text)
        blocks.append(
            ParsedBlock(
                ordinal=len(blocks),
                text=text,
                raw_text=text,
                source_kind="page",
                source_start=start,
                source_end=cursor,
                page_start=page_number,
                page_end=page_number,
            )
        )
    return "".join(evidence_parts), tuple(blocks)
