"""The experiment's two kinds of work and the cases of each: a monthly expense summary and a record of a customer's
letter. Every case is a few attached files and an answer of eight fields, each of one kind:

- planted: follows a rule the task does not state and no file spells out, so only a taught agent gets it right;
- control: the task or the files say it outright;
- absent: no file holds it, so the right answer is null and any value is made up.

One planted rule of each kind of work needs the last page of a long PDF. The PDF has more pages than a model is sent
(attachments.MAX_PAGES), so the agent reads it only through code and can stop at its first page, where the value looks
settled. Which case meets which condition is fixed by PLANS, not drawn, so that no simpler rule than the planted one
fits the training cases; the values themselves are drawn from the seed."""

import json
import random
import textwrap
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from computer_use.attachments import MAX_PAGES

from learning import files

PDF_PAGES = MAX_PAGES + 20
YEAR = 2026
SPLITS = ("train", "heldout")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
GIVEN = ("Alder", "Brisa", "Cato", "Delph", "Edda", "Faro", "Greta", "Hale", "Ines", "Joss", "Kaia", "Leif", "Mira",
         "Nico", "Odile", "Piers", "Rhea", "Soren", "Tamsin", "Ulla")
FAMILY = ("Abernant", "Birchall", "Caldwell", "Dunmore", "Eastlake", "Fenwick", "Garrow", "Hartnell", "Ingleby",
          "Kestrel", "Lowther", "Merrow", "Norcott", "Oakshott", "Penhale", "Redfern", "Stanhope", "Thackray")
TEAMS = ("Site Operations", "Archive", "Dispatch", "Customer Care", "Maintenance")
SPENT_ON = ("Fares", "Food", "Hotel", "Stationery", "Courses")
REGIONS = ("Coastal", "Highland", "Central", "Lakeside")
FILLER = (
    "Read this part together with the general terms at the front of the document.",
    "A written instruction from the head of a team replaces this part for that team only.",
    "Anyone unsure how this part applies should ask the owner of the document before acting.",
    "Each exception is written down with who agreed to it, on which day, and why.",
    "This part is looked at again once a year, and staff are told of any change.",
    "Papers that support a request are kept as long as the part on records says.",
    "Figures are in the reporting currency unless the part itself names another.",
    "A supervisor may send a request back and ask for more before it moves on.",
)

Kind = str  # "planted", "control" or "absent"


@dataclass(frozen=True)
class Field:
    value: str | int | None
    kind: Kind
    rule: str | None = None  # the planted rule it follows


@dataclass(frozen=True)
class LastPage:
    """The field the long PDF's last page decides: where that page is, text only it holds (so finding any of it in
    what a tool returned shows the page was read), and whether it changes this case's answer from `first`, the value
    the first page gives."""

    field: str
    file: str
    page: int
    markers: tuple[str, ...]
    changes: bool
    first: str


@dataclass(frozen=True)
class Case:
    id: str
    work: str
    split: str
    files: dict[str, bytes]
    fields: dict[str, Field]
    last_page: LastPage

    @property
    def answer(self) -> dict:
        return {name: field.value for name, field in self.fields.items()}

    def key(self) -> dict:
        """What the scorer reads: everything but the files."""
        return {"case": self.id, "work": self.work, "split": self.split,
                "fields": {name: asdict(field) for name, field in self.fields.items()},
                "last_page": asdict(self.last_page)}


@dataclass(frozen=True)
class Expenses:
    """One expense case's conditions. `large`: a claim that counts is 1000 or more. `total`: "low" or "high" pins the
    sum, so the flag and the sum come apart. `near`: a claim that counts is just under 1000. `large_void`: a void claim
    is over 1000. `delegated`: the policy's last page names a delegate."""

    large: bool = False
    total: str = ""
    near: bool = False
    large_void: bool = False
    delegated: bool = True


@dataclass(frozen=True)
class Letter:
    """One letter case's conditions. `about`: "card", "fee" or "" for neither. `amount`: "small", "under" or "over"
    (just either side of 500) or "large". `replaced`: the handbook's last page names a new contact for the region."""

    about: str = ""
    amount: str = "small"
    urgent: bool = False
    replaced: bool = True


# Each split's cases in order. Among the training cases: the flag is Y once, on the lowest total, and N on the highest
# and beside a large void claim and a claim just under 1000; a card letter comes once and is not P1; a fee letter is P3
# once; and amounts just either side of 500 differ in priority. The held-out cases meet every condition again.
PLANS = {
    "expenses": {
        "train": (Expenses(), Expenses(large=True, total="low"), Expenses(near=True, delegated=False),
                  Expenses(total="high"), Expenses(large_void=True), Expenses(delegated=False)),
        "heldout": (Expenses(large=True), Expenses(total="high"), Expenses(near=True, delegated=False),
                    Expenses(large=True, total="low", delegated=False), Expenses(large_void=True), Expenses()),
    },
    "letters": {
        "train": (Letter("fee"), Letter(amount="over"), Letter("card", replaced=False), Letter("fee", urgent=True),
                  Letter(amount="under"), Letter("fee", "large", replaced=False)),
        "heldout": (Letter("card", "large"), Letter("fee", "under", replaced=False), Letter(urgent=True),
                    Letter("card"), Letter("fee", "over"), Letter(replaced=False)),
    },
}
# Cents a letter names, by Letter.amount.
AMOUNTS = {"small": (1500, 42000), "under": (46000, 49999), "over": (50001, 54999), "large": (60000, 98000)}


def person(rng: random.Random, taken: set[str]) -> str:
    """A name no one else in the case has, so an answer names one person."""
    while (name := f"{rng.choice(GIVEN)} {rng.choice(FAMILY)}") in taken:
        pass
    taken.add(name)
    return name


def money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def long_pdf(rng: random.Random, title: str, first: list[str], last: list[str]) -> bytes:
    """A PDF of PDF_PAGES pages: `first`, pages that hold nothing an answer needs, then `last`."""
    middle = []
    for number in range(2, PDF_PAGES):
        text = " ".join(rng.choice(FILLER) for _ in range(12))
        middle.append([f"{title}, part {number}", "", *textwrap.wrap(text, 92)])
    return files.pdf([first, *middle, last])


def claims(rng: random.Random, plan: Expenses) -> list[dict]:
    """The month's claims, two of them void, meeting the plan. Drawn again until the sum that counts is on the right
    side of its bound and rounds one way only, away from a whole number and a half."""
    low, high = {"low": (1200, 25000), "high": (55000, 89000)}.get(plan.total, (1200, 89000))
    while True:
        count = {"low": 7, "high": 10}.get(plan.total) or rng.randint(7, 10)
        drawn = [{"id": f"CL-{rng.randint(10000, 99999)}", "day": rng.randint(1, 28), "on": rng.choice(SPENT_ON),
                  "cents": rng.randint(low, high), "void": False} for _ in range(count)]
        void = rng.sample(drawn, 2)
        for claim in void:
            claim["void"] = True
        counted = [claim for claim in drawn if not claim["void"]]
        rng.shuffle(counted)
        if plan.large:
            counted[0]["cents"] = rng.randint(100000, 130000 if plan.total == "low" else 240000)
        if plan.near:
            counted[1]["cents"] = rng.randint(95000, 99999)
        if plan.large_void:
            void[0]["cents"] = rng.randint(110000, 190000)
        total = sum(claim["cents"] for claim in counted)
        bounded = {"low": total < 300000, "high": total > 400000}.get(plan.total, True)
        if bounded and total % 100 not in (0, 50) and len({claim["id"] for claim in drawn}) == count:
            return sorted(drawn, key=lambda claim: (claim["day"], claim["id"]))


def expenses(rng: random.Random, id: str, split: str, plan: Expenses) -> Case:
    names: set[str] = set()
    employee, approver, delegate = person(rng, names), person(rng, names), person(rng, names)
    team, cost_center, month = rng.choice(TEAMS), f"CC-{rng.randint(1000, 9999)}", rng.randint(1, 12)
    period = f"{YEAR}-{month:02d}"
    listed = claims(rng, plan)
    counted = [claim["cents"] for claim in listed if not claim["void"]]
    rows = [["Employee", employee], ["Cost center", cost_center], ["Team", team], ["Period", period], [],
            ["Claim", "Date", "Spent on", "Amount", "Status"]]
    rows += [[claim["id"], f"{period}-{claim['day']:02d}", claim["on"], files.Decimal(money(claim["cents"])),
              "void" if claim["void"] else "approved"] for claim in listed]
    heading = "Annex: standing in for an approver"
    policy = long_pdf(rng, "Expense policy", [
        "Expense policy", "", f"Team: {team}", f"Claims of the {team} team are approved by {approver}.", "",
        "This policy says what staff may claim, what proof a claim needs, and who approves it.",
    ], [heading, "", (f"For {period}, {delegate} approves the claims of the {team} team instead of the approver."
                      if plan.delegated else f"Nobody stands in for the approver of the {team} team in {period}.")])
    return Case(id, "expenses", split, {"claims.xlsx": files.workbook("Claims", rows), "expense-policy.pdf": policy}, {
        "employee": Field(employee, "control"),
        "cost_center": Field(cost_center, "control"),
        "period": Field(f"{YEAR}/{month:02d}", "planted", "expenses.period"),
        "claim_count": Field(len(counted), "planted", "expenses.count"),
        "total_amount": Field((sum(counted) + 50) // 100, "planted", "expenses.total"),
        "review_flag": Field("Y" if max(counted) >= 100000 else "N", "planted", "expenses.review"),
        "approved_by": Field(delegate if plan.delegated else approver, "planted", "expenses.delegate"),
        "payment_reference": Field(None, "absent"),
    }, LastPage("approved_by", "expense-policy.pdf", PDF_PAGES,
                (heading, "instead of the approver", "Nobody stands in for"), plan.delegated, approver))


def letter(rng: random.Random, id: str, split: str, plan: Letter) -> Case:
    names: set[str] = set()
    customer, replacement = person(rng, names), person(rng, names)
    contacts = {region: person(rng, names) for region in REGIONS}
    region, day, month = rng.choice(REGIONS), rng.randint(1, 28), rng.randint(1, 12)
    cents = rng.randint(*AMOUNTS[plan.amount])
    accounts = {f"AC-{number}": (rng.choice(REGIONS), f"BR-{rng.randint(10, 99)}")
                for number in rng.sample(range(100000, 1000000), 8)}
    account = rng.choice(sorted(accounts))
    accounts[account] = (region, accounts[account][1])
    said = {
        "card": ["My card was turned down at a shop last week and I would like to know why.",
                 f"The payment was for {money(cents)} and there was money in the account."],
        "fee": [f"My statement shows a fee of {money(cents)} that I do not recognise.",
                "Nobody told me about this charge, so please explain what it is for."],
    }.get(plan.about, ["Please send me copies of my statements for the last twelve months.",
                       f"My address has changed as well; my latest deposit was {money(cents)}."])
    if plan.urgent:
        said.append("This is urgent: I need your answer by the end of this week.")
    text = [f"Customer: {customer}", f"Account number: {account}", f"Received on {day} {MONTHS[month - 1]} {YEAR}",
            "", "Dear Sir or Madam,", "", *said, "", "Yours faithfully,", customer]
    heading = "Annex: changes of contact this month"
    handbook = long_pdf(rng, "Customer letter handbook", [
        "Customer letter handbook", "", "Who to escalate to, by region:",
        *(f"{each}: {contacts[each]}" for each in REGIONS), "",
        "This handbook says how a letter from a customer is recorded, answered and closed.",
    ], [heading, "", (f"{region} region: escalate to {replacement} from now on." if plan.replaced
                      else "No contact changes this month.")])
    table = files.table([["account", "region", "branch"], *([each, *accounts[each]] for each in sorted(accounts))])
    return Case(id, "letters", split, {"letter.docx": files.document(text), "accounts.csv": table,
                                       "letter-handbook.pdf": handbook}, {
        "customer_name": Field(customer, "control"),
        "account_number": Field(account, "control"),
        "branch_code": Field(accounts[account][1], "control"),
        "received_date": Field(f"{day:02d}/{month:02d}/{YEAR}", "planted", "letters.date"),
        "category": Field({"card": "C2", "fee": "B1"}.get(plan.about, "G0"), "planted", "letters.category"),
        "priority": Field("P1" if plan.urgent or cents > 50000 else "P3", "planted", "letters.priority"),
        "escalation_contact": Field(replacement if plan.replaced else contacts[region], "planted", "letters.contact"),
        "case_reference": Field(None, "absent"),
    }, LastPage("escalation_contact", "letter-handbook.pdf", PDF_PAGES,
                (heading, "from now on", "No contact changes this month"), plan.replaced, contacts[region]))


@dataclass(frozen=True)
class Work:
    """A kind of work: what the agent is asked, how a case is built, its planted rules as the report words them, and
    two skills to compare a learned one with, under the name and description a learned one would have. `oracle` states
    the planted rules: what the agent does with it is the most that learning could reach. `sham` is as long but says
    only what the task already does: a gain it brings is not from the rules."""

    task: str
    build: Callable[[random.Random, str, str, object], Case]
    rules: dict[str, str]
    skill: str
    description: str
    oracle: tuple[str, ...]
    sham: tuple[str, ...]

    def skill_file(self, steps: tuple[str, ...]) -> str:
        """A SKILL.md of these steps, as the runtime's skills are written."""
        listed = "\n".join(f"{number}. {step}" for number, step in enumerate(steps, 1))
        return f"---\nname: {self.skill}\ndescription: {self.description}\n---\n\n# Steps\n\n{listed}\n"


WORKS = {
    "expenses": Work(
        "From the attached files, prepare the employee's monthly expense summary. Reply with one JSON object and "
        "nothing else, with the keys employee, cost_center, period, claim_count, total_amount, review_flag, "
        "approved_by and payment_reference. Give null for a value the files do not hold.",
        expenses,
        {
            "expenses.period": "period is written YYYY/MM",
            "expenses.count": "claim_count leaves out void claims",
            "expenses.total": "total_amount leaves out void claims and is rounded half up to a whole number",
            "expenses.review": "review_flag is Y when a claim that is not void is 1000 or more, else N",
            "expenses.delegate": "approved_by is the delegate the policy's last page names, when it names one",
        },
        "expense-summary",
        "Prepare an employee's monthly expense summary from a claims sheet and an expense policy.",
        (
            "Take employee and cost_center from the rows above the claims table.",
            "Write period as YYYY/MM, with a slash.",
            "Leave void claims out of every count and sum.",
            "claim_count is the number of claims that are not void.",
            "total_amount is the sum of the claims that are not void, rounded half up to a whole number.",
            "review_flag is Y when any claim that is not void is 1000 or more, and N otherwise. The total does not "
            "decide it.",
            "For approved_by, read the expense policy to its last page. When that page names someone standing in for "
            "the approver in the period, give that person; otherwise give the approver on the first page.",
            "Give null for payment_reference and any other value no file holds.",
        ),
        (
            "List the attached files and find the claims sheet and the expense policy.",
            "Read the claims sheet from its first row, including the rows above the table.",
            "Look in the expense policy for whatever the claims sheet does not say.",
            "Take every value of the summary from the attached files, not from memory.",
            "Fill in each key the task names, once.",
            "Check that the reply is the JSON object alone.",
            "Check each value against the file it came from before replying.",
            "Give null for a requested value that no attached file holds.",
        ),
    ),
    "letters": Work(
        "From the attached files, prepare the follow-up record for the customer's letter. Reply with one JSON object "
        "and nothing else, with the keys customer_name, account_number, branch_code, received_date, category, "
        "priority, escalation_contact and case_reference. Give null for a value the files do not hold.",
        letter,
        {
            "letters.date": "received_date is written DD/MM/YYYY",
            "letters.category": "category is C2 for a letter about a card, B1 for one about a fee or charge, else G0",
            "letters.priority": "priority is P1 when the letter says urgent or names an amount over 500, else P3",
            "letters.contact": "escalation_contact is the replacement the handbook's last page names, when it names one",
        },
        "letter-follow-up",
        "Prepare the follow-up record for a customer's letter from the letter, an account list and a letter handbook.",
        (
            "Take customer_name and account_number from the letter's first lines.",
            "branch_code is the branch of that account in the account list.",
            "Write received_date as DD/MM/YYYY.",
            "category is C2 when the letter is about a card, B1 when it is about a fee or charge, and G0 otherwise.",
            "priority is P1 when the letter says it is urgent or names an amount over 500, and P3 otherwise. What the "
            "letter is about does not decide it.",
            "For escalation_contact, read the letter handbook to its last page. When that page names a new contact "
            "for the account's region, give that person; otherwise give the region's contact on the first page.",
            "Give null for case_reference and any other value no file holds.",
        ),
        (
            "List the attached files and find the letter, the account list and the letter handbook.",
            "Read the letter from its first line, including the lines above the greeting.",
            "Find the customer's account in the account list.",
            "Look in the letter handbook for whatever the letter and the account list do not say.",
            "Take every value of the record from the attached files, not from memory.",
            "Check that the reply is the JSON object alone, each requested key once.",
            "Give null for a requested value that no attached file holds.",
        ),
    ),
}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def generate(out: Path, seed: int) -> dict[str, list[Case]]:
    """Write both kinds of work under `out`, which must be new or empty, and return their cases. Each gets a folder:

    task.txt                          the message every case is sent with
    rules.json                        the planted rules, for the report
    oracle/<skill>/SKILL.md           a skill that states the planted rules
    sham/<skill>/SKILL.md             a skill that states none of them
    train/<case>/files/, answer.json  a case to teach, and the answer it is corrected with
    heldout/<case>/files/             a case to measure
    keys/<case>.json                  every case's answer key, which no run is sent
    """
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} is not empty")
    made = {}
    for name, work in WORKS.items():
        rng = random.Random(f"{seed}/{name}")
        root = out / name
        (root / "keys").mkdir(parents=True)
        made[name] = []
        for split in SPLITS:
            for number, plan in enumerate(PLANS[name][split], 1):
                case = work.build(rng, f"{name}-{split}-{number:02d}", split, plan)
                folder = root / split / case.id
                (folder / "files").mkdir(parents=True)
                for file, data in case.files.items():
                    (folder / "files" / file).write_bytes(data)
                if split == "train":
                    write_json(folder / "answer.json", case.answer)
                write_json(root / "keys" / f"{case.id}.json", case.key())
                made[name].append(case)
        (root / "task.txt").write_text(work.task + "\n", encoding="utf-8")
        write_json(root / "rules.json", work.rules)
        for arm, steps in (("oracle", work.oracle), ("sham", work.sham)):
            (root / arm / work.skill).mkdir(parents=True)
            (root / arm / work.skill / "SKILL.md").write_text(work.skill_file(steps), encoding="utf-8")
    return made
