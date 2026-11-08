"""
CinePlayer — videomap inlezen (geen Qt).

Levert relatieve paden (met '/' als scheidingsteken) van alle video's in de map
en haar submappen. Bestanden in de hoofdmap komen eerst, daarna per submap;
binnen een map natuurlijk gesorteerd (film2 vóór film10). Verborgen bestanden
en mappen (beginnend met '.') worden overgeslagen. Submappen worden later
afspeellijsten.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".m4v", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".flv",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".vob", ".3gp",
})

_NUM = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple:
    """Sorteersleutel waarbij cijferreeksen als getal tellen: 'film2' < 'film10'."""
    return tuple(int(part) if part.isdigit() else part.casefold() for part in _NUM.split(text))


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS and not path.name.startswith(".")


def scan_videos(root: Path) -> list[str]:
    """Alle video's onder `root`, als relatieve posix-paden, gesorteerd."""
    root = Path(root)
    if not root.is_dir():
        return []
    found: list[tuple[tuple, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted((d for d in dirnames if not d.startswith(".")), key=natural_key)
        rel_dir = Path(dirpath).relative_to(root)
        parts = () if rel_dir == Path(".") else rel_dir.parts
        folder_key = tuple(natural_key(p) for p in parts)
        for name in filenames:
            p = Path(dirpath) / name
            if is_video(p):
                rel = (rel_dir / name).as_posix() if parts else name
                found.append(((len(parts), folder_key, natural_key(name)), rel))
    found.sort(key=lambda item: item[0])
    return [rel for _, rel in found]


def resolve_video(root: Path, rel: str) -> Path | None:
    """Absoluut pad van een relatieve video, of None als het buiten de map valt of niet bestaat."""
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
        return None
    root = Path(root).resolve()
    full = (root / rel).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        return None
    return full if full.is_file() else None
