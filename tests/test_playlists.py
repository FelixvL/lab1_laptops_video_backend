from pathlib import Path

from cineplayer.playlists import (Playlist, clean_name, delete_playlist, import_playlist, load_playlists,
                                  parse_playlist_text, read_playlist_file)

MPCPL = """MPCPLAYLIST
audio,0
subtitles,-1
1,type,0
1,label,Eerste
1,filename,D:\\Films\\08 - Frigid Hare.avi
2,type,0
2,filename,D:\\Films\\Kort, met komma.mp4
10,type,0
10,filename,relatief\\derde.mkv
"""


def test_mpcpl_in_volgorde_van_het_nummer_en_met_komma_in_de_naam() -> None:
    assert parse_playlist_text(MPCPL, ".mpcpl") == [
        "D:\\Films\\08 - Frigid Hare.avi", "D:\\Films\\Kort, met komma.mp4", "relatief\\derde.mkv"]


def test_m3u_en_pls() -> None:
    m3u = "#EXTM3U\n#EXTINF:123,Titel\nC:\\a\\een.mp4\n\n../twee.avi\nhttp://voorbeeld.nl/stream.m3u8\n"
    assert parse_playlist_text(m3u, ".m3u8") == ["C:\\a\\een.mp4", "../twee.avi", "http://voorbeeld.nl/stream.m3u8"]
    pls = "[playlist]\nFile2=b.mp4\nTitle2=B\nFile1=a.mp4\nNumberOfEntries=2\nVersion=2\n"
    assert parse_playlist_text(pls, ".pls") == ["a.mp4", "b.mp4"]


def test_relatieve_paden_tov_het_bestand_en_internetadressen_overgeslagen(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "een.mp4").write_bytes(b"x")
    src = tmp_path / "lijst.m3u"
    src.write_text("sub/een.mp4\nhttp://voorbeeld.nl/x.mp4\n", encoding="utf-8")
    assert read_playlist_file(src) == [str((tmp_path / "sub" / "een.mp4").resolve())]


def test_import_bewaren_laden_verwijderen(tmp_path: Path) -> None:
    src = tmp_path / "Avond programma.mpcpl"
    src.write_text(MPCPL, encoding="utf-8-sig")
    store = tmp_path / "playlists"
    pl = import_playlist(src, store)
    assert pl.name == "Avond programma" and len(pl.items) == 3
    geladen = load_playlists(store)
    assert [p.name for p in geladen] == ["Avond programma"] and geladen[0].items == pl.items
    assert delete_playlist("Avond programma", store) and load_playlists(store) == []


def test_ontbrekende_film_wordt_op_naam_in_de_videomap_gezocht(tmp_path: Path) -> None:
    videos = tmp_path / "videos"
    (videos / "cartoons").mkdir(parents=True)
    (videos / "cartoons" / "08 - Frigid Hare.avi").write_bytes(b"x")
    pl = Playlist("x", ["D:\\Films\\08 - FRIGID HARE.avi", "D:\\Films\\bestaat niet.mp4"])
    assert pl.resolve(videos) == [videos / "cartoons" / "08 - Frigid Hare.avi", None]
    assert pl.summary(videos) == {"name": "x", "count": 1, "missing": 1}


def test_lege_lijst_geeft_een_fout(tmp_path: Path) -> None:
    src = tmp_path / "leeg.m3u"
    src.write_text("#EXTM3U\n", encoding="utf-8")
    try:
        import_playlist(src, tmp_path / "pl")
    except ValueError as e:
        assert "Geen films" in str(e)
    else:
        raise AssertionError("ValueError verwacht")
    assert clean_name("  a/b:c*?  ") == "abc"
