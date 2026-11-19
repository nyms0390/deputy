"""Each user's own skills, beside the built-in ones (middleware.skills): a folder per skill holding one SKILL.md, in the
user's skills folder, by default data/users/<user>/skills (config.Settings.skills_folder). The runtime writes one only
once the user approves the main agent's proposal (tools.skills), and overwrites it whole when they approve a change.

A proposal waits as a file of its own in the user's proposals folder, answered as a desktop step is (desktop.control
.ApprovalFile), so that the web page and the command line approve or reject it the same way."""

import logging
import os
import re
import uuid
from collections.abc import Iterable
from pathlib import Path

import yaml

from computer_use.desktop.control import Approval, ApprovalFile
from computer_use.middleware.skills import Skill, skill_at

# A skill's name, as its folder is named: lowercase words joined by hyphens.
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_NAME = 64
# A description is one or two sentences on when to use it: every listing carries it.
MAX_DESCRIPTION = 1024

logger = logging.getLogger(__name__)


def user_catalog(folder: Path | None) -> tuple[Skill, ...]:
    """The skills in a user's folder, read afresh: they change while the runtime runs. One a person broke by hand is
    left out, with a warning, rather than stopping the run."""
    if folder is None or not folder.is_dir():
        return ()
    skills = []
    for path in sorted(folder.glob("*/SKILL.md")):
        try:
            skills.append(skill_at(path))
        except (ValueError, yaml.YAMLError, UnicodeDecodeError) as error:
            logger.warning("skipped a user skill: %s", error)
    return tuple(skills)


def skill_text(name: str, description: str, instructions: str) -> str:
    """A SKILL.md: its frontmatter, then its instructions."""
    meta = yaml.safe_dump({"name": name, "description": description}, allow_unicode=True, sort_keys=False, width=1 << 16)
    return f"---\n{meta}---\n\n{instructions.strip()}\n"


def write_skill(folder: Path, name: str, text: str) -> Path:
    """Write a skill's SKILL.md whole, replacing the one it had: one file, no revisions."""
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return path


def apply_edits(text: str, edits: Iterable[tuple[str, str]]) -> str:
    """`text` with each (old, new) applied in order; ValueError unless each old occurs exactly once when it applies."""
    for number, (old, new) in enumerate(edits, 1):
        found = text.count(old) if old else 0
        if found != 1:
            raise ValueError(f"edit {number}: its old_string occurs {found} times in the skill, not once")
        text = text.replace(old, new, 1)
    return text


class Proposals:
    """A user's skill proposals awaiting an answer, one file each. A run that died while one waited leaves its file
    behind; the server shows only those of the user's running sessions."""

    def __init__(self, folder: Path):
        self.folder = folder

    def ask(self, approval: Approval) -> ApprovalFile:
        asked = ApprovalFile(self.folder / f"{uuid.uuid4().hex}.json")
        asked.ask(approval)
        return asked

    @staticmethod
    def close(asked: ApprovalFile, id: str) -> None:
        """Drop an answered or abandoned proposal, and the lock its answer took."""
        asked.clear(id)
        asked.path.with_suffix(".lock").unlink(missing_ok=True)

    def waiting(self, runs: set[str]) -> list[tuple[ApprovalFile, Approval]]:
        """Those awaiting an answer from one of `runs`, oldest first."""
        found = []
        for path in self.folder.glob("*.json") if self.folder.is_dir() else ():
            approval = (asked := ApprovalFile(path)).read()
            if approval is not None and approval.decision is None and approval.run in runs:
                found.append((asked, approval))
        return sorted(found, key=lambda each: each[1].since)

