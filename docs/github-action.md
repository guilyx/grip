---
description: Post grip's five questions on every pull request, for the author or a reviewer to answer in review.
---

# GitHub Action

The hook asks the person pushing. The action asks the pull request: on every
`pull_request` event it writes five questions about the pull request's diff and posts them
as one comment, which it updates on each new push. The author can answer in a reply, or a
reviewer can ask them in review.

Nothing is graded in CI. The answers come from people, in the conversation, and a
reviewer judges them. The rubrics are never posted. To be graded on your own answers, run
`grip quiz --branch` locally.

## Usage

```yaml
# .github/workflows/grip.yml
name: grip

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  questions:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0          # the merge base must be in the clone
      - uses: guilyx/grip@v0.5.0  # a release tag or a commit SHA
        with:
          api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

The questions cover the pull request's own changes: the diff from the merge base of the
base and head commits to the head, the same diff as `main...feature`. They are also
written to the job summary, so they are visible even when no comment is posted.

### With the whiteboard

```yaml
      - uses: guilyx/grip@v0.5.0
        with:
          api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          export-board: true
```

The action also runs `grip whiteboard --export`, uploads `board.html` as a workflow
artifact and links it from the comment. Download it and open it in a browser. Like any
export it shows the board and the questions, no rubrics, and grades nothing.

### Another provider

```yaml
      - uses: guilyx/grip@v0.5.0
        with:
          provider: openai
          model: gpt-5
          api-key: ${{ secrets.OPENAI_API_KEY }}
          difficulty: hard
```

For an OpenAI-compatible server or a gateway, set `base-url` too. `provider: fake` needs
no key and no network: it writes templated questions, which is useful to try the
workflow, not to review code.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `provider` | `anthropic` | `anthropic`, `openai`, `ollama` or `fake`. The coding-agent providers (`claude-code`, `codex`, `gemini`) need a CLI that is installed and signed in, which a CI runner does not have. |
| `model` | provider default | Model identifier. Empty uses the provider's default (`claude-opus-5` for Anthropic). |
| `api-key` | empty | The provider's key, from a secret. Required for `anthropic` and `openai`. |
| `base-url` | provider default | API base URL for a gateway, an OpenAI-compatible server or an Ollama server the runner can reach. |
| `difficulty` | from `.grip.toml` | `easy`, `normal` or `hard`. |
| `post-comment` | `true` | Post the questions as a comment, or update grip's earlier one. |
| `export-board` | `false` | Also draw the whiteboard and upload it as an artifact. One more model call. |
| `github-token` | `github.token` | Token used to post the comment. |
| `package` | `grip-hook` | What to `pip install`: the latest release by default, `grip-hook==X.Y.Z` to pin, or a local path. |
| `python-version` | `3.12` | Python used to run grip. |

## Outputs

| Output | Description |
| --- | --- |
| `status` | `questions`, `nothing-to-quiz` (the diff is empty once `exclude` is applied), or `skipped` (no key on a pull request from a fork). |
| `questions-file` | Path of the `grip ask` JSON: summary, files and questions, no rubrics. |
| `comment-url` | The comment's URL, when one was posted or updated. |
| `board-url` | The whiteboard artifact's URL, when `export-board` is on. |

## Permissions and forks

The job needs `pull-requests: write` to post the comment and `contents: read` to check
out the code. Nothing else.

On a pull request from a fork, GitHub gives the workflow a read-only token and no
secrets. The action then posts no comment. With `provider: fake` or a keyless Ollama
server the questions still go to the job summary; with a provider that needs a key there
is no key, so the action notes it and stops without failing the job.

Use the `pull_request` event, not `pull_request_target`. The action only reads the
history and never runs the pull request's code, but `pull_request_target` hands your
secrets to a run that a fork's author starts, and that is best kept for workflows that
need it.

The pull request's `.grip.toml` still sets `exclude`, `max_diff_bytes`, the model and
difficulty defaults. Its `base_url` is ignored: the key goes only where the workflow's
`base-url` says.

## Privacy and cost

The diff of every pull request goes to the configured provider, under your agreement
with it. That is the same data the hook sends, from a CI runner instead of a laptop: see
[what leaves your machine](how-it-works.md#what-leaves-your-machine). A secret committed in the pull request reaches
the provider like any other line, so keep `exclude` up to date. For code that must not
leave your network, run the action on a self-hosted runner with `provider: ollama`.

Each run is one model call to write the questions, plus one to draw the board when
`export-board` is on. Nothing is graded, so there is no second call. A push that leaves
the diff unchanged (a rebase onto the same base, a re-run) is still asked again: CI
runners keep no cache between runs.

## What it is not

- **Not a merge gate.** It posts questions; it does not pass or fail on answers. A
  reviewer decides what a good answer is.
- **Not proof that the author ran grip.** Local passes live in each developer's `.git/`
  and never reach CI.
