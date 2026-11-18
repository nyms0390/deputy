"""Text reader contract: the text on a screenshot, read by an OCR service rather than the agent's model.

PaddleOcrReader (desktop.paddleocr) is the HTTP adapter; adapters.build_text_reader picks it, or none. Without a
reader, the ocr tools are not offered. Boxes are pixels of the image the reader was given."""

from dataclasses import dataclass
from typing import Protocol

Box = tuple[int, int, int, int]  # x0, y0, x1, y1


@dataclass(frozen=True)
class Line:
    """One line of text, as the text model read it."""

    text: str
    box: Box
    score: float  # the model's confidence, 0 to 1


@dataclass(frozen=True)
class Block:
    """One part of a page's layout, in reading order: a title, a paragraph, a table (as HTML) and so on."""

    label: str
    text: str
    box: Box | None


class OcrError(RuntimeError):
    """The service could not be reached, refused the image or answered in a shape this does not read."""


class TextReader(Protocol):
    async def lines(self, png: bytes) -> list[Line]: ...  # exact text, line by line
    async def blocks(self, png: bytes) -> list[Block]: ...  # layout: tables, reading order, structure
