# grip

**Keep a grip on your code.** grip is a git hook that quizzes you about your own diff
before you commit or push it. Five questions, a Grip Score out of 100, and a pass mark you
choose. Below the mark, nothing goes upstream.

[![CI](https://github.com/guilyx/grip/actions/workflows/ci.yml/badge.svg)](https://github.com/guilyx/grip/actions/workflows/ci.yml)
[![Docs](https://github.com/guilyx/grip/actions/workflows/docs.yml/badge.svg)](https://guilyx.github.io/grip/)
[![PyPI](https://img.shields.io/pypi/v/grip-hook)](https://pypi.org/project/grip-hook/)
[![Python](https://img.shields.io/pypi/pyversions/grip-hook)](https://pypi.org/project/grip-hook/)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](LICENSE)

```text
╭─────────────────────────────── grip ───────────────────────────────╮
│ 5 questions about your staged changes (pre-commit). Pass mark: 70/100 │
│                                                                        │
│ src/billing/refund.py                                                  │
│ tests/test_refund.py                                                   │
╰────────────────────── keep a grip on your code ──────────────────────╯

Q1/5 (behaviour) What does refund() now do when the charge was already refunded?
> It returns the existing refund instead of raising, so retries are idempotent.

Q2/5 (edge case) Why is the currency check done before the amount comparison?
> ...

┏━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Focus     ┃ Score ┃ Feedback                                           ┃
┡━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ behaviour │ 20/20 │ Correct, and you named the idempotency motivation. │
│ 2 │ edge case │ 12/20 │ Right order, but missed that mixed currencies ...  │
...
╭────────────────────────────────────────────╮
│ Grip Score: 84/100  PASS (pass mark 70)    │
╰────────────────────────────────────────────╯
```

## Why

Generated code, big refactors, late-night pastes: it is easy to ship a change you could
not explain in a code review. grip makes you explain it *before* it leaves your machine.
It is a lightweight forcing function, not a gate for correctness: the model asks about
behaviour, motivation, edge cases, risks and verification, and grades your answers against
a rubric it wrote from the diff.

## Install

grip is a Python CLI. Install it once, globally:

```bash
pipx install grip-hook        # or: uv tool install grip-hook
```

Then pick one of two ways to wire it into git.

### Option A: native git hook

```bash
cd your-repo
grip install                  # pre-push (recommended)
grip install --stage pre-commit --stage pre-push   # both
```

### Option B: pre-commit framework

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/guilyx/grip
    rev: v0.1.0
    hooks:
      - id: grip          # runs at pre-push
      # - id: grip-commit # runs at pre-commit
```

```bash
pre-commit install --hook-type pre-push
```

### Provider

By default grip talks to Anthropic. Export `ANTHROPIC_API_KEY` (or run `ant auth login`).
For a local, free setup use Ollama:

```toml
# .grip.toml
provider = "ollama"
model = "qwen3:14b"
```

Any OpenAI-compatible API works with `provider = "openai"` plus `base_url` and
`OPENAI_API_KEY`.

## Use

Nothing to do: commit or push as usual. When grip runs, answer five questions in the
terminal. Pass the mark and the commit or push continues; fail it and it is blocked with
per-question feedback so you know what to go read.

```bash
grip quiz                     # quiz your staged changes right now, no hook needed
grip quiz --unpushed          # quiz everything not yet on a remote
grip quiz --provider fake     # try the flow offline with a dummy grader
grip status                   # installed hooks and effective configuration
GRIP_SKIP=1 git push          # bypass once (git's --no-verify also works)
```

A diff that passed is remembered for 24 hours, so a pre-push right after a pre-commit
does not ask again.

## Configure

`.grip.toml` at the repository root, or `[tool.grip]` in `pyproject.toml`. Environment
variables (`GRIP_PASSING_SCORE=80`) and flags (`--passing-score 80`) override files.

```toml
passing_score = 70            # 0-100, the Grip Score you need
difficulty = "normal"         # easy | normal | hard
provider = "anthropic"        # anthropic | openai | ollama | fake
model = "claude-opus-5"
effort = "medium"             # low | medium | high | xhigh | max | none
exclude = ["*.lock", "*.snap"]
max_diff_bytes = 200000
remember_passes_hours = 24
require_tty = false           # fail instead of skipping in non-interactive runs
fail_open = false             # let commits through when the provider is down
```

Full reference: [guilyx.github.io/grip](https://guilyx.github.io/grip/).

## How the score works

- The model reads the diff and writes exactly five questions, each with a hidden rubric.
- You answer each in a sentence or two.
- The model grades all five against the rubrics: 0 to 20 points each, summed to a
  Grip Score out of 100.
- Score at or above `passing_score` lets the commit or push through.

Only the diff (after `exclude`) and your answers are sent to the provider. Reports are
saved to `.git/grip/last-report.json`.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup, and [SECURITY.md](SECURITY.md) for what data grip touches.

## License

[BSD-3-Clause](LICENSE). Copyright (c) 2026, Erwin L.
