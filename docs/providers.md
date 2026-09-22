# Providers

A provider turns a diff into questions and answers into grades. All of them use the same
prompts and the same JSON schemas, so the quality of the quiz depends on the model you
pick, not on the transport.

## Coding agents you already have

If Claude Code, Codex or Gemini CLI is installed and signed in, grip can drive it in
non-interactive mode. No API key, no extra bill: the quiz rides on the subscription you
already pay for.

=== "Claude Code"

    ```toml
    provider = "claude-code"   # or "claude"
    # model = "sonnet"         # optional; empty means the CLI's default
    ```

    Runs `claude -p --output-format json --json-schema ...` with tools disabled and no
    session persistence. Install with `npm install -g @anthropic-ai/claude-code`.

=== "Codex"

    ```toml
    provider = "codex"
    # model = "gpt-5-codex"    # optional
    ```

    Runs `codex exec --sandbox read-only --output-schema ...`. Install with
    `npm install -g @openai/codex`, then `codex login`.

=== "Gemini CLI"

    ```toml
    provider = "gemini"
    # model = "gemini-2.5-pro" # optional
    ```

    Runs `gemini --output-format json`. Gemini CLI has no schema flag, so the JSON schema
    is included in the prompt and the object is extracted from the reply. Install with
    `npm install -g @google/gemini-cli`.

Every agent starts in an empty temporary directory with tools disabled where the CLI
allows it, so it cannot read your repository, run commands, or pick up project
instructions and hooks. It only sees the diff and your answers, exactly like the API
providers. `timeout` applies to the whole agent run; agents are slower than a direct API
call, so raise it if you see timeouts.

!!! tip "Which one?"
    Claude Code and Codex enforce the JSON schema themselves, so their output is always
    well-formed. Gemini usually complies but can occasionally answer with prose, which
    grip reports as a provider error (retry, or set `fail_open = true`).

## Anthropic (default)

Uses the official `anthropic` SDK with structured outputs, so the questions and grades
are always well-formed.

```toml
provider = "anthropic"
model = "claude-opus-5"   # default
effort = "medium"
```

Credentials are resolved the way the SDK does: `ANTHROPIC_API_KEY`, then
`ANTHROPIC_AUTH_TOKEN`, then a profile created by `ant auth login`. Set `api_key_env` to
read the key from a differently named variable, and `base_url` to go through a gateway.

`effort` maps to the API's `output_config.effort`. `medium` is a good balance between
question quality and hook latency; raise it for `difficulty = "hard"`, lower it if the hook
feels slow. It is omitted automatically for Haiku 4.5, which does not support it.

## Ollama (local)

```toml
provider = "ollama"
model = "qwen3:14b"
timeout = 300
```

Defaults to `http://localhost:11434/v1`. No key is required; set `OLLAMA_API_KEY` if your
server enforces one. Smaller models produce shallower questions and noisier grading, so
consider `passing_score` accordingly.

## OpenAI-compatible APIs

Any server that implements `POST /chat/completions` with `response_format` JSON schemas:
OpenAI, vLLM, LM Studio, Groq, Together, and most gateways.

```toml
provider = "openai"
model = "gpt-5"
base_url = "https://api.openai.com/v1"   # default
api_key_env = "OPENAI_API_KEY"           # default
```

This provider is implemented with the standard library, so it adds no dependency.

## fake (offline)

```bash
grip quiz --provider fake
```

Templated questions and a length-based grader. It exists so you can try the flow, write
tests, or demo grip without a network. Never use it as your real provider.

Two environment variables shape it:

| Variable | Effect |
| --- | --- |
| `GRIP_FAKE_SCRIPT` | Path to a JSON file with a summary, five questions, substring grading rules and verdicts. The demo recording uses `scripts/demo/scenario.json`. |
| `GRIP_FAKE_DELAY` | Seconds to sleep per call, so the spinner is visible in recordings. |

## Writing your own

Implement the `Provider` protocol (two methods) and register a factory in
`grip_hook.providers.REGISTRY`. See the [API reference](reference/providers.md) and
`tests/test_providers.py` for the testing pattern.
