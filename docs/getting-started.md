# Getting started

## 1. Install the CLI

grip is on PyPI as [`grip-hook`](https://pypi.org/project/grip-hook/). Install it as a
command-line tool, in its own environment, with pipx or uv (Python 3.11+):

```bash
pipx install grip-hook        # or: uv tool install grip-hook
```

You get `grip` and a `git-grip` alias, so `git grip` works too. Linux, macOS and Windows.
Plain `pip install grip-hook` works as well, inside a virtualenv.

No pipx or uv? This script uses whichever one it finds, and tells you how to get uv when
there is neither:

```bash
curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh
```

Check it works:

```bash
grip --version
```

To upgrade: `pipx upgrade grip-hook` (or `uv tool upgrade grip-hook`).

## 2. Give it a model

Already have Claude Code, Codex or Gemini CLI installed and signed in? grip uses it
automatically, no key needed: with no provider configured, it picks the first of the three
it finds. `grip init` (step 3) asks which one to use and writes it down:

```toml
# .grip.toml at the root of your repository
provider = "claude-code"   # or "codex", "gemini", or "auto"
```

With none of them installed, grip uses Anthropic's API:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Prefer something local and free? Point grip at [Ollama](https://ollama.com):

```toml
# .grip.toml at the root of your repository
provider = "ollama"
model = "qwen3:14b"
```

See [Providers](providers.md) for the full list, including OpenAI-compatible servers
and proxies.

!!! tip "Using Claude Code?"
    Install the [plugin](agents.md) as well: `/grip` runs the quiz inside the agent
    and a hook keeps Claude from pushing a diff you have not passed.

## 3. Wire it into git

The quick way does steps 2, 3 and 5 at once:

```bash
cd your-repo
grip init
```

It writes a commented `.grip.toml` (picking `claude-code`, `codex` or `gemini` as the
provider when that CLI is installed, `anthropic` otherwise), installs the pre-push hook,
and writes the rule file for every [coding agent](agents.md) found on your machine. Commit
what it wrote and the team shares it. Flags: `--provider`, `--passing-score`,
`--difficulty`, `--stage`, `--no-hook`, `--no-agents`, `--force`.

To do it by hand, choose one of the two approaches.

=== "Native git hook"

    ```bash
    cd your-repo
    grip install                                   # pre-push (recommended)
    grip install --stage pre-commit --stage pre-push
    ```

    `grip install` writes a small shell script to `.git/hooks/<stage>` (or wherever
    `core.hooksPath` points). If a hook already exists there, use `--append` to chain grip
    after it or `--force` to replace it. `grip uninstall` removes exactly what it added.

=== "pre-commit framework"

    ```yaml
    # .pre-commit-config.yaml
    repos:
      - repo: https://github.com/guilyx/grip
        rev: v0.4.1
        hooks:
          - id: grip          # runs at pre-push
          # - id: grip-commit # runs at pre-commit
    ```

    ```bash
    pre-commit install --hook-type pre-push
    ```

    pre-commit installs grip into its own environment, so the global install from step 1
    is optional in this mode. grip talks to your terminal through `/dev/tty`, so it works
    even though pre-commit captures hook output.

??? question "pre-push or pre-commit?"
    **pre-push** is the sweet spot for most teams: you are quizzed once per batch of
    commits, right before the code becomes visible to others. **pre-commit** is stricter
    and fits solo work or high-stakes repositories. You can install both; a diff that
    passed at commit time is remembered so the push does not ask again.

## 4. Try it

```bash
git add .grip.toml          # the quiz covers staged changes; stage something first
grip quiz --provider fake   # offline dry run with a dummy grader
grip quiz                   # quiz your staged changes for real
grip status                 # what is installed, effective configuration
```

## 5. Pick a pass mark

```toml
# .grip.toml
passing_score = 70
difficulty = "normal"   # easy | normal | hard
```

Commit that file so the whole team shares the same bar. Everything else is optional; see
[Configuration](configuration.md).
