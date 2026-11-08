#!/usr/bin/env python3
"""
Lokale testserver die php/api.php nabootst, zodat je de speler kunt testen
zonder webhosting.   Starten:   python tools/dev_server.py [--port 8765]

Zet in settings.json tijdelijk:  "server_url": "http://127.0.0.1:8765/api.php"

Opdrachten geven (geen login nodig, alleen lokaal):
  curl -X POST "http://127.0.0.1:8765/api.php?action=command" -d '{"cmd":"play","video":"Testvideo A.mp4"}'
  curl -X POST "http://127.0.0.1:8765/api.php?action=command" -d '{"cmd":"next"}'
  curl -X POST "http://127.0.0.1:8765/api.php?action=command" -d '{"cmd":"website"}'
  curl        "http://127.0.0.1:8765/api.php?action=status"
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API_KEY = json.loads((Path(__file__).resolve().parent.parent / "settings.json").read_text("utf-8")).get("api_key", "")
WEBSITE_URL = "https://0111.nl/lab-1/lobby-video/"

_lock = threading.Lock()
_status: dict = {}
_command: dict = {"seq": 0, "mode": "video", "video": None, "url": WEBSITE_URL, "rescan": 0, "issued_at": 0}


class Handler(BaseHTTPRequestHandler):
    def _send(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def do_GET(self) -> None:  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        if q.get("action") == ["status"]:
            with _lock:
                st, cmd = dict(_status), dict(_command)
            online = (time.time() - st.get("updated_at", 0)) <= 12
            self._send({"ok": True, "online": online, "status": st, "command": cmd, "server_time": int(time.time())})
            return
        self._send({"ok": False, "error": "Onbekende actie"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        action = (q.get("action") or [""])[0]
        body = self._body()
        if action == "poll":
            key = self.headers.get("X-Api-Key") or body.get("key")
            if key != API_KEY:
                self._send({"ok": False, "error": "Ongeldige API-sleutel"}, 401)
                return
            with _lock:
                _status.update(body)
                _status["updated_at"] = int(time.time())
                cmd = dict(_command)
            self._send({"ok": True, "command": cmd, "server_time": int(time.time())})
            return
        if action == "command":
            with _lock:
                videos = [v for v in _status.get("videos", []) if isinstance(v, str)]
                c = body.get("cmd")
                if c == "play":
                    _command.update(mode="video", video=body.get("video"))
                elif c in ("next", "prev") and videos:
                    try:
                        i = videos.index(_command.get("video"))
                    except ValueError:
                        i = -1 if c == "next" else 0
                    i = (i + 1) % len(videos) if c == "next" else (i - 1) % len(videos)
                    _command.update(mode="video", video=videos[i])
                elif c == "video":
                    if not _command.get("video") and videos:
                        _command["video"] = videos[0]
                    _command["mode"] = "video"
                elif c == "website":
                    _command.update(mode="website", url=body.get("url") or _command.get("url") or WEBSITE_URL)
                elif c == "rescan":
                    _command["rescan"] = int(_command.get("rescan", 0)) + 1
                else:
                    self._send({"ok": False, "error": "Onbekende opdracht"}, 400)
                    return
                _command["seq"] = int(_command.get("seq", 0)) + 1
                _command["issued_at"] = int(time.time())
                cmd = dict(_command)
            print("opdracht:", json.dumps(cmd, ensure_ascii=False), flush=True)
            self._send({"ok": True, "command": cmd})
            return
        self._send({"ok": False, "error": "Onbekende actie"}, 404)

    def log_message(self, fmt: str, *args) -> None:  # stiller
        if "action=poll" not in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"testserver op http://127.0.0.1:{a.port}/api.php  (Ctrl+C stopt)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
