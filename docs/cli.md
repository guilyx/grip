# Command line

`grip` and `git grip` are the same program.

## `grip quiz`

Run the quiz now, without a hook.

```bash
grip quiz                       # staged changes
grip quiz --unpushed            # commits not on any remote
grip quiz --range main..HEAD    # explicit range
grip quiz --passing-score 90 --difficulty hard
grip quiz --provider fake       # offline dry run
grip quiz --report out.json     # also write the report here
```

Exit codes: `0` passed or skipped, `1` failed the quiz, `2` error.

## `grip hook STAGE [ARGS...]`

The entry point installed hooks and pre-commit call. `STAGE` is `pre-commit` or
`pre-push`; extra arguments are whatever git passes to the hook. You rarely run this
yourself, but it is handy for debugging:

```bash
printf 'refs/heads/main %s refs/heads/main %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)" \
  | grip hook pre-push origin
```

Accepts the same `--passing-score`, `--provider`, `--model`, `--difficulty` and
`--report` flags as `grip quiz`.

## `grip ask` / `grip grade` / `grip check`

The quiz in three non-interactive steps, for coding agents. `ask` prints the questions as
JSON (rubrics stay in `.git/grip/pending.json`), `grade` scores a JSON list of answers and
remembers a pass, `check` exits 0 when the diff already passed. Same `--staged`,
`--unpushed` and `--range` selectors as `grip quiz`. See [Coding agents](agents.md).

```bash
grip ask --unpushed
grip grade --answers - <<'EOF'
["answer 1", "answer 2", "answer 3", "answer 4", "answer 5"]
EOF
grip check --unpushed && git push
```

## `grip agent-hook claude-code [--gate push|commit|both]`

Claude Code `PreToolUse` hook. Reads the hook payload from stdin and denies `git push`
(and `git commit` with `--gate both`) until the diff has passed a quiz. Installed by the
[plugin](agents.md#claude-code-plugin).

## `grip install` / `grip uninstall`

```bash
grip install                                   # pre-push
grip install --stage pre-commit --stage pre-push
grip install --append                          # chain after an existing hook
grip install --force                           # replace an existing hook
grip uninstall                                 # remove from every stage
```

Hooks go to `.git/hooks/` or to `core.hooksPath` when it is set. Files grip wrote start
with a marker line so `uninstall` never touches a hook it does not own.

## `grip status`

Shows which stages have grip installed, whether a foreign hook is in the way, and the
effective configuration.

## `grip config`

Prints the effective configuration after merging files, environment and defaults.

## `grip forget`

Clears remembered passes so the next commit or push is quizzed again.
