"""The learning experiment (eval/learning) offline: that the generated files hold what the answer keys say, that no
simpler rule than the planted ones fits the training cases, that the scorer measures what it claims, and that the
runs keep what the scorer reads, against a fake API. Nothing here says how a real model does."""

import io
import json
import re
import sys
import zipfile
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

import pypdfium2
import pytest

from computer_use import attachments

EVAL = Path(__file__).resolve().parents[3] / "eval"
SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture(scope="module")
def learning():
    """The experiment's modules, imported as eval/learn runs them: with eval/ on the path."""
    sys.path.insert(0, str(EVAL))
    try:
        from learning import __main__ as command
        from learning import cases, runs, score
    finally:
        sys.path.remove(str(EVAL))
    return type("Learning", (), {"cases": cases, "runs": runs, "score": score, "main": staticmethod(command.main)})


@pytest.fixture(scope="module")
def made(learning, tmp_path_factory):
    """Both kinds of work, generated once: their folder and their cases."""
    out = tmp_path_factory.mktemp("experiment") / "out"
    return out, learning.cases.generate(out, 11)


def split(cases, name):
    return [case for case in cases if case.split == name]


def rows_of(workbook: bytes) -> list[list]:
    """A generated workbook's rows: numbers as Decimal, text as text."""
    sheet = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(workbook)).read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(f"{SHEET}row"):
        rows.append([Decimal(cell.find(f"{SHEET}v").text) if cell.get("t") is None
                     else cell.find(f"{SHEET}is/{SHEET}t").text for cell in row])
    return rows


def claims_of(case) -> list[tuple[Decimal, str]]:
    """A case's claims as its sheet has them: amount and status."""
    return [(row[3], row[4]) for row in rows_of(case.files["claims.xlsx"]) if row and str(row[0]).startswith("CL-")]


def pages_of(pdf: bytes) -> list[str]:
    document = pypdfium2.PdfDocument(pdf)
    try:
        return [document[index].get_textpage().get_text_range() for index in range(len(document))]
    finally:
        document.close()


def letter_of(case) -> str:
    body = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(case.files["letter.docx"])).read("word/document.xml"))
    return "\n".join(text.text or "" for text in body.iter(f"{WORD}t"))


def test_each_kind_of_work_is_written_with_its_cases_keys_and_skills(learning, made):
    out, cases = made
    for name, work in learning.cases.WORKS.items():
        root = out / name
        assert [len(split(cases[name], each)) for each in ("train", "heldout")] == [6, 6]
        assert (root / "task.txt").read_text().strip() == work.task
        assert set(json.loads((root / "rules.json").read_text())) == {
            field.rule for case in cases[name] for field in case.fields.values() if field.rule
        }
        for case in cases[name]:
            folder = root / case.split / case.id
            assert {path.name for path in (folder / "files").iterdir()} == set(case.files)
            # Only a training case comes with its answer, and no case's folder holds its key.
            assert (folder / "answer.json").exists() == (case.split == "train")
            assert json.loads((root / "keys" / f"{case.id}.json").read_text())["fields"].keys() == case.answer.keys()
        for arm in ("oracle", "sham"):
            assert (root / arm / work.skill / "SKILL.md").read_text().startswith(f"---\nname: {work.skill}\n")
    assert json.loads((out / "expenses" / "train" / "expenses-train-01" / "answer.json").read_text()) == \
        cases["expenses"][0].answer


def test_the_same_seed_writes_the_same_cases_and_a_full_folder_is_refused(learning, made, tmp_path):
    out, cases = made
    again = learning.cases.generate(tmp_path / "again", 11)
    assert [case.files for case in again["letters"]] == [case.files for case in cases["letters"]]
    with pytest.raises(ValueError, match="not empty"):
        learning.cases.generate(out, 11)


def test_the_long_pdf_reaches_no_model_whole_and_only_its_last_page_holds_the_change(learning, made):
    _, cases = made
    for case in (*cases["expenses"], *cases["letters"]):
        pdf = case.files[case.last_page.file]
        assert not attachments.model_reads(pdf) and attachments.as_text(pdf) is None
        pages = pages_of(pdf)
        assert len(pages) == case.last_page.page == learning.cases.PDF_PAGES
        final = case.fields[case.last_page.field].value
        assert case.last_page.first in pages[0]
        assert any(marker in pages[-1] for marker in case.last_page.markers)
        assert not any(marker in page for marker in case.last_page.markers for page in pages[:-1])
        assert case.last_page.changes == (final != case.last_page.first)
        if case.last_page.changes:
            assert final in pages[-1] and not any(final in page for page in pages[:-1])


def test_an_expense_answer_follows_the_planted_rules_from_its_own_sheet(made):
    _, cases = made
    for case in cases["expenses"]:
        rows = rows_of(case.files["claims.xlsx"])
        counted = [amount for amount, status in claims_of(case) if status != "void"]
        answer = case.answer
        assert [rows[0][1], rows[1][1]] == [answer["employee"], answer["cost_center"]]
        assert answer["period"] == rows[3][1].replace("-", "/")
        assert answer["claim_count"] == len(counted) == len(claims_of(case)) - 2
        total = sum(counted)
        assert total % 1 not in (0, Decimal("0.5"))  # it rounds one way only
        assert answer["total_amount"] == int(total.quantize(Decimal(1), rounding="ROUND_HALF_UP"))
        assert answer["review_flag"] == ("Y" if max(counted) >= 1000 else "N")
        assert answer["payment_reference"] is None


def test_no_simpler_rule_explains_the_review_flag_in_either_split(made):
    _, cases = made
    for each in ("train", "heldout"):
        seen = [(case.answer["review_flag"], case.answer["total_amount"], claims_of(case))
                for case in split(cases["expenses"], each)]
        flagged = [total for flag, total, _ in seen if flag == "Y"]
        unflagged = [(total, claims) for flag, total, claims in seen if flag == "N"]
        assert flagged and min(flagged) < max(total for total, _ in unflagged)  # not the total
        assert any(amount >= 1000 for _, claims in unflagged for amount, status in claims if status == "void")
        assert any(950 <= amount < 1000 for _, claims in unflagged for amount, status in claims if status != "void")
    assert [case.answer["review_flag"] for case in split(cases["expenses"], "train")].count("Y") == 1


def test_no_simpler_rule_explains_a_letters_priority_or_category(made):
    _, cases = made
    for each in ("train", "heldout"):
        seen = []
        for case in split(cases["letters"], each):
            text = letter_of(case)
            amount = Decimal(re.search(r"\d+\.\d\d", text).group())
            urgent = "urgent" in text
            answer = case.answer
            assert answer["priority"] == ("P1" if urgent or amount > 500 else "P3")
            assert answer["category"] == ("C2" if "card" in text else "B1" if "fee" in text else "G0")
            day, month = re.search(r"Received on (\d+) (\w+) 2026", text).groups()
            assert answer["received_date"].startswith(f"{int(day):02d}/") and answer["account_number"] in text
            seen.append((answer["category"], answer["priority"], urgent, amount))
        assert {("C2", "P3"), ("B1", "P3"), ("B1", "P1")} <= {(category, priority) for category, priority, *_ in seen}
        assert any(priority == "P1" and not urgent for _, priority, urgent, _ in seen)  # not urgency alone
        assert any(460 <= amount < 500 and priority == "P3" for _, priority, urgent, amount in seen)
        assert any(500 < amount < 550 and priority == "P1" and not urgent for _, priority, urgent, amount in seen)
    assert [case.answer["category"] for case in split(cases["letters"], "train")].count("C2") == 1


def test_a_field_is_graded_exactly_after_light_tidying(learning):
    grade = learning.score.grade
    assert grade("Mira  Lowther", " mira lowther ") == "right"
    assert grade("2026/08", "2026-08") == "wrong"
    assert [grade(5434, each) for each in (5434, "5,434", 5434.0, "5434.5", True, [5434])] == \
        ["right", "right", "right", "wrong", "wrong", "wrong"]
    assert [grade("Y", each) for each in (None, " ", {"flag": "Y"})] == ["missing", "missing", "wrong"]
    assert [grade(None, each) for each in (None, "", "REF-1")] == ["right", "right", "made_up"]


def test_a_replys_json_is_found_with_words_or_a_fence_around_it(learning):
    answer_of = learning.score.answer_of
    assert answer_of('Here it is:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert [answer_of(each) for each in ("", "no object", "{broken", "[1, 2]")] == [{}, {}, {}, {}]


def repeat(folder: Path, keys: dict, answer, read=lambda key: True, skill=None, digest="abc123abc123") -> Path:
    """A repeat's folder as `runs.heldout` writes it, each case answered by `answer(key)`. `read(key)` says whether
    its log shows the last page (None: no log); `skill` is one the main agent read."""
    for case, key in keys.items():
        (folder / case).mkdir(parents=True)
        result = {"stop_reason": "replied", "reply": json.dumps(answer(key))}
        (folder / case / "result.json").write_text(json.dumps(result))
        if (reached := read(key)) is None:
            continue
        events = [{"type": "action", "tool": "bash", "result": "Part 2\nRead this part."}]
        if reached:
            events.append({"type": "action", "tool": "bash", "result": f"x\n{key['last_page']['markers'][0]}\ny"})
        if skill:
            events.append({"type": "action", "agent": "main", "tool": "read_skill", "args": {"name": skill}})
        (folder / case / "run" / "turn").mkdir(parents=True)
        (folder / case / "run" / "turn" / "steps.jsonl").write_text("".join(json.dumps(each) + "\n" for each in events))
    (folder / "arm.json").write_text(json.dumps({"skills": {skill: digest} if skill else {}}))
    return folder


def by_key(key: dict) -> dict:
    return {name: field["value"] for name, field in key["fields"].items()}


def unlearned(key: dict) -> dict:
    """What an agent that knows no planted rule answers: the control fields, the first page's value, and a made-up
    one for the field no file holds."""
    answer = {name: field["value"] if field["kind"] == "control" else "REF-1" if field["kind"] == "absent" else "?"
              for name, field in key["fields"].items()}
    return answer | {key["last_page"]["field"]: key["last_page"]["first"]}


def test_the_criteria_pass_when_the_learned_arm_follows_the_rules(learning, made, tmp_path):
    work = made[0] / "letters"
    keys = learning.score.load_keys(work)
    arms = {
        "baseline": [repeat(tmp_path / f"b{n}", keys, unlearned, read=lambda key: False) for n in (1, 2)],
        "sham": [repeat(tmp_path / "s1", keys, unlearned, read=lambda key: False, skill="letter-follow-up")],
        "learned": [repeat(tmp_path / f"l{n}", keys, by_key, skill="letter-follow-up", digest="fed456fed456")
                    for n in (1, 2)],
    }
    text, data = learning.score.report(work, arms, "learned", ["baseline", "sham"])
    assert [each["passed"] for each in data["criteria"]] == [True, True, True, True]
    assert data["arms"]["learned"][0] | {"planted": 1.0, "control": 1.0, "made_up": 0, "last_page": 1.0,
                                         "used_skill": 6, "not_replied": 0} == data["arms"]["learned"][0]
    # Where the last page changes nothing, the first page's value is right: it counts as a planted field.
    assert data["arms"]["baseline"][0] | {"planted": 2 / 24, "made_up": 6, "last_page": 0.0, "reached": 0.0} == \
        data["arms"]["baseline"][0]
    assert data["rules"]["letters.contact"] == {"baseline": [0, 8], "sham": [0, 4], "learned": [8, 8]}
    assert "| learned | letter-follow-up fed456fe | 100%, 100% |" in text and "| baseline | none |" in text
    assert text.count("- PASS: ") == 4


def test_a_right_value_that_did_not_come_from_the_last_page_does_not_pass(learning, made, tmp_path):
    work = made[0] / "expenses"
    keys = learning.score.load_keys(work)
    arms = {
        "baseline": [repeat(tmp_path / "b", keys, unlearned, read=lambda key: False)],
        "sham": [repeat(tmp_path / "s", keys, unlearned, read=lambda key: False)],
        "learned": [repeat(tmp_path / "l", keys, by_key, read=lambda key: False)],
    }
    _, data = learning.score.report(work, arms, "learned", ["baseline", "sham"])
    assert [each["passed"] for each in data["criteria"]] == [True, True, True, False]


def test_cases_without_logs_leave_the_last_page_unknown_and_missing_runs_are_counted(learning, made, tmp_path):
    work = made[0] / "expenses"
    keys = learning.score.load_keys(work)
    (tmp_path / "empty").mkdir()
    arms = {
        "baseline": [tmp_path / "empty"],
        "sham": [repeat(tmp_path / "s", keys, unlearned, read=lambda key: None)],
        "learned": [repeat(tmp_path / "l", keys, by_key, read=lambda key: None)],
    }
    text, data = learning.score.report(work, arms, "learned", ["baseline", "sham"])
    assert data["arms"]["baseline"][0] | {"planted": 0.0, "control": 0.0, "made_up": 0, "not_replied": 6} == \
        data["arms"]["baseline"][0]
    assert data["arms"]["learned"][0]["last_page"] is None and data["criteria"][3]["passed"] is None
    assert "| baseline | ? |" in text and "- ?: On the last-page rule" in text
    with pytest.raises(ValueError, match="no runs for learned"):
        learning.score.report(work, {"baseline": arms["baseline"], "sham": arms["sham"]}, "learned", ["baseline", "sham"])


def test_repeats_of_an_arm_that_saw_different_skills_are_said(learning, made, tmp_path):
    work = made[0] / "letters"
    keys = learning.score.load_keys(work)
    arms = {name: [repeat(tmp_path / f"{name}{n}", keys, by_key, skill="letter-follow-up", digest=digest * 6)
                   for n, digest in enumerate(digests)]
            for name, digests in (("baseline", ["aa"]), ("sham", ["bb"]), ("learned", ["cc", "dd"]))}
    text, _ = learning.score.report(work, arms, "learned", ["baseline", "sham"])
    assert "learned's repeats did not all see the same skills." in text


class FakeApi:
    """The runtime as the runs use it: each message starts a run whose log is `script(message)`'s events."""

    def __init__(self, script):
        self.script, self.sent, self.answers, self.stopped, self.logs = script, [], [], [], {}
        self.waiting = None

    def upload(self, file: Path) -> str:
        return f"upload:{file.name}"

    def start(self, goal, *, project=None, attachments=None, **_):
        run = f"run-{len(self.sent) + 1}"
        self.sent.append({"goal": goal, "project": project, "attachments": attachments})
        self.logs[run] = [{"type": "start", "kind": "turn", "thread": run}, *self.script(goal)]
        return {"id": run}

    def events(self, run, after=-1, full=False):
        for index, event in enumerate(self.logs[run]):
            if event["type"] == "confirm":
                self.waiting = event["id"]
            yield type("Event", (), {"kind": "line", "index": index, "data": event})
        yield type("Event", (), {"kind": "done", "index": None, "data": {}})

    def status(self):
        return {"running": [], "approval": {"id": self.waiting} if self.waiting else None}

    def runs(self, thread):
        return []

    def answer(self, id, decision, note=""):
        self.answers.append((id, decision, note))
        self.waiting = None

    def stop(self, thread):
        self.stopped.append(thread)

    def export_session(self, run, folder):
        (folder / run).mkdir(parents=True)
        (folder / run / "steps.jsonl").write_text("".join(json.dumps(each) + "\n" for each in self.logs[run]))


def test_a_heldout_run_keeps_what_the_scorer_reads_and_saves_no_skill(learning, made, tmp_path):
    work = made[0] / "letters"
    keys = learning.score.load_keys(work)
    replies = iter(by_key(key) for key in keys.values())
    api = FakeApi(lambda goal: [
        {"type": "confirm", "id": "c1", "tool": "propose_skill", "args": {}, "consequence": "Save a skill"},
        {"type": "action", "tool": "bash", "result": "Annex: changes of contact this month"},
        {"type": "end", "stop_reason": "replied", "summary": json.dumps(next(replies))},
    ])
    said = []
    learning.runs.heldout(api, work, tmp_path / "out", work / "oracle", said.append)
    assert [each["goal"] for each in api.sent] == [learning.cases.WORKS["letters"].task] * 6
    assert api.sent[0]["attachments"] == ["upload:accounts.csv", "upload:letter-handbook.pdf", "upload:letter.docx"]
    assert api.answers == [("c1", "reject", learning.runs.NOT_HERE)] * 6
    assert said[0] == "letters-heldout-01: replied" and len(said) == 6
    arm = json.loads((tmp_path / "out" / "arm.json").read_text())
    assert list(arm["skills"]) == ["letter-follow-up"] and arm["changed"] is False
    [scored] = [learning.score.measures(learning.score.score_repeat(tmp_path / "out", keys), keys)]
    assert scored | {"planted": 1.0, "control": 1.0, "reached": 1.0, "not_replied": 0} == scored


def test_teaching_sends_each_worked_case_and_keeps_the_skills_after_it(learning, made, tmp_path):
    work = made[0] / "expenses"
    skills = tmp_path / "skills"
    skills.mkdir()

    def script(goal):  # the fake runtime saves the skill at once: its approval is the decision under test
        (skills / "expense-summary").mkdir(exist_ok=True)
        with (skills / "expense-summary" / "SKILL.md").open("a") as file:
            file.write("a step\n")
        return [{"type": "confirm", "id": "c1", "tool": "propose_skill", "args": {"skill": "..."}, "consequence": ""},
                {"type": "confirm", "id": "c2", "tool": "bash", "args": {}, "consequence": ""},
                {"type": "end", "stop_reason": "replied", "summary": "Saved."}]

    api = FakeApi(script)
    decide = lambda event: ("reject", "It copies the case's names.") if event["tool"] == "propose_skill" else ("approve", "")  # noqa: E731
    said = []
    learning.runs.teach(api, work, tmp_path / "out", skills, decide, said.append)
    assert len(api.sent) == 6 and all(each["goal"].startswith(learning.cases.WORKS["expenses"].task) for each in api.sent)
    assert api.sent[0]["attachments"] == ["upload:claims.xlsx", "upload:expense-policy.pdf", "upload:answer.json"]
    assert api.answers[:2] == [("c1", "reject", "It copies the case's names."), ("c2", "approve", "")]
    assert said[0] == "expenses-train-01: replied, proposals: reject"
    copies = [(tmp_path / "out" / f"after-{n:02d}" / "expense-summary" / "SKILL.md").read_text() for n in (1, 3, 6)]
    assert [len(copy.splitlines()) for copy in copies] == [1, 3, 6]
    result = json.loads((tmp_path / "out" / "expenses-train-02" / "result.json").read_text())
    assert result["approvals"][0]["decision"] == "reject" and list(result["skills"]) == ["expense-summary"]


def test_a_run_whose_agent_asks_the_user_is_stopped_and_one_not_started_is_kept(learning, tmp_path):
    api = FakeApi(lambda goal: [{"type": "action", "tool": "ask_user", "result": ""},
                                {"type": "end", "stop_reason": "cancelled", "summary": ""}])
    sent = learning.runs.send(api, "Do it", [], learning.runs.only_the_task)
    assert (sent["stop_reason"], api.stopped) == ("asked", ["run-1"])

    def refused(*args, **kwargs):
        raise learning.runs.ApiError(409, {}, "the coding agent is not available")

    api.start = refused
    assert learning.runs.send(api, "Do it", [], learning.runs.only_the_task) == {
        "run": None, "stop_reason": "not_started", "reply": "the coding agent is not available", "approvals": [],
    }


def test_the_command_line_scores_and_says_a_mistake_in_a_line(learning, made, tmp_path, capsys):
    work = made[0] / "letters"
    keys = learning.score.load_keys(work)
    folders = {name: repeat(tmp_path / name, keys, answer) for name, answer in
               (("baseline", unlearned), ("sham", unlearned), ("learned", by_key))}
    given = [part for name, folder in folders.items() for part in ("--arm", f"{name}={folder}")]
    assert learning.main(["score", str(work), *given, "--json", str(tmp_path / "report.json")]) == 0
    assert "# Learning experiment: letters" in capsys.readouterr().out
    assert json.loads((tmp_path / "report.json").read_text())["work"] == "letters"
    assert learning.main(["score", str(work), "--arm", "baseline"]) == 1
    assert capsys.readouterr().err == "score: --arm takes NAME=FOLDER, not 'baseline'\n"
    assert learning.main(["generate", str(work)]) == 1
