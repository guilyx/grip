---
description: What leaves your machine with each provider, how many model calls a quiz makes, and how to estimate what it costs.
---

# Cost and privacy

grip has no server of its own. Everything it sends goes to the provider you configure, and
everything it keeps stays in your repository. This page says what that means for each
provider and how to work out what a quiz costs you.

## What is sent

Every model call carries the same block: the scope line (`staged changes`, `main..HEAD`),
the `git diff --stat` summary, and the patch after `exclude` is applied and the patch is
cut at `max_diff_bytes`. The grading call also carries the five questions, their rubrics
and your answers. Nothing else is sent: no other files, no history, no telemetry.

## Where it goes, per provider

| Provider | Where the diff and your answers go | Under whose terms |
| --- | --- | --- |
| `claude-code` | Anthropic, through the `claude` CLI signed in on your machine | Your Claude account's terms |
| `codex` | OpenAI, through the `codex` CLI signed in on your machine | Your OpenAI account's terms |
| `gemini` | Google, through the `gemini` CLI signed in on your machine | Your Google account's terms |
| `anthropic` | Anthropic's API, or the gateway set in `base_url` | Your API agreement (or your gateway's) |
| `openai` | The server at `base_url` (OpenAI by default) | That server's operator |
| `ollama` | `http://localhost:11434/v1` by default: it stays on your machine | Yours |
| `fake` | Nowhere. It never makes a network call. | Not applicable |

With a coding agent as the provider, grip runs the CLI in an empty temporary directory
with tools disabled where the CLI allows it, so the agent sees the diff and your answers
and nothing else in the repository. The data still reaches the vendor exactly as a prompt
you typed into that CLI would, so what that vendor keeps, for how long, and whether it may
train on it is set by your plan with them, not by grip. Personal and business plans often
differ. Read your plan's data terms before you point grip at code your employer or a
client owns.

`auto`, the default, resolves to one of the rows above on each machine (`grip status`
says which). On a team where people have different agents installed, the same repository
can send diffs to different vendors. If that matters, set `provider` explicitly in the
committed `.grip.toml`.

`ollama` keeps everything local only while `base_url` points at your own machine or a
server you run. Pointed somewhere else, it is a remote provider like the others.

## Keeping secrets out

- Add globs to `exclude` for files that can hold secrets or customer data (fixtures,
  dumps, `.env` examples). Patterns without `/` match at any depth.
- `max_diff_bytes` caps how much of a large patch is sent; it does not remove anything
  sensitive from a small one.
- For code that must not leave the machine, use `ollama`.

## What stays on your machine

| Where | What |
| --- | --- |
| `.git/grip/last-report.json` | The last graded quiz: questions, rubrics, answers, grades |
| `.git/grip/history.jsonl` | One line per graded quiz, for `grip study export` |
| `.git/grip/passed.json` | Digests of diffs that passed: when, the score and pass mark, and which provider and model graded them |
| `.git/grip/cache/` | Questions and boards written for a diff, reused for `cache_hours` |
| `.git/grip/pending.json` | The questions from `grip ask`, until `grip grade` |
| `~/.local/share/grip/` | The list of quizzed repositories and an installation id, for `grip study` |

None of it is sent anywhere. `.git/` is never pushed, so these files do not reach your
remote either.

## How many model calls

| What you run | Calls |
| --- | --- |
| `grip quiz`, the hook, `grip ask` then `grip grade` | 2: write the questions, grade the answers |
| A retry on the same diff within `cache_hours` | 1: the questions come from the cache |
| `grip whiteboard` (defend) | 2: draw the board, grade the answers. 3 when the first board fails grip's checks and is asked for again |
| `grip whiteboard --export` or `--interview` | 1 to draw the board (2 when it is asked for again). Nothing is graded by the model |
| A diff that already passed within `remember_passes_hours` | 0 |

`--fresh`, `cache_hours = 0`, or `retry_questions = "new"` after a failed attempt, makes a
retry write new questions again: two calls.

## Estimating the cost

grip does not count tokens, and prices change, so this page has no dollar figures. Work
it out from your own diffs and your provider's current price list:

1. **Input tokens per call.** Roughly the size of the diff block in bytes divided by four
   (the usual rule of thumb for code and English; check with your provider's tokenizer
   for exact numbers), plus about a thousand tokens of instructions. `git diff --cached
   --stat` or `git diff main...HEAD | wc -c` gives the bytes. The diff is capped at
   `max_diff_bytes` (200,000 by default, about 50,000 tokens).
2. **Calls per quiz.** Two, from the table above. The diff is sent with both, so input
   is about twice the step 1 figure.
3. **Output tokens.** The questions with their rubrics, or the grades with their
   feedback, are each a few hundred to a couple of thousand tokens. A board is larger.
   Models that reason before answering also bill their reasoning; on the Anthropic
   provider, `effort` controls how much.
4. **Multiply** each by your model's input and output price per token.

Your provider's usage dashboard shows the real numbers after a few quizzes; trust it over
the estimate.

Ways to spend less:

- Use a coding agent you already pay for (`claude-code`, `codex`, `gemini`): no
  per-token bill, the calls count against that plan's usage limits instead.
- Use `ollama` for no bill at all, at the price of shallower questions on small models.
- Pick a smaller model with `model`, or lower `effort`, for the hook.
- Keep `cache_hours` on, so retries cost one call.
- Push in batches: the default pre-push hook quizzes once for all unpushed commits.
