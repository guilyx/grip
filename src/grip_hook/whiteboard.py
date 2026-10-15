"""The whiteboard: a change drawn as parts and connections, with questions pinned to it.

``grip whiteboard`` asks the provider for a :class:`Board`: the parts of the change
(nodes), how they connect (edges), which inputs are untrusted, and exactly
``QUESTION_COUNT`` questions pinned to specific parts, each with a hidden rubric. grip
checks the board against the diff before anything is shown (:func:`check_board`), lays
it out itself (:func:`layout`), and grades the answers with the provider's ordinary
``grade`` call, so the Grip Score means the same thing as in the terminal quiz.

Model output is untrusted. Every string the browser shows is inserted as text, never as
markup; this module also rejects labels with control characters and caps their length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from grip_hook import POINTS_PER_QUESTION, QUESTION_COUNT
from grip_hook.errors import ProviderError
from grip_hook.models import Difficulty, Question

if TYPE_CHECKING:
    from grip_hook.git import Diff
    from grip_hook.providers.base import Provider

MAX_NODES = 8
MAX_EDGES = 12
MAX_LABEL = 40
MAX_ROLE = 60
MAX_QUESTION = 300

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class PinKind(StrEnum):
    """What a pinned question asks. The first four are the whiteboard defense."""

    WHY = "why this way"
    ADVERSARY = "malicious actor"
    DATA = "data structure"
    FAILURE = "where it fails"
    VERIFY = "how you know"
    HOW = "how it works"


ALWAYS_REQUIRED = (PinKind.WHY, PinKind.FAILURE, PinKind.VERIFY)
"""Kinds every board must ask, whatever the diff."""


class Node(BaseModel):
    """One part of the change, as it would be drawn on a whiteboard."""

    id: str = Field(description="Short lowercase slug, unique on the board, e.g. 'rate-limit'.")
    label: str = Field(
        description=f"What a person would write in the box, at most {MAX_LABEL} characters."
    )
    role: str = Field(description=f"What it does, one line, at most {MAX_ROLE} characters.")
    kind: Literal["component", "store", "external", "context"] = Field(
        description=(
            "'component': code the diff changes. 'store': data the diff adds or changes "
            "(a map, table, cache, file, queue). 'external': a caller, user, service or "
            "clock outside the code. 'context': existing code the change calls, unchanged."
        )
    )
    trust: Literal["trusted", "untrusted"] = Field(
        description=(
            "'untrusted' for anything an outsider controls: request data, user input, "
            "uploaded files, third-party responses."
        ),
    )
    files: list[str] = Field(
        description="Paths from the diff this part lives in; empty for external and context.",
    )


class Edge(BaseModel):
    """What flows from one part to another."""

    source: str = Field(description="Id of the node the arrow starts at.")
    target: str = Field(description="Id of the node the arrow points to.")
    label: str = Field(description=f"What flows, at most {MAX_LABEL} characters.")


class Pin(BaseModel):
    """A question pinned to a node or an edge."""

    on: str = Field(description="A node id, or an edge written 'source->target'.")
    kind: PinKind
    question: str = Field(
        description=(
            "One specific question a senior engineer would ask at the whiteboard, answerable "
            "in one to three sentences. Never ask for line numbers, names or exact strings."
        )
    )
    rubric: list[str] = Field(
        description=(
            "Two to four concrete points a complete answer covers. Hidden from the developer."
        ),
        min_length=2,
        max_length=4,
    )


class Board(BaseModel):
    """The whiteboard for one change."""

    summary: str = Field(description="One or two sentences on what the change does.")
    nodes: list[Node] = Field(description=f"Two to {MAX_NODES} parts.")
    edges: list[Edge] = Field(description=f"At most {MAX_EDGES} connections.")
    pins: list[Pin] = Field(
        description=f"Exactly {QUESTION_COUNT} questions, each pinned to a node or edge.",
        min_length=QUESTION_COUNT,
        max_length=QUESTION_COUNT,
    )

    def edge_key(self, edge: Edge) -> str:
        """How pins refer to ``edge``."""
        return f"{edge.source}->{edge.target}"

    def untrusted(self) -> set[str]:
        """Ids of the untrusted nodes."""
        return {n.id for n in self.nodes if n.trust == "untrusted"}

    def crosses_trust(self, edge: Edge) -> bool:
        """Whether ``edge`` connects an untrusted node to a trusted one."""
        bad = self.untrusted()
        return (edge.source in bad) != (edge.target in bad)

    def required_kinds(self) -> list[PinKind]:
        """The kinds this board has to ask, given its parts."""
        kinds = list(ALWAYS_REQUIRED)
        if self.untrusted():
            kinds.append(PinKind.ADVERSARY)
        if any(n.kind == "store" for n in self.nodes):
            kinds.append(PinKind.DATA)
        return kinds

    def questions(self) -> list[Question]:
        """The pins as quiz questions, so the provider's ``grade`` can score them."""
        return [
            Question(
                question=p.question,
                rubric="\n".join(f"- {point}" for point in p.rubric),
                focus=p.kind.value,
            )
            for p in self.pins
        ]


def _text_problem(what: str, text: str, limit: int) -> str | None:
    if not text.strip():
        return f"{what} is empty"
    if len(text) > limit:
        return f"{what} is longer than {limit} characters"
    if _CONTROL_RE.search(text):
        return f"{what} contains control characters"
    return None


def _check_nodes(board: Board, files: set[str]) -> list[str]:
    errors: list[str] = []
    if not 2 <= len(board.nodes) <= MAX_NODES:
        errors.append(f"the board has {len(board.nodes)} nodes; it needs 2 to {MAX_NODES}")
    ids: set[str] = set()
    for node in board.nodes:
        if not _ID_RE.match(node.id):
            errors.append(f"node id {node.id!r} must be a short lowercase slug")
        if node.id in ids:
            errors.append(f"node id {node.id!r} is used twice")
        ids.add(node.id)
        for what, text, limit in (("label", node.label, MAX_LABEL), ("role", node.role, MAX_ROLE)):
            problem = _text_problem(f"node {node.id!r} {what}", text, limit)
            if problem:
                errors.append(problem)
        if node.kind in {"component", "store"}:
            if not node.files:
                errors.append(f"node {node.id!r} is changed code but lists no files")
            unknown = [f for f in node.files if files and f not in files]
            if unknown:
                errors.append(f"node {node.id!r} lists files not in the diff: {', '.join(unknown)}")
    return errors


def _check_edges(board: Board) -> list[str]:
    errors: list[str] = []
    if len(board.edges) > MAX_EDGES:
        errors.append(f"the board has {len(board.edges)} edges; at most {MAX_EDGES}")
    ids = {n.id for n in board.nodes}
    keys: set[str] = set()
    for edge in board.edges:
        key = board.edge_key(edge)
        if edge.source not in ids or edge.target not in ids:
            errors.append(f"edge {key!r} points at a node that does not exist")
        if edge.source == edge.target:
            errors.append(f"edge {key!r} connects a node to itself")
        if key in keys:
            errors.append(f"edge {key!r} appears twice")
        keys.add(key)
        problem = _text_problem(f"edge {key!r} label", edge.label, MAX_LABEL)
        if problem:
            errors.append(problem)
    return errors


def _check_pins(board: Board) -> list[str]:
    errors: list[str] = []
    if len(board.pins) != QUESTION_COUNT:
        errors.append(f"the board has {len(board.pins)} pins; it needs exactly {QUESTION_COUNT}")
    targets = {n.id for n in board.nodes} | {board.edge_key(e) for e in board.edges}
    seen: list[PinKind] = []
    for i, pin in enumerate(board.pins, 1):
        if pin.on not in targets:
            errors.append(f"pin {i} is on {pin.on!r}, which is not a node id or an edge")
        problem = _text_problem(f"pin {i} question", pin.question, MAX_QUESTION)
        if problem:
            errors.append(problem)
        if any(not point.strip() for point in pin.rubric):
            errors.append(f"pin {i} has an empty rubric point")
        if pin.kind in seen and pin.kind is not PinKind.HOW:
            errors.append(f"two pins ask {pin.kind.value!r}; only 'how it works' may repeat")
        seen.append(pin.kind)
    errors.extend(
        f"no pin asks {kind.value!r}, which this board needs"
        for kind in board.required_kinds()
        if kind not in seen
    )
    return errors


def check_board(board: Board, diff_files: tuple[str, ...] | list[str]) -> list[str]:
    """Everything wrong with ``board`` for a diff touching ``diff_files``; empty when valid."""
    return [*_check_nodes(board, set(diff_files)), *_check_edges(board), *_check_pins(board)]


class BoardError(ProviderError):
    """The provider could not produce a valid board."""


def generate_board(provider: Provider, diff: Diff, difficulty: Difficulty) -> Board:
    """Ask ``provider`` for a board, check it, and ask once more with the errors if needed."""
    board = provider.generate_board(diff, difficulty, ())
    errors = check_board(board, diff.files)
    if not errors:
        return board
    board = provider.generate_board(diff, difficulty, tuple(errors))
    errors = check_board(board, diff.files)
    if errors:
        listed = "; ".join(errors[:5])
        raise BoardError(
            f"the model could not draw a valid whiteboard ({listed}). `grip quiz` still works."
        )
    return board


# --------------------------------------------------------------------------- layout

NODE_W, NODE_H = 184, 66
GAP_X, GAP_Y = 150, 54  # room for an edge label between two columns
MARGIN = 40


@dataclass(frozen=True)
class Placement:
    """Where every node goes, in board pixels."""

    positions: dict[str, tuple[int, int]]
    width: int
    height: int
    zone: tuple[int, int, int, int] | None
    """The untrusted area (x, y, width, height), when the board has untrusted nodes."""


def _columns(board: Board) -> dict[str, int]:
    """Column per node: untrusted on the left, then the flow of the edges."""
    order = [n.id for n in board.nodes]
    bad = board.untrusted()
    preds: dict[str, list[str]] = {i: [] for i in order}
    succs: dict[str, list[str]] = {i: [] for i in order}
    for e in board.edges:
        if e.source in preds and e.target in preds and e.target not in bad:
            preds[e.target].append(e.source)
            succs[e.source].append(e.target)
    col = {i: (0 if i in bad else 1) for i in order}
    # Longest path, bounded so a cycle cannot loop forever.
    for _ in range(len(order)):
        changed = False
        for i in order:
            if i in bad:
                continue
            want = max((col[p] + 1 for p in preds[i]), default=col[i])
            if want > col[i]:
                col[i], changed = want, True
        if not changed:
            break
    # A trusted external with nothing feeding it (a clock, a config service) sits above
    # the first thing it feeds instead of taking a column of its own.
    nodes = {n.id: n for n in board.nodes}
    for i in order:
        if nodes[i].kind == "external" and i not in bad and not preds[i] and succs[i]:
            col[i] = min(col[s] for s in succs[i])
    # Close the gaps that moves leave behind, keeping the order of the columns.
    rank = {c: k for k, c in enumerate(sorted(set(col.values())))}
    return {i: rank[c] for i, c in col.items()}


_ROW_ORDER = {"external": 0, "component": 1, "store": 2, "context": 3}


def layout(board: Board) -> Placement:
    """A deterministic layered layout: same board, same picture."""
    col = _columns(board)
    order = {n.id: k for k, n in enumerate(board.nodes)}
    by_col: dict[int, list[str]] = {}
    for n in sorted(board.nodes, key=lambda n: (_ROW_ORDER[n.kind], order[n.id])):
        by_col.setdefault(col[n.id], []).append(n.id)
    rows = max(len(ids) for ids in by_col.values())
    height = MARGIN * 2 + rows * NODE_H + (rows - 1) * GAP_Y + 24
    positions: dict[str, tuple[int, int]] = {}
    for c, ids in by_col.items():
        top = MARGIN + 24 + ((rows - len(ids)) * (NODE_H + GAP_Y)) // 2
        for r, node_id in enumerate(ids):
            positions[node_id] = (MARGIN + c * (NODE_W + GAP_X), top + r * (NODE_H + GAP_Y))
    columns = max(col.values()) + 1
    width = MARGIN * 2 + columns * NODE_W + (columns - 1) * GAP_X
    zone = None
    if board.untrusted():
        zone = (MARGIN - 20, MARGIN - 10, NODE_W + 40, height - 2 * MARGIN + 20)
    return Placement(positions, width, height, zone)


# --------------------------------------------------------------------------- the page data

Mode = Literal["defend", "interview", "export"]


def board_data(
    board: Board,
    *,
    mode: Mode,
    passing_score: int,
    description: str,
    files: list[str],
) -> dict[str, Any]:
    """What the page receives. Rubrics are included only in interview mode."""
    placed = layout(board)
    edge_set = {board.edge_key(e) for e in board.edges}
    return {
        "mode": mode,
        "summary": board.summary,
        "description": description,
        "files": files,
        "passing_score": passing_score,
        "points_per_question": POINTS_PER_QUESTION,
        "size": {"width": placed.width, "height": placed.height},
        "zone": list(placed.zone) if placed.zone else None,
        "node": {"width": NODE_W, "height": NODE_H},
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "role": n.role,
                "kind": n.kind,
                "trust": n.trust,
                "files": n.files,
                "x": placed.positions[n.id][0],
                "y": placed.positions[n.id][1],
            }
            for n in board.nodes
        ],
        "edges": [
            {
                "id": board.edge_key(e),
                "source": e.source,
                "target": e.target,
                "label": e.label,
                "crosses_trust": board.crosses_trust(e),
                # Draw a pair of opposite arrows side by side instead of on top of each other.
                "offset": (-13 if e.source < e.target else 13)
                if f"{e.target}->{e.source}" in edge_set
                else 0,
            }
            for e in board.edges
        ],
        "pins": [
            {
                "on": p.on,
                "kind": p.kind.value,
                "question": p.question,
                **({"rubric": p.rubric} if mode == "interview" else {}),
            }
            for p in board.pins
        ],
    }


__all__ = [
    "ALWAYS_REQUIRED",
    "Board",
    "BoardError",
    "Edge",
    "Mode",
    "Node",
    "Pin",
    "PinKind",
    "Placement",
    "board_data",
    "check_board",
    "generate_board",
    "layout",
]
