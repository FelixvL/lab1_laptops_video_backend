"""
CinePlayer — de tekstlaag (filmprogramma over de video) bijhouden.

Houdt bij of de laag aan staat (opdracht van het paneel), haalt het programma op
in een eigen thread (elke minuut, zoals de site), tekent de laag opnieuw als de
minuut verspringt (de highlight hangt van de klok af), de uitlijning verandert of
het beeldformaat verandert, en zet hem via de PlayerController in mpv. Buiten
videomodus wordt de laag weggehaald.

Voor het uitlijnen op het paneel stuurt de status na elke hertekening een
miniatuur mee (JPEG, base64) plus de vakken van geheel/titel/lijst, zodat je op
het paneel ziet wat er op het scherm staat en erop kunt tikken.
"""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter

from .config import parse_overlay, uitlijning_van
from .overlay import render
from .programma import Dag, haal_programma, stel_lijst_samen

_log = logging.getLogger("cineplayer.tekstlaag")

FEED_MS = 60_000      # programma opnieuw ophalen
TICK_MS = 5_000       # kijken of de minuut verspringt
PREVIEW_W = 480       # miniatuur voor het paneel
PREVIEW_FRESH_S = 8   # zo lang na een hertekening gaat de miniatuur mee in de status (± 2 polls)


class _FeedWorker(QThread):
    klaar = pyqtSignal(list)

    def __init__(self, url: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.url = url

    def run(self) -> None:
        self.klaar.emit(haal_programma(self.url))


class Tekstlaag(QObject):
    def __init__(self, ctrl, programma_url: str, uitlijning=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctrl = ctrl
        self.url = programma_url
        self.basis_uitlijning = uitlijning_van(uitlijning)      # standaard uit settings.json (of de site)
        self.instellingen: dict = parse_overlay(None, self.basis_uitlijning)
        self.dagen: list[Dag] | None = None     # None = nog nooit opgehaald
        self.opgehaald = 0.0
        self.titel = ""
        self.aantal = 0
        self.rects: dict = {}
        self._preview_b64 = ""
        self._preview_at = 0.0
        self._actief = False
        self._w = self._h = 0
        self._minuut = ""
        self._getoond = False
        self._worker: _FeedWorker | None = None
        self._feed_timer = QTimer(self)
        self._feed_timer.setInterval(FEED_MS)
        self._feed_timer.timeout.connect(self.haal_op)
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._op_tick)

    # ── aansturing ──────────────────────────────────────────────────────
    @property
    def aan(self) -> bool:
        return bool(self.instellingen.get("aan"))

    def configure(self, instellingen: dict) -> None:
        instellingen = parse_overlay(instellingen, self.basis_uitlijning)
        if instellingen == self.instellingen:
            return
        self.instellingen = instellingen
        _log.info("tekstlaag: %s, modus %s, geschiedenis %d min, uitlijning %s", "aan" if self.aan else "uit",
                  instellingen["modus"], instellingen["geschiedenis"], instellingen["uitlijning"])
        if self.aan:
            if not self._feed_timer.isActive():
                self._feed_timer.start()
                self._tick.start()
            if self.dagen is None or time.time() - self.opgehaald > 60:
                self.haal_op()
        else:
            self._feed_timer.stop()
            self._tick.stop()
        self.refresh()

    def set_actief(self, actief: bool, w: int = 0, h: int = 0) -> None:
        """Videomodus met beeld op het scherm ja/nee, en de maat van het videovenster in pixels."""
        veranderd = actief != self._actief or (w and (w, h) != (self._w, self._h))
        self._actief = actief
        if w:
            self._w, self._h = int(w), int(h)
        if veranderd:
            self.refresh()

    def stop(self) -> None:
        self._feed_timer.stop()
        self._tick.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)

    # ── programma ───────────────────────────────────────────────────────
    def haal_op(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _FeedWorker(self.url, self)
        self._worker.klaar.connect(self._op_feed)
        self._worker.start()

    def _op_feed(self, dagen: list) -> None:
        self.opgehaald = time.time()
        if dagen or self.dagen is None:
            self.dagen = list(dagen)
        if dagen:
            _log.info("programma: %d dagen, vandaag %d films", len(dagen), len(dagen[0].films))
        self.refresh()

    def _op_tick(self) -> None:
        if datetime.now().strftime("%H:%M") != self._minuut:
            self.refresh()

    # ── tekenen ─────────────────────────────────────────────────────────
    def refresh(self) -> None:
        if not (self.aan and self._actief and self._w > 0):
            if self._getoond:
                self.ctrl.set_overlay(None)
                self._getoond = False
            return
        self._minuut = datetime.now().strftime("%H:%M")
        lijst = None
        if self.dagen is not None:
            lijst = stel_lijst_samen(self.dagen, modus=self.instellingen["modus"], nu=self._minuut,
                                     geschiedenis=int(self.instellingen["geschiedenis"]))
            self.titel, self.aantal = lijst.titel, len(lijst.films)
        t = time.perf_counter()
        uit = render(lijst, self._w, self._h, uitlijning=self.instellingen["uitlijning"])
        self.ctrl.set_overlay(uit.image)
        self._getoond = True
        self.rects = uit.rects
        self._maak_preview(uit.image)
        _log.debug("tekstlaag getekend %dx%d in %.0f ms", self._w, self._h, (time.perf_counter() - t) * 1000)

    def _maak_preview(self, image: QImage) -> None:
        """Miniatuur voor het paneel: de laag op een grijze achtergrond, als JPEG in base64."""
        try:
            h = max(1, round(PREVIEW_W * image.height() / max(1, image.width())))
            klein = image.scaled(PREVIEW_W, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            mini = QImage(klein.size(), QImage.Format.Format_RGB32)
            mini.fill(QColor(96, 104, 112))
            p = QPainter(mini)
            p.drawImage(0, 0, klein)
            p.end()
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            mini.save(buf, "JPEG", 80)
            data: QByteArray = buf.data()
            self._preview_b64 = base64.b64encode(bytes(data)).decode("ascii")
            self._preview_at = time.time()
        except Exception as e:  # noqa: BLE001 — een miniatuur mag nooit de speler hinderen
            _log.warning("miniatuur mislukt: %s", e)

    # ── status richting paneel ──────────────────────────────────────────
    def status(self) -> dict:
        if self.dagen is None:
            programma = "laden" if self.aan else ""
        else:
            programma = "ok" if self.dagen else "fout"
        st = {**self.instellingen, "programma": programma, "films": self.aantal if self.dagen else 0,
              "titel": self.titel if self.dagen else "", "opgehaald": int(self.opgehaald),
              "zichtbaar": self._getoond, "rects": self.rects if self._getoond else {},
              "preview_at": int(self._preview_at)}
        if self._getoond and self._preview_b64 and time.time() - self._preview_at < PREVIEW_FRESH_S:
            st["preview"] = self._preview_b64
        return st
