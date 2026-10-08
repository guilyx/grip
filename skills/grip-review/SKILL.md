---
name: grip-review
description: Check that a reviewer actually understands a pull request before approving it, or produce five review questions for its author. Use for /grip-review, "quiz me on this PR", "review questions for this branch", "do I understand this PR".
disable-model-invocation: true
allowed-tools: Bash(grip *), Bash(git fetch *), Bash(git rev-parse *), Bash(git merge-base *)
---

# grip-review

Same quiz, other side of the table. grip writes five questions about a range of commits;
either the reviewer answers them before approving, or the questions go to the author with
the review. You relay, you never answer.

Arguments: `$ARGUMENTS` is a range `BASE..HEAD` (for example `origin/main..origin/feat-x`),
a single branch (then the base is `origin/main`, or `main`), or empty (then ask which
branch). Add `--questions-only` to skip the grading and just print the questions.

## Steps

1. Work out the range. For a branch name `X`, fetch it if needed (`git fetch origin X`)
   and use `$(git merge-base origin/main X)..X`. Tell the developer the exact range you
   will quiz.
2. Run `grip ask --range BASE..HEAD` and read the JSON.
   - `"status": "nothing-to-quiz"`: say the range is empty and stop.
   - `"status": "already-passed"`: say this range already passed recently; offer
     `grip forget` if they want a fresh quiz anyway.
3. Show the `summary` and the five questions verbatim, numbered, each with its `focus`.
4. With `--questions-only`: stop here, formatted so the text can be pasted into the pull
   request as review questions for the author. Mention that the rubrics stay local in
   `.git/grip/pending.json` and that `grip grade` can score the author's replies later.
5. Otherwise the reviewer answers all five in one reply. Do not answer, hint or open the
   code for them. Grade:

   ```bash
   grip grade --answers - <<'EOF'
   ["answer 1", "answer 2", "answer 3", "answer 4", "answer 5"]
   EOF
   ```

6. Show the per-question scores and feedback, then the Grip Score. Below the pass mark,
   suggest which parts of the diff to read before approving, taken from the feedback.

## Notes

- The pass mark comes from the repository's `.grip.toml` (`passing_score`, default 70).
- A reviewer's pass is remembered for the range like any other quiz; it does not affect
  the author's own hook.
- To quiz the author's local, unpushed work instead, use `/grip --unpushed`.
