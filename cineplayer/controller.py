"""
CinePlayer — PlayerController: alles rond libmpv, zonder widgets.

- `attach(wid)` koppelt mpv één keer aan het native videovenster
- `play(path)` speelt een bestand in een oneindige herhaling (loop-file)
- 500 ms-poll → `position_changed(pos, dur, paused)` voor de statusmelding aan het paneel
- `set_overlay(QImage)` legt een beeldlaag (BGRA, premultiplied) over het beeld via
  mpv's overlay-add; None haalt hem weg. Na een nieuwe `play()` wordt hij opnieuw gezet.
- geen OSD, geen OSC, geen toetsbindingen: puur beeld en geluid
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

_log = logging.getLogger("cineplayer.mpv")

POLL_MS = 500
OVERLAY_ID = 1


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
        self._pending_start = 0.0       # hervatpositie, toegepast zodra de duur bekend is
        self._attached = False
        self._overlay = None            # QImage die mpv in gebruik heeft (moet in leven blijven)
        self._overlay_vorige = None     # de vorige, tot mpv zeker de nieuwe heeft
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

    def play(self, path: str, *, loop: bool = True, start: float = 0.0) -> bool:
        """Bestand laden. `start` > 0: zodra het geladen is daarheen springen (hervatten); tot die
        tijd staat het beeld op pauze zodat je het begin niet even ziet."""
        self.path = path
        self.duration = 0.0
        self._pending_start = float(start) if start and start > 1.0 else 0.0
        try:
            self.mpv["loop-file"] = "inf" if loop else "no"
            self.mpv.play(path)
            self.mpv.pause = self._pending_start > 0
        except Exception as e:  # noqa: BLE001 — mpv-fouten mogen de kiosk niet laten crashen
            _log.error("Laden mislukt (%s): %s", path, e)
            self.error.emit(str(e))
            self.path = ""
            return False
        self.file_loaded.emit(path)
        if self._overlay is not None:
            self._overlay_add(self._overlay)
        return True

    def stop(self) -> None:
        try:
            self.mpv.command("stop")
        except Exception:  # noqa: BLE001
            pass
        self.path = ""
        self.duration = 0.0
        self._pending_start = 0.0

    def seek(self, delta: float) -> bool:
        """Zoveel seconden vooruit (of terug) in het lopende bestand."""
        if not self.path:
            return False
        try:
            self.mpv.command("seek", float(delta), "relative")
            return True
        except Exception as e:  # noqa: BLE001
            _log.warning("seek %s: %s", delta, e)
            return False

    # ── tekstlaag ───────────────────────────────────────────────────────
    def set_overlay(self, image) -> None:
        """Beeldlaag over de video (QImage ARGB32_Premultiplied, zelfde maat als het videovenster)."""
        from PyQt6.QtGui import QImage
        if image is None:
            if self._overlay is not None:
                self._overlay_vorige, self._overlay = self._overlay, None
                try:
                    self.mpv.command("overlay-remove", OVERLAY_ID)
                except Exception as e:  # noqa: BLE001
                    _log.warning("overlay-remove: %s", e)
            return
        if image.format() != QImage.Format.Format_ARGB32_Premultiplied:
            image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._overlay_vorige, self._overlay = self._overlay, image
        self._overlay_add(image)

    def _overlay_add(self, image) -> None:
        addr = int(image.constBits())
        try:
            self.mpv.command("overlay-add", OVERLAY_ID, 0, 0, f"&{addr}", 0, "bgra",
                             image.width(), image.height(), image.bytesPerLine())
        except Exception as e:  # noqa: BLE001
            _log.warning("overlay-add: %s", e)

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
            if self._pending_start > 0:
                # hervatten: springen zodra het bestand geladen is; vlak bij het einde gewoon vooraan
                start = self._pending_start if self._pending_start < self.duration - 5 else 0.0
                self._pending_start = 0.0
                try:
                    if start > 0:
                        self.mpv.command("seek", start, "absolute")
                    self.mpv.pause = False
                except Exception as e:  # noqa: BLE001
                    _log.warning("hervatten op %.0f s mislukt: %s", start, e)
                return
        self.position_changed.emit(float(pos or 0.0), self.duration, paused)
