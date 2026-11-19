"""What an agent is told of its skills: one line each, and when to read one."""

from collections.abc import Iterable

SKILLS = "Skills: when your work matches a skill's description, read it with read_skill before acting, and follow it."


def section(skills: Iterable[tuple[str, str]]) -> str:
    """The system prompt's list of skills, by name and description."""
    return "\n".join([SKILLS, *(f"- {name}: {description}" for name, description in skills)])


# What start_task's `skills` argument says.
HAND = (
    "The names of your skills whose instructions apply to this task, for its agent to follow; none if none apply. "
    "Its agent gets them word for word and cannot read them otherwise."
)
HANDED = "Skills handed over with this task; follow the instructions of each:"


def handed(skills: Iterable[tuple[str, str]]) -> str:
    """The instructions of the skills a task was handed over with, by name, for the opening of the agent doing it."""
    listed = "\n\n".join(f"### {name}\n\n{instructions}" for name, instructions in skills)
    return f"{HANDED}\n\n{listed}" if listed else ""


def no_skill(names: Iterable[str], known: Iterable[str]) -> str:
    return f"No skill named {', '.join(map(repr, names))}. Skills: {', '.join(known) or 'none'}."


PROPOSE_SKILL = """\
Propose a skill of the user's, new or changed, for them to approve. Nothing is written until they do: the call waits \
for their answer, and returns whether they saved it, or their reason for declining it.

Give name, description and instructions for a new skill. For a change to one of the user's skills, give its name and \
edits to its instructions, and a description only to change it. Built-in skills cannot be changed.

In your reply with this call, show the user what they are approving: the whole SKILL.md for a new skill, each edit \
for a change. A skill you save is listed from your next turn."""
SKILL_NAME = "The skill's name: lowercase words joined by hyphens, e.g. invoice-entry; for a change, the skill's own."
SKILL_DESCRIPTION = (
    "When to use the skill, in one or two sentences in English: the tasks, apps or sites it covers. Agents choose "
    "skills by it alone."
)
SKILL_INSTRUCTIONS = "A new skill's instructions, in Markdown: the SKILL.md after its frontmatter."
SKILL_EDITS = "A change's edits to the skill's instructions, applied in order."
EDIT_OLD = "Text copied exactly from the skill's instructions, occurring there once."
EDIT_NEW = "Its replacement; empty to delete it."

SAVED = "Saved: the user approved the skill {name}. It is listed from your next turn."
CHANGED = "The skill {name} changed while the user decided, so this proposal no longer applies to it. Read it again."


def declined(note: str) -> str:
    return f"the user declined the skill. {note}" if note else "the user declined the skill."


def consequence(name: str, description: str, new: bool, edits: int) -> str:
    """What the user is asked to allow, in one line: the composer and the command line show it."""
    if new:
        return f"save a new skill {name}: {description}"
    return f"change the skill {name} ({edits} edit{'s' if edits != 1 else ''}): {description}"
