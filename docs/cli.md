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

## `grip init`

Sets a repository up in one go: writes a commented `.grip.toml`, installs the pre-push hook
(chaining after an existing hook rather than failing) and runs `grip agents` for the
coding agents it detects. The provider defaults to `claude-code`, `codex` or `gemini` when
that CLI is installed, `anthropic` otherwise.

```bash
grip init                                   # detect, write, install
grip init --provider ollama --passing-score 80 --difficulty hard
grip init --stage pre-commit --stage pre-push
grip init --no-hook --no-agents             # only .grip.toml
grip init --force                           # overwrite an existing .grip.toml
```

## `grip agents`

Writes the file each installed coding agent reads so it runs the quiz before pushing:
`AGENTS.md`, `CLAUDE.md` and `.claude/settings.json`, `GEMINI.md`, `.cursor/rules/grip.mdc`,
`.windsurf/rules/grip.md`, `.clinerules/grip.md`, `.github/copilot-instructions.md`.
Detection looks for the agent's binary on `PATH` or its directory under your home.

```bash
grip agents --list                  # detected agents and what each would get
grip agents                         # write for the detected ones
grip agents --all                   # write for every known agent
grip agents --agent gemini --dry-run
grip agents --remove                # strip the blocks, leave the rest of each file
```

See [Coding agents](agents.md#one-command-for-every-agent).

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

Shows which stages have grip installed, whether a foreign hook is in the way, the last
quiz (score, pass or fail, how long ago), whether the staged and unpushed diffs have
already passed, whether hooks are paused, and the effective configuration.

## `grip skip` / `grip resume`

`GRIP_SKIP=1` is awkward from an IDE's push button. `grip skip` skips the next hook run
once; `grip skip --hours 2` pauses every hook run for two hours; `grip resume` cancels.
Only the hooks and the agent push gate look at it, a manual `grip quiz` never does.

```bash
grip skip && git push         # one push without the quiz
grip skip --hours 1           # a demo, a pairing session
grip resume
```

## `grip statusline`

One short line for an editor or Claude Code status line: `grip 92/100 pass`, with
`paused` appended while `grip skip` is in effect. Outside a repository or before any quiz
it prints nothing and exits 0. It reads an optional JSON payload on stdin and uses its
`cwd`, which is what Claude Code sends:

```json
{ "statusLine": { "type": "command", "command": "grip statusline" } }
```

`grip statusline --json` prints the score, pass mark, time and skip state for other tools.

## `grip config`

Prints the effective configuration after merging files, environment and defaults.

## `grip last`

Shows the last graded quiz in this repository: each question with its focus, your answer,
the points and the feedback, then the verdict. Rubrics are not shown. `--json` prints the
same payload as `grip grade` plus `summary`, `questions`, `answers` and `at`; the
`/grip-explain` skill builds a commit message from it.

## `grip forget`

Clears remembered passes so the next commit or push is quizzed again.

## `grip study export` / `grip study status`

Every graded quiz is appended to `.git/grip/history.jsonl` and the repository is noted in
`~/.local/share/grip/repos.json` (`$XDG_DATA_HOME/grip`, or `GRIP_DATA_HOME`). `export`
collects that history from every repository on the machine into one anonymised JSON file
you can upload to a study platform yourself; grip never uploads anything.

```bash
grip study status                 # how many repos and quizzes, and the exact field list
grip study export                 # last 45 days -> grip-export-<YYYYMMDD>.json
grip study export --since 0 --out ~/all-quizzes.json
```

The export carries, per quiz: an opaque id, an opaque per-repository hash, the time,
stage, provider, model, pass mark, score, pass/fail, and for each question its `focus`
label and points. It never contains the diff, file paths, repository names, question
text, rubrics, your answers, feedback or the verdict. `grip study status` prints both
lists so you can check before sharing.

## `grip login` / `grip solve SLUG` / `grip submit`

The [Keep A Grip](keepagrip.md) commands. `login TOKEN [--url URL]` verifies and stores an
API token from your dashboard (`logout` forgets it, `whoami` shows the account).
`problems [--category SLUG]` lists the catalogue. `solve SLUG [--dir PATH]` sets up a
repository with `PROBLEM.md` and the problem's pass mark, and remembers the base commit.
`submit [--no-tests] [--dry-run]` runs the problem's test command, quizzes you on everything
since that base commit (uncommitted work included) and reports the score; `--dry-run` prints
the payload instead of sending it.

```bash
grip login kag_…
grip solve cmd-vel-safety-filter
cd cmd-vel-safety-filter            # solve it, commit as you go
grip submit --provider claude
```
