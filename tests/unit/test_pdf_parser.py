from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from vietlegalcorpus.parsing import (
    ParserError,
    ParseStatus,
    PdfParser,
    default_parser_registry,
)


def make_pdf(*page_texts: str | None) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text is None:
            continue
        resources = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_parser_extracts_text_with_page_anchors() -> None:
    content = make_pdf("Dieu 1. Pham vi dieu chinh", "Dieu 2. Hieu luc thi hanh")

    first = PdfParser(minimum_alphanumeric_characters=10).parse(content)
    second = PdfParser(minimum_alphanumeric_characters=10).parse(content)

    assert first == second
    assert first.status is ParseStatus.PARSED
    assert [block.page_start for block in first.blocks] == [1, 2]
    assert [block.page_end for block in first.blocks] == [1, 2]
    assert first.warnings == ()
    for block in first.blocks:
        assert first.raw_text[block.source_start : block.source_end] == block.raw_text


def test_scan_only_pdf_is_routed_to_ocr_without_fake_text() -> None:
    result = PdfParser(minimum_alphanumeric_characters=10).parse(make_pdf(None, None))

    assert result.status is ParseStatus.OCR_REQUIRED
    assert result.blocks == ()
    assert result.raw_text == ""
    assert result.warnings == (
        "pages without usable text: 1, 2",
        "PDF has no usable text layer; OCR is required",
    )


def test_partially_empty_pdf_keeps_text_and_warns() -> None:
    result = PdfParser(minimum_alphanumeric_characters=10).parse(
        make_pdf("Dieu 1. Pham vi dieu chinh", None)
    )

    assert result.status is ParseStatus.PARSED
    assert len(result.blocks) == 1
    assert result.warnings == ("pages without usable text: 2",)


def test_encrypted_and_malformed_pdfs_are_rejected() -> None:
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(BytesIO(make_pdf(None))))
    writer.encrypt("secret")
    encrypted = BytesIO()
    writer.write(encrypted)

    with pytest.raises(ParserError, match="encrypted"):
        PdfParser().parse(encrypted.getvalue())
    with pytest.raises(ParserError, match="valid PDF"):
        PdfParser().parse(b"not a pdf")


def test_default_registry_dispatches_pdf() -> None:
    assert isinstance(default_parser_registry().get("application/pdf"), PdfParser)
