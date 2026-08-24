from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from vietlegalcorpus.identity import sha256_bytes
from vietlegalcorpus.parsing import DocxParser, ParserError, default_parser_registry

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(body: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}<w:sectPr/></w:body></w:document>'
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def test_docx_parser_preserves_order_headings_paragraphs_and_tables() -> None:
    content = make_docx(
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>LUẬT MẪU</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>Điều 1.</w:t></w:r><w:r><w:tab/><w:t>Phạm vi</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Ô 1</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Ô 2</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )

    first = DocxParser().parse(content)
    second = DocxParser().parse(content)

    assert first == second
    assert first.raw_sha256 == sha256_bytes(content)
    assert [block.source_kind for block in first.blocks] == [
        "heading",
        "paragraph",
        "table_cell",
        "table_cell",
    ]
    assert [block.text for block in first.blocks] == [
        "LUẬT MẪU",
        "Điều 1.\tPhạm vi",
        "Ô 1",
        "Ô 2",
    ]
    for block in first.blocks:
        assert first.raw_text[block.source_start : block.source_end] == block.raw_text
    assert first.warnings == ()


def test_docx_parser_warns_on_merged_and_nested_tables() -> None:
    content = make_docx(
        '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
        "<w:p><w:r><w:t>Outer</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Nested</w:t></w:r></w:p>"
        "</w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>"
    )

    result = DocxParser().parse(content)

    assert "table contains merged cells" in result.warnings
    assert "table contains nested tables" in result.warnings


@pytest.mark.parametrize(
    "content",
    [
        b"not a zip",
        make_docx("<w:p>broken"),
    ],
)
def test_docx_parser_rejects_invalid_packages(content: bytes) -> None:
    with pytest.raises(ParserError):
        DocxParser().parse(content)


def test_default_registry_dispatches_docx() -> None:
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert isinstance(default_parser_registry().get(media_type), DocxParser)
