---
title: grip, keep a grip on your code
description: A quiz on your own diff before it goes upstream. In your terminal and inside your coding agent.
hide:
  - navigation
  - toc
  - path
---

<div class="grip-hero" markdown>

<div class="grip-hero__copy" markdown>

<span class="grip-eyebrow">open source · git hook · agent skill</span>

<h1 class="grip-hero__title">Keep a grip<br>on your <em>code.</em></h1>

<p class="grip-hero__lede">grip quizzes you on your own diff before it goes upstream. Five
questions written from the change, a Grip Score out of 100, and a pass mark your team
picks. In the terminal, and inside Claude Code, Codex, Gemini CLI and Cursor.</p>

<div class="grip-hero__actions" markdown>

[Get started](getting-started.md){ .md-button .md-button--primary }
[Watch the 90-second tour](#see-it-run){ .md-button }

</div>
</div>

<div class="grip-score" aria-hidden="true">
<svg viewBox="0 0 120 120">
<circle class="grip-score__track" cx="60" cy="60" r="46" fill="none" stroke-width="11" pathLength="100"/>
<circle class="grip-score__arc" cx="60" cy="60" r="46" fill="none" stroke-width="11" stroke-linecap="round" pathLength="100"/>
</svg>
<div class="grip-score__label"><b>92</b><span>/100</span><em>PASS</em></div>
</div>

</div>

## Install in one line

=== "Terminal"

    ```bash
    curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh
    cd your-repo && grip init
    ```

    `grip init` writes `.grip.toml`, installs the pre-push hook and tells the coding agents
    on your machine to run the quiz. It reuses Claude Code, Codex or Gemini CLI as the
    model when one is installed, so most people never need an API key.

=== "Any coding agent"

    ```bash
    npx skills add guilyx/grip -g
    ```

    Adds `/grip`, `/grip-review` and `/grip-explain` to Claude Code, Codex, Gemini CLI,
    Cursor, Windsurf, Cline and Copilot. Type `/grip` before you push.

=== "Claude Code plugin"

    ```text
    /plugin marketplace add guilyx/grip
    /plugin install grip@grip
    ```

    The skill, plus a hook that stops Claude from running `git push` until the diff has
    passed a quiz.

=== "pre-commit"

    ```yaml
    repos:
      - repo: https://github.com/guilyx/grip
        rev: v0.1.0
        hooks:
          - id: grip
    ```

## How it works

<div class="grip-steps" markdown>

<div class="grip-step" markdown>

### You push

The hook collects the diff: staged changes for a commit, every unpushed commit for a
push. Lockfiles and generated code are left out.

</div>

<div class="grip-step" markdown>

### grip asks

A model reads the diff and writes five questions with hidden rubrics: behaviour,
motivation, an edge case, a risk, how you would test it.

</div>

<div class="grip-step" markdown>

### You answer

A sentence or two each. At or above the pass mark the push goes through. Below it you
get per-question feedback on what to re-read.

</div>

</div>

[Read the details](how-it-works.md){ .md-button }

## What you can do with it

<div class="grid cards" markdown>

-   :material-console-line:{ .lg } __Set up in one command__

    ---

    `grip init` writes the config, installs the hook and wires your agents.

    [:octicons-arrow-right-24: Install](getting-started.md)

-   :material-robot-outline:{ .lg } __Quiz inside your agent__

    ---

    `/grip` in Claude Code, Codex, Gemini CLI or Cursor. The agent relays, you answer.

    [:octicons-arrow-right-24: Coding agents](agents.md)

-   :material-shield-lock-outline:{ .lg } __Gate the agent's push__

    ---

    The Claude Code plugin denies `git push` until the diff has passed.

    [:octicons-arrow-right-24: The push gate](agents.md#claude-code-plugin)

-   :material-account-eye-outline:{ .lg } __Quiz the reviewer__

    ---

    `/grip-review main..feature` checks that the person approving understands it too.

    [:octicons-arrow-right-24: Review skill](agents.md#two-more-skills)

-   :material-pause-circle-outline:{ .lg } __Skip without hacks__

    ---

    `grip skip` for one push, `--hours 1` for a demo. No environment variables.

    [:octicons-arrow-right-24: Command line](cli.md#grip-skip-grip-resume)

-   :material-school-outline:{ .lg } __Practise on real problems__

    ---

    ROS 2, robotics and AI problems on Keep A Grip. Solve with any assistant, then get
    quizzed.

    [:octicons-arrow-right-24: Keep A Grip](keepagrip.md)

</div>

## Bring your own model

grip writes and grades the questions with whatever you already use. Nothing but the diff
and your answers leaves the machine, and only to the provider you choose.

| You have | Set in `.grip.toml` | Key needed |
| --- | --- | --- |
| Claude Code, Codex or Gemini CLI | `provider = "claude-code"`, `"codex"` or `"gemini"` | no |
| An Anthropic key | `provider = "anthropic"` (the default) | `ANTHROPIC_API_KEY` |
| A local model | `provider = "ollama"` and a `model` | no |
| Any OpenAI-compatible API | `provider = "openai"`, `base_url`, `model` | yes |

## See it run

<video class="grip-video" controls muted playsinline preload="none" poster="assets/features/grip-whats-new.jpg">
  <source src="assets/features/grip-whats-new.mp4" type="video/mp4">
</video>

Recorded against the real CLI with the offline scripted provider, so the questions are
canned. A real model writes them from your diff. What has and has not been measured about
the quiz is on the [honest numbers](honest-numbers.md) page.

<div class="grip-closing" markdown>

<p class="grip-closing__line">We let AI write the code.<br>We still own it.</p>

[Get started](getting-started.md){ .md-button }

</div>
