---
name: grip-explain
description: Turn the developer's own answers from the last grip quiz into a commit message or pull request description, so the explanation they just gave is not lost. Use for /grip-explain, "write the commit message from my quiz", "describe this change from my answers".
disable-model-invocation: true
allowed-tools: Bash(grip *)
---

# grip-explain

The developer just explained their change in five answers. Reuse those words. You are an
editor, not an author: tighten, order, fix typos, keep their claims and their voice. Add
nothing they did not say.

Arguments: `$ARGUMENTS` is empty (a commit message), `--pr` (a pull request description
with headings), or `--short` (a one-line subject only).

## Steps

1. Run `grip last --json`. If it fails because nothing was graded, say so and suggest
   `/grip` first.
2. Read `summary` (grip's one-line description of the change), `questions`, `answers`,
   and the per-question `grades` with their `focus`.
3. Draft from the answers, in this order: what the change does (behaviour), why
   (motivation), what to watch for (edge case, risk), how it was verified (testing). Skip
   any answer that scored 0, it was blank or wrong. Never quote scores, feedback or the
   verdict; they are for the developer, not for the log.
4. Format:
   - Default: a commit message. Subject under 72 characters in the imperative, taken from
     the behaviour answer or the summary. Blank line. Body in short paragraphs, wrapped at
     72 columns. No bullet lists of file names.
   - `--pr`: `## What`, `## Why`, `## Risks`, `## How it was tested`, one or two sentences
     each, from the matching answers.
   - `--short`: the subject line only.
5. Show the draft in a code block and ask whether to use it. Do not commit or push on
   your own; if the developer asks you to, use their draft unchanged.

## Notes

- `grip last` without `--json` shows the same quiz in a readable form; use it when the
  developer wants to reread what they wrote.
- If the quiz failed, say so once and still draft: a message written from a failed quiz
  is a prompt to go back and read, not a ticket to push.
