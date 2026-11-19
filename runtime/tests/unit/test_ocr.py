"""OCR of the desktop: the PaddleOCR adapter, and the tools that read the screen through a text reader."""

import io
import zipfile

import httpx
import pytest
from langchain_core.messages import AIMessage
from PIL import Image

from computer_use.adapters import build_text_reader
from computer_use.desktop.ocr import Block, Line, OcrError
from computer_use.desktop.paddleocr import CALLER, LAYOUT_PATH, TEXT_PATH, PaddleOcrReader
from computer_use.agent import coding_agent
from computer_use.tools.ocr import MAX_PAGES, file_ocr_tools, ocr_tools

from conftest import ScriptedModel, run_turn, tool_call, use_script

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


async def test_ocr_reads_a_region_as_text_and_gives_full_screen_positions(executor, computer):
    reader = FakeReader(lines=[Line("Invoice 1042", (10, 20, 110, 40), 0.99), Line("1,234.50", (10, 60, 150, 80), 0.6)])
    [ocr] = ocr_tools(executor, reader)
    result = await call(ocr, region=[100, 200, 500, 400])
    assert result.status == "success" and reader.given[0].size == (400, 200)
    assert result.content.splitlines() == [
        "2 lines of text in region (100, 200)-(500, 400); center and box in full-screen screenshot pixels.",
        '1. "Invoice 1042" at (160, 230), box [110, 220, 210, 240]',
        '2. "1,234.50" at (180, 270), box [110, 260, 250, 280] (unsure)',
    ]
    assert computer.calls == []  # reading sends no input


async def test_ocr_reads_the_layout_of_the_whole_screen_without_a_region(executor):
    reader = FakeReader(blocks=[Block("table", "<table></table>", (0, 0, 50, 20)), Block("text", "Note", None)])
    [ocr] = ocr_tools(executor, reader)
    result = await call(ocr, mode="layout")
    assert reader.given[0].size == (1280, 800)
    assert result.content == (
        "2 blocks in reading order in region (0, 0)-(1280, 800); boxes in full-screen screenshot pixels.\n"
        "1. [table, box [0, 0, 50, 20]]\n<table></table>\n2. [text]\nNote"
    )


async def test_reading_finds_nothing_or_fails_as_a_tool_result(executor):
    [ocr] = ocr_tools(executor, FakeReader())
    assert (await call(ocr)).content == "No text found in region (0, 0)-(1280, 800)."
    [ocr] = ocr_tools(executor, FakeReader(fail="the OCR service could not be reached (ConnectError)"))
    failed = await call(ocr)
    assert failed.status == "error" and failed.content == "OCR failed: the OCR service could not be reached (ConnectError)."


async def test_a_region_outside_the_screen_is_refused_before_reading(executor):
    reader = FakeReader()
    [ocr] = ocr_tools(executor, reader)
    result = await call(ocr, region=[0, 0, 1281, 10])
    assert result.status == "error" and "not a region inside the 1280x800 screen" in result.content
    assert reader.given == []


async def test_nothing_is_read_while_the_user_has_the_desktop(executor, control):
    control.write("user", "taken over by user")
    reader = FakeReader()
    [ocr] = ocr_tools(executor, reader)
    result = await call(ocr)
    assert result.status == "error" and "the user has control" in result.content and reader.given == []


def test_the_reader_is_built_only_when_a_service_is_named(settings):
    assert build_text_reader(settings) is None
    assert isinstance(build_text_reader(settings.model_copy(update={"ocr_url": "http://ocr.test"})), PaddleOcrReader)


async def test_a_task_has_the_ocr_tools_only_with_a_reader(settings, computer, monkeypatch):
    async def offered(**reader) -> set[str]:
        model, _ = use_script(monkeypatch, [AIMessage(content="Done.")])
        await run_turn(settings, "read the invoice", max_steps=5, computer=computer, **reader)
        return {tool.name for tool in model.bindings[0]["tools"]}

    assert "ocr" in await offered(ocr=FakeReader())
    assert "ocr" not in await offered()


def pdf(pages: int) -> bytes:
    """A PDF of blank 300x200 pages, as a scanner without a text layer makes them."""
    images = [Image.new("RGB", (300, 200), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:], resolution=72)
    return buffer.getvalue()


def office(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def png(size=(40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def file_ocr(code_runner, reader):
    [ocr] = file_ocr_tools(code_runner, "thread", reader)
    return ocr


async def test_ocr_reads_each_page_of_a_scanned_pdf_rendered_at_144_dpi(code_runner):
    code_runner.files["attachments/scan.pdf"] = pdf(2)
    reader = FakeReader(lines=[Line("Invoice 1042", (0, 0, 9, 9), 0.99), Line("1,234.50", (0, 0, 9, 9), 0.5)])
    result = await call(file_ocr(code_runner, reader), path="attachments/scan.pdf")
    assert [image.size for image in reader.given] == [(600, 400), (600, 400)]
    page = "Invoice 1042\n1,234.50 (unsure)"
    assert result.content == f"Page 1:\n{page}\n\nPage 2:\n{page}"


async def test_ocr_reads_the_layout_of_the_pages_asked_for_or_an_image(code_runner):
    code_runner.files["scan.pdf"] = pdf(3)
    code_runner.files["photo.png"] = png()
    reader = FakeReader(blocks=[Block("table", "<table></table>", None)])
    ocr = file_ocr(code_runner, reader)
    assert (await call(ocr, path="scan.pdf", pages=[3], mode="layout")).content == "Page 3:\n[table]\n<table></table>"
    assert (await call(ocr, path="photo.png", mode="layout")).content == "photo.png:\n[table]\n<table></table>"
    assert reader.given[-1].size == (40, 30)


@pytest.mark.parametrize("path, pages, says", [
    ("missing.pdf", None, "there is no missing.pdf in your working folder"),
    ("long.pdf", None, f"has {MAX_PAGES + 2} pages; ask for at most {MAX_PAGES}"),
    ("long.pdf", [MAX_PAGES + 3], f"has {MAX_PAGES + 2} pages, not page {MAX_PAGES + 3}"),
    ("photo.png", [2], "an image, which has only page 1"),
    ("notes.txt", None, "not a PDF, an Office file or an image"),
    ("empty.docx", None, "has no embedded pictures"),
])
async def test_a_file_that_cannot_be_read_is_refused_before_ocr(code_runner, path, pages, says):
    code_runner.files.update({"long.pdf": pdf(MAX_PAGES + 2), "photo.png": png(), "notes.txt": "plain text",
                              "empty.docx": office({"word/document.xml": b"<w:document/>"})})
    reader = FakeReader()
    result = await call(file_ocr(code_runner, reader), path=path, **({"pages": pages} if pages else {}))
    assert result.status == "error" and says in result.content and reader.given == []


async def test_ocr_reads_the_pictures_in_an_office_file_in_name_order(code_runner):
    code_runner.files["report.docx"] = office({
        "word/document.xml": b"<w:document/>",
        "word/media/image10.png": png((10, 10)),
        "word/media/image2.png": png((20, 20)),
        "word/media/image3.emf": b"vector drawing",
    })
    reader = FakeReader(lines=[Line("Total 42", (0, 0, 9, 9), 0.99)])
    ocr = file_ocr(code_runner, reader)
    assert (await call(ocr, path="report.docx")).content == (
        "Picture 1 (image2.png):\nTotal 42\n\n"
        "Picture 2 (image3.emf): not a picture this reads, such as a vector drawing (EMF or WMF)\n\n"
        "Picture 3 (image10.png):\nTotal 42"
    )
    assert [image.size for image in reader.given] == [(20, 20), (10, 10)]
    picked = await call(ocr, path="report.docx", pages=[3])
    assert picked.content == "Picture 3 (image10.png):\nTotal 42"
    refused = await call(ocr, path="report.docx", pages=[4])
    assert refused.status == "error" and "has 3 pictures, not picture 4" in refused.content


async def test_an_ocr_failure_on_a_page_is_the_agents_to_hear(code_runner):
    code_runner.files["scan.pdf"] = pdf(1)
    ocr = file_ocr(code_runner, FakeReader(fail="the OCR service is busy or its model is not loaded (HTTP 503)"))
    result = await call(ocr, path="scan.pdf")
    assert result.status == "error" and result.content.startswith("OCR failed on Page 1: the OCR service is busy")


async def test_a_coding_task_has_the_ocr_tools_only_with_a_reader(settings, computer, code_runner, monkeypatch):
    async def offered(**reader) -> set[str]:
        handing = AIMessage(content="", tool_calls=[
            tool_call("m1", "start_task", agent="coding", title="Task", task="Read the scan.")])
        use_script(monkeypatch, [], main=[handing, AIMessage("Done.")])
        model = ScriptedModel(messages=iter([AIMessage("Read.")]), received=[], bindings=[])
        monkeypatch.setattr(coding_agent, "build_chat_model", lambda _spec, _settings: model)
        await run_turn(settings, "read my scan", max_steps=5, computer=computer, code_runner=code_runner, **reader)
        return {tool.name for tool in model.bindings[0]["tools"]}

    assert {"bash", "ocr"} <= await offered(ocr=FakeReader())
    assert "ocr" not in await offered()
