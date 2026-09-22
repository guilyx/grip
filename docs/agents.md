# Coding agents

When Claude Code, Codex or Gemini CLI writes the code, the question grip asks gets
sharper: do *you* understand what is about to be pushed? Inside an agent there is no
terminal for grip to talk to, so the agent relays the quiz instead. It shows you the
questions, you answer, grip grades.

## Claude Code plugin

The repository is a Claude Code plugin marketplace. Install once:

```text
/plugin marketplace add guilyx/grip
/plugin install grip@grip
```

You need the `grip` binary too (`curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh`).

The plugin adds two things.

**`/grip:quiz`**: Claude runs `grip ask`, shows you the five questions, waits for your
answers, then runs `grip grade` and reports the score. The skill tells Claude not to
answer, hint or open the code for you. `/grip:quiz --unpushed` quizzes every commit not
on a remote, `/grip:quiz --range BASE..HEAD` an explicit range.

**A `git push` gate**: a `PreToolUse` hook runs `grip agent-hook claude-code` before
every Bash command. When the command is a `git push` and the unpushed diff has not passed
a quiz, the push is denied and Claude is told to ask you to run `/grip:quiz --unpushed`.
Once you pass, the diff is remembered and the push goes through. `GRIP_SKIP=1` bypasses
the gate, as does `CI=true`.

To gate commits as well, point the hook at `--gate both` in your own settings:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "grip agent-hook claude-code --gate both" }]
      }
    ]
  }
}
```

To turn the plugin on for a whole team, add it to the repository's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "grip": { "source": { "source": "github", "repo": "guilyx/grip" } }
  },
  "enabledPlugins": { "grip@grip": {} }
}
```

!!! tip "Which provider?"
    With `provider = "claude-code"` in `.grip.toml`, grip writes and grades the questions
    through the same `claude` CLI, so nothing else needs an API key. Any other provider
    works too.

## Any other agent

The plugin is a thin wrapper over three commands that any agent can call:

| Command | What it does |
| --- | --- |
| `grip ask [--unpushed \| --range A..B]` | Prints the summary and five questions as JSON. Rubrics stay in `.git/grip/pending.json`. |
| `grip grade --answers FILE` | Grades a JSON list of answers, prints the scores, remembers a pass. Exit 1 on fail. |
| `grip check [--unpushed]` | Exit 0 when the diff already passed (or is empty), 1 otherwise. |

For Codex or Gemini CLI, paste this into `AGENTS.md` or `GEMINI.md`:

```markdown
## grip

Before `git push`, run `grip check --unpushed`. If it exits 1, run `grip ask --unpushed`,
show me the questions verbatim, wait for my answers, then run
`grip grade --answers -` with my answers as a JSON list of five strings. Never answer
the questions yourself. Push only after `grip check --unpushed` exits 0.
```

The JSON `grip ask` prints:

```json
{
  "status": "questions",
  "summary": "Adds an optional clamp to add().",
  "passing_score": 70,
  "max_score": 100,
  "diff": { "description": "staged changes", "files": ["a.py"], "truncated": false },
  "questions": [
    { "index": 1, "focus": "behaviour", "question": "What does add(3, 4, clamp=5) return, and why?" }
  ],
  "next": "Show these to the developer, collect their answers, then run `grip grade`."
}
```

`status` is `nothing-to-quiz` when the diff is empty and `already-passed` when it was
quizzed recently. `grip grade` prints `status` (`pass` or `fail`), `score`, per-question
`grades` with `feedback`, the `verdict`, and the path of the full report.

## Honour system

The agent could answer for you. The skill and the snippet above tell it not to, and the
rubrics never reach it, but grip cannot stop a determined cheat any more than a terminal
hook can stop `--no-verify`. It is a forcing function for people who want one.
