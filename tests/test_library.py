from pathlib import Path

from cineplayer.library import natural_key, resolve_video, scan_videos


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_scan_sorts_root_first_then_folders_naturally(tmp_path: Path) -> None:
    _touch(tmp_path / "film10.mp4")
    _touch(tmp_path / "film2.mp4")
    _touch(tmp_path / "notities.txt")
    _touch(tmp_path / ".verborgen.mp4")
    _touch(tmp_path / "zomer" / "b.mkv")
    _touch(tmp_path / "zomer" / "a.MOV")
    _touch(tmp_path / "Herfst" / "clip.webm")
    _touch(tmp_path / ".cache" / "x.mp4")

    assert scan_videos(tmp_path) == [
        "film2.mp4", "film10.mp4",
        "Herfst/clip.webm",
        "zomer/a.MOV", "zomer/b.mkv",
    ]


def test_scan_missing_folder_is_empty(tmp_path: Path) -> None:
    assert scan_videos(tmp_path / "bestaat-niet") == []


def test_natural_key() -> None:
    assert sorted(["b10", "B2", "a1"], key=natural_key) == ["a1", "B2", "b10"]


def test_resolve_video_stays_inside_root(tmp_path: Path) -> None:
    _touch(tmp_path / "map" / "v.mp4")
    _touch(tmp_path.parent / "buiten.mp4")
    assert resolve_video(tmp_path, "map/v.mp4") == (tmp_path / "map" / "v.mp4").resolve()
    assert resolve_video(tmp_path, "map/ontbreekt.mp4") is None
    assert resolve_video(tmp_path, "../buiten.mp4") is None
    assert resolve_video(tmp_path, "") is None
