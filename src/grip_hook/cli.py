"""Command-line interface."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from grip_hook import MAX_SCORE, __version__
from grip_hook.agent import (
    Gate,
    PendingStore,
    ask,
    claude_code_decision,
    grade,
    hook_request,
    parse_answers,
    questions_payload,
    report_payload,
)
from grip_hook.config import Config, describe, load_config
from grip_hook.errors import GripError, NoTerminalError, ProviderError, QuizFailed
from grip_hook.git import Diff, Git, PushedRef
from grip_hook.hooks import INSTALLABLE_STAGES, install, status, uninstall
from grip_hook.memory import PassMemory
from grip_hook.models import Difficulty, Report, Stage
from grip_hook.providers import REGISTRY, get_provider
from grip_hook.quiz import run_quiz
from grip_hook.study import (
    DEFAULT_SINCE_DAYS,
    EXCLUDED_FIELDS,
    INCLUDED_FIELDS,
    Registry,
    build_export,
)
from grip_hook.terminal import open_terminal

_err = Console(stderr=True, highlight=False)
_out = Console(highlight=False)

SKIP_ENV = "GRIP_SKIP"


class _Failure(click.ClickException):
    """A :class:`GripError` re-raised so click exits with the right code and message."""

    def __init__(self, error: GripError) -> None:
        super().__init__(str(error))
        self.exit_code = error.exit_code  # type: ignore[misc]
        self.prefix = "grip" if isinstance(error, QuizFailed) else "grip error"

    def show(self, file: Any = None) -> None:
        _err.print(f"[bold red]{self.prefix}:[/bold red] {self.format_message()}")


class _Group(click.Group):
    """Click group that translates grip's exceptions into exit codes."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except GripError as exc:
            raise _Failure(exc) from exc


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _stage_choice() -> click.Choice[str]:
    return click.Choice([s.value for s in INSTALLABLE_STAGES])


def quiz_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Options shared by every command that can run a quiz."""
    decorators = [
        click.option(
            "--passing-score",
            type=click.IntRange(0, MAX_SCORE),
            default=None,
            help=f"Minimum Grip Score out of {MAX_SCORE} to pass.",
        ),
        click.option(
            "--provider",
            type=click.Choice(sorted(REGISTRY), case_sensitive=False),
            default=None,
            help="LLM provider.",
        ),
        click.option("--model", default=None, help="Model identifier for the provider."),
        click.option(
            "--difficulty",
            type=click.Choice([d.value for d in Difficulty], case_sensitive=False),
            default=None,
            help="How demanding the questions are.",
        ),
        click.option(
            "--report",
            "report_path",
            type=click.Path(dir_okay=False, path_type=Path),
            default=None,
            help="Also write the JSON report to this path.",
        ),
    ]
    for decorator in reversed(decorators):
        fn = decorator(fn)
    return fn


def _config(git: Git | None, **overrides: Any) -> Config:
    root = git.root() if git else None
    return load_config(root, overrides=overrides)


def _skip(reason: str) -> None:
    _err.print(f"[yellow]grip:[/yellow] {reason}")


def _run(
    git: Git,
    diff: Diff,
    cfg: Config,
    stage: Stage,
    report_path: Path | None,
) -> int:
    """Shared driver for ``quiz`` and ``hook``. Returns the process exit code."""
    if diff.is_empty:
        _skip("nothing to quiz, no changes found.")
        return 0

    memory = PassMemory(git.git_dir(), cfg.remember_passes_hours)
    if memory.has_passed(diff.digest):
        _skip("this exact diff already passed recently, skipping the quiz.")
        return 0

    try:
        term = open_terminal()
    except NoTerminalError as exc:
        if cfg.require_tty:
            raise
        _skip(f"{exc} Skipping. Set require_tty = true to fail instead.")
        return 0

    try:
        provider = get_provider(cfg)
        outcome = run_quiz(diff=diff, cfg=cfg, provider=provider, term=term, stage=stage)
    except ProviderError as exc:
        if cfg.fail_open:
            _skip(f"provider error, letting this through because fail_open is set: {exc}")
            return 0
        raise ProviderError(
            f"{exc}\nNothing was sent upstream. To bypass grip once, set {SKIP_ENV}=1 "
            "(or use git's --no-verify)."
        ) from exc
    finally:
        term.close()

    saved = _record(git, memory, outcome.report)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(outcome.report.model_dump_json(indent=2), "utf-8")
    if outcome.passed:
        memory.record_pass(diff.digest)
        return 0
    raise QuizFailed(
        f"Grip Score {outcome.report.score}/{MAX_SCORE} is below the pass mark of "
        f"{cfg.passing_score}. Re-read your change and try again. Report: {_pretty_path(saved)}"
    )


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


@click.group(cls=_Group, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="grip")
def cli() -> None:
    """Keep a grip on your code: a quiz before every commit or push."""


@cli.command()
@click.option("--staged", "mode", flag_value="staged", default=True, help="Quiz staged changes.")
@click.option("--unpushed", "mode", flag_value="unpushed", help="Quiz commits not on any remote.")
@click.option("--range", "rev_range", default=None, help="Quiz an explicit BASE..HEAD range.")
@quiz_options
def quiz(
    mode: str,
    rev_range: str | None,
    report_path: Path | None,
    **overrides: Any,
) -> None:
    """Run the quiz now, without a hook."""
    git = Git()
    cfg = _config(git, **overrides)
    diff = _select_diff(git, cfg, mode, rev_range)
    sys.exit(_run(git, diff, cfg, Stage.MANUAL, report_path))


def _select_diff(git: Git, cfg: Config, mode: str, rev_range: str | None) -> Diff:
    """The diff for ``--staged`` (default), ``--unpushed`` or ``--range BASE..HEAD``."""
    if rev_range:
        base, sep, head = rev_range.partition("..")
        if not sep or not base:
            raise click.BadParameter("expected BASE..HEAD", param_hint="--range")
        return git.range_diff(base, head or "HEAD", cfg.exclude, cfg.max_diff_bytes)
    if mode == "unpushed":
        head_sha = git.run("rev-parse", "HEAD").strip()
        base_sha = git.unpushed_base(head_sha)
        if base_sha is None:
            return Diff("unpushed commits", "", "", ())
        return git.range_diff(base_sha, head_sha, cfg.exclude, cfg.max_diff_bytes)
    return git.staged_diff(cfg.exclude, cfg.max_diff_bytes)


def _diff_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    decorators = [
        click.option("--staged", "mode", flag_value="staged", default=True, help="Staged changes."),
        click.option(
            "--unpushed", "mode", flag_value="unpushed", help="Commits not on any remote."
        ),
        click.option("--range", "rev_range", default=None, help="An explicit BASE..HEAD range."),
    ]
    for decorator in reversed(decorators):
        fn = decorator(fn)
    return fn


def _emit(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, indent=2))


@cli.command(name="ask")
@_diff_options
@quiz_options
def ask_cmd(mode: str, rev_range: str | None, report_path: Path | None, **overrides: Any) -> None:
    """Write the questions as JSON for a coding agent to relay (no terminal needed).

    The rubrics stay in .git/grip/pending.json. Follow up with `grip grade`.
    """
    git = Git()
    cfg = _config(git, **overrides)
    diff = _select_diff(git, cfg, mode, rev_range)
    if diff.is_empty:
        _emit({"status": "nothing-to-quiz", "message": "no changes found"})
        return
    memory = PassMemory(git.git_dir(), cfg.remember_passes_hours)
    if memory.has_passed(diff.digest):
        _emit({"status": "already-passed", "message": "this exact diff passed recently"})
        return
    pending = ask(diff, cfg, get_provider(cfg), Stage.MANUAL)
    PendingStore(git.git_dir()).save(pending)
    _emit(questions_payload(pending, cfg))


@cli.command(name="grade")
@click.option(
    "--answers",
    "answers_path",
    type=click.Path(dir_okay=False, allow_dash=True, path_type=Path),
    required=True,
    help='JSON file with ["answer 1", ...] or {"answers": [...]}; "-" reads stdin.',
)
@quiz_options
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


@cli.command(name="check")
@_diff_options
def check_cmd(mode: str, rev_range: str | None) -> None:
    """Exit 0 when the diff already passed a quiz (or there is nothing to quiz), else 1."""
    git = Git()
    cfg = _config(git)
    verdict = _check(git, cfg, mode, rev_range)
    if verdict is None:
        _out.print("[green]grip:[/green] ok")
        return
    _err.print(f"[bold red]grip:[/bold red] {verdict}")
    sys.exit(1)


def _check(git: Git, cfg: Config, mode: str, rev_range: str | None) -> str | None:
    """``None`` when the diff may go through, otherwise why it may not."""
    diff = _select_diff(git, cfg, mode, rev_range)
    if diff.is_empty:
        return None
    if PassMemory(git.git_dir(), cfg.remember_passes_hours).has_passed(diff.digest):
        return None
    what = "unpushed commits" if mode == "unpushed" else "staged changes"
    return f"the {what} have not passed a grip quiz yet ({len(diff.files)} files)."


@cli.command(name="agent-hook")
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
        verdict = _check(git, _config(git), mode, None)
    except GripError:
        return
    if verdict is None:
        return
    _emit(
        claude_code_decision(
            f"grip: {verdict} Ask the developer to run /grip:quiz"
            f"{' --unpushed' if mode == 'unpushed' else ''} and answer the questions "
            f"themselves, then retry. Do not answer for them. GRIP_SKIP=1 bypasses once."
        )
    )


@cli.command()
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
    cfg = _config(git, **overrides)
    stage_enum = Stage(stage)
    if stage_enum is Stage.PRE_COMMIT:
        diff = git.staged_diff(cfg.exclude, cfg.max_diff_bytes)
    else:
        diff = _push_diff(git, cfg, hook_args)
    sys.exit(_run(git, diff, cfg, stage_enum, report_path))


def _push_diff(git: Git, cfg: Config, hook_args: tuple[str, ...]) -> Diff:
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF")
    to_ref = os.environ.get("PRE_COMMIT_TO_REF")
    if from_ref and to_ref:
        return git.range_diff(from_ref, to_ref, cfg.exclude, cfg.max_diff_bytes)

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
        return git.range_diff(base, head, cfg.exclude, cfg.max_diff_bytes)
    return git.push_diff(refs, remote, cfg.exclude, cfg.max_diff_bytes)


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
        _out.print(f"[green]grip:[/green] {stage} hook {how} {result.path}")
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
            _out.print(f"[green]grip:[/green] removed from {result.path}")
        else:
            _out.print(f"[dim]grip: nothing to remove for {stage}[/dim]")


@cli.command(name="status")
def status_cmd() -> None:
    """Show which hooks are installed and the effective configuration."""
    git = Git()
    table = Table(title="hooks", show_header=True, header_style="bold")
    table.add_column("stage")
    table.add_column("state")
    table.add_column("path")
    for stage in INSTALLABLE_STAGES:
        s = status(git, stage)
        if s.managed:
            state = "[green]installed[/green]"
        elif s.appended:
            state = "[green]appended[/green]"
        elif s.exists:
            state = "[yellow]foreign hook[/yellow]"
        else:
            state = "[dim]not installed[/dim]"
        table.add_row(stage.value, state, str(s.path))
    _out.print(table)
    if _truthy(os.environ.get("PRE_COMMIT")) or (git.root() / ".pre-commit-config.yaml").exists():
        _out.print(
            "[dim]pre-commit config detected: grip may also run via .pre-commit-config.yaml[/dim]"
        )
    _print_config(_config(git))


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


@cli.command()
def forget() -> None:
    """Forget remembered passes so the next commit or push is quizzed again."""
    git = Git()
    PassMemory(git.git_dir(), 1).forget()
    _out.print("[green]grip:[/green] forgot all remembered passes.")


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


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    cli.main(args=argv, prog_name="grip")


if __name__ == "__main__":  # pragma: no cover
    main()
