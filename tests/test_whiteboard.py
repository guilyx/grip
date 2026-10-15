"""grip whiteboard: the board, its checks and layout, the page, the server and the command."""

from __future__ import annotations

import http.client
import json
import re
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from click.testing import CliRunner

from grip_hook import QUESTION_COUNT
from grip_hook.cli import cli
from grip_hook.config import Config
from grip_hook.errors import ProviderError
from grip_hook.git import Diff
from grip_hook.models import Difficulty
from grip_hook.providers.fake import FakeProvider, load_script
from grip_hook.whiteboard import (
    MARGIN,
    Board,
    BoardError,
    Edge,
    Node,
    Pin,
    PinKind,
    board_data,
    check_board,
    generate_board,
    layout,
)
from grip_hook.whiteboard_server import (
    MAX_BODY,
    WhiteboardServer,
    content_security_policy,
    render_page,
)

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scripts" / "demo" / "whiteboard-scenario.json"
FILES = ["api/handlers.py"]


@pytest.fixture
def board() -> Board:
    loaded = load_script(SCENARIO).board
    assert loaded is not None
    return loaded


def _mutate(board: Board, **changes: Any) -> Board:
    return board.model_copy(update=changes, deep=True)


# -- the board -------------------------------------------------------------------------


def test_scenario_board_is_valid(board: Board) -> None:
    assert check_board(board, FILES) == []
    assert set(board.required_kinds()) == {
        PinKind.WHY,
        PinKind.FAILURE,
        PinKind.VERIFY,
        PinKind.ADVERSARY,
        PinKind.DATA,
    }


def test_pins_become_questions_with_their_kind_and_rubric(board: Board) -> None:
    questions = board.questions()
    assert len(questions) == QUESTION_COUNT
    assert questions[1].focus == "malicious actor"
    assert questions[1].rubric.startswith("- Every new key")
    assert questions[1].rubric.count("\n- ") == 2


def test_crosses_trust(board: Board) -> None:
    by_key = {board.edge_key(e): board.crosses_trust(e) for e in board.edges}
    assert by_key["client->handle"] is True
    assert by_key["handle->client"] is True
    assert by_key["handle->allow"] is False


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda b: b.edges.append(Edge(source="handle", target="ghost", label="x")), "ghost"),
        (lambda b: setattr(b.pins[0], "on", "nowhere"), "not a node id or an edge"),
        (lambda b: setattr(b.nodes[1], "files", ["invented.py"]), "not in the diff"),
        (lambda b: setattr(b.nodes[1], "files", []), "lists no files"),
        (lambda b: setattr(b.nodes[1], "label", "two\nlines"), "control characters"),
        (lambda b: setattr(b.nodes[1], "label", "x" * 41), "longer than 40"),
        (lambda b: setattr(b.nodes[1], "id", "Handle Node"), "lowercase slug"),
        (lambda b: setattr(b.pins[1], "kind", PinKind.HOW), "'malicious actor'"),
        (lambda b: setattr(b.pins[4], "kind", PinKind.WHY), "two pins ask 'why this way'"),
        (lambda b: b.edges.append(Edge(source="allow", target="allow", label="x")), "itself"),
        (lambda b: b.edges.append(b.edges[0].model_copy()), "appears twice"),
        (lambda b: setattr(b.pins[0], "rubric", ["ok", "  "]), "empty rubric point"),
    ],
)
def test_check_board_rejects(board: Board, change: Callable[[Board], None], message: str) -> None:
    broken = _mutate(board)
    change(broken)
    errors = check_board(broken, FILES)
    assert any(message in e for e in errors), errors


def test_check_board_limits_the_number_of_nodes(board: Board) -> None:
    extra = [
        Node(id=f"n{i}", label="x", role="x", kind="context", trust="trusted", files=[])
        for i in range(3)
    ]
    errors = check_board(_mutate(board, nodes=[*board.nodes, *extra]), FILES)
    assert any("it needs 2 to 8" in e for e in errors)


def test_how_it_works_may_repeat_and_fills_free_slots() -> None:
    nodes = [
        Node(id="a", label="a", role="r", kind="component", trust="trusted", files=["a.py"]),
        Node(id="b", label="b", role="r", kind="context", trust="trusted", files=[]),
    ]
    kinds = [PinKind.WHY, PinKind.FAILURE, PinKind.VERIFY, PinKind.HOW, PinKind.HOW]
    pins = [Pin(on="a", kind=k, question="q?", rubric=["x", "y"]) for k in kinds]
    simple = Board(
        summary="s", nodes=nodes, edges=[Edge(source="a", target="b", label="l")], pins=pins
    )
    assert check_board(simple, ["a.py"]) == []


# -- generation ------------------------------------------------------------------------


class _Scripted:
    """A provider that returns the given boards in order and records the problems it saw."""

    name, model = "scripted", "s"

    def __init__(self, *boards: Board) -> None:
        self.boards = list(boards)
        self.problems: list[tuple[str, ...]] = []

    def generate_board(
        self, diff: Diff, difficulty: Difficulty, problems: tuple[str, ...]
    ) -> Board:
        self.problems.append(problems)
        return self.boards.pop(0)


DIFF = Diff("staged changes", "", "+x\n", tuple(FILES))


def test_generate_board_retries_once_with_the_problems(board: Board) -> None:
    bad = _mutate(board)
    bad.pins[0].on = "nowhere"
    provider = _Scripted(bad, board)
    assert generate_board(provider, DIFF, Difficulty.NORMAL) == board  # type: ignore[arg-type]
    assert provider.problems[0] == ()
    assert any("nowhere" in p for p in provider.problems[1])


def test_generate_board_gives_up_after_the_retry(board: Board) -> None:
    bad = _mutate(board)
    bad.pins[0].on = "nowhere"
    with pytest.raises(BoardError, match="grip quiz") as info:
        generate_board(_Scripted(bad, bad), DIFF, Difficulty.NORMAL)  # type: ignore[arg-type]
    assert isinstance(info.value, ProviderError)


@pytest.mark.parametrize("files", [("a.py",), ("a.py", "b/c.py", "d.ts")])
def test_fake_provider_draws_a_valid_board(files: tuple[str, ...]) -> None:
    drawn = FakeProvider(Config()).generate_board(
        Diff("staged changes", "", "+x\n", files), Difficulty.NORMAL, ()
    )
    assert check_board(drawn, files) == []


# -- layout ----------------------------------------------------------------------------


def test_layout_is_deterministic_and_reads_left_to_right(board: Board) -> None:
    placed = layout(board)
    assert placed == layout(board)
    pos = placed.positions
    assert pos["client"][0] == MARGIN  # untrusted on the left
    assert pos["clock"][0] == pos["handle"][0]  # a clock sits above what it feeds
    assert pos["clock"][1] < pos["handle"][1]
    assert pos["client"][0] < pos["handle"][0] < pos["allow"][0] < pos["buckets"][0]
    columns = sorted({x for x, _ in pos.values()})
    assert len(columns) == 4  # no empty column left behind
    assert placed.zone is not None
    assert placed.zone[0] < MARGIN and placed.zone[0] + placed.zone[2] > MARGIN


def test_layout_without_untrusted_nodes_starts_at_the_margin() -> None:
    nodes = [
        Node(id="a", label="a", role="r", kind="component", trust="trusted", files=["a.py"]),
        Node(id="b", label="b", role="r", kind="component", trust="trusted", files=["a.py"]),
    ]
    pins = [Pin(on="a", kind=PinKind.HOW, question="q?", rubric=["x", "y"])] * QUESTION_COUNT
    placed = layout(
        Board(summary="s", nodes=nodes, edges=[Edge(source="a", target="b", label="")], pins=pins)
    )
    assert placed.positions["a"][0] == MARGIN
    assert placed.zone is None


def test_a_cycle_does_not_hang_the_layout() -> None:
    nodes = [
        Node(id=i, label=i, role="r", kind="component", trust="trusted", files=["a.py"])
        for i in "abc"
    ]
    edges = [Edge(source=s, target=t, label="") for s, t in ("ab", "bc", "ca")]
    pins = [Pin(on="a", kind=PinKind.HOW, question="q?", rubric=["x", "y"])] * QUESTION_COUNT
    assert layout(Board(summary="s", nodes=nodes, edges=edges, pins=pins)).width > 0


# -- what the page receives ------------------------------------------------------------


def _data(board: Board, mode: str = "defend") -> dict[str, Any]:
    return board_data(board, mode=mode, passing_score=70, description="staged changes", files=FILES)  # type: ignore[arg-type]


def test_rubrics_reach_the_page_only_in_interview_mode(board: Board) -> None:
    for mode in ("defend", "export"):
        page = render_page(_data(board, mode))
        assert all("rubric" not in p for p in _data(board, mode)["pins"])
        assert board.pins[1].rubric[1] not in page
    interview = _data(board, "interview")
    assert interview["pins"][1]["rubric"] == board.pins[1].rubric


def test_opposite_edges_are_drawn_side_by_side(board: Board) -> None:
    offsets = {e["id"]: e["offset"] for e in _data(board)["edges"]}
    assert offsets["client->handle"] == -offsets["handle->client"] != 0
    assert offsets["handle->allow"] == 0


def test_hostile_labels_cannot_escape_the_data_block(board: Board) -> None:
    hostile = _mutate(board)
    hostile.summary = "</script><script>alert(1)</script><!--"
    hostile.nodes[1].label = "\u2028</SCRIPT>"
    data = _data(hostile)
    page = render_page(data)
    block = re.search(r'<script type="application/json" id="board-data">(.*?)</script>', page, re.S)
    assert block is not None
    assert "<" not in block.group(1)
    assert json.loads(block.group(1)) == data
    assert page.count("<script") == 2  # the data block and grip's own script, nothing else


def test_csp_allows_exactly_the_inline_style_and_script(board: Board) -> None:
    page = render_page(_data(board))
    csp = content_security_policy(page)
    assert "default-src 'none'" in csp
    assert "unsafe-inline" not in csp
    assert csp.count("'sha256-") == 2
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


# -- the server ------------------------------------------------------------------------


class _Running:
    def __init__(self, server: WhiteboardServer) -> None:
        self.server = server
        self.outcome: dict[str, Any] | None = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        self.outcome = self.server.serve()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, http.client.HTTPResponse]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        sent = {"Host": f"127.0.0.1:{self.server.port}", **(headers or {})}
        conn.request(method, path, body=body, headers=sent)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data, response

    def post(self, payload: Any, **headers: str) -> tuple[int, dict[str, Any]]:
        origin = {"Origin": f"http://127.0.0.1:{self.server.port}"}
        sent = {"Content-Type": "application/json", **origin, **headers}
        status, data, _ = self.request(
            "POST", f"/{self.server.token}/answers", json.dumps(payload).encode(), sent
        )
        return status, json.loads(data)


@pytest.fixture
def served(board: Board) -> Iterator[tuple[_Running, list[dict[str, Any]]]]:
    seen: list[dict[str, Any]] = []

    def submit(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        if payload.get("fail"):
            raise ProviderError("model unavailable")
        if "answers" not in payload:
            raise ValueError("no answers")
        return {"score": 80, "passed": True}

    running = _Running(WhiteboardServer(render_page(_data(board)), submit))
    yield running, seen
    running.server.result = running.server.result or {}
    running.thread.join(timeout=5)


def test_server_serves_the_page_only_behind_the_token(
    served: tuple[_Running, list[dict[str, Any]]],
) -> None:
    running, _ = served
    status, body, response = running.request("GET", f"/{running.server.token}/")
    assert status == 200
    assert b"grip whiteboard" in body
    assert response.getheader("Content-Security-Policy", "").startswith("default-src 'none'")
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert running.request("GET", "/")[0] == 404
    assert running.request("GET", f"/{running.server.token}x/")[0] == 404


def test_server_rejects_other_hosts_and_origins(
    served: tuple[_Running, list[dict[str, Any]]],
) -> None:
    running, seen = served
    token = running.server.token
    evil_host = {"Host": f"evil.example:{running.server.port}"}
    assert running.request("GET", f"/{token}/", headers=evil_host)[0] == 403
    assert running.post({"answers": []}, Origin="http://evil.example")[0] == 403
    status, _, _ = running.request(
        "POST", f"/{token}/answers", b"{}", {"Content-Type": "application/json"}
    )
    assert status == 403
    assert seen == []


def test_server_validates_requests(served: tuple[_Running, list[dict[str, Any]]]) -> None:
    running, seen = served
    assert running.post({"answers": []}, **{"Content-Type": "text/plain"})[0] == 415
    origin = {"Origin": f"http://127.0.0.1:{running.server.port}"}
    too_big = b"x" * (MAX_BODY + 1)
    status, _, _ = running.request(
        "POST",
        f"/{running.server.token}/answers",
        too_big,
        {"Content-Type": "application/json", **origin},
    )
    assert status == 413
    assert running.post([1, 2])[0] == 400
    assert running.post({"nothing": True})[0] == 400
    assert len(seen) == 1  # only the dict reached submit


def test_server_lets_the_page_retry_after_a_provider_error_then_grades_once(
    served: tuple[_Running, list[dict[str, Any]]],
) -> None:
    running, _ = served
    status, payload = running.post({"answers": ["a"], "fail": True})
    assert (status, payload) == (502, {"error": "model unavailable"})
    assert running.post({"answers": ["a"]}) == (200, {"score": 80, "passed": True})
    running.thread.join(timeout=5)
    assert running.outcome == {"score": 80, "passed": True}


def test_server_stops_when_idle(board: Board) -> None:
    server = WhiteboardServer(render_page(_data(board)), lambda p: p, idle_seconds=0.2)
    assert server.serve() is None


# -- the command -----------------------------------------------------------------------


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GRIP_FAKE_SCRIPT", str(SCENARIO))
    (repo / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\n', "utf-8")
    (repo / "api").mkdir()
    (repo / "api" / "handlers.py").write_text("_buckets = {}\n", "utf-8")
    return repo


class _AutoServer:
    """Stands in for WhiteboardServer: hands in ``payload`` as soon as it is served."""

    payload: ClassVar[dict[str, Any]] = {}
    page: ClassVar[str] = ""

    def __init__(self, page: str, submit: Callable[[dict[str, Any]], dict[str, Any]], **_: Any):
        type(self).page = page
        self.submit = submit
        self.url = "http://127.0.0.1:1/token/"

    def serve(self) -> dict[str, Any]:
        return self.submit(type(self).payload)


def _run(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], *args: str) -> Any:
    _AutoServer.payload = payload
    monkeypatch.setattr("grip_hook.cli.WhiteboardServer", _AutoServer)
    return CliRunner().invoke(cli, ["whiteboard", "--no-open", *args])


GOOD = [
    "a fixed window allows twice the limit at the boundary",
    "new keys get a full bucket and memory grows without bound",
    "per process, and entries are never removed",
    "each server keeps its own buckets, and a restart resets them",
    "pass now explicitly and assert the 61st is refused",
]


def test_whiteboard_pass_is_remembered_for_the_push(
    in_repo: Path, monkeypatch: pytest.MonkeyPatch, run_git: Callable[..., str]
) -> None:
    run_git("add", "api/handlers.py")
    result = _run(monkeypatch, {"answers": GOOD})
    assert result.exit_code == 0, result.output
    assert "Defended" in result.output
    assert CliRunner().invoke(cli, ["check"]).exit_code == 0
    report = json.loads((in_repo / ".git" / "grip" / "last-report.json").read_text("utf-8"))
    assert report["score"] >= 70
    assert [q["focus"] for q in report["questions"]][1] == "malicious actor"
    again = _run(monkeypatch, {"answers": GOOD})
    assert "already passed" in again.output


def test_whiteboard_fail_exits_one_and_records_no_pass(
    in_repo: Path, monkeypatch: pytest.MonkeyPatch, run_git: Callable[..., str]
) -> None:
    run_git("add", "api/handlers.py")
    result = _run(monkeypatch, {"answers": ["", "", "", "", ""]})
    assert result.exit_code == 1
    assert "below the pass mark" in result.output
    assert CliRunner().invoke(cli, ["check"]).exit_code == 1


def test_whiteboard_interview_scores_ticks_and_records_nothing(
    in_repo: Path, monkeypatch: pytest.MonkeyPatch, run_git: Callable[..., str]
) -> None:
    run_git("add", "api/handlers.py")
    ticks = [[True, True, True]] * 4 + [[True, False, False]]
    result = _run(monkeypatch, {"ticks": ticks}, "--interview")
    assert result.exit_code == 0, result.output
    assert "Interview score: 87/100 (not recorded)" in result.output
    assert "Every new key starts with a full bucket" in _AutoServer.page
    assert not (in_repo / ".git" / "grip" / "last-report.json").exists()


def test_whiteboard_export_writes_a_page_without_rubrics(
    in_repo: Path, run_git: Callable[..., str]
) -> None:
    run_git("add", "api/handlers.py")
    out = in_repo / "board.html"
    result = CliRunner().invoke(cli, ["whiteboard", "--export", str(out)])
    assert result.exit_code == 0, result.output
    page = out.read_text("utf-8")  # not the platform default: cp1252 cannot decode it
    assert '"mode": "export"' in page
    assert "Every new key starts with a full bucket" not in page


def test_whiteboard_with_nothing_staged(in_repo: Path) -> None:
    result = CliRunner().invoke(cli, ["whiteboard", "--no-open"])
    assert result.exit_code == 0
    assert "nothing to draw" in result.output


def test_bad_submissions_are_rejected_before_grading(
    in_repo: Path, monkeypatch: pytest.MonkeyPatch, run_git: Callable[..., str]
) -> None:
    run_git("add", "api/handlers.py")
    for payload in ({"answers": "text"}, {"answers": ["a"] * 6}, {"answers": ["x" * 4001]}):
        result = _run(monkeypatch, payload)
        assert isinstance(result.exception, ValueError)
    result = _run(monkeypatch, {"ticks": [[True]]}, "--interview")
    assert isinstance(result.exception, ValueError)
