"""
CinePlayer — het kioskvenster.

Eén fullscreen venster zonder rand met twee bladen:
  0  rustscherm (zwart, één regel tekst)       — geen video gekozen / video niet gevonden
  1  video (mpv in een native child-venster)   — één film in herhaling of een speellijst,
                                                 met optioneel de tekstlaag

Het venster past opdrachten van het paneel toe (`apply_command`), houdt de
videomap en de speellijsten bij en levert elke halve seconde een statusmomentopname
aan de poller.

Lokale bediening is er bewust bijna niet. Zodra de muis beweegt verschijnt
rechtsboven drie seconden een kruisje (mpv tekent in een native venster, daarom is
het kruisje een zwevend randloos venstertje; muisbeweging wordt gepeild via de
cursorpositie). Het kruisje sluit de speler niet helemaal af: het fullscreen
venster verdwijnt, het afspelen stopt en er blijft een klein instellingenvenster
over (settings_window.py) waarin je de videomap en de paneelgegevens kunt
wijzigen, speellijsten kunt importeren en de speler weer fullscreen kunt starten.
Ctrl+Q, of het instellingenvenster sluiten, sluit het programma echt af.
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QCursor, QGuiApplication, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from . import __version__
from .config import Config, State, clean_player_id, parse_overlay, update_settings
from .controller import PlayerController
from .library import resolve_video, scan_videos
from .playlists import Playlist, basename_index, delete_playlist, import_playlist, load_playlists
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
LIST_PREFIX = "speellijst:"  # merkteken in _current_video_abs als er een speellijst speelt


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
        self.setToolTip("Speler sluiten (instellingen blijven open)")
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
        self.playlists: list[Playlist] = load_playlists()
        self.playlist_info: list[dict] = []     # per speellijst {name, count, missing}, voor paneel en venster
        self._list_files: list[Path] = []       # de films van de speellijst die nu speelt
        self._list_index = -1
        self._online = False
        self._last_error = ""
        self._current_video_abs = ""            # pad van de film die speelt, of LIST_PREFIX + naam
        self._pos = 0.0
        self._dur = 0.0
        self._paused = False
        self._ready = False             # na _after_show: dan mag rescan() zelf de video (her)starten
        self._settings_open = False     # speler gesloten met het kruisje: instellingenvenster open
        self._quitting = False
        self.settings_win = None
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
        self.close_btn.clicked.connect(self.open_settings)
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
        quit_action.triggered.connect(self.quit_app)
        self.addAction(quit_action)

        self.poller: Poller | None = None
        self._start_poller()

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

    def _start_poller(self) -> None:
        """(Opnieuw) verbinden met het paneel volgens de huidige instellingen."""
        if self.poller is not None:
            self.poller.stop()
            self.poller.deleteLater()
            self.poller = None
        self._online = False
        cfg = self.cfg
        if not cfg.remote_enabled:
            _log.warning("Geen server_url/api_key in settings.json: speler draait zonder paneel")
            return
        self.poller = Poller(cfg.server_url, cfg.api_key, cfg.poll_seconds, cfg.request_timeout, self)
        self.poller.command_received.connect(self.apply_command)
        self.poller.online_changed.connect(self._on_online)
        self.poller.failed.connect(self._on_remote_failed)
        if self._ready:
            self._push_status()
            self.poller.start()

    # ── levensloop ──────────────────────────────────────────────────────
    def start(self) -> None:
        self._show_player()
        QTimer.singleShot(0, self._after_show)

    def _show_player(self) -> None:
        if self.kiosk:
            self.showFullScreen()
        else:
            self.show()

    def _after_show(self) -> None:
        self.ctrl.attach(int(self.video.winId()))
        self._last_mouse = QCursor.pos()
        self._mouse_timer.start()
        self._hide_close()
        self.rescan()
        self._refresh_playlists()
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
        """Alt+F4 op het spelervenster doet hetzelfde als het kruisje; echt afsluiten gaat via quit_app()."""
        if self._quitting:
            super().closeEvent(event)
            return
        event.ignore()
        self.open_settings()

    def quit_app(self) -> None:
        """Het programma echt afsluiten (Ctrl+Q, of het instellingenvenster sluiten)."""
        if self._quitting:
            return
        self._quitting = True
        self._status_timer.stop()
        self._rescan_timer.stop()
        self._save_timer.stop()
        self._close_timer.stop()
        self._mouse_timer.stop()
        self._set_cursor_hidden(False)
        self.close_btn.hide()
        self._save_positions()
        if self.poller is not None:
            self.poller.stop()
        self.tekstlaag.stop()
        self.ctrl.terminate()
        self.state.save()
        self.close_btn.close()
        self.close_btn.deleteLater()
        if self.settings_win is not None:
            self.settings_win.close()
        _log.info("afgesloten")
        self.close()
        QApplication.instance().quit()

    # ── instellingenvenster (na het kruisje) ────────────────────────────
    def open_settings(self) -> None:
        """Speler sluiten: afspelen stoppen, fullscreen venster weg, klein instellingenvenster tonen."""
        if self._settings_open or self._quitting:
            return
        from .settings_window import SettingsWindow
        self._settings_open = True
        self._save_positions()
        self.tekstlaag.set_actief(False)
        self.ctrl.stop()
        self._current_video_abs = ""
        self._close_timer.stop()
        self._mouse_timer.stop()
        self.close_btn.hide()
        self._set_cursor_hidden(False)
        if self.settings_win is None:
            self.settings_win = SettingsWindow(self)
        self.settings_win.refresh()
        self.settings_win.show()
        self.settings_win.raise_()
        self.settings_win.activateWindow()
        self.hide()
        _log.info("speler gesloten, instellingenvenster open")

    def resume_player(self) -> None:
        """Vanuit het instellingenvenster: de speler weer (fullscreen) starten waar hij was."""
        if not self._settings_open or self._quitting:
            return
        self._settings_open = False
        self._show_player()
        self.raise_()
        self.activateWindow()
        if self.settings_win is not None:
            self.settings_win.hide()
        self._last_mouse = QCursor.pos()
        self._mouse_timer.start()
        self._hide_close()
        self._ready = False              # rescan() mag nu niet zelf starten; dat doen we hieronder één keer
        self.videos = []                 # dwing een verse blik op de videomap af
        self.rescan()
        self._refresh_playlists()
        self._ready = True
        _log.info("speler weer gestart")
        self._apply_state(restart=True)

    def set_videos_dir(self, path: str | Path) -> None:
        """Andere videomap kiezen (instellingenvenster): bewaren in settings.json en opnieuw inlezen."""
        path = Path(path)
        self.cfg.videos_dir = path
        update_settings(self.cfg.settings_path, videos_dir=str(path))
        _log.info("videomap gewijzigd: %s", path)
        self.videos = []
        self.rescan()
        self._refresh_playlists()

    def set_panel(self, name: str, server_url: str, api_key: str) -> None:
        """Naam op het paneel, adres van api.php en API-sleutel wijzigen en opnieuw verbinden."""
        self.cfg.player_id = clean_player_id(name.strip() or self._hostname)
        self.cfg.server_url = server_url.strip()
        self.cfg.api_key = api_key.strip()
        update_settings(self.cfg.settings_path, player_id=self.cfg.player_id,
                        server_url=self.cfg.server_url, api_key=self.cfg.api_key)
        # Voor het paneel is dit (mogelijk) een nieuwe laptop: de eerste opdracht niet als klik behandelen
        self.state.seq = self.state.seek_n = self.state.skip_n = -1
        _log.info("paneel gewijzigd: naam %s, adres %s", self.cfg.player_id, self.cfg.server_url or "(geen)")
        self._start_poller()

    def import_playlists(self, files: list[tuple[str, str | None]]) -> list[str]:
        """Speellijstbestanden importeren: [(pad, naam of None)]. Geeft per bestand een regel terug."""
        out: list[str] = []
        index = basename_index(self.cfg.videos_dir)
        for src, name in files:
            try:
                pl = import_playlist(Path(src), name=name)
            except (OSError, ValueError) as e:
                out.append(f"{Path(src).name}: mislukt ({e})")
                _log.warning("import %s mislukt: %s", src, e)
                continue
            s = pl.summary(self.cfg.videos_dir, index)
            out.append(f"{pl.name}: {s['count']} films" + (f", {s['missing']} niet gevonden" if s["missing"] else ""))
        self._refresh_playlists()
        return out

    def remove_playlist(self, name: str) -> None:
        if delete_playlist(name):
            _log.info("speellijst verwijderd: %s", name)
        self.state.playlist_pos.pop(name, None)
        self._refresh_playlists()

    def _refresh_playlists(self) -> None:
        """Speellijsten opnieuw van schijf lezen en per lijst tellen hoeveel films vindbaar zijn."""
        self.playlists = load_playlists()
        index = basename_index(self.cfg.videos_dir) if self.playlists else {}
        self.playlist_info = [pl.summary(self.cfg.videos_dir, index) for pl in self.playlists]

    def _playlist(self, name: str | None) -> Playlist | None:
        return next((pl for pl in self.playlists if pl.name == name), None)

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
        if self.kiosk and not self._settings_open:
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
            if self.playlists:
                self._refresh_playlists()
            if self._ready and (self.state.video or self.state.playlist) and not self._current_video_abs:
                self._apply_state(restart=True)     # de gekozen video is er nu misschien wel

    # ── opdrachten ──────────────────────────────────────────────────────
    @staticmethod
    def _counter(cmd: dict, key: str) -> tuple[int, float]:
        """Teller-opdracht {n, delta} uit de opdracht lezen (seek, skip)."""
        d = cmd.get(key) if isinstance(cmd.get(key), dict) else {}
        try:
            return int(d.get("n", 0)), float(d.get("delta", 0))
        except (TypeError, ValueError):
            return 0, 0.0

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
        playlist = cmd.get("playlist")
        playlist = str(playlist) if isinstance(playlist, str) and playlist else None
        if playlist:
            video = None
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
            self.videos = []
            self.rescan()
            self._refresh_playlists()

        # Springen (+30 s e.d.): elke klik op het paneel verhoogt seek.n; na een herstart niet herhalen
        seek_n, seek_delta = self._counter(cmd, "seek")
        if self.state.seek_n < 0:
            self.state.seek_n = seek_n
        elif seek_n != self.state.seek_n:
            self.state.seek_n = seek_n
            bijzaak = True
            if seek_delta and self.ctrl.seek(seek_delta):
                _log.info("spring %+.0f s", seek_delta)

        # Vorige/volgende binnen een speellijst: zelfde principe met skip.n
        skip_n, skip_delta = self._counter(cmd, "skip")
        if self.state.skip_n < 0:
            self.state.skip_n = skip_n
        elif skip_n != self.state.skip_n:
            self.state.skip_n = skip_n
            bijzaak = True
            if skip_delta and self.ctrl.step(int(skip_delta)):
                _log.info("speellijst: %s film", "volgende" if skip_delta > 0 else "vorige")

        overlay = parse_overlay(cmd.get("overlay"), self.tekstlaag.basis_uitlijning)
        if overlay != self.state.overlay:
            self.state.overlay = overlay
            bijzaak = True
            self.tekstlaag.configure(overlay)

        differs = video != self.state.video or playlist != self.state.playlist
        self.state.video, self.state.playlist, self.state.seq = video, playlist, seq
        # Een nieuwe seq betekent een klik op het paneel: dezelfde video opnieuw laden (hervat waar
        # hij was), behalve als die klik over de tekstlaag, springen of de videomap ging.
        if differs or (changed_seq and not bijzaak):
            _log.info("opdracht #%d: %s", seq, f"speellijst {playlist}" if playlist else f"video {video}")
            self._apply_state(restart=True)
        self.state.save()

    def _apply_state(self, *, restart: bool) -> None:
        if self._settings_open:
            return                          # speler staat stil; bij opnieuw starten wordt dit toegepast
        st = self.state
        self._last_error = ""
        self.tekstlaag.set_actief(False)
        if st.playlist:
            self._apply_playlist(restart)
            return
        self._list_files, self._list_index = [], -1
        if not st.video:
            self.ctrl.stop()
            self._current_video_abs = ""
            self._show_idle("Nog geen video gekozen.")
            return
        full = resolve_video(self.cfg.videos_dir, st.video)
        if full is None:
            self._fail(f"Video niet gevonden: {st.video}", f"Video niet gevonden in de videomap:\n{st.video}")
            return
        if restart or str(full) != self._current_video_abs:
            self._save_positions()                       # positie van de vorige film bewaren
            start = self.state.positions.get(st.video, 0.0)
            if self.ctrl.play(str(full), start=start):
                if start > 1:
                    _log.info("hervat %s op %d:%02d", st.video, int(start) // 60, int(start) % 60)
                self._current_video_abs = str(full)
            else:
                self._fail(f"Afspelen mislukt: {st.video}", f"Afspelen mislukt:\n{st.video}", log=False)
                return
        self.stack.setCurrentIndex(PAGE_VIDEO)
        self._tekstlaag_maat()

    def _apply_playlist(self, restart: bool) -> None:
        name = self.state.playlist
        pl = self._playlist(name)
        if pl is None:
            self._fail(f"Speellijst niet gevonden: {name}", f"Speellijst niet gevonden op deze laptop:\n{name}")
            return
        files = pl.files(self.cfg.videos_dir)
        if not files:
            self._fail(f"Speellijst {name}: geen films gevonden", f"Geen films gevonden voor speellijst:\n{name}")
            return
        key = LIST_PREFIX + name
        if restart or key != self._current_video_abs:
            self._save_positions()
            waar = self.state.playlist_pos.get(name, {})
            index = int(waar.get("index", 0)) % len(files)
            start = float(waar.get("pos", 0.0))
            if self.ctrl.play_list([str(f) for f in files], index, start):
                _log.info("speellijst %s: %d films, begin bij %d (%s)", name, len(files), index + 1, files[index].name)
                self._current_video_abs = key
                self._list_files, self._list_index = files, index
            else:
                self._fail(f"Speellijst {name}: afspelen mislukt", f"Afspelen mislukt:\n{name}", log=False)
                return
        self.stack.setCurrentIndex(PAGE_VIDEO)
        self._tekstlaag_maat()

    def _fail(self, error: str, screen_text: str, *, log: bool = True) -> None:
        self.ctrl.stop()
        self._current_video_abs = ""
        self._list_files, self._list_index = [], -1
        self._last_error = error
        self._show_idle(screen_text)
        if log:
            _log.warning(error)

    def _show_idle(self, text: str) -> None:
        self.lbl_idle.setText(text)
        self.stack.setCurrentIndex(PAGE_IDLE)

    # ── status richting paneel ──────────────────────────────────────────
    def _on_position(self, pos: float, dur: float, paused: bool) -> None:
        self._pos, self._dur, self._paused = pos, dur, paused
        if not self._current_video_abs or dur <= 0:
            return
        if self.state.playlist:
            i = self.ctrl.list_index()
            if i >= 0:
                if i != self._list_index and self._list_index >= 0 and i < len(self._list_files):
                    _log.info("speellijst %s: film %d (%s)", self.state.playlist, i + 1, self._list_files[i].name)
                self._list_index = i
                self.state.playlist_pos[self.state.playlist] = {"index": i, "pos": round(pos, 1)}
        elif self.state.video:
            self.state.positions[self.state.video] = round(pos, 1)

    def _save_positions(self) -> None:
        """Voortgang per film en per speellijst naar state.json; wat niet meer bestaat valt eruit."""
        if self.videos:
            for k in list(self.state.positions):
                if k not in self.videos:
                    del self.state.positions[k]
        namen = {pl.name for pl in self.playlists}
        for k in list(self.state.playlist_pos):
            if k not in namen:
                del self.state.playlist_pos[k]
        self.state.save()

    def _on_player_error(self, msg: str) -> None:
        self._last_error = msg

    def _on_online(self, online: bool) -> None:
        self._online = online
        if self.settings_win is not None and self._settings_open:
            self.settings_win.refresh_status()

    def _on_remote_failed(self, msg: str) -> None:
        if self.stack.currentIndex() == PAGE_IDLE and not self._last_error and not self.state.playlist:
            self.lbl_idle.setText(f"Nog geen video gekozen.\nPaneel niet bereikbaar: {msg}")

    def _video_label(self) -> str | None:
        """Naam van de film die nu speelt, zoals het paneel hem toont."""
        if not self.state.playlist:
            return self.state.video
        if 0 <= self._list_index < len(self._list_files):
            f = self._list_files[self._list_index]
            try:
                return f.resolve().relative_to(Path(self.cfg.videos_dir).resolve()).as_posix()
            except (ValueError, OSError):
                return f.name
        return None

    def status_snapshot(self) -> dict:
        playing = self.stack.currentIndex() == PAGE_VIDEO and bool(self._current_video_abs)
        in_list = playing and bool(self.state.playlist)
        error = self._last_error
        if self._settings_open and not error:
            error = "speler staat stil: instellingenvenster open op de laptop"
        return {
            "id": self.cfg.player_id,
            "video": self._video_label() if playing else None,
            "playlist": self.state.playlist if in_list else None,
            "playlist_index": self._list_index + 1 if in_list else 0,
            "playlist_count": len(self._list_files) if in_list else 0,
            "playlists": list(self.playlist_info),
            "overlay": self.tekstlaag.status(),
            "position": round(self._pos, 1) if playing else 0,
            "duration": round(self._dur, 1) if playing else 0,
            "paused": self._paused if playing else False,
            "videos": list(self.videos),
            "videos_dir": str(self.cfg.videos_dir),
            "hostname": self._hostname,
            "version": __version__,
            "seq_applied": self.state.seq,
            "settings_open": self._settings_open,
            "error": error,
        }

    def _push_status(self) -> None:
        if self.poller is not None:
            self.poller.update_status(self.status_snapshot())
