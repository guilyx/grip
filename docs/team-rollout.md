---
description: Bring grip to a team: what to commit, what each person installs, how to pilot it and tune the pass mark.
---

# Rolling out to a team

grip is configured per repository and enforced per clone. The configuration is committed
and shared; the git hook is installed by each person on their own machine. That split is
what makes a gradual rollout easy.

## What `grip init` writes

| File | Committed? | Effect |
| --- | --- | --- |
| `.grip.toml` | yes | The shared bar: `passing_score`, `difficulty`, `provider`, and the rest of the [options](configuration.md) |
| `.git/hooks/pre-push` (or `core.hooksPath`) | no, `.git/` is never pushed | The hook itself, on this clone only |
| `AGENTS.md` | yes | A marked block telling Codex, OpenCode and other agents to run `grip check` before pushing |
| `CLAUDE.md` and `.claude/settings.json` | yes | The same rule for Claude Code, and the grip plugin enabled for everyone who opens the repository in Claude Code |
| `GEMINI.md`, `.cursor/rules/grip.mdc`, `.windsurf/rules/grip.md`, `.clinerules/grip.md`, `.github/copilot-instructions.md` | yes | The same rule for each of those agents, when it is installed on the machine that ran `grip init` |

`grip init --no-agents` writes only `.grip.toml` and the hook. `grip agents --list` shows
what would be written for whom without touching anything.

Two things to check before committing:

- **`provider`.** `grip init` writes `provider = "auto"` unless you pick one, so each
  person uses whichever coding agent they have (else `anthropic`). A fixed provider, such
  as `provider = "claude-code"`, gives everyone the same model and the same vendor, but
  fails for a teammate who only has Codex (see
  [Cost and privacy](cost-and-privacy.md)).
- **The agent files.** Once `CLAUDE.md`, `AGENTS.md` and `.claude/settings.json` are on
  the main branch, every teammate's coding agent follows the rule and runs `grip check`
  before it pushes, whether or not that person installed the hook. Commit them when the
  team has agreed, not during a pilot.

## Pilot with a few people

1. Commit `.grip.toml` only (`grip init --no-agents`, or leave the agent files out of the
   commit). It changes nothing for anyone who has not installed the hook.
2. Volunteers run `grip install` in their clone. Everyone else is unaffected.
3. Run it for a couple of weeks. Each person's `grip status` shows their last score, and
   `.git/grip/history.jsonl` has every graded quiz; `grip study export` turns it into one
   anonymised file per person if you want to compare notes.
4. Collect the questions people thought were unfair, the scores of answers that were
   honest but failed, and how often `grip skip` was used. Those three tell you more about
   the pass mark than any default.
5. When the team agrees, commit the agent files and ask everyone to run `grip install`
   (or add grip to `.pre-commit-config.yaml` if the team already uses the pre-commit
   framework; each person still runs `pre-commit install --hook-type pre-push`).

## Tune the pass mark

The default of 70 is a starting point, not a calibrated threshold (see
[Honest numbers](honest-numbers.md)). Questions are worth 20 points each, so 70 allows
one missed question and some partial credit.

- Start lower during a pilot (`passing_score = 50` or `60`) so the first weeks are about
  the questions, not about blocked pushes.
- Raise it once honest answers reliably clear it. Watch the scores of people who know the
  change well: if they sit just above the mark, the mark is right; if they fail, it is
  too high or the model is too strict.
- `difficulty` changes both the questions and how strictly they are graded. `hard` with a
  high pass mark is a demanding combination; change one at a time.
- A person can lower the bar for themselves with `GRIP_PASSING_SCORE` or
  `GRIP_DIFFICULTY` in their environment, for onboarding. That is by design: the shared
  file sets the team's bar, the environment is personal.

## CI

grip skips itself whenever `CI` is set, which every CI provider does, so a hook that
runs in a CI job never waits for answers that cannot come. Nothing to configure.

## Be honest about the limits

Tell the team what grip is before they meet it:

- **It is an honour system.** `GRIP_SKIP=1`, `grip skip` and `git push --no-verify` all
  get past it, and it should stay that way for emergencies. It is a forcing function for
  people who want one, not a control.
- **Every record is local.** Scores live in each person's `.git/grip/`. Nothing reaches a
  shared dashboard, and nobody else can verify that a push was quizzed.
- **The model can be wrong.** It sometimes asks about something the diff does not show,
  or grades a right answer down. Make it normal to skip and say so, rather than to fight
  the quiz.
- **The score is a signal, not a review.** Keep code review and tests. grip checks that
  the person shipping the change can explain it, not that the change is correct.
