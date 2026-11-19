"""The user's own skills: listed to the main agent beside its built-in ones, handed over with a task by name, and
written only once the user approves the main agent's proposal."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from computer_use.desktop.control import ApprovalFile
from computer_use.middleware.skills import SkillsMiddleware, skills_of
from computer_use.storage import MAIN
from computer_use.userskills import apply_edits, skill_text, user_catalog, write_skill
from conftest import logged, run_turn, tool_call, use_script

pytestmark = pytest.mark.usefixtures("the_log_is_the_whole_record", "fast_polls")

INVOICES = skill_text("invoices", "Entering invoices in the ledger app.", "Type the total last; the app recalculates.")


def proposing(**args) -> AIMessage:
    call = {"id": "p1", "name": "propose_skill", "args": args, "type": "tool_call"}
    return AIMessage(content="Here is the skill.", tool_calls=[call])


async def the_user_answers(settings, decision: str, note: str = "") -> dict:
    """The user answering the skill proposal that waits, as the page does; what they were asked."""
    folder = settings.proposals_folder("local")
    while not (files := list(folder.glob("*.json")) if folder.is_dir() else []):
        await asyncio.sleep(0.01)
    asked = ApprovalFile(files[0])
    approval = asked.read()
    assert asked.decide(approval.id, decision, note, "web")
    return {"tool": approval.tool, "consequence": approval.consequence, "screenshot": approval.screenshot}


def test_a_users_skills_are_read_afresh_and_one_broken_by_hand_is_left_out(tmp_path, caplog):
    assert user_catalog(tmp_path / "missing") == () and user_catalog(None) == ()
    write_skill(tmp_path, "invoices", INVOICES)
    (first,) = user_catalog(tmp_path)

    write_skill(tmp_path, "broken", "no frontmatter\n")
    write_skill(tmp_path, "receipts", skill_text("receipts", "Filing receipts.", "By month."))

    assert [skill.name for skill in user_catalog(tmp_path)] == ["invoices", "receipts"]
    assert first.description == "Entering invoices in the ledger app." and "skipped a user skill" in caplog.text


def test_a_built_in_skill_keeps_its_name_from_a_users(tmp_path):
    write_skill(tmp_path / "users", "skill-creator", skill_text("skill-creator", "Mine.", "Mine."))
    write_skill(tmp_path / "users", "invoices", INVOICES)

    skills = skills_of("main", user=user_catalog(tmp_path / "users"))

    assert [skill.name for skill in skills] == ["skill-creator", "invoices"]
    assert "Mine." not in SkillsMiddleware("main", user=user_catalog(tmp_path / "users")).section


def test_edits_apply_in_order_and_each_must_match_once():
    assert apply_edits("a b c", [("b", "x"), ("x c", "y")]) == "a y"
    for edits in ([("z", "x")], [("a", "x"), ("a", "y")], [("", "x")]):
        with pytest.raises(ValueError, match="occurs"):
            apply_edits("a b c", edits)


async def test_the_main_agent_lists_the_users_skills_and_hands_one_over_word_for_word(settings, computer, monkeypatch):
    write_skill(settings.skills_folder("local"), "invoices", INVOICES)
    computer_model, main_model = use_script(monkeypatch, [AIMessage("Entered.")], main=[
        AIMessage(content="", tool_calls=[tool_call(
            "m1", "start_task", agent="computer", title="Invoice", task="Enter the invoice.", skills=["invoices"],
        )]),
        AIMessage("Done."),
    ])

    await run_turn(settings, "enter this invoice", max_steps=5, computer=computer)

    system = main_model.received[0][0].text
    assert "- invoices: Entering invoices in the ledger app." in system and "- skill-creator: " in system
    opening = computer_model.received[0][1].text
    assert "Skills handed over with this task" in opening and "### invoices" in opening
    assert "Type the total last; the app recalculates." in opening


async def test_a_run_can_be_pointed_at_a_skills_folder_of_its_own(settings, computer, monkeypatch, tmp_path):
    write_skill(settings.skills_folder("local"), "invoices", INVOICES)
    write_skill(tmp_path / "arm", "receipts", skill_text("receipts", "Filing receipts.", "By month."))
    _, main_model = use_script(monkeypatch, [], main=[AIMessage("Hi.")])

    await run_turn(settings, "hi", max_steps=5, computer=computer, skills=tmp_path / "arm")

    system = main_model.received[0][0].text
    assert "- receipts: Filing receipts." in system and "invoices" not in system


async def test_a_hand_over_naming_no_skill_starts_nothing(settings, computer, monkeypatch):
    _, main_model = use_script(monkeypatch, [], main=[
        AIMessage(content="", tool_calls=[tool_call(
            "m1", "start_task", agent="computer", title="Invoice", task="Enter it.", skills=["invoice"],
        )]),
        AIMessage("Sorry."),
    ])

    result = await run_turn(settings, "enter this invoice", max_steps=5, computer=computer)

    refused = main_model.received[1][-1]
    assert isinstance(refused, ToolMessage) and refused.status == "error"
    assert "No skill named 'invoice'. Skills: skill-creator." in refused.text
    assert logged(result, "task", MAIN) == [] and computer.calls == []


async def test_a_new_skill_is_written_only_once_the_user_approves_it(settings, computer, monkeypatch):
    _, main_model = use_script(monkeypatch, [], main=[
        proposing(name="invoices", description="Entering invoices in the ledger app.",
                  instructions="Type the total last; the app recalculates."),
        AIMessage("Saved."),
    ])
    folder = settings.skills_folder("local")
    answering = asyncio.create_task(the_user_answers(settings, "approve"))
    result = await asyncio.wait_for(run_turn(settings, "save that as a skill", max_steps=5, computer=computer), 20)

    asked = await answering
    assert asked["consequence"] == "save a new skill invoices: Entering invoices in the ledger app."
    assert asked["tool"] == "propose_skill" and asked["screenshot"] is None
    assert (folder / "invoices" / "SKILL.md").read_text(encoding="utf-8") == INVOICES
    assert "Saved: the user approved the skill invoices." in main_model.received[1][-1].text
    (confirm,) = logged(result, "confirm")
    assert confirm["args"] == {"name": "invoices", "skill": INVOICES}
    (decision,) = logged(result, "decision")
    assert (decision["outcome"], decision["by"]) == ("approved", "web")
    assert list(settings.proposals_folder("local").iterdir()) == []  # nothing left waiting


async def test_a_declined_skill_is_not_written_and_the_agent_hears_why(settings, computer, monkeypatch):
    _, main_model = use_script(monkeypatch, [], main=[
        proposing(name="invoices", description="Invoices.", instructions="Total last."),
        AIMessage("Understood."),
    ])
    answering = asyncio.create_task(the_user_answers(settings, "reject", "Too narrow."))

    result = await asyncio.wait_for(run_turn(settings, "save that as a skill", max_steps=5, computer=computer), 20)

    await answering
    assert not (settings.skills_folder("local") / "invoices").exists()
    declined = main_model.received[1][-1]
    assert declined.status == "error" and declined.text == "Not executed: the user declined the skill. Too narrow."
    (decision,) = logged(result, "decision")
    assert (decision["outcome"], decision["note"]) == ("declined", "Too narrow.")


async def test_a_change_is_proposed_as_edits_and_overwrites_the_skill(settings, computer, monkeypatch):
    folder = settings.skills_folder("local")
    write_skill(folder, "invoices", INVOICES)
    use_script(monkeypatch, [], main=[
        proposing(name="invoices", edits=[{"old_string": "Type the total last", "new_string": "Type the date first"}]),
        AIMessage("Changed."),
    ])
    answering = asyncio.create_task(the_user_answers(settings, "approve"))

    await asyncio.wait_for(run_turn(settings, "change the skill", max_steps=5, computer=computer), 20)

    asked = await answering
    assert asked["consequence"] == "change the skill invoices (1 edit): Entering invoices in the ledger app."
    assert (folder / "invoices" / "SKILL.md").read_text(encoding="utf-8") == skill_text(
        "invoices", "Entering invoices in the ledger app.", "Type the date first; the app recalculates.",
    )
    assert [path.name for path in folder.iterdir()] == ["invoices"]


@pytest.mark.parametrize(("args", "error"), [
    ({"name": "Invoices!", "description": "x", "instructions": "y"}, "is not a skill name"),
    ({"name": "windows", "description": "x", "instructions": "y"}, "is a built-in skill"),
    ({"name": "invoices", "description": "x"}, "give a description and instructions for a new one"),
    ({"name": "receipts", "edits": [{"old_string": "a", "new_string": "b"}]}, "no skill receipts"),
    ({"name": "invoices", "description": "x", "instructions": "y", "existing": True}, "change it with edits"),
    ({"name": "invoices", "edits": [{"old_string": "nowhere", "new_string": "b"}], "existing": True}, "occurs 0 times"),
])
async def test_a_proposal_that_cannot_be_asks_nothing(settings, computer, monkeypatch, args, error):
    if args.pop("existing", False):
        write_skill(settings.skills_folder("local"), "invoices", INVOICES)
    _, main_model = use_script(monkeypatch, [], main=[proposing(**args), AIMessage("Sorry.")])

    result = await asyncio.wait_for(run_turn(settings, "save it", max_steps=5, computer=computer), 20)

    refused = main_model.received[1][-1]
    assert refused.status == "error" and error in refused.text
    assert logged(result, "confirm") == []

