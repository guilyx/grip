---
description: Take grip out of a repository and off your machine, step by step.
---

# Removing grip

grip touches three places: the git hooks in each clone, files committed to the
repository, and the `grip` command itself. Remove them in this order, because the first
steps need the `grip` command.

## 1. The git hooks

In each clone where you installed them:

```bash
grip uninstall              # every stage grip is installed at
```

It only touches hooks grip wrote. When grip was appended to a hook you already had
(`grip install --append`), it removes grip's block and leaves the rest of the hook.

If you used the pre-commit framework instead, delete the `grip` entry from
`.pre-commit-config.yaml`.

## 2. The agent rule files

```bash
grip agents --remove --all --dry-run   # what would change
grip agents --remove --all
```

This takes grip's marked block out of `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` and
`.github/copilot-instructions.md`, deletes the rules files grip owns
(`.cursor/rules/grip.mdc`, `.windsurf/rules/grip.md`, `.clinerules/grip.md`), and removes
grip's keys from `.claude/settings.json`. Everything else in those files stays.
`--all` covers every agent grip knows, not only the ones installed on your machine, so
files a teammate's `grip init` wrote are removed too.

## 3. The configuration

```bash
git rm .grip.toml
```

If the configuration lives in `pyproject.toml`, delete the `[tool.grip]` table. Commit
steps 2 and 3 together so the team stops seeing grip in one change.

## 4. Local state (optional)

grip keeps its records inside `.git/grip/` in each clone and never pushes them. Delete
the directory if you do not want to keep your quiz history. The list of quizzed
repositories for `grip study` lives in `~/.local/share/grip/` (`$XDG_DATA_HOME/grip`, or
`GRIP_DATA_HOME`), along with a Keep A Grip token if you ran `grip platform login`. Run
`grip platform logout` first, then delete the directory.

## 5. The command

```bash
pipx uninstall grip-hook
# or: uv tool uninstall grip-hook
# or, in a virtualenv: pip uninstall grip-hook
```

## Skills and the Claude Code plugin

If you added the skills with `npx skills add guilyx/grip -g`, remove them with the same
`skills` CLI. For the Claude Code plugin, run `/plugin uninstall grip@grip` inside Claude
Code.
