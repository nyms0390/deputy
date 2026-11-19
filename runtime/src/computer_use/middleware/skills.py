"""Skills: know-how an agent reads only when its work calls for it. An agent has them by carrying this middleware. Each
is a folder holding a SKILL.md, whose frontmatter names and describes it, and any files its instructions point to. The
built-in ones are in the agent's own folder, computer_use/skills/<agent>; the main agent also has the user's own
(userskills), which it passes along with the work it hands over. The agent's system prompt lists them; read_skill
reads one."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated

import yaml
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import Field

from computer_use.middleware.common import with_section
from computer_use.prompts.skills import section

SKILLS = Path(__file__).parent.parent / "skills"
# YAML between "---" lines at the top of a SKILL.md.
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str  # when to use it
    folder: Path


def parse(path: Path) -> tuple[dict, str]:
    """A SKILL.md's frontmatter, and its instructions after it."""
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    meta = yaml.safe_load(match.group(1)) if match else None
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: no frontmatter")
    return meta, text[match.end():].strip()


def skill_at(path: Path) -> Skill:
    """The skill a SKILL.md describes; ValueError unless its frontmatter names its folder and describes it."""
    meta, _ = parse(path)
    name, description = str(meta.get("name", "")).strip(), " ".join(str(meta.get("description", "")).split())
    if name != path.parent.name or not description:
        raise ValueError(f"{path}: the frontmatter needs the folder's name as name, and a description")
    return Skill(name, description, path.parent)


@cache
def catalog(folder: Path) -> tuple[Skill, ...]:
    """The skills in `folder`, by name, read once: they are files of the runtime."""
    return tuple(skill_at(path) for path in sorted(folder.glob("*/SKILL.md")))


def built_in() -> set[str]:
    """Every built-in skill's name, of every agent: a user's skill takes none of them."""
    return {skill.name for agent in SKILLS.iterdir() if agent.is_dir() for skill in catalog(agent)}


def skills_of(agent: str, folder: Path = SKILLS, user: tuple[Skill, ...] = ()) -> tuple[Skill, ...]:
    """An agent's built-in skills, then the user's `user` skills whose names those leave free."""
    own = catalog(folder / agent)
    taken = {skill.name for skill in own}
    return own + tuple(skill for skill in user if skill.name not in taken)


def read_skill_tool(skills: tuple[Skill, ...]) -> BaseTool:
    """read_skill, for these skills only."""
    folders = {skill.name: skill.folder.resolve() for skill in skills}

    @tool
    def read_skill(
        name: Annotated[str, Field(description="The skill's name, as listed in your instructions")],
        file: Annotated[str | None, Field(description=(
            "A file in the skill's folder that its instructions point to, e.g. references/x.md; omit for the "
            "instructions"
        ))] = None,
    ) -> str:
        """Read a skill's instructions, or a file in its folder that they point to."""
        folder = folders.get(name)
        if folder is None:
            raise ToolException(f"No skill named {name!r}. Skills: {', '.join(folders)}.")
        if file is None:
            return parse(folder / "SKILL.md")[1]
        path = (folder / file).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise ToolException(f"{file!r} is not a file of skill {name!r}.")
        return path.read_text(encoding="utf-8")

    read_skill.handle_tool_error = True  # a wrong name or file is the agent's to hear
    return read_skill


class SkillsMiddleware(AgentMiddleware):
    """An agent's skills, its built-in ones and the `user`'s given: listed at the end of its system prompt, the same
    on every call so a model's cache of it holds, and read with read_skill. Without skills it adds nothing."""

    def __init__(self, agent: str, folder: Path = SKILLS, user: tuple[Skill, ...] = ()):
        super().__init__()
        skills = skills_of(agent, folder, user)
        self.section = section((skill.name, skill.description) for skill in skills) if skills else ""
        self.tools = [read_skill_tool(skills)] if skills else []

    async def awrap_model_call(self, request, handler):
        return await handler(with_section(request, self.section) if self.section else request)
