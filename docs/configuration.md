# Configuration

grip reads configuration from four places. Higher in the list wins.

1. Command-line flags, e.g. `--passing-score 80`.
2. Environment variables prefixed with `GRIP_`, e.g. `GRIP_PASSING_SCORE=80`.
3. `.grip.toml` at the repository root.
4. `[tool.grip]` in `pyproject.toml` at the repository root.

`grip config` prints the effective result.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `passing_score` | `70` | Minimum Grip Score (0 to 100) required to let the commit or push through. |
| `difficulty` | `"normal"` | `easy`, `normal` or `hard`. Changes both the questions and the grading strictness. |
| `provider` | `"auto"` | `auto`, `anthropic`, `openai` (any OpenAI-compatible API), `ollama`, `claude-code`, `codex`, `gemini` or `fake`. `auto` uses the first of Claude Code, Codex and Gemini CLI found on `PATH`, else `anthropic`; `grip status` shows what it picked. |
| `model` | provider default | Model identifier passed to the provider. Empty uses the provider's default (`claude-opus-5` for Anthropic, the CLI's own default for coding agents). Required for `openai` and `ollama`. |
| `effort` | `"medium"` | Reasoning effort for models that support it: `low`, `medium`, `high`, `xhigh`, `max`, or `none` to omit the parameter. Ignored for Haiku 4.5 and non-Anthropic providers. |
| `api_key_env` | provider default | Name of the environment variable holding the API key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_API_KEY`). |
| `base_url` | provider default | Override the API base URL, for proxies or self-hosted servers. |
| `exclude` | lockfiles and generated files | Globs removed from the diff. Patterns without `/` match at any depth. |
| `max_diff_bytes` | `200000` | Larger patches are truncated before being sent. |
| `remember_passes_hours` | `24` | How long a passed diff is remembered. `0` disables. |
| `cache_hours` | `24` | How long the questions (and the whiteboard) written for a diff are reused when the same diff is quizzed again, so a retry after a failed quiz costs one grading call instead of two. `0` asks the model every time; `--fresh` does so once. |
| `hook_mode` | `"quiz"` | What the git hook asks for. `quiz`: questions in the terminal. `whiteboard`: the [whiteboard](whiteboard.md) opens in your browser and you defend the change there. With no browser to open (over SSH, no display), the hook falls back to the terminal quiz. `grip quiz` always asks in the terminal. |
| `require_tty` | `false` | Fail (exit 2) instead of skipping when no interactive terminal is available. |
| `fail_open` | `false` | Let the commit or push through when the provider errors out. |
| `timeout` | `120` | Per-request timeout for provider calls, in seconds. |
| `paths` | none | `[[paths]]` tables: a different pass mark or difficulty, or no quiz, for part of the repository. See [Per-path rules](#per-path-rules). Read from the files only, not from the environment. |

The default `exclude` list is:

```toml
exclude = [
  "*.lock", "*.lockb", "*.min.js", "*.min.css", "*.map", "*.snap", "*.svg",
  "*.pb.go", "*_pb2.py", "*.generated.*",
  "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "poetry.lock",
  "uv.lock", "go.sum", "Pipfile.lock", "composer.lock", "Gemfile.lock",
]
```

Setting `exclude` replaces the list; copy the defaults if you want to extend them.

## Examples

A strict team setup in `.grip.toml`:

```toml
passing_score = 80
difficulty = "hard"
remember_passes_hours = 4
```

Defend every push on the whiteboard instead of in the terminal:

```toml
hook_mode = "whiteboard"
```

Or for yourself only: `export GRIP_HOOK_MODE=whiteboard`.

A local-only setup with Ollama:

```toml
provider = "ollama"
model = "qwen3:14b"
timeout = 300
```

Anthropic through a corporate gateway, keeping the key in a custom variable:

```toml
provider = "anthropic"
base_url = "https://llm-gateway.example.com"
api_key_env = "COMPANY_ANTHROPIC_KEY"
```

Per-developer overrides without touching the shared file:

```bash
export GRIP_DIFFICULTY=easy      # while onboarding
export GRIP_MODEL=claude-sonnet-5
```

## Per-path rules

A monorepo, or a repository that mixes customer-facing code with docs and prototypes,
rarely wants one bar for everything. Each `[[paths]]` table names some files and changes
what a diff that touches them is asked:

| Key | Description |
| --- | --- |
| `match` | A glob or a list of globs, read like `exclude`: `*` stays inside one directory, `**` crosses directories, a pattern without `/` matches at any depth, and a directory covers every file under it. Required. |
| `passing_score` | The pass mark for these files. |
| `difficulty` | `easy`, `normal` or `hard` for these files. |
| `skip` | `true`: these files need no quiz. |

How a diff is judged:

- Each file takes the strictest `passing_score` and the hardest `difficulty` of the rules
  it matches. A file no rule sets them for keeps the repository's own.
- The diff takes the strictest over all its files. A rule that lowers the bar for
  `prototypes/` lowers it only for a diff that touches nothing else.
- Files that match a `skip = true` rule do not count. A diff made only of them is not
  quizzed: the hook lets it through with a message and `grip check` exits 0.
- The quiz shows the pass mark that applies, and `grip ask` and `grip grade` use the
  same one.

A monorepo where payments code gets the hardest quiz, docs need none, and spikes get an
easy one:

```toml
# .grip.toml
passing_score = 70
difficulty = "normal"

[[paths]]
match = ["services/payments/**", "services/auth/**"]
passing_score = 90
difficulty = "hard"

[[paths]]
match = ["docs/**", "*.md"]
skip = true

[[paths]]
match = "prototypes"
passing_score = 40
difficulty = "easy"
```

A change to `services/payments/` and `prototypes/` together is quizzed at 90, hard. A
change to `docs/` and `README.md` only is not quizzed. A change to `prototypes/` and
`docs/` is quizzed at 40, easy.

!!! note "Put `[[paths]]` last"
    In TOML every key after a `[[paths]]` header belongs to that table, so keep the
    top-level options above the first `[[paths]]`. In `pyproject.toml` the tables are
    `[[tool.grip.paths]]`. Rules in `.grip.toml` replace the ones in `pyproject.toml`
    rather than adding to them. `grip config` lists the rules in effect.

## Environment variables that are not options

| Variable | Effect |
| --- | --- |
| `GRIP_SKIP=1` | Skip the quiz once. |
| `CI=true` | Skip the quiz (set by every CI provider). |
| `PRE_COMMIT_FROM_REF` / `PRE_COMMIT_TO_REF` | Set by the pre-commit framework at pre-push; grip uses them as the diff range. |
| `GRIP_FAKE_SCRIPT` / `GRIP_FAKE_DELAY` | Script file and artificial latency for the `fake` provider. See [Providers](providers.md#fake-offline). |
