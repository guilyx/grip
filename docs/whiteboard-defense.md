---
description: Could you defend this change at a whiteboard? What grip asks, why, and when to leave it off.
---

# The whiteboard defense

> I should be able to pull you aside at any moment and ask you to explain any
> customer-facing system you've shipped. You should be able to clearly explain how it
> works and defend the decisions you made. [...]
>
> I don't expect line-level familiarity with the code. [...] But if I ask "why did you do
> X instead of Y?", "what happens if this actor behaves maliciously?", "what data structure
> did you use here and why?", or "where does this fail?" you should be able to answer
> confidently. [...]
>
> For PoCs, demos, experiments, whatever: I don't care. [...] But if you're shipping
> customer-facing work, you can't be shipping things you don't understand at a high level.
>
> Mitchell Hashimoto

We came across this post after grip was built. It names the bar grip was made for better
than we had. Mitchell is not involved with grip; the words are his, the tool is ours.

## The bar

- **High level, not line level.** Nobody needs to recite function names. You need to know
  how the system works and why it is shaped that way.
- **Defend, not just describe.** "Why X instead of Y" has an answer you chose, even if a
  model typed it.
- **Know where it breaks.** The failure modes, the hostile inputs, the state nobody planned
  for.
- **Only for what ships.** A prototype owes nobody an explanation. A customer-facing change
  does.

## How grip asks

At the push, grip reads your diff and writes five questions with hidden rubrics. You
answer each in a sentence or two. A Grip Score at or above your team's pass mark lets the
push through.

| At the whiteboard | grip's question |
| --- | --- |
| How does it work? | **behaviour**: what the change does, in your words |
| Why X instead of Y? | **motivation**: why it is built this way |
| Where does this fail? | **edge case**: the input or state that breaks it |
| What could go wrong? | **risk**: side effects, and what else it touches |
| How do you know? | **testing**: how you would show it works |

The model is told never to ask for line numbers, exact strings, or trivia that only tests
memory. Questions favour the riskiest and most surprising parts of the diff.

!!! tip "The whole defense: `grip whiteboard`"
    The terminal quiz does not ask two of the questions above directly: what a malicious
    actor could do, and why a data structure was chosen. [`grip whiteboard`](whiteboard.md)
    does. It draws the change, its parts, how they connect and which inputs are untrusted,
    and pins the questions to it in your browser.

## Why now

Generated code, big refactors, late-night pastes: it is easy to ship a change you could
not explain in a code review. The gap is measured, not imagined. In METR's 2025
randomised trial, experienced open-source developers using AI tools were
[19% slower while believing they were 20% faster](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/).
GitClear's analysis of 153 million changed lines found
[code churn on track to double since AI assistants arrived](https://www.gitclear.com/coding_on_copilot_data_shows_ais_downward_pressure_on_code_quality).
The [2024 DORA report](https://dora.dev/research/2024/dora-report/) linked higher AI
adoption to lower delivery stability, and the
[2025 Stack Overflow survey](https://survey.stackoverflow.co/2025/ai) put developer trust
in AI output at a low. Each of those tools checks the code; grip checks the person who is
about to ship it.

## Prototype freely, defend what ships

grip should cost nothing where the bar does not apply.

| Situation | What to do |
| --- | --- |
| A spike, demo or experiment repository | Leave the hook out. Nothing to configure. |
| One push you will explain later | `grip skip`, then push |
| A pairing session or live demo | `grip skip --hours 2`; `grip resume` ends it early |
| Generated files and lockfiles | Already excluded; add globs to `exclude` |
| Customer-facing code | Keep the hook. Set the pass mark as a team. |

## What it is not

- **Not a correctness check.** Tests and review still do that. grip checks the person.
- **Not proof of understanding.** Five answers are a signal, not a certificate. What has
  and has not been measured is on the [honest numbers](honest-numbers.md) page.
- **Not a ban on AI.** Use any assistant to write the code, and to study the diff before
  the quiz. The only rule is that the answers are yours.
