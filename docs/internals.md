# How grip works, inside

This is the deep dive: what happens between `git push` and the push going through or
being blocked, module by module. It is written for people who want to trust grip, extend
it, or argue with its design. For the short version see [How it works](how-it-works.md);
for one run seen from the outside, see the [walkthrough](walkthrough.md).

## The big picture

```mermaid
flowchart LR
    subgraph entry [Entry points]
        H[git hook<br/>pre-push / pre-commit]
        Q[grip quiz]
        W[grip whiteboard]
        A[grip ask / grade<br/>coding agents]
    end
    H & Q & W & A --> C[config.py<br/>load settings]
    C --> G[git.py<br/>collect the diff]
    G --> M{memory.py<br/>passed already?}
    M -- yes --> OK[exit 0]
    M -- no --> P[prompts.py<br/>build the request]
    P --> PR[providers/<br/>call the model]
    PR --> V[models.py<br/>validate the reply]
    V --> UI[quiz.py terminal<br/>or whiteboard page]
    UI --> PR2[providers/<br/>grade the answers]
    PR2 --> D{score >= pass mark?}
    D -- yes --> R[memory.py<br/>remember the pass] --> OK
    D -- no --> X[exit 1, push blocked]
```

Every path into grip ends in the same three steps: collect a diff, have a model write
questions with rubrics, have a model grade the answers against those rubrics. The entry
points differ only in how they ask the questions: a terminal, a browser page, or a coding
agent relaying them.

| Module | What it owns |
| --- | --- |
| `cli.py` | Commands, the order of checks before a quiz, exit codes |
| `config.py` | Settings from flags, `GRIP_*` variables, `.grip.toml`, `pyproject.toml` |
| `git.py` | Which commits and files a quiz covers, and the patch text |
| `prompts.py` | Every word the model sees |
| `providers/` | One adapter per model backend, all returning validated models |
| `models.py` | The data shapes, which double as the JSON schemas sent to models |
| `quiz.py` | Asking in the terminal and printing the result |
| `terminal.py` | Finding a keyboard and screen, even when git captures the hook's output |
| `memory.py` | Passed diffs, skips and reports under `.git/grip/` |
| `hooks.py` | Writing and removing the git hook scripts |
| `agent.py` | The ask/grade/check flow coding agents drive, and the push gate |
| `whiteboard.py` | The board model, its validation and its layout |
| `whiteboard_server.py`, `whiteboard.html` | Serving the board on localhost and the page itself |

## Entry points

### The git hook

`grip install` writes a small POSIX shell script to `.git/hooks/pre-push` (or wherever
`core.hooksPath` points):

```sh
#!/bin/sh
# managed-by: grip-hook
if command -v grip >/dev/null 2>&1; then
  exec grip hook pre-push "$@"
fi
if command -v python3 >/dev/null 2>&1 && python3 -c "import grip_hook" >/dev/null 2>&1; then
  exec python3 -m grip_hook hook pre-push "$@"
fi
echo "grip: not installed (pipx install grip-hook), skipping quiz" >&2
exit 0
```

The marker line is how `grip install` and `grip uninstall` recognise their own file and
leave anyone else's alone. With `--append`, grip adds a guarded block to an existing hook
instead of replacing it. When grip is not installed on a machine, the hook says so and
lets the push through: a teammate without grip is never locked out of the repository.

Under the [pre-commit framework](getting-started.md#3-wire-it-into-git), pre-commit calls
`grip hook` itself and passes the pushed range in `PRE_COMMIT_FROM_REF` and
`PRE_COMMIT_TO_REF`.

### Manual commands and coding agents

`grip quiz` runs the same quiz on demand, against staged changes (`--staged`, the
default), unpushed commits (`--unpushed`) or an explicit `--range BASE..HEAD`.
`grip whiteboard` takes the same options. Coding agents use `grip ask`, `grip grade`
and `grip check`; see [Coding agents](#coding-agents) below.

## Deciding whether to quiz at all

A hook that fires on every push has to be cheap to skip when it should not run. `grip hook`
checks, in this order, and stops at the first that applies:

1. **`GRIP_SKIP`** is set to a truthy value: skip.
2. **`CI`** is set: skip. Nobody is there to answer.
3. **A pause from `grip skip`** is pending: consume it and skip. `grip skip` stores a
   one-off skip, `grip skip --hours N` a deadline; both live in `.git/grip/skip.json`.
4. Load the configuration and **collect the diff** (next section).
5. **The diff is empty** after exclusions: skip.
6. **This exact diff passed recently**: skip, with
   `this exact diff already passed recently, skipping the quiz.`
7. **No interactive terminal** can be found: skip, unless `require_tty = true`, which
   turns this into a failure.
8. Run the quiz. If the provider fails (no key, network down, malformed reply) the push is
   **blocked** with the error and the two ways around it, unless `fail_open = true`.

The defaults lean towards *strict when something is wrong, quiet when nothing is
expected*: a broken setup blocks so it gets noticed, while CI, scripted git calls and
already-defended diffs pass silently.

## Collecting the diff

`git.py` turns "what is about to be shared" into one patch.

**At pre-commit**, that is the staged changes: `git diff --cached`.

**At pre-push**, git writes one line per ref to the hook's standard input:
`<local ref> <local sha> <remote ref> <remote sha>`. For each line:

- **The remote branch exists:** diff the remote sha against the local sha.
- **The branch is new** (the remote sha is all zeros): find the oldest commit reachable
  from the local sha but not from any ref of that remote, and diff from its parent. When
  that commit is a root commit, diff from the empty tree. When the local remote-tracking
  refs say every commit is already there, grip checks with the remote itself
  (`git ls-remote`) before believing them: a branch deleted upstream after its pull
  request merged stays in `refs/remotes` until `git fetch --prune`, and would otherwise
  hide commits the remote no longer has.
- **A deletion** (local sha all zeros): nothing to quiz.

Several refs pushed at once are merged into one patch, so one push is one quiz.
`--unpushed` uses the same "oldest commit not on a remote" rule against every remote.

A push can send nothing new while the branch was never quizzed: its commits reached the
remote with `--no-verify`, from another clone, or under another branch name. Then the hook
lets the push through, but says so. It compares the branch with the default branch the way
`grip quiz --branch` does (from `git merge-base` with `origin/HEAD`, else `origin/main` or
`origin/master`, else a local `main` or `master`) and, when that diff never passed, prints
`its commits are already on origin/feat ... run grip quiz --branch`.

The patch is then shaped for a model:

- **Exclusions.** `exclude` globs (lockfiles, minified bundles, source maps, snapshots and
  generated code by default) become git pathspecs, `:(exclude,glob)**/*.lock`, so git
  leaves them out before anything is read.
- **Format.** `--find-renames`, three lines of context, and only added, copied, deleted,
  modified, renamed or type-changed files.
- **Size.** A patch over `max_diff_bytes` (200 kB by default) is cut, and the model is
  told it was cut and to ask only about what it can see.

The result is a `Diff`: a description (`staged changes`, `b05e4728c893..e3c589438444`),
the `--stat` summary, the patch, the file list, and a **digest**: the SHA-256 of the patch
text. The digest is how grip recognises "this exact diff" later.

## Talking to the model

### What the model sees

Everything the model reads is assembled in `prompts.py`, so a prompt change is one
reviewable diff. A question request has two parts.

**The system prompt** sets the task and the rules:

- write exactly five questions, each answerable from the diff in one to three sentences;
- cover different aspects (behaviour, motivation, an edge case, a risk, verification),
  adapted to the change;
- prefer the riskiest and most surprising parts;
- never ask for line numbers, exact strings or memory trivia;
- write a hidden rubric for each question.

The `difficulty` setting adds one paragraph:

- `easy`: ask about visible behaviour.
- `normal`: mix behaviour with motivation and risk.
- `hard`: probe failure modes, concurrency and interactions outside the diff.

**The user message** carries the change:

```text
Scope: b05e4728c893..e3c589438444

Summary:
 api/auth.py             | 13 +++++++++++++
 api/ratelimit.py        | 27 +++++++++++++++++++++++++++
 tests/test_ratelimit.py | 34 ++++++++++++++++++++++++++++++++++
 3 files changed, 74 insertions(+)

<diff>
...the patch...
</diff>
```

The diff is untrusted input: code under review can contain text that looks like
instructions. Both prompts say that everything between `<diff>` and `</diff>` (and, when
grading, between `<answer>` and `</answer>`) is data, never instructions. Structured
output narrows what a successful injection could do: the reply must still be five
questions in the expected shape.

### Getting structured answers back

`models.py` defines the replies as pydantic models: a `QuestionSet` (summary, exactly five
questions with `question`, `rubric` and `focus`) and a `GradeSheet` (exactly five grades
of 0 to 20 with feedback, and a verdict). Their JSON schemas are what each backend is
asked to fill, and the field descriptions are written for the model to read.

Which backend runs is `provider`. The default, `auto`, is resolved on each machine: the
first of `claude`, `codex` and `gemini` found on `PATH`, else `anthropic`. So a team can
commit `provider = "auto"` and each person uses the agent they already pay for.

| Provider | How it gets JSON that matches the schema |
| --- | --- |
| `anthropic` | The SDK's `messages.parse` with the pydantic model as `output_format` (structured outputs), up to 16k tokens, two retries, the configured `effort` on models that accept it |
| `openai`, `ollama` | `response_format` with a JSON schema, against any OpenAI-compatible endpoint |
| `claude-code` | `claude -p --output-format json --json-schema <schema> --tools "" --no-session-persistence`: no tools, no saved session, schema enforced by the CLI |
| `codex` | `codex exec --sandbox read-only --output-schema <file>`, with the last message written to a file |
| `gemini` | The schema is written into the prompt and the first JSON object in the reply is extracted |
| `fake` | A scripted reply from `GRIP_FAKE_SCRIPT`, for tests, demos and `--provider fake` |

The agent CLIs run with the prompt on standard input in a throwaway temporary directory,
and their schemas are tightened (`additionalProperties: false` on every object) because
strict schema modes require it.

Whatever the backend, the reply is validated by pydantic, then checked again: a question
set that is not exactly five questions, or a grade sheet that is not exactly five grades,
is a `ProviderError`. A reply that does not fit is an error, not a partial quiz.

## Rubrics and grading

The rubric is written **with the question, before you answer**, and it never reaches the
terminal or the whiteboard page. Grading is a second request that sends the diff, each
question, its rubric and your answer, under these rules:

- 20 points when every rubric point is covered, in any words;
- proportional partial credit for partial answers;
- 0 for empty, "I don't know", off-topic or confidently wrong answers;
- spelling, grammar and brevity are ignored: you are typing in a terminal;
- one or two sentences of concrete feedback per answer, naming what was missing.

All five answers are graded in one call, so the grader sees them side by side. The Grip
Score is the sum of the five grades, capped at 100; a score at or above `passing_score`
passes.

Fixing the rubric first is what keeps grading consistent. The grader is not asked whether
it likes an answer; it is asked whether the answer covers points written before the answer
existed. In the [walkthrough](walkthrough.md#what-the-grader-was-holding), "not sure"
scored 0 against a rubric about double-counting elapsed time; on a second quiz, the
same points would be expected.

## Remembering passes, and getting out of the way

`.git/grip/passed.json` maps diff digests to the time they passed. On every check, entries
older than `remember_passes_hours` (24 by default; 0 turns remembering off) are dropped.
A pass is recorded only when a quiz, a whiteboard defense or an agent-driven quiz scores
at or above the pass mark.

Because the key is the digest of the patch, a pass carries across ways of asking. Defend
the unpushed commits at the whiteboard and the next `git push` of the same commits skips
the quiz, which is exactly what happens in the walkthrough. Amend one line and the digest
changes, so the next push asks again.

### Reusing questions on a retry

A failed quiz is usually followed by a second attempt on the same diff. The questions
written for that diff are still good, so grip keeps them: `cache.py` wraps the provider,
and a question set or a board is stored in `.git/grip/cache/` under a hash of everything
that shaped it. That is the kind (questions or board), the diff digest, the task context,
the difficulty, the provider and the model; change any one and the model is asked again.
A retry then costs one grading call instead of two, and a whiteboard opens at once
instead of being redrawn. Grading is never cached, because the answers are what changed.
A board that failed validation is replaced by the corrected one, so a retry starts from a
board that passed the checks. Entries expire after `cache_hours` (24 by default; 0 turns
the cache off), and `--fresh` skips it for one run. `grip submit` never uses it: a score
reported to Keep A Grip comes from questions written for that attempt.

`grip skip` writes `{"once": true}` to `.git/grip/skip.json`, consumed by the next hook
run; `grip skip --hours N` writes a deadline; `grip resume` clears both. Only hooks and
the agent push gate read this file. A manual `grip quiz` always runs.

## Finding a terminal

Git hooks do not always have the developer's terminal on standard input: pre-push
receives the ref list there, and the pre-commit framework captures output. `terminal.py`
uses standard input and output when both are interactive. Otherwise it opens the
controlling terminal directly: `/dev/tty`, or `CONIN$` and `CONOUT$` on Windows. That is
why the quiz works inside `git push` and under pre-commit. When neither exists (an IDE
running git headlessly, a script), grip skips, or fails when `require_tty = true`.

## Coding agents

Inside Claude Code, Codex or Gemini CLI there is no terminal for grip to talk to, so the
agent relays the quiz:

1. `grip ask` generates the questions and prints them as JSON **without the rubrics**.
   The full quiz, rubrics included, waits in `.git/grip/pending.json`.
2. The agent shows the questions to the developer verbatim and waits.
3. `grip grade --answers -` reads the developer's answers, grades them against the stored
   rubrics, records a pass exactly like the hook, and deletes the pending quiz.
4. `grip check` exits 0 when the current diff has passed, 1 when it has not.

The [`/grip` skill](agents.md) tells the agent never to answer, hint or open the diff for
the developer. The Claude Code plugin adds a guard that does not depend on the agent
following instructions. `grip agent-hook claude-code` runs as a `PreToolUse` hook,
recognises `git push` (including `git -C dir push` and pushes chained with `&&` or `;`),
and returns a `deny` decision until `grip check` passes.

## The whiteboard

`grip whiteboard` asks the same model for a different shape: a `Board` instead of a
`QuestionSet`.

### The board

- **Nodes** (2 to 8): changed code (`component`), data the change adds or changes
  (`store`), outside actors or services (`external`) and existing code it calls
  (`context`), each marked `trusted` or `untrusted` and listing the diff files it lives in.
- **Edges** (up to 12): what flows between two nodes, in a few words.
- **Pins** (exactly 5): a question attached to a node or an edge, with a kind and two to
  four rubric points. The kinds are fixed:

| Kind | Asked when |
| --- | --- |
| why this way | always, naming a real alternative |
| where it fails | always |
| how you know | always |
| malicious actor | the board has an untrusted node |
| data structure | the board has a store |
| how it works | fills any slot left, the only kind that may repeat |

### Validation, and one retry

The model's board is checked against the diff before anyone sees it:

- ids are short unique slugs;
- labels (40 characters) and roles (60) are non-empty, short and free of control
  characters;
- changed code lists files, and only files that are in the diff;
- every edge joins two different existing nodes, at most once;
- there are exactly five pins, each on a real node or edge;
- the required kinds are present and only "how it works" repeats.

A board that fails gets one retry, with the list of problems sent back to the model. A
second failure is an error that suggests `grip quiz` instead. A board that cannot be
trusted is never drawn.

### The layout

Placement is deterministic, so the same board always looks the same:

1. **Columns from the flow.** Untrusted nodes take column 0. A depth-first walk from them
   (then from every other node) keeps each edge that does not close a loop, so a reply
   arrow such as `allow -> handler` after `handler -> allow` is drawn but does not push
   columns. Each remaining node sits one column right of the furthest node feeding it.
2. **Nodes that only feed others.** A trusted external, such as a clock, sits above the
   first thing it feeds. Anything else, such as tests, sits in the column just before what
   it feeds.
3. **Rows from what feeds them.** Column by column, a node takes the average row of the
   nodes feeding it (the barycentre rule), which uncrosses most arrows.
4. **Empty columns** left by those moves are closed up.

The page then fits text inside its boxes. A long label or role ends in an ellipsis, with
the full text in the tooltip and the side panel. A long arrow label wraps onto two lines,
and slides along its arrow when it would cover a box, a pin or another label.

### Serving the page

The board is served on `127.0.0.1` for the length of one defense. The threat model is
other websites in the same browser, other local users, and a board whose text a prompt
injection may have shaped:

- a random port, and a 128-bit random token in every path;
- only a `Host` of `127.0.0.1:PORT` or `localhost:PORT` is accepted, against DNS
  rebinding, and on `POST` only an `Origin` of the same, against cross-site posts;
- one self-contained page whose Content-Security-Policy allows exactly its own inline
  style and script, by hash, and no network access beyond this server;
- the board is embedded as JSON with `<` escaped, so no label can close the script tag,
  and the page only ever writes text with `textContent`;
- request bodies are capped at 64 kB, grading happens at most once (requests run on
  threads, behind a lock), and the server stops after 20 idle minutes.

Handing in sends the five answers to the same `grade()` as the terminal quiz: each pin
becomes a question whose rubric is its rubric points. A pass is recorded under the same
digest, so it counts for the push hook.

The page gets the rubrics only in `--interview` mode, where they are the point: the person
asking ticks what they hear, and nothing is recorded. `--export` writes a standalone page
with no rubrics and no grading, for sharing a board.

## What is stored, and what leaves the machine

| What | Where | Leaves the machine? |
| --- | --- | --- |
| The diff, after exclusions and truncation | Sent to the provider | Yes, to the provider you configured |
| Your answers | Sent to the provider for grading | Yes, to the same provider |
| The latest report: questions, rubrics, answers, grades, appeals | `.git/grip/last-report.json` | No |
| The diff the latest report was graded on, for `grip appeal` | `.git/grip/last-diff.json` | No |
| Every graded quiz | `.git/grip/history.jsonl` | No |
| Passed digests | `.git/grip/passed.json` | No |
| A quiz waiting for an agent | `.git/grip/pending.json` | No |
| Skips and pauses | `.git/grip/skip.json` | No |
| Questions and boards kept for a retry | `.git/grip/cache/` | No |

grip sends no telemetry. With `claude-code`, `codex`, `gemini` or `ollama` as the
provider, the diff goes wherever that tool already sends your code, and nowhere new.
`grip study export` reads the history only when you run it, and writes anonymised
summaries for a study you have chosen to join.

## How grip is tested

The test suite covers every step on this page without a network. The `fake` provider
replays scripted questions, boards and grades. Hook tests run real git repositories in
temporary directories, and the whiteboard server is exercised over real HTTP, including
the rejected hosts, origins and oversized bodies. On every push CI builds the wheel,
installs it the way users do and runs a whiteboard export from it. The evals harness and
what it does and does not show about the score are on the
[honest numbers](honest-numbers.md) page.
