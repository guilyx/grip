"""Command-line interface."""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from grip_hook import MAX_SCORE, POINTS_PER_QUESTION, QUESTION_COUNT, __version__
from grip_hook.agent import (
    Gate,
    PendingQuiz,
    PendingStore,
    ask,
    claude_code_decision,
    grade,
    hook_request,
    parse_answers,
    questions_payload,
    report_payload,
)
from grip_hook.cache import CachedProvider, GenerationCache
from grip_hook.config import (
    AGENT_PROVIDERS,
    AUTO_PROVIDER,
    DEFAULT_EXCLUDES,
    DEFAULT_PASSING_SCORE,
    Config,
    auto_provider,
    describe,
    load_config,
    render_template,
)
from grip_hook.errors import GitError, GripError, NoTerminalError, ProviderError, QuizFailed
from grip_hook.git import NO_COMMITS, Diff, Git, PushedRef
from grip_hook.hooks import INSTALLABLE_STAGES, install, status, uninstall
from grip_hook.integrations import AGENTS, Action, agent, apply, detect
from grip_hook.memory import PassMemory, SkipState, local_time
from grip_hook.models import Difficulty, QuestionSet, Report, Stage
from grip_hook.platform import (
    DEFAULT_URL,
    URL_ENV,
    Client,
    Credentials,
    PlatformError,
    Problem,
    SolveState,
    attempt_payload,
    forget_credentials,
    load_credentials,
    problem_context,
    problem_markdown,
    read_state,
    save_credentials,
    write_state,
)
from grip_hook.providers import REGISTRY, get_provider
from grip_hook.providers.base import Provider
from grip_hook.quiz import run_quiz
from grip_hook.study import (
    DEFAULT_SINCE_DAYS,
    EXCLUDED_FIELDS,
    INCLUDED_FIELDS,
    Registry,
    build_export,
)
from grip_hook.terminal import open_terminal
from grip_hook.whiteboard import Board, Mode, board_data, generate_board
from grip_hook.whiteboard_server import IDLE_SECONDS, WhiteboardServer, render_page

_err = Console(stderr=True, highlight=False)
_out = Console(highlight=False)

SKIP_ENV = "GRIP_SKIP"
BINARY_ONLY = "only binary files changed, nothing to quiz."
DEBUG_ENV = "GRIP_DEBUG"
"""When truthy, a failed git command also prints the full command line and git's output."""


class _Failure(click.ClickException):
    """A :class:`GripError` re-raised so click exits with the right code and message."""

    def __init__(self, error: GripError) -> None:
        super().__init__(str(error))
        self.exit_code = error.exit_code  # type: ignore[misc]
        self.prefix = "grip" if isinstance(error, QuizFailed) else "grip error"
        self.detail = error.detail if isinstance(error, GitError) else ""

    def show(self, file: Any = None) -> None:
        _err.print(f"[bold red]{self.prefix}:[/bold red] {self.format_message()}")
        if self.detail and _truthy(os.environ.get(DEBUG_ENV)):
            _err.print(escape(self.detail), style="dim")


INTERRUPTED_EXIT = 130
"""Exit code after Ctrl-C: 128 + SIGINT, as shells report it."""


class _Interrupted(click.ClickException):
    """Ctrl-C, reported in one line with the conventional exit code instead of ``Aborted!``."""

    exit_code = INTERRUPTED_EXIT

    def __init__(self) -> None:
        super().__init__("stopped, nothing recorded.")

    def show(self, file: Any = None) -> None:
        _err.print(f"\n[yellow]grip:[/yellow] {self.format_message()}")


class _Group(click.Group):
    """Click group that translates grip's exceptions (and Ctrl-C) into exit codes.

    ``sections`` lists the help's command groups, ``(title, names)`` in order; commands in
    none of them are listed last, under "Other commands". Hidden commands never show.
    """

    def __init__(
        self, *args: Any, sections: tuple[tuple[str, tuple[str, ...]], ...] = (), **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.sections = sections

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except GripError as exc:
            raise _Failure(exc) from exc
        except KeyboardInterrupt:
            raise _Interrupted() from None

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        if not self.sections:
            super().format_commands(ctx, formatter)
            return
        visible: dict[str, click.Command] = {}
        for name in self.list_commands(ctx):
            command = self.get_command(ctx, name)
            if command is not None and not command.hidden:
                visible[name] = command
        if not visible:
            return
        width = max(len(name) for name in visible)
        limit = formatter.width - 6 - width
        placed: set[str] = set()
        for title, names in (*self.sections, ("Other commands", tuple(visible))):
            listed = [name for name in names if name in visible and name not in placed]
            placed.update(listed)
            if not listed:
                continue
            with formatter.section(title):
                # Padded to one width so the help text lines up across sections.
                formatter.write_dl(
                    [(n.ljust(width), visible[n].get_short_help_str(limit)) for n in listed]
                )


_SECTIONS = (
    ("Core commands", ("init", "quiz", "whiteboard", "status", "skip", "resume", "last")),
    ("Setup and upkeep", ("install", "uninstall", "agents", "check", "config", "forget")),
    ("More", ("study", "platform")),
)

_EXAMPLES = """\
\b
Examples:
  grip init              set this repository up: .grip.toml, the hook, agent files
  grip quiz              quiz the staged changes now
  grip quiz --branch     quiz the whole branch since main: its pull request
  grip whiteboard        draw the change and defend it in the browser
  grip status            what is installed, what passed, what is paused
  grip skip              let the next commit or push through without a quiz

Commands for tools and agents (hook, agent-hook, statusline, ask, grade) are not listed:
https://guilyx.github.io/grip/cli/#for-tools-and-agents
"""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _stage_choice() -> click.Choice[str]:
    return click.Choice([s.value for s in INSTALLABLE_STAGES])


def _provider_choice() -> click.Choice[str]:
    """Every provider name, ``auto`` included, as the configuration accepts them."""
    return click.Choice(sorted({*REGISTRY, AUTO_PROVIDER}), case_sensitive=False)


def _apply_options(
    fn: Callable[..., Any], decorators: list[Callable[..., Any]]
) -> Callable[..., Any]:
    for decorator in reversed(decorators):
        fn = decorator(fn)
    return fn


def _grading_options() -> list[Callable[..., Any]]:
    """Options that matter when answers are graded: the pass mark and who grades."""
    return [
        click.option(
            "--passing-score",
            type=click.IntRange(0, MAX_SCORE),
            default=None,
            help=f"Minimum Grip Score out of {MAX_SCORE} to pass.",
        ),
        click.option(
            "--provider",
            type=_provider_choice(),
            default=None,
            help="LLM provider. auto: an installed coding agent, else anthropic.",
        ),
        click.option("--model", default=None, help="Model identifier for the provider."),
    ]


def _report_option() -> Callable[..., Any]:
    return click.option(
        "--report",
        "report_path",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help="Also write the JSON report to this path.",
    )


def grade_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Options for `grip grade`: the questions already exist, so no --fresh or --difficulty."""
    return _apply_options(fn, [*_grading_options(), _report_option()])


def quiz_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Options shared by every command that can run a quiz."""
    decorators = [
        *_grading_options(),
        click.option(
            "--fresh",
            "cache_hours",
            flag_value=0.0,
            default=None,
            type=float,
            help="Ask the model for new questions even if this diff was quizzed recently.",
        ),
        click.option(
            "--difficulty",
            type=click.Choice([d.value for d in Difficulty], case_sensitive=False),
            default=None,
            help="How demanding the questions are.",
        ),
        _report_option(),
    ]
    return _apply_options(fn, decorators)


def _config(git: Git | None, **overrides: Any) -> Config:
    root = git.root() if git else None
    return load_config(root, overrides=overrides)


def _skip(reason: str) -> None:
    _err.print(f"[yellow]grip:[/yellow] {reason}")


def _provider(git: Git, cfg: Config) -> Provider:
    """The configured provider, answering from the question cache unless it is off."""
    provider = get_provider(cfg)
    if cfg.cache_hours <= 0:
        return provider

    def reused(kind: str, written: datetime) -> None:
        minutes = max(1, round((datetime.now(UTC) - written).total_seconds() / 60))
        ago = f"{minutes} min" if minutes < 120 else f"{round(minutes / 60)} h"
        what = "board" if kind == "board" else "questions"
        _err.print(
            f"[dim]grip: same diff as {ago} ago, so the same {what}; only grading calls the "
            "model. --fresh asks for new ones.[/dim]"
        )

    return CachedProvider(provider, GenerationCache(git.git_dir(), cfg.cache_hours), reused)


def _run(
    git: Git,
    diff: Diff,
    cfg: Config,
    stage: Stage,
    report_path: Path | None,
) -> int:
    """Shared driver for ``quiz`` and ``hook``. Returns the process exit code."""
    if diff.is_empty or diff.binary_only:
        _skip(BINARY_ONLY if diff.binary_only else "nothing to quiz, no changes found.")
        return 0

    memory = PassMemory(git.git_dir(), cfg.remember_passes_hours)
    if memory.has_passed(diff.digest):
        _skip("this exact diff already passed recently, skipping the quiz.")
        return 0

    if cfg.hook_mode == "whiteboard" and stage is not Stage.MANUAL:
        if _browser_available():
            return _run_whiteboard(git, diff, cfg, stage, memory=memory, report_path=report_path)
        _skip("hook_mode is whiteboard, but no browser can be opened here; asking in the terminal.")

    try:
        term = open_terminal()
    except NoTerminalError as exc:
        if stage is Stage.MANUAL:
            # Typed by hand, so skipping would only hide that nothing was asked.
            raise NoTerminalError(
                f"{exc} Without one, `grip ask` and `grip grade` run the quiz as JSON, "
                "and `grip whiteboard` asks in the browser."
            ) from exc
        if cfg.require_tty:
            raise
        _skip(f"{exc} Skipping the quiz. Set require_tty = true to fail instead.")
        return 0

    try:
        provider = _provider(git, cfg)
        outcome = run_quiz(diff=diff, cfg=cfg, provider=provider, term=term, stage=stage)
    except ProviderError as exc:
        return _provider_failed(cfg, exc)
    finally:
        term.close()
    return _decide(git, memory, outcome.report, report_path, "Re-read your change and try again.")


def _provider_failed(cfg: Config, exc: ProviderError) -> int:
    """Let the push through when ``fail_open`` says so; otherwise block with the way out."""
    if cfg.fail_open:
        _skip(f"provider error, letting this through because fail_open is set: {exc}")
        return 0
    raise ProviderError(
        f"{exc}\nNothing was sent upstream. To bypass grip once, set {SKIP_ENV}=1 "
        "(or use git's --no-verify)."
    ) from exc


def _decide(
    git: Git, memory: PassMemory, report: Report, report_path: Path | None, retry_hint: str
) -> int:
    """Record a graded quiz; exit 0 on a pass, raise :class:`QuizFailed` below the mark."""
    saved = _record(git, memory, report)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.model_dump_json(indent=2), "utf-8")
    if report.passed:
        memory.record_pass(report.diff_digest)
        return 0
    raise QuizFailed(
        f"Grip Score {report.score}/{MAX_SCORE} is below the pass mark of "
        f"{report.passing_score}. {retry_hint} `grip last` shows the feedback again "
        f"(report: {_repo_path(git, saved)})."
    )


def _browser_available() -> bool:
    """Whether ``webbrowser`` can open a page someone will see (not over SSH, not headless)."""
    if os.environ.get("BROWSER"):
        return True
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return False
    try:
        webbrowser.get()
    except webbrowser.Error:
        return False
    return True


def _run_whiteboard(
    git: Git,
    diff: Diff,
    cfg: Config,
    stage: Stage,
    *,
    memory: PassMemory,
    report_path: Path | None,
) -> int:
    """The hook, defended on the whiteboard instead of in the terminal."""
    try:
        provider = _provider(git, cfg)
        with _err.status("grip is drawing the whiteboard..."):
            board = generate_board(provider, diff, cfg.difficulty)
        report = _defend_board(board, diff, cfg, provider, stage)
    except ProviderError as exc:
        return _provider_failed(cfg, exc)
    code = _decide(git, memory, report, report_path, "The page shows what each answer missed.")
    _err.print(f"[green]grip:[/green] Grip Score {report.score}/{MAX_SCORE}. Defended.")
    return code


def _record(git: Git, memory: PassMemory, report: Report) -> Path:
    """Persist a graded quiz: last-report.json, history.jsonl and the study registry.

    Returns the path of ``last-report.json``. The registry lives in the user's data
    directory, so a failure to write it (read-only home, sandbox) never blocks the quiz.
    """
    saved = memory.save_report(report)
    memory.append_history(report)
    with contextlib.suppress(OSError):
        Registry().add(git.git_dir())
    return saved


def _pretty_path(path: Path) -> str:
    """Relative to the working directory when the path is inside it, else absolute."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _repo_path(git: Git, path: Path) -> str:
    """``.git/hooks/pre-push``: relative to the repository root when inside it.

    Shorter than an absolute path, so a table never cuts it to ``/tmp/clau…``, and the
    same from any subdirectory. Paths outside the repository go through :func:`_pretty_path`.
    """
    try:
        # Directories resolved on both sides (/var vs /private/var on macOS, 8.3 names on
        # Windows); the file itself is not, so a symlinked hook keeps its own name.
        where = path.parent.resolve() / path.name
        return where.relative_to(git.root().resolve()).as_posix()
    except (ValueError, OSError, GripError):
        return _pretty_path(path)


@click.group(
    cls=_Group,
    sections=_SECTIONS,
    epilog=_EXAMPLES,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, prog_name="grip")
def cli() -> None:
    """Keep a grip on your code: a quiz before every commit or push."""


_SCOPES = ("staged", "unpushed", "branch")


def _diff_options(
    staged_help: str = "Staged changes (the default).",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``--staged``, ``--unpushed``, ``--branch`` and ``--range``, at most one of them.

    The command receives ``mode`` (``None`` when no scope flag was given; most commands
    then use the staged changes) and ``rev_range``. Two scope flags at once are a usage
    error rather than the last one silently winning.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(
            *args: Any,
            staged: bool,
            unpushed: bool,
            branch: bool,
            rev_range: str | None,
            **kwargs: Any,
        ) -> Any:
            given = (staged, unpushed, branch)
            chosen = [name for name, on in zip(_SCOPES, given, strict=True) if on]
            flags = [f"--{name}" for name in chosen] + (["--range"] if rev_range else [])
            if len(flags) > 1:
                raise click.UsageError(
                    f"{' and '.join(flags)} cannot be used together; pick one scope.",
                    ctx=click.get_current_context(silent=True),
                )
            mode = chosen[0] if chosen else None
            return fn(*args, mode=mode, rev_range=rev_range, **kwargs)

        decorators = [
            click.option("--staged", is_flag=True, help=staged_help),
            click.option("--unpushed", is_flag=True, help="Commits not on any remote."),
            click.option("--branch", is_flag=True, help="The branch since it left main: its PR."),
            click.option(
                "--range",
                "rev_range",
                default=None,
                help="An explicit BASE..HEAD range (BASE...HEAD: since the merge base).",
            ),
        ]
        for decorator in reversed(decorators):
            wrapper = decorator(wrapper)
        return wrapper

    return decorate


@cli.command()
@_diff_options()
@quiz_options
def quiz(
    mode: str | None,
    rev_range: str | None,
    report_path: Path | None,
    **overrides: Any,
) -> None:
    """Run the quiz now, without a hook. Quizzes the staged changes unless told otherwise."""
    git = Git()
    cfg = _config(git, **overrides)
    diff = _select_diff(git, cfg, mode, rev_range)
    if diff.is_empty:
        _skip(f"nothing to quiz: {_nothing_found(git, cfg, mode, rev_range, 'quiz')[0]}")
        return
    sys.exit(_run(git, diff, cfg, Stage.MANUAL, report_path))


def _branch_diff(git: Git, cfg: Config) -> Diff | None:
    """The current branch since its merge base with the default branch: its pull request.

    ``None`` when there is no default branch to compare with.
    """
    found = git.branch_base()
    if found is None:
        return None
    default, base = found
    branch = git.run("rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "HEAD"
    return git.range_diff(
        base, "HEAD", cfg.exclude, cfg.max_diff_bytes, description=f"{branch} vs {default}"
    )


def _parse_range(rev_range: str) -> tuple[str, str, bool]:
    """Split ``BASE..HEAD`` or ``BASE...HEAD`` into ``(base, head, three_dot)``.

    Two dots diff ``BASE`` against ``HEAD``; three dots diff from their merge base, as git
    does, which is what a pull request shows. A missing ``HEAD`` means ``HEAD``.
    """
    base, sep, rest = rev_range.strip().partition("..")
    three_dot = rest.startswith(".")
    head = rest[1:] if three_dot else rest
    if not sep or not base or head.startswith(".") or ".." in head:
        raise click.BadParameter(
            f"expected BASE..HEAD or BASE...HEAD, got {rev_range!r}", param_hint="--range"
        )
    return base, head or "HEAD", three_dot


def _select_diff(git: Git, cfg: Config, mode: str | None, rev_range: str | None) -> Diff:
    """The diff for ``--staged`` (default), ``--unpushed``, ``--branch`` or ``--range``."""
    if rev_range:
        base, head, three_dot = _parse_range(rev_range)
        if three_dot:
            return git.merge_base_diff(base, head, cfg.exclude, cfg.max_diff_bytes)
        # The refs as typed: the header reads main..HEAD, not two hashes.
        return git.range_diff(
            base, head, cfg.exclude, cfg.max_diff_bytes, description=f"{base}..{head}"
        )
    if mode == "unpushed":
        head_sha = git.run("rev-parse", "HEAD").strip()
        base_sha = git.unpushed_base(head_sha)
        if base_sha is None:
            return Diff("unpushed commits", "", "", ())
        description = git.describe_commits(
            base_sha, head_sha, branch=git.current_branch(), unpushed=True
        )
        return git.range_diff(base_sha, head_sha, cfg.exclude, cfg.max_diff_bytes, description)
    if mode == "branch":
        diff = _branch_diff(git, cfg)
        if diff is None:
            if not git.has_commits():
                raise GitError(NO_COMMITS)
            raise GripError(
                "no default branch to compare with (no origin/HEAD, main or master); "
                "use --range BASE..HEAD"
            )
        return diff
    return git.staged_diff(cfg.exclude, cfg.max_diff_bytes)


@dataclasses.dataclass(frozen=True, slots=True)
class _Elsewhere:
    """What there is to quiz besides the diff that came out empty."""

    staged: int
    """Files with staged changes."""
    modified: int
    """Tracked files changed in the working tree but not staged."""
    untracked: int
    unpushed: int
    """Commits not on any remote."""
    branch: int
    """Commits on the current branch since it left the default branch."""
    default: str | None
    """The default branch ``branch`` counts from."""

    def hint(self, tries: list[str]) -> dict[str, Any]:
        """The machine-readable side, for ``grip ask``."""
        return {
            "staged_files": self.staged,
            "modified_files": self.modified,
            "untracked_files": self.untracked,
            "unpushed_commits": self.unpushed,
            "branch_commits": self.branch,
            "try": tries,
        }


def _elsewhere(git: Git, exclude: tuple[str, ...]) -> _Elsewhere:
    found = git.branch_base()
    return _Elsewhere(
        staged=len(git.staged_files(exclude)),
        modified=len(git.unstaged_files(exclude)),
        untracked=len(git.untracked_files(exclude)),
        unpushed=git.count_commits("HEAD", "--not", "--remotes"),
        branch=git.count_commits(f"{found[1]}..HEAD") if found else 0,
        default=found[0] if found else None,
    )


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'' if n == 1 else 's'}"


def _nothing_found(
    git: Git, cfg: Config, mode: str | None, rev_range: str | None, command: str
) -> tuple[str, dict[str, Any]]:
    """Why the selected diff is empty, what there is instead and what to run.

    Returns the sentence for people and the same as data for ``grip ask``: right after
    ``grip init`` nothing is staged and the new files are untracked, which a bare "no
    changes found" does not say. ``mode`` is ``None`` when no scope flag was given, which
    for these commands means the staged changes.
    """
    mode = mode or "staged"
    found = _elsewhere(git, cfg.exclude)
    if rev_range:
        return f"no changes in {rev_range}.", found.hint([])
    grip = f"grip {command}"
    offers: list[str] = []
    tries: list[str] = []
    if mode != "staged" and found.staged:
        offers.append(f"{_count(found.staged, 'staged file')}: try `{grip}`")
        tries.append(grip)
    work = [
        _count(n, kind)
        for n, kind in ((found.untracked, "untracked file"), (found.modified, "modified file"))
        if n
    ]
    if work:
        them = "it" if found.untracked + found.modified == 1 else "them"
        then = "first" if mode == "staged" else f"then `{grip}`"
        offers.append(f"{' and '.join(work)}: `git add` {them} {then}")
        tries.append("git add")
    if mode != "unpushed" and found.unpushed:
        branch_too = mode != "branch" and found.branch > 0
        offers.append(
            f"{_count(found.unpushed, 'unpushed commit')}: try `{grip} --unpushed`"
            + (" or `--branch`" if branch_too else "")
        )
        tries += [f"{grip} --unpushed", *([f"{grip} --branch"] if branch_too else [])]
    elif mode != "branch" and found.branch:
        offers.append(
            f"{_count(found.branch, 'commit')} since {found.default}: try `{grip} --branch`"
        )
        tries.append(f"{grip} --branch")
    lead = {
        "unpushed": "no unpushed commits",
        "branch": f"no changes on this branch since {found.default or 'the default branch'}",
    }.get(mode, "nothing staged")
    if not offers:
        pushed = "" if mode == "unpushed" else " and every commit is pushed"
        text = f"{lead}; the working tree is clean{pushed}."
    elif len(offers) == 1:
        text = f"{lead}, but {offers[0]}."
    else:
        text = f"{lead}. " + "; ".join(offers) + "."
    return text, found.hint(tries)


def _emit(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, indent=2))


@cli.command(name="ask", hidden=True)
@_diff_options()
@quiz_options
def ask_cmd(
    mode: str | None, rev_range: str | None, report_path: Path | None, **overrides: Any
) -> None:
    """Write the questions as JSON for a coding agent to relay (no terminal needed).

    Asks about the staged changes unless a scope flag says otherwise.
    The rubrics stay in .git/grip/pending.json. Follow up with `grip grade`.
    """
    git = Git()
    cfg = _config(git, **overrides)
    diff = _select_diff(git, cfg, mode, rev_range)
    if diff.is_empty:
        message, hint = _nothing_found(git, cfg, mode, rev_range, "ask")
        _emit({"status": "nothing-to-quiz", "message": message, "hint": hint})
        return
    if diff.binary_only:
        _emit({"status": "nothing-to-quiz", "message": "only binary files changed"})
        return
    memory = PassMemory(git.git_dir(), cfg.remember_passes_hours)
    if memory.has_passed(diff.digest):
        _emit({"status": "already-passed", "message": "this exact diff passed recently"})
        return
    pending = ask(diff, cfg, _provider(git, cfg), Stage.MANUAL)
    PendingStore(git.git_dir()).save(pending)
    _emit(questions_payload(pending, cfg))


@cli.command(name="grade", hidden=True)
@click.option(
    "--answers",
    "answers_path",
    type=click.Path(dir_okay=False, allow_dash=True, path_type=Path),
    required=True,
    help='JSON file with ["answer 1", ...] or {"answers": [...]}; "-" reads stdin.',
)
@grade_options
def grade_cmd(answers_path: Path, report_path: Path | None, **overrides: Any) -> None:
    """Grade answers to the questions from `grip ask` and remember a pass."""
    git = Git()
    cfg = _config(git, **overrides)
    store = PendingStore(git.git_dir())
    pending = store.load()
    if pending is None:
        raise GripError("no pending quiz; run `grip ask` first")
    text = sys.stdin.read() if str(answers_path) == "-" else answers_path.read_text("utf-8")
    answers = parse_answers(text)
    report = grade(pending, answers, cfg, get_provider(cfg))
    memory = PassMemory(git.git_dir(), cfg.remember_passes_hours)
    saved = _record(git, memory, report)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.model_dump_json(indent=2), "utf-8")
    store.clear()
    _emit(report_payload(report, saved))
    if report.passed:
        memory.record_pass(report.diff_digest)
        return
    sys.exit(QuizFailed.exit_code)


MAX_ANSWER = 4000


@cli.command(name="whiteboard")
@_diff_options()
@click.option(
    "--interview",
    is_flag=True,
    help="For the person asking: show the rubrics and tick what you hear. Nothing is recorded.",
)
@click.option(
    "--export",
    "export_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the board to a standalone HTML file instead. No rubrics, no grading.",
)
@click.option("--no-open", is_flag=True, help="Print the address instead of opening a browser.")
@click.option(
    "--port",
    type=click.IntRange(0, 65535),
    default=0,
    help="Port on 127.0.0.1 (default: any free port).",
)
@quiz_options
def whiteboard_cmd(
    *,
    mode: str | None,
    rev_range: str | None,
    interview: bool,
    export_path: Path | None,
    no_open: bool,
    port: int,
    report_path: Path | None,
    **overrides: Any,
) -> None:
    """Draw the change as a whiteboard and defend it in the browser.

    The parts of the change, how they connect and five questions pinned to them come from
    your diff (the staged changes unless a scope flag says otherwise). Answers are graded
    like `grip quiz`, and a pass counts for the push hook.
    """
    git = Git()
    cfg = _config(git, **overrides)
    diff = _select_diff(git, cfg, mode, rev_range)
    if diff.is_empty:
        _skip(f"nothing to draw: {_nothing_found(git, cfg, mode, rev_range, 'whiteboard')[0]}")
        return
    if diff.binary_only:
        _skip("only binary files changed, nothing to draw.")
        return
    memory = PassMemory(git.git_dir(), cfg.remember_passes_hours)
    defending = not interview and export_path is None
    if defending and memory.has_passed(diff.digest):
        _skip("this exact diff already passed recently; nothing to defend.")
        return
    provider = _provider(git, cfg)
    with _err.status("grip is drawing the whiteboard..."):
        board = generate_board(provider, diff, cfg.difficulty)
    board_mode: Mode = "export" if export_path else "interview" if interview else "defend"
    page = render_page(
        board_data(
            board,
            mode=board_mode,
            passing_score=cfg.passing_score,
            description=diff.description,
            files=list(diff.files),
        )
    )
    if export_path is not None:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(page, "utf-8")
        _err.print(f"[green]grip:[/green] wrote the board to {_pretty_path(export_path)}")
        return

    if interview:

        def tick(payload: dict[str, Any]) -> dict[str, Any]:
            return _interview_result(board, payload, cfg.passing_score)

        result = _serve_board(page, tick, port=port, open_browser=not no_open)
        _out.print(f"Interview score: {result['score']}/{MAX_SCORE} (not recorded)")
        return
    report = _defend_board(
        board, diff, cfg, provider, Stage.MANUAL, port=port, open_browser=not no_open, page=page
    )
    _decide(git, memory, report, report_path, "The page shows what each answer missed.")
    _out.print(f"[green]grip:[/green] Grip Score {report.score}/{MAX_SCORE}. Defended.")


def _serve_board(
    page: str,
    submit: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    port: int = 0,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Serve ``page`` until it is handed in; return what ``submit`` answered."""
    server = WhiteboardServer(page, submit, port=port)
    click.echo(server.url)
    _err.print(
        "[bold]grip:[/bold] the whiteboard is open at the address above. "
        "Answer there; this waits until you hand in (Ctrl-C to stop)."
    )
    if open_browser:
        webbrowser.open(server.url)
    try:
        result = server.serve()
    except KeyboardInterrupt:
        raise GripError("stopped before handing in; nothing was recorded.") from None
    if result is None:
        minutes = IDLE_SECONDS // 60
        raise GripError(f"no answers after {minutes} idle minutes; nothing was recorded.")
    return result


def _defend_board(
    board: Board,
    diff: Diff,
    cfg: Config,
    provider: Provider,
    stage: Stage,
    *,
    port: int = 0,
    open_browser: bool = True,
    page: str | None = None,
) -> Report:
    """Serve ``board`` for a defense and return the graded report."""
    if page is None:
        page = render_page(
            board_data(
                board,
                mode="defend",
                passing_score=cfg.passing_score,
                description=diff.description,
                files=list(diff.files),
            )
        )
    pending = PendingQuiz(
        stage=stage,
        provider=provider.name,
        model=provider.model,
        description=diff.description,
        stat=diff.stat,
        patch=diff.patch,
        files=list(diff.files),
        truncated=diff.truncated,
        question_set=QuestionSet(summary=board.summary, questions=board.questions()),
    )
    graded: list[Report] = []

    def submit(payload: dict[str, Any]) -> dict[str, Any]:
        report = grade(pending, _answers_from(payload), cfg, provider)
        graded.append(report)
        return {
            "score": report.score,
            "passed": report.passed,
            "passing_score": report.passing_score,
            "verdict": report.verdict,
            "grades": [{"score": g.score, "feedback": g.feedback} for g in report.grades],
            "recorded": True,
        }

    _serve_board(page, submit, port=port, open_browser=open_browser)
    return graded[-1]


def _answers_from(payload: dict[str, Any]) -> list[str]:
    """The ``answers`` of a whiteboard submission, padded to one per question."""
    answers = payload.get("answers")
    if (
        not isinstance(answers, list)
        or len(answers) > QUESTION_COUNT
        or not all(isinstance(a, str) and len(a) <= MAX_ANSWER for a in answers)
    ):
        raise ValueError(
            f"answers must be at most {QUESTION_COUNT} strings of up to {MAX_ANSWER} characters"
        )
    return [a.strip() for a in answers] + [""] * (QUESTION_COUNT - len(answers))


def _interview_result(board: Board, payload: dict[str, Any], passing_score: int) -> dict[str, Any]:
    """Score an interview from the rubric points the person asking ticked."""
    ticks = payload.get("ticks")
    shapes = [len(p.rubric) for p in board.pins]
    if (
        not isinstance(ticks, list)
        or [len(t) if isinstance(t, list) else -1 for t in ticks] != shapes
        or not all(isinstance(x, bool) for t in ticks for x in t)
    ):
        raise ValueError("ticks must be one list of booleans per question, one per rubric point")
    scores = [round(POINTS_PER_QUESTION * sum(t) / len(t)) for t in ticks]
    grades = [
        {"score": score, "feedback": f"Covered {sum(t)} of {len(t)} points."}
        for score, t in zip(scores, ticks, strict=True)
    ]
    total = sum(scores)
    return {
        "score": total,
        "passed": total >= passing_score,
        "passing_score": passing_score,
        "verdict": "Scored by the person asking, from the rubric points they heard.",
        "grades": grades,
        "recorded": False,
    }


@cli.command(name="check")
@_diff_options("Staged changes. The default is what the installed hook quizzes.")
def check_cmd(mode: str | None, rev_range: str | None) -> None:
    """Exit 0 when the diff already passed a quiz (or there is nothing to quiz), else 1.

    Without a scope flag it checks what the installed hooks quiz: the unpushed commits for
    a pre-push hook, the staged changes for a pre-commit hook, both for both. With no hook
    installed it checks the staged changes.
    """
    git = Git()
    cfg = _config(git)
    modes: list[str | None] = [mode] if mode or rev_range else list(_hook_scopes(git))
    verdicts = [v for m in modes if (v := _check(git, cfg, m, rev_range)) is not None]
    if not verdicts:
        checked = " and ".join(_scope_name(m, rev_range) for m in modes)
        _out.print(f"[green]grip:[/green] ok ({escape(checked)})")
        return
    for verdict in verdicts:
        _err.print(f"[bold red]grip:[/bold red] {escape(verdict)}")
    sys.exit(1)


def _hook_scopes(git: Git) -> tuple[str, ...]:
    """What the installed hooks quiz: ``staged`` for pre-commit, ``unpushed`` for pre-push.

    ``("staged",)`` when neither hook is installed.
    """
    found = tuple(
        scope
        for stage, scope in ((Stage.PRE_COMMIT, "staged"), (Stage.PRE_PUSH, "unpushed"))
        if status(git, stage).active
    )
    return found or ("staged",)


def _scope_name(mode: str | None, rev_range: str | None) -> str:
    """What ``grip check`` looked at, as said back: ``unpushed commits``, ``main..HEAD``."""
    if rev_range:
        return rev_range
    return {"unpushed": "unpushed commits", "branch": "the branch"}.get(
        mode or "", "staged changes"
    )


def _check(git: Git, cfg: Config, mode: str | None, rev_range: str | None) -> str | None:
    """``None`` when the diff may go through, otherwise why it may not."""
    diff = _select_diff(git, cfg, mode, rev_range)
    if diff.is_empty or diff.binary_only:  # the hook does not quiz these either
        return None
    if PassMemory(git.git_dir(), cfg.remember_passes_hours).has_passed(diff.digest):
        return None
    if rev_range:
        subject = f"the range {rev_range} has"
    else:
        what = {"unpushed": "unpushed commits", "branch": "branch's commits"}.get(
            mode or "", "staged changes"
        )
        subject = f"the {what} have"
    return f"{subject} not passed a grip quiz yet ({_files(diff)})."


@cli.command(name="agent-hook", hidden=True)
@click.argument("agent", type=click.Choice(["claude-code"]))
@click.option(
    "--gate",
    type=click.Choice(["push", "commit", "both"]),
    default="push",
    show_default=True,
    help="Which git commands need a passed quiz.",
)
def agent_hook_cmd(agent: str, gate: Gate) -> None:
    """Claude Code PreToolUse hook: deny `git push` until the diff passed a quiz.

    Reads the hook payload from stdin and prints a decision when one is needed. Honours
    GRIP_SKIP and CI like the git hooks do. Always exits 0 so a missing repository or a
    broken payload never blocks the agent by accident.
    """
    if _truthy(os.environ.get(SKIP_ENV)) or _truthy(os.environ.get("CI")):
        return
    request = hook_request(sys.stdin.read(), gate)
    if request is None:
        return
    mode, cwd = request
    try:
        git = Git(cwd)
        if SkipState(git.git_dir()).consume():
            return
        verdict = _check(git, _config(git), mode, None)
    except GripError:
        return
    if verdict is None:
        return
    _emit(
        claude_code_decision(
            f"grip: {verdict} Ask the developer to run /grip"
            f"{' --unpushed' if mode == 'unpushed' else ''} and answer the questions "
            f"themselves, then retry. Do not answer for them. GRIP_SKIP=1 bypasses once."
        )
    )


@cli.command(hidden=True)
@click.argument("stage", type=_stage_choice())
@click.argument("hook_args", nargs=-1)
@quiz_options
def hook(
    stage: str, hook_args: tuple[str, ...], report_path: Path | None, **overrides: Any
) -> None:
    """Entry point for git hooks. Called by the installed hook or by pre-commit.

    STAGE is pre-commit or pre-push. Extra arguments are the ones git passes to the hook
    (remote name and URL for pre-push). Under the pre-commit framework the pushed range
    is read from PRE_COMMIT_FROM_REF / PRE_COMMIT_TO_REF.
    """
    if _truthy(os.environ.get(SKIP_ENV)):
        _skip(f"{SKIP_ENV} is set, skipping the quiz.")
        return
    if _truthy(os.environ.get("CI")):
        _skip("CI environment detected, skipping the quiz.")
        return

    git = Git()
    if reason := SkipState(git.git_dir()).consume():
        _skip(f"{reason}, skipping the quiz. `grip resume` cancels a pause.")
        return
    cfg = _config(git, **overrides)
    stage_enum = Stage(stage)
    if stage_enum is Stage.PRE_COMMIT:
        diff = git.staged_diff(cfg.exclude, cfg.max_diff_bytes)
    else:
        diff = _push_diff(git, cfg, hook_args)
        if diff.is_empty:
            _skip(_nothing_pushed(git, cfg))
            return
    sys.exit(_run(git, diff, cfg, stage_enum, report_path))


def _nothing_pushed(git: Git, cfg: Config) -> str:
    """Why a push has nothing to quiz, and what to quiz when the branch was never quizzed.

    A push sends nothing new when its commits already reached the remote some other way
    (pushed with --no-verify, from another clone, under another branch name). The branch
    still differs from the default branch, and that diff may never have passed.
    """
    nothing = "nothing to quiz: this push sends no commits the remote does not have."
    try:
        branch = _branch_diff(git, cfg)
    except GripError:
        return nothing
    if branch is None or branch.is_empty:
        return nothing
    if PassMemory(git.git_dir(), cfg.remember_passes_hours).has_passed(branch.digest):
        return nothing
    upstream = git.run(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False
    ).strip()
    where = upstream or "the remote"
    return (
        f"nothing to quiz in this push: its commits are already on {where}. "
        f"The branch ({branch.description}, {_files(branch)}) has not passed a "
        "quiz; run `grip quiz --branch`."
    )


def _push_diff(git: Git, cfg: Config, hook_args: tuple[str, ...]) -> Diff:
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF")
    to_ref = os.environ.get("PRE_COMMIT_TO_REF")
    if from_ref and to_ref:
        local = os.environ.get("PRE_COMMIT_LOCAL_BRANCH", "").removeprefix("refs/heads/")
        description = git.describe_commits(
            from_ref,
            to_ref,
            branch=local or git.current_branch(),
            remote=os.environ.get("PRE_COMMIT_REMOTE_NAME") or None,
        )
        return git.range_diff(from_ref, to_ref, cfg.exclude, cfg.max_diff_bytes, description)

    remote = hook_args[0] if hook_args else None
    refs: list[PushedRef] = []
    if not sys.stdin.isatty():
        for line in sys.stdin:
            parsed = PushedRef.parse(line)
            if parsed:
                refs.append(parsed)
    if not refs:
        head = git.run("rev-parse", "HEAD").strip()
        base = git.unpushed_base(head, remote)
        if base is None:
            return Diff("push", "", "", ())
        description = git.describe_commits(base, head, branch=git.current_branch(), remote=remote)
        return git.range_diff(base, head, cfg.exclude, cfg.max_diff_bytes, description)
    return git.push_diff(refs, remote, cfg.exclude, cfg.max_diff_bytes)


_PROVIDER_HINTS = {
    "auto": "each machine's coding agent, else anthropic",
    "claude-code": "Claude Code, already signed in: no key, no extra bill",
    "codex": "Codex CLI, already signed in: no key, no extra bill",
    "gemini": "Gemini CLI, already signed in: no key, no extra bill",
    "anthropic": "Anthropic API: needs ANTHROPIC_API_KEY or `ant auth login`",
    "ollama": "a local model through Ollama: free, set `model` in .grip.toml",
    "openai": "any OpenAI-compatible API: set `base_url` and OPENAI_API_KEY",
}


@dataclasses.dataclass(frozen=True, slots=True)
class _InitChoices:
    provider: str
    passing_score: int
    difficulty: str
    stages: tuple[str, ...]
    agents: bool


def _agent_names(agents: Iterable[str]) -> str:
    """Who an agent file is for, without the generic "AGENTS.md readers" entry as a name."""
    generic = {a.name for a in AGENTS if a.always}
    names = list(agents)
    named = [name for name in names if name not in generic]
    if len(named) == len(names):
        return ", ".join(named)
    if named:
        return f"{', '.join(named)} and other agents that read it"
    return "any coding agent that reads it"


def _init_wizard(
    defaults: _InitChoices,
    *,
    ask_provider: bool,
    ask_score: bool,
    ask_difficulty: bool,
    ask_stages: bool,
    ask_agents: bool,
) -> _InitChoices:
    """Ask, in the terminal, for what the flags did not say. Enter keeps each default."""
    _out.print("[bold]grip init[/bold] [dim]· Enter keeps the default in brackets[/dim]\n")
    provider = defaults.provider
    if ask_provider:
        installed = [name for name, command in AGENT_PROVIDERS if shutil.which(command)]
        options = [AUTO_PROVIDER, *installed, "anthropic", "ollama", "openai"]
        _out.print("[bold]Which model asks and grades the questions?[/bold]")
        for name in options:
            hint = _PROVIDER_HINTS[name]
            if name == AUTO_PROVIDER:
                hint += f" (here: {auto_provider()})"
            _out.print(f"  [cyan]{name:<12}[/cyan] {hint}")
        provider = click.prompt(
            "Provider", type=click.Choice(options), default=defaults.provider, show_choices=False
        )
    score = defaults.passing_score
    if ask_score:
        score = click.prompt(
            f"\nPass mark, out of {MAX_SCORE}", type=click.IntRange(0, MAX_SCORE), default=score
        )
    difficulty = defaults.difficulty
    if ask_difficulty:
        difficulty = click.prompt(
            "\nHow demanding should the questions be?",
            type=click.Choice([d.value for d in Difficulty], case_sensitive=False),
            default=difficulty,
        ).lower()
    stages = defaults.stages
    if ask_stages:
        when = click.prompt(
            "\nQuiz before every push, every commit, or both?",
            type=click.Choice(["push", "commit", "both"]),
            default="push",
        )
        stages = {"push": ("pre-push",), "commit": ("pre-commit",)}.get(
            when, ("pre-commit", "pre-push")
        )
    agents = defaults.agents
    if ask_agents and (found := detect()):
        names = ", ".join(a.name for a in found)
        agents = click.confirm(
            f"\nTell the coding agents here ({names}) to run the quiz before they push?",
            default=True,
        )
    _out.print()
    return _InitChoices(provider, score, difficulty, stages, agents)


def _interactive() -> bool:
    """Whether someone is at the keyboard to answer ``grip init``'s questions."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _provider_tip(provider: str) -> str | None:
    """What still has to happen for ``provider`` to work, if anything."""
    resolved = auto_provider() if provider == "auto" else provider
    command = dict(AGENT_PROVIDERS).get(resolved)
    if command and not shutil.which(command):
        return f"`{command}` is not on PATH yet; install it and sign in before the next push."
    if resolved == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        return "set ANTHROPIC_API_KEY (or run `ant auth login`) before the next push."
    if resolved == "ollama":
        return "set `model` in .grip.toml to a model you have pulled, e.g. qwen3:14b."
    if resolved == "openai":
        return "set `base_url` in .grip.toml and export OPENAI_API_KEY."
    return None


@cli.command(name="init")
@click.option(
    "--provider",
    type=_provider_choice(),
    default=None,
    help="Provider for .grip.toml. Defaults to auto: each machine's coding agent, else anthropic.",
)
@click.option(
    "--passing-score",
    type=click.IntRange(0, MAX_SCORE),
    default=None,
    help=f"Pass mark out of {MAX_SCORE} to write into .grip.toml.",
)
@click.option(
    "--difficulty",
    type=click.Choice([d.value for d in Difficulty], case_sensitive=False),
    default=None,
    help="Difficulty to write into .grip.toml.",
)
@click.option(
    "--stage",
    "stages",
    multiple=True,
    type=_stage_choice(),
    help="Hook stage(s) to install. Defaults to pre-push.",
)
@click.option("--no-hook", is_flag=True, help="Do not install a git hook.")
@click.option("--no-agents", is_flag=True, help="Do not write coding-agent rule files.")
@click.option("--force", is_flag=True, help="Overwrite an existing .grip.toml.")
@click.option("--yes", "-y", is_flag=True, help="Take every default instead of asking.")
def init_cmd(
    *,
    provider: str | None,
    passing_score: int | None,
    difficulty: str | None,
    stages: tuple[str, ...],
    no_hook: bool,
    no_agents: bool,
    force: bool,
    yes: bool,
) -> None:
    """Set a repository up in one go: .grip.toml, the git hook and the agent rule files.

    In a terminal it asks which model to use, the pass mark, the difficulty and when to
    quiz; flags answer a question up front and --yes takes every default. The provider
    defaults to auto, so each teammate uses the coding agent they have (Claude Code, Codex
    or Gemini CLI), else anthropic. Everything it writes is meant to be committed so the
    whole team shares it.
    """
    git = Git()
    root = git.root()
    toml_path = root / ".grip.toml"
    writing = force or not toml_path.exists()
    choices = _InitChoices(
        provider=(provider or AUTO_PROVIDER).lower(),
        passing_score=DEFAULT_PASSING_SCORE if passing_score is None else passing_score,
        difficulty=(difficulty or Difficulty.NORMAL.value).lower(),
        stages=stages or (Stage.PRE_PUSH.value,),
        agents=not no_agents,
    )
    if not yes and _interactive():
        choices = _init_wizard(
            choices,
            ask_provider=writing and provider is None,
            ask_score=writing and passing_score is None,
            ask_difficulty=writing and difficulty is None,
            ask_stages=not no_hook and not stages,
            ask_agents=not no_agents,
        )
    if not writing:
        _out.print(
            f"[dim]grip: {_pretty_path(toml_path)} exists, keeping it (--force overwrites)[/dim]"
        )
    else:
        text = render_template(
            provider=choices.provider,
            passing_score=choices.passing_score,
            difficulty=choices.difficulty,
        )
        toml_path.write_text(text, "utf-8")
        which = choices.provider
        if which == AUTO_PROVIDER:
            which = f"{AUTO_PROVIDER}, which is {auto_provider()} on this machine"
        _out.print(f"[green]grip:[/green] wrote {_pretty_path(toml_path)} (provider {which})")
        if tip := _provider_tip(choices.provider):
            _out.print(f"[yellow]grip:[/yellow] {tip}")
    if not no_hook:
        for stage in choices.stages:
            result = install(git, Stage(stage), append=True)
            how = "appended to" if result.appended else "installed at"
            _out.print(f"[green]grip:[/green] {stage} hook {how} {_repo_path(git, result.path)}")
    if choices.agents:
        changes = apply(root, detect())
        past = {Action.WRITTEN: "wrote", Action.UPDATED: "updated"}
        for change in changes:
            if change.action in past:
                _out.print(
                    f"[green]grip:[/green] {past[change.action]} {_pretty_path(change.path)} "
                    f"for {_agent_names(change.agents)}"
                )
    _out.print(
        f"\n[dim]Try it: {_try_it(git)}. Commit the files above so your team gets "
        "the same setup. grip status shows what is active.[/dim]"
    )


def _try_it(git: Git) -> str:
    """The dry run ``grip init`` suggests, picked so that it has something to quiz here.

    In a fresh repository nothing is staged and the files init just wrote are untracked,
    so a bare ``grip quiz`` would only say there is nothing to quiz.
    """
    try:
        exclude = _config(git).exclude
    except GripError:
        exclude = DEFAULT_EXCLUDES
    found = _elsewhere(git, exclude)
    if found.staged:
        return "grip quiz --provider fake"
    if found.branch:
        return "grip quiz --branch --provider fake"
    if found.untracked or found.modified or not found.unpushed:
        return (
            "stage a change with `git add` (the files above will do), then "
            "grip quiz --provider fake"
        )
    return "grip quiz --unpushed --provider fake"


@cli.command(name="agents")
@click.option(
    "--list", "list_only", is_flag=True, help="Show which agents were detected, write nothing."
)
@click.option("--all", "everything", is_flag=True, help="Every known agent, detected or not.")
@click.option(
    "--agent",
    "keys",
    multiple=True,
    type=click.Choice([a.key for a in AGENTS]),
    help="Only these agents (repeatable).",
)
@click.option("--remove", is_flag=True, help="Take out what `grip agents` wrote.")
@click.option("--dry-run", is_flag=True, help="Print what would change, write nothing.")
def agents_cmd(
    list_only: bool, everything: bool, keys: tuple[str, ...], remove: bool, dry_run: bool
) -> None:
    """Tell the coding agents you use to run the grip quiz before they push.

    Detects the agents installed on this machine and writes, at the repository root, the
    file each one reads: a marked block in AGENTS.md, CLAUDE.md or GEMINI.md, a rules
    file for Cursor, Windsurf and Cline, and the plugin enablement in
    .claude/settings.json for Claude Code. Run it again to refresh the blocks after an
    upgrade; --remove takes them out and leaves the rest of each file alone.
    """
    git = Git()
    detected = detect()
    if list_only:
        table = Table(title="agents", show_header=True, header_style="bold")
        table.add_column("agent")
        table.add_column("installed")
        table.add_column("writes")
        for a in AGENTS:
            found = "[green]yes[/green]" if a in detected else "[dim]no[/dim]"
            table.add_row(a.key, found, ", ".join(t.path for t in a.targets))
        _out.print(table)
        return
    if keys:
        chosen = [agent(k) for k in keys]
    elif everything:
        chosen = list(AGENTS)
    else:
        chosen = detected
    changes = apply(git.root(), chosen, remove=remove, dry_run=dry_run)
    verbs = {
        Action.WRITTEN: "write",
        Action.UPDATED: "update",
        Action.REMOVED: "remove",
        Action.UNCHANGED: "leave unchanged",
        Action.ABSENT: "find absent",
    }
    table = Table(show_header=True, header_style="bold")
    table.add_column("file", no_wrap=True)
    table.add_column("read by")
    table.add_column("action")
    for change in changes:
        label = f"would {verbs[change.action]}" if dry_run else change.action.value
        style = "dim" if change.action in {Action.UNCHANGED, Action.ABSENT} else "green"
        table.add_row(
            _pretty_path(change.path), ", ".join(change.agents), f"[{style}]{label}[/{style}]"
        )
    _out.print(table)
    if not remove:
        _out.print(
            "[dim]These files make the agent run `grip check` before pushing. The quiz itself "
            "also installs as a skill: npx skills add guilyx/grip -g[/dim]"
        )


@cli.command(name="install")
@click.option(
    "--stage",
    "stages",
    multiple=True,
    type=_stage_choice(),
    help="Which hook(s) to install. Defaults to pre-push.",
)
@click.option("--force", is_flag=True, help="Replace a hook that grip did not write.")
@click.option(
    "--append", is_flag=True, help="Chain grip after an existing hook instead of failing."
)
def install_cmd(stages: tuple[str, ...], force: bool, append: bool) -> None:
    """Install grip as a native git hook in this repository."""
    git = Git()
    for stage in stages or (Stage.PRE_PUSH.value,):
        result = install(git, Stage(stage), force=force, append=append)
        how = "appended to" if result.appended else "installed at"
        _out.print(f"[green]grip:[/green] {stage} hook {how} {_repo_path(git, result.path)}")
    cfg = _config(git)
    if cfg.provider == AUTO_PROVIDER:
        _out.print(
            f"[dim]grip: no provider configured, so the questions come from "
            f"{cfg.provider_name} (provider auto). `grip init` sets the model, the pass mark "
            "and the team's defaults.[/dim]"
        )
        if tip := _provider_tip(cfg.provider):
            _out.print(f"[yellow]grip:[/yellow] {tip}")
    _out.print("[dim]Run `grip status` to check, `grip uninstall` to remove.[/dim]")


@cli.command(name="uninstall")
@click.option("--stage", "stages", multiple=True, type=_stage_choice(), help="Defaults to all.")
def uninstall_cmd(stages: tuple[str, ...]) -> None:
    """Remove grip's native git hooks from this repository."""
    git = Git()
    for stage in stages or tuple(s.value for s in INSTALLABLE_STAGES):
        before = status(git, Stage(stage))
        result = uninstall(git, Stage(stage))
        if before.active and not result.active:
            _out.print(f"[green]grip:[/green] removed from {_repo_path(git, result.path)}")
        else:
            _out.print(f"[dim]grip: nothing to remove for {stage}[/dim]")


@cli.command(name="status")
def status_cmd() -> None:
    """Show which hooks are installed and the effective configuration."""
    git = Git()
    table = Table(title="hooks", show_header=True, header_style="bold")
    table.add_column("stage")
    table.add_column("state")
    table.add_column("path", overflow="fold")
    missing: list[str] = []
    for stage in INSTALLABLE_STAGES:
        s = status(git, stage)
        if s.grip_missing:
            state = "[red]grip missing[/red]"
            missing.append(
                f"[red]grip:[/red] the {stage.value} hook runs {escape(s.grip_path or '')}, "
                "which no longer exists. Unless grip is on the PATH your git client uses, the "
                "hook skips the quiz; run `grip install` again to point it at this grip."
            )
        elif s.managed:
            state = "[green]installed[/green]"
        elif s.appended:
            state = "[green]appended[/green]"
        elif s.exists:
            state = "[yellow]foreign hook[/yellow]"
        else:
            state = "[dim]not installed[/dim]"
        table.add_row(stage.value, state, _repo_path(git, s.path))
    _out.print(table)
    for line in missing:
        _out.print(line)
    if _truthy(os.environ.get("PRE_COMMIT")) or (git.root() / ".pre-commit-config.yaml").exists():
        _out.print(
            "[dim]pre-commit config detected: grip may also run via .pre-commit-config.yaml[/dim]"
        )
    cfg = _config(git)
    _print_activity(git, cfg)
    _print_config(cfg)


def _ago(when: datetime, now: datetime | None = None) -> str:
    """``3 hours ago`` style text for a UTC timestamp."""
    delta = (now or datetime.now(UTC)) - when
    seconds = max(0, int(delta.total_seconds()))
    for unit, size in (("day", 86_400), ("hour", 3_600), ("minute", 60)):
        count = seconds // size
        if count:
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "just now"


def _files(diff: Diff) -> str:
    count = len(diff.files)
    return f"{count} file{'' if count == 1 else 's'}"


def _at(when: datetime, now: datetime | None = None) -> str:
    """``at 17:52`` today, ``on 2026-10-18 17:52`` before, in local time."""
    local = when.astimezone()
    today = (now or datetime.now(UTC)).astimezone().date()
    return f"at {local:%H:%M}" if local.date() == today else f"on {local:%Y-%m-%d %H:%M}"


def _diff_state(git: Git, cfg: Config, mode: str) -> str:
    """Whether the staged, unpushed or branch diff passed: ``passed 98 at 17:52``."""
    try:
        diff = _select_diff(git, cfg, mode, None)
    except GripError as exc:
        return f"[yellow]could not check[/yellow] ({escape(str(exc))})"
    if diff.is_empty:
        return "[dim]nothing[/dim]"
    memory = PassMemory(git.git_dir(), cfg.remember_passes_hours)
    files = _files(diff)
    latest = memory.latest_for(diff.digest)
    score = f"{latest.score}/{MAX_SCORE} {_at(latest.created_at)}" if latest else ""
    if memory.has_passed(diff.digest):
        return f"[green]passed[/green] {score}".rstrip() + ", remembered"
    if latest is None:
        return f"[yellow]not quizzed[/yellow] ({files})"
    if latest.passed:
        return f"[yellow]passed {score}, no longer remembered[/yellow] ({files})"
    return f"[red]failed[/red] {score} ({files}); `grip last` shows why"


def _print_activity(git: Git, cfg: Config) -> None:
    table = Table(title="activity", show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    report = PassMemory(git.git_dir(), cfg.remember_passes_hours).last_report()
    if report is None:
        table.add_row("last quiz", "[dim]none yet[/dim]")
    else:
        verdict = "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]"
        when = f"{_ago(report.created_at)} ({report.stage.value})"
        table.add_row("last quiz", f"{report.score}/{MAX_SCORE} {verdict}, {when}")
    table.add_row("staged changes", _diff_state(git, cfg, "staged"))
    if git.has_commits():
        table.add_row("unpushed commits", _diff_state(git, cfg, "unpushed"))
    else:
        table.add_row("unpushed commits", "[dim]none, no commits yet[/dim]")
    default = git.default_branch()
    if default is not None:
        table.add_row(f"branch vs {default}", _diff_state(git, cfg, "branch"))
    skip = SkipState(git.git_dir()).describe()
    table.add_row("skipping", f"[yellow]{skip}[/yellow]" if skip else "[dim]no[/dim]")
    _out.print(table)


@cli.command(name="config")
def config_cmd() -> None:
    """Print the effective configuration."""
    _print_config(_config(Git()))


def _print_config(cfg: Config) -> None:
    table = Table(title="config", show_header=True, header_style="bold")
    table.add_column("option")
    table.add_column("value")
    for name, value in describe(cfg):
        table.add_row(name, value)
    _out.print(table)


@cli.command(name="skip")
@click.option(
    "--hours",
    type=click.FloatRange(min=0, min_open=True),
    default=None,
    help="Pause every hook run for this long instead of skipping only the next one.",
)
def skip_cmd(hours: float | None) -> None:
    """Skip the next commit or push hook once, or pause hooks for a while.

    For the IDE push button and every other place where GRIP_SKIP=1 is awkward to set. A
    manual `grip quiz` is never affected. `grip resume` cancels a pause early.
    """
    git = Git()
    state = SkipState(git.git_dir())
    if hours is None:
        state.set_once()
        _out.print("[yellow]grip:[/yellow] the next commit or push hook is skipped once.")
        return
    until = datetime.now(UTC) + timedelta(hours=hours)
    state.set_until(until)
    _out.print(
        f"[yellow]grip:[/yellow] hooks paused until {local_time(until)}. "
        "`grip resume` ends it early."
    )


@cli.command(name="resume")
def resume_cmd() -> None:
    """Cancel `grip skip`: hooks quiz again from the next run."""
    git = Git()
    state = SkipState(git.git_dir())
    had = state.describe() is not None
    state.clear()
    _out.print(
        "[green]grip:[/green] hooks resume from the next run."
        if had
        else "[dim]grip: nothing was skipped.[/dim]"
    )


def _stdin_cwd() -> Path | None:
    """The ``cwd`` of a JSON payload on stdin (Claude Code status line), if any."""
    if sys.stdin.isatty():
        return None
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, json.JSONDecodeError):
        return None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return Path(cwd) if isinstance(cwd, str) and cwd else None


@cli.command(name="statusline", hidden=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def statusline_cmd(as_json: bool) -> None:
    """One short line for an editor or Claude Code status line.

    Prints the last Grip Score, pass or fail, and whether hooks are paused. Reads an
    optional JSON payload on stdin and uses its "cwd" (that is what Claude Code sends).
    Never fails: outside a repository, or before any quiz, it prints nothing and exits 0.
    """
    try:
        git = Git(_stdin_cwd())
        git_dir = git.git_dir()
        cfg = _config(git)
    except GripError:
        if as_json:
            _emit({"repo": False})
        return
    report = PassMemory(git_dir, cfg.remember_passes_hours).last_report()
    skip = SkipState(git_dir).describe()
    if as_json:
        _emit(
            {
                "repo": True,
                "score": None if report is None else report.score,
                "max_score": MAX_SCORE,
                "passed": None if report is None else report.passed,
                "passing_score": cfg.passing_score,
                "at": None if report is None else report.created_at.isoformat(),
                "skipping": skip,
            }
        )
        return
    parts = ["grip"]
    if report is not None:
        parts.append(f"{report.score}/{MAX_SCORE} {'pass' if report.passed else 'fail'}")
    if skip:
        parts.append("paused")
    if len(parts) > 1:
        click.echo(" ".join(parts))


@cli.command(name="last")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable: the `grip grade` payload.")
def last_cmd(as_json: bool) -> None:
    """Show the last graded quiz: questions, your answers, scores and feedback.

    The rubrics stay out of the plain view; `--json` prints the same payload `grip grade`
    prints, for skills that build on your answers (such as a commit message).
    """
    git = Git()
    cfg = _config(git)
    memory = PassMemory(git.git_dir(), cfg.remember_passes_hours)
    report = memory.last_report()
    if report is None:
        raise GripError("no quiz has been graded in this repository yet")
    if as_json:
        payload = report_payload(report, memory.report_path)
        payload["summary"] = report.summary
        payload["answers"] = [
            a.text for a in sorted(report.answers, key=lambda a: a.question_index)
        ]
        payload["questions"] = [q.question for q in report.questions]
        payload["at"] = report.created_at.isoformat()
        _emit(payload)
        return
    verdict = "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]"
    _out.print(
        f"[bold]Grip Score {report.score}/{MAX_SCORE}[/bold] {verdict}, "
        f"{_ago(report.created_at)} ({report.stage.value}, {report.provider})"
    )
    _out.print(f"[dim]{report.summary}[/dim]\n")
    by_index = {a.question_index: a.text for a in report.answers}
    for grade_ in report.grades:
        q = report.questions[grade_.question_index]
        number = f"[bold cyan]Q{grade_.question_index + 1}[/bold cyan]"
        _out.print(f"{number} [dim]({q.focus})[/dim] {q.question}")
        answer = by_index.get(grade_.question_index, "") or "(no answer)"
        indented = escape(answer).replace("\n", "\n    ")
        _out.print(f"  [bold]>[/bold] {indented}")
        _out.print(f"  {grade_.score}/{POINTS_PER_QUESTION}: {escape(grade_.feedback)}\n")
    _out.print(report.verdict)


@cli.command()
def forget() -> None:
    """Forget remembered passes so the next commit or push is quizzed again."""
    git = Git()
    count = PassMemory(git.git_dir(), _config(git).remember_passes_hours).forget()
    if not count:
        _out.print("[dim]grip: no remembered passes.[/dim]")
        return
    _out.print(f"[green]grip:[/green] forgot {count} remembered pass{'' if count == 1 else 'es'}.")


@cli.group()
def study() -> None:
    """Export an anonymised summary of your quiz history for a study platform.

    Nothing leaves the machine unless you upload the file yourself. `grip study status`
    shows exactly which fields an export contains.
    """


def _since_option(fn: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--since",
        type=click.IntRange(0),
        default=DEFAULT_SINCE_DAYS,
        show_default=True,
        help="Only quizzes from the last N days; 0 for all of them.",
    )(fn)


def _since_delta(days: int) -> timedelta | None:
    return None if days == 0 else timedelta(days=days)


@study.command(name="export")
@_since_option
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to write the JSON document. Defaults to grip-export-<YYYYMMDD>.json here.",
)
def study_export_cmd(since: int, out_path: Path | None) -> None:
    """Write every quiz you took, across all repositories, as anonymised JSON."""
    registry = Registry()
    if not registry.prune():
        _out.print(
            "[yellow]grip:[/yellow] no repositories have been quizzed yet, nothing to export."
        )
        return
    now = datetime.now(UTC)
    export = build_export(registry, _since_delta(since), now)
    if out_path is None:
        out_path = Path.cwd() / f"grip-export-{now:%Y%m%d}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(export.model_dump_json(indent=2) + "\n", "utf-8")
    _out.print(
        f"[green]grip:[/green] {len(export.quizzes)} quizzes from {export.repo_count} repos, "
        f"written to {_pretty_path(out_path)}"
    )


@study.command(name="status")
@_since_option
def study_status_cmd(since: int) -> None:
    """Show what `grip study export` would write and which fields it contains."""
    registry = Registry()
    repos = registry.prune()
    export = build_export(registry, _since_delta(since))
    window = "all time" if since == 0 else f"the last {since} days"
    _out.print(f"registered repositories: {len(repos)} ({registry.path})")
    _out.print(
        f"quizzes to export: {len(export.quizzes)} from {export.repo_count} repos ({window})"
    )
    _out.print(f"installation id: {export.installation_id}")
    _out.print("\n[bold]included[/bold] per quiz:")
    for field in INCLUDED_FIELDS:
        _out.print(f"  [green]+[/green] {field}")
    _out.print("\n[bold]never exported[/bold]:")
    for field in EXCLUDED_FIELDS:
        _out.print(f"  [red]-[/red] {field}")


# --------------------------------------------------------------------------- Keep A Grip


def _platform_url() -> str:
    creds = load_credentials()
    return os.environ.get(URL_ENV, "").strip() or (creds.url if creds else DEFAULT_URL)


_PLATFORM_EXAMPLES = """\
\b
Examples:
  grip platform login kag_…                  a token from your dashboard
  grip platform problems --category ros2     what there is to solve
  grip platform solve cmd-vel-safety-filter  set up ./cmd-vel-safety-filter
  grip platform submit                       run the tests, take the quiz, report
"""


@cli.group(name="platform", epilog=_PLATFORM_EXAMPLES)
def platform_group() -> None:
    """Keep A Grip practice problems: log in, list, solve, submit a score.

    Only scores reach the platform; the diff, the questions and your answers stay here.
    """


@platform_group.command(name="login")
@click.argument("token")
@click.option(
    "--url",
    default=None,
    help=f"Keep A Grip base URL. Defaults to ${URL_ENV} or {DEFAULT_URL}.",
)
def login_cmd(token: str, url: str | None) -> None:
    """Store a Keep A Grip API token for `grip platform solve` and `submit`.

    Create the token on your dashboard. It is kept under your data home, readable by you
    only, and is used for nothing but reporting scores.
    """
    base = (url or os.environ.get(URL_ENV, "") or DEFAULT_URL).strip().rstrip("/")
    token = token.strip()
    who = Client(base, token).whoami()
    login = str(who.get("github_login") or "")
    path = save_credentials(Credentials(url=base, token=token, github_login=login))
    _out.print(f"[green]grip:[/green] logged in to {base} as {login or 'unknown'} ({path})")


@platform_group.command(name="logout")
def logout_cmd() -> None:
    """Forget the stored Keep A Grip token."""
    if forget_credentials():
        _out.print("[green]grip:[/green] logged out.")
    else:
        _out.print("[yellow]grip:[/yellow] no stored token.")


@platform_group.command(name="whoami")
def whoami_cmd() -> None:
    """Show which Keep A Grip account the stored token belongs to."""
    creds = load_credentials()
    who = Client.from_credentials(creds).whoami()
    assert creds is not None  # from_credentials raised otherwise
    _out.print(f"{who.get('github_login', 'unknown')} at {creds.url}")


@platform_group.command(name="problems")
@click.option("--category", default=None, help="Only this category slug, e.g. ros2.")
def problems_cmd(category: str | None) -> None:
    """List Keep A Grip problems you can `grip platform solve`."""
    rows = Client(_platform_url()).problems(category)
    if not rows:
        _out.print("[yellow]grip:[/yellow] no problems found.")
        return
    table = Table(show_header=True, header_style="bold")
    for column in ("slug", "title", "level", "category", "solvers", "pass"):
        table.add_column(column, no_wrap=column == "slug")
    for row in rows:
        rate = row.get("pass_rate")
        table.add_row(
            str(row.get("slug", "")),
            str(row.get("title", "")),
            str(row.get("difficulty", "")),
            str(row.get("category_slug", "")),
            str(row.get("solvers", 0)),
            "-" if rate is None else f"{round(float(rate) * 100)}%",
        )
    _out.print(table)


def _init_solve_repo(root: Path, problem: Problem) -> Git:
    """Clone the starter repo or start an empty one, then commit the problem files."""
    if problem.starter_repo:
        args = ["clone", "--quiet"]
        if problem.starter_ref:
            args += ["--branch", problem.starter_ref]
        Git(root.parent).run(*args, problem.starter_repo, str(root))
    else:
        root.mkdir(parents=True, exist_ok=True)
        Git(root).run("init", "--quiet", "--initial-branch", "main")
    git = Git(root)
    (root / "PROBLEM.md").write_text(problem_markdown(problem), "utf-8")
    grip_toml = root / ".grip.toml"
    if not grip_toml.exists():
        grip_toml.write_text(f"passing_score = {problem.passing_score}\n", "utf-8")
    identity: list[str] = []
    if not git.run("config", "--get", "user.email", check=False).strip():
        identity = ["-c", "user.name=grip", "-c", "user.email=grip@localhost"]
    git.run("add", "--all", ".")
    git.run(*identity, "commit", "--quiet", "-m", f"keepagrip: start {problem.slug}")
    return git


@platform_group.command(name="solve")
@click.argument("slug")
@click.option(
    "--dir",
    "target",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory to create. Defaults to ./<slug>.",
)
def solve_cmd(slug: str, target: Path | None) -> None:
    """Start a Keep A Grip problem: set up a repository with the statement in PROBLEM.md."""
    url = _platform_url()
    problem = Client(url).problem(slug)
    root = (target or Path.cwd() / slug).resolve()
    if root.exists() and any(root.iterdir()):
        raise PlatformError(f"{root} exists and is not empty.")
    git = _init_solve_repo(root, problem)
    state = SolveState(
        url=url,
        problem=problem.slug,
        title=problem.title,
        base=git.run("rev-parse", "HEAD").strip(),
        passing_score=problem.passing_score,
        focus=problem.focus,
        tests_command=problem.tests_command,
    )
    write_state(git.git_dir(), state)
    _out.print(f"[green]grip:[/green] {problem.title} ({problem.difficulty}) is in {root}")
    _out.print(f"  read   {root / 'PROBLEM.md'}")
    _out.print("  solve  with whatever you like, commit as you go")
    _out.print("  then   grip platform submit")


def _run_tests(root: Path, command: str) -> bool:
    """Run the problem's test command and return whether it passed."""
    _out.print(f"[dim]grip: running tests: {command}[/dim]")
    proc = subprocess.run(command, shell=True, cwd=root, check=False, timeout=1800)
    passed = proc.returncode == 0
    _out.print("[green]grip:[/green] tests passed." if passed else "[red]grip:[/red] tests failed.")
    return passed


@platform_group.command(name="submit")
@click.option("--no-tests", is_flag=True, help="Skip the problem's test command.")
@click.option("--dry-run", is_flag=True, help="Quiz, then print the payload instead of sending it.")
@quiz_options
def submit_cmd(no_tests: bool, dry_run: bool, report_path: Path | None, **overrides: Any) -> None:
    """Finish a Keep A Grip problem: run its tests, take the quiz, report the score."""
    git = Git()
    state = read_state(git.git_dir())
    cfg = _config(git, **overrides)
    diff = git.worktree_diff(state.base, cfg.exclude, cfg.max_diff_bytes)
    if diff.is_empty:
        raise PlatformError(
            "nothing has changed since `grip platform solve` set this repository up."
        )
    diff = dataclasses.replace(
        diff, description=f"solution to {state.problem}", context=problem_context(state)
    )
    # Check the login before the quiz so a missing token never wastes a graded attempt.
    client = None if dry_run else Client.from_credentials(load_credentials())
    tests_passed = None
    if state.tests_command and not no_tests:
        tests_passed = _run_tests(git.root(), state.tests_command)

    term = open_terminal()
    try:
        provider = get_provider(cfg)
        outcome = run_quiz(diff=diff, cfg=cfg, provider=provider, term=term, stage=Stage.MANUAL)
    finally:
        term.close()
    _record(git, PassMemory(git.git_dir(), cfg.remember_passes_hours), outcome.report)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(outcome.report.model_dump_json(indent=2), "utf-8")

    payload = attempt_payload(state, outcome.report, diff, tests_passed)
    if client is None:
        _emit(payload)
        return
    result = client.submit(payload)
    verdict = "[bold green]solved[/bold green]" if result.passed else "[bold red]not yet[/bold red]"
    extra = f", +{result.points} points" if result.points else ""
    _out.print(
        f"[green]grip:[/green] {state.title}: {verdict}. Score {result.score}/{MAX_SCORE} "
        f"(pass mark {result.passing_score}, your best {result.best_score}{extra})."
    )
    _out.print(f"  {client.url}/problems/{state.problem}")
    if not result.passed:
        raise QuizFailed("recorded. Re-read what the assistant wrote and try again.")


def _moved(command: click.Command, group: click.Group) -> click.Command:
    """``command`` under its old top-level name: hidden, with a note that it moved."""
    assert command.callback is not None
    assert command.name is not None
    callback = command.callback
    new = f"grip {group.name} {command.name}"

    def run(**kwargs: Any) -> Any:
        _err.print(
            f"[yellow]grip:[/yellow] `grip {command.name}` is now `{new}`; "
            "the old name will be removed in a later release.",
            soft_wrap=True,
        )
        return callback(**kwargs)

    return click.Command(
        command.name,
        callback=run,
        params=command.params,
        help=command.help,
        short_help=command.short_help,
        context_settings=command.context_settings,
        hidden=True,
    )


for _command in list(platform_group.commands.values()):
    cli.add_command(_moved(_command, platform_group))


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    cli.main(args=argv, prog_name="grip")


if __name__ == "__main__":  # pragma: no cover
    main()
