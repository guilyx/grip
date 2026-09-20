# Security Policy

## What grip sends where

grip sends your **diff** (after the `exclude` globs are applied) and your **answers** to
the LLM provider you configure. Nothing else leaves your machine: no repository history,
no file contents outside the diff, no telemetry. Reports are written locally under
`.git/grip/`. Review `exclude` and `max_diff_bytes` in your configuration if your diffs
may contain secrets, and prefer a local provider such as Ollama for sensitive code.

The diff is treated as untrusted input in the prompts: the model is instructed to ignore
any instructions embedded in it. This is a mitigation, not a guarantee; grip never
executes anything the model returns.

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting on
the repository ("Security" tab, "Report a vulnerability") or email
erwin.lejeune15@gmail.com. You will get an acknowledgement within a few days and a fix or
mitigation plan as soon as possible after that.
