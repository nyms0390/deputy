"""The user's own skills in a session (userskills): the main agent's tool to propose one, new or changed, which is
written only once the user approves it, and the instructions a task is handed over with."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel, Field

from computer_use.agent.models import MAIN_AGENT
from computer_use.agent.trajectory import Trajectory
from computer_use.desktop.control import Approval
from computer_use.middleware.skills import Skill, built_in, parse, skills_of
from computer_use.prompts.skills import (
    CHANGED, EDIT_NEW, EDIT_OLD, PROPOSE_SKILL, SAVED, SKILL_DESCRIPTION, SKILL_EDITS, SKILL_INSTRUCTIONS, SKILL_NAME,
    consequence, declined, handed, no_skill,
)
from computer_use.tools.common import CallId, not_executed
from computer_use.userskills import (
    MAX_DESCRIPTION, MAX_NAME, NAME, Proposals, apply_edits, skill_text, user_catalog, write_skill,
)

PROPOSE = "propose_skill"


class UserSkills:
    """A user's own skills in a session: in `folder`, with their proposals waiting in `proposals`. Read afresh each
    time, since an approval or a person may change them while the runtime runs (agent.handover.SessionSkills)."""

    def __init__(self, folder: Path, proposals: Proposals):
        self.folder, self.proposals = folder, proposals

    def listed(self) -> tuple[Skill, ...]:
        return user_catalog(self.folder)

    def hand(self, names: list[str]) -> str:
        known = {skill.name: skill for skill in skills_of(MAIN_AGENT, user=self.listed())}
        if unknown := [name for name in names if name not in known]:
            raise LookupError(no_skill(unknown, known))
        return handed((name, parse(known[name].folder / "SKILL.md")[1]) for name in dict.fromkeys(names))

    def tools(self, turn: Callable[[], Trajectory]) -> list[BaseTool]:
        return [propose_skill_tool(self.folder, self.proposals, turn)]


class Edit(BaseModel):
    old_string: str = Field(description=EDIT_OLD)
    new_string: str = Field(description=EDIT_NEW)


def propose_skill_tool(folder: Path, proposals: Proposals, turn: Callable[[], Trajectory]) -> BaseTool:
    """propose_skill, for the skills in `folder`, asked in the log of the turn under way."""

    def proposed(
        name: str, description: str | None, instructions: str | None, edits: list[Edit],
    ) -> tuple[str, str, str | None]:
        """The SKILL.md the proposal would write, its description, and the SKILL.md it replaces (None for a new skill);
        ToolException for one that cannot be."""
        if not NAME.fullmatch(name) or len(name) > MAX_NAME:
            raise ToolException(f"{name!r} is not a skill name: lowercase words joined by hyphens, {MAX_NAME} at most.")
        if name in built_in():
            raise ToolException(f"{name} is a built-in skill, which cannot be changed. Choose another name.")
        path = folder / name / "SKILL.md"
        before = path.read_text(encoding="utf-8") if path.is_file() else None
        if before is None:
            if edits or not (description and instructions and instructions.strip()):
                raise ToolException(f"The user has no skill {name}: give a description and instructions for a new one.")
            body = instructions
        else:
            if instructions is not None:
                raise ToolException(f"The user has a skill {name}: change it with edits, or choose another name.")
            if not edits and not description:
                raise ToolException("Nothing to change: give edits, a description, or both.")
            try:
                meta, body = parse(path)
                body = apply_edits(body, [(edit.old_string, edit.new_string) for edit in edits])
            except ValueError as error:
                raise ToolException(str(error)) from None
            description = description or str(meta.get("description", ""))
        description = " ".join((description or "").split())
        if not description or len(description) > MAX_DESCRIPTION:
            raise ToolException(f"The description must be one or two sentences, {MAX_DESCRIPTION} characters at most.")
        return skill_text(name, description, body), description, before

    @tool(PROPOSE, description=PROPOSE_SKILL)
    async def propose_skill(
        name: Annotated[str, Field(description=SKILL_NAME)],
        description: Annotated[str | None, Field(description=SKILL_DESCRIPTION)] = None,
        instructions: Annotated[str | None, Field(description=SKILL_INSTRUCTIONS)] = None,
        edits: Annotated[list[Edit] | None, Field(description=SKILL_EDITS)] = None,
        *,
        call_id: CallId,
    ) -> str | ToolMessage:
        text, description, before = proposed(name, description, instructions, edits or [])
        path = folder / name / "SKILL.md"
        call = {"id": call_id, "name": PROPOSE}
        asking = consequence(name, description, before is None, len(edits or []))
        log = turn()
        # The log keeps what was asked: a new skill whole, a change as its edits.
        shown = {"skill": text} if before is None else {
            "edits": [edit.model_dump() for edit in edits or []], "description": description,
        }
        await log.confirm(call, {"name": name, **shown}, asking, None)
        asked = proposals.ask(Approval(call_id, log.run, PROPOSE, asking, None, time.time()))
        try:
            answer = await asked.wait(call_id, None)
        finally:  # a turn stopped while it waits leaves nothing to answer
            proposals.close(asked, call_id)
        if answer.decision != "approve":
            log.decision(call_id, "declined", answer.note, answer.by)
            return not_executed(call, declined(answer.note), "declined")
        log.decision(call_id, "approved", by=answer.by)
        if (path.read_text(encoding="utf-8") if path.is_file() else None) != before:
            raise ToolException(CHANGED.format(name=name))
        write_skill(folder, name, text)
        return SAVED.format(name=name)

    propose_skill.handle_tool_error = True  # a proposal that cannot be is the agent's to hear
    return propose_skill
