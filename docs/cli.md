# Command line

`grip` and `git grip` are the same program.

`grip --help` lists the everyday commands first (`init`, `quiz`, `whiteboard`, `status`,
`skip`, `resume`, `last`), then setup and upkeep, then `study` and `platform`, with a few
examples. The commands git hooks, editors and coding agents call are left out of that list
but work as always: see [For tools and agents](#for-tools-and-agents).

## `grip quiz`

Run the quiz now, without a hook.

```bash
grip quiz                       # staged changes
grip quiz --unpushed            # commits not on any remote
grip quiz --branch              # the branch since it left main: its pull request
grip quiz --range main..HEAD    # explicit range
grip quiz --range main...HEAD   # HEAD since it forked from main (merge base), like a PR
grip quiz --passing-score 90 --difficulty hard
grip quiz --provider fake       # offline dry run
grip quiz --report out.json     # also write the report here
grip quiz --fresh               # new questions, even for a diff quizzed recently
grip quiz --force               # quiz a rename, whitespace or skip_paths diff anyway
```

`--staged`, `--unpushed`, `--branch` and `--range` pick one scope; giving two of them is a
usage error (exit 2). The same goes for `grip ask`, `grip check` and `grip whiteboard`.

A diff with nothing to explain (only renames or mode changes, only whitespace, only
[`skip_paths`](configuration.md#diffs-with-nothing-to-explain), or fewer lines than
`min_changed_lines`) is not quizzed: grip says why and exits 0. `--force` quizzes it. It
does not override a [`[[paths]]`](configuration.md#per-path-rules) rule with `skip = true`.

Trying again on the same diff reuses the questions from the last attempt (see
[`cache_hours`](configuration.md)), so only the grading calls the model. `--fresh` asks for
new ones. `grip whiteboard` reuses its board the same way. With
[`retry_questions = "new"`](configuration.md), a failed attempt forgets them, so the next
try gets new ones.

`--branch` diffs from the merge base with the default branch: what `origin/HEAD` points
at, else `origin/main` or `origin/master`, else a local `main` or `master`. It is the
"quiz my PR" command, whatever has or has not been pushed yet.

`--range` takes git's two spellings. `BASE..HEAD` compares the two tips, so whatever `BASE`
gained after `HEAD` forked from it shows up too, as if reverted. `BASE...HEAD` diffs from
their merge base, so only `HEAD`'s side counts, as in a pull request. A missing `HEAD`
(`main..`, `main...`) means `HEAD`.

When the diff is empty, grip says what there is instead and what to run, and exits 0:

```text
grip: nothing to quiz: nothing staged. 3 untracked files and 1 modified file: `git add`
them first; 2 unpushed commits: try `grip quiz --unpushed` or `--branch`.
```

Answer each question on one line and press Enter. To answer in paragraphs, paste them: a
pasted answer keeps its line breaks and ends at the next empty line, or at the empty line
the paste itself ends with. (On Windows each pasted line still answers the next
question.)

A change where every file is binary (images, archives) has nothing to ask about: the quiz
and the hooks skip it with `grip: only binary files changed, nothing to quiz.`

Exit codes: `0` passed or skipped, `1` failed the quiz, `2` error, `130` stopped with
Ctrl-C (nothing is recorded).

`grip quiz` needs an interactive terminal. Without one (stdin redirected, a script, an IDE
task) it exits `2` rather than skipping, and points to `grip ask` / `grip grade` (JSON in,
JSON out) or `grip whiteboard`. The hooks still skip in that case unless `require_tty` is
set.

## `grip whiteboard`

Draw the change as a whiteboard and defend it in the browser. The parts of the change, how
they connect and five questions pinned to them come from your diff. Answers are graded like
`grip quiz`, and a pass counts for the push hook. See [the whiteboard](whiteboard.md).

```bash
grip whiteboard                         # staged changes
grip whiteboard --unpushed              # commits not on any remote
grip whiteboard --branch                # the whole branch since main
grip whiteboard --range main..HEAD      # explicit range
grip whiteboard --range main...HEAD     # HEAD since it forked from main (merge base)
grip whiteboard --interview             # rubrics visible to the person asking; nothing recorded
grip whiteboard --export board.html     # standalone page, no rubrics, nothing graded
grip whiteboard --no-open --port 8765   # print the address instead of opening a browser
```

It takes the same `--provider`, `--model`, `--difficulty`, `--passing-score` and `--report`
options as `grip quiz`. The page is served on `127.0.0.1` behind a random token and waits
while it is open in a browser, and up to twenty minutes after it is closed. Exit codes: `0` defended, skipped or exported, `1` below the pass
mark, `2` error (including Ctrl-C or the idle timeout, which record nothing).

## `grip init`

Sets a repository up in one go: writes a commented `.grip.toml`, installs the pre-push hook
(chaining after an existing hook rather than failing) and runs `grip agents` for the
coding agents it detects.

In a terminal it asks four questions: which model writes and grades the questions, the
pass mark, the difficulty (`easy`, `normal` or `hard`), and whether to quiz before
pushes, commits or both. Press Enter to keep a default. A flag answers its question up
front, `--yes` takes every default without asking, and outside a terminal (scripts, CI)
nothing is asked.

The provider defaults to `auto`, and `.grip.toml` is meant to be committed, so each
teammate gets the coding agent they have installed (Claude Code, Codex or Gemini CLI),
else `anthropic`. `grip init` prints what `auto` resolves to on your machine. It writes a
concrete provider only when you pick one in the menu or pass `--provider`. It then says
what is still missing for that provider, such as `ANTHROPIC_API_KEY`.

```bash
grip init                                   # ask, write, install
grip init --yes                             # every default, no questions
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
with a marker line so `uninstall` never touches a hook it does not own. The hook runs the
grip that installed it by absolute path, so it works from IDEs and GUI clients whose `PATH`
lacks grip, and falls back to `PATH` when that grip is gone. After moving or reinstalling
grip, run `grip install` again to update the path.

## `grip status`

Shows which stages have grip installed (hook paths relative to the repository root, such
as `.git/hooks/pre-push`; `grip missing` when the grip a hook was installed with no
longer exists), whether a foreign hook is in the way, the last quiz (score, pass or fail,
how long ago), the average time and tokens per quiz over the last 20, whether hooks are
paused, a short configuration summary (provider, pass mark, difficulty, hook mode;
`grip config` has the rest), and for each of the staged
changes, the unpushed commits and the branch against the default branch, where it stands:

```text
time and tokens (last 20)  9.8 s and 3,140 tokens per quiz, over 12 quizzes
staged changes             nothing
unpushed commits           nothing
branch vs origin/main      not quizzed (4 files)
```

`passed 98/100 at 17:52, remembered`, `failed 40/100 at 17:40` and `not quizzed` read as
they say. A branch whose commits are all on the remote can still be unquizzed:
`grip quiz --branch` covers it.

## `grip skip` / `grip resume`

`GRIP_SKIP=1` is awkward from an IDE's push button. `grip skip` skips the next hook run
once; `grip skip --hours 2` pauses every hook run for two hours; `grip resume` cancels.
Only the hooks and the agent push gate look at it, a manual `grip quiz` never does. The end
of a pause is shown in local time with the time zone, such as `paused until 2026-10-25
18:40 CET`.

```bash
grip skip && git push         # one push without the quiz
grip skip --hours 1           # a demo, a pairing session
grip resume
```

## `grip config`

Prints every option with its effective value and where the value came from: `default`,
`pyproject.toml`, `.grip.toml`, an environment variable (`env GRIP_TIMEOUT`) or a flag
(`flag --difficulty`). Below the table it lists the configuration files it read, lowest
precedence first. See [Configuration](configuration.md) for the order.

```bash
grip config                          # what a hook run here would use
grip config --provider fake          # the same, as `grip quiz --provider fake` would see it
```

It takes `--provider`, `--model`, `--passing-score` and `--difficulty`, the flags most often
passed to `grip quiz`.

## `grip last`

Shows the last graded quiz in this repository: how long it took and how many tokens it
used (`took 9.8 s · 3,140 tokens (questions 6.1 s, grading 3.7 s)`), each question with its
focus, your answer, the points and the feedback, then the verdict. Rubrics are not shown.
`--json` prints the same payload as `grip grade` (which includes `seconds` and `tokens`)
plus `summary`, `questions`, `answers`, `calls` (each model request with its time and
token counts, an appeal's included), `appeals` and `at`; the `/grip-explain` skill builds a
commit message from it. Token counts are `null` for providers that do not report them,
such as the coding-agent CLIs. See
[the FAQ](faq.md#how-much-does-it-cost-and-how-long-does-it-take) to turn them into a
cost.

## `grip appeal`

A grade can be wrong: the model misread the code, or the rubric missed a correct answer.
`grip appeal` has one question of the last graded quiz graded again, with your case for it.

```bash
grip appeal 3 "the loop is bounded by max_attempts, so it cannot spin forever"
```

The number is the question's number in `grip last`. The provider and model that graded the
quiz grade your original answer again, with the diff, the rubric, the first grade and your
justification; the justification goes to the model as data, like your answers.

- What only the justification says earns nothing: it can explain what your answer meant,
  or show that the rubric misread the code, but you have seen the feedback by now.
- An appeal never lowers a grade.
- Each question can be appealed once per quiz. A new quiz on the same diff can be appealed
  again.
- When the new score reaches the quiz's pass mark, the pass is remembered exactly as a
  passed quiz is, and the push hook lets the diff through. Appealing a quiz that already
  passed updates its score but does not restart the time its pass is remembered.

```text
grip: appeal upheld. Q3 (edge case): 6 -> 18/20.
  The answer names the bound; the first grade missed that max_attempts caps the loop.
Grip Score 58 -> 70/100 PASS (pass mark 70)
grip: passed on appeal; the commit or push can go through.
```

The appeal (question, old and new score, justification and feedback) is saved in
`.git/grip/last-report.json` and appended to the history, and `grip last` shows it under
the question. Exit codes: `0` the quiz now passes, `1` it still fails, `2` error (no graded
quiz, a question appealed already, an empty justification). Appeals need the diff the quiz
was graded on, which grip keeps in `.git/grip/last-diff.json`; a quiz graded before this
version cannot be appealed.

## `grip forget`

Clears remembered passes so the next commit or push is quizzed again, and says how many it
cleared (`no remembered passes` when there were none).

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

## `grip platform`

The [Keep A Grip](keepagrip.md) commands. `grip platform login TOKEN [--url URL]` verifies
and stores an API token from your dashboard (`logout` forgets it, `whoami` shows the
account). `problems [--category SLUG]` lists the catalogue. `solve SLUG [--dir PATH]` sets
up a repository with `PROBLEM.md` and the problem's pass mark, and remembers the base
commit. `submit [--no-tests] [--dry-run]` runs the problem's test command, quizzes you on
everything since that base commit (uncommitted work included) and reports the score;
`--dry-run` prints the payload instead of sending it.

```bash
grip platform login kag_…
grip platform solve cmd-vel-safety-filter
cd cmd-vel-safety-filter            # solve it, commit as you go
grip platform submit --provider claude-code
```

The old top-level names (`grip login`, `grip logout`, `grip whoami`, `grip problems`,
`grip solve`, `grip submit`) still work, print a one-line note on stderr that they moved,
and will be removed in a later release.

## For tools and agents

These commands are what git hooks, editors and coding agents call. `grip --help` does not
list them, to keep it short, but they are stable and `grip <command> --help` describes
each one.

### `grip hook STAGE [ARGS...]`

The entry point installed hooks and pre-commit call. `STAGE` is `pre-commit` or
`pre-push`; extra arguments are whatever git passes to the hook. You rarely run this
yourself, but it is handy for debugging:

```bash
printf 'refs/heads/main %s refs/heads/main %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)" \
  | grip hook pre-push origin
```

Accepts the same `--passing-score`, `--provider`, `--model`, `--difficulty` and
`--report` flags as `grip quiz`.

### `grip ask` / `grip grade` / `grip check`

The quiz in three non-interactive steps, for coding agents. `ask` prints the questions as
JSON (rubrics stay in `.git/grip/pending.json`), `grade` scores a JSON list of answers and
remembers a pass, `check` exits 0 when the diff already passed. Same `--staged`,
`--unpushed`, `--branch` and `--range` selectors as `grip quiz`. Raw line breaks inside an
answer's JSON string are accepted and kept. `grade` takes `--provider`, `--model` and
`--report`; the questions already exist, so it has no `--fresh` or `--difficulty`, and it
grades at the difficulty `ask` wrote them at. See [Coding agents](agents.md). (`check` is
also handy by hand, so `grip --help` lists it.)

`grade` always uses the configured pass mark: it takes no `--passing-score`. A pass only
counts for the hooks and `check` if its score reaches the current pass mark and, unless
`fake` is the configured provider, it was not graded by `fake`. See
[Remembering passes](internals.md#remembering-passes-and-getting-out-of-the-way).

`ask` uses the staged changes when no selector is given. `check` without a selector checks
what the installed hooks quiz: the unpushed commits for the pre-push hook (the default
`grip install`), the staged changes for a pre-commit hook, both when both are installed,
and the staged changes when no hook is installed. It says what it checked:
`grip: ok (unpushed commits)`.

```bash
grip ask --unpushed
grip grade --answers - <<'EOF'
["answer 1", "answer 2", "answer 3", "answer 4", "answer 5"]
EOF
grip check --unpushed && git push
```

### `grip agent-hook claude-code [--gate push|commit|both]`

Claude Code `PreToolUse` hook. Reads the hook payload from stdin and denies `git push`
(and `git commit` with `--gate both`) until the diff has passed a quiz. Installed by the
[plugin](agents.md#claude-code-plugin). Like the git hooks, it lets the command through
when `GRIP_SKIP=1` is set for it (`GRIP_SKIP=1 git push`), after `grip skip`, or in CI.

### `grip statusline`

One short line for an editor or Claude Code status line: `grip 92/100 pass`, with
`paused` appended while `grip skip` is in effect. Outside a repository or before any quiz
it prints nothing and exits 0. It reads an optional JSON payload on stdin and uses its
`cwd`, which is what Claude Code sends:

```json
{ "statusLine": { "type": "command", "command": "grip statusline" } }
```

`grip statusline --json` prints the score, pass mark, time and skip state for other tools.
