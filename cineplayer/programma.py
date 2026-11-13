"""
CinePlayer — het filmprogramma van LAB-1, zonder Qt.

Zelfde logica als index.php van de lobby-website:
  - `lees_feed(tekst)`      : de feed (https://www.lab-1.nl/list2) als platte tekst → dagen met films
  - `haal_programma(url)`   : ophalen met een cache van een minuut en een terugval op de
                              laatste goede versie van vandaag (programma-cache.json)
  - `stel_lijst_samen(...)` : de lijst voor het scherm: geschiedenis, modus Avond, programma van
                              morgen, titel inkorten, en de highlight-tinten per groep van 10 minuten
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

_log = logging.getLogger("cineplayer.programma")

FEED_URL = "https://www.lab-1.nl/list2"
FEED_TIMEOUT = 3
CACHE_SECONDS = 60
AVOND_VANAF = "18:00"
GROEP_MINUTEN = 10
MODI = ("highlight", "avond", "gewoon")     # gewoon = hele dag, alles even wit (geen highlight)

APP_ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = APP_ROOT / "programma-cache.json"

_RE_FILM = re.compile(r"^(\d{1,2}:\d{2})\s+(.+?)\s{2,}(Cinema.*)$", re.U)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_HAAKJES = re.compile(r"\s*\(.*?\)\s*$", re.U)


@dataclass
class Film:
    tijd: str
    titel: str
    zaal: str
    klasse: str = ""      # '', 'hl0', 'hl1', 'hl2' of 'mat'


@dataclass
class Dag:
    dag: str
    films: list[Film] = field(default_factory=list)


@dataclass
class Lijst:
    films: list[Film]
    heeft_vandaag: bool
    titel: str                  # volledige titel, bv. "LAB-1 Cinema Today" of "Tomorrow in LAB-1 Cinema"
    titel_groot: str | None     # "Tomorrow"/"Tonight", of None voor de gewone titel
    titel_klein: str            # "in LAB-1 Cinema"
    volgende_dag: bool

    @property
    def melding(self) -> str | None:
        """Tekst in plaats van de lijst als er niets te tonen is (zoals .leeg op de site)."""
        if self.films:
            return None
        return "Geen voorstellingen meer vandaag" if self.heeft_vandaag else "Het programma kon niet worden geladen"


# ── feed ────────────────────────────────────────────────────────────────
def lees_feed(tekst: str) -> list[Dag]:
    """Alle dagen uit de feed, de bovenste (vandaag) eerst. Een regel zonder tijd is een dagkop."""
    tekst = html.unescape(_RE_TAG.sub("", tekst))
    dagen: list[Dag] = []
    dag: Dag | None = None
    for regel in re.split(r"\r\n|\r|\n", tekst):
        regel = regel.strip()
        if not regel:
            continue
        m = _RE_FILM.match(regel)
        if m:
            if dag is not None:
                dag.films.append(Film(m.group(1), m.group(2).strip(), m.group(3).strip()))
            continue
        if dag is not None and dag.films:
            dagen.append(dag)
        dag = Dag(regel)
    if dag is not None and dag.films:
        dagen.append(dag)
    return dagen


def _dagen_naar_json(dagen: list[Dag]) -> dict:
    return {"dagen": [{"dag": d.dag, "films": [{"tijd": f.tijd, "titel": f.titel, "zaal": f.zaal} for f in d.films]}
                      for d in dagen]}


def _dagen_uit_json(d: dict) -> list[Dag]:
    out = []
    for dag in d.get("dagen") or []:
        films = [Film(str(f["tijd"]), str(f["titel"]), str(f["zaal"])) for f in dag.get("films") or []]
        if films:
            out.append(Dag(str(dag.get("dag", "")), films))
    return out


def download_feed(url: str = FEED_URL, timeout: float = FEED_TIMEOUT) -> list[Dag]:
    """Feed ophalen en lezen; leeg bij een fout (geen exception)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LAB-1 Lobby speler"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            tekst = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 — netwerk, dns, http, timeout
        _log.warning("programma niet opgehaald: %s", e)
        return []
    return lees_feed(tekst)


def haal_programma(url: str = FEED_URL, cache: Path = CACHE_FILE, *, nu: float | None = None) -> list[Dag]:
    """Programma met cache van een minuut; mislukt het ophalen, dan de laatste versie van vandaag."""
    nu = time.time() if nu is None else nu
    gecached: list[Dag] = []
    mtime = 0.0
    if cache.is_file():
        try:
            mtime = cache.stat().st_mtime
            gecached = _dagen_uit_json(json.loads(cache.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            gecached = []
    if gecached and nu - mtime < CACHE_SECONDS:
        return gecached

    nieuw = download_feed(url)
    if nieuw:
        try:
            cache.write_text(json.dumps(_dagen_naar_json(nieuw), ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            _log.warning("programma-cache niet opgeslagen: %s", e)
        return nieuw

    if gecached and date.fromtimestamp(mtime) == date.fromtimestamp(nu):
        return gecached
    return []


# ── lijst voor het scherm ───────────────────────────────────────────────
def naar_min(tijd: str) -> int:
    return int(tijd[:2]) * 60 + int(tijd[3:5])


def _kort_titel(titel: str) -> str:
    if len(titel) > 32 and ":" in titel:
        titel = titel[: titel.index(":")]
    return _RE_HAAKJES.sub("", titel)       # "(OV)" e.d. eraf


def stel_lijst_samen(dagen: list[Dag], *, modus: str = "highlight", nu: str | None = None,
                     geschiedenis: int = 120, avond_vanaf: str = AVOND_VANAF) -> Lijst:
    """Lijst voor het lobbyscherm (hele programma). `nu` als "HH:MM"; None = huidige tijd."""
    if nu is None:
        nu = datetime.now().strftime("%H:%M")
    modus = modus if modus in MODI else "highlight"
    nu_min = naar_min(nu)
    vandaag = dagen[0] if dagen else Dag("")

    # Voorstellingen van vandaag vanaf N minuten geleden tonen
    vanaf_min = max(0, nu_min - int(geschiedenis))
    vanaf = f"{vanaf_min // 60:02d}:{vanaf_min % 60:02d}"
    if modus == "avond" and avond_vanaf > vanaf:
        vanaf = avond_vanaf
    films = [Film(f.tijd, f.titel, f.zaal) for f in vandaag.films if f.tijd >= vanaf]

    # Laatste film van vandaag te lang geleden: programma van morgen
    volgende_dag = False
    if not films and len(dagen) > 1 and dagen[1].films:
        films = [Film(f.tijd, f.titel, f.zaal) for f in dagen[1].films]
        volgende_dag = True
        if modus == "avond":
            films = [f for f in films if f.tijd >= avond_vanaf]

    titel = "LAB-1 Cinema Today"
    titel_klein = "in LAB-1 Cinema"
    titel_groot = "Tomorrow" if volgende_dag else ("Tonight" if modus == "avond" else None)
    if titel_groot is not None:
        titel = f"{titel_groot} {titel_klein}"

    for f in films:
        f.titel = _kort_titel(f.titel)
        f.zaal = _RE_HAAKJES.sub("", f.zaal)     # "(Boutique)" eraf

    # Highlight: groepen van films die binnen 10 minuten na elkaar beginnen; hl0, hl1, hl2, daarna mat.
    # In modus Avond blijft alles gewoon wit (zoals op de site).
    groep = 0
    groep_start = 0
    for f in films:
        if modus != "highlight":
            break
        if not volgende_dag and f.tijd < nu:
            f.klasse = "mat"                     # al begonnen
            continue
        t = naar_min(f.tijd)
        if groep == 0 or t > groep_start + GROEP_MINUTEN:
            groep += 1
            groep_start = t
        f.klasse = f"hl{groep - 1}" if groep <= 3 else "mat"

    return Lijst(films=films, heeft_vandaag=bool(vandaag.films), titel=titel,
                 titel_groot=titel_groot, titel_klein=titel_klein, volgende_dag=volgende_dag)
