# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `retry_questions` option: `same` (the default) keeps reusing a diff's questions and
  board on a retry; `new` forgets them when a quiz, a whiteboard defense or `grip grade`
  fails, so the next try asks new ones. Grading feedback now says where to look again
  and what kind of point is missing, instead of naming the rubric point, so a failed
  quiz can no longer be passed by pasting its feedback back.
- `[[paths]]` tables in `.grip.toml` (or `[[tool.grip.paths]]` in `pyproject.toml`) set
  a `passing_score`, a `difficulty` or `skip = true` for the files that match their
  `match` globs. A diff takes the strictest pass mark and the hardest difficulty of the
  files it touches, and a diff made only of skipped files is not quizzed.
- A GitHub Action (`uses: guilyx/grip@...`): on each pull request it writes five
  questions about the diff since the merge base and posts them as one comment, updated on
  every push, for the author or a reviewer to answer in review. Nothing is graded in CI.
  `export-board: true` also uploads the whiteboard as a workflow artifact and links it.
- Diffs with nothing to explain are skipped, with the reason printed: only renames or mode
  changes, only whitespace (a re-indent still counts), only paths in the new `skip_paths`
  option (e.g. `["docs/**", "*.md"]`), or fewer added and removed lines than the new
  `min_changed_lines` option (off by default). `grip check`, the agent push gate and
  `grip status` agree with the hook; `grip quiz --force` quizzes such a diff anyway.

### Changed

- An empty diff in `grip quiz`, `grip whiteboard` and `grip ask` says what there is
  instead and what to run, e.g. "nothing staged. 3 untracked files and 1 modified file:
  `git add` them first; 2 unpushed commits: try `grip quiz --unpushed` or `--branch`".
  `grip ask` adds the same as data in a `hint` field. The "Try it" line at the end of
  `grip init` suggests a command that has something to quiz in that repository.
- `grip init` writes `provider = "auto"` instead of this machine's coding agent, so the
  committed `.grip.toml` works for teammates without that tool. It prints what `auto`
  means on this machine, lists `auto` first in its menu, and writes a concrete provider
  only when you pick one. It now also asks for the difficulty (default `normal`) and says
  "wrote AGENTS.md for any coding agent that reads it" instead of "for AGENTS.md
  readers". `--provider auto` is accepted on every command.
- A missing API key for the `anthropic` or `openai` provider is reported before the
  quiz starts, instead of after the "reading your diff" spinner. The message lists the
  fixes: set the key variable (`api_key_env`, `ANTHROPIC_API_KEY` by default), use a
  coding agent installed on the machine, or try `--provider fake`. The `openai` provider
  no longer asks for a key when `base_url` points at a server of your own.
- The quiz header names what is being quizzed in words: `3 commits on feat/x → origin`
  for a push, `main..HEAD` as typed for `--range`, `2 unpushed commits on main` for
  `--unpushed`, and `everything since the first commit` instead of the empty tree's hash
  for a first push. When the diff is over `max_diff_bytes` it also says how much the model
  saw: `grip saw the first 200 KB of a 790 KB change (max_diff_bytes)`.
- `grip --help` lists the core commands first (init, quiz, whiteboard, status, skip,
  resume, last), groups the rest, and ends with examples. The Keep A Grip commands moved
  under `grip platform` (`grip platform login`, `solve`, `submit`, …); the old top-level
  names still work and print a one-line note that they moved. `hook`, `agent-hook`,
  `statusline`, `ask` and `grade` are no longer listed in the help but work as before.
- `grip config` shows where each value comes from (`default`, `pyproject.toml`,
  `.grip.toml`, `env GRIP_…` or a flag) and which configuration files it read, and takes
  `--provider`, `--model`, `--passing-score` and `--difficulty` to preview a run with
  those flags. `grip status` shows a short summary instead of the full table: provider,
  pass mark, difficulty and hook mode.
- Docs: new pages on cost and privacy per provider, rolling grip out to a team, removing
  it, and a glossary. The FAQ, honest numbers and home page no longer say that every run
  asks new questions or that `anthropic` is the default provider, the README says the
  offline `fake` grader only checks answer length, and the Mitchell Hashimoto quote links
  to his post.

### Fixed

- `--range main...HEAD` (three dots) no longer fails with `bad revision '.HEAD'`. It diffs
  from the merge base, as git does, in `grip quiz`, `ask`, `check` and `whiteboard`.
  `main..` and `main...` run up to `HEAD`, and a malformed range is rejected with the
  expected form.
- Git failures are one plain line instead of git's command line and stderr: "not inside a
  git repository (run grip from your project)", "this repository has no commits yet",
  "unknown revision 'nope'". `GRIP_DEBUG=1` also prints the full git command and output.
  `grip status` works in a repository with no commits yet.
- `grip quiz` without an interactive terminal (`grip quiz </dev/null`, a script) exits 2
  and points to `grip ask` / `grip grade` or `grip whiteboard`, instead of skipping with
  exit 0. Hooks still skip unless `require_tty` is set, and the notice no longer reads
  "grip: grip needs".
- A pasted answer that ends with an empty line ends there, instead of waiting for one
  more Enter. Empty lines between pasted paragraphs are still kept.
- `grip check` without a scope flag checks what the installed hooks quiz (the unpushed
  commits for the default pre-push hook) instead of always the staged changes, which said
  `ok` while the push would block. It says what it checked
  (`grip: ok (unpushed commits)`), names a `--range` as typed instead of calling it "the
  staged changes", and writes "1 file". Two scope flags at once (`--branch --unpushed`, `--range` with
  `--staged`) are a usage error in `grip quiz`, `ask`, `check` and `whiteboard` instead of
  the last one silently winning.
- The installed hook runs grip by the absolute path of the grip that ran `grip install`,
  then falls back to `PATH`, so a push from an IDE or GUI client whose `PATH` lacks grip is
  quizzed instead of skipped with "grip: not installed". When that path is gone, the hook
  says so and suggests `grip install` again, and `grip status` shows `grip missing`.
  Re-running `grip install --append` refreshes the appended block.
- CLI paper cuts: Ctrl-C exits 130 with `grip: stopped, nothing recorded.` instead of
  `Aborted!` and exit 1; `grip forget` says `no remembered passes` when there were none;
  pause times are shown in local time with the time zone instead of UTC; a change where
  every file is binary is skipped instead of quizzed; `grip status`, `install` and
  `uninstall` show hook paths relative to the repository root (`.git/hooks/pre-push`);
  `grip grade --help` no longer lists `--fresh` and `--difficulty`, and grading uses the
  difficulty `grip ask` wrote the questions at; a retry that reuses cached questions no
  longer shows a "reading your diff" spinner.
- A remembered pass can no longer be faked. `.git/grip/passed.json` now stores each pass's
  score, pass mark, provider and model, and the hooks, `grip check` and the agent push
  gate only accept it if the score reaches the current pass mark and, unless `fake` is
  the configured provider, it was not graded by `fake`. `grip grade` no longer takes
  `--passing-score`, and `grip status` says when a pass does not count and why. Passes
  recorded by older versions are asked again once.
- The Claude Code push gate honours `GRIP_SKIP=1 git push` typed in the command, as its
  message promised; before, it only read its own environment and denied the push anyway.
  The `/grip` skill no longer tells the agent where the rubrics are stored, every skill
  and the agent rules say not to read `.git/grip/`, and the docs say plainly that the
  rubrics are on disk there and that an agent with file access could read them.

## [0.4.1] - 2026-10-21

### Changed

- The README and the PyPI page lead with what grip does ("a git hook that won't let you
  push code you can't explain"), show the whiteboard first, and start with a 30-second
  offline try-out: a throwaway repository and `--provider fake`, no hook, no API key.

## [0.4.0] - 2026-10-20

### Added

- `grip quiz --branch` quizzes the branch since it left the default branch, the diff of
  its pull request, without typing `--range main..feat/x`. `grip ask`, `grip check` and
  `grip whiteboard` take it too.
- `grip status` shows where the branch stands against the default branch, and every row
  says when and how it was quizzed: `branch vs origin/main  passed 98/100 at 17:52`,
  `failed 40/100 at 17:40`, `not quizzed (4 files)`.

### Changed

- A push with nothing new to quiz says why, and when its commits are already on the
  remote but the branch never passed a quiz, says to run `grip quiz --branch`.
- A failed quiz points to `grip last`, on the score line and in the final message.

### Fixed

- Answers in paragraphs keep their line breaks. A paste in the terminal is one answer,
  blank lines included, ending at the next empty line, instead of spilling into the next
  questions. `grip grade` accepts raw line breaks inside the JSON strings, which coding
  agents write when they copy an answer as it is; the `/grip` skills say to keep them.
  `grip last` indents the lines of a long answer.

## [0.3.0] - 2026-10-20

### Added

- A retry reuses the questions. Quizzing the same diff again (after a failed quiz, from the
  hook, `grip quiz` or `grip ask`) reuses the questions written for it, so only the
  grading calls the model; `grip whiteboard` reuses its board the same way, which skips
  the slowest call. Entries are keyed by the diff, the task context, the difficulty, the
  provider and the model, live in `.git/grip/cache/` and expire after `cache_hours` (24 by
  default, 0 turns it off). `--fresh` asks for new questions once. `grip submit` never
  reuses questions.
- The hook can use the whiteboard. With `hook_mode = "whiteboard"` (or
  `GRIP_HOOK_MODE=whiteboard`), the pre-push and pre-commit hooks open the board in the
  browser and the change is defended there instead of in the terminal. Where no browser
  can be opened, over SSH or without a display, they fall back to the terminal quiz.

## [0.2.2] - 2026-10-19

### Changed

- Releases are automatic. Each pull request carries one label, `release:patch`,
  `release:minor`, `release:major` or `release:none`; merging a labelled one bumps the
  version, dates the changelog, tags `main` and publishes to PyPI and GitHub. A check
  makes sure every pull request has exactly one.
- `grip init` asks, in a terminal, which model to use, the pass mark and whether to quiz
  before pushes, commits or both, with defaults detected on the machine, then says what
  the chosen provider still needs. `--yes` takes every default; scripts are not asked.
- `grip install` says which model will write the questions when none is configured.

### Fixed

- Pushing a new branch said "nothing to quiz" when its commits were also on a branch that
  had been deleted upstream (a merged pull request) but not pruned locally: the stale
  `origin/...` ref made them look pushed. grip now asks the remote with `git ls-remote`
  whenever the local refs say a new branch has nothing to send.
- With no provider configured, grip used the Anthropic API even when Claude Code, Codex or
  Gemini CLI was installed, and without an API key it crashed with a traceback. The
  default provider is now `auto`: the first installed coding agent, else Anthropic. A
  missing Anthropic key is now a clear error that says what to set.
- `grip quiz --range main..feat/x` cut branch names to 12 characters in its header
  (`main..feat/kidnap-`); only full hashes are shortened now.

### Documentation

- A shorter README in the usual open-source shape: what grip is, install, quick start,
  configuration, links. The long-form material moved to the docs site.
- New [walkthrough](https://guilyx.github.io/grip/walkthrough/): one real change, from
  `git commit` to an accepted push, with the real questions, grades and screenshots.
- New [inside grip](https://guilyx.github.io/grip/internals/) deep dive: the checks before
  a quiz, diff collection, prompts, structured output per provider, rubric grading, pass
  memory, the agent flow, the whiteboard's validation, layout and server, and what is
  stored or sent.
- The docs navigation is regrouped into Get started, Guides, Deep dive, Reference and
  Project.

## [0.2.1] - 2026-10-19

grip is on PyPI.

### Changed

- grip is published to PyPI as `grip-hook`: `pipx install grip-hook` or
  `uv tool install grip-hook`. Releases publish there through trusted publishing, and
  the GitHub release carries the same sdist and wheel.
- The prebuilt binaries are gone. They bundled the build machine's Python, so the 0.1.0
  and 0.2.0 Linux binaries needed glibc 2.38 and failed on Ubuntu 22.04 and older with
  `GLIBC_2.38 not found`. `install.sh` now installs the package with uv or pipx, and
  replaces a binary left by an earlier install.
- Windows is supported through the same install.

### Fixed

- Whiteboard layout: a reply arrow (`allow -> handler` after `handler -> allow`) no longer
  pushes the board into extra columns, a part that only feeds others (tests, a fixture)
  sits just before what it feeds instead of across the input's arrow, and each column is
  ordered by what feeds it so fewer arrows cross.
- Whiteboard page: long labels and roles end in an ellipsis inside their box (the full
  text is in the tooltip and the side panel), long arrow labels wrap onto two lines and
  slide along their arrow when they would cover another label, a box or a pin, and a pin
  on an arrow sits on the arrow.
- Whiteboard page: two questions pinned to the same box or arrow were drawn on the same
  spot, so one was hidden and could not be clicked. They now line up side by side.
- `--unpushed` described its range as `sha..sha` with the same commit twice; the base is
  now resolved to the parent's hash.

## [0.2.0] - 2026-10-15

The whiteboard release.

### Added

- `grip whiteboard`: the change drawn as a whiteboard (its parts, how they connect, which
  inputs are untrusted) with five questions pinned to it, defended in the browser. It asks
  the two whiteboard questions the quiz does not: what a malicious actor could do and why
  a data structure was chosen. Graded on the same 100-point scale; a pass counts for the
  push hook. `--interview` shows the rubrics to the person asking and records nothing,
  `--export` writes a shareable page without rubrics. Served on 127.0.0.1 behind a random
  token, with `Host`/`Origin` checks and a hash-based Content-Security-Policy.
- `/grip-whiteboard` skill for Claude Code, Codex, Gemini CLI, Cursor and friends.
- Providers draw boards (`generate_board`); the `fake` provider draws one from the changed
  files or from a scripted `board`.

### Changed

- grip is now explained around the whiteboard defense: could you explain and defend this
  change at a whiteboard? The README, the docs homepage and a new
  [whiteboard defense](https://guilyx.github.io/grip/whiteboard-defense/) page map grip's
  five questions to that bar, and say when to leave grip off (prototypes, demos,
  experiments).

## [0.1.0] - 2026-10-15

The first public release.

### Added

- `grip quiz`: five questions about your staged, unpushed or ranged diff, graded to a Grip
  Score out of 100 with a configurable pass mark.
- `grip hook pre-commit` / `grip hook pre-push`: hook entry points, both for native git
  hooks and for the pre-commit framework (`.pre-commit-hooks.yaml`).
- `grip install` / `grip uninstall` / `grip status` / `grip config` / `grip forget`.
- Providers: Anthropic (default, `claude-opus-5`), any OpenAI-compatible API including
  Ollama, and an offline `fake` provider for tests and demos.
- Configuration through `.grip.toml`, `[tool.grip]` in `pyproject.toml`, `GRIP_*`
  environment variables and command-line flags.
- Passed diffs are remembered for a configurable time so you are not quizzed twice.
- Documentation site built with MkDocs Material and published to GitHub Pages.
- A brand identity under `branding/`: the grip mark (an 87.5 percent score ring that
  reads as a G) in ember orange on warm graphite, Archivo wordmark lockups, a social card,
  and a palette generated in OKLCH by `branding/build.py`, which refuses to write colours that fail WCAG 2 or APCA contrast or
  that collapse under simulated colour-blindness. `tests/test_branding.py` keeps the
  generated files current.
- A redesigned docs site: the generated palette and brand type, tabs for each section, a
  homepage with an install switcher and feature cards, dark code blocks with checked
  syntax colours, an announcement bar, social preview tags, and a Brand page.
- Feature clips under `docs/assets/features/`: one MP4 and GIF per new feature and a
  stitched "what's new" cut, recorded by `scripts/demo/make_features.py` against the real
  CLI with a scripted offline scenario.
- Every recording in the brand: the demo, the feature clips, the launch video and the
  Product Hunt stills now use the ember and graphite palette, JetBrains Mono in the
  terminal, Archivo and Instrument Sans on the title cards, and the mark. Colours come from
  `branding/tokens.json` through `scripts/demo/brandkit.py`, so videos and docs cannot drift.
- Two more skills: `grip-review` quizzes a reviewer on a range of commits (or prints the
  questions for the author with `--questions-only`), `grip-explain` turns the developer's
  answers from the last quiz into a commit message or pull request description. `grip last`
  shows the last graded quiz, with `--json` for skills to build on.
- An evals harness under `evals/`: eight fixture diffs with author notes, three answer
  arms (blank, generic, notes), repeated grading for noise, a committed fake-provider
  snapshot the tests keep current, and a Markdown summary. A "Honest numbers" docs page
  says what is measured, what is not, and where grip gets in the way.
- `CITATION.cff` and a Cite section in the README; `.github/FUNDING.yml`. The README opens
  with a three-tile summary and a before/after comparison, and its Why section cites the
  METR, GitClear, DORA and Stack Overflow findings on AI-assisted code.
- `grip skip` skips the next hook run once, `grip skip --hours N` pauses hooks for a
  while, `grip resume` cancels. Both the git hooks and the Claude Code push gate honour
  it; a manual `grip quiz` does not. `grip status` gained an activity section: last quiz,
  whether the staged and unpushed diffs have passed, and the skip state.
  `grip statusline` prints `grip 92/100 pass` for editor and Claude Code status lines.
- `grip init`: one command that writes a commented `.grip.toml` (defaulting to an
  installed coding agent as provider), installs the pre-push hook and writes the agent
  rule files. `--provider`, `--passing-score`, `--difficulty`, `--stage`, `--no-hook`,
  `--no-agents`, `--force`. `config.render_template` produces the file.
- `grip agents`: detects the coding agents installed on the machine (Claude Code, Codex,
  Gemini CLI, Cursor, Windsurf, Cline, Copilot, OpenCode) and writes, inside the
  repository, the rule file each one reads so it runs `grip check` before pushing.
  Marked blocks in `AGENTS.md`, `CLAUDE.md` and `GEMINI.md`, grip-owned rules files for
  Cursor, Windsurf and Cline, and the plugin enablement merged into
  `.claude/settings.json`. `--list`, `--all`, `--agent`, `--dry-run` and `--remove`.
- Keep A Grip, the problem platform: `grip login`, `grip logout`, `grip whoami`,
  `grip problems`, `grip solve SLUG` and `grip submit`. Solve a problem with any assistant,
  get quizzed on the diff, have the score reported with an API token. `Diff` gained a
  `context` field that the prompts pass to the model as data, and `Git.worktree_diff` diffs
  everything since a base commit, uncommitted work included.

- Demo recording (`docs/assets/demo.gif`, `.webm`, `.cast`) and the script that
  regenerates it, `scripts/demo/make_demo.py`.
- Scripted mode for the `fake` provider through `GRIP_FAKE_SCRIPT`, plus `GRIP_FAKE_DELAY`
  for artificial latency.
- Providers that reuse a coding agent already installed and signed in, so no API key is
  needed: `claude-code` (alias `claude`), `codex` and `gemini`. Each runs the CLI in
  non-interactive mode with tools disabled, in an empty scratch directory.

- Claude Code plugin, served from this repository as a marketplace
  (`/plugin marketplace add guilyx/grip`): `/grip` relays the quiz through Claude and
  a `PreToolUse` hook blocks `git push` until the diff has passed.
- The quiz skill lives at `skills/grip/SKILL.md` in the Agent Skills layout, so
  `npx skills add guilyx/grip` installs it into any agent that reads `SKILL.md` files.
  The plugin is served from the repository root (`hooks/hooks.json`, `skills/`).
- `grip ask`, `grip grade`, `grip check` and `grip agent-hook`: the quiz as
  non-interactive JSON steps so any coding agent can drive it.
- `grip study export` and `grip study status`: an anonymised JSON summary of every quiz
  taken on the machine (scores and per-question focus only, never code, paths, questions,
  answers or feedback) for study platforms. Graded quizzes are now appended to
  `.git/grip/history.jsonl` and the repository is recorded in
  `$XDG_DATA_HOME/grip/repos.json`. Nothing is uploaded by grip.

- Launch assets under `docs/assets/launch/` (Product Hunt image, icon, 1080p launch video)
  and `docs/assets/demo.mp4`, generated by `scripts/launch/make_launch.py`; a Media page
  on the docs site.

### Changed

- `grip init` reports agent files as "wrote" or "updated" instead of "written", and the
  generic `AGENTS.md` target is labelled "AGENTS.md readers".

- The demo now quizzes a ROS 2 change: a `cmd_vel` safety filter gaining a lidar scan
  watchdog, instead of a payment refund.
- Distribution: `install.sh` (`curl -fsSL .../install.sh | sh`) downloads a prebuilt,
  checksum-verified binary from GitHub Releases into `~/.local/bin`. The release workflow
  builds binaries for Linux and macOS (x86_64 and arm64) with PyInstaller; nothing is
  published to PyPI anymore.
- `model` now defaults to empty, meaning the provider's own default (`claude-opus-5` for
  Anthropic, the CLI's default for coding agents). `openai` and `ollama` require it.
- The score table uses rounded borders, matching the panels.
- The "blocked" message prints the report path relative to the working directory.

[Unreleased]: https://github.com/guilyx/grip/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/guilyx/grip/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/guilyx/grip/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/guilyx/grip/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/guilyx/grip/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/guilyx/grip/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/guilyx/grip/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/guilyx/grip/releases/tag/v0.1.0
