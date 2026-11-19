# Windows evaluation

Run Windows desktop tasks through the runtime, grade the results by hand, and compare code versions or models. These tasks make real changes on the connected Windows test machine and call model APIs. The [learning experiment](#learning-experiment) is apart from them and uses no desktop.

## Quick start

First, complete the [runtime setup](../runtime/README.md#setup). Keep the runtime container running and its Windows connection active. Use a dedicated test desktop.

From the repository root, install the local Python environment used by `eval/ev`:

```bash
uv sync --frozen --project runtime
```

Close unrelated windows on the Windows machine. Once the desktop is ready for testing, save its initial screenshot as the local baseline:

```bash
eval/ev reset --accept
```

Then list the tasks, run a sample and create the report:

```bash
eval/ev tasks              # list the tasks
eval/ev run excel-sort word-zh  # run two tasks
eval/ev report             # pass rates, the grading sheet and the HTML report
```

`eval/ev` runs [eval.py](eval.py) with the runtime's Python and the [CLI](../clients/cli/README.md)'s API client. Each run copies the selected `runtime/src` into the running container and starts an eval server on port 8001. Source changes need no image rebuild; dependency changes do.

To try a version by hand the same way, `eval/ev serve [--version REF]` serves it on port 8001 until Ctrl-C. Docker commands use your current Docker context (`docker context use` or `DOCKER_CONTEXT`).

The eval and web servers share stored runs and the desktop lock. Only one run can use the desktop at a time. Task setups and the cleanup script hold the same lock while they type; if something else holds it, the eval stops. Eval conversations appear under the eval project in the web UI. To follow them live, run `CU_API_URL=http://127.0.0.1:8001 bun run dev` in `clients/web/`. Versions predating the CLI's HTTP API client are unsupported.

## Tasks

[tasks.jsonl](tasks.jsonl) holds 22 tasks, one per line. `eval/ev tasks excel-sort` shows one task in full, with the
PowerShell that sets it up.

| Kind | Tasks |
|---|---|
| Windows Agent Arena | notepad-count, calc-days, paint-canvas, explorer-archive, explorer-restore, storage-sense, chrome-pdf |
| Back-office web pages | web-form, web-reconcile (a page against a spreadsheet), web-paged-lookup (an ID from one page, its phone from a 250-page table) |
| OSWorld Windows (Office) | excel-sort, excel-dropdown, word-subscript, ppt-duplicate, bookkeeping, paper-authors |
| Typing Traditional Chinese | word-zh |
| Infeasible: the right answer is "this cannot be done" | missing-file, calc-lunar; scored apart |
| Distractor: a similar open file must be left alone | notepad-distractor |
| Keyboard only: a submenu ignores the mouse | submenu-keys |
| Professional system: function codes, query mode, value lists, maker-checker | pro-system: a local mock, change a party's phone and leave it unauthorized |

Each line defines one task: its ID (`id`), which names its results folder, source, starting state (`start`), goal (`goal`),
expected result (`expect`) and setup steps (`setup`). The setup steps are converted to PowerShell by
`step_script` in [winsetup.py](winsetup.py). Optional fields are `feasible: false` and `max_steps` (default 60).

The `{name}` placeholder becomes `ev-<version>-<task digest>-<attempt>`, giving each attempt its own files without naming the task to the agent.

## Running

```bash
eval/ev run excel-sort word-zh --trials 3                 # run each task three times
eval/ev run excel-sort --version HEAD                     # use the latest committed runtime source
eval/ev run excel-sort --computer openai:gpt-6-luna medium # override the desktop model and effort
```

Name no tasks to run all 22. Without `--version`, the evaluator uses the current working-tree source, including uncommitted changes under `runtime/src`.

Before each task, `run` checks that the desktop is connected, available and matches the local `clean-desktop.png` baseline. It waits up to a minute for reconnection. It then sets up the task through PowerShell, runs the goal and closes any windows it opened without saving.

Close unrelated windows yourself before testing. If only the wallpaper changed, update the baseline with `eval/ev reset --accept`.

Steps the agent asks approval for are approved automatically, and a run whose agent asks the user a question ends there, its question in the output. A run still going after 30 minutes is stopped.

Results go to `results/<version>/<task>/<attempt>/` (`result.json`, `start.png`, the run's log and screenshots
as `computer-use export` writes them). See [versions and prompts](#versions-and-prompts) for how a version is named.

## Versions and prompts

The eval compares results: of two prompt versions, two code versions or two model setups.

Versions use the short commit hash. Uncommitted changes under `runtime/src` add a source digest, such as `e856315-80cad5`, keeping each revision's results separate. Use `--note` to give the version a readable label in reports. The label stays until a later run replaces it.

```bash
eval/ev run excel-sort word-zh --trials 3 --note "shorter plan steps"
```

Each version saves its tested source in `src/`, including uncommitted prompts. Its `version.json` records separate digests for `runtime/src/computer_use/prompts/` and the remaining code. The report identifies comparisons where only prompts changed and shows their diff below the results.

## Grading

Open `results/<version>/grade.md`. Each run shows the goal, what to expect, the agent's reply, the start and end
screens and the actions. Write `pass` or `fail` after `Grade:`, then why. Leave a run ungraded to leave it out.

`eval/ev report [versions]` rebuilds the sheets (keeping the grades), prints each version's pass rate and per-task
numbers, and writes `results/report.html`, where you pick two versions or model setups to compare side by side.
The report's thumbnails link to the screenshots next to it, so keep it in `results/`.

## Repeated trials

`eval/ev report` prints, for each version and agents' models, every trial: pass/graded per task, ungraded and
not-started counts, how the desktop tasks ended, main/subagent model calls (`M/S`), the median time of the passes and
the cost, `?` where one is unknown. Infeasible tasks are marked and graded separately. The HTML compares each task's
latest trial, for diagnosis; use the terminal for repeated A/B comparisons. Offline checks:

```bash
cd runtime
.venv/bin/python -m pytest tests/unit/test_eval.py -q
```

## Fair comparisons

- **Alternate** versions or models from task to task: model latency drifts over hours.
- **Run each task at least three times** before claiming a change helped.
- **Reset state an earlier run left** (a setting turned on stays on) with a `registry` or `reset-app` setup step.
- **Watch for a frozen screen**: after a reconnect the RDP client can keep showing an old frame (the taskbar clock
  stops). Kill `xfreerdp3` in the container and drop those runs.
- **Keep the Mac awake** (`caffeinate -i -s`), or the connection drops mid-run.

## Learning experiment

A separate measurement from the Windows tasks: does a skill the agent made from worked cases change its answers on
cases it never saw? It uses no desktop. `eval/learn` ([learning/](learning/)) drives it; every case is made-up data.

`eval/learn generate OUT` writes two kinds of office work, `OUT/expenses` and `OUT/letters`. Each has 6 training
cases and 6 held-out cases: a few attached files, and an answer of eight fields.

| Field kind | What it is | What it shows |
|---|---|---|
| planted | follows a rule the task does not state and no file spells out | whether the rule was learned |
| control | the task or the files say it outright | whether a skill costs anything |
| absent | no file holds it; the right answer is null | whether a skill makes the agent invent values |

One planted rule of each kind of work needs the last page of a PDF with more pages than a model is sent, so the agent
reads it only through code and can stop at its first page, where the value looks settled. Which case meets which
condition is fixed, so that no simpler rule than the planted one fits the training cases.

### Arms

An arm is the held-out cases run with one folder of skills. Run each arm at least three times.

```bash
eval/learn run OUT/expenses RUNS/baseline-1                                # no skill
eval/learn run OUT/expenses RUNS/sham-1 --skills OUT/expenses/sham         # a skill that states no rule
eval/learn run OUT/expenses RUNS/oracle-1 --skills OUT/expenses/oracle     # a skill that states every rule
eval/learn teach OUT/expenses TAUGHT                                       # the agent makes a skill, case by case
eval/learn run OUT/expenses RUNS/learned-1 --skills TAUGHT/after-06
```

`run` and `teach` each start the eval's server (port 8001) with the working tree's `runtime/src`, or `--version REF`,
and a folder of skills of its own under `runtime/data/learning/` (`CU_SKILLS_DIR`), so a run sees those skills and no
others. They call model APIs. A held-out run rejects any skill the agent proposes, since a later case would run with
it; `arm.json` says which skills the cases saw and whether they changed.

`teach` sends each training case with its right answer and asks for a skill, new or improved. It shows you each
proposal: Enter approves it, and a reason rejects it. Reject one that copies a case's names or figures, contradicts
the cases, or cannot be followed, and give no hint of the rules in your reason: what you type is taught, not learned.
`TAUGHT/after-NN` is the skills folder after case NN, so `after-01`, `after-03` and `after-06` show how the skill
grows. If you can, approve without having read `keys/`, `rules.json`, the oracle skill or `learning/cases.py`;
otherwise note that you knew the rules.

The oracle arm is the ceiling: a rule it misses is one the agent cannot apply even when told, not one learning
missed. Score the baseline alone first: where it already reads the last page and gets that field right, the
last-page criterion cannot pass.

### Scoring

```bash
eval/learn score OUT/expenses --arm baseline=RUNS/baseline-1 --arm baseline=RUNS/baseline-2 \
    --arm sham=RUNS/sham-1 --arm learned=RUNS/learned-1 --report expenses.md
```

Give `--arm NAME=FOLDER` once for each repeat. Fields are compared by code: text after trimming, joining spaces and
ignoring case, numbers by value, and nothing else, so another date format is wrong. The learning worked for a kind
of work only if all four criteria pass:

1. On planted fields, the learned arm's worst repeat beats the best repeat of the baseline and the sham.
2. On control fields, its worst repeat is not below the baseline's worst.
3. It makes up values no more often than the baseline.
4. On the last-page rule, its worst repeat reads that page and gets the field right more often than their best.
   Reading the page shows in the run's log as text only that page holds.

One taught skill shows whether that skill helps, not whether teaching works reliably: for that, teach twice more and
score each skill's arms apart. Offline checks, which say nothing of how a real model does:

```bash
cd runtime
.venv/bin/python -m pytest tests/unit/test_learning_experiment.py -q
```
