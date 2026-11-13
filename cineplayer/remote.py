"""
CinePlayer — verbinding met het PHP-paneel.

De speler meldt zich elke `poll_seconds` bij api.php?action=poll: hij stuurt zijn
status (video, positie, tekstlaag, videolijst) en krijgt de actuele opdracht terug.
Zo hoeft er op de laptop geen poort open en werkt het achter elke router.

`post_status()` is een gewone functie (testbaar zonder Qt); `Poller` draait die
in een QThread en geeft het resultaat via signalen door aan het hoofdvenster.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

_log = logging.getLogger("cineplayer.remote")


class RemoteError(Exception):
    pass


def post_status(server_url: str, api_key: str, status: dict[str, Any], timeout: float = 6.0) -> dict[str, Any]:
    """Stuur de status naar het paneel en geef de opdracht (dict) terug. Gooit RemoteError."""
    sep = "&" if "?" in server_url else "?"
    url = server_url if "action=" in server_url else f"{server_url}{sep}action=poll"
    body = json.dumps(status, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8",
        "X-Api-Key": api_key,
        "User-Agent": "CinePlayer/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error", "")
        except Exception:  # noqa: BLE001
            pass
        raise RemoteError(f"HTTP {e.code}{': ' + detail if detail else ''}") from e
    except urllib.error.URLError as e:
        raise RemoteError(f"geen verbinding ({e.reason})") from e
    except (TimeoutError, OSError) as e:
        raise RemoteError(f"netwerkfout ({e})") from e
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RemoteError("antwoord is geen JSON") from e
    if not isinstance(data, dict) or not data.get("ok"):
        raise RemoteError(str(data.get("error") if isinstance(data, dict) else "onbekend antwoord"))
    cmd = data.get("command")
    if not isinstance(cmd, dict):
        raise RemoteError("antwoord zonder opdracht")
    return cmd


class Poller(QThread):
    command_received = pyqtSignal(dict)      # opdracht van het paneel
    online_changed = pyqtSignal(bool)        # verbinding met het paneel aan/uit
    failed = pyqtSignal(str)                 # laatste foutmelding (voor het logboek / idle-scherm)

    def __init__(self, server_url: str, api_key: str, poll_seconds: float, timeout: float = 6.0,
                 parent=None) -> None:
        super().__init__(parent)
        self.server_url = server_url
        self.api_key = api_key
        self.poll_seconds = max(1.0, poll_seconds)
        self.timeout = timeout
        self._status: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._online: bool | None = None

    # ── vanuit de hoofdthread ───────────────────────────────────────────
    def update_status(self, status: dict[str, Any]) -> None:
        with self._lock:
            self._status = dict(status)

    def poll_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.wait(int(self.timeout * 1000) + 2000)

    # ── worker ──────────────────────────────────────────────────────────
    def run(self) -> None:  # noqa: D102
        while not self._stop.is_set():
            with self._lock:
                status = dict(self._status)
            try:
                cmd = post_status(self.server_url, self.api_key, status, self.timeout)
            except RemoteError as e:
                self._set_online(False, str(e))
                self.failed.emit(str(e))
            else:
                self._set_online(True)
                self.command_received.emit(cmd)
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def _set_online(self, online: bool, reason: str = "") -> None:
        if online != self._online:
            self._online = online
            self.online_changed.emit(online)
            if online:
                _log.info("paneel bereikbaar: %s", self.server_url)
            else:
                _log.warning("paneel niet bereikbaar (%s): %s", reason, self.server_url)
