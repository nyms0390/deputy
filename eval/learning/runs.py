"""Run the experiment's cases through the runtime: the held-out ones with a given set of skills, for `score`, and the
training ones as worked cases the agent makes a skill from.

Each uses a server of its own on the eval's port whose users' skills are one folder under runtime/data
(CU_SKILLS_DIR), so that a run sees those skills and no others. No case uses the desktop: the files are attached and
the coding agent opens them."""

import contextlib
import hashlib
import json
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import eval as desktop

# The CLI's API client, which the eval puts on the path.
ApiError, Client, Follow = desktop.ApiError, desktop.Client, desktop.Follow

SKILLS = desktop.RUNTIME / "data" / "learning"  # the folders servers were given, a new one each
CONTAINER_SKILLS = "/app/data/learning"  # the same folder as the container has it
PROJECT = "eval"
PROPOSE, ASK_USER = "propose_skill", "ask_user"
APPROVAL_SECONDS = 30  # for a step the log says is waiting to show as the one to answer
# What a worked case is sent with, after the task. The same for every case and kind of work: a hint here would be
# taught, not learned.
TEACH = (
    "{task}\n\nThis is a worked case of that task: the attached answer.json is the right answer for the other attached "
    "files. Use it to make my skill for this kind of task, or to improve the one I have, so that an agent following "
    "the skill gets this answer from these files and the right answer from other files of the same kind. Propose the "
    "skill without asking me questions and without running tests."
)
NOT_HERE = "This run only does the task; no skill is saved from it."

# What to answer a step waiting for approval, given its confirm event: "approve" or "reject", and a note.
Decide = Callable[[dict], tuple[str, str]]


def digests(folder: Path) -> dict[str, str]:
    """The skills in a folder, each by a digest of its SKILL.md."""
    return {path.parent.name: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            for path in sorted(folder.glob("*/SKILL.md"))}


@contextlib.contextmanager
def server(skills: Path | None, ref: str = "worktree"):
    """The runtime at a git ref, serving a new folder of users' skills that starts as a copy of `skills`, or empty:
    its client, and the folder as this machine has it."""
    folder = SKILLS / uuid.uuid4().hex
    if skills is None:
        folder.mkdir(parents=True)
    else:
        shutil.copytree(skills, folder)
    with tempfile.TemporaryDirectory() as tmp:
        source = desktop.copy_source(ref, Path(tmp))
        code = desktop.install(desktop.version_of(ref, source), source)
        with desktop.version_server(code, {"CU_SKILLS_DIR": f"{CONTAINER_SKILLS}/{folder.name}"}) as api:
            yield api, folder


def answer(api: Client, event: dict, decide: Decide) -> dict:
    """Answer the step a confirm event says is waiting, once the runtime shows it: what was decided."""
    decision, note = decide(event)
    deadline = time.monotonic() + APPROVAL_SECONDS
    while True:
        with contextlib.suppress(ApiError):  # not waiting yet, or answered meanwhile
            if ((api.status().get("approval") or {}).get("id")) == event["id"]:
                api.answer(event["id"], decision, note)
                return {"tool": event["tool"], "decision": decision, "note": note}
        if time.monotonic() > deadline:
            return {"tool": event["tool"], "decision": None, "note": "it never showed as waiting"}
        time.sleep(0.5)


def send(api: Client, message: str, files: list[Path], decide: Decide, timeout: float = desktop.RUN_TIMEOUT) -> dict:
    """Send a message with files in a new conversation and follow its session to the end: its run, how its last turn
    ended ("asked" when the agent asked the user, since no one answers; "cancelled" past `timeout`), the main agent's
    reply, and how each step that waited for approval was answered."""
    try:
        run = api.start(message, project=PROJECT, attachments=[api.upload(file) for file in files])["id"]
    except ApiError as error:
        return {"run": None, "stop_reason": "not_started", "reply": str(error), "approvals": []}
    feed, done, expired = Follow(api, run), threading.Event(), threading.Event()

    def stop() -> None:
        with contextlib.suppress(ApiError):  # it ended meanwhile
            api.stop(feed.thread or run)

    def at_timeout() -> None:
        if not done.wait(timeout):
            expired.set()
            stop()

    threading.Thread(target=at_timeout, daemon=True).start()
    kinds, end, asked, approvals = {}, {}, False, []
    try:
        for log, event in feed.lines():
            if event["type"] == "start":
                kinds[log] = event["kind"]
            elif event["type"] == "end" and kinds.get(log) == "turn":
                end = event
            elif event["type"] == "confirm":
                approvals.append(answer(api, event, decide))
            elif event["type"] == "action" and event.get("tool") == ASK_USER:
                asked = True
                stop()
    finally:
        done.set()
    reason = "cancelled" if expired.is_set() else "asked" if asked else end.get("stop_reason", "crashed")
    return {"run": run, "stop_reason": reason, "reply": end.get("summary", ""), "approvals": approvals}


def only_the_task(event: dict) -> tuple[str, str]:
    """A held-out run's answers: no skill is saved from it, since a later case would run with it."""
    return ("reject", NOT_HERE) if event["tool"] == PROPOSE else ("approve", "")


def keep(api: Client, sent: dict, into: Path, **more) -> None:
    """A case's result.json and, when it started, its session's logs."""
    into.mkdir(parents=True)
    if sent["run"]:
        api.export_session(sent["run"], into / "run")
    (into / "result.json").write_text(json.dumps(sent | more, ensure_ascii=False, indent=1), encoding="utf-8")


def cases(work: Path, split: str) -> list[Path]:
    found = sorted(path for path in (work / split).iterdir() if path.is_dir())
    if not found:
        raise ValueError(f"{work} has no {split} cases")
    return found


def heldout(api: Client, work: Path, out: Path, skills: Path, say: Callable[[str], None] = print) -> None:
    """Run each held-out case of `work` once, in a conversation of its own, and write what `score` reads to `out`.
    `skills` is the folder the server's runs see: arm.json records what it holds, and whether it still held the same
    after the last case."""
    task = (work / "task.txt").read_text(encoding="utf-8").strip()
    before = digests(skills)
    out.mkdir(parents=True)
    for case in cases(work, "heldout"):
        sent = send(api, task, sorted((case / "files").iterdir()), only_the_task)
        keep(api, sent, out / case.name)
        say(f"{case.name}: {sent['stop_reason']}")
    arm = {"skills": before, "changed": digests(skills) != before}
    (out / "arm.json").write_text(json.dumps(arm, indent=1), encoding="utf-8")
    if arm["changed"]:
        say("The skills changed while the cases ran: the cases did not all see the same ones.")


def teach(api: Client, work: Path, out: Path, skills: Path, decide: Decide, say: Callable[[str], None] = print) -> None:
    """Send each training case of `work` in order as a worked case, each in a conversation of its own, with `decide`
    answering what the agent proposes. After each, `out`/after-NN keeps a copy of `skills` as it then is: a folder of
    skills to run the held-out cases with."""
    message = TEACH.format(task=(work / "task.txt").read_text(encoding="utf-8").strip())
    out.mkdir(parents=True)
    for number, case in enumerate(cases(work, "train"), 1):
        sent = send(api, message, [*sorted((case / "files").iterdir()), case / "answer.json"], decide)
        keep(api, sent, out / case.name, skills=digests(skills))
        shutil.copytree(skills, out / f"after-{number:02d}")
        proposals = [each["decision"] or "unanswered" for each in sent["approvals"] if each["tool"] == PROPOSE]
        say(f"{case.name}: {sent['stop_reason']}, proposals: {', '.join(proposals) or 'none'}")
