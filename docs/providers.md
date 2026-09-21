# Providers

A provider turns a diff into questions and answers into grades. All of them use the same
prompts and the same JSON schemas, so the quality of the quiz depends on the model you
pick, not on the transport.

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

## Writing your own

Implement the `Provider` protocol (two methods) and register a factory in
`grip_hook.providers.REGISTRY`. See the [API reference](reference/providers.md) and
`tests/test_providers.py` for the testing pattern.
