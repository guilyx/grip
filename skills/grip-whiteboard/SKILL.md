---
name: grip-whiteboard
description: Open a whiteboard of the developer's change in their browser, with five questions pinned to its parts (why this way, a malicious actor, the data structure, where it fails, how you know), and let them defend it there. Use for /grip-whiteboard, "whiteboard this change", "let me defend this diff".
disable-model-invocation: true
allowed-tools: Bash(grip *)
---

# grip-whiteboard

The whiteboard defense: could the developer explain and defend this change if a senior
engineer pulled them aside? grip draws the change and asks. The developer answers in the
browser, not through you.

Arguments: `$ARGUMENTS` may be empty (staged changes), `--unpushed`, or `--range A..B`.
Pass them to `grip whiteboard` unchanged.

## Steps

1. Run `grip whiteboard $ARGUMENTS` in the background if you can (it waits up to twenty
   minutes for the developer). It opens the board in the developer's browser and prints
   its local address. If you cannot run a background command, ask the developer to run
   `grip whiteboard` in their own terminal and stop here.
2. Tell the developer, in one line, that the whiteboard is open in their browser and that
   they answer there.
3. Wait for the command to finish, then report its last line: the Grip Score and whether
   the change was defended.
4. If it passed, the push hook and `grip check` now accept this diff. If it did not, say so
   and suggest re-reading the parts the board marked in red.

## Rules

- Never open, fetch, or post to the whiteboard address yourself, and never write the
  developer's answers, in the browser or anywhere else. The score is only worth something
  if the answers are theirs.
- Never read `.git/grip/` to find rubrics.
- If `grip whiteboard` says the model could not draw a valid board, suggest `/grip`
  instead; the terminal quiz works for every diff.
