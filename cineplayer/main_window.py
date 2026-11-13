"""
CinePlayer — het kioskvenster.

Eén fullscreen venster zonder rand met twee bladen:
  0  rustscherm (zwart, één regel tekst)       — geen video gekozen / video niet gevonden
  1  video (mpv in een native child-venster)   — herhaalt oneindig, met optioneel de tekstlaag

Het venster past opdrachten van het paneel toe (`apply_command`), houdt de
videomap bij en levert elke halve seconde een statusmomentopname aan de poller.
Er is bewust geen lokale bediening, op twee dingen na: Ctrl+Q sluit af, en
zodra de muis beweegt verschijnt rechtsboven drie seconden een kruisje waarmee
je de speler sluit (mpv tekent in een native venster, daarom is het kruisje een
zwevend randloos venstertje; muisbeweging wordt gepeild via de cursorpositie).
"""

from __future__ import annotations

import logging
import socket

from PyQt6.QtCore import QPoint, QRect, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QCursor, QGuiApplication, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QMainWindow, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from . import __version__
from .config import Config, State, parse_overlay
from .controller import PlayerController
from .library import resolve_video, scan_videos
from .remote import Poller
from .tekstlaag import Tekstlaag

_log = logging.getLogger("cineplayer.window")

PAGE_IDLE, PAGE_VIDEO = 0, 1
STATUS_MS = 500
SAVE_POS_MS = 15_000        # voortgang per film naar state.json (hoeft niet nauwkeurig)
MOUSE_POLL_MS = 100         # cursorpositie peilen
CLOSE_HIDE_MS = 3000        # kruisje verdwijnt na zoveel ms zonder muisbeweging
CLOSE_SIZE = 48
CLOSE_MARGIN = 16


class CloseButton(QWidget):
    """Rond kruisje rechtsboven, als zelfstandig randloos tool-venster boven de video."""

    clicked = pyqtSignal()

    def __init__(self, owner: QWidget) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self._owner = owner
        self._hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Speler afsluiten")
        self.setMouseTracking(True)
        self.hide()

    def place(self) -> None:
        geo = QRect(self._owner.mapToGlobal(QPoint(0, 0)), self._owner.size())
        self.move(geo.right() - CLOSE_SIZE - CLOSE_MARGIN + 1, geo.top() + CLOSE_MARGIN)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 120 if self._hover else 70), 1.5))
        p.setBrush(QColor(229, 83, 75, 230) if self._hover else QColor(0, 0, 0, 165))
        p.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
        p.setPen(QPen(QColor(255, 255, 255), 2.5, cap=Qt.PenCapStyle.RoundCap))
        c = self.rect().center()
        r = CLOSE_SIZE // 5
        p.drawLine(c.x() - r, c.y() - r, c.x() + r, c.y() + r)
        p.drawLine(c.x() - r, c.y() + r, c.x() + r, c.y() - r)
        p.end()


class VideoWidget(QWidget):
    """Native venster waar mpv in tekent."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, False)
        self.setStyleSheet("background:#000;")
        self.setAutoFillBackground(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


class KioskWindow(QMainWindow):
    def __init__(self, cfg: Config, *, kiosk: bool = True) -> None:
        super().__init__()
        self.cfg = cfg
        self.kiosk = kiosk
        self.state = State.load()
        self.videos: list[str] = []
        self._online = False
        self._last_error = ""
        self._current_video_abs = ""
        self._pos = 0.0
        self._dur = 0.0
        self._paused = False
        self._ready = False             # na _after_show: dan mag rescan() zelf de video (her)starten
        self._hostname = socket.gethostname()

        self.setWindowTitle("LAB-1 Lobby")
        self.setStyleSheet("QMainWindow, QWidget { background:#000; color:#9aa0a8; }")
        if kiosk:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.resize(1280, 720)

        self._build_ui()

        # Sluitkruisje: zichtbaar zolang de muis beweegt, daarna weg (en de aanwijzer ook).
        self.close_btn = CloseButton(self)
        self.close_btn.clicked.connect(self.close)
        self._cursor_hidden = False
        self._last_mouse = QCursor.pos()
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(CLOSE_HIDE_MS)
        self._close_timer.timeout.connect(self._hide_close)
        self._mouse_timer = QTimer(self)
        self._mouse_timer.setInterval(MOUSE_POLL_MS)
        self._mouse_timer.timeout.connect(self._poll_mouse)

        self.ctrl = PlayerController(self)
        self.ctrl.position_changed.connect(self._on_position)
        self.ctrl.error.connect(self._on_player_error)
        # Tekstlaag: het filmprogramma over de video (aan/uit vanaf het paneel)
        self.tekstlaag = Tekstlaag(self.ctrl, cfg.programma_url, cfg.overlay_uitlijning, self)

        quit_action = QAction("Afsluiten", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

        self.poller: Poller | None = None
        if cfg.remote_enabled:
            self.poller = Poller(cfg.server_url, cfg.api_key, cfg.poll_seconds, cfg.request_timeout, self)
            self.poller.command_received.connect(self.apply_command)
            self.poller.online_changed.connect(self._on_online)
            self.poller.failed.connect(self._on_remote_failed)
        else:
            _log.warning("Geen server_url/api_key in settings.json: speler draait zonder paneel")

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_MS)
        self._status_timer.timeout.connect(self._push_status)
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setInterval(int(cfg.rescan_seconds * 1000))
        self._rescan_timer.timeout.connect(self.rescan)
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(SAVE_POS_MS)
        self._save_timer.timeout.connect(self._save_positions)

    # ── opbouw ──────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        idle = QWidget()
        lay = QVBoxLayout(idle)
        lay.setContentsMargins(40, 40, 40, 40)
        self.lbl_idle = QLabel("")
        self.lbl_idle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_idle.setWordWrap(True)
        self.lbl_idle.setStyleSheet("color:#6b7280; font-size:18px; font-family:'Segoe UI', sans-serif;")
        lay.addWidget(self.lbl_idle)
        self.stack.addWidget(idle)

        self.video = VideoWidget()
        self.stack.addWidget(self.video)
        self.stack.setCurrentIndex(PAGE_IDLE)

    # ── levensloop ──────────────────────────────────────────────────────
    def start(self) -> None:
        if self.kiosk:
            self.showFullScreen()
        else:
            self.show()
        QTimer.singleShot(0, self._after_show)

    def _after_show(self) -> None:
        self.ctrl.attach(int(self.video.winId()))
        self._last_mouse = QCursor.pos()
        self._mouse_timer.start()
        self._hide_close()
        self.rescan()
        self.tekstlaag.configure(self.state.overlay)
        # Verdergaan waar we gebleven waren; de eerste poll van het paneel kan dit overschrijven.
        self._ready = True
        self._apply_state(restart=True)
        self._status_timer.start()
        self._rescan_timer.start()
        self._save_timer.start()
        if self.poller is not None:
            self._push_status()
            self.poller.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._status_timer.stop()
        self._rescan_timer.stop()
        self._save_timer.stop()
        self._close_timer.stop()
        self._mouse_timer.stop()
        self._set_cursor_hidden(False)
        self.close_btn.hide()
        if self.poller is not None:
            self.poller.stop()
        self.tekstlaag.stop()
        self.ctrl.terminate()
        self.state.save()
        self.close_btn.close()
        self.close_btn.deleteLater()
        _log.info("afgesloten")
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.stack.currentIndex() == PAGE_VIDEO:
            QTimer.singleShot(0, self._tekstlaag_maat)

    def _tekstlaag_maat(self) -> None:
        """Tekstlaag even groot als het videovenster, in fysieke pixels (mpv tekent in schermpixels)."""
        dpr = self.video.devicePixelRatioF()
        actief = self.stack.currentIndex() == PAGE_VIDEO and bool(self._current_video_abs)
        self.tekstlaag.set_actief(actief, round(self.video.width() * dpr), round(self.video.height() * dpr))

    # ── sluitkruisje / muis ─────────────────────────────────────────────
    def _poll_mouse(self) -> None:
        pos = QCursor.pos()
        if pos == self._last_mouse:
            return
        self._last_mouse = pos
        self._show_close()

    def _show_close(self) -> None:
        self._set_cursor_hidden(False)
        self.close_btn.place()
        if not self.close_btn.isVisible():
            self.close_btn.show()
        self.close_btn.raise_()
        self._close_timer.start()

    def _hide_close(self) -> None:
        self.close_btn.hide()
        if self.kiosk:
            self._set_cursor_hidden(True)

    def _set_cursor_hidden(self, hidden: bool) -> None:
        if hidden == self._cursor_hidden:
            return
        self._cursor_hidden = hidden
        if hidden:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)
        else:
            while QGuiApplication.overrideCursor() is not None:
                QGuiApplication.restoreOverrideCursor()

    # ── videomap ────────────────────────────────────────────────────────
    def rescan(self) -> None:
        videos = scan_videos(self.cfg.videos_dir)
        if videos != self.videos:
            self.videos = videos
            _log.info("videomap: %d video's", len(videos))
            if self._ready and self.state.video and not self._current_video_abs:
                self._apply_state(restart=True)     # de gekozen video is er nu misschien wel

    # ── opdrachten ──────────────────────────────────────────────────────
    def apply_command(self, cmd: dict) -> None:
        """Opdracht van het paneel toepassen. `seq` verandert bij elke klik op het paneel;
        dezelfde video opnieuw kiezen start hem dan opnieuw. Bij de eerste poll na een
        herstart (seq nog onbekend) wordt alleen toegepast wat echt anders is."""
        try:
            seq = int(cmd.get("seq", 0))
        except (TypeError, ValueError):
            seq = 0
        video = cmd.get("video")
        video = str(video) if isinstance(video, str) and video else None
        try:
            rescan = int(cmd.get("rescan", 0))
        except (TypeError, ValueError):
            rescan = 0

        first_contact = self.state.seq < 0
        changed_seq = (not first_contact) and seq != self.state.seq
        bijzaak = False                     # een klik die niet over de video zelf ging (geen herstart)
        if rescan != self.state.rescan:
            self.state.rescan = rescan
            bijzaak = True
            self.rescan()

        # Springen (+30 s e.d.): elke klik op het paneel verhoogt seek.n; na een herstart niet herhalen
        sk = cmd.get("seek") if isinstance(cmd.get("seek"), dict) else {}
        try:
            seek_n, seek_delta = int(sk.get("n", 0)), float(sk.get("delta", 0))
        except (TypeError, ValueError):
            seek_n, seek_delta = 0, 0.0
        if self.state.seek_n < 0:
            self.state.seek_n = seek_n
        elif seek_n != self.state.seek_n:
            self.state.seek_n = seek_n
            bijzaak = True
            if seek_delta and self.ctrl.seek(seek_delta):
                _log.info("spring %+.0f s", seek_delta)

        overlay = parse_overlay(cmd.get("overlay"), self.tekstlaag.basis_uitlijning)
        if overlay != self.state.overlay:
            self.state.overlay = overlay
            bijzaak = True
            self.tekstlaag.configure(overlay)

        differs = video != self.state.video
        self.state.video, self.state.seq = video, seq
        # Een nieuwe seq betekent een klik op het paneel: dezelfde video opnieuw laden (hervat waar
        # hij was), behalve als die klik over de tekstlaag, springen of de videomap ging.
        if differs or (changed_seq and not bijzaak):
            _log.info("opdracht #%d: video %s", seq, video)
            self._apply_state(restart=True)
        self.state.save()

    def _apply_state(self, *, restart: bool) -> None:
        st = self.state
        self._last_error = ""
        self.tekstlaag.set_actief(False)
        if not st.video:
            self.ctrl.stop()
            self._current_video_abs = ""
            self._show_idle("Nog geen video gekozen.")
            return
        full = resolve_video(self.cfg.videos_dir, st.video)
        if full is None:
            self.ctrl.stop()
            self._current_video_abs = ""
            self._last_error = f"Video niet gevonden: {st.video}"
            self._show_idle(f"Video niet gevonden in de videomap:\n{st.video}")
            _log.warning(self._last_error)
            return
        if restart or str(full) != self._current_video_abs:
            self._save_positions()                       # positie van de vorige film bewaren
            start = self.state.positions.get(st.video, 0.0)
            if self.ctrl.play(str(full), start=start):
                if start > 1:
                    _log.info("hervat %s op %d:%02d", st.video, int(start) // 60, int(start) % 60)
                self._current_video_abs = str(full)
            else:
                self._current_video_abs = ""
                self._last_error = f"Afspelen mislukt: {st.video}"
                self._show_idle(f"Afspelen mislukt:\n{st.video}")
                return
        self.stack.setCurrentIndex(PAGE_VIDEO)
        self._tekstlaag_maat()

    def _show_idle(self, text: str) -> None:
        self.lbl_idle.setText(text)
        self.stack.setCurrentIndex(PAGE_IDLE)

    # ── status richting paneel ──────────────────────────────────────────
    def _on_position(self, pos: float, dur: float, paused: bool) -> None:
        self._pos, self._dur, self._paused = pos, dur, paused
        if self.state.video and self._current_video_abs:
            self.state.positions[self.state.video] = round(pos, 1)

    def _save_positions(self) -> None:
        """Voortgang per film naar state.json; video's die niet meer bestaan vallen eruit."""
        if self.videos:
            for k in list(self.state.positions):
                if k not in self.videos:
                    del self.state.positions[k]
        self.state.save()

    def _on_player_error(self, msg: str) -> None:
        self._last_error = msg

    def _on_online(self, online: bool) -> None:
        self._online = online

    def _on_remote_failed(self, msg: str) -> None:
        if self.stack.currentIndex() == PAGE_IDLE and not self._last_error:
            self.lbl_idle.setText(f"Nog geen video gekozen.\nPaneel niet bereikbaar: {msg}")

    def status_snapshot(self) -> dict:
        playing = self.stack.currentIndex() == PAGE_VIDEO and bool(self._current_video_abs)
        return {
            "id": self.cfg.player_id,
            "video": self.state.video if playing else None,
            "overlay": self.tekstlaag.status(),
            "position": round(self._pos, 1) if playing else 0,
            "duration": round(self._dur, 1) if playing else 0,
            "paused": self._paused if playing else False,
            "videos": list(self.videos),
            "videos_dir": str(self.cfg.videos_dir),
            "hostname": self._hostname,
            "version": __version__,
            "seq_applied": self.state.seq,
            "error": self._last_error,
        }

    def _push_status(self) -> None:
        if self.poller is not None:
            self.poller.update_status(self.status_snapshot())
