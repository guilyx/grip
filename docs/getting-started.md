# Getting started

## 1. Install the CLI

One command. It downloads the prebuilt binary for your OS and CPU from the latest GitHub
release, checks its SHA-256 against the published `SHA256SUMS`, and installs `grip` plus a
`git-grip` alias into `~/.local/bin`:

```bash
curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh
```

Options go after `sh -s --`:

| Option | Effect |
| --- | --- |
| `--version v0.1.0` | Install a specific release instead of the latest. |
| `--dir DIR` | Install somewhere other than `~/.local/bin`. |
| `--uninstall` | Remove `grip` and `git-grip` from the install directory. |

Supported: Linux and macOS, x86_64 and arm64. There is no Windows binary yet; use the
pre-commit framework route in step 3, which installs grip into its own environment.

Check it works:

```bash
grip --version
```

!!! note "Nothing to do with Python on your side"
    grip is written in Python, but the binary bundles everything it needs. No `pip`, no
    virtualenv, and nothing is published to PyPI. To upgrade, run the install command
    again.

## 2. Give it a model

Already have Claude Code, Codex or Gemini CLI installed and signed in? Reuse it, no key
needed:

```toml
# .grip.toml at the root of your repository
provider = "claude-code"   # or "codex", or "gemini"
```

Otherwise grip uses Anthropic's API by default:

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
