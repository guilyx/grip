# FAQ

## Can I bypass it?

Yes. `GRIP_SKIP=1 git push` skips once, and git's own `--no-verify` skips every hook.
grip is a forcing function for honest developers, not a security control.

## Does it work with GUI git clients and IDEs?

If the client runs hooks without a terminal, grip cannot ask questions. By default it
prints a notice and lets the operation through; set `require_tty = true` to block instead.
Most IDE terminals work fine.

## Does it run in CI?

No. When `CI` is set grip exits immediately. CI should run your tests, not quiz a robot.

## Why do I keep getting the same questions?

You do not: each run is a fresh request. But a diff that *passed* is remembered for
`remember_passes_hours`, so an identical diff is not quizzed again. `grip forget` clears
that memory.

## Why did a failing provider block my commit?

Because a silently bypassed hook is worse than a loud one. Set `fail_open = true` if you
prefer availability over strictness.

## How much does it cost?

Two requests per quiz. Input is dominated by the diff (sent twice) plus a few hundred
tokens of prompt; output is a few hundred tokens. Use `exclude` and `max_diff_bytes` to
keep large diffs in check, or Ollama for zero cost.

## Is my code sent anywhere else?

Only to the provider you configured, and only the diff plus your answers. See
[How it works](how-it-works.md#what-leaves-your-machine) and `SECURITY.md`.

## Can questions be multiple choice?

Not currently. Free-text answers are a much better signal of understanding than
recognition, and grading against a rubric keeps it fair. Open an issue if you have a use
case.

## Why exactly five questions?

It is enough to cover behaviour, motivation, an edge case, a risk and verification, and
short enough that people answer rather than bypass. It is fixed on purpose so scores are
comparable across repositories.
