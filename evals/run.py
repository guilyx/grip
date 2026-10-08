"""Measure what the grip quiz separates, and how stable its grading is.

The quiz exists to tell apart a developer who understands a change from one who does not.
This harness checks that claim on fixed diffs with three answer arms:

* ``blank``: five empty answers. Must fail, or the pass mark is meaningless.
* ``generic``: five plausible developer answers written without reading any diff
  ("it refactors the code to be cleaner", "I would run the tests"). The gaming arm. The
  quiz is only useful if these fail.
* ``notes``: the author's notes on the change, written from the diff before any question
  was generated, picked per question by its focus (what, why, edge case, risk, test).
  A careful developer answering from memory. Should pass. Because the notes are not
  answers to the exact questions asked, this arm understates what a live developer would
  score, so the separation it shows is conservative.

Every arm is graded ``--repeats`` times so grading noise is visible, and the generated
questions are kept so their focus coverage can be inspected. Usage::

    python evals/run.py --provider fake                       # offline, deterministic
    python evals/run.py --provider anthropic --repeats 3      # a real model
    python evals/run.py --provider claude-code --cases clamp-add,rename-only

Results go to ``evals/snapshots/<provider>.json`` and a Markdown summary is printed. The
fake provider's snapshot is committed and checked by the test suite; real-model snapshots
are committed by hand when someone runs them, with the model and date inside the file.
"""

from __future__ import annotations

import argparse
import difflib
import json
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grip_hook import MAX_SCORE, QUESTION_COUNT, __version__
from grip_hook.config import load_config
from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, Question
from grip_hook.providers import get_provider
from grip_hook.providers.base import Provider

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
SNAPSHOTS_DIR = HERE / "snapshots"

ARMS = ("blank", "generic", "notes")
FOCUS_KEYS = ("what", "why", "edge", "risk", "test")

GENERIC: dict[str, str] = {
    "what": "It refactors the code to make it cleaner, more readable and easier to maintain.",
    "why": "The change was needed to fix a bug users reported and to improve reliability.",
    "edge": (
        "An edge case would be unexpected input such as null or empty values, which the new "
        "code should handle gracefully."
    ),
    "risk": (
        "Other parts of the codebase that call this code could be affected, so the callers "
        "should be checked for regressions."
    ),
    "test": (
        "I would verify it by running the existing test suite and adding unit tests for the "
        "new behaviour."
    ),
}
"""Answers that sound right for any diff. If these pass, the quiz can be gamed."""

_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("test", ("test", "verif", "valid", "check", "prove", "confirm", "measure", "reproduce")),
    (
        "risk",
        ("risk", "side", "affect", "impact", "depend", "caller", "regress", "secur", "compat"),
    ),
    ("edge", ("edge", "corner", "fail", "wrong", "invalid", "empty", "null", "boundary", "break")),
    ("why", ("why", "motiv", "reason", "rational", "design", "purpose", "instead", "chosen")),
    ("what", ("behav", "what", "does", "output", "return", "happen", "result", "effect")),
)


def classify(question: Question) -> str | None:
    """Which note (what, why, edge, risk, test) a question is closest to, by keywords."""
    for key, words in _KEYWORDS:
        text = question.focus.lower()
        if any(w in text for w in words):
            return key
    for key, words in _KEYWORDS:
        text = question.question.lower()
        if any(w in text for w in words):
            return key
    return None


@dataclass(frozen=True, slots=True)
class Case:
    """One fixture: a diff built from ``before/`` and ``after/`` plus the author's notes."""

    name: str
    title: str
    tags: tuple[str, ...]
    notes: dict[str, str]
    diff: Diff


def _files(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_text("utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def build_diff(case_dir: Path, description: str) -> Diff:
    """A unified diff of ``before/`` against ``after/``, in git's shape."""
    before, after = _files(case_dir / "before"), _files(case_dir / "after")
    names = sorted(set(before) | set(after))
    patches: list[str] = []
    stat_lines: list[str] = []
    insertions = deletions = 0
    for name in names:
        old, new = before.get(name, ""), after.get(name, "")
        old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
        body = list(difflib.unified_diff(old_lines, new_lines, f"a/{name}", f"b/{name}", n=3))
        if not body:
            continue
        added = sum(1 for line in body[2:] if line.startswith("+"))
        removed = sum(1 for line in body[2:] if line.startswith("-"))
        insertions += added
        deletions += removed
        header = f"diff --git a/{name} b/{name}\n"
        if name not in before:
            header += "new file mode 100644\n"
        elif name not in after:
            header += "deleted file mode 100644\n"
        patches.append(header + "".join(body))
        stat_lines.append(f" {name} | {added + removed} {'+' * added}{'-' * removed}")
    changed = [n for n in names if before.get(n, "") != after.get(n, "")]
    stat = "\n".join(stat_lines)
    stat += (
        f"\n {len(changed)} file{'s' if len(changed) != 1 else ''} changed, "
        f"{insertions} insertion{'s' if insertions != 1 else ''}(+), "
        f"{deletions} deletion{'s' if deletions != 1 else ''}(-)"
    )
    return Diff(description, stat, "".join(patches), tuple(changed))


def load_case(case_dir: Path) -> Case:
    """Read ``case.json`` and build the diff."""
    meta = json.loads((case_dir / "case.json").read_text("utf-8"))
    notes = {k: str(meta["notes"][k]) for k in FOCUS_KEYS}
    return Case(
        name=case_dir.name,
        title=str(meta["title"]),
        tags=tuple(meta.get("tags", [])),
        notes=notes,
        diff=build_diff(case_dir, str(meta.get("description", "staged changes"))),
    )


def load_cases(names: list[str] | None = None) -> list[Case]:
    """Every case under ``evals/cases``, or only ``names``."""
    dirs = sorted(p for p in CASES_DIR.iterdir() if (p / "case.json").is_file())
    if names:
        wanted = set(names)
        dirs = [d for d in dirs if d.name in wanted]
        missing = wanted - {d.name for d in dirs}
        if missing:
            raise SystemExit(f"unknown case(s): {', '.join(sorted(missing))}")
    return [load_case(d) for d in dirs]


def answers_for(arm: str, questions: list[Question], notes: dict[str, str]) -> list[str]:
    """The five answers an arm gives to these questions."""
    if arm == "blank":
        return [""] * len(questions)
    out: list[str] = []
    for i, q in enumerate(questions):
        key = classify(q)
        if arm == "generic":
            out.append(GENERIC[key or FOCUS_KEYS[i % len(FOCUS_KEYS)]])
        else:
            out.append(notes[key] if key else " ".join(notes.values()))
    return out


def _stats(values: list[int]) -> dict[str, float]:
    return {
        "mean": round(statistics.fmean(values), 2),
        "min": min(values),
        "max": max(values),
        "stdev": round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0,
    }


def run_case(
    provider: Provider,
    case: Case,
    *,
    arms: tuple[str, ...],
    repeats: int,
    difficulty: Difficulty,
    passing_score: int,
) -> dict[str, Any]:
    """Generate questions once, grade every arm ``repeats`` times."""
    started = time.perf_counter()
    question_set = provider.generate_questions(case.diff, difficulty)
    generated_in = time.perf_counter() - started
    questions = question_set.questions
    result: dict[str, Any] = {
        "name": case.name,
        "title": case.title,
        "tags": list(case.tags),
        "files": list(case.diff.files),
        "patch_bytes": len(case.diff.patch.encode("utf-8")),
        "summary": question_set.summary,
        "questions": [
            {"focus": q.focus, "question": q.question, "note": classify(q)} for q in questions
        ],
        "arms": {},
        "seconds": {"questions": round(generated_in, 2)},
    }
    for arm in arms:
        texts = answers_for(arm, questions, case.notes)
        answers = [Answer(question_index=i, text=t) for i, t in enumerate(texts)]
        totals: list[int] = []
        per_question: list[list[int]] = []
        graded_in = 0.0
        for _ in range(repeats):
            started = time.perf_counter()
            sheet = provider.grade(case.diff, questions, answers, difficulty)
            graded_in += time.perf_counter() - started
            sheet.grades.sort(key=lambda g: g.question_index)
            totals.append(sheet.total)
            per_question.append([g.score for g in sheet.grades])
        result["arms"][arm] = {
            "answers": texts,
            "scores": totals,
            "per_question": per_question,
            "passed": totals[0] >= passing_score,
            "always_passed": all(t >= passing_score for t in totals),
            **_stats(totals),
        }
        result["seconds"][f"grade_{arm}"] = round(graded_in, 2)
    return result


def summarise(cases: list[dict[str, Any]], arms: tuple[str, ...]) -> dict[str, Any]:
    """Pass rates and mean scores per arm, the separation, and grading spread."""
    summary: dict[str, Any] = {"arms": {}, "cases": len(cases)}
    for arm in arms:
        rows = [c["arms"][arm] for c in cases]
        summary["arms"][arm] = {
            "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 2),
            "mean_score": round(statistics.fmean(r["mean"] for r in rows), 1),
            "mean_stdev": round(statistics.fmean(r["stdev"] for r in rows), 2),
        }
    if {"notes", "generic"} <= set(arms):
        summary["separation"] = round(
            summary["arms"]["notes"]["mean_score"] - summary["arms"]["generic"]["mean_score"], 1
        )
    focus = [q["note"] or "other" for c in cases for q in c["questions"]]
    summary["focus_coverage"] = {k: focus.count(k) for k in (*FOCUS_KEYS, "other")}
    return summary


def markdown(snapshot: dict[str, Any]) -> str:
    """A short table for the terminal and for HONEST-NUMBERS."""
    arms = list(snapshot["summary"]["arms"])
    head = "| case | " + " | ".join(arms) + " |\n|---|" + "---:|" * len(arms) + "\n"
    rows = []
    for c in snapshot["cases"]:
        cells = []
        for arm in arms:
            r = c["arms"][arm]
            spread = f" ±{r['stdev']}" if r["stdev"] else ""
            cells.append(f"{r['mean']:.0f}{spread} {'pass' if r['passed'] else 'fail'}")
        rows.append(f"| {c['name']} | " + " | ".join(cells) + " |")
    s = snapshot["summary"]
    foot = (
        "| **pass rate** | "
        + " | ".join(f"**{s['arms'][a]['pass_rate']:.0%}**" for a in arms)
        + " |\n"
    )
    sep = s.get("separation")
    tail = (
        f"\nprovider `{snapshot['provider']}` model `{snapshot['model']}`, "
        f"{snapshot['repeats']} grading repeat(s), pass mark {snapshot['passing_score']}, "
        f"difficulty {snapshot['difficulty']}."
    )
    if sep is not None:
        tail += f" Separation (notes minus generic): {sep:+.1f} points."
    return head + "\n".join(rows) + "\n" + foot + tail + "\n"


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", default="fake")
    parser.add_argument("--model", default=None)
    parser.add_argument("--difficulty", default="normal", choices=[d.value for d in Difficulty])
    parser.add_argument("--passing-score", type=int, default=None)
    parser.add_argument("--repeats", type=int, default=1, help="gradings per arm (noise)")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--cases", default=None, help="comma-separated case names")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    unknown = set(arms) - set(ARMS)
    if unknown:
        parser.error(f"unknown arm(s): {', '.join(sorted(unknown))}")
    overrides = {"provider": args.provider, "model": args.model, "difficulty": args.difficulty}
    if args.passing_score is not None:
        overrides["passing_score"] = args.passing_score
    cfg = load_config(None, overrides=overrides)
    provider = get_provider(cfg)
    cases = load_cases(args.cases.split(",") if args.cases else None)
    if not cases:
        parser.error("no cases found")

    results = []
    for case in cases:
        print(f"{case.name} ...", file=sys.stderr, flush=True)
        results.append(
            run_case(
                provider,
                case,
                arms=arms,
                repeats=max(1, args.repeats),
                difficulty=cfg.difficulty,
                passing_score=cfg.passing_score,
            )
        )
    snapshot = {
        "grip_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": provider.name,
        "model": provider.model,
        "difficulty": cfg.difficulty.value,
        "passing_score": cfg.passing_score,
        "max_score": MAX_SCORE,
        "question_count": QUESTION_COUNT,
        "repeats": max(1, args.repeats),
        "cases": results,
        "summary": summarise(results, arms),
    }
    out = args.out or SNAPSHOTS_DIR / f"{provider.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2) + "\n", "utf-8")
    print(markdown(snapshot))
    print(f"written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
