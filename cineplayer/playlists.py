"""
CinePlayer — speellijsten (geen Qt).

Een speellijst is een rij films die de speler achter elkaar afspeelt en daarna
opnieuw begint. Ze komen binnen via een import van bestaande speellijstbestanden,
bijvoorbeeld uit Media Player Classic:

    .mpcpl          MPC-HC / MPC-BE   regels "N,filename,<pad>"
    .m3u / .m3u8    algemeen          één pad per regel, '#' is commentaar
    .pls            algemeen          regels "FileN=<pad>"

`import_playlist()` leest zo'n bestand en bewaart het als `playlists/<naam>.json`
naast player.py, met de paden zoals ze in het bestand stonden (relatieve paden
ten opzichte van het speellijstbestand). Bij het afspelen zoekt `Playlist.files()`
elke film op: eerst op het bewaarde pad, en als die er niet is op bestandsnaam
in de videomap. Zo werkt een lijst van een andere computer ook als de films
hier in een andere map staan.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from .library import is_video

_log = logging.getLogger("cineplayer.playlists")

APP_ROOT = Path(__file__).resolve().parent.parent
PLAYLISTS_DIR = APP_ROOT / "playlists"
PLAYLIST_EXTS = (".mpcpl", ".m3u", ".m3u8", ".pls")
FILE_FILTER = "Speellijsten (*.mpcpl *.m3u *.m3u8 *.pls);;Alle bestanden (*)"

_RE_MPCPL = re.compile(r"^(\d+),([^,]+),(.*)$")
_RE_PLS = re.compile(r"^File(\d+)=(.*)$", re.I)
_RE_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_RE_NAME = re.compile(r"[^A-Za-z0-9 _.()\-]")


def clean_name(name: str) -> str:
    """Naam die veilig is als bestandsnaam en op het paneel (zelfde tekens als in php/lib.php)."""
    name = _RE_NAME.sub("", name).strip(" .")[:60]
    return name or "speellijst"


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            if enc == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def parse_playlist_text(text: str, suffix: str) -> list[str]:
    """De paden uit een speellijstbestand, in afspeelvolgorde. `suffix` bepaalt het formaat."""
    suffix = suffix.lower()
    lines = [ln.strip() for ln in text.splitlines()]
    if suffix == ".mpcpl":
        found: dict[int, str] = {}
        for ln in lines:
            m = _RE_MPCPL.match(ln)
            if m and m.group(2).strip().lower() == "filename" and m.group(3).strip():
                found.setdefault(int(m.group(1)), m.group(3).strip())
        return [found[k] for k in sorted(found)]
    if suffix == ".pls":
        found = {}
        for ln in lines:
            m = _RE_PLS.match(ln)
            if m and m.group(2).strip():
                found[int(m.group(1))] = m.group(2).strip()
        return [found[k] for k in sorted(found)]
    # .m3u / .m3u8 (en onbekend): één pad per regel
    return [ln for ln in lines if ln and not ln.startswith("#")]


def _absolute(item: str, base: Path) -> str | None:
    """Pad uit een speellijst absoluut maken; None voor internetadressen (alleen lokale films)."""
    item = item.strip().strip('"')
    if not item or _RE_URL.match(item):
        if item.lower().startswith("file:///"):
            item = item[8:].replace("/", "\\")
        else:
            return None
    win = PureWindowsPath(item)
    if win.is_absolute() or item.startswith(("/", "\\\\")):
        return str(win) if win.drive else item
    return str((base / Path(item.replace("\\", "/"))).resolve())


@dataclass
class Playlist:
    name: str
    items: list[str] = field(default_factory=list)      # absolute paden zoals geïmporteerd
    source: str = ""                                    # het bestand waar hij uit komt

    def files(self, videos_root: Path | None = None, _index: dict[str, Path] | None = None) -> list[Path]:
        """De films die nu echt te vinden zijn, in volgorde."""
        return [p for p in self.resolve(videos_root, _index) if p is not None]

    def resolve(self, videos_root: Path | None = None, _index: dict[str, Path] | None = None) -> list[Path | None]:
        """Per item het pad waar de film nu staat, of None als hij nergens te vinden is."""
        index = _index
        out: list[Path | None] = []
        for item in self.items:
            p = Path(item)
            try:
                if p.is_file():
                    out.append(p)
                    continue
            except OSError:
                pass
            if index is None:
                index = basename_index(videos_root)
            out.append(index.get(PureWindowsPath(item).name.casefold()))
        return out

    def summary(self, videos_root: Path | None = None, _index: dict[str, Path] | None = None) -> dict:
        found = self.files(videos_root, _index)
        return {"name": self.name, "count": len(found), "missing": len(self.items) - len(found)}

    def to_json(self) -> dict:
        return {"name": self.name, "source": self.source, "items": self.items}


def basename_index(videos_root: Path | None) -> dict[str, Path]:
    """Bestandsnaam (kleine letters) → pad, voor alle video's onder de videomap."""
    index: dict[str, Path] = {}
    if videos_root is None or not Path(videos_root).is_dir():
        return index
    for p in sorted(Path(videos_root).rglob("*")):
        if p.is_file() and is_video(p):
            index.setdefault(p.name.casefold(), p)
    return index


def read_playlist_file(src: Path) -> list[str]:
    """Speellijstbestand lezen → absolute lokale paden (relatief t.o.v. het bestand zelf)."""
    src = Path(src)
    raw = parse_playlist_text(_read_text(src), src.suffix)
    out = []
    for item in raw:
        full = _absolute(item, src.parent)
        if full:
            out.append(full)
    return out


def load_playlists(directory: Path = PLAYLISTS_DIR) -> list[Playlist]:
    """Alle bewaarde speellijsten, op naam."""
    out: list[Playlist] = []
    if not Path(directory).is_dir():
        return out
    for f in sorted(Path(directory).glob("*.json"), key=lambda p: p.stem.casefold()):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            items = [str(i) for i in d.get("items", []) if isinstance(i, str) and i]
            out.append(Playlist(name=clean_name(str(d.get("name") or f.stem)), items=items,
                                source=str(d.get("source") or "")))
        except (OSError, ValueError, AttributeError) as e:
            _log.warning("speellijst %s onleesbaar: %s", f.name, e)
    return out


def import_playlist(src: Path, directory: Path = PLAYLISTS_DIR, name: str | None = None) -> Playlist:
    """Speellijstbestand inlezen en bewaren. Een lijst met dezelfde naam wordt vervangen.
    Gooit ValueError als er geen films in staan."""
    src = Path(src)
    items = read_playlist_file(src)
    if not items:
        raise ValueError(f"Geen films gevonden in {src.name}")
    pl = Playlist(name=clean_name(name or src.stem), items=items, source=str(src))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pl.name}.json").write_text(json.dumps(pl.to_json(), ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    _log.info("speellijst geïmporteerd: %s (%d films) uit %s", pl.name, len(items), src)
    return pl


def delete_playlist(name: str, directory: Path = PLAYLISTS_DIR) -> bool:
    f = Path(directory) / f"{clean_name(name)}.json"
    try:
        f.unlink()
        return True
    except OSError:
        return False
