---
description: Design for grip whiteboard, the 0.2.0 feature. A generated, clickable whiteboard of a change, with the questions pinned to its parts.
---

# Design: `grip whiteboard`

| | |
| --- | --- |
| **Status** | Draft, open for comments |
| **Target** | 0.2.0 |
| **Prototype** | [Open the clickable prototype](whiteboard-prototype.html) (one example diff, simulated grading) |

## The problem

Today grip asks five questions in a terminal, one after another. That works, but it misses
three things the whiteboard defense needs:

- **The picture.** At a whiteboard you draw the system before you defend it. A terminal
  quiz never shows how the parts of a change connect, so questions about trust boundaries
  and data flow are hard to ask and hard to answer.
- **Two of the four questions.** "What happens if this actor behaves maliciously?" and
  "what data structure did you use here and why?" are not asked directly.
- **A place to be pulled aside.** There is no way for a reviewer to run the conversation
  in person, with the rubric in front of them.

## What it does

1. `grip whiteboard` reads the diff, exactly like `grip quiz`.
2. One model call returns a **board**: the parts of the change, how they connect, which
   inputs are untrusted, and five questions pinned to specific parts, each with a hidden
   rubric.
3. grip serves the board on `127.0.0.1` and opens it in the browser.
4. The developer clicks a part, reads the question pinned there, answers in their own
   words. Rubrics stay on the server.
5. "Hand in" sends the answers to grip, which grades them with the same provider and the
   same 100-point scale as the quiz. A pass is remembered for that diff, so the push hook
   lets it through.

## Commands

| Command | What it does |
| --- | --- |
| `grip whiteboard` | Board for the staged changes, or every unpushed commit (same rules as `grip quiz`) |
| `grip whiteboard main..feat` | Board for a range |
| `grip whiteboard --interview` | Reviewer mode: rubrics visible, tick the points you hear, score at the end |
| `grip whiteboard --export board.html` | One static file with the board and questions, no rubrics, nothing graded |
| `grip whiteboard --no-open` | Print the URL instead of opening a browser (SSH, containers) |
| `/grip-whiteboard` | Skill: the agent starts the board and gives you the link; you answer in the browser |

Configuration, one new key:

```toml
[tool.grip]
whiteboard = "off"   # "off" | "fallback" | "always"
```

`fallback` serves a board when the hook has no terminal (an IDE push button, an agent's
shell) instead of skipping. `always` replaces the terminal quiz.

## Question kinds

Each pin has one kind. The first four are the whiteboard defense; the fifth keeps grip's
existing testing question.

| Kind | Asks | Required when |
| --- | --- | --- |
| `why this way` | Why X instead of Y; the model must name a real alternative Y | always |
| `malicious actor` | What a hostile caller, input or tenant can do | the board has an untrusted node |
| `data structure` | Why this representation, and what it costs | the diff adds or changes one |
| `where it fails` | The input, load or state that breaks it | always |
| `how you know` | How you would show it works | always |
| `how it works` | What the change does, in your words | fills a slot a kind above cannot |

Five pins, 20 points each, so the Grip Score and pass mark mean the same thing as today.

## The board

```python
class Node(BaseModel):
    id: str  # short slug, unique on the board
    label: str  # what a person would write on the whiteboard
    role: str  # one line
    kind: Literal["component", "store", "external", "context"]
    trust: Literal["trusted", "untrusted"] = "trusted"
    files: list[str]  # paths from the diff; empty only for external/context


class Edge(BaseModel):
    source: str
    target: str
    label: str  # what flows: "request + api_key"
    crosses_trust: bool  # derived by grip, not the model


class Pin(BaseModel):
    on: str  # node id or "source->target"
    kind: PinKind
    question: str
    rubric: list[str]  # 2 to 4 points, hidden from the developer


class Board(BaseModel):
    summary: str
    nodes: list[Node]  # 2 to 8
    edges: list[Edge]
    pins: list[Pin]  # exactly 5
```

**Validation before anything is shown.** Every edge and pin points at a real node. Every
`component` or `store` lists at least one file that is in the diff. Required kinds are
present. Labels are plain text under 40 characters. On failure grip asks the model once
more with the errors listed, then falls back to the terminal quiz. A board that does not
validate is never served.

**Layout is grip's job, not the model's.** Columns by role: untrusted actors on the left,
then the code in topological order of the edges, stores below what writes them, external
services on top. Deterministic, so the same board always looks the same, and testable
without a browser.

## Serving

- Python standard library `ThreadingHTTPServer`, bound to `127.0.0.1` on a random port. No
  new dependency.
- Every URL carries a 128-bit random token: `http://127.0.0.1:PORT/TOKEN/`. Requests without
  it get 404.
- The `Host` header must be `127.0.0.1:PORT`, and `POST` requires a matching `Origin`. This
  blocks DNS rebinding and other sites posting to the server.
- One page, all CSS and JS inline, a strict Content-Security-Policy with a hash for the one
  script, no network requests, system fonts with brand fallbacks.
- Routes: `GET /TOKEN/` the page, `GET /TOKEN/board.json` the board **without rubrics**,
  `POST /TOKEN/answers` grade once and return the sheet.
- The server exits after grading, on Ctrl-C, or after 20 idle minutes. Nothing is recorded
  until grading succeeds.

## Defending this design

The same four questions, asked of this document.

**Why a local web page and not a richer terminal UI?** A diagram with clickable parts,
multi-line answers and a reviewer view do not fit a terminal. A browser is on every
developer machine and needs no new dependency. **Why not a hosted service?** The diff and
the answers would leave the machine; today only the model provider sees them, and that
stays true.

**What if an actor behaves maliciously?**

- *Another website* tries to read or post to the server: token in the path, `Host` and
  `Origin` checks, no CORS headers.
- *The diff contains a prompt injection* that makes the model write HTML or script into a
  label: every model string is inserted with `textContent`, the CSP forbids inline script
  other than grip's own, and labels are validated as plain text.
- *The coding agent* tries to answer for the developer: the answers are typed into the
  browser and go straight to grip; the agent only ever sees the link and the final score.
  This is stronger than today's relay through `grip ask` and `grip grade`.
- *A developer* reads the rubric from disk: the same exposure as `pending.json` today. grip
  checks understanding; it is not an exam proctor.

**What data structure, and why?** A board is lists of nodes, edges and pins with string
ids, not free-form Mermaid or SVG from the model. Ids let grip validate references, pin
questions, and run its own layout. Free-form drawing output could not be checked, could
carry markup, and would lay out differently every run.

**Where does this fail?**

| Situation | What happens |
| --- | --- |
| No browser (SSH, container) | `--no-open` prints the URL; forward the port, or use the terminal quiz |
| A very large diff | At most 8 nodes; the model groups files. Pins favour the riskiest parts |
| The model invents a component | Validation rejects a node with no files from the diff; one retry, then the terminal quiz |
| The browser is closed mid-way | Nothing is recorded; the server times out; the push stays blocked |
| A slow agent CLI provider | Same latency as the quiz today; the page shows progress while grading |
| A docs-only change | No untrusted node, no data structure: `how it works` fills the slots |

## Milestones

**M1: the board** (0.2.0)

- [ ] `Board` models, prompt, validation, one retry
- [ ] `generate_board` on every provider; scripted boards for the `fake` provider
- [ ] Layered layout in Python, with tests
- [ ] `grip whiteboard --export` (static, no rubrics)

**M2: defend it** (0.2.0)

- [ ] Local server, token, `Host`/`Origin` checks, CSP, idle timeout
- [ ] Defend mode: answer, hand in, grade, remember the pass
- [ ] Interview mode
- [ ] `/grip-whiteboard` skill and docs; feature clip

**M3: everywhere** (0.3.0)

- [ ] `whiteboard = "fallback"` in the hook for pushes without a terminal
- [ ] Boards for Keep A Grip practice problems

## Open questions

1. Should a whiteboard pass satisfy the push hook? Proposed: yes, same score and pass mark.
2. Should `--export` ever include rubrics, for a reviewer who runs the session elsewhere?
   Proposed: no; use `--interview` on the reviewer's machine.
3. Five pins always, or four to six by diff size? Proposed: five, to keep scores comparable.
4. Brand fonts embedded in the binary (about 150 KB) or system fonts? Proposed: system fonts.
