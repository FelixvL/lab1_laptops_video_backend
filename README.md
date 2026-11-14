# Kiosk-speler (Python)

```bash
pip install -r requirements.txt
python player.py              # fullscreen kiosk
python player.py --windowed   # testen in een venster
python -m pytest tests -q     # tests
```

Vereist `libmpv-2.dll` in `C:\mpv\` of naast `player.py`. Instellingen in `settings.json`
(kopieer `settings.example.json`; `settings.json` zelf staat in `.gitignore` vanwege de
API-sleutel, net als `state.json`, `player.log` en de video's).

```
player.py              entry point: PATH voor libmpv, logging, QApplication
cineplayer/
  config.py            settings.json (jij) en state.json (laatste opdracht, voor herstart)
  library.py           videomap inlezen, natuurlijk gesorteerd, submappen gegroepeerd
  remote.py            poller: status posten, opdracht ontvangen (QThread, urllib)
  controller.py        libmpv: koppelen aan venster, film in herhaling of speellijst, positie, springen
  playlists.py         speellijsten importeren (.mpcpl, .m3u, .m3u8, .pls), bewaren, films opzoeken
  programma.py         filmprogramma van lab-1.nl/list2: feed lezen, lijst + highlight zoals de site
  overlay.py           tekstlaag tekenen (Bebas Neue, maten uit de CSS van de site) als QImage
  tekstlaag.py         tekstlaag bijhouden: ophalen per minuut, hertekenen, in mpv zetten
  main_window.py       kioskvenster: rustscherm / video, opdrachten toepassen
  settings_window.py   klein instellingenvenster na het kruisje: videomap, paneel, speellijsten
fonts/                 Bebas Neue en Unibody 8 Pro (uit de lobby-website)
playlists/             geïmporteerde speellijsten (per laptop, niet in git)
tools/dev_server.py    lokale nabootsing van php/api.php om zonder hosting te testen
tests/                 pytest voor library, config en de verbinding
videos/                de video's (twee testvideo's meegeleverd)
```

Beweeg de muis en klik op het kruisje rechtsboven (verdwijnt na 3 s): de speler stopt en er
blijft een klein instellingenvenster over (videomap, paneelgegevens, speellijsten importeren,
speler weer starten). Ctrl+Q, of dat venster sluiten, sluit het programma af.
Logboek: `player.log`. Uitleg voor Claude Code en de hele voorgeschiedenis: `CLAUDE.md`.
