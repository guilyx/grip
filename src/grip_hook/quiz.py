"""The quiz itself: generate questions, ask them, grade, decide."""

from __future__ import annotations

from dataclasses import dataclass

from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from grip_hook import MAX_SCORE, POINTS_PER_QUESTION, QUESTION_COUNT
from grip_hook.config import Config
from grip_hook.errors import ProviderError
from grip_hook.git import Diff
from grip_hook.models import Answer, GradeSheet, QuestionSet, Report, Stage
from grip_hook.providers.base import Provider
from grip_hook.terminal import Terminal


@dataclass(frozen=True, slots=True)
class QuizOutcome:
    """Result of one quiz run."""

    report: Report

    @property
    def passed(self) -> bool:
        """Whether the developer reached the passing score."""
        return self.report.passed


def _score_style(score: int, passing: int) -> str:
    if score >= passing:
        return "bold green"
    if score >= passing - 15:
        return "bold yellow"
    return "bold red"


def render_intro(term: Terminal, diff: Diff, cfg: Config, stage: Stage) -> None:
    """Show what is about to be quizzed."""
    files = "\n".join(escape(f) for f in diff.files[:12])
    if len(diff.files) > 12:
        files += f"\n… and {len(diff.files) - 12} more"
    body = Text.from_markup(
        f"[bold]{QUESTION_COUNT} questions[/bold] about your "
        f"[bold]{escape(diff.description)}[/bold] ({stage.value}). "
        f"Pass mark: [bold]{cfg.passing_score}/{MAX_SCORE}[/bold].\n\n{files}"
    )
    term.console.print()  # pre-commit prints its own status on the current line
    term.console.print(Panel(body, title="grip", subtitle="keep a grip on your code", expand=False))


def ask_questions(term: Terminal, question_set: QuestionSet) -> list[Answer]:
    """Present each question and collect one line of answer for it."""
    console = term.console
    console.print(f"\n[dim]{escape(question_set.summary)}[/dim]\n")
    console.print("[dim]Answer in a sentence or two. Press Enter on an empty line to skip.[/dim]\n")
    answers: list[Answer] = []
    for i, q in enumerate(question_set.questions):
        console.print(
            f"[bold cyan]Q{i + 1}/{QUESTION_COUNT}[/bold cyan] [dim]({escape(q.focus)})[/dim] "
            f"{escape(q.question)}"
        )
        try:
            text = term.read_line("[bold]> [/bold]")
        except EOFError:
            text = ""
        answers.append(Answer(question_index=i, text=text.strip()))
        console.print()
    return answers


def render_result(
    term: Terminal, question_set: QuestionSet, sheet: GradeSheet, cfg: Config
) -> None:
    """Print the score table and verdict."""
    console = term.console
    table = Table(
        show_header=True, header_style="bold", expand=False, pad_edge=False, box=box.ROUNDED
    )
    table.add_column("#", justify="right")
    table.add_column("Focus")
    table.add_column("Score", justify="right")
    table.add_column("Feedback")
    for grade in sheet.grades:
        q = question_set.questions[grade.question_index]
        style = _score_style(grade.score * (MAX_SCORE // POINTS_PER_QUESTION), cfg.passing_score)
        table.add_row(
            str(grade.question_index + 1),
            escape(q.focus),
            f"[{style}]{grade.score}/{POINTS_PER_QUESTION}[/{style}]",
            escape(grade.feedback),
        )
    console.print(table)

    total = sheet.total
    passed = total >= cfg.passing_score
    style = _score_style(total, cfg.passing_score)
    headline = (
        f"[{style}]Grip Score: {total}/{MAX_SCORE}[/{style}]  "
        + ("[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]")
        + f" [dim](pass mark {cfg.passing_score})[/dim]"
    )
    console.print(Panel(Text.from_markup(f"{headline}\n\n{escape(sheet.verdict)}"), expand=False))


def run_quiz(
    *,
    diff: Diff,
    cfg: Config,
    provider: Provider,
    term: Terminal,
    stage: Stage,
) -> QuizOutcome:
    """Run the full quiz and return the outcome. Does not exit or raise on failure."""
    render_intro(term, diff, cfg, stage)

    with term.console.status("[dim]grip is reading your diff…[/dim]"):
        question_set = provider.generate_questions(diff, cfg.difficulty)
    if len(question_set.questions) != QUESTION_COUNT:
        raise ProviderError(
            f"provider returned {len(question_set.questions)} questions, expected {QUESTION_COUNT}"
        )

    answers = ask_questions(term, question_set)

    with term.console.status("[dim]grip is grading…[/dim]"):
        sheet = provider.grade(diff, question_set.questions, answers, cfg.difficulty)
    if len(sheet.grades) != QUESTION_COUNT:
        raise ProviderError(
            f"provider returned {len(sheet.grades)} grades, expected {QUESTION_COUNT}"
        )
    sheet.grades.sort(key=lambda g: g.question_index)

    render_result(term, question_set, sheet, cfg)

    total = sheet.total
    report = Report(
        stage=stage,
        provider=provider.name,
        model=provider.model,
        diff_digest=diff.digest,
        passing_score=cfg.passing_score,
        score=total,
        passed=total >= cfg.passing_score,
        summary=question_set.summary,
        questions=question_set.questions,
        answers=answers,
        grades=sheet.grades,
        verdict=sheet.verdict,
    )
    return QuizOutcome(report=report)


__all__ = ["QuizOutcome", "ask_questions", "render_intro", "render_result", "run_quiz"]
