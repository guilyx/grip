# Coding agents

When Claude Code, Codex or Gemini CLI writes the code, the question grip asks gets
sharper: do *you* understand what is about to be pushed? Inside an agent there is no
terminal for grip to talk to, so the agent relays the quiz instead. It shows you the
questions, you answer, grip grades.

Two ways in. The **skill** works in every agent that reads `SKILL.md` files. The
**Claude Code plugin** adds a `git push` gate on top.

## The skill, in any agent

grip ships as an [Agent Skill](https://agentskills.io): `skills/grip/SKILL.md` in this
repository. Install it into every agent on your machine with one command:

```bash
npx skills add guilyx/grip -g
```

That puts the skill where Claude Code, Codex, Gemini CLI, Cursor, Windsurf, Cline,
Copilot and the other agents the `skills` CLI knows about look for it. Then type `/grip`
(`$grip` in Codex) before you push, or say "quiz me on this diff". The skill tells the
agent to run `grip ask`, show you the five questions verbatim, wait for your answers, run
`grip grade`, and never answer for you.

You need the `grip` binary too:

```bash
curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh
```

## One command for every agent

Rule files are what agents without skill support read, and what keeps a skill-aware agent
honest when the skill is not loaded. `grip agents` writes them for you:

```bash
grip agents             # detect the agents on this machine, write their files
grip agents --list      # what would be written for whom, nothing touched
grip agents --all       # every known agent, detected or not
grip agents --agent codex --agent cursor
grip agents --remove    # take the blocks out again
```

Detection is local: a binary on `PATH` or a configuration directory under your home.
Everything is written inside the repository, so commit it and the whole team's agents
follow the same rule.

| Agent | File written |
| --- | --- |
| Codex CLI, OpenCode, Jules, Amp, Zed and anything else that reads it | `AGENTS.md` (always offered) |
| Claude Code | `CLAUDE.md` and the plugin enablement in `.claude/settings.json` |
| Gemini CLI | `GEMINI.md` |
| Cursor | `.cursor/rules/grip.mdc` |
| Windsurf | `.windsurf/rules/grip.md` |
| Cline | `.clinerules/grip.md` |
| GitHub Copilot | `.github/copilot-instructions.md` |

In Markdown files the developer owns, grip appends one block between
`<!-- grip:start -->` and `<!-- grip:end -->` and only ever touches what is between the
markers. The rules files for Cursor, Windsurf and Cline are grip's own. The Claude Code
settings file is merged key by key. Re-running refreshes the block text after an upgrade.

## Two more skills

Installed by the same `npx skills add guilyx/grip -g` and by the plugin.

**`/grip-review BASE..HEAD`** (or a branch name): the reviewer's side of the table. grip
writes five questions about the range; you answer them before approving, and the agent
grades you. With `--questions-only` it stops after printing the questions, formatted to
paste into the pull request for the author to answer.

**`/grip-explain`**: turns your answers from the last quiz into a commit message
(`--pr` for a pull request description, `--short` for the subject only). The agent edits
your words, adds nothing, and never commits on its own. It reads `grip last --json`.

## Claude Code plugin

The repository is also a Claude Code plugin marketplace. Install once:

```text
/plugin marketplace add guilyx/grip
/plugin install grip@grip
```

The plugin adds two things.

**`/grip`**: Claude runs `grip ask`, shows you the five questions, waits for your
answers, then runs `grip grade` and reports the score. The skill tells Claude not to
answer, hint or open the code for you. `/grip --unpushed` quizzes every commit not on a
remote, `/grip --range BASE..HEAD` an explicit range.

**A `git push` gate**: a `PreToolUse` hook runs `grip agent-hook claude-code` before
every Bash command. When the command is a `git push` and the unpushed diff has not passed
a quiz, the push is denied and Claude is told to ask you to run `/grip --unpushed`.
Once you pass, the diff is remembered and the push goes through. `GRIP_SKIP=1` bypasses
the gate, as does `CI=true`.

A status line badge shows the last Grip Score next to Claude's own status:

```json
{ "statusLine": { "type": "command", "command": "grip statusline" } }
```

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
  "enabledPlugins": { "grip@grip": true }
}
```

!!! tip "Which provider?"
    With `provider = "claude-code"` in `.grip.toml`, grip writes and grades the questions
    through the same `claude` CLI, so nothing else needs an API key. Any other provider
    works too.

## Any other agent

The skill is a thin wrapper over three commands that any agent can call:

| Command | What it does |
| --- | --- |
| `grip ask [--unpushed \| --range A..B]` | Prints the summary and five questions as JSON. Rubrics stay in `.git/grip/pending.json`. |
| `grip grade --answers FILE` | Grades a JSON list of answers, prints the scores, remembers a pass. Exit 1 on fail. |
| `grip check [--unpushed]` | Exit 0 when the diff already passed (or is empty), 1 otherwise. |

For an agent without skill support, paste this into `AGENTS.md`, `CLAUDE.md` or
`GEMINI.md`:

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
