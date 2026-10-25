"""Serve one whiteboard on 127.0.0.1 until it is handed in.

Threat model, in short: other websites in the same browser, other local users, and a
board whose text a prompt injection may have shaped. So:

* bind to ``127.0.0.1`` on a random port, and put a 128-bit random token in every path;
* accept only a ``Host`` of ``127.0.0.1:PORT`` or ``localhost:PORT`` (DNS rebinding) and,
  on ``POST``, only an ``Origin`` of the same (cross-site posts);
* serve one page with a Content-Security-Policy that allows exactly its own inline style
  and script, by hash, the fonts bundled with grip, and no network access beyond this server;
* embed the board as JSON with ``<`` escaped, so no label can close the script tag;
* grade at most once, cap request bodies, and stop after a stretch of inactivity. Only
  requests that carry the token count as activity; the open page sends one every minute
  (``GET /TOKEN/heartbeat``), so a long think does not time out but a closed tab does.

Requests run on threads, because browsers open spare connections that would otherwise stall
a single-threaded server; a lock makes grading happen once, never concurrently.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any

from grip_hook.errors import GripError

MAX_BODY = 64 * 1024
IDLE_SECONDS = 20 * 60
FONTS = ("archivo.woff2", "instrument-sans.woff2", "jetbrains-mono.woff2")
"""The fonts the page uses, subset to Latin, served from ``/TOKEN/fonts/``. All three are
under the SIL Open Font License; the license texts sit next to them in the package."""
HEARTBEAT_PATH = "heartbeat"
"""What the open page fetches every minute to keep the server from timing out."""

Submit = Callable[[dict[str, Any]], dict[str, Any]]
"""Grades a submission and returns the result for the page. Raises ``ValueError`` for a bad
submission and :class:`GripError` when grading fails; either way the page may try again."""


def _template() -> str:
    return resources.files("grip_hook").joinpath("whiteboard.html").read_text("utf-8")


def font(name: str) -> bytes:
    """The bundled font file ``name``, one of :data:`FONTS`."""
    return resources.files("grip_hook").joinpath("fonts").joinpath(name).read_bytes()


def _hash(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode()}'"


def content_security_policy(page: str) -> str:
    """The CSP for ``page``: its own inline style and script, grip's fonts, nothing else."""
    styles = re.findall(r"<style>(.*?)</style>", page, re.S)
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    return "; ".join(
        [
            "default-src 'none'",
            "style-src " + " ".join(_hash(s) for s in styles),
            "script-src " + " ".join(_hash(s) for s in scripts),
            "font-src 'self'",
            "connect-src 'self'",
            "img-src 'self' data:",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        ]
    )


def render_page(data: dict[str, Any], *, embed_fonts: bool = False) -> str:
    """The page with ``data`` embedded as JSON that cannot break out of its script tag.

    The page loads its fonts from the server. ``embed_fonts`` puts them in the page as
    ``data:`` URLs instead, for a page that has to stand alone, like an export.
    """
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = _template()
    if embed_fonts:
        for name in FONTS:
            encoded = base64.b64encode(font(name)).decode()
            page = page.replace(f'url("fonts/{name}")', f'url("data:font/woff2;base64,{encoded}")')
    return page.replace("__BOARD_DATA__", payload)


class WhiteboardServer:
    """Serves ``page`` and hands submissions to ``submit`` until one succeeds."""

    def __init__(
        self, page: str, submit: Submit, *, port: int = 0, idle_seconds: float = IDLE_SECONDS
    ) -> None:
        self.page = page.encode("utf-8")
        self.csp = content_security_policy(page)
        self.submit = submit
        self.token = secrets.token_urlsafe(16)
        self.idle_seconds = idle_seconds
        self.result: dict[str, Any] | None = None
        self._last_activity = time.monotonic()
        self._lock = threading.Lock()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self._httpd.daemon_threads = True
        self._httpd.timeout = 0.5
        self.port = self._httpd.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    @property
    def url(self) -> str:
        """Where the page lives."""
        return f"http://127.0.0.1:{self.port}/{self.token}/"

    def serve(self) -> dict[str, Any] | None:
        """Handle requests until a submission is graded or the server sits idle too long.

        Returns the graded result, or ``None`` on timeout. ``KeyboardInterrupt`` propagates.
        """
        try:
            while self.result is None:
                self._httpd.handle_request()
                if time.monotonic() - self._last_activity > self.idle_seconds:
                    return None
            return self.result
        finally:
            self._httpd.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "grip"
            sys_version = ""
            timeout = 10

            def log_message(self, format: str, *args: Any) -> None:
                """Keep the terminal quiet."""

            def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                if content_type.startswith("text/html"):
                    self.send_header("Content-Security-Policy", server.csp)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
                self._send(status, json.dumps(payload).encode(), "application/json")

            def _host_ok(self) -> bool:
                return self.headers.get("Host", "") in server.hosts

            def _touch(self) -> None:
                """Count this request as activity, if it is for this board."""
                if self._host_ok() and self.path.startswith(f"/{server.token}/"):
                    server._last_activity = time.monotonic()

            def do_GET(self) -> None:
                self._touch()
                if not self._host_ok():
                    self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
                elif self.path == f"/{server.token}/":
                    self._send(HTTPStatus.OK, server.page, "text/html; charset=utf-8")
                elif self.path == f"/{server.token}/{HEARTBEAT_PATH}":
                    self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")
                elif self.path.removeprefix(f"/{server.token}/fonts/") in FONTS:
                    name = self.path.removeprefix(f"/{server.token}/fonts/")
                    self._send(HTTPStatus.OK, font(name), "font/woff2")
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

            def _rejection(self) -> tuple[HTTPStatus, str] | None:
                """Why this POST cannot be graded, or ``None`` when it can."""
                if not self._host_ok() or self.headers.get("Origin") not in server.origins:
                    return HTTPStatus.FORBIDDEN, "forbidden"
                if self.path != f"/{server.token}/answers":
                    return HTTPStatus.NOT_FOUND, "not found"
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON"
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    length = -1
                if not 0 <= length <= MAX_BODY:
                    return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large"
                return None if length else (HTTPStatus.BAD_REQUEST, "empty body")

            def do_POST(self) -> None:
                self._touch()
                rejection = self._rejection()
                if rejection is not None:
                    self._json(rejection[0], {"error": rejection[1]})
                    return
                body = self.rfile.read(int(self.headers["Content-Length"]))
                with server._lock:
                    status, result = self._grade(body)
                    self._json(status, result)
                    if status == HTTPStatus.OK:
                        server.result = result

            def _grade(self, body: bytes) -> tuple[HTTPStatus, dict[str, Any]]:
                if server.result is not None:
                    return HTTPStatus.CONFLICT, {"error": "already handed in"}
                try:
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise ValueError("expected a JSON object")
                    return HTTPStatus.OK, server.submit(payload)
                except (ValueError, UnicodeDecodeError) as exc:
                    return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
                except GripError as exc:
                    return HTTPStatus.BAD_GATEWAY, {"error": str(exc)}

        return Handler


__all__ = [
    "FONTS",
    "HEARTBEAT_PATH",
    "IDLE_SECONDS",
    "MAX_BODY",
    "Submit",
    "WhiteboardServer",
    "content_security_policy",
    "font",
    "render_page",
]
