"""
CinePlayer — de tekstlaag over de video, getekend met Qt.

Zelfde opmaak als de lobby-website (index.php): donkere laag van 50 %, titel in
Bebas Neue Bold met witte onderstreep, daaronder de tabel tijd / titel / zaal met
letterschaduw en de highlight-tinten. Alle maten volgen de CSS van de site, met
`vw`/`vh` = 1 % van de schermbreedte/-hoogte en `rem` = 16 px:

    --basis   = clamp(2rem, 6vw, 5.5rem)        titel = 1,61 × basis, rijen = basis
    main      padding 5vh 6vw                   .lijst margin-top 4.5vh
    h1        line-height 1, padding-bottom .08em, border-bottom .06em
    table     letter-spacing .027em, line-height .98
    td.tijd   padding-right 1.95em   td.titel padding-right .6em   td.zaal padding-left 1.4em
    schaduw   .07em 0 .11em rgba(0,0,0,.68) + .07em 0 .2em rgba(0,0,0,.4)
    tinten    hl0 #fff · hl1 45 % · hl2 33 % · mat 24 % (zonder schaduw)

Daarbovenop de uitlijning uit het beheer van de site (config.json → uitlijning.lobby):
per element een verschuiving x (vw), y (vh) en schaal s (%), voor `geheel` (om het
midden van het tekstvlak), `titel` (lettergrootte + verschuiving) en `lijst`
(lettergrootte + verschuiving). `DEFAULT_UITLIJNING` is de stand van de lobby
op 13 november 2026.

`render_overlay(lijst, w, h)` geeft een QImage (ARGB32 premultiplied, dus BGRA in
geheugen) die één op één als mpv-overlay over het beeld kan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

from .config import DEFAULT_UITLIJNING, uitlijning_van  # noqa: F401 — ook voor oude importeurs
from .programma import Lijst

_log = logging.getLogger("cineplayer.overlay")

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
REM = 16.0
_family: str | None = None


def font_family() -> str:
    """Bebas Neue uit fonts/ laden (één keer); valt terug op Impact als de bestanden ontbreken."""
    global _family
    if _family is not None:
        return _family
    for name in ("BebasNeue-Bold.ttf", "BebasNeue-Regular.ttf"):
        p = FONTS_DIR / name
        if p.is_file():
            fid = QFontDatabase.addApplicationFont(str(p))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams and _family is None:
                _family = fams[0]
    if _family is None:
        _log.warning("Bebas Neue niet gevonden in %s; Impact als vervanger", FONTS_DIR)
        _family = "Impact"
    return _family


@dataclass
class _Tekst:
    """Eén stuk tekst op het beeld: positie van de baseline, lettertype, kleur, schaduw ja/nee."""
    x: float
    baseline: float
    text: str
    font: QFont
    color: QColor
    shadow: bool


def _font(px: float, letter_spacing_em: float = 0.0) -> QFont:
    f = QFont(font_family())
    f.setPixelSize(max(1, round(px)))
    f.setWeight(QFont.Weight.Bold)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing_em * px)
    return f


def _baseline(top: float, line_height: float, fm: QFontMetricsF) -> float:
    """CSS: regelbox `line_height` hoog, glyphs gecentreerd met half-leading; baseline = top + leading/2 + ascent."""
    return top + (line_height - (fm.ascent() + fm.descent())) / 2 + fm.ascent()


def _wrap(text: str, fm: QFontMetricsF, width: float) -> list[str]:
    """Woorden afbreken zoals white-space: normal (een enkel te lang woord blijft staan)."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        probe = f"{cur} {w}" if cur else w
        if cur and fm.horizontalAdvance(probe) > width:
            lines.append(cur)
            cur = w
        else:
            cur = probe
    if cur:
        lines.append(cur)
    return lines or [""]


def _blurred(src: QImage, radius: float) -> QImage:
    """Gaussisch vervagen via QGraphicsBlurEffect (offscreen)."""
    if radius <= 0.5:
        return src
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(src))
    eff = QGraphicsBlurEffect()
    eff.setBlurRadius(radius)
    eff.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(eff)
    scene.addItem(item)
    out = QImage(src.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    scene.render(p, QRectF(out.rect()), QRectF(src.rect()))
    p.end()
    return out


def _draw_shadow(canvas: QImage, teksten: list[_Tekst], em: float, clip: QRectF | None = None) -> None:
    """text-shadow: .07em 0 .11em rgba(0,0,0,.68), .07em 0 .2em rgba(0,0,0,.4) — voor één lettergrootte."""
    if not teksten:
        return
    mask = QImage(canvas.size(), QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(Qt.GlobalColor.transparent)
    p = QPainter(mask)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    if clip is not None:
        p.setClipRect(clip)
    p.setPen(QColor(0, 0, 0))
    for t in teksten:
        p.setFont(t.font)
        p.drawText(QPointF(t.x + 0.07 * em, t.baseline), t.text)
    p.end()
    p = QPainter(canvas)
    for radius, alpha in ((0.20 * em, 0.40), (0.11 * em, 0.68)):
        p.setOpacity(alpha)
        p.drawImage(0, 0, _blurred(mask, radius))
    p.end()


@dataclass
class Rendered:
    image: QImage
    rects: dict[str, dict[str, float]]      # per element {x, y, w, h} als fractie van het beeld


def render_overlay(lijst: Lijst | None, w: int, h: int, *, dim: float = 0.5,
                   uitlijning: dict[str, dict[str, float]] | None = None) -> QImage:
    return render(lijst, w, h, dim=dim, uitlijning=uitlijning).image


def render(lijst: Lijst | None, w: int, h: int, *, dim: float = 0.5,
           uitlijning: dict[str, dict[str, float]] | None = None) -> Rendered:
    """De complete laag voor een beeld van w × h pixels. `lijst` None = alleen de donkere laag."""
    img = QImage(max(1, w), max(1, h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, round(255 * dim)))
    if lijst is None:
        return Rendered(img, {})
    u = uitlijning or DEFAULT_UITLIJNING
    ug, ut, ul = u["geheel"], u["titel"], u["lijst"]

    vw, vh = w / 100.0, h / 100.0
    basis = min(max(2 * REM, 6 * vw), 5.5 * REM)
    x0, x1 = 6 * vw, w - 6 * vw
    y0, y1 = 5 * vh, h - 5 * vh
    white = QColor(255, 255, 255)

    # ── titel (h1: gecentreerd, line-height 1, onderstreep onder de tekstbreedte) ──
    titel_px = basis * 1.61 * ut["s"] / 100
    tdx, tdy = ut["x"] * vw, ut["y"] * vh            # transform: translate (telt niet mee in de opmaak)
    f_titel = _font(titel_px)
    fm_t = QFontMetricsF(f_titel)
    teksten_titel: list[_Tekst] = []
    base = _baseline(y0, titel_px, fm_t) + tdy
    if lijst.titel_groot is not None:
        f_klein = _font(titel_px * 0.45)
        fm_k = QFontMetricsF(f_klein)
        groot, klein = lijst.titel_groot.upper(), lijst.titel_klein.upper()
        w_groot = fm_t.horizontalAdvance(groot)
        w_spatie = fm_t.horizontalAdvance(" ")
        breedte = w_groot + w_spatie + fm_k.horizontalAdvance(klein)
        tx = (w - breedte) / 2 + tdx
        teksten_titel.append(_Tekst(tx, base, groot, f_titel, white, True))
        teksten_titel.append(_Tekst(tx + w_groot + w_spatie, base, klein, f_klein, white, True))
    else:
        tekst = lijst.titel.upper()
        breedte = fm_t.horizontalAdvance(tekst)
        tx = (w - breedte) / 2 + tdx
        teksten_titel.append(_Tekst(tx, base, tekst, f_titel, white, True))
    lijn = QRectF(tx, y0 + titel_px * 1.08 + tdy, breedte, 0.06 * titel_px)
    titel_onder = y0 + titel_px * 1.14                # zonder translate: zo rekent de flexkolom door

    # ── lijst / melding ─────────────────────────────────────────────────
    teksten_rij: list[_Tekst] = []
    teksten_overig: list[_Tekst] = []
    rij = basis
    ldx, ldy = ul["x"] * vw, ul["y"] * vh
    lijst_clip = QRectF(0, 0, w, y1 + ldy)
    if lijst.melding:
        px = min(max(1.6 * REM, 4 * vw), 3.5 * REM)
        f = _font(px, 0.011)
        fm = QFontMetricsF(f)
        kleur = QColor(255, 255, 255, round(255 * 0.85))
        teksten_overig.append(_Tekst(x0, _baseline(titel_onder + 6 * vh, px * 1.45, fm), lijst.melding.upper(), f, kleur, False))
    else:
        lijst_top = titel_onder + 4.5 * vh + ldy
        tijd_t = [f.tijd for f in lijst.films]
        zaal_t = [f.zaal.upper() for f in lijst.films]
        # --fit: verkleinen tot de vaste kolommen (tijd, zaal) en een redelijke titelkolom passen
        fit = 1.0
        while True:
            rij = basis * ul["s"] / 100 * fit
            f_rij = _font(rij, 0.027)
            fm = QFontMetricsF(f_rij)
            w_tijd = max(fm.horizontalAdvance(t) for t in tijd_t) + 1.95 * rij
            w_zaal = max(fm.horizontalAdvance(z) for z in zaal_t) + 1.4 * rij
            w_titel = (x1 - x0) - w_tijd - w_zaal - 0.6 * rij
            if w_titel >= 8 * rij or fit <= 0.15:
                break
            fit = round(fit - 0.02, 2)
        regel = 0.98 * rij
        y = lijst_top
        tinten = {"hl0": 1.0, "hl1": 0.45, "hl2": 0.33, "mat": 0.24, "": 1.0}
        for film in lijst.films:
            kleur = QColor(255, 255, 255, round(255 * tinten.get(film.klasse, 1.0)))
            schaduw = film.klasse != "mat"
            regels = _wrap(film.titel.upper(), fm, w_titel)
            base = _baseline(y, regel, fm)
            teksten_rij.append(_Tekst(x0 + ldx, base, film.tijd, f_rij, kleur, schaduw))
            for i, r in enumerate(regels):
                teksten_rij.append(_Tekst(x0 + ldx + w_tijd, _baseline(y + i * regel, regel, fm), r, f_rij, kleur, schaduw))
            zaal = film.zaal.upper()
            teksten_rij.append(_Tekst(x1 + ldx - fm.horizontalAdvance(zaal), base, zaal, f_rij, kleur, schaduw))
            y += regel * len(regels)
            if y > y1 + ldy + regel:
                break                                   # .lijst overflow: hidden

    # ── tekenen op een eigen laag: schaduwen (per lettergrootte), tekst, onderstreep ──
    layer = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(Qt.GlobalColor.transparent)
    _draw_shadow(layer, [t for t in teksten_titel if t.shadow], titel_px)
    _draw_shadow(layer, [t for t in teksten_rij if t.shadow], rij, lijst_clip)
    p = QPainter(layer)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setClipRect(lijst_clip)
    for t in teksten_rij:
        p.setFont(t.font)
        p.setPen(t.color)
        p.drawText(QPointF(t.x, t.baseline), t.text)
    p.setClipping(False)
    for t in teksten_titel + teksten_overig:
        p.setFont(t.font)
        p.setPen(t.color)
        p.drawText(QPointF(t.x, t.baseline), t.text)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(white)
    p.drawRect(lijn)
    p.end()

    # ── .geheel: translate + scale om het midden van het tekstvlak ──
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    tr = QTransform()
    tr.translate(cx + ug["x"] * vw, cy + ug["y"] * vh)
    tr.scale(ug["s"] / 100, ug["s"] / 100)
    tr.translate(-cx, -cy)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setTransform(tr)
    p.drawImage(0, 0, layer)
    p.end()

    # vakken voor het uitlijnen op het paneel (zoals lobbyMeld op de site), in beeldcoördinaten
    def frac(r: QRectF) -> dict[str, float]:
        m = tr.mapRect(r)
        return {"x": round(m.x() / w, 4), "y": round(m.y() / h, 4), "w": round(m.width() / w, 4), "h": round(m.height() / h, 4)}
    rects = {
        "geheel": frac(QRectF(x0, y0, x1 - x0, y1 - y0)),
        "titel": frac(QRectF(tx, y0 + tdy, breedte, titel_px * 1.14)),
    }
    if not lijst.melding:
        rects["lijst"] = frac(QRectF(x0 + ldx, lijst_top, x1 - x0, max(0.0, y1 + ldy - lijst_top)))
    return Rendered(img, rects)
