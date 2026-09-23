# Contributing to grip

Thanks for helping keep a grip on things. This document covers the workflow; the
[Code of Conduct](CODE_OF_CONDUCT.md) covers how we treat each other.

## Development setup

```bash
git clone https://github.com/guilyx/grip
cd grip
uv venv && source .venv/bin/activate      # or: python -m venv .venv
uv pip install -e ".[dev,docs]"           # or: pip install -e ".[dev,docs]"
pre-commit install --hook-type pre-commit --hook-type pre-push
```

Yes, grip quizzes you before you push to grip. Set `GRIP_SKIP=1` if you are pushing
work in progress to a draft branch and know what you are doing.

## Day-to-day commands

| Task            | Command                                  |
| --------------- | ---------------------------------------- |
| Lint and format | `ruff check . && ruff format .`          |
| Type-check      | `mypy`                                   |
| Tests           | `pytest` (add `--cov` for coverage)      |
| Try it offline  | `grip quiz --provider fake`              |
| Docs preview    | `mkdocs serve`                           |

CI runs the same four checks on Linux, macOS and Windows across supported Python
versions, so running them locally first saves a round trip.

## Making changes

1. Open an issue first for anything bigger than a bug fix, so we can agree on the
   approach before you spend time on it.
2. Branch from `main`. Keep pull requests focused: one change per PR.
3. Add or update tests. New behaviour without a test will be asked to grow one.
4. Add a line under `Unreleased` in `CHANGELOG.md`.
5. Update the docs in `docs/` if the user-facing behaviour changed.
6. Open the pull request; the template lists what reviewers look for.

### Style

- Python 3.11+, fully typed (`mypy --strict` passes), Google-style docstrings.
- `ruff` is the single source of truth for formatting and linting.
- Prompts live in `src/grip_hook/prompts.py` only. Changing them is a behaviour change:
  say what you changed and why in the PR, and try it against a few real diffs.

### Adding a provider

Implement the `Provider` protocol from `grip_hook.providers.base`, register a factory in
`grip_hook.providers.REGISTRY`, and add tests that exercise it without network access
(see `tests/test_providers.py` for the pattern). Keep new runtime dependencies to a
minimum; optional ones belong in an extra.

## Regenerating the demo

The README animation is produced from a scripted, offline session so it is reproducible:

```bash
pip install -e ".[demo]"
python scripts/demo/make_demo.py all   # record -> docs/assets/demo.cast, render -> .gif + .webm
python scripts/launch/make_launch.py   # demo.mp4 + the launch image, icon and video under docs/assets/launch
```

`record` builds a throwaway repository, installs the hook with the `fake` provider driven
by `scripts/demo/scenario.json`, and types the answers in `make_demo.py` into a real
`bash` session. `render` replays the asciinema-format cast through `pyte` and Pillow.
Edit the scenario or the answers, re-run, and commit the three assets together. The
cast also plays with `asciinema play docs/assets/demo.cast`.

## Releasing (maintainers)

Releases are prebuilt binaries on GitHub Releases; nothing goes to PyPI.

1. Bump `__version__` in `src/grip_hook/__init__.py` and move the `Unreleased` section
   of `CHANGELOG.md` under the new version.
2. Commit, tag `vX.Y.Z`, push the tag.
3. The `release` workflow builds single-file binaries with PyInstaller for Linux and
   macOS (x86_64 and arm64), writes `SHA256SUMS`, and creates the GitHub release with the
   changelog section as notes. `install.sh` fetches from there.

To build a binary locally: `pip install -e ".[build]" && scripts/build/package.sh`, then
`GRIP_DOWNLOAD_BASE=file://$PWD/some-dir sh install.sh` to exercise the installer against
it (CI does exactly this on every push).
