"""The learning experiment's command line (eval/learn): see eval/README.md."""

import argparse
import json
import sys
from pathlib import Path

from learning import cases, runs, score

SEED = 20261119


def shown(event: dict) -> str:
    """A proposal as the person deciding reads it: what it would do, then the whole skill, or its edits."""
    args = event.get("args") or {}
    edits = "\n".join(f"- {edit['old_string']}\n+ {edit['new_string']}" for edit in args.get("edits") or [])
    return "\n".join(part for part in (event.get("consequence", ""), args.get("skill", ""), edits) if part)


def ask(event: dict) -> tuple[str, str]:
    """The person at the terminal answers each proposed skill; any other step is approved."""
    if event["tool"] != runs.PROPOSE:
        return "approve", ""
    print(f"\n{shown(event)}\n")
    reason = input("Approve this skill? Enter to approve, or say why not: ").strip()
    return ("reject", reason) if reason else ("approve", "")


def approve_all(event: dict) -> tuple[str, str]:
    return "approve", ""


def arms(given: list[str]) -> dict[str, list[Path]]:
    """NAME=FOLDER, once for each repeat of an arm."""
    found: dict[str, list[Path]] = {}
    for each in given:
        name, equals, folder = each.partition("=")
        if not (name and equals and folder):
            raise ValueError(f"--arm takes NAME=FOLDER, not {each!r}")
        found.setdefault(name, []).append(Path(folder))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval/learn", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="write both kinds of work: cases, answer keys, sham and oracle skills")
    generate.add_argument("out", type=Path)
    generate.add_argument("--seed", type=int, default=SEED)
    teach = sub.add_parser("teach", help="have the agent make a skill from the training cases, one at a time")
    teach.add_argument("work", type=Path, help="a kind of work's folder, as generate wrote it")
    teach.add_argument("out", type=Path, help="a new folder for the logs and the skills after each case")
    teach.add_argument("--yes", action="store_true", help="approve every proposed skill without asking")
    run = sub.add_parser("run", help="run the held-out cases once with a folder of skills, or with none")
    run.add_argument("work", type=Path)
    run.add_argument("out", type=Path, help="a new folder for this repeat's results")
    run.add_argument("--skills", type=Path, help="a folder of skills, such as WORK/oracle or a teach run's after-06")
    for each in (teach, run):
        each.add_argument("--version", default="worktree", help="a git ref whose runtime/src to run (default: the "
                                                                "working tree as it is)")
    judge = sub.add_parser("score", help="score runs of the held-out cases and judge the criteria")
    judge.add_argument("work", type=Path)
    judge.add_argument("--arm", action="append", required=True, metavar="NAME=FOLDER", help="once for each repeat")
    judge.add_argument("--learned", default="learned", help="the arm the criteria are about (default: learned)")
    judge.add_argument("--baseline", action="append", metavar="NAME",
                       help="an arm it must beat, the one without a skill first (default: baseline and sham)")
    judge.add_argument("--report", type=Path, help="also write the report here, as Markdown")
    judge.add_argument("--json", type=Path, help="also write the report's data here")
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            for name, made in cases.generate(args.out, args.seed).items():
                print(f"{args.out / name}: {len(made)} cases")
        elif args.command == "teach":
            with runs.server(None, args.version) as (api, skills):
                runs.teach(api, args.work, args.out, skills, approve_all if args.yes else ask)
        elif args.command == "run":
            with runs.server(args.skills, args.version) as (api, skills):
                runs.heldout(api, args.work, args.out, skills)
        else:
            text, data = score.report(args.work, arms(args.arm), args.learned, args.baseline or ["baseline", "sham"])
            if args.report:
                args.report.write_text(text, encoding="utf-8")
            if args.json:
                args.json.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
            print(text)
    except (OSError, ValueError) as error:
        print(f"{args.command}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
