"""Score held-out runs against their answer keys, arm by arm, and judge the experiment's four criteria.

An arm is a name and its repeats, each a folder a run of the held-out cases wrote:

    <repeat>/arm.json                 {"skills": {name: digest}}, the skills the runs could see
    <repeat>/<case>/result.json       {"stop_reason", "reply"}, the main agent's last reply
    <repeat>/<case>/run/<log>/steps.jsonl   the session's logs, as `computer-use export` writes them

Each field of a reply's JSON object is graded by code. Texts are equal once trimmed, their runs of spaces joined and
their case ignored; a key that is a number is met by that number, also written as text. Nothing else is forgiven, so
a date or a code in another format is wrong.

For the rule that needs the last page of the long PDF, a right value is not enough: the page must have been read,
which shows as text only that page holds in what a tool returned. A case without logs has that unknown, and is left
out of the rate."""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

RIGHT, WRONG, MISSING, MADE_UP = "right", "wrong", "missing", "made_up"
REPLIED = "replied"
SPACES = re.compile(r"\s+")
# The tools by which a skill reaches the work: the main agent reads it, or hands it over with a task.
READ_SKILL, START_TASK = "read_skill", "start_task"


def folded(value) -> str:
    return SPACES.sub(" ", str(value)).strip().casefold()


def number(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        return None


def grade(expected, answer) -> str:
    """How an answered field stands against its key. A key of None is a value no file holds: leaving it out is right
    and anything else is made up."""
    empty = answer is None or (isinstance(answer, str) and not answer.strip())
    if expected is None:
        return RIGHT if empty else MADE_UP
    if empty:
        return MISSING
    if isinstance(expected, int):
        return RIGHT if number(answer) == expected else WRONG
    return RIGHT if isinstance(answer, (str, int, float)) and folded(answer) == folded(expected) else WRONG


def answer_of(reply: str) -> dict:
    """The JSON object a reply holds, alone or with words or a code fence around it; {} when it holds none."""
    start, end = reply.find("{"), reply.rfind("}")
    try:
        found = json.loads(reply[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        found = None
    return found if isinstance(found, dict) else {}


def events_of(run: Path) -> list[dict]:
    """Every event of a session's logs, each log in order; a line that is not an event is passed over."""
    events = []
    for log in sorted(run.glob("*/steps.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def actions(events: Iterable[dict], *tools: str) -> list[dict]:
    return [event for event in events if event.get("type") == "action" and (not tools or event.get("tool") in tools)]


def read_page(events: list[dict], markers: Iterable[str]) -> bool | None:
    """Whether a tool returned text of the page the markers are from; None without logs to tell by."""
    if not events:
        return None
    returned = [folded(event["result"]) for event in actions(events) if isinstance(event.get("result"), str)]
    return any(folded(marker) in text for marker in markers for text in returned)


@dataclass(frozen=True)
class Scored:
    """One held-out case of one repeat."""

    case: str
    replied: bool
    grades: dict[str, str]  # by field
    read_last_page: bool | None
    used_skill: bool  # the main agent read a skill or handed one over


def load_keys(work: Path) -> dict[str, dict]:
    """The held-out cases' keys, by case."""
    keys = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((work / "keys").glob("*.json"))]
    held = {key["case"]: key for key in keys if key["split"] == "heldout"}
    if not held:
        raise ValueError(f"{work} has no held-out keys")
    return held


def score_repeat(folder: Path, keys: dict[str, dict]) -> list[Scored]:
    """Every held-out case of a repeat. A case that was not run, or whose reply holds no JSON, has every field
    missing: it is counted, not dropped."""
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    scored = []
    for case, key in keys.items():
        result = folder / case / "result.json"
        row = json.loads(result.read_text(encoding="utf-8")) if result.is_file() else {}
        answer = answer_of(row.get("reply") or "")
        events = events_of(folder / case / "run")
        used = any(event["tool"] == READ_SKILL or (event.get("args") or {}).get("skills")
                   for event in actions(events, READ_SKILL, START_TASK))
        scored.append(Scored(
            case, row.get("stop_reason") == REPLIED,
            {name: grade(field["value"], answer.get(name)) for name, field in key["fields"].items()},
            read_page(events, key["last_page"]["markers"]), used,
        ))
    return scored


def skills_of(folder: Path) -> str:
    """The skills a repeat's runs could see, as its arm.json says: "name digest" each, "none", or "?" without one."""
    try:
        skills = json.loads((folder / "arm.json").read_text(encoding="utf-8"))["skills"]
    except (OSError, ValueError, KeyError):
        return "?"
    return ", ".join(f"{name} {digest[:8]}" for name, digest in sorted(skills.items())) or "none"


def share(hits: int, of: int) -> float | None:
    return hits / of if of else None


def measures(scored: list[Scored], keys: dict[str, dict]) -> dict:
    """A repeat's rates over its held-out cases; None where nothing was counted.

    planted, control   fields of that kind answered right
    made_up            fields no file holds that were given a value: a count
    last_page          of the cases whose last page changes the answer (and whose logs can tell): the page was read
                       and the field is right
    reached            of the cases whose logs can tell: the page was read
    """
    right = {"planted": [0, 0], "control": [0, 0]}
    made_up, last_page, reached = 0, [0, 0], [0, 0]
    for each in scored:
        key = keys[each.case]
        for name, graded in each.grades.items():
            kind = key["fields"][name]["kind"]
            if kind in right:
                right[kind][0] += graded == RIGHT
                right[kind][1] += 1
            made_up += graded == MADE_UP
        if each.read_last_page is None:
            continue
        reached[0] += each.read_last_page
        reached[1] += 1
        if key["last_page"]["changes"]:
            last_page[0] += each.read_last_page and each.grades[key["last_page"]["field"]] == RIGHT
            last_page[1] += 1
    return {
        "planted": share(*right["planted"]), "control": share(*right["control"]), "made_up": made_up,
        "last_page": share(*last_page), "reached": share(*reached),
        "used_skill": sum(each.used_skill for each in scored),
        "not_replied": sum(not each.replied for each in scored), "cases": len(scored),
    }


@dataclass(frozen=True)
class Criterion:
    statement: str
    passed: bool | None  # None: not enough repeats with a value to tell
    detail: str


def criteria(by_arm: dict[str, list[dict]], learned: str, baselines: list[str]) -> list[Criterion]:
    """The four criteria, set before any run. The learning worked only if all pass: on planted fields and on the
    last-page rule the learned arm's worst repeat beats every baseline's best, so that the spread between repeats
    cannot explain the gain; and it costs nothing on control fields or in made-up values against the first baseline,
    the arm without a skill."""
    plain, others = baselines[0], ", ".join(baselines)

    def values(arm: str, measure: str) -> list[float]:
        return [repeat[measure] for repeat in by_arm[arm] if repeat[measure] is not None]

    def beats(measure: str, statement: str) -> Criterion:
        mine, theirs = values(learned, measure), [value for arm in baselines for value in values(arm, measure)]
        if not mine or not theirs:
            return Criterion(statement, None, "no repeat with a value")
        return Criterion(statement, min(mine) > max(theirs),
                         f"{learned} worst {min(mine):.0%}, best of {others} {max(theirs):.0%}")

    control = (values(learned, "control"), values(plain, "control"))
    made_up = [sum(repeat["made_up"] for repeat in by_arm[arm]) / len(by_arm[arm]) for arm in (learned, plain)]
    return [
        beats("planted", f"On planted fields, {learned}'s worst repeat beats the best repeat of {others}"),
        Criterion(f"On control fields, {learned}'s worst repeat is not below {plain}'s worst",
                  min(control[0]) >= min(control[1]) if all(control) else None,
                  f"{min(control[0]):.0%} against {min(control[1]):.0%}" if all(control) else "no repeat with a value"),
        Criterion(f"{learned} makes up values no more often than {plain}", made_up[0] <= made_up[1],
                  f"{made_up[0]:.1f} against {made_up[1]:.1f} a repeat"),
        beats("last_page", f"On the last-page rule, {learned}'s worst repeat reads the page and gets the field right "
                           f"more often than the best repeat of {others}"),
    ]


def by_rule(arms: dict[str, list[list[Scored]]], keys: dict[str, dict]) -> dict[str, dict[str, list[int]]]:
    """Each planted rule's [right, counted] by arm, over all repeats. The last-page rule counts only the cases whose
    last page changes the answer: in the others the first page's value is right."""
    table: dict[str, dict[str, list[int]]] = {}
    for arm, repeats in arms.items():
        for each in (each for repeat in repeats for each in repeat):
            key = keys[each.case]
            for name, graded in each.grades.items():
                rule = key["fields"][name]["rule"]
                if rule is None or (name == key["last_page"]["field"] and not key["last_page"]["changes"]):
                    continue
                cell = table.setdefault(rule, {}).setdefault(arm, [0, 0])
                cell[0] += graded == RIGHT
                cell[1] += 1
    return table


def rates(repeats: list[dict], measure: str) -> str:
    return ", ".join("?" if repeat[measure] is None else f"{repeat[measure]:.0%}" for repeat in repeats)


def report(work: Path, arms: dict[str, list[Path]], learned: str, baselines: list[str]) -> tuple[str, dict]:
    """One kind of work's report, as Markdown and as data: the criteria, each arm's measures a repeat, and each
    planted rule's score by arm. ValueError when an arm the criteria need has no repeat."""
    if missing := [arm for arm in (learned, *baselines) if not arms.get(arm)]:
        raise ValueError(f"no runs for {', '.join(missing)}")
    keys = load_keys(work)
    rules = json.loads((work / "rules.json").read_text(encoding="utf-8"))
    scored = {arm: [score_repeat(folder, keys) for folder in folders] for arm, folders in arms.items()}
    by_arm = {arm: [measures(repeat, keys) for repeat in repeats] for arm, repeats in scored.items()}
    judged = criteria(by_arm, learned, baselines)
    table = by_rule(scored, keys)
    lines = [f"# Learning experiment: {work.name}", "", "## Criteria", ""]
    lines += [f"- {'?' if each.passed is None else 'PASS' if each.passed else 'FAIL'}: {each.statement} "
              f"({each.detail})" for each in judged]
    lines += ["", "## Arms, a value for each repeat", "",
              ("| Arm | Skills | Planted right | Control right | Made up | Last page read and right | Last page read "
              "| Cases a skill was used in | Cases without a reply |"), "|---|---|---|---|---|---|---|---|---|"]
    for arm, repeats in by_arm.items():
        skills = sorted({skills_of(folder) for folder in arms[arm]})
        lines.append(" | ".join([
            f"| {arm}", "; ".join(skills), rates(repeats, "planted"), rates(repeats, "control"),
            ", ".join(str(repeat["made_up"]) for repeat in repeats), rates(repeats, "last_page"),
            rates(repeats, "reached"), ", ".join(f"{repeat['used_skill']}/{repeat['cases']}" for repeat in repeats),
            ", ".join(str(repeat["not_replied"]) for repeat in repeats) + " |",
        ]))
        if len(skills) > 1:
            lines.append(f"\n{arm}'s repeats did not all see the same skills.\n")
    lines += ["", "## Planted rules, right of counted over all repeats", "",
              "| Rule | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for rule, text in rules.items():
        cells = ("{}/{}".format(*table.get(rule, {}).get(arm, [0, 0])) for arm in arms)
        lines.append(f"| {text} | " + " | ".join(cells) + " |")
    data = {
        "work": work.name,
        "criteria": [vars(each) for each in judged],
        "arms": by_arm,
        "rules": table,
        "grades": {arm: [{each.case: each.grades for each in repeat} for repeat in repeats]
                   for arm, repeats in scored.items()},
    }
    return "\n".join(lines) + "\n", data
