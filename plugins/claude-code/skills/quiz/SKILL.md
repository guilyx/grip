---
name: quiz
description: Quiz the developer on the current diff with grip. Five questions about the change, graded to a Grip Score out of 100. Run it before committing or pushing code the developer did not write line by line.
disable-model-invocation: true
allowed-tools: Bash(grip *)
---

# grip quiz

You are the messenger, not the student. grip writes the questions and grades the
answers; you relay them. The developer answers.

Arguments: `$ARGUMENTS` is empty (staged changes, the default), `--unpushed`
(every commit not yet on a remote) or `--range BASE..HEAD`.

## Steps

1. Run `grip ask $ARGUMENTS` and read the JSON.
   - `"status": "nothing-to-quiz"`: say there is nothing to quiz and stop.
   - `"status": "already-passed"`: say this diff already passed recently and stop.
   - If the command fails because `grip` is missing, tell the developer to install it:
     `curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh`
2. Show the `summary`, then the five questions, numbered 1 to 5, each with its `focus`
   in parentheses. Quote them verbatim. Ask the developer to answer all five in one
   reply, a sentence or two each, and say a blank answer scores zero.
3. Wait for the developer's reply. Do not answer for them, do not hint, do not open
   the diff or the code to help, and do not paraphrase their answers. If they ask you
   to answer, decline: the point is to check what they understand.
4. Grade with their answers in order, as a JSON list of five strings:

   ```bash
   grip grade --answers - <<'EOF'
   ["answer 1", "answer 2", "answer 3", "answer 4", "answer 5"]
   EOF
   ```

5. Show the result as a short table: question number, focus, score out of 20, feedback.
   Then the Grip Score against the pass mark and the verdict.
   - Exit code 0: they passed. The diff is remembered, so `git push` goes through.
   - Exit code 1: they failed. Suggest what to re-read from the feedback and offer to
     run `/grip:quiz` again for a fresh set of questions.

## Notes

- A passed diff is remembered for `remember_passes_hours` (24 by default). Changing
  the diff invalidates the pass.
- The full report, including the hidden rubrics, is in `.git/grip/last-report.json`.
- Configuration lives in `.grip.toml` at the repository root: `passing_score`,
  `difficulty`, `provider`, `model`. With `provider = "claude-code"` grip drives the
  `claude` CLI itself, so no API key is needed.
