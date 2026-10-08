# Honest numbers

grip claims to tell a developer who understands a change from one who does not. This page
says what has been measured about that claim, what has not, and where grip gets in the
way. No rounding up. If you measure something different, [open an issue](https://github.com/guilyx/grip/issues)
and it goes here.

## What is measured

The [evals harness](https://github.com/guilyx/grip/tree/main/evals) runs the quiz on
eight fixed diffs (Python, ROS 2, JavaScript, Go, SQL, YAML, Markdown, a pure rename) with
three sets of answers to the same five generated questions:

| Arm | Answers | Should |
| --- | --- | --- |
| blank | five empty strings | fail |
| generic | plausible developer answers written without reading any diff | fail, or the quiz is gameable |
| notes | the author's notes on the change, matched to each question by focus | pass |

Each arm is graded several times so grading noise shows up as a spread.

### Offline stand-in: the `fake` provider

Committed as `evals/snapshots/fake.json` and checked by the test suite.

| case | blank | generic | notes |
|---|---:|---:|---:|
| every case (8) | 0, fail | 100, pass | 100, pass |

The fake provider grades by answer length. It exists so the flow can be demoed and tested
offline, and these numbers show exactly why it is not a grader: **generic answers pass
it.** Treat it as a test of the harness, not of grip.

### Real models

**No real-model snapshot is committed yet.** The harness is ready; it needs someone with a
key to run it and commit the file:

```bash
python evals/run.py --provider anthropic --repeats 3
python evals/run.py --provider claude-code --repeats 3
python evals/run.py --provider ollama --model qwen3:14b --repeats 3
```

Until a snapshot is here, the claim that the quiz separates careful answers from generic
ones is a design intent backed by the rubric prompt, not a measurement. The pass mark of
70 is a default, not a calibrated threshold.

## What is not measured

- **Whether passing the quiz predicts anything.** Fewer review comments, fewer reverts,
  better recall a week later: none of it has been studied. The quiz measures whether you
  can explain the diff right now.
- **Question quality across many diffs.** Eight fixtures are enough to catch regressions,
  not to characterise a model. Real diffs are longer, noisier and in more languages.
- **Live developers.** The `notes` arm reuses text written before the questions existed,
  which understates a real person answering live, so the separation it will show is a
  floor.
- **Cost.** A quiz is two model calls over the diff. Tokens are not counted by the harness;
  with `provider = "claude-code"` the calls ride an existing subscription.
- **Gaming by the agent.** Nothing stops a coding agent from answering for you if you tell
  it to. The skill forbids it and the rubrics never reach it; that is the extent of it.

## Where grip gets in the way

Reports from use, not from the harness.

- **Tiny changes.** A one-line fix gets five questions like any other diff. Use
  `grip skip` for one push, or raise `max_diff_bytes` and `exclude` so generated files do
  not count.
- **Renames and moves.** grip sees a large diff and asks about behaviour that did not
  change. The `rename-only` fixture is there to keep this visible.
- **Documentation and configuration diffs.** Questions tend toward "why" and "how would
  you check", which is fine for a config change and tedious for a typo.
- **Unfair questions.** The model sometimes asks about something the diff does not show,
  or grades a correct answer down. The per-question feedback says what it wanted; `grip
  quiz` gives a fresh set; `fail_open` and the pass mark are yours to set.
- **Latency.** Two model calls before every push. With a fast model it is a few seconds;
  with a large local model it can be a minute.
- **No terminal.** Inside an IDE push button there is no tty, so grip skips unless
  `require_tty` is set. The coding-agent flow (`grip ask` / `grip grade`) exists for that.

## Measure it yourself

1. `grip quiz --provider fake` to see the flow with no model.
2. Run the harness with your provider and read the per-question scores in the snapshot,
   not just the totals.
3. Keep `.git/grip/history.jsonl`: every graded quiz is appended there, and
   `grip study export` turns it into an anonymised summary you can compare over time.
