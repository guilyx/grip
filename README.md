# grip

**A git hook that won't let you push code you can't explain.**

[![CI](https://github.com/guilyx/grip/actions/workflows/ci.yml/badge.svg)](https://github.com/guilyx/grip/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/grip-hook)](https://pypi.org/project/grip-hook/)
[![Python](https://img.shields.io/pypi/pyversions/grip-hook)](https://pypi.org/project/grip-hook/)
[![Docs](https://img.shields.io/badge/docs-guilyx.github.io%2Fgrip-ff8644)](https://guilyx.github.io/grip/)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](https://github.com/guilyx/grip/blob/main/LICENSE)

![grip whiteboard: the change drawn as parts and arrows, with a question about a malicious client pinned to it](https://raw.githubusercontent.com/guilyx/grip/main/docs/assets/whiteboard.png)

Before your code leaves your machine, grip reads the diff and asks you about it: why it is
built this way, where it fails, what a malicious caller could do, how you know it works.
In the terminal, or on a whiteboard of your change in the browser. A model grades your
answers out of 100. Below your team's pass mark, the push waits.

It checks the developer, not the code. Let AI write it; just be able to defend it.
**[Follow one real change from commit to push](https://guilyx.github.io/grip/walkthrough/)**
to see what it asks and how it grades.

> "If I ask 'why did you do X instead of Y?', 'what happens if this actor behaves
> maliciously?' [...] you should be able to answer confidently."
> Mitchell Hashimoto, [on X](https://x.com/mitchellh/status/2100249348345057389), about
> [the whiteboard defense](https://guilyx.github.io/grip/whiteboard-defense/) (not affiliated with grip)

## Try it in 30 seconds

No hook, no account, no API key: the `fake` provider runs the whole flow offline.

```bash
pipx install grip-hook                 # or: uv tool install grip-hook
mkdir try-grip && cd try-grip && git init -q
echo 'def add(a, b): return a + b' > math.py && git add math.py
grip quiz --provider fake              # five questions in your terminal
grip whiteboard --provider fake        # the same, drawn in your browser
```

The offline grader does not read your answers, it only checks their length: twenty
characters, or the word "because", gets full marks. Any long answer passes and a row of
"idk" fails, so this shows the flow, not the grading. For questions written from your diff
and real grading, use a model you have (`--provider claude-code`, `codex`, `gemini` or
`anthropic`), or read the [walkthrough](https://guilyx.github.io/grip/walkthrough/).

Nothing is blocked until you install the hook with `grip init`.

## Features

- **Five questions from your diff**, graded against a hidden rubric into a Grip Score out
  of 100. Never line numbers or trivia.
- **`grip whiteboard`**: your change drawn as parts and arrows in the browser, with the
  questions pinned to it, including what a malicious actor could do. Set
  `hook_mode = "whiteboard"` and every push is defended there.
- **Works with what you have**: reuse Claude Code, Codex or Gemini CLI as the model (no
  API key), or use Anthropic, any OpenAI-compatible API, or Ollama locally.
- **Inside coding agents**: `/grip` in Claude Code, Codex, Gemini CLI, Cursor and others,
  and a gate that keeps the agent from pushing until you pass.
- **Out of the way when it should be**: a passed diff is remembered, a retry reuses its
  questions so only grading costs a call, `grip skip` lets one push through, CI and
  non-interactive runs are skipped.

## Installation

```bash
pipx install grip-hook        # or: uv tool install grip-hook
```

Python 3.11 or newer, on Linux, macOS or Windows. This installs the `grip` command and a
`git grip` alias.

## Quick start

![grip blocking a push with lazy answers, then letting it through after real ones](https://raw.githubusercontent.com/guilyx/grip/main/docs/assets/demo.gif)

```bash
cd your-repo
grip init        # writes .grip.toml, installs the pre-push hook
git push         # grip asks five questions first
```

`grip init` writes `provider = "auto"`: each machine uses Claude Code, Codex or Gemini CLI
when one is installed. Otherwise set `ANTHROPIC_API_KEY`, or see
[Providers](https://guilyx.github.io/grip/providers/).

Other commands you will use:

```bash
grip quiz                  # quiz your staged changes now, no hook needed
grip quiz --branch         # quiz the whole branch since main: your pull request
grip whiteboard            # defend the change in the browser
grip quiz --provider fake  # try the flow offline (grades by length only)
grip skip                  # let the next push through
grip status                # hooks, last score, configuration
```

The [walkthrough](https://guilyx.github.io/grip/walkthrough/) follows one real change from
`git commit` to an accepted push.

## Configuration

`.grip.toml` at the root of the repository, committed so the team shares one bar:

```toml
passing_score = 70        # 0-100
difficulty = "normal"     # easy | normal | hard
provider = "claude-code"  # auto | anthropic | claude-code | codex | gemini | openai | ollama
```

Every option, environment variables and the pre-commit framework hook are in the
[configuration reference](https://guilyx.github.io/grip/configuration/).

## Documentation

**[guilyx.github.io/grip](https://guilyx.github.io/grip/)**: getting started, a
walkthrough, coding agents, the whiteboard, how grip works inside, and the honest numbers
on what the score does and does not measure.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](https://github.com/guilyx/grip/blob/main/CONTRIBUTING.md)
for the development setup and [SECURITY.md](https://github.com/guilyx/grip/blob/main/SECURITY.md)
for reporting vulnerabilities and what data grip sends. This project follows a
[code of conduct](https://github.com/guilyx/grip/blob/main/CODE_OF_CONDUCT.md).

## Citation

If grip is part of a study or a course, please cite it; GitHub's "Cite this repository"
button reads [CITATION.cff](https://github.com/guilyx/grip/blob/main/CITATION.cff).

## License

[BSD-3-Clause](https://github.com/guilyx/grip/blob/main/LICENSE). Copyright (c) 2026, Erwin L.

## Star history

<a href="https://star-history.com/#guilyx/grip&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=guilyx/grip&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=guilyx/grip&type=Date" />
    <img alt="Star history of guilyx/grip" src="https://api.star-history.com/svg?repos=guilyx/grip&type=Date" />
  </picture>
</a>
