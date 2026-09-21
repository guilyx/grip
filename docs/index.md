# grip

**Keep a grip on your code.** grip is a git hook that quizzes you about your own diff
before you commit or push it. Five questions, a Grip Score out of 100, and a pass mark
you choose. Below the mark, nothing goes upstream.

```text
Q1/5 (behaviour) What does refund() now do when the charge was already refunded?
> It returns the existing refund instead of raising, so retries are idempotent.
```

## Why grip exists

Generated code, big refactors and late-night pastes make it easy to ship a change you
could not explain in a code review. grip makes you explain it *before* it leaves your
machine. It is a forcing function for understanding, not a correctness gate: the model
asks about behaviour, motivation, edge cases, risks and verification, and grades your
answers against a rubric it derived from the diff.

## At a glance

- **Always five questions**, graded 0 to 20 each, summed to a Grip Score out of 100.
- **A pass mark you set**: `passing_score = 70` by default.
- **Two install paths**: a native git hook (`grip install`) or the
  [pre-commit](https://pre-commit.com) framework.
- **Bring your model**: Anthropic by default, any OpenAI-compatible API, or Ollama for a
  fully local setup.
- **No double quizzing**: a diff that passed is remembered for 24 hours.
- **Escape hatch**: `GRIP_SKIP=1` or git's `--no-verify` when you really need it.

## Next steps

- [Getting started](getting-started.md): install in two minutes.
- [How it works](how-it-works.md): what is sent, how the score is built.
- [Configuration](configuration.md): every option, with defaults.
