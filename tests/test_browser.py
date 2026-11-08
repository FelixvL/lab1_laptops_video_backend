from pathlib import Path

from cineplayer.browser import browser_args, find_browser, is_edge


def test_edge_kiosk_args_end_with_url_and_use_own_profile(tmp_path: Path) -> None:
    args = browser_args(r"C:\x\msedge.exe", "https://0111.nl/lab-1/lobby-video/", tmp_path / "prof")
    assert args[0] == r"C:\x\msedge.exe"
    assert args[-1] == "https://0111.nl/lab-1/lobby-video/"
    assert f"--user-data-dir={tmp_path / 'prof'}" in args
    assert "--kiosk" in args
    assert "--edge-kiosk-type=fullscreen" in args
    assert "--kiosk-idle-timeout-minutes=0" in args
    assert "--autoplay-policy=no-user-gesture-required" in args


def test_chrome_has_no_edge_only_flags(tmp_path: Path) -> None:
    args = browser_args(r"C:\x\chrome.exe", "http://a/", tmp_path)
    assert "--kiosk" in args
    assert not any(a.startswith("--edge-kiosk-type") for a in args)
    assert not is_edge(r"C:\x\chrome.exe") and is_edge(r"C:\Edge\MSEDGE.EXE")


def test_windowed_uses_app_window(tmp_path: Path) -> None:
    args = browser_args(r"C:\x\msedge.exe", "http://a/", tmp_path, kiosk=False, size=(800, 600))
    assert "--kiosk" not in args
    assert "--app=http://a/" in args
    assert "--window-size=800,600" in args


def test_find_browser_prefers_existing_setting(tmp_path: Path) -> None:
    fake = tmp_path / "mybrowser.exe"
    fake.write_bytes(b"")
    assert find_browser(str(fake)) == str(fake)
    # een niet-bestaand pad wordt genegeerd (geen exception), resultaat is None of een echte browser
    found = find_browser(str(tmp_path / "bestaat-niet.exe"))
    assert found is None or Path(found).is_file()
