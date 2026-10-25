# FAQ

## Do I need Python installed?

Python 3.11 or newer. grip is a Python package (`grip-hook` on PyPI). `uv tool install
grip-hook` fetches a suitable Python by itself if you do not have one, and the pre-commit
framework route manages its own environment.

## How do I upgrade or uninstall?

`pipx upgrade grip-hook`, or `uv tool upgrade grip-hook`. Uninstalling the package leaves
the git hooks, `.grip.toml` and the agent rule files behind; [Removing grip](removing.md)
takes each of them out in order.

## Can I bypass it?

Yes. `GRIP_SKIP=1 git push` skips once, and git's own `--no-verify` skips every hook.
grip is a forcing function for honest developers, not a security control.

## Does it work with GUI git clients and IDEs?

If the client runs hooks without a terminal, grip cannot ask questions. By default it
prints a notice and lets the operation through; set `require_tty = true` to block instead.
Most IDE terminals work fine.

GUI clients often run hooks with a shorter `PATH` than your shell. The hook does not
depend on it: it runs grip by the absolute path recorded at `grip install`. If you later
move or reinstall grip, run `grip install` again; `grip status` shows `grip missing` until
you do.

## Does it run in CI?

The hooks do not: when `CI` is set they exit immediately, because nobody is there to
answer. The [GitHub Action](github-action.md) does something else in CI: it posts the
five questions on the pull request for the author or a reviewer to answer in review, and
grades nothing.

## Why do I keep getting the same questions?

Because the diff is the same. The questions written for a diff are reused for
`cache_hours` (24 by default) when that exact diff is quizzed again, so a retry after a
failed quiz is graded against the same five questions and costs one model call instead of
two. The feedback says where to look again, not what the answer was. Change the diff, set
`retry_questions = "new"` for new questions after a failed attempt, pass `--fresh` for one
run, or set `cache_hours = 0` to get new questions every time.

A diff that *passed* is remembered for `remember_passes_hours`, so an identical diff is
not quizzed again at all. `grip forget` clears that memory.

## Why did a failing provider block my commit?

Because a silently bypassed hook is worse than a loud one. Set `fail_open = true` if you
prefer availability over strictness.

## How much does it cost, and how long does it take?

Two requests per quiz: one writes the questions (or draws the whiteboard), one grades the
answers. A retry on the same diff reuses the questions, so it costs only the grading.
Input is dominated by the diff, sent with each request, plus a few hundred tokens of
prompt; output is a few hundred tokens.

grip measures it instead of guessing. After every graded quiz it prints one line:

```text
took 9.8 s · 3,140 tokens
```

`grip last` repeats it with the time of each request, and `grip status` shows the average
over the last 20 quizzes. The Anthropic and OpenAI-compatible APIs report token counts;
the coding-agent CLIs (`claude-code`, `codex`, `gemini`) do not, so for them grip shows
the time only. `grip last --json` lists every request with its `input_tokens` and
`output_tokens`.

To turn tokens into money, use the price your provider publishes for the model, per
million tokens, for input and output separately:

```text
cost ≈ input_tokens × input price / 1,000,000 + output_tokens × output price / 1,000,000
```

grip carries no price list: prices change, and a gateway or a plan may charge differently.
With a coding agent the quiz counts against that agent's plan, and Ollama costs nothing.
Use `exclude` and `max_diff_bytes` to keep large diffs in check, and see
[Cost and privacy](cost-and-privacy.md) to estimate the tokens for your own diffs. A
`grip appeal` is one more request, recorded with the quiz it regrades.

## Is my code sent anywhere else?

Only to the provider you configured, and only the diff plus your answers. With a coding
agent as the provider, that is the agent's vendor, under the terms of your account with
them. See [Cost and privacy](cost-and-privacy.md),
[How it works](how-it-works.md#what-leaves-your-machine) and `SECURITY.md`.

grip also keeps a local log of every graded quiz in `.git/grip/history.jsonl` and a list
of quizzed repositories under `~/.local/share/grip/`, so that `grip study export` can
build one anonymised file of your scores across repositories, for a study platform. That
file is only written when you run the command, contains no code, paths, question text,
answers or feedback, and goes nowhere unless you upload it. See
[`grip study`](cli.md#grip-study-export-grip-study-status).

## Can questions be multiple choice?

Not currently. Free-text answers are a much better signal of understanding than
recognition, and grading against a rubric keeps it fair. Open an issue if you have a use
case.

## Why exactly five questions?

It is enough to cover behaviour, motivation, an edge case, a risk and verification, and
short enough that people answer rather than bypass. It is fixed on purpose so scores are
comparable across repositories.

## How do I cite grip?

GitHub's "Cite this repository" button reads
[CITATION.cff](https://github.com/guilyx/grip/blob/main/CITATION.cff). In BibTeX:

```bibtex
@software{lejeune2026grip,
  author = {L., Erwin},
  title  = {grip: keep a grip on your code},
  year   = {2026},
  url    = {https://github.com/guilyx/grip}
}
```
