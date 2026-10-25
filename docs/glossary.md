---
description: The words grip uses, in one place.
---

# Glossary

Grip Score
:   The total for one quiz, out of 100: five questions worth 20 points each, graded by the
    model against each question's rubric.

pass mark
:   The Grip Score a diff needs for the commit or push to go through. `passing_score` in
    `.grip.toml`, 70 by default.

rubric
:   The points a complete answer covers, written by the model together with the question,
    before you answer. It is used for grading and is not shown in the terminal quiz, the
    questions `grip ask` prints, or the whiteboard in defend mode.

focus
:   The one-word label on a question that says what it is about: what the change does
    (behaviour), why it is built this way (motivation), an edge case, a risk, or how you
    would test it.

verdict
:   One or two sentences from the grader about the quiz as a whole, shown with the score.

board
:   The whiteboard: your change drawn as parts and arrows, with the untrusted inputs
    marked, and five questions pinned to it. `grip whiteboard` draws it; see
    [The whiteboard](whiteboard.md).

pin
:   A question attached to one part or one arrow of the board. Each pin has a kind: why
    this way, malicious actor, data structure, where it fails, how you know, how it works.

stage
:   When grip runs: `pre-commit` (before each commit), `pre-push` (before each push, the
    default), or `manual` (`grip quiz` and the other commands you run yourself).

difficulty
:   `easy`, `normal` or `hard`. It changes both which questions are asked and how strictly
    the answers are graded.

provider
:   The model that writes and grades the questions: a coding agent you already have, an
    API, or a local model. See [Providers](providers.md).
