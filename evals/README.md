# Evals

Does the quiz tell apart a developer who understands a change from one who does not? This
directory measures that on fixed diffs instead of asserting it.

```bash
python evals/run.py --provider fake                   # offline, deterministic, run by the tests
python evals/run.py --provider anthropic --repeats 3  # a real model, needs ANTHROPIC_API_KEY
python evals/run.py --provider claude-code            # through a signed-in Claude Code CLI
python evals/run.py --provider ollama --model qwen3:14b
```

## Cases

Each directory under `cases/` is one change: `before/` and `after/` trees the harness turns
into a unified diff, and `case.json` with the author's notes on the change under five
keys: `what`, `why`, `edge`, `risk`, `test`. Two cases are deliberately the kind of change
where grip is annoying: a docs-only edit and a pure rename.

| Case | Language | What it is |
| --- | --- | --- |
| `clamp-add` | Python | an optional clamp on `add()`, three tests |
| `cmd-vel-watchdog` | Python, ROS 2 | the demo diff: a scan watchdog in a cmd_vel safety filter |
| `debounce-leading` | JavaScript | `debounce()` gains a leading edge and a `cancel()` |
| `retry-backoff` | Go | HTTP retries with jitter and context cancellation |
| `migration-index` | SQL | a partial index, a backfill and a `NOT NULL` |
| `ci-cache` | YAML | cache key on the lockfile, Windows in the matrix |
| `docs-typos` | Markdown | wording fixes and a corrected default |
| `rename-only` | Python | `fetch_user` becomes `load_user`, alias kept |

## Arms

Questions are generated once per case; every arm answers the same five.

| Arm | Answers | Expected |
| --- | --- | --- |
| `blank` | five empty strings | fail every time |
| `generic` | five plausible answers written without reading any diff | fail, or the quiz can be gamed |
| `notes` | the author's note that matches each question's focus | pass |

The `notes` arm understates a live developer: the notes were written before the questions
existed and are matched to them by keywords in the focus label. When no note fits, all
five notes are sent as one answer. So the separation it shows is a floor, not a ceiling.

## What the snapshot holds

`snapshots/<provider>.json`: provider, model, difficulty, pass mark, the generated
questions with the note each was matched to, every arm's answers and scores for each
grading repeat, min/max/mean/stdev per arm, pass rates, the notes-minus-generic
separation, and how the questions spread over the five focus areas. Timings are kept
separately so the test suite can compare everything else byte for byte.

`fake.json` is committed and `tests/test_evals.py` fails when it is stale. Real-model
snapshots are committed when someone runs them; the file names the model and the date.

## Adding a case

1. `mkdir -p evals/cases/<name>/{before,after}` and put the files in.
2. Write `case.json`: `title`, `description` (how grip would describe the diff, for example
   `staged changes`), `tags`, and the five `notes`. Write the notes as the person who made
   the change, before looking at any questions.
3. `python evals/run.py --provider fake --repeats 2` to refresh the committed snapshot.
