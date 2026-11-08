"""
CinePlayer — PlayerController: alles rond libmpv, zonder widgets.

- `attach(wid)` koppelt mpv één keer aan het native videovenster
- `play(path)` speelt een bestand in een oneindige herhaling (loop-file)
- 500 ms-poll → `position_changed(pos, dur, paused)` voor de statusmelding aan het paneel
- geen OSD, geen OSC, geen toetsbindingen: puur beeld en geluid
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

_log = logging.getLogger("cineplayer.mpv")

POLL_MS = 500


class PlayerController(QObject):
    position_changed = pyqtSignal(float, float, bool)   # pos, dur (0 = onbekend), paused
    file_loaded = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        import mpv
        self.mpv = mpv.MPV(
            log_handler=self._on_log, loglevel="error",
            input_default_bindings=False, input_vo_keyboard=False, osc=False,
            hwdec="auto-safe",
        )
        self.mpv["keep-open"] = True
        self.mpv["loop-file"] = "inf"
        self.mpv["osd-level"] = 0
        self.mpv["cursor-autohide"] = "always"
        self.mpv["cache"] = "yes"
        self.path: str = ""
        self.duration: float = 0.0
        self._attached = False
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._tick)
        self._poll.start()

    def _on_log(self, level: str, component: str, message: str) -> None:
        if level in ("error", "fatal"):
            _log.error("mpv [%s] %s", component, message.rstrip())

    # ── koppeling / laden ───────────────────────────────────────────────
    def attach(self, wid: int) -> None:
        if not self._attached:
            self.mpv["wid"] = int(wid)
            self._attached = True

    def play(self, path: str, *, loop: bool = True) -> bool:
        self.path = path
        self.duration = 0.0
        try:
            self.mpv["loop-file"] = "inf" if loop else "no"
            self.mpv.play(path)
            self.mpv.pause = False
        except Exception as e:  # noqa: BLE001 — mpv-fouten mogen de kiosk niet laten crashen
            _log.error("Laden mislukt (%s): %s", path, e)
            self.error.emit(str(e))
            self.path = ""
            return False
        self.file_loaded.emit(path)
        return True

    def stop(self) -> None:
        try:
            self.mpv.command("stop")
        except Exception:  # noqa: BLE001
            pass
        self.path = ""
        self.duration = 0.0

    def set_paused(self, paused: bool) -> None:
        if self.path:
            try:
                self.mpv.pause = paused
            except Exception:  # noqa: BLE001
                pass

    def terminate(self) -> None:
        self._poll.stop()
        try:
            self.mpv.terminate()
        except Exception:  # noqa: BLE001
            pass

    # ── status ──────────────────────────────────────────────────────────
    @property
    def loaded(self) -> bool:
        return bool(self.path)

    @property
    def paused(self) -> bool:
        try:
            return bool(self.mpv.pause)
        except Exception:  # noqa: BLE001
            return True

    def position(self) -> float:
        try:
            return float(self.mpv.time_pos or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    def _tick(self) -> None:
        if not self.path:
            return
        try:
            pos = self.mpv.time_pos
            dur = self.mpv.duration
            paused = bool(self.mpv.pause)
        except Exception:  # noqa: BLE001
            return
        if dur and dur > 0:
            self.duration = float(dur)
        self.position_changed.emit(float(pos or 0.0), self.duration, paused)
