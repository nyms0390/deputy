"""Starts a task in the background when the main agent hands one over to an agent."""

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from computer_use.agent.background import MAX_TASKS, Background, Resource
from computer_use.agent.inbox import Inbox
from computer_use.agent.subagent import MakeAgent, Subagent
from computer_use.agent.trajectory import Trajectory
from computer_use.prompts import notes
from computer_use.storage import MAIN
from computer_use.tools.common import not_executed
from computer_use.tools.tasks import START_TASK, TaskTool

ENDED, STOPPED, FAILED = "ended", "stopped", "error"


@dataclass(frozen=True)
class Handed:
    """The rules a task is handed over with: word for word for its agent, by id and text for its log."""

    text: str = ""
    rules: Sequence[dict[str, str]] = ()


class SessionSkills(Protocol):
    """The main agent's skills, as far as its hand-overs and tools need."""

    def hand(self, names: list[str]) -> str: ...  # their instructions, for a task's agent; LookupError for one unknown
    def tools(self, turn: Callable[[], Trajectory]) -> list[BaseTool]: ...  # the main agent's, in the turn under way


class SessionMemory(Protocol):
    """What a session remembers for its user, as far as its agents need."""

    async def recall(self) -> bool: ...  # once, as the session begins: whether it has rules to hand over
    def middleware(self, run: str) -> list[AgentMiddleware]: ...  # around the main agent's calls in this turn
    async def hand(self, ids: list[str]) -> Handed: ...  # the rules a task goes with, each counted as used


def task_tool(
    kind: type[Subagent], make: MakeAgent, background: Background, user: Inbox, turn: Callable[[], Trajectory],
    session: str, thread: str, memory: SessionMemory | None = None, resource: Resource | None = None,
    skills: SessionSkills | None = None,
) -> TaskTool:
    """`kind`'s part of start_task. Tasks that need a `resource` run one at a time and hold it, under their log's name,
    until they end. `memory` supplies the remembered rules a task is handed over with, by id; `skills` the
    instructions of the skills it is handed over with, by name."""

    async def start(
        title: str, task: str, rules: list[str], named: list[str], steps: list[str], call_id: str,
    ) -> str | ToolMessage:
        # One hand-over at a time: what refuses one is decided before its log exists, as the one before left things.
        async with background.starting:
            return await begin(title, task, rules, named, steps, call_id)

    async def begin(
        title: str, task: str, rules: list[str], named: list[str], steps: list[str], call_id: str,
    ) -> str | ToolMessage:
        call = {"id": call_id, "name": START_TASK}
        try:
            following = skills.hand(named) if skills is not None and named else ""
        except LookupError as unknown:  # a name it was not told, or one deleted since
            return ToolMessage(str(unknown.args[0]), tool_call_id=call_id, name=START_TASK, status="error")
        if resource is not None and (using := background.working(kind.agent)) is not None:
            return not_executed(call, *resource.in_use(using.id))
        if background.full:
            return not_executed(call, notes.full(MAX_TASKS), "tooManyTasks")
        if resource is not None and (refused := await resource.unavailable()) is not None:
            return not_executed(call, *refused)
        parent = turn()
        if resource is not None and (refused := resource.acquire(parent.run)) is not None:
            return not_executed(call, *refused)
        log = await Trajectory.open(parent.store, parent.listeners)
        handed = await memory.hand(rules) if memory is not None and rules else Handed()
        log.start_task(thread, session, parent.run, background.number(), kind.agent, task, title)
        if resource is not None:
            resource.taken_by(log.run)

        worker = make(log, "\n\n".join(part for part in (handed.text, following) if part))

        async def work(id: str, told: Inbox):
            worker.task_id = id
            if handed.rules:  # the page shows them in the task
                log.note(notes.following(len(handed.rules)), "memoryUsed", rules=list(handed.rules))
            ending, reply, by = FAILED, "", None
            try:
                result = await worker.run(task, told)
                ending, reply = ENDED, result[1].get("reply", "")
                return result
            except asyncio.CancelledError as stop:
                ending, by = STOPPED, next(iter(stop.args), None)
                raise
            finally:
                log.end(ending, reply, by=by, agent_stop_reason=worker.stop_reason)
                with contextlib.suppress(Exception):  # the stop or error is what to tell; the store logs its own
                    await log.store.flush(log.run)
                if resource is not None:
                    resource.release()

        return notes.started(background.start(
            kind.agent, task, work, worker.steps, turn=parent, record=log.run, heard=len(user.read(MAIN)),
            title=title, plan_steps=steps,
        ))

    return TaskTool(kind.agent, kind.about, start)
