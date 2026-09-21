# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-20

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

[Unreleased]: https://github.com/guilyx/grip/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/guilyx/grip/releases/tag/v0.1.0
