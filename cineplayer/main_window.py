"""
CinePlayer — het kioskvenster.

Eén fullscreen venster zonder rand met twee bladen:
  0  rustscherm (zwart, één regel tekst)   — geen video gekozen / video niet gevonden / onder de browser
  1  video (mpv in een native child-venster) — modus 'video', herhaalt oneindig

Website-modus gebruikt géén ingebouwde browser: de speler start Microsoft Edge
(of Chrome) in kioskstand vóór dit venster en sluit hem weer bij terugschakelen
naar video (zie browser.py). Zo ziet de site er precies zo uit als in een gewone
browser. Zolang Edge voorligt is dit venster niet meer "altijd bovenop"; dat
regelen we met SetWindowPos, zonder het venster opnieuw op te bouwen.

Het venster past opdrachten van het paneel toe (`apply_command`), houdt de
videomap bij en levert elke halve seconde een statusmomentopname aan de poller.
Er is bewust geen lokale bediening, op twee dingen na: Ctrl+Q sluit af, en
zodra de muis beweegt verschijnt rechtsboven drie seconden een kruisje waarmee
je de speler (en Edge) sluit. Muisbeweging wordt gepeild via de cursorpositie,
zodat het ook werkt terwijl Edge de muis heeft.
"""

from __future__ import annotations

import ctypes
import logging
import socket
import sys

from PyQt6.QtCore import QPoint, QRect, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QCursor, QGuiApplication, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QMainWindow, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from . import __version__
from .browser import BrowserKiosk, find_browser
from .config import APP_ROOT, Config, State
from .controller import PlayerController
from .library import resolve_video, scan_videos
from .remote import Poller

_log = logging.getLogger("cineplayer.window")

PAGE_IDLE, PAGE_VIDEO = 0, 1
STATUS_MS = 500
MOUSE_POLL_MS = 100         # cursorpositie peilen (werkt ook als Edge de muis heeft)
CLOSE_HIDE_MS = 3000        # kruisje verdwijnt na zoveel ms zonder muisbeweging
CLOSE_SIZE = 48
CLOSE_MARGIN = 16
BROWSER_PROFILE_DIR = APP_ROOT / "browser_profile"


def _set_topmost(wid: int, on: bool) -> None:
    """Venster wel/niet 'altijd bovenop' zonder het opnieuw aan te maken (alleen Windows)."""
    if sys.platform != "win32" or not wid:
        return
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
    ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(wid), ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST),
                                      0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)


class CloseButton(QWidget):
    """Rond kruisje rechtsboven, als randloos tool-venster boven video én browser."""

    clicked = pyqtSignal()

    def __init__(self, owner: QWidget) -> None:
        # Bewust zónder Qt-ouder: een "owned" venster verliest zijn altijd-bovenop-status
        # zodra het hoofdvenster die verliest (website-modus, Edge ervoor). Zelfstandig blijft
        # het kruisje ook boven Edge staan.
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
        self._hostname = socket.gethostname()

        self.setWindowTitle("LAB-1 Lobby")
        self.setStyleSheet("QMainWindow, QWidget { background:#000; color:#9aa0a8; }")
        if kiosk:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.resize(1280, 720)

        self._build_ui()

        # Browser voor website-modus (Edge, anders Chrome).
        exe = find_browser(cfg.browser_path)
        if exe:
            _log.info("browser voor website-modus: %s", exe)
        else:
            _log.error("geen browser gevonden (Edge/Chrome); website-modus werkt niet")
        self.browser = BrowserKiosk(exe, BROWSER_PROFILE_DIR, kiosk=kiosk, parent=self)
        self.browser.failed.connect(self._on_browser_failed)
        self.browser.running_changed.connect(self._on_browser_running)

        # Sluitkruisje: zichtbaar zolang de muis beweegt, daarna weg (en de aanwijzer ook).
        self.close_btn = CloseButton(self)
        self.close_btn.clicked.connect(self.close)
        self._cursor_hidden = False
        self._last_mouse = QCursor.pos()
        self._parked_mouse: QPoint | None = None
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
        self.browser.cleanup_stale()
        self._last_mouse = QCursor.pos()
        self._mouse_timer.start()
        self._hide_close()
        self.rescan()
        # Verdergaan waar we gebleven waren; de eerste poll van het paneel kan dit overschrijven.
        self._apply_state(restart=True)
        self._status_timer.start()
        self._rescan_timer.start()
        if self.poller is not None:
            self._push_status()
            self.poller.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._status_timer.stop()
        self._rescan_timer.stop()
        self._close_timer.stop()
        self._mouse_timer.stop()
        self._set_cursor_hidden(False)
        self.close_btn.hide()
        if self.poller is not None:
            self.poller.stop()
        self.browser.stop()
        self.ctrl.terminate()
        self.state.save()
        self.close_btn.close()
        self.close_btn.deleteLater()
        _log.info("afgesloten")
        super().closeEvent(event)

    # ── sluitkruisje / muis ─────────────────────────────────────────────
    def _poll_mouse(self) -> None:
        pos = QCursor.pos()
        if pos == self._last_mouse:
            return
        self._last_mouse = pos
        if self._parked_mouse is not None and pos == self._parked_mouse:
            return                      # dat waren wij zelf (cursor geparkeerd)
        self._parked_mouse = None
        self._show_close()

    def _show_close(self) -> None:
        self._set_cursor_hidden(False)
        self.close_btn.place()
        if not self.close_btn.isVisible():
            self.close_btn.show()
        self.close_btn.raise_()
        _set_topmost(int(self.close_btn.winId()), True)     # ook boven Edge
        self._close_timer.start()

    def _hide_close(self) -> None:
        self.close_btn.hide()
        if not self.kiosk:
            return
        if self.browser.running:
            # Edge tekent zijn eigen aanwijzer; die kunnen we niet verbergen, wel uit beeld parkeren.
            scr = self.screen() or QGuiApplication.primaryScreen()
            if scr is not None:
                corner = scr.geometry().bottomRight()
                QCursor.setPos(corner)
                self._parked_mouse = QCursor.pos()
                self._last_mouse = self._parked_mouse
        else:
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
            if self.state.mode == "video" and self.state.video and not self._current_video_abs:
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
        mode = "website" if cmd.get("mode") == "website" else "video"
        video = cmd.get("video")
        video = str(video) if isinstance(video, str) and video else None
        url = str(cmd.get("url") or self.cfg.website_url)
        try:
            rescan = int(cmd.get("rescan", 0))
        except (TypeError, ValueError):
            rescan = 0

        first_contact = self.state.seq < 0
        changed_seq = (not first_contact) and seq != self.state.seq
        if rescan != self.state.rescan:
            self.state.rescan = rescan
            self.rescan()

        differs = mode != self.state.mode or video != self.state.video or url != self.state.url
        self.state.mode, self.state.video, self.state.url, self.state.seq = mode, video, url, seq
        if differs or changed_seq:
            _log.info("opdracht #%d: %s %s", seq, mode, video if mode == "video" else url)
            self._apply_state(restart=True)
        self.state.save()

    def _apply_state(self, *, restart: bool) -> None:
        st = self.state
        self._last_error = ""
        if st.mode == "website":
            self.ctrl.stop()
            self._current_video_abs = ""
            self._pos = self._dur = 0.0
            self._show_idle("")
            if self.kiosk:
                _set_topmost(int(self.winId()), False)
            if restart or not self.browser.running or self.browser.want_url != st.url:
                self.browser.start(st.url)
            return

        # videomodus
        self.browser.stop()
        self._foreground()
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
            if self.ctrl.play(str(full)):
                self._current_video_abs = str(full)
            else:
                self._current_video_abs = ""
                self._last_error = f"Afspelen mislukt: {st.video}"
                self._show_idle(f"Afspelen mislukt:\n{st.video}")
                return
        self.stack.setCurrentIndex(PAGE_VIDEO)

    def _foreground(self) -> None:
        """Na het sluiten van de browser: dit venster weer bovenop en actief."""
        if self.kiosk:
            _set_topmost(int(self.winId()), True)
        self.raise_()
        self.activateWindow()
        if self.kiosk and not self.close_btn.isVisible():
            self._set_cursor_hidden(True)

    def _show_idle(self, text: str) -> None:
        self.lbl_idle.setText(text)
        self.stack.setCurrentIndex(PAGE_IDLE)

    # ── status richting paneel ──────────────────────────────────────────
    def _on_position(self, pos: float, dur: float, paused: bool) -> None:
        self._pos, self._dur, self._paused = pos, dur, paused

    def _on_player_error(self, msg: str) -> None:
        self._last_error = msg

    def _on_browser_failed(self, msg: str) -> None:
        self._last_error = msg
        _log.error(msg)
        if self.state.mode == "website":
            self._foreground()
            self._show_idle(f"Website niet te tonen:\n{msg}")

    def _on_browser_running(self, running: bool) -> None:
        if running and self.state.mode == "website" and self.kiosk:
            _set_topmost(int(self.winId()), False)
            # na 3 s zonder muisbeweging verdwijnt het kruisje weer; de cursor parkeren we dan ook
            if not self.close_btn.isVisible():
                self._close_timer.start()

    def _on_online(self, online: bool) -> None:
        self._online = online

    def _on_remote_failed(self, msg: str) -> None:
        if self.stack.currentIndex() == PAGE_IDLE and self.state.mode != "website" and not self._last_error:
            self.lbl_idle.setText(f"Nog geen video gekozen.\nPaneel niet bereikbaar: {msg}")

    def status_snapshot(self) -> dict:
        playing = self.stack.currentIndex() == PAGE_VIDEO and bool(self._current_video_abs)
        website = self.state.mode == "website"
        return {
            "mode": self.state.mode,
            "video": self.state.video if playing else None,
            "url": self.state.url if website else None,
            "browser": self.browser.name if (website and self.browser.running) else "",
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
