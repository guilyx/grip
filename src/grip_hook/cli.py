"""Command-line interface."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from grip_hook import MAX_SCORE, POINTS_PER_QUESTION, __version__
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
from grip_hook.config import (
    DEFAULT_PASSING_SCORE,
    Config,
    describe,
    load_config,
    render_template,
)
from grip_hook.errors import GripError, NoTerminalError, ProviderError, QuizFailed
from grip_hook.git import Diff, Git, PushedRef
from grip_hook.hooks import INSTALLABLE_STAGES, install, status, uninstall
from grip_hook.integrations import AGENTS, Action, agent, apply, detect, is_installed
from grip_hook.memory import PassMemory, SkipState
from grip_hook.models import Difficulty, Report, Stage
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
    if reason := SkipState(git.git_dir()).consume():
        _skip(f"{reason}, skipping the quiz. `grip resume` cancels a pause.")
        return
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


def _default_provider() -> str:
    """The provider ``grip init`` picks: a signed-in coding agent when there is one."""
    for key in ("claude-code", "codex", "gemini"):
        if is_installed(agent(key)):
            return key
    return "anthropic"


@cli.command(name="init")
@click.option(
    "--provider",
    type=click.Choice(sorted(REGISTRY), case_sensitive=False),
    default=None,
    help="Provider for .grip.toml. Defaults to an installed coding agent, else anthropic.",
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
def init_cmd(
    *,
    provider: str | None,
    passing_score: int | None,
    difficulty: str | None,
    stages: tuple[str, ...],
    no_hook: bool,
    no_agents: bool,
    force: bool,
) -> None:
    """Set a repository up in one go: .grip.toml, the git hook and the agent rule files.

    Picks a provider you will not need a key for when Claude Code, Codex or Gemini CLI is
    installed. Everything it writes is meant to be committed so the whole team shares it.
    """
    git = Git()
    root = git.root()
    toml_path = root / ".grip.toml"
    if toml_path.exists() and not force:
        _out.print(
            f"[dim]grip: {_pretty_path(toml_path)} exists, keeping it (--force overwrites)[/dim]"
        )
    else:
        chosen = (provider or _default_provider()).lower()
        text = render_template(
            provider=chosen,
            passing_score=DEFAULT_PASSING_SCORE if passing_score is None else passing_score,
            difficulty=difficulty or Difficulty.NORMAL.value,
        )
        toml_path.write_text(text, "utf-8")
        _out.print(f"[green]grip:[/green] wrote {_pretty_path(toml_path)} (provider {chosen})")
    if not no_hook:
        for stage in stages or (Stage.PRE_PUSH.value,):
            result = install(git, Stage(stage), append=True)
            how = "appended to" if result.appended else "installed at"
            _out.print(f"[green]grip:[/green] {stage} hook {how} {_pretty_path(result.path)}")
    if not no_agents:
        changes = apply(root, detect())
        past = {Action.WRITTEN: "wrote", Action.UPDATED: "updated"}
        for change in changes:
            if change.action in past:
                _out.print(
                    f"[green]grip:[/green] {past[change.action]} {_pretty_path(change.path)} "
                    f"for {', '.join(change.agents)}"
                )
    _out.print(
        "\n[dim]Try it: grip quiz --provider fake. Commit the files above so your team gets "
        "the same setup. grip status shows what is active.[/dim]"
    )


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


def _diff_state(git: Git, cfg: Config, mode: str) -> str:
    """``nothing``, ``passed`` or ``not quizzed`` for the staged or unpushed diff."""
    try:
        diff = _select_diff(git, cfg, mode, None)
    except GripError as exc:
        return f"[yellow]could not check[/yellow] ({exc})"
    if diff.is_empty:
        return "[dim]nothing[/dim]"
    if PassMemory(git.git_dir(), cfg.remember_passes_hours).has_passed(diff.digest):
        return "[green]passed[/green], remembered"
    return f"[yellow]not quizzed yet[/yellow] ({len(diff.files)} files)"


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
    table.add_row("unpushed commits", _diff_state(git, cfg, "unpushed"))
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
        f"[yellow]grip:[/yellow] hooks paused until {until:%Y-%m-%d %H:%M} UTC. "
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


@cli.command(name="statusline")
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
        _out.print(f"  [bold]>[/bold] {answer}")
        _out.print(f"  {grade_.score}/{POINTS_PER_QUESTION}: {grade_.feedback}\n")
    _out.print(report.verdict)


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


# --------------------------------------------------------------------------- Keep A Grip


def _platform_url() -> str:
    creds = load_credentials()
    return os.environ.get(URL_ENV, "").strip() or (creds.url if creds else DEFAULT_URL)


@cli.command(name="login")
@click.argument("token")
@click.option(
    "--url",
    default=None,
    help=f"Keep A Grip base URL. Defaults to ${URL_ENV} or {DEFAULT_URL}.",
)
def login_cmd(token: str, url: str | None) -> None:
    """Store a Keep A Grip API token for `grip solve` and `grip submit`.

    Create the token on your dashboard. It is kept under your data home, readable by you
    only, and is used for nothing but reporting scores.
    """
    base = (url or os.environ.get(URL_ENV, "") or DEFAULT_URL).strip().rstrip("/")
    token = token.strip()
    who = Client(base, token).whoami()
    login = str(who.get("github_login") or "")
    path = save_credentials(Credentials(url=base, token=token, github_login=login))
    _out.print(f"[green]grip:[/green] logged in to {base} as {login or 'unknown'} ({path})")


@cli.command(name="logout")
def logout_cmd() -> None:
    """Forget the stored Keep A Grip token."""
    if forget_credentials():
        _out.print("[green]grip:[/green] logged out.")
    else:
        _out.print("[yellow]grip:[/yellow] no stored token.")


@cli.command(name="whoami")
def whoami_cmd() -> None:
    """Show which Keep A Grip account the stored token belongs to."""
    creds = load_credentials()
    who = Client.from_credentials(creds).whoami()
    assert creds is not None  # from_credentials raised otherwise
    _out.print(f"{who.get('github_login', 'unknown')} at {creds.url}")


@cli.command(name="problems")
@click.option("--category", default=None, help="Only this category slug, e.g. ros2.")
def problems_cmd(category: str | None) -> None:
    """List Keep A Grip problems you can `grip solve`."""
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


@cli.command(name="solve")
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
    _out.print("  then   grip submit")


def _run_tests(root: Path, command: str) -> bool:
    """Run the problem's test command and return whether it passed."""
    _out.print(f"[dim]grip: running tests: {command}[/dim]")
    proc = subprocess.run(command, shell=True, cwd=root, check=False, timeout=1800)
    passed = proc.returncode == 0
    _out.print("[green]grip:[/green] tests passed." if passed else "[red]grip:[/red] tests failed.")
    return passed


@cli.command(name="submit")
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
        raise PlatformError("nothing has changed since `grip solve` set this repository up.")
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


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    cli.main(args=argv, prog_name="grip")


if __name__ == "__main__":  # pragma: no cover
    main()
