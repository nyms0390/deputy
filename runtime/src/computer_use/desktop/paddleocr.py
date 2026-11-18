"""TextReader over a PaddleOCR inference server: PP-OCR for text lines, PaddleOCR-VL for layout.

Each endpoint takes one image as a multipart upload and answers with a list of pages, each `{"res": {...}}`: text
lines in `rec_texts`, `rec_scores` and `rec_boxes`, layout blocks in `parsing_res_list`."""

import math

import httpx

from computer_use.desktop.ocr import Block, Box, Line, OcrError

TEXT_PATH = "/infer/v6/raw"
LAYOUT_PATH = "/infer/vl/raw"
# The server runs one request per model at a time and queues the rest; layout on a full screen takes seconds.
TIMEOUT_SECONDS = 120
# The server's request log tells callers apart by this header.
CALLER = "computer-use"


class PaddleOcrReader:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=url.rstrip("/"), headers={"X-Caller": CALLER}, timeout=TIMEOUT_SECONDS, transport=transport,
        )

    async def lines(self, png: bytes) -> list[Line]:
        lines = []
        for res in await self._pages(TEXT_PATH, png):
            texts, scores, boxes = res.get("rec_texts"), res.get("rec_scores"), res.get("rec_boxes")
            if not (isinstance(texts, list) and isinstance(scores, list) and isinstance(boxes, list)
                    and len(texts) == len(scores) == len(boxes)):
                raise OcrError("the OCR service answered without aligned text lines, scores and boxes")
            for text, score, box in zip(texts, scores, boxes):
                lines.append(Line(_text(text), _box(box), _score(score)))
        return lines

    async def blocks(self, png: bytes) -> list[Block]:
        blocks = []
        for res in await self._pages(LAYOUT_PATH, png):
            parsed = res.get("parsing_res_list")
            if not isinstance(parsed, list):
                raise OcrError("the OCR service answered without layout blocks")
            for block in parsed:
                if not isinstance(block, dict):
                    raise OcrError("the OCR service answered with a malformed layout block")
                box = block.get("block_bbox")
                blocks.append(Block(
                    _text(block.get("block_label")), _text(block.get("block_content")),
                    None if box is None else _box(box),
                ))
        return blocks

    async def _pages(self, path: str, png: bytes) -> list[dict]:
        try:
            response = await self._http.post(path, files={"file": ("screen.png", png, "image/png")})
        except httpx.HTTPError as error:
            raise OcrError(f"the OCR service could not be reached ({type(error).__name__})") from None
        if response.status_code in (429, 503):
            raise OcrError(f"the OCR service is busy or its model is not loaded (HTTP {response.status_code})")
        if response.status_code != 200:
            raise OcrError(f"the OCR service refused the image (HTTP {response.status_code})")
        try:
            pages = response.json()
        except ValueError:
            raise OcrError("the OCR service answered with something other than JSON") from None
        if not isinstance(pages, list) or not all(isinstance(page, dict) and isinstance(page.get("res"), dict)
                                                  for page in pages):
            raise OcrError("the OCR service answered without a list of page results")
        return [page["res"] for page in pages]


def _text(value) -> str:
    if not isinstance(value, str):
        raise OcrError("the OCR service answered with text that is not a string")
    return value


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OcrError("the OCR service answered with a number that is not finite")
    return value


def _score(value) -> float:
    score = _number(value)
    if not 0 <= score <= 1:
        raise OcrError("the OCR service answered with a score outside 0 to 1")
    return float(score)


def _box(value) -> Box:
    if not isinstance(value, list) or len(value) != 4:
        raise OcrError("the OCR service answered with a box that is not four numbers")
    x0, y0, x1, y1 = (round(_number(each)) for each in value)
    return x0, y0, x1, y1
