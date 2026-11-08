# Kiosk-speler (Python)

```bash
pip install -r requirements.txt
python player.py              # fullscreen kiosk
python player.py --windowed   # testen in een venster
python -m pytest tests -q     # tests
```

Vereist `libmpv-2.dll` in `C:\mpv\` of naast `player.py`, en Microsoft Edge (of Chrome) voor de
website-modus. Instellingen in `settings.json` (kopieer `settings.example.json`; `settings.json`
zelf staat in `.gitignore` vanwege de API-sleutel, net als `state.json`, `player.log`,
`browser_profile/` en de video's).

```
player.py              entry point: PATH voor libmpv, logging, QApplication
cineplayer/
  config.py            settings.json (jij) en state.json (laatste opdracht, voor herstart)
  library.py           videomap inlezen, natuurlijk gesorteerd, submappen gegroepeerd
  remote.py            poller: status posten, opdracht ontvangen (QThread, urllib)
  controller.py        libmpv: koppelen aan venster, afspelen in herhaling, positie
  browser.py           website-modus: Edge/Chrome in kioskstand starten, bewaken, sluiten
  main_window.py       kioskvenster: rustscherm / video, browser ervoor zetten, opdrachten toepassen
tools/dev_server.py    lokale nabootsing van php/api.php om zonder hosting te testen
tests/                 pytest voor library, config en de verbinding
videos/                de video's (twee testvideo's meegeleverd)
browser_profile/       eigen Edge-profiel van de speler (maakt hij zelf aan; mag je weggooien)
```

Afsluiten: beweeg de muis en klik op het kruisje rechtsboven (verdwijnt na 3 s), of Ctrl+Q.
Logboek: `player.log`.
