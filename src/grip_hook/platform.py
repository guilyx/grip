"""Talking to Keep A Grip, the problem platform: credentials, the HTTP client, solve state.

The platform only ever receives scores. Diffs, questions and answers stay on the machine,
exactly as with every other grip command.
"""

from __future__ import annotations

import json
import os
import re
import stat
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from grip_hook import __version__
from grip_hook.errors import GripError
from grip_hook.git import Diff
from grip_hook.models import Report
from grip_hook.study import data_home

DEFAULT_URL = "https://keepagrip.vercel.app"
URL_ENV = "KEEPAGRIP_URL"
TOKEN_ENV = "KEEPAGRIP_TOKEN"
STATE_FILE = "grip/keepagrip.json"
"""Under the repository's git dir: written by ``grip solve``, read by ``grip submit``."""

_Opener = Callable[[str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


class PlatformError(GripError):
    """Keep A Grip could not be reached, or refused the request."""


class Credentials(BaseModel):
    """What ``grip login`` stores under the data home."""

    url: str = DEFAULT_URL
    token: str
    github_login: str = ""


class Problem(BaseModel):
    """A problem as served by ``GET /api/problems/<slug>``."""

    slug: str
    title: str
    category: str
    difficulty: str
    summary: str
    statement: str
    starter_repo: str | None = None
    starter_ref: str | None = None
    tests_command: str | None = None
    focus: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    passing_score: int = 70


class SolveState(BaseModel):
    """Everything ``grip submit`` needs, kept in :data:`STATE_FILE` at the repo root."""

    version: int = 1
    url: str
    problem: str
    title: str
    base: str
    """Commit the solution is diffed against."""
    passing_score: int
    focus: list[str] = Field(default_factory=list)
    tests_command: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AttemptResult(BaseModel):
    """What the platform returns for a reported attempt."""

    attempt_id: str
    score: int
    passed: bool
    passing_score: int
    best_score: int
    first_solve: bool
    points: int


def credentials_path(home: Path | None = None) -> Path:
    """Where the token lives: ``<data home>/credentials.json``."""
    return (home or data_home()) / "credentials.json"


def save_credentials(creds: Credentials, home: Path | None = None) -> Path:
    """Write the credentials file, readable by the owner only."""
    path = credentials_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.model_dump_json(indent=2) + "\n", "utf-8")
    with _suppress_oserror():
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def load_credentials(
    home: Path | None = None, env: Mapping[str, str] | None = None
) -> Credentials | None:
    """Credentials from the environment (``KEEPAGRIP_TOKEN``) or the credentials file."""
    environ = os.environ if env is None else env
    token = environ.get(TOKEN_ENV, "").strip()
    if token:
        return Credentials(url=environ.get(URL_ENV, "").strip() or DEFAULT_URL, token=token)
    try:
        raw = credentials_path(home).read_text("utf-8")
        creds = Credentials.model_validate_json(raw)
    except (OSError, ValidationError):
        return None
    override = environ.get(URL_ENV, "").strip()
    if override:
        creds = creds.model_copy(update={"url": override})
    return creds


def forget_credentials(home: Path | None = None) -> bool:
    """Delete the credentials file. Returns whether there was one."""
    path = credentials_path(home)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


class _suppress_oserror:  # noqa: N801 - context manager, used like a function
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def _urllib_opener(
    method: str, url: str, body: dict[str, Any] | None, token: str | None
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": f"grip/{__version__}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PlatformError(f"could not reach {url}: {exc}") from exc


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": raw[:200].decode("utf-8", "replace")}


class Client:
    """A tiny client for the platform's public API.

    Args:
        url: Base URL of the site.
        token: An API token (``kag_…``), or ``None`` for the public endpoints.
        opener: Transport, replaced in tests. Defaults to :mod:`urllib`.
    """

    def __init__(self, url: str, token: str | None = None, opener: _Opener | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self._open = opener or _urllib_opener

    @classmethod
    def from_credentials(cls, creds: Credentials | None, opener: _Opener | None = None) -> Client:
        """A client for the stored credentials, raising when there are none."""
        if creds is None:
            raise PlatformError(
                "not logged in. Create a token on your Keep A Grip dashboard and run "
                "`grip login <token>`."
            )
        return cls(creds.url, creds.token, opener)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        status, payload = self._open(method, f"{self.url}{path}", body, self.token)
        if status >= 400:
            message = payload.get("error") if isinstance(payload, dict) else None
            raise PlatformError(f"{self.url}{path} answered {status}: {message or 'no detail'}")
        return payload

    def whoami(self) -> dict[str, Any]:
        """The account the token belongs to."""
        payload = self._call("GET", "/api/me")
        return dict(payload) if isinstance(payload, dict) else {}

    def problems(self, category: str | None = None) -> list[dict[str, Any]]:
        """The public catalogue, optionally for one category."""
        query = f"?category={category}" if category else ""
        payload = self._call("GET", f"/api/problems{query}")
        return list(payload.get("problems", [])) if isinstance(payload, dict) else []

    def problem(self, slug: str) -> Problem:
        """One approved problem."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug):
            raise PlatformError(f"{slug!r} is not a problem slug")
        payload = self._call("GET", f"/api/problems/{slug}")
        try:
            return Problem.model_validate(payload)
        except ValidationError as exc:
            raise PlatformError(f"unexpected problem payload: {exc}") from exc

    def submit(self, payload: dict[str, Any]) -> AttemptResult:
        """Report an attempt."""
        answer = self._call("POST", "/api/attempts", payload)
        try:
            return AttemptResult.model_validate(answer)
        except ValidationError as exc:
            raise PlatformError(f"unexpected attempt payload: {exc}") from exc


@dataclass(frozen=True, slots=True)
class DiffStats:
    """File and line counts parsed from ``git diff --stat``."""

    files: int
    insertions: int
    deletions: int


def diff_stats(diff: Diff) -> DiffStats:
    """Parse the summary line of ``diff.stat``. Missing pieces count as zero."""
    files = len(diff.files)
    insertions = deletions = 0
    last = diff.stat.strip().rsplit("\n", 1)[-1] if diff.stat.strip() else ""
    if match := re.search(r"(\d+) insertion", last):
        insertions = int(match.group(1))
    if match := re.search(r"(\d+) deletion", last):
        deletions = int(match.group(1))
    return DiffStats(files, insertions, deletions)


def problem_context(problem: Problem | SolveState) -> str:
    """The text handed to the quiz generator alongside the diff, as data."""
    focus = ", ".join(problem.focus) if problem.focus else "no particular focus given"
    title = problem.title
    return (
        f"This diff is a solution to the Keep A Grip problem '{title}'. "
        f"The problem author suggests probing: {focus}."
    )


def attempt_payload(
    state: SolveState,
    report: Report,
    diff: Diff,
    tests_passed: bool | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the JSON body for ``POST /api/attempts`` from a graded quiz."""
    stats = diff_stats(diff)
    grades = [
        {"focus": report.questions[g.question_index].focus[:40], "score": g.score}
        for g in report.grades
        if 0 <= g.question_index < len(report.questions)
    ]
    elapsed = int(((now or datetime.now(UTC)) - state.started_at).total_seconds())
    return {
        "problem": state.problem,
        "score": report.score,
        "grades": grades,
        "provider": report.provider[:40],
        "model": report.model[:80],
        "stage": "solve",
        "files": stats.files,
        "insertions": stats.insertions,
        "deletions": stats.deletions,
        "tests_passed": tests_passed,
        "duration_s": max(0, elapsed),
        "source": "cli",
        "grip_version": __version__,
    }


def write_state(git_dir: Path, state: SolveState) -> Path:
    """Write :data:`STATE_FILE` under the git dir."""
    path = git_dir / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2) + "\n", "utf-8")
    return path


def read_state(git_dir: Path) -> SolveState:
    """Read :data:`STATE_FILE`, raising a helpful error when this is not a solve repo."""
    path = git_dir / STATE_FILE
    try:
        return SolveState.model_validate_json(path.read_text("utf-8"))
    except FileNotFoundError as exc:
        raise PlatformError(
            f"no {STATE_FILE} here. Run `grip solve <slug>` first, then work inside that directory."
        ) from exc
    except ValidationError as exc:
        raise PlatformError(f"{path} is not a valid solve state: {exc}") from exc


def problem_markdown(problem: Problem) -> str:
    """``PROBLEM.md`` for a fresh solve repository."""
    focus = "".join(f"- {f}\n" for f in problem.focus) or "- whatever a good reviewer would ask\n"
    tests = f"\nTests: `{problem.tests_command}`\n" if problem.tests_command else ""
    return (
        f"# {problem.title}\n\n"
        f"{problem.summary}\n\n"
        f"Category: {problem.category} · Difficulty: {problem.difficulty} · "
        f"Pass mark: {problem.passing_score}/100\n{tests}\n"
        f"{problem.statement.strip()}\n\n"
        f"## The quiz will probe\n\n{focus}\n"
        "## When you are done\n\n"
        "```sh\ngrip submit\n```\n"
    )


__all__ = [
    "DEFAULT_URL",
    "STATE_FILE",
    "TOKEN_ENV",
    "URL_ENV",
    "AttemptResult",
    "Client",
    "Credentials",
    "DiffStats",
    "PlatformError",
    "Problem",
    "SolveState",
    "attempt_payload",
    "credentials_path",
    "diff_stats",
    "forget_credentials",
    "load_credentials",
    "problem_context",
    "problem_markdown",
    "read_state",
    "save_credentials",
    "write_state",
]
