"""
CinePlayer — instellingen (settings.json) en lokale toestand (state.json), geen Qt.

settings.json  wordt door jou ingevuld: serveradres, API-sleutel, videomap, polling-interval.
state.json     schrijft de speler zelf: laatst toegepaste opdracht en per video de positie,
               zodat hij na een herstart (of na wisselen) verdergaat waar die film was.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("cineplayer.config")

APP_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = APP_ROOT / "settings.json"
STATE_FILE = APP_ROOT / "state.json"

MODI = ("highlight", "avond", "gewoon")      # zoals op de lobby-site; 'gewoon' = alles even wit


def clean_player_id(s: str) -> str:
    """Zelfde regels als clean_player_id() in php/lib.php: letters, cijfers, _ . -, max 40 tekens."""
    s = re.sub(r"[^A-Za-z0-9_.-]", "", s)[:40].strip(".")
    return s or "onbekend"
# Uitlijning per element: x in % van de breedte, y in % van de hoogte, s = schaal in %.
# Standaard = de stand van de lobby-site op 13 november 2026 (config.json → uitlijning.lobby).
DEFAULT_UITLIJNING: dict[str, dict[str, float]] = {
    "geheel": {"x": 0, "y": 7, "s": 95},
    "titel": {"x": 0, "y": 3, "s": 60},
    "lijst": {"x": 1, "y": 0, "s": 55},
}
DEFAULT_OVERLAY: dict[str, Any] = {"aan": False, "modus": "highlight", "geschiedenis": 30,
                                   "uitlijning": {k: dict(v) for k, v in DEFAULT_UITLIJNING.items()}}


def uitlijning_van(v: Any, basis: dict[str, dict[str, float]] | None = None) -> dict[str, dict[str, float]]:
    """Uitlijning samenvoegen met de standaard (of `basis`); ongeldige waarden genegeerd, begrensd
    zoals op de site (x/y -60..60, s 20..400)."""
    out = {k: dict(d) for k, d in (basis or DEFAULT_UITLIJNING).items()}
    if isinstance(v, dict):
        for el, d in v.items():
            if el in out and isinstance(d, dict):
                for key, lo, hi in (("x", -60, 60), ("y", -60, 60), ("s", 20, 400)):
                    try:
                        if key in d:
                            out[el][key] = max(lo, min(hi, round(float(d[key]), 2)))
                    except (TypeError, ValueError):
                        pass
    return out


def parse_overlay(v: Any, basis_uitlijning: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    """Tekstlaag-instellingen uit command.json/state.json netjes maken (onbekend = standaard)."""
    d = v if isinstance(v, dict) else {}
    try:
        geschiedenis = max(0, min(720, int(d.get("geschiedenis", 30))))
    except (TypeError, ValueError):
        geschiedenis = 30
    modus = d.get("modus")
    return {"aan": bool(d.get("aan", False)),
            "modus": modus if modus in MODI else "highlight",
            "geschiedenis": geschiedenis,
            "uitlijning": uitlijning_van(d.get("uitlijning"), basis_uitlijning)}


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
    player_id: str = ""            # naam waarmee deze laptop zich meldt op het paneel (leeg = hostnaam)
    programma_url: str = "https://www.lab-1.nl/list2"   # feed met het filmprogramma (tekstlaag)
    overlay_uitlijning: dict[str, Any] | None = None     # optioneel: geheel/titel/lijst {x, y, s} zoals op de site
    settings_path: Path = SETTINGS_FILE                  # waar deze instellingen vandaan komen (voor opslaan)

    @property
    def remote_enabled(self) -> bool:
        return bool(self.server_url and self.api_key)


def load_config(path: Path = SETTINGS_FILE) -> Config:
    d = _read_json(path)
    cfg = Config()
    cfg.settings_path = Path(path)
    cfg.server_url = str(d.get("server_url") or "").strip()
    cfg.api_key = str(d.get("api_key") or "").strip()
    videos = str(d.get("videos_dir") or "videos")
    vp = Path(videos)
    cfg.videos_dir = vp if vp.is_absolute() else (path.parent / vp)
    cfg.poll_seconds = max(1.0, float(d.get("poll_seconds") or 3.0))
    cfg.rescan_seconds = max(5.0, float(d.get("rescan_seconds") or 30.0))
    cfg.request_timeout = max(2.0, float(d.get("request_timeout") or 6.0))
    cfg.player_id = clean_player_id(str(d.get("player_id") or socket.gethostname()))
    cfg.programma_url = str(d.get("programma_url") or cfg.programma_url).strip()
    u = d.get("overlay_uitlijning")
    cfg.overlay_uitlijning = uitlijning_van(u) if isinstance(u, dict) else None
    return cfg


def update_settings(path: Path, **changes: Any) -> bool:
    """Sleutels in settings.json bijwerken (de rest blijft staan); voor het instellingenvenster."""
    d = _read_json(Path(path))
    d.update(changes)
    return _write_json(Path(path), d)


@dataclass
class State:
    video: str | None = None       # relatief pad binnen de videomap (één film in herhaling)
    playlist: str | None = None    # naam van de speellijst die speelt (dan is `video` leeg)
    playlist_pos: dict[str, dict[str, float]] = field(default_factory=dict)   # per speellijst: {index, pos}
    skip_n: int = -1               # laatst toegepaste vorige/volgende-opdracht binnen een speellijst
    seq: int = -1                  # laatst toegepaste opdrachtvolgnummer (-1 = nog geen)
    rescan: int = 0
    overlay: dict[str, Any] = field(default_factory=lambda: parse_overlay(None))   # tekstlaag over de video
    positions: dict[str, float] = field(default_factory=dict)   # per video: waar hij was (seconden), voor hervatten
    seek_n: int = -1               # laatst toegepaste spring-opdracht van het paneel (-1 = nog geen)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "State":
        d = _read_json(path)
        st = cls()
        v = d.get("video")
        st.video = str(v) if isinstance(v, str) and v else None
        v = d.get("playlist")
        st.playlist = str(v) if isinstance(v, str) and v else None
        pp = d.get("playlist_pos")
        if isinstance(pp, dict):
            for k, v in pp.items():
                try:
                    st.playlist_pos[str(k)] = {"index": int(v.get("index", 0)), "pos": round(float(v.get("pos", 0)), 1)}
                except (AttributeError, TypeError, ValueError):
                    pass
        try:
            st.skip_n = int(d.get("skip_n", -1))
        except (TypeError, ValueError):
            st.skip_n = -1
        try:
            st.seq = int(d.get("seq", -1))
        except (TypeError, ValueError):
            st.seq = -1
        try:
            st.rescan = int(d.get("rescan", 0))
        except (TypeError, ValueError):
            st.rescan = 0
        st.overlay = parse_overlay(d.get("overlay"))
        pos = d.get("positions")
        if isinstance(pos, dict):
            for k, v in pos.items():
                try:
                    if isinstance(k, str) and float(v) > 0:
                        st.positions[k] = round(float(v), 1)
                except (TypeError, ValueError):
                    pass
        try:
            st.seek_n = int(d.get("seek_n", -1))
        except (TypeError, ValueError):
            st.seek_n = -1
        return st

    def save(self, path: Path = STATE_FILE) -> bool:
        d = asdict(self)
        d.pop("extra", None)
        return _write_json(path, d)
