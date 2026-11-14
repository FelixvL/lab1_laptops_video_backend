# LAB-1 Lobby — instructies voor Claude Code

Dit bestand reist mee met de repository, zodat een Claude op een andere computer na een clone
meteen weet wat de bedoeling is, wat er besproken en besloten is, en wat er nog ligt.
Houd het bij: zie "Dit bestand bijhouden" onderaan. Laatst bijgewerkt: 14 november 2026.

## 1. Wat dit is

Filmtheater LAB-1 heeft weggewerkte laptops die elk een scherm aansturen (lobby, hoeken zoals
NINTENDO-CORNER en SCENE-CORNER). Ze speelden films met Media Player Classic en iemand moest
steeds naar de laptop lopen. Dat "steeds de laptop pakken" moet weg.

Deze repository is de **speler**: een Windows-programma (Python, PyQt6, libmpv) dat fullscreen
films uit een lokale map speelt en zich op afstand laat sturen via een **PHP-beheerpaneel** op
internet. Over de film kan de speler het **filmprogramma van LAB-1** tekenen, in de opmaak van
de lobby-website.

```
 laptop (deze repo, player.py)                   webhosting 0111.nl/lab-1/laptops/ (PHP)
 ┌──────────────────────────┐   elke 3 s POST     ┌──────────────────────────────┐
 │ videomap + speellijsten  │ ──status (met id)─▶ │ api.php                      │
 │ mpv  +  tekstlaag        │ ◀──opdracht──────── │   data/players/<id>/*.json   │
 │ instellingenvenster      │                     │ index.php ◀── beheerder      │
 └──────────────────────────┘                     └──────────────────────────────┘
        ▲ leest zelf elke minuut https://www.lab-1.nl/list2 (filmprogramma)
```

- De laptop opent geen poort. Hij vraagt elke 3 seconden om zijn opdracht en meldt zijn status.
- Het paneel is één webpagina voor alle laptops: links kies je de laptop, rechts stuur je hem aan.
- **De PHP-bron van het paneel zit niet in deze repository.** Die staat op de pc van de eigenaar
  (`C:\_CLAUDE_CODE\LAB1VIDEOAPP\php`, een losse git-map zonder remote) en op de hosting. Op een
  andere laptop kun je het paneel dus niet aanpassen; gebruik dan het contract in hoofdstuk 4 en
  `tools/dev_server.py`, dat `api.php` nabootst. Moet het paneel veranderen, zeg dat dan tegen de
  eigenaar: dat gebeurt op zijn pc.

## 2. Zo werk je met de eigenaar (Felix)

- **Taal: Nederlands.** Kort en concreet. Hij is geen fulltime programmeur; leg uit wat iets doet,
  niet hoe de code heet.
- **Hij commit en pusht zelf.** Nooit committen of pushen tenzij hij het uitdrukkelijk vraagt.
- **Hij zet PHP-bestanden zelf op de hosting** (FileZilla) en zegt dan "Gedaan". Dat gaat vaak
  half goed. Controleer daarna altijd zelf met `curl` of de nieuwe versie er echt staat, en zeg
  precies welk bestand achterloopt.
- **Het echte paneel is productie.** Lezen mag (inloggen, `action=players`, `action=status`).
  Stuur er zelf geen opdrachten naar toe; vraag hem te klikken en controleer daarna de status.
- **Hij start de speler vaak zelf vanuit cmd.** Beëindig nooit een speler die jij niet zelf hebt
  gestart. Vraag hem die te sluiten. Start testspelers altijd met een eigen `--settings`-bestand
  en een eigen `player_id`, en ruim ze daarna op.
- **Zet zijn bestanden terug.** `state.json`, `settings.json`, `playlists/` en
  `programma-cache.json` zijn van de laptop. Maak een kopie voor je ermee test en herstel die.
- **De map `NIET_AANRAKEN`** (alleen op zijn pc) is een kopie van de lobby-website: `index.php`,
  `beheer.php`, lettertypen. Alleen lezen. Dat is de bron van de waarheid voor opmaak en gedrag
  van de tekstlaag.
- **Hij wil het echte resultaat zien, geen omweg.** Een oplossing die vraagt om iets aan de
  lobby-website te veranderen of bestanden om te zetten, wijst hij af. Vraag bij twijfel eerst.
- **Meld eerlijk wat niet getest is.** Hij test zelf op de echte laptops en stuurt foto's.
- Geheimen: het paneelwachtwoord en de API-sleutel staan in `php/config.php` (zijn pc) en in
  `settings.json` (per laptop, niet in git). Zet ze nooit in een bestand dat wel in git komt.

## 3. Wat de speler nu kan

- Eén film in oneindige herhaling, of een **speellijst**: films achter elkaar, daarna opnieuw.
- Per film en per speellijst onthouden waar hij was; verder na wisselen en na een herstart.
- Vanaf het paneel: film of speellijst kiezen, Vorige, Volgende, +30 s, videomap opnieuw lezen.
- **Tekstlaag**: aan/uit, modus (aanstaande film fel, avond, alles even wit), filmgeschiedenis in
  minuten, en uitlijnen van Geheel, Titel en Lijst. De speler stuurt een miniatuur terug.
- **Meerdere laptops**: elke speler meldt zich met een naam (`player_id`, standaard de hostnaam).
- **Kruisje** (verschijnt drie seconden bij muisbeweging): stopt de speler en toont een klein
  **instellingenvenster** met videomap, paneelgegevens, import van speellijsten
  (`.mpcpl` van Media Player Classic, `.m3u`, `.m3u8`, `.pls`) en "Start speler". Ctrl+Q, of dat
  venster sluiten, sluit het programma echt af.
- Start niet vanzelf bij het opstarten van Windows. Dat is zo gevraagd.

## 4. Contract tussen speler en paneel

Alles is JSON. De server bewaart per speler `data/players/<id>/command.json`, `status.json` en
`preview.jpg`. Geen database.

**Speler naar server**, elke `poll_seconds`:
`POST api.php?action=poll`, header `X-Api-Key`, body = de status. Antwoord: `{ok, command, server_time}`.

Status (zie `KioskWindow.status_snapshot`):

| veld | betekenis |
|---|---|
| `id` | naam van de speler |
| `video` | film die nu speelt (pad binnen de videomap), of null |
| `playlist`, `playlist_index`, `playlist_count` | speellijst die speelt en de hoeveelste film |
| `playlists` | lijst `{name, count, missing}` van de speellijsten op deze laptop |
| `videos` | alle films in de videomap, relatieve paden met `/` |
| `position`, `duration`, `paused` | voortgang in seconden |
| `overlay` | stand van de tekstlaag, plus `programma` (ok/fout/laden), `films`, `titel`, `zichtbaar`, `rects`, `preview_at`, en soms `preview` (JPEG in base64) |
| `settings_open` | true als het instellingenvenster open staat |
| `error` | foutmelding voor het paneel, of leeg |
| `hostname`, `version`, `videos_dir`, `seq_applied` | ter informatie |

Opdracht (zie `KioskWindow.apply_command`):

```json
{
  "seq": 41,
  "video": "map/film.mp4",
  "playlist": null,
  "skip": {"n": 0, "delta": 0},
  "seek": {"n": 3, "delta": 30},
  "rescan": 0,
  "overlay": {
    "aan": true, "modus": "highlight", "geschiedenis": 30,
    "uitlijning": {"geheel": {"x": 0, "y": 7, "s": 95}, "titel": {"x": 0, "y": 3, "s": 60}, "lijst": {"x": 1, "y": 0, "s": 55}}
  }
}
```

- `seq` gaat omhoog bij elke klik op het paneel. Een nieuwe `seq` met dezelfde film laadt die
  film opnieuw (hij hervat waar hij was). Uitzondering: een klik die alleen tekstlaag, `seek`,
  `skip` of `rescan` raakte, herstart niets. Dat heet in de code `bijzaak`.
- `video` en `playlist` sluiten elkaar uit; `playlist` wint.
- `seek`, `skip` en `rescan` zijn tellers: de speler handelt als `n` (of het getal) verandert.
  Bij het eerste contact na een start (`state.seq`, `seek_n`, `skip_n` zijn dan -1) neemt hij de
  tellers alleen over, zodat een oude klik niet opnieuw wordt uitgevoerd.
- `modus`: `highlight`, `avond` of `gewoon`. `uitlijning`: x en y in procent van het scherm
  (-60..60), s is schaal in procent (20..400). De standaard is de stand van de lobby-website.

**Paneel naar server** (ingelogde sessie plus CSRF-token, dus niet voor de speler):
`GET api.php?action=players`, `GET api.php?action=status&player=<id>`,
`POST api.php?action=command` met `{csrf, player, cmd, …}` waarbij `cmd` is
`play | playlist | next | prev | seek | rescan | overlay | uitlijnen`, en `GET preview.php?player=<id>`.
`player: "all"` werkt al voor `overlay` en `uitlijnen`; er is nog geen knop voor.

## 5. Bestanden

```
player.py                entry point: PATH voor libmpv, logging, QApplication, KioskWindow
cineplayer/
  config.py              settings.json en state.json, parse_overlay, uitlijning, update_settings
  library.py             videomap inlezen, natuurlijk gesorteerd; resolve_video (geen pad-ontsnapping)
  remote.py              post_status + Poller (QThread) naar api.php
  controller.py          libmpv: play, play_list, step, seek, hervatten, set_overlay
  programma.py           feed lab-1.nl/list2 lezen; lijst, titel en highlight zoals index.php van de site
  overlay.py             tekstlaag tekenen als QImage: Bebas Neue, maten uit de CSS van de site
  tekstlaag.py           tekstlaag bijhouden: feed per minuut, hertekenen, miniatuur, in mpv zetten
  playlists.py           speellijstbestanden lezen, bewaren in playlists/, films opzoeken
  main_window.py         KioskWindow: alles aan elkaar; opdrachten toepassen; status
  settings_window.py     het kleine instellingenvenster na het kruisje
fonts/                   Bebas Neue en Unibody 8 Pro (van de lobby-website)
tools/dev_server.py      bootst api.php na op poort 8765, zonder login, meerdere spelers
tests/                   pytest: library, config, verbinding, programma-logica, speellijsten
```

Niet in git, per laptop: `settings.json` (kopieer `settings.example.json`), `state.json`,
`player.log`, `programma-cache.json`, `playlists/`, de inhoud van `videos/`, `libmpv-2.dll`.

## 6. Een laptop inrichten

1. Python 3.11 of nieuwer, `pip install -r requirements.txt`.
2. `libmpv-2.dll` in `C:\mpv\` of naast `player.py`
   (https://sourceforge.net/projects/mpv-player-windows/files/libmpv/).
3. `copy settings.example.json settings.json` en de API-sleutel invullen. Dat kan ook in het
   instellingenvenster: start de speler, klik het kruisje, vul Paneel in.
4. `python player.py`. De laptop verschijnt links op het paneel onder zijn hostnaam, of onder
   `player_id` als die is ingevuld. Een nieuwe laptop start zonder film; kies er een op het paneel.
5. Bijwerken later: `git pull` en de speler herstarten.

Bekende spelers: `lp` (ontwikkel-pc van de eigenaar), `NINTENDO-CORNER`, `SCENE-CORNER`.
Op de LAB-1-laptops staat de clone in `C:\Users\LAB-1\Desktop\video_player\lab1_laptops_video_backend`.

## 7. Testen

```bash
python -m pytest tests -q
python tools/dev_server.py
python player.py --windowed --settings <eigen testbestand.json>
```

- Zet in het testbestand `"server_url": "http://127.0.0.1:8765/api.php"`, een eigen `player_id`
  en `"poll_seconds": 1`. Opdrachten geef je met curl; voorbeelden staan boven in `dev_server.py`.
- `--windowed` geeft een gewoon venster in plaats van fullscreen.
- Kijk wat er echt op het scherm staat: maak een schermafbeelding en bekijk die. De status zegt
  alleen wat de speler denkt te doen.
- Een proef met de vensters kan zonder klikken: maak een los script dat `KioskWindow` start en
  met `QTimer.singleShot` `open_settings()`, `settings_win.import_files(...)` en
  `resume_player()` aanroept.
- Er zijn twee testclips van zes seconden in `videos/`. Voor hervatten en springen heb je langere
  clips nodig; maak die met ffmpeg (`testsrc2`, 90 seconden).

## 8. Besluiten en waarom

Deze dingen zijn uitgeprobeerd of besproken. Stel ze niet opnieuw voor zonder nieuwe reden.

- **Geen database, geen open poort.** JSON-bestanden en polling om de 3 seconden zijn genoeg.
- **Geen website-modus meer.** Eerst toonde de speler de lobby-website in een ingebouwde browser
  (QtWebEngine). Die kan geen MP4/H.264 afspelen, dus de achtergrondvideo ontbrak. Een omweg met
  WebM-bestanden wees de eigenaar af. Daarna startte de speler Microsoft Edge in kioskstand. Ook
  dat is op zijn verzoek helemaal gesloopt. Nu tekent de speler de tekst zelf over de film.
- **De tekstlaag is nagetekend van de lobby-website**, niet verzonnen: regels uit `index.php`
  (geschiedenis, programma van morgen als "Tomorrow", modus Avond als "Tonight" vanaf 18:00,
  groepen van tien minuten met tinten 100/45/33/24 procent, lange titels inkorten) en maten uit
  de CSS. De uitlijning komt uit het beheer van die site. Wijzig gedrag alleen als de site het
  ook zo doet of als de eigenaar erom vraagt.
- **Wel overgenomen van `beheer.php`:** modussen, filmgeschiedenis, uitlijnen. **Niet:** de
  effecten (filmkorrel, tv-ruis, Halloween: "hoeven nog niet"), de videokeuze van de site, en de
  iPads bij de zalen ("hoeven we niet in ons beheer").
- **Android-tablets** draaien deze speler niet (PyQt6 en libmpv). De eigenaar regelt tablets zelf
  met PHP en Chrome. Niet aan beginnen tenzij hij het vraagt.
- **Voortgang hoeft niet nauwkeurig.** Elke 15 seconden bewaren is goed.
- **Speellijsten komen van de laptop zelf**, via import in het instellingenvenster. Het oude idee
  "een submap is een speellijst" is niet gebouwd.
- **Een nieuwe laptop erft alleen de tekstlaag-instellingen**, niet de film van een andere laptop.

## 9. Valkuilen in de techniek

- mpv tekent in een eigen native venster en verbergt Qt-widgets die erboven liggen. Daarom is het
  kruisje een los venstertje zonder ouder, en zit de tekst **in mpv zelf** (`overlay-add`, BGRA,
  premultiplied). De `QImage` moet blijven leven zolang mpv hem gebruikt; `PlayerController`
  houdt hem vast en zet hem opnieuw na elke `play`.
- De tekstlaag wordt getekend in **fysieke pixels** van het videovenster (let op schermschaal).
- Een losse film: `loop-file=inf`. Een speellijst: mpv's eigen lijst met `loop-playlist=inf`.
  De lijst wordt gedraaid aangeboden (de film waar hij was eerst), zodat mpv meteen de goede
  laadt; `list_index()` rekent terug naar de echte volgorde.
- Hervatten: laden op pauze, wachten tot de duur bekend is, dan springen. Springen met +30 s
  gebruikt `relative+exact`; zonder `exact` springt mpv naar een sleutelbeeld en dat scheelt
  seconden of schiet over het einde.
- `rescan()` start zelf een film als er nog niets speelt. De vlag `_ready` voorkomt dat dit
  tegelijk gebeurt met een start elders; anders laadt de film twee keer en logt mpv een fout.
- Het kruisje sluit niet af maar roept `open_settings()` aan. Zolang dat venster open is, voert
  `_apply_state` niets uit; opdrachten worden wel onthouden. `QApplication` staat op
  `setQuitOnLastWindowClosed(False)`; echt afsluiten gaat via `quit_app()`.
- `Poller.stop()` wacht op een lopend verzoek, tot een paar seconden. Het venster hangt dan even.
- De programma-cache geldt alleen op de dag zelf. Vlak na middernacht zonder verbinding is er dus
  geen programma (zie open punt 1).
- In `.gitignore` moet commentaar op een eigen regel staan, niet achter een patroon.
- In de Windows-console worden gedachtestreepjes uit het logboek als een vraagteken getoond. Het
  bestand `player.log` is wel goed (UTF-8).
- Git Bash op Windows: een heredoc met een apostrof in de tekst breekt. Schrijf een script naar
  een bestand en voer dat uit.

## 10. Wat er nog ligt

1. **Netwerkstoring netter opvangen. Voorgesteld, nog niet akkoord.** Op een laptop verscheen
   `programma niet opgehaald: handshake operation timed out`. Nagebootst: de speler overleeft dat,
   maar het publiek ziet dan "Het programma kon niet worden geladen" over de film. Voorstel:
   time-out van de feed van 3 naar 10 seconden en na een mislukking elke 10 seconden opnieuw;
   zonder programma geen tekst tonen in plaats van een foutregel; een crashlogboek
   (`faulthandler` en `sys.excepthook` naar `player.log`).
2. **Onverklaarde stop.** Op diezelfde laptop stond na die waarschuwing meteen de prompt terug,
   zonder de logregel "afgesloten". Niet te reproduceren. Vraag om `player.log` van die laptop en
   of hij zelf is gesloten.
3. **Echte speellijst van Media Player Classic nog niet gezien.** De import is getest met zelf
   gemaakte bestanden. Vraag om één echt `.mpcpl`-bestand en controleer het formaat.
4. **Knop "tekst voor alle schermen tegelijk"** op het paneel. De API kan het al.
5. **Effecten van de lobby-website** op de tekstlaag. Uitgesteld door de eigenaar.
6. **Terugvallen op de eerste film** als de gekozen film ontbreekt. Aangeboden, niet gevraagd.
7. **Hosting bijwerken.** Na 14 november zijn `lib.php`, `api.php` en `index.php` gewijzigd
   (speellijsten, nieuwe laptop erft geen film). Controleer met curl of ze op de hosting staan:
   de opdracht moet de velden `playlist` en `skip` bevatten.

## 11. Dit bestand bijhouden

Werk dit bestand bij in dezelfde beurt als de wijziging: nieuw gedrag in hoofdstuk 3, een nieuw
veld in hoofdstuk 4, een besluit van de eigenaar in hoofdstuk 8 met de reden erbij, een opgelost
punt weg uit hoofdstuk 10. Schrijf op wat een ander niet uit de code kan halen: wat de eigenaar
wilde, wat is afgewezen en waarom. Zet er geen wachtwoorden of sleutels in.
