---
name: skill-creator
description: Create a skill of the user's, or improve one, and test it against working without it. Read when the user asks to make, save, change or test a skill, or to turn how a task was just done into something the agents follow next time.
---

A skill is know-how the agents follow when their work matches its description: a name, a description of when to use it, and instructions. The user's skills are listed with yours; you pass one to an agent with start_task's `skills`. You never write a skill yourself: propose_skill shows it to the user, and only their approval saves it.

Work out where the user is and start there. Someone who says "make that a skill" right after a task wants a draft from that task; someone who brings a draft wants it tested; someone who says "just save it" wants no tests.

## 1. Capture what the skill is for

Take what you can from the conversation first: what was asked, what the agents did, where they went wrong, what the user corrected or confirmed. Then settle what is still open, with ask_user, one question at a time:

- What should the agents be able to do with it, and what should the result look like?
- When should it apply: which tasks, apps, sites or files? That becomes the description.
- Should it be tested? Suggest tests when the result can be checked (a file's contents, a form's values, a fixed sequence of steps), and none when it is a matter of taste. Tests on the computer take the desktop and time, so let the user choose.

## 2. Write the draft

- **Name**: lowercase words joined by hyphens, after what it does, e.g. `expense-report`.
- **Description**: what it covers and when to use it, in one or two sentences. Agents decide from the description alone whether to read a skill, so name the tasks, apps and words a user would use, and lean towards applying it.
- **Instructions**: Markdown, in the imperative. Say why a step matters rather than only that it must be done, so the agent can handle a case the skill does not list. Keep to what the agent would not know: the user's own conventions, where things are, the order that works, and the traps that were hit. Leave out what any agent already does, and anything that holds for one task only. A worked example helps more than a rule when the format matters.

Read the draft once more as the agent that gets it: with only this text and the task, would it do the work the way the user wants?

## 3. Test it against working without it

Write two or three realistic tasks the skill should cover, as a user would phrase them, with what a good result is. Show them to the user before running anything.

For each task, hand over two tasks with start_task: one whose brief carries the draft's instructions under "Follow these instructions:", and one, the baseline, with the same brief and no instructions. Computer tasks run one at a time; coding tasks can run together. Then compare each pair against what a good result is:

- Did the skill change the result, or would the agent have done the same without it? A skill that changes nothing is not worth saving.
- Where the run with the skill still went wrong, was it the instructions (unclear, missing a case, wrong) or something else?

Tell the user what you saw in a few lines per task, and ask what they think.

## 4. Revise

Change the draft for what the comparison and the user showed: generalise from the failures rather than adding a rule for each one, and remove what did not help. Test again when the change was large. Stop when the user is satisfied or the results stop improving.

## 5. Propose it

Call propose_skill, and in the same reply show the user what they are approving: the whole skill for a new one, the edits for a change. If they decline, read their reason, revise, and propose again only if they want it.

To improve one of the user's skills, read it with read_skill first, then propose edits that copy the text to replace exactly. Propose a new description only when when to use it has changed.
