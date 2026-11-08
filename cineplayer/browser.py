"""
CinePlayer — website-modus met de echte browser (Microsoft Edge, anders Chrome).

De speler bouwt de website niet na en gebruikt geen ingebouwde browser: hij start
Edge in kioskstand (fullscreen, zonder adresbalk of meldingen) op de URL, en sluit
Edge weer zodra het paneel terug naar video schakelt. De pagina ziet er dus precies
zo uit als in een gewone browser, inclusief MP4-video.

- Eigen profielmap (`browser_profile/` naast player.py): de gewone Edge-instellingen
  van de laptop blijven onaangeroerd, en er komt geen "pagina's herstellen?"-balk.
- Sluit iemand Edge (Alt+F4) terwijl website-modus actief is, dan start hij opnieuw.
- `find_browser()` zoekt Edge en Chrome op de gebruikelijke plekken; `browser_path`
  in settings.json gaat daar bovenuit.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

_log = logging.getLogger("cineplayer.browser")

CHECK_MS = 1000            # hoe vaak we kijken of de browser nog leeft
QUICK_EXIT_S = 2.0         # eerder dan dit afgesloten = overgedragen aan een al draaiende Edge
RESTART_DELAYS_S = (2, 5, 10, 20)

_WIN = sys.platform == "win32"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if _WIN else 0  # type: ignore[attr-defined]

CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
)


def find_browser(preferred: str = "") -> str | None:
    """Pad naar msedge.exe (of chrome.exe). `preferred` uit settings.json gaat voor."""
    if preferred:
        p = Path(preferred)
        if p.is_file():
            return str(p)
        _log.warning("browser_path bestaat niet: %s — ik zoek zelf", preferred)
    for c in CANDIDATES:
        if c and os.path.isfile(c):
            return c
    if _WIN:
        try:
            import winreg
            for exe in ("msedge.exe", "chrome.exe"):
                for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                    try:
                        with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                            val, _ = winreg.QueryValueEx(k, "")
                            if val and os.path.isfile(val):
                                return val
                    except OSError:
                        continue
        except ImportError:  # pragma: no cover
            pass
    for exe in ("msedge", "chrome"):
        found = shutil.which(exe)
        if found:
            return found
    return None


def _find_main_window(pid: int) -> int:
    """Grootste zichtbare top-level venster van proces `pid` (0 als er nog geen is)."""
    if not _WIN:
        return 0
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: list[int] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left > 200 and r.bottom - r.top > 200:
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else 0


def _bring_to_front(hwnd: int) -> None:
    """Venster vooraan en actief maken (wij zijn het voorgrondproces, dus dat mag)."""
    if not _WIN or not hwnd:
        return
    import ctypes
    user32 = ctypes.windll.user32
    HWND_TOP, SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0, 0x0001, 0x0002, 0x0040
    user32.ShowWindow(ctypes.c_void_p(hwnd), 5)  # SW_SHOW
    user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOP), 0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
    user32.SetForegroundWindow(ctypes.c_void_p(hwnd))


def _allow_foreground() -> None:
    """Sta het volgende proces dat we starten toe om zelf de voorgrond te pakken."""
    if _WIN:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint(0xFFFFFFFF))  # ASFW_ANY


def is_edge(exe: str) -> bool:
    return "msedge" in Path(exe).name.lower()


def browser_args(exe: str, url: str, profile_dir: Path | str, *, kiosk: bool = True,
                 size: tuple[int, int] = (1280, 720)) -> list[str]:
    """Opdrachtregel voor Edge/Chrome: kiosk fullscreen (of een app-venster voor --windowed)."""
    args = [
        exe,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-pinch",
        "--overscroll-history-navigation=0",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-features=Translate,TranslateUI,msEdgeSidebarV2,msHubApps",
    ]
    if kiosk:
        args += ["--kiosk", "--start-fullscreen", "--window-position=0,0"]
        if is_edge(exe):
            args += ["--edge-kiosk-type=fullscreen", "--kiosk-idle-timeout-minutes=0"]
        args.append(url)
    else:
        args += [f"--window-size={size[0]},{size[1]}", "--window-position=40,40", f"--app={url}"]
    return args


PROFILE_PREFS = {
    "translate": {"enabled": False},                 # geen "pagina vertalen?"-popup
    "profile": {"exit_type": "Normal", "exited_cleanly": True},
    "browser": {"has_seen_welcome_page": True, "check_default_browser": False},
    "intl": {"accept_languages": "nl-NL,nl,en"},
    "edge": {"show_first_run_experience": False},
}


def prepare_profile(profile_dir: Path) -> None:
    """Maak de profielmap en, bij de eerste keer, een Preferences met rustige instellingen."""
    default_dir = profile_dir / "Default"
    prefs = default_dir / "Preferences"
    try:
        default_dir.mkdir(parents=True, exist_ok=True)
        if not prefs.exists():
            prefs.write_text(json.dumps(PROFILE_PREFS), encoding="utf-8")
    except OSError as e:
        _log.warning("profielmap niet voor te bereiden: %s", e)


class BrowserKiosk(QObject):
    """Start/stop de browser en houdt hem in de lucht zolang `want_url` gezet is."""

    running_changed = pyqtSignal(bool)
    failed = pyqtSignal(str)

    def __init__(self, exe: str | None, profile_dir: Path, *, kiosk: bool = True,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.exe = exe
        self.profile_dir = Path(profile_dir)
        self.kiosk = kiosk
        self.want_url: str = ""
        self._proc: subprocess.Popen | None = None
        self._hwnd = 0
        self._started_at = 0.0
        self._failures = 0
        self._running = False
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_MS)
        self._timer.timeout.connect(self._check)
        self._restart_at = 0.0

    # ── publiek ─────────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def name(self) -> str:
        return Path(self.exe).stem if self.exe else ""

    def start(self, url: str) -> bool:
        """Toon `url`. Een andere URL (of al gestopt) herstart de browser; dezelfde URL
        terwijl hij draait doet niets."""
        if self.running and url == self.want_url:
            return True
        self.want_url = url
        self._failures = 0
        self._restart_at = 0.0
        self._kill_current()
        ok = self._launch()
        self._timer.start()
        return ok

    def stop(self) -> None:
        self.want_url = ""
        self._timer.stop()
        self._kill_current()

    # ── intern ──────────────────────────────────────────────────────────
    def _launch(self) -> bool:
        if not self.exe:
            self.failed.emit("Geen browser gevonden (Microsoft Edge of Chrome). Zet browser_path in settings.json.")
            return False
        prepare_profile(self.profile_dir)
        args = browser_args(self.exe, self.want_url, self.profile_dir, kiosk=self.kiosk)
        self._hwnd = 0
        _allow_foreground()
        try:
            self._proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, close_fds=True)
        except OSError as e:
            self._proc = None
            self.failed.emit(f"Browser start niet: {e}")
            return False
        self._started_at = time.monotonic()
        _log.info("%s gestart (pid %d): %s", self.name, self._proc.pid, self.want_url)
        self._set_running(True)
        return True

    def _check(self) -> None:
        if not self.want_url:
            return
        if self.running:
            if not self._hwnd:
                # Een nieuw gestart programma krijgt van Windows niet vanzelf de voorgrond;
                # zodra het browservenster er is halen we het zelf vóór het spelervenster.
                self._hwnd = _find_main_window(self._proc.pid)
                if self._hwnd:
                    _bring_to_front(self._hwnd)
                    _log.info("%s-venster naar voren gehaald", self.name)
                elif time.monotonic() - self._started_at > 15:
                    _log.warning("geen venster van %s gevonden (pid %d)", self.name, self._proc.pid)
                    self._hwnd = -1
            return
        rc = self._proc.returncode if self._proc is not None else None
        lived = time.monotonic() - self._started_at if self._proc is not None else 0.0
        if self._proc is not None:
            self._proc = None
            self._set_running(False)
            if lived < QUICK_EXIT_S:
                # Edge geeft de URL door aan een al draaiende Edge met ons profiel en stopt zelf.
                _log.warning("%s stopte direct (code %s); oude instantie met ons profiel opruimen", self.name, rc)
                self._kill_by_profile()
                self._restart_at = time.monotonic() + 1.0
            else:
                _log.warning("%s afgesloten (code %s); herstart over %d s", self.name, rc,
                             RESTART_DELAYS_S[min(self._failures, len(RESTART_DELAYS_S) - 1)])
                self._restart_at = time.monotonic() + RESTART_DELAYS_S[min(self._failures, len(RESTART_DELAYS_S) - 1)]
                self._failures += 1
            return
        if self._restart_at and time.monotonic() >= self._restart_at:
            self._restart_at = 0.0
            self._launch()

    def bring_to_front(self) -> None:
        """Browservenster opnieuw naar voren (bv. als het spelervenster per ongeluk actief werd)."""
        if self.running and self._hwnd and self._hwnd > 0:
            _bring_to_front(self._hwnd)

    def _kill_current(self) -> None:
        proc, self._proc = self._proc, None
        self._hwnd = 0
        if proc is None:
            return
        if proc.poll() is None:
            _log.info("%s sluiten (pid %d)", self.name, proc.pid)
            if _WIN:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW, check=False)
            else:  # pragma: no cover
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._set_running(False)

    def _kill_by_profile(self) -> None:
        """Sluit elke Edge/Chrome die met onze profielmap draait (bv. een achtergebleven exemplaar)."""
        if not _WIN or not self.profile_dir.exists():
            return
        needle = str(self.profile_dir).replace("'", "''")
        script = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
                  f"Where-Object {{ $_.CommandLine -like '*{needle}*' }} | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            _log.warning("opruimen oude browser mislukt: %s", e)

    def cleanup_stale(self) -> None:
        """Bij het opstarten van de speler: browser van een vorige (gecrashte) run opruimen."""
        if self.profile_dir.exists():
            self._kill_by_profile()

    def _set_running(self, running: bool) -> None:
        if running != self._running:
            self._running = running
            self.running_changed.emit(running)
