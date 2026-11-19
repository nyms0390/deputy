"""The smallest files of each kind a case attaches: a workbook, a Word document, a text PDF and a CSV, written with the
standard library so that what a case holds is exactly what its answer key says."""

import io
import zipfile
from collections.abc import Sequence
from decimal import Decimal
from xml.sax.saxutils import escape, quoteattr

PACKAGE = "http://schemas.openxmlformats.org/package/2006"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml"
WORD_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def package(main: str, kinds: dict[str, str], parts: dict[str, str]) -> bytes:
    """An Office file: its `parts` zipped with the two that every package needs. It opens at `main`; `kinds` gives the
    content type of each part that is not plain XML."""
    types = "".join(f'<Override PartName="/{name}" ContentType={quoteattr(kind)}/>' for name, kind in kinds.items())
    listed = {
        "[Content_Types].xml": (
            f'<Types xmlns="{PACKAGE}/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{types}</Types>'
        ),
        "_rels/.rels": relationships(("officeDocument", main)),
        **parts,
    }
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in listed.items():
            # A fixed date, so the same parts are the same file whenever it is written.
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + text)
    return data.getvalue()


def relationships(*links: tuple[str, str]) -> str:
    """A part's links to others, each a kind and a target, numbered rId1 onwards."""
    return f'<Relationships xmlns="{PACKAGE}/relationships">' + "".join(
        f'<Relationship Id="rId{number}" Type="{OFFICE}/{kind}" Target={quoteattr(target)}/>'
        for number, (kind, target) in enumerate(links, 1)
    ) + "</Relationships>"


def column(index: int) -> str:
    """A column's letters, from 0: A, B, ..., Z, AA."""
    letters = ""
    while index >= 0:
        letters = chr(ord("A") + index % 26) + letters
        index = index // 26 - 1
    return letters


def cell(reference: str, value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return f'<c r="{reference}"><v>{value}</v></c>'
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def workbook(sheet: str, rows: Sequence[Sequence]) -> bytes:
    """An .xlsx of one sheet. A whole number or a Decimal is a number, None an empty cell, anything else text."""
    lines = "".join(
        f'<row r="{number}">' + "".join(cell(f"{column(at)}{number}", value) for at, value in enumerate(row)) + "</row>"
        for number, row in enumerate(rows, 1)
    )
    kinds = {"xl/workbook.xml": f"{SHEET_TYPE}.sheet.main+xml", "xl/worksheets/sheet1.xml": f"{SHEET_TYPE}.worksheet+xml"}
    return package("xl/workbook.xml", kinds, {
        "xl/workbook.xml": (
            f'<workbook xmlns="{SHEET}" xmlns:r="{OFFICE}"><sheets>'
            f'<sheet name={quoteattr(sheet)} sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": relationships(("worksheet", "worksheets/sheet1.xml")),
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{SHEET}"><sheetData>{lines}</sheetData></worksheet>',
    })


def document(paragraphs: Sequence[str]) -> bytes:
    """A .docx of plain paragraphs, an empty text being an empty line."""
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>' for text in paragraphs)
    return package("word/document.xml", {"word/document.xml": WORD_TYPE}, {
        "word/document.xml": f'<w:document xmlns:w="{WORD}"><w:body>{body}</w:body></w:document>',
    })


def table(rows: Sequence[Sequence]) -> bytes:
    """A .csv of values that need no quoting."""
    if any("," in str(value) or '"' in str(value) or "\n" in str(value) for row in rows for value in row):
        raise ValueError("a value needs quoting")
    return "".join(",".join(str(value) for value in row) + "\r\n" for row in rows).encode()


FONT, CATALOG, PAGES = 1, 2, 3  # the PDF's fixed objects; each page takes the next two numbers


def pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """A PDF whose text can be selected: each page its lines in one font, from the top. ASCII only."""
    objects = {
        FONT: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        CATALOG: f"<< /Type /Catalog /Pages {PAGES} 0 R >>".encode(),
    }
    kids = []
    for lines in pages:
        page = len(objects) + 2  # PAGES is written last
        shown = "\n".join(f"({escaped(line)}) '" for line in lines)
        stream = f"BT /F1 10 Tf 13 TL 54 760 Td\n{shown}\nET".encode("ascii")
        objects[page] = (
            f"<< /Type /Page /Parent {PAGES} 0 R /MediaBox [0 0 612 792] /Contents {page + 1} 0 R "
            f"/Resources << /Font << /F1 {FONT} 0 R >> >> >>"
        ).encode()
        objects[page + 1] = b"<< /Length %d >>\nstream\n%b\nendstream" % (len(stream), stream)
        kids.append(f"{page} 0 R")
    objects[PAGES] = f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(kids)}] >>".encode()
    # The comment's four high bytes mark the file as binary, as the format advises: it is never read as text.
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%b\nendobj\n" % (number, objects[number])
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for number in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, CATALOG, start)
    return bytes(out)


def escaped(line: str) -> str:
    line.encode("ascii")  # UnicodeEncodeError for text the one font cannot show
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
