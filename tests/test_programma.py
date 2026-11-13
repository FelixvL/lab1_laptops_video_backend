from pathlib import Path

from cineplayer.programma import Dag, Film, haal_programma, lees_feed, stel_lijst_samen

FEED = """
<html><body><pre>
Vrijdag 13 november 2026
10:00\t    Salvation                                  Cinema 1
10:10\t    Bitter Christmas (Amarga Navidad)          Cinema 3
17:45\t    Naza                                       Cinema 2 (Boutique)
18:50\t    Wild Horse Nine                            Cinema 1
18:55\t    I Love Boosters                            Cinema 4
19:15\t    Primetime                                  Cinema 3
21:15\t    LAB-1 HALLOWEEN MOVIE NIGHT: Godzilla Minus Zero (OV)   Cinema 1
Zaterdag 14 november 2026
11:00\t    Akira                                      Cinema 3
19:00\t    The Cycle of Love                          Cinema 5
</pre></body></html>
"""


def test_feed_wordt_in_dagen_en_films_geknipt() -> None:
    dagen = lees_feed(FEED)
    assert [d.dag for d in dagen] == ["Vrijdag 13 november 2026", "Zaterdag 14 november 2026"]
    assert len(dagen[0].films) == 7 and len(dagen[1].films) == 2
    f = dagen[0].films[2]
    assert (f.tijd, f.titel, f.zaal) == ("17:45", "Naza", "Cinema 2 (Boutique)")


def test_lijst_overdag_met_geschiedenis_en_highlightgroepen() -> None:
    lijst = stel_lijst_samen(lees_feed(FEED), nu="18:00", geschiedenis=120)
    assert lijst.titel == "LAB-1 Cinema Today" and lijst.titel_groot is None
    assert [f.tijd for f in lijst.films] == ["17:45", "18:50", "18:55", "19:15", "21:15"]
    # 17:45 is al begonnen (mat); 18:50+18:55 vormen één groep (binnen 10 min); 19:15 tweede; 21:15 derde
    assert [f.klasse for f in lijst.films] == ["mat", "hl0", "hl0", "hl1", "hl2"]
    assert lijst.films[0].zaal == "Cinema 2"                       # "(Boutique)" eraf
    assert lijst.films[-1].titel == "LAB-1 HALLOWEEN MOVIE NIGHT"  # ingekort op de dubbele punt


def test_vierde_groep_en_verder_is_mat() -> None:
    dagen = [Dag("x", [Film("10:00", "A", "Cinema 1"), Film("11:00", "B", "Cinema 1"),
                       Film("12:00", "C", "Cinema 1"), Film("13:00", "D", "Cinema 1")])]
    assert [f.klasse for f in stel_lijst_samen(dagen, nu="09:00").films] == ["hl0", "hl1", "hl2", "mat"]


def test_modus_avond_toont_vanaf_18_uur_met_titel_tonight() -> None:
    lijst = stel_lijst_samen(lees_feed(FEED), modus="avond", nu="12:00")
    assert lijst.titel_groot == "Tonight" and lijst.titel == "Tonight in LAB-1 Cinema"
    assert [f.tijd for f in lijst.films] == ["18:50", "18:55", "19:15", "21:15"]
    assert all(f.klasse == "" for f in lijst.films)              # Avond: geen tinten, alles wit


def test_laat_op_de_avond_programma_van_morgen() -> None:
    lijst = stel_lijst_samen(lees_feed(FEED), nu="23:59", geschiedenis=120)
    assert lijst.volgende_dag and lijst.titel_groot == "Tomorrow"
    assert [f.tijd for f in lijst.films] == ["11:00", "19:00"]
    assert [f.klasse for f in lijst.films] == ["hl0", "hl1"]        # morgen: niets 'al begonnen'
    avond = stel_lijst_samen(lees_feed(FEED), modus="avond", nu="23:59")
    assert [f.tijd for f in avond.films] == ["19:00"]


def test_meldingen_bij_lege_lijst() -> None:
    alleen_vandaag = [Dag("x", [Film("10:00", "A", "Cinema 1")])]
    assert stel_lijst_samen(alleen_vandaag, nu="23:00").melding == "Geen voorstellingen meer vandaag"
    assert stel_lijst_samen([], nu="12:00").melding == "Het programma kon niet worden geladen"


def test_haal_programma_gebruikt_cache_bij_storing(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache.json"
    monkeypatch.setattr("cineplayer.programma.download_feed", lambda url: lees_feed(FEED))
    dagen = haal_programma("http://x", cache)
    assert len(dagen) == 2 and cache.is_file()
    # cache op vandaag 12:00 zetten, zodat de test ook vlak voor middernacht klopt
    import os
    from datetime import date, datetime, time as dtime
    noon = datetime.combine(date.today(), dtime(12)).timestamp()
    os.utime(cache, (noon, noon))
    # feed valt weg: binnen de minuut uit de cache, en daarna nog steeds (zelfde dag)
    monkeypatch.setattr("cineplayer.programma.download_feed", lambda url: [])
    assert len(haal_programma("http://x", cache, nu=noon + 30)) == 2
    assert len(haal_programma("http://x", cache, nu=noon + 600)) == 2
    # een dag later is een oude cache niets meer waard
    assert haal_programma("http://x", cache, nu=noon + 86400 * 2) == []
