"""Turn ``grip ask`` JSON into the pull request comment the GitHub Action posts.

    python scripts/action/comment.py QUESTIONS.json [--board-url URL] [--out FILE]
    python scripts/action/comment.py --status QUESTIONS.json   # questions, nothing-to-quiz
    python scripts/action/comment.py --marker                  # the hidden marker

Standard library only: it runs before anything but grip is installed. The questions are
written by a model from the diff, which anyone opening a pull request controls, so their
text is defused before it lands in a comment: no HTML, and no ``@`` mentions that would
notify people.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MARKER = "<!-- grip-action -->"
"""Hidden first line of grip's comment, so a new push updates it instead of adding one."""

_MENTION = re.compile(r"@(?=[A-Za-z0-9])")


def defuse(text: str) -> str:
    """Model text made safe for a comment: one line, no HTML, no mentions."""
    text = " ".join(str(text).split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # An empty HTML comment between @ and the name renders as nothing and notifies nobody.
    return _MENTION.sub("@<!-- -->", text)


def render(payload: dict[str, Any], board_url: str = "") -> str:
    """The Markdown body of the comment for a ``grip ask`` payload with questions."""
    diff = payload.get("diff") or {}
    files = diff.get("files") or []
    count = len(files)
    lines = [
        MARKER,
        "### grip: could you defend this change?",
        "",
    ]
    if summary := payload.get("summary"):
        lines += [f"> {defuse(summary)}", ""]
    lines += [
        f"Five questions written from this pull request's diff "
        f"({count} file{'' if count == 1 else 's'}). Nothing is graded here: the author can "
        "answer in a reply, or a reviewer can ask them in review.",
        "",
    ]
    for question in payload.get("questions") or []:
        index = question.get("index", len(lines))
        focus = defuse(question.get("focus", ""))
        label = f"**{focus}**: " if focus else ""
        lines.append(f"{index}. {label}{defuse(question.get('question', ''))}")
    lines.append("")
    if board_url:
        lines += [
            f"[The whiteboard of this change]({board_url}): a workflow artifact; download "
            "it and open `board.html` in a browser.",
            "",
        ]
    notes = ["To be graded on your own answers, run `grip quiz --branch` locally."]
    if diff.get("truncated"):
        notes.append("The diff was too large and was cut before the model read it.")
    lines.append(f"<sub>{' '.join(notes)} [What is grip?](https://guilyx.github.io/grip/)</sub>")
    return "\n".join(lines) + "\n"


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected the JSON object `grip ask` prints")
    return data


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("questions", nargs="?", type=Path, help="`grip ask` JSON output")
    parser.add_argument("--board-url", default="", help="link to the uploaded whiteboard")
    parser.add_argument("--out", type=Path, help="write the comment here, as UTF-8")
    parser.add_argument("--status", action="store_true", help="print the payload's status")
    parser.add_argument("--marker", action="store_true", help="print the hidden marker")
    args = parser.parse_args(argv)
    if args.marker:
        print(MARKER)
        return 0
    if args.questions is None:
        parser.error("QUESTIONS is required")
    payload = _load(args.questions)
    if args.status:
        print(payload.get("status", ""))
        return 0
    body = render(payload, args.board_url)
    if args.out is None:
        sys.stdout.write(body)
    else:
        args.out.write_text(body, "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
