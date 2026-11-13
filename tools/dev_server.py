#!/usr/bin/env python3
"""
Lokale testserver die php/api.php nabootst, zodat je de speler kunt testen
zonder webhosting.   Starten:   python tools/dev_server.py [--port 8765]

Zet in settings.json tijdelijk:  "server_url": "http://127.0.0.1:8765/api.php"

Meerdere spelers: elke speler meldt zich met zijn "id"; opdrachten gaan per speler
(parameter player=<id>, zonder parameter de eerste bekende speler).

Opdrachten geven (geen login nodig, alleen lokaal):
  curl -X POST "http://127.0.0.1:8765/api.php?action=command&player=lp" -d '{"cmd":"play","video":"Testvideo A.mp4"}'
  curl -X POST "http://127.0.0.1:8765/api.php?action=command" -d '{"cmd":"next"}'
  curl -X POST "http://127.0.0.1:8765/api.php?action=command" -d '{"cmd":"seek","delta":30}'
  curl -X POST "http://127.0.0.1:8765/api.php?action=command" -d '{"cmd":"overlay","aan":true,"modus":"avond","geschiedenis":30}'
  curl -X POST "http://127.0.0.1:8765/api.php?action=command" -d '{"cmd":"uitlijnen","element":"titel","dy":-0.5}'
  curl        "http://127.0.0.1:8765/api.php?action=players"
  curl        "http://127.0.0.1:8765/api.php?action=status&player=lp"
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API_KEY = json.loads((Path(__file__).resolve().parent.parent / "settings.json").read_text("utf-8")).get("api_key", "")

DEFAULT_COMMAND: dict = {"seq": 0, "video": None, "rescan": 0, "issued_at": 0, "seek": {"n": 0, "delta": 0},
                         "overlay": {"aan": False, "modus": "highlight", "geschiedenis": 30,
                                     "uitlijning": {"geheel": {"x": 0, "y": 7, "s": 95}, "titel": {"x": 0, "y": 3, "s": 60},
                                                    "lijst": {"x": 1, "y": 0, "s": 55}}}}
_lock = threading.Lock()
_players: dict[str, dict] = {}      # id → {"status": {}, "command": {}, "preview": bytes}


def _clean_id(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "", s)[:40].strip(".")
    return s or "onbekend"


def _player(pid: str) -> dict:
    return _players.setdefault(pid, {"status": {}, "command": copy.deepcopy(DEFAULT_COMMAND), "preview": b""})


def _pick(q: dict) -> str | None:
    pid = (q.get("player") or [""])[0]
    if pid:
        return _clean_id(pid)
    return next(iter(_players), None)


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
            d = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            d = {}
        return d if isinstance(d, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        action = (q.get("action") or [""])[0]
        with _lock:
            if action == "players":
                self._send({"ok": True, "players": [
                    {"id": pid, "online": time.time() - p["status"].get("updated_at", 0) <= 12,
                     "status": p["status"], "command": p["command"]} for pid, p in sorted(_players.items())],
                    "server_time": int(time.time())})
                return
            pid = _pick(q)
            if action == "preview":
                data = _players.get(pid, {}).get("preview", b"") if pid else b""
                self.send_response(200 if data else 404)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(data)
                return
            if action == "status":
                p = _player(pid) if pid else {"status": {}, "command": copy.deepcopy(DEFAULT_COMMAND)}
                online = time.time() - p["status"].get("updated_at", 0) <= 12
                self._send({"ok": True, "player": pid, "online": online, "status": p["status"],
                            "command": p["command"], "server_time": int(time.time())})
                return
        self._send({"ok": False, "error": "Onbekende actie"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        action = (q.get("action") or [""])[0]
        body = self._body()
        if action == "poll":
            key = self.headers.get("X-Api-Key") or body.pop("key", None)
            if key != API_KEY:
                self._send({"ok": False, "error": "Ongeldige API-sleutel"}, 401)
                return
            pid = _clean_id(str(body.get("id") or body.get("hostname") or ""))
            with _lock:
                p = _player(pid)
                if isinstance(body.get("overlay"), dict) and body["overlay"].get("preview"):
                    p["preview"] = base64.b64decode(body["overlay"].pop("preview"))
                body["id"] = pid
                p["status"].update(body)
                p["status"]["updated_at"] = int(time.time())
                cmd = dict(p["command"])
            self._send({"ok": True, "command": cmd, "server_time": int(time.time())})
            return
        if action == "command":
            with _lock:
                pid = _pick(q) or _clean_id(str(body.get("player") or ""))
                p = _player(pid)
                _command, _status = p["command"], p["status"]
                videos = [v for v in _status.get("videos", []) if isinstance(v, str)]
                c = body.get("cmd")
                if c == "play":
                    _command["video"] = body.get("video")
                elif c in ("next", "prev") and videos:
                    try:
                        i = videos.index(_command.get("video"))
                    except ValueError:
                        i = -1 if c == "next" else 0
                    i = (i + 1) % len(videos) if c == "next" else (i - 1) % len(videos)
                    _command["video"] = videos[i]
                elif c == "rescan":
                    _command["rescan"] = int(_command.get("rescan", 0)) + 1
                elif c == "seek":
                    sk = _command.setdefault("seek", {"n": 0, "delta": 0})
                    sk["n"] = int(sk.get("n", 0)) + 1
                    sk["delta"] = max(-600, min(600, int(body.get("delta", 30))))
                elif c == "overlay":
                    ov = _command["overlay"]
                    if "aan" in body:
                        ov["aan"] = bool(body["aan"])
                    if body.get("modus") in ("highlight", "avond", "gewoon"):
                        ov["modus"] = body["modus"]
                    if "geschiedenis" in body:
                        ov["geschiedenis"] = max(0, min(720, int(body["geschiedenis"])))
                elif c == "uitlijnen" and body.get("element") in ("geheel", "titel", "lijst"):
                    u = _command["overlay"]["uitlijning"][body["element"]]
                    if body.get("reset"):
                        u.update(copy.deepcopy(DEFAULT_COMMAND["overlay"]["uitlijning"][body["element"]]))
                    else:
                        u["x"] = max(-60, min(60, round(u["x"] + float(body.get("dx", 0)), 2)))
                        u["y"] = max(-60, min(60, round(u["y"] + float(body.get("dy", 0)), 2)))
                        u["s"] = max(20, min(400, int(round(u["s"] + float(body.get("ds", 0))))))
                else:
                    self._send({"ok": False, "error": "Onbekende opdracht"}, 400)
                    return
                _command["seq"] = int(_command.get("seq", 0)) + 1
                _command["issued_at"] = int(time.time())
                cmd = dict(_command)
            print(f"opdracht voor {pid}:", json.dumps(cmd, ensure_ascii=False), flush=True)
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
    print(f"dev_server op http://127.0.0.1:{a.port}/api.php  (Ctrl+C stopt)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
