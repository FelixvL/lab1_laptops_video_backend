import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from cineplayer.config import State, load_config
from cineplayer.remote import RemoteError, post_status


def test_load_config_resolves_relative_videos_dir(tmp_path: Path) -> None:
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"server_url": "http://x/api.php", "api_key": "k", "videos_dir": "films",
                             "poll_seconds": 0.2}), encoding="utf-8")
    cfg = load_config(f)
    assert cfg.videos_dir == tmp_path / "films"
    assert cfg.remote_enabled
    assert cfg.poll_seconds == 1.0          # ondergrens


def test_load_config_defaults_without_file(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "geen.json")
    assert not cfg.remote_enabled
    assert cfg.videos_dir == tmp_path / "videos"


def test_state_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    st = State(mode="website", video="a/b.mp4", url="https://voorbeeld.nl", seq=7, rescan=2)
    assert st.save(p)
    back = State.load(p)
    assert (back.mode, back.video, back.url, back.seq, back.rescan) == ("website", "a/b.mp4", "https://voorbeeld.nl", 7, 2)
    assert State.load(tmp_path / "ontbreekt.json").seq == -1


class _Handler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.received.append({"path": self.path, "key": self.headers.get("X-Api-Key"), "body": body})
        if self.headers.get("X-Api-Key") != "geheim":
            out, code = {"ok": False, "error": "Ongeldige API-sleutel"}, 401
        else:
            out, code = {"ok": True, "command": {"seq": 3, "mode": "video", "video": "x.mp4"}}, 200
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a) -> None:
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api.php"
    srv.shutdown()


def test_post_status_returns_command(server: str) -> None:
    _Handler.received.clear()
    cmd = post_status(server, "geheim", {"mode": "video", "videos": ["x.mp4"]}, timeout=3)
    assert cmd == {"seq": 3, "mode": "video", "video": "x.mp4"}
    assert _Handler.received[0]["path"] == "/api.php?action=poll"
    assert _Handler.received[0]["body"]["videos"] == ["x.mp4"]


def test_post_status_bad_key_raises(server: str) -> None:
    with pytest.raises(RemoteError, match="401"):
        post_status(server, "fout", {}, timeout=3)


def test_post_status_unreachable_raises() -> None:
    with pytest.raises(RemoteError):
        post_status("http://127.0.0.1:9/api.php", "k", {}, timeout=1)
