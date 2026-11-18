"""Model-facing OCR of the desktop: exact text lines, and layout, read off the screen by a text reader."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, tool

from computer_use.computers import ComputerError, Frame
from computer_use.desktop.executor import ActionResult, Executor
from computer_use.desktop.ocr import Block, Box, Line, OcrError, TextReader
from computer_use.tools.common import CallId
from computer_use.tools.desktop import RegionArg, reply

# Lines the text model scored below this are marked, so the model checks them before relying on their characters.
UNSURE = 0.9


def ocr_tools(executor: Executor, reader: TextReader) -> list[BaseTool]:
    async def read[T](
        name: str, call_id: str, region: Sequence[int] | None,
        recognize: Callable[[bytes], Awaitable[list[T]]], render: Callable[[list[T], Box], str],
    ) -> ToolMessage:
        """The screen, or a region of it, read by `recognize`, its boxes moved back to full-screen pixels."""
        if held := await executor.held():  # the user has the desktop, or it is disconnected
            return reply(held, call_id, name)
        try:
            screen = await executor.computer.image()
        except ComputerError as error:
            return reply(ActionResult(False, f"No screenshot: {error}"), call_id, name)
        x0, y0, x1, y1 = region or (0, 0, screen.width, screen.height)
        if not (0 <= x0 < x1 <= screen.width and 0 <= y0 < y1 <= screen.height):
            refused = f"{region} is not a region inside the {screen.width}x{screen.height} screen"
            return reply(ActionResult(False, refused), call_id, name)
        crop = await asyncio.to_thread(Frame.encode, screen.crop((x0, y0, x1, y1)))
        try:
            found = await recognize(crop.png)
        except OcrError as error:
            return reply(ActionResult(False, f"OCR failed: {error}."), call_id, name)
        return reply(ActionResult(True, render(found, (x0, y0, x1, y1))), call_id, name)

    @tool
    async def read_text(region: RegionArg | None = None, *, call_id: CallId) -> ToolMessage:
        """Read the text on the screen, or in a region of it, with an OCR model: each line's exact characters and
        where it is.

        Use it when exact characters matter and the screenshot leaves them uncertain, such as numbers, codes, names
        and small print, or to find where a piece of text is so you can act on it. A region around what you need
        reads more reliably than the whole screen. Returns each line with its center and box in full-screen
        screenshot pixels; lines marked unsure scored low, so check them before relying on them. Does not change
        the screen.
        """
        return await read("read_text", call_id, region, reader.lines, text_report)

    @tool
    async def read_layout(region: RegionArg | None = None, *, call_id: CallId) -> ToolMessage:
        """Read the layout of the screen, or a region of it, with a document model: titles, paragraphs and tables
        (tables as HTML) in reading order, with each block's box in full-screen screenshot pixels when it has one.

        Use it for tables and documents whose structure matters; use read_text for the exact characters of a value.
        Slower than read_text. Does not change the screen.
        """
        return await read("read_layout", call_id, region, reader.blocks, layout_report)

    return [read_text, read_layout]


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
