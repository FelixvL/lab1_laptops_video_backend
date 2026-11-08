"""
CinePlayer — instellingen (settings.json) en lokale toestand (state.json), geen Qt.

settings.json  wordt door jou ingevuld: serveradres, API-sleutel, videomap, polling-interval.
state.json     schrijft de speler zelf: laatst toegepaste opdracht, zodat hij na een
               herstart verdergaat waar hij gebleven was (modus + video, niet de positie).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("cineplayer.config")

APP_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = APP_ROOT / "settings.json"
STATE_FILE = APP_ROOT / "state.json"

DEFAULT_WEBSITE_URL = "https://0111.nl/lab-1/lobby-video/"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        _log.warning("%s onleesbaar: %s", path.name, e)
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> bool:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as e:
        _log.warning("%s niet opgeslagen: %s", path.name, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


@dataclass
class Config:
    server_url: str = ""
    api_key: str = ""
    videos_dir: Path = APP_ROOT / "videos"
    poll_seconds: float = 3.0
    rescan_seconds: float = 30.0
    request_timeout: float = 6.0
    website_url: str = DEFAULT_WEBSITE_URL
    browser_path: str = ""         # leeg = Edge/Chrome zelf zoeken (website-modus, zie browser.py)

    @property
    def remote_enabled(self) -> bool:
        return bool(self.server_url and self.api_key)


def load_config(path: Path = SETTINGS_FILE) -> Config:
    d = _read_json(path)
    cfg = Config()
    cfg.server_url = str(d.get("server_url") or "").strip()
    cfg.api_key = str(d.get("api_key") or "").strip()
    videos = str(d.get("videos_dir") or "videos")
    vp = Path(videos)
    cfg.videos_dir = vp if vp.is_absolute() else (path.parent / vp)
    cfg.poll_seconds = max(1.0, float(d.get("poll_seconds") or 3.0))
    cfg.rescan_seconds = max(5.0, float(d.get("rescan_seconds") or 30.0))
    cfg.request_timeout = max(2.0, float(d.get("request_timeout") or 6.0))
    cfg.website_url = str(d.get("website_url") or DEFAULT_WEBSITE_URL)
    cfg.browser_path = str(d.get("browser_path") or "").strip()
    return cfg


@dataclass
class State:
    mode: str = "video"            # 'video' of 'website'
    video: str | None = None       # relatief pad binnen de videomap
    url: str = DEFAULT_WEBSITE_URL
    seq: int = -1                  # laatst toegepaste opdrachtvolgnummer (-1 = nog geen)
    rescan: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "State":
        d = _read_json(path)
        st = cls()
        st.mode = "website" if d.get("mode") == "website" else "video"
        v = d.get("video")
        st.video = str(v) if isinstance(v, str) and v else None
        st.url = str(d.get("url") or DEFAULT_WEBSITE_URL)
        try:
            st.seq = int(d.get("seq", -1))
        except (TypeError, ValueError):
            st.seq = -1
        try:
            st.rescan = int(d.get("rescan", 0))
        except (TypeError, ValueError):
            st.rescan = 0
        return st

    def save(self, path: Path = STATE_FILE) -> bool:
        d = asdict(self)
        d.pop("extra", None)
        return _write_json(path, d)
