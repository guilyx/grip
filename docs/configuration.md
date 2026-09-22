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
| `provider` | `"anthropic"` | `anthropic`, `openai` (any OpenAI-compatible API), `ollama` or `fake`. |
| `model` | `"claude-opus-5"` | Model identifier passed to the provider. |
| `effort` | `"medium"` | Reasoning effort for models that support it: `low`, `medium`, `high`, `xhigh`, `max`, or `none` to omit the parameter. Ignored for Haiku 4.5 and non-Anthropic providers. |
| `api_key_env` | provider default | Name of the environment variable holding the API key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_API_KEY`). |
| `base_url` | provider default | Override the API base URL, for proxies or self-hosted servers. |
| `exclude` | lockfiles and generated files | Globs removed from the diff. Patterns without `/` match at any depth. |
| `max_diff_bytes` | `200000` | Larger patches are truncated before being sent. |
| `remember_passes_hours` | `24` | How long a passed diff is remembered. `0` disables. |
| `require_tty` | `false` | Fail (exit 2) instead of skipping when no interactive terminal is available. |
| `fail_open` | `false` | Let the commit or push through when the provider errors out. |
| `timeout` | `120` | Per-request timeout for provider calls, in seconds. |

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

## Environment variables that are not options

| Variable | Effect |
| --- | --- |
| `GRIP_SKIP=1` | Skip the quiz once. |
| `CI=true` | Skip the quiz (set by every CI provider). |
| `PRE_COMMIT_FROM_REF` / `PRE_COMMIT_TO_REF` | Set by the pre-commit framework at pre-push; grip uses them as the diff range. |
| `GRIP_FAKE_SCRIPT` / `GRIP_FAKE_DELAY` | Script file and artificial latency for the `fake` provider. See [Providers](providers.md#fake-offline). |
