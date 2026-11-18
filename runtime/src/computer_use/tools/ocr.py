"""Model-facing OCR through a text reader, as one tool for each agent: exact text lines or layout, read off the
desktop for the computer agent, and off PDFs, images and Office files' pictures in the working folder for the coding
agent."""

import asyncio
import io
import json
import re
import zipfile
from typing import Annotated, Literal

import pypdfium2
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, ToolException, tool
from PIL import Image
from pydantic import Field

from computer_use.computers import ComputerError, Frame
from computer_use.desktop.executor import ActionResult, Executor
from computer_use.desktop.ocr import Block, Box, Line, OcrError, TextReader
from computer_use.sandbox import CodeRunner
from computer_use.tools.common import CallId
from computer_use.tools.desktop import RegionArg, reply

# Lines the text model scored below this are marked, so the model checks them before relying on their characters.
UNSURE = 0.9
# Pages of a file read in one call: the service reads one page at a time, a scanned page in a second or more.
MAX_PAGES = 10
# A PDF page is rendered at this many times 72 dpi: 144 dpi, which reads small print and keeps an A4 page under 2 MP.
PDF_SCALE = 2
# Where Word, Excel and PowerPoint keep the pictures embedded in a document.
OFFICE_MEDIA = re.compile(r"(word|xl|ppt)/media/[^/]+$")
MAX_PICTURE_BYTES = 50 * 2**20


ModeArg = Annotated[Literal["text", "layout"], Field(description=(
    "text: each line's exact characters, from a text model. layout: titles, paragraphs and tables (tables as HTML) "
    "in reading order, from a slower document model"
))]


def ocr_tools(executor: Executor, reader: TextReader) -> list[BaseTool]:
    @tool
    async def ocr(region: RegionArg | None = None, mode: ModeArg = "text", *, call_id: CallId) -> ToolMessage:
        """Read the screen, or a region of it, with an OCR model.

        Use mode text when exact characters matter and the screenshot leaves them uncertain, such as numbers, codes,
        names and small print, or to find where a piece of text is so you can act on it; use mode layout for tables
        and documents whose structure matters. A region around what you need reads more reliably than the whole
        screen. Returns each line, or block, with its box in full-screen screenshot pixels; lines marked unsure scored
        low, so check them before relying on them. Does not change the screen.
        """
        if held := await executor.held():  # the user has the desktop, or it is disconnected
            return reply(held, call_id, "ocr")
        try:
            screen = await executor.computer.image()
        except ComputerError as error:
            return reply(ActionResult(False, f"No screenshot: {error}"), call_id, "ocr")
        x0, y0, x1, y1 = box = tuple(region or (0, 0, screen.width, screen.height))
        if not (0 <= x0 < x1 <= screen.width and 0 <= y0 < y1 <= screen.height):
            refused = f"{region} is not a region inside the {screen.width}x{screen.height} screen"
            return reply(ActionResult(False, refused), call_id, "ocr")
        crop = await asyncio.to_thread(Frame.encode, screen.crop(box))
        try:
            if mode == "text":
                text = text_report(await reader.lines(crop.png), box)
            else:
                text = layout_report(await reader.blocks(crop.png), box)
        except OcrError as error:
            return reply(ActionResult(False, f"OCR failed: {error}."), call_id, "ocr")
        return reply(ActionResult(True, text), call_id, "ocr")

    return [ocr]


def _shift(box: Box, x: int, y: int) -> list[int]:
    x0, y0, x1, y1 = box
    return [x0 + x, y0 + y, x1 + x, y1 + y]


def _where(region: Box) -> str:
    x0, y0, x1, y1 = region
    return f"region ({x0}, {y0})-({x1}, {y1})"


def text_report(lines: list[Line], region: Box) -> str:
    if not lines:
        return f"No text found in {_where(region)}."
    rows = []
    for number, line in enumerate(lines, 1):
        x0, y0, x1, y1 = box = _shift(line.box, *region[:2])
        unsure = " (unsure)" if line.score < UNSURE else ""
        rows.append(f"{number}. {json.dumps(line.text, ensure_ascii=False)} at ({(x0 + x1) // 2}, {(y0 + y1) // 2}), "
                    f"box {box}{unsure}")
    head = f"{len(lines)} lines of text in {_where(region)}; center and box in full-screen screenshot pixels."
    return "\n".join([head, *rows])


def layout_report(blocks: list[Block], region: Box) -> str:
    if not blocks:
        return f"No layout found in {_where(region)}."
    rows = []
    for number, block in enumerate(blocks, 1):
        box = "" if block.box is None else f", box {_shift(block.box, *region[:2])}"
        rows.append(f"{number}. [{block.label}{box}]\n{block.text}")
    head = f"{len(blocks)} blocks in reading order in {_where(region)}; boxes in full-screen screenshot pixels."
    return "\n".join([head, *rows])


PagesArg = Annotated[list[int] | None, Field(description=(
    f"1-based numbers of a PDF's pages or an Office file's pictures, at most {MAX_PAGES}; omit for all of them when "
    "there are no more"
))]


def file_ocr_tools(runner: CodeRunner, thread: str, reader: TextReader) -> list[BaseTool]:
    """ocr of files in the conversation's folder on the code runner, which has no network: the runtime reads them."""

    @tool
    async def ocr(
        path: Annotated[str, Field(description="A PDF, an image, or a Word, Excel or PowerPoint file in your working folder.")],
        pages: PagesArg = None,
        mode: ModeArg = "text",
    ) -> str:
        """Read the pages of a PDF, an image, or the pictures embedded in a .docx, .xlsx or .pptx file with an OCR
        model.

        Use it for scanned pages and pictures of text, which extracting a file's text misses or garbles. Use mode text
        for exact characters and mode layout for tables and structure. An Office file's pictures are numbered in the
        order of their names in the file, not where they appear in it. Lines marked unsure scored low, so check them,
        such as with view_image, before relying on their characters.
        """
        try:
            data = await runner.read(thread, path)
        except FileNotFoundError:
            raise ToolException(f"Not executed: there is no {path} in your working folder.")
        except ValueError as refused:
            raise ToolException(f"Not executed: {refused}.")
        parts = []
        for name, png in await asyncio.to_thread(file_images, path, data, pages):
            if isinstance(png, str):  # why this picture is not read
                parts.append(f"{name}: {png}")
                continue
            try:
                found = page_text(await reader.lines(png)) if mode == "text" else page_layout(await reader.blocks(png))
            except OcrError as error:
                raise ToolException(f"OCR failed on {name}: {error}.")
            parts.append(f"{name}:\n{found}")
        return "\n\n".join(parts)

    ocr.handle_tool_error = True
    return [ocr]


def file_images(path: str, data: bytes, pages: list[int] | None) -> list[tuple[str, bytes | str]]:
    """Each page or picture asked for, by name, as a PNG or why it is not read: the pages of a PDF, the pictures in an
    Office file, or an image as its one page."""
    if data.startswith(b"%PDF-"):
        return _pdf_pages(path, data, pages)
    if zipfile.is_zipfile(io.BytesIO(data)):
        return _office_pictures(path, data, pages)
    if pages and pages != [1]:
        raise ToolException(f"Not executed: {path} is an image, which has only page 1.")
    png = _png(data)
    if isinstance(png, str):
        raise ToolException(f"Not executed: {path} is not a PDF, an Office file or an image this reads.")
    return [(path, png)]


def _chosen(path: str, count: int, pages: list[int] | None, what: str) -> list[int]:
    wanted = pages or list(range(1, count + 1))
    if len(wanted) > MAX_PAGES:
        raise ToolException(f"Not executed: {path} has {count} {what}s; ask for at most {MAX_PAGES} at a time with pages.")
    if missing := [number for number in wanted if not 1 <= number <= count]:
        raise ToolException(f"Not executed: {path} has {count} {what}s, not {what} {missing[0]}.")
    return wanted


def _pdf_pages(path: str, data: bytes, pages: list[int] | None) -> list[tuple[str, bytes | str]]:
    try:
        document = pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError as error:
        raise ToolException(f"Not executed: {path} does not open as a PDF ({error}).") from None
    try:
        return [
            (f"Page {number}", Frame.encode(document[number - 1].render(scale=PDF_SCALE).to_pil().convert("RGB")).png)
            for number in _chosen(path, len(document), pages, "page")
        ]
    finally:
        document.close()


def _office_pictures(path: str, data: bytes, pages: list[int] | None) -> list[tuple[str, bytes | str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = sorted((info for info in archive.infolist() if OFFICE_MEDIA.match(info.filename)),
                         key=lambda info: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", info.filename)])
        if not members:
            raise ToolException(f"Not executed: {path} has no embedded pictures.")
        found = []
        for number in _chosen(path, len(members), pages, "picture"):
            info = members[number - 1]
            name = f"Picture {number} ({info.filename.rsplit('/', 1)[-1]})"
            if info.file_size > MAX_PICTURE_BYTES:
                found.append((name, f"larger than {MAX_PICTURE_BYTES // 2**20} MB, not read"))
            else:
                found.append((name, _png(archive.read(info))))
        return found


def _png(data: bytes) -> bytes | str:
    """The picture as a PNG, or why it is not read."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return Frame.encode(image.convert("RGB")).png
    except (OSError, Image.DecompressionBombError):
        return "not a picture this reads, such as a vector drawing (EMF or WMF)"


def page_text(lines: list[Line]) -> str:
    if not lines:
        return "No text found."
    return "\n".join(f"{line.text} (unsure)" if line.score < UNSURE else line.text for line in lines)


def page_layout(blocks: list[Block]) -> str:
    if not blocks:
        return "No layout found."
    return "\n".join(f"[{block.label}]\n{block.text}" for block in blocks)
