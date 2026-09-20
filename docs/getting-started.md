# Getting started

## 1. Install the CLI

grip is a Python 3.11+ command-line tool. Install it once per machine:

=== "pipx"

    ```bash
    pipx install grip-hook
    ```

=== "uv"

    ```bash
    uv tool install grip-hook
    ```

=== "pip"

    ```bash
    pip install --user grip-hook
    ```

Check it works:

```bash
grip --version
```

!!! note "Why is the package called grip-hook?"
    The name `grip` on PyPI belongs to an unrelated Markdown previewer. The package is
    `grip-hook`; the command it installs is `grip` (and `git grip`).

## 2. Give it a model

By default grip uses Anthropic's API.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Prefer something local and free? Point grip at [Ollama](https://ollama.com):

```toml
# .grip.toml at the root of your repository
provider = "ollama"
model = "qwen3:14b"
```

See [Providers](providers.md) for OpenAI-compatible servers and proxies.

## 3. Wire it into git

Choose one of the two approaches.

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
        rev: v0.1.0
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
