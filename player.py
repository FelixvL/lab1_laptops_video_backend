#!/usr/bin/env python3
"""
LAB-1 Lobby — kiosk-videospeler.   Starten:   python player.py

Speelt fullscreen video's uit de map `videos/`, desgewenst met het filmprogramma van
LAB-1 als tekstlaag erover, en wordt op afstand aangestuurd via het PHP-beheerpaneel
(zie settings.json).

Opties:
  --windowed      gewoon venster in plaats van fullscreen kiosk (voor testen)
  --settings PAD  ander settings.json

Vereist: PyQt6, python-mpv, libmpv-2.dll (C:\\mpv of naast dit bestand).
Afsluiten voor onderhoud: Ctrl+Q.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent

# libmpv-2.dll vindbaar maken: C:\mpv e.d. én de app-map zelf vóór op PATH
if sys.platform == "win32":
    for candidate in (r"C:\mpv", r"C:\Program Files\mpv", r"C:\Program Files (x86)\mpv"):
        if os.path.isdir(candidate):
            os.environ["PATH"] = candidate + os.pathsep + os.environ["PATH"]
    os.environ["PATH"] = str(APP_ROOT) + os.pathsep + os.environ["PATH"]


def _setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s — %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        fh = RotatingFileHandler(APP_ROOT / "player.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LAB-1 Lobby kiosk-videospeler")
    parser.add_argument("--windowed", action="store_true", help="geen fullscreen kiosk (testen)")
    parser.add_argument("--settings", default=str(APP_ROOT / "settings.json"), help="pad naar settings.json")
    args = parser.parse_args(argv)
    _setup_logging()

    from PyQt6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LAB-1 Lobby")

    try:
        import mpv  # noqa: F401
    except OSError:
        QMessageBox.critical(None, "mpv niet gevonden",
                             "libmpv-2.dll ontbreekt.\n\nZet libmpv-2.dll in C:\\mpv\\ of naast player.py.\n"
                             "Download: https://sourceforge.net/projects/mpv-player-windows/files/libmpv/")
        return 1

    from cineplayer.config import load_config
    from cineplayer.main_window import KioskWindow

    cfg = load_config(Path(args.settings))
    logging.getLogger("cineplayer").info("start — videomap %s — paneel %s", cfg.videos_dir, cfg.server_url or "(geen)")
    cfg.videos_dir.mkdir(parents=True, exist_ok=True)

    win = KioskWindow(cfg, kiosk=not args.windowed)
    win.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
