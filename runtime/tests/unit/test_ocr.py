"""OCR of the desktop: the PaddleOCR adapter, and the tools that read the screen through a text reader."""

import io

import httpx
import pytest
from langchain_core.messages import AIMessage
from PIL import Image

from computer_use.adapters import build_text_reader
from computer_use.desktop.ocr import Block, Line, OcrError
from computer_use.desktop.paddleocr import CALLER, LAYOUT_PATH, TEXT_PATH, PaddleOcrReader
from computer_use.tools.ocr import ocr_tools

from conftest import run_turn, tool_call, use_script

TEXT_PAGE = {"res": {
    "rec_texts": ["Invoice 1042", "Total 1,234.50"],
    "rec_scores": [0.99, 0.62],
    "rec_boxes": [[10, 20, 110, 40], [10.4, 60, 150.6, 80]],
    "rec_polys": [],
}}
LAYOUT_PAGE = {"res": {"parsing_res_list": [
    {"block_label": "doc_title", "block_content": "Invoice", "block_bbox": [0, 0, 200, 30]},
    {"block_label": "table", "block_content": "<table><tr><td>1</td></tr></table>"},
]}}


def reader_answering(status: int, body, seen: list[httpx.Request] | None = None) -> PaddleOcrReader:
    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return PaddleOcrReader("http://ocr.test/", transport=httpx.MockTransport(respond))


async def test_text_lines_are_read_from_the_text_endpoint_as_one_png_upload():
    seen: list[httpx.Request] = []
    lines = await reader_answering(200, [TEXT_PAGE], seen).lines(b"png bytes")
    assert lines == [Line("Invoice 1042", (10, 20, 110, 40), 0.99), Line("Total 1,234.50", (10, 60, 151, 80), 0.62)]
    [request] = seen
    assert request.url == f"http://ocr.test{TEXT_PATH}" and request.headers["X-Caller"] == CALLER
    assert b'name="file"' in request.content and b"png bytes" in request.content


async def test_layout_blocks_keep_reading_order_and_a_missing_box():
    seen: list[httpx.Request] = []
    blocks = await reader_answering(200, [LAYOUT_PAGE], seen).blocks(b"png")
    assert blocks == [Block("doc_title", "Invoice", (0, 0, 200, 30)),
                      Block("table", "<table><tr><td>1</td></tr></table>", None)]
    assert seen[0].url.path == LAYOUT_PATH


@pytest.mark.parametrize("status, body, says", [
    (503, {"detail": "engine not loaded"}, "busy or its model is not loaded"),
    (429, {"detail": "queue full"}, "busy"),
    (415, {"detail": "unsupported"}, r"refused the image \(HTTP 415\)"),
    (200, {"res": {}}, "list of page results"),
    (200, [{"res": {"rec_texts": ["a"], "rec_scores": [], "rec_boxes": []}}], "aligned"),
    (200, [{"res": {"rec_texts": ["a"], "rec_scores": [1.5], "rec_boxes": [[0, 0, 1, 1]]}}], "score outside"),
    (200, [{"res": {"rec_texts": ["a"], "rec_scores": [0.5], "rec_boxes": [[0, 0, 1]]}}], "four numbers"),
])
async def test_a_failed_or_malformed_answer_is_an_ocr_error(status, body, says):
    with pytest.raises(OcrError, match=says):
        await reader_answering(status, body).lines(b"png")


async def test_an_unreachable_service_is_an_ocr_error():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    reader = PaddleOcrReader("http://ocr.test", transport=httpx.MockTransport(refuse))
    with pytest.raises(OcrError, match="could not be reached"):
        await reader.blocks(b"png")


class FakeReader:
    """Answers with fixed results and keeps the images it was given."""

    def __init__(self, lines=(), blocks=(), fail: str | None = None):
        self.given: list[Image.Image] = []
        self._lines, self._blocks, self.fail = list(lines), list(blocks), fail

    async def lines(self, png: bytes) -> list[Line]:
        return self._answer(png, self._lines)

    async def blocks(self, png: bytes) -> list[Block]:
        return self._answer(png, self._blocks)

    def _answer(self, png, found):
        self.given.append(Image.open(io.BytesIO(png)))
        if self.fail:
            raise OcrError(self.fail)
        return found


async def call(tool, call_id="c1", **args):
    return await tool.ainvoke(tool_call(call_id, tool.name, **args))


async def test_read_text_reads_a_region_and_gives_full_screen_positions(executor, computer):
    reader = FakeReader(lines=[Line("Invoice 1042", (10, 20, 110, 40), 0.99), Line("1,234.50", (10, 60, 150, 80), 0.6)])
    read_text, _ = ocr_tools(executor, reader)
    result = await call(read_text, region=[100, 200, 500, 400])
    assert result.status == "success" and reader.given[0].size == (400, 200)
    assert result.content.splitlines() == [
        "2 lines of text in region (100, 200)-(500, 400); center and box in full-screen screenshot pixels.",
        '1. "Invoice 1042" at (160, 230), box [110, 220, 210, 240]',
        '2. "1,234.50" at (180, 270), box [110, 260, 250, 280] (unsure)',
    ]
    assert computer.calls == []  # reading sends no input


async def test_read_layout_reads_the_whole_screen_without_a_region(executor):
    reader = FakeReader(blocks=[Block("table", "<table></table>", (0, 0, 50, 20)), Block("text", "Note", None)])
    _, read_layout = ocr_tools(executor, reader)
    result = await call(read_layout)
    assert reader.given[0].size == (1280, 800)
    assert result.content == (
        "2 blocks in reading order in region (0, 0)-(1280, 800); boxes in full-screen screenshot pixels.\n"
        "1. [table, box [0, 0, 50, 20]]\n<table></table>\n2. [text]\nNote"
    )


async def test_reading_finds_nothing_or_fails_as_a_tool_result(executor):
    read_text, _ = ocr_tools(executor, FakeReader())
    assert (await call(read_text)).content == "No text found in region (0, 0)-(1280, 800)."
    read_text, _ = ocr_tools(executor, FakeReader(fail="the OCR service could not be reached (ConnectError)"))
    failed = await call(read_text)
    assert failed.status == "error" and failed.content == "OCR failed: the OCR service could not be reached (ConnectError)."


async def test_a_region_outside_the_screen_is_refused_before_reading(executor):
    reader = FakeReader()
    read_text, _ = ocr_tools(executor, reader)
    result = await call(read_text, region=[0, 0, 1281, 10])
    assert result.status == "error" and "not a region inside the 1280x800 screen" in result.content
    assert reader.given == []


async def test_nothing_is_read_while_the_user_has_the_desktop(executor, control):
    control.write("user", "taken over by user")
    reader = FakeReader()
    read_text, _ = ocr_tools(executor, reader)
    result = await call(read_text)
    assert result.status == "error" and "the user has control" in result.content and reader.given == []


def test_the_reader_is_built_only_when_a_service_is_named(settings):
    assert build_text_reader(settings) is None
    assert isinstance(build_text_reader(settings.model_copy(update={"ocr_url": "http://ocr.test"})), PaddleOcrReader)


async def test_a_task_has_the_ocr_tools_only_with_a_reader(settings, computer, monkeypatch):
    async def offered(**reader) -> set[str]:
        model, _ = use_script(monkeypatch, [AIMessage(content="Done.")])
        await run_turn(settings, "read the invoice", max_steps=5, computer=computer, **reader)
        return {tool.name for tool in model.bindings[0]["tools"]}

    assert {"read_text", "read_layout"} <= await offered(ocr=FakeReader())
    assert not {"read_text", "read_layout"} & await offered()
