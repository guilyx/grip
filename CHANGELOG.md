# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/guilyx/grip/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/guilyx/grip/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/guilyx/grip/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/guilyx/grip/releases/tag/v0.1.0
