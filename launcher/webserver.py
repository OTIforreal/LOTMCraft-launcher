"""Fallback shell: serve the interface on 127.0.0.1 and show it in an Edge/Chrome app window.

Used when the embedded WebView (pywebview + .NET) cannot start on a machine.
Every request needs a random per-run token, so web pages in other tabs cannot drive the launcher.
"""
import json
import mimetypes
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

BLOCKED = {"attach", "emit", "log", "status", "progress", "set_busy", "set_sink"}


class _Events:
    def __init__(self) -> None:
        self.items: list[tuple[int, dict]] = []
        self.counter = 0
        self.cond = threading.Condition()

    def push(self, event: dict) -> None:
        with self.cond:
            self.counter += 1
            self.items.append((self.counter, event))
            del self.items[:-400]
            self.cond.notify_all()

    def wait(self, after: int, timeout: float) -> list[dict]:
        with self.cond:
            if not any(i > after for i, _ in self.items):
                self.cond.wait(timeout)
            return [{"id": i, "event": e} for i, e in self.items if i > after]


def _browser() -> str | None:
    roots = [os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")]
    parts = [("Microsoft", "Edge", "Application", "msedge.exe"), ("Google", "Chrome", "Application", "chrome.exe")]
    for part in parts:
        for root in roots:
            if root and (Path(root).joinpath(*part)).is_file():
                return str(Path(root).joinpath(*part))
    return None


def run(api, web_root: Path, profile_dir: Path) -> None:
    token = secrets.token_urlsafe(24)
    events = _Events()
    api.set_sink(events.push)
    state = {"last_ping": 0.0}
    public = {name for name in dir(api) if not name.startswith("_") and callable(getattr(api, name)) and name not in BLOCKED}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _ok_host(self) -> bool:
            return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

        def _authorized(self) -> bool:
            return self._ok_host() and secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self._ok_host():
                self._send(403, b"")
                return
            parsed = urlparse(self.path)
            if parsed.path == "/events":
                if not self._authorized():
                    self._send(403, b"")
                    return
                after = int((parse_qs(parsed.query).get("after") or ["0"])[0])
                self._send(200, json.dumps({"events": events.wait(after, 20)}).encode("utf-8"))
                return
            relative = unquote(parsed.path).lstrip("/") or "index.html"
            target = (web_root / relative).resolve()
            if web_root.resolve() not in target.parents or not target.is_file():
                self._send(404, b"")
                return
            kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), kind)

        def do_POST(self) -> None:
            if not self._authorized():
                self._send(403, b"")
                return
            if self.path == "/ping":
                state["last_ping"] = time.time()
                self._send(200, b"{}")
                return
            name = self.path.removeprefix("/rpc/")
            if name not in public:
                self._send(404, b"{}")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                args = json.loads(self.rfile.read(length) or b"[]")
                result = getattr(api, name)(*args)
                payload = json.dumps(result, default=str).encode("utf-8")
            except Exception as error:
                payload = json.dumps({"ok": False, "error": str(error) or error.__class__.__name__}).encode("utf-8")
            self._send(200, payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/index.html#t={token}"

    browser = _browser()
    if browser:
        profile_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={profile_dir}", "--window-size=1280,800", "--no-first-run", "--disable-features=Translate"])
    else:
        webbrowser.open(url)

    started = time.time()
    while True:
        time.sleep(1)
        last = state["last_ping"]
        if last and time.time() - last > 20:
            break
        if not last and time.time() - started > 90:
            break
    server.shutdown()
