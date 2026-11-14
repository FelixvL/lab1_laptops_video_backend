"""
CinePlayer — het kleine instellingenvenster.

Blijft over als je de fullscreen speler sluit met het kruisje. Hier kun je:
  - de videomap kiezen (waar de films staan),
  - de naam op het paneel, het adres van api.php en de API-sleutel wijzigen,
  - speellijsten importeren (Media Player Classic .mpcpl, .m3u, .m3u8, .pls) of verwijderen,
  - de speler weer (fullscreen) starten, of het programma echt afsluiten.

Alle wijzigingen gaan via het kioskvenster (`owner`), dat ze in settings.json zet
en meteen toepast. Dit venster sluiten sluit het programma af.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .playlists import FILE_FILTER

STYLE = """
QWidget { font-family: 'Segoe UI', sans-serif; font-size: 10pt; }
QGroupBox { font-weight: 600; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QLabel#kop { font-size: 14pt; font-weight: 600; }
QLabel#hint, QLabel#stand { color: #5f6368; }
QPushButton { padding: 6px 14px; }
QPushButton#start { font-weight: 600; padding: 10px 22px; }
"""


class SettingsWindow(QWidget):
    def __init__(self, owner) -> None:
        super().__init__(None)
        self.owner = owner
        self._last_dir = str(Path.home())
        self.setWindowTitle("LAB-1 Lobby — instellingen")
        self.setStyleSheet(STYLE)
        self.resize(660, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        kop = QLabel("LAB-1 Lobby — speler staat stil")
        kop.setObjectName("kop")
        root.addWidget(kop)
        uitleg = QLabel("De fullscreen speler is gesloten. Pas hier de instellingen aan en start hem daarna opnieuw.")
        uitleg.setObjectName("hint")
        uitleg.setWordWrap(True)
        root.addWidget(uitleg)

        # ── videomap ──
        box = QGroupBox("Videomap")
        lay = QVBoxLayout(box)
        rij = QHBoxLayout()
        self.ed_videos = QLineEdit()
        self.ed_videos.setReadOnly(True)
        self.btn_videos = QPushButton("Kies map…")
        self.btn_videos.clicked.connect(self._choose_videos)
        rij.addWidget(self.ed_videos, 1)
        rij.addWidget(self.btn_videos)
        lay.addLayout(rij)
        self.lbl_videos = QLabel("")
        self.lbl_videos.setObjectName("stand")
        lay.addWidget(self.lbl_videos)
        root.addWidget(box)

        # ── paneel ──
        box = QGroupBox("Paneel")
        form = QFormLayout(box)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("leeg = naam van deze computer")
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("https://…/api.php")
        self.ed_key = QLineEdit()
        self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.chk_key = QCheckBox("tonen")
        self.chk_key.toggled.connect(
            lambda on: self.ed_key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        rij = QHBoxLayout()
        rij.addWidget(self.ed_key, 1)
        rij.addWidget(self.chk_key)
        form.addRow("Naam op het paneel", self.ed_name)
        form.addRow("Adres van api.php", self.ed_url)
        form.addRow("API-sleutel", rij)
        rij = QHBoxLayout()
        self.lbl_panel = QLabel("")
        self.lbl_panel.setObjectName("stand")
        self.btn_panel = QPushButton("Opslaan en verbinden")
        self.btn_panel.clicked.connect(self._save_panel)
        rij.addWidget(self.lbl_panel, 1)
        rij.addWidget(self.btn_panel)
        form.addRow(rij)
        root.addWidget(box)

        # ── speellijsten ──
        box = QGroupBox("Speellijsten")
        lay = QVBoxLayout(box)
        self.lst = QListWidget()
        self.lst.setMinimumHeight(130)
        lay.addWidget(self.lst, 1)
        rij = QHBoxLayout()
        self.btn_import = QPushButton("Importeren…")
        self.btn_import.clicked.connect(self._choose_import)
        self.btn_delete = QPushButton("Verwijderen")
        self.btn_delete.clicked.connect(self._delete_selected)
        rij.addWidget(self.btn_import)
        rij.addWidget(self.btn_delete)
        rij.addStretch(1)
        lay.addLayout(rij)
        hint = QLabel("Uit Media Player Classic: bestanden .mpcpl, .m3u of .pls. De laatst geopende lijst heet "
                      "default.mpcpl en staat in %APPDATA%\\MPC-HC of %APPDATA%\\MPC-BE. Een film die niet meer op "
                      "zijn oude plek staat, wordt op bestandsnaam in de videomap gezocht. Kiezen welke lijst speelt "
                      "doe je op het paneel.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        root.addWidget(box, 1)

        # ── onderaan ──
        rij = QHBoxLayout()
        self.btn_quit = QPushButton("Afsluiten")
        self.btn_quit.clicked.connect(self.owner.quit_app)
        self.btn_start = QPushButton("Start speler (fullscreen)" if owner.kiosk else "Start speler")
        self.btn_start.setObjectName("start")
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self.owner.resume_player)
        rij.addWidget(self.btn_quit)
        rij.addStretch(1)
        rij.addWidget(self.btn_start)
        root.addLayout(rij)

    # ── tonen ───────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Alle velden vullen met de huidige stand van de speler."""
        cfg = self.owner.cfg
        self.ed_videos.setText(str(cfg.videos_dir))
        self.ed_name.setText(cfg.player_id)
        self.ed_url.setText(cfg.server_url)
        self.ed_key.setText(cfg.api_key)
        self.refresh_status()
        self.refresh_lists()

    def refresh_status(self) -> None:
        n = len(self.owner.videos)
        map_ok = Path(self.owner.cfg.videos_dir).is_dir()
        self.lbl_videos.setText(f"{n} video's gevonden (ook in submappen)" if map_ok else "Deze map bestaat niet.")
        if not self.owner.cfg.remote_enabled:
            self.lbl_panel.setText("Niet ingesteld: vul adres en sleutel in.")
        elif self.owner._online:
            self.lbl_panel.setText(f"Verbonden als {self.owner.cfg.player_id}.")
        else:
            self.lbl_panel.setText("Nog geen verbinding met het paneel…")

    def refresh_lists(self) -> None:
        self.lst.clear()
        for info in self.owner.playlist_info:
            tekst = f"{info['name']}  —  {info['count']} films"
            if info["missing"]:
                tekst += f"  ({info['missing']} niet gevonden)"
            self.lst.addItem(tekst)
            self.lst.item(self.lst.count() - 1).setData(Qt.ItemDataRole.UserRole, info["name"])
        self.btn_delete.setEnabled(self.lst.count() > 0)

    # ── acties ──────────────────────────────────────────────────────────
    def _choose_videos(self) -> None:
        start = str(self.owner.cfg.videos_dir)
        path = QFileDialog.getExistingDirectory(self, "Kies de map met video's", start)
        if path:
            self.apply_videos_dir(path)

    def apply_videos_dir(self, path: str) -> None:
        self.owner.set_videos_dir(path)
        self.ed_videos.setText(str(self.owner.cfg.videos_dir))
        self.refresh_status()
        self.refresh_lists()

    def _save_panel(self) -> None:
        url = self.ed_url.text().strip()
        if url and not url.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "Paneel", "Het adres moet met http:// of https:// beginnen.")
            return
        self.owner.set_panel(self.ed_name.text(), url, self.ed_key.text())
        self.ed_name.setText(self.owner.cfg.player_id)
        self.refresh_status()

    def _choose_import(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Speellijsten importeren", self._last_dir, FILE_FILTER)
        if not paths:
            return
        self._last_dir = str(Path(paths[0]).parent)
        files: list[tuple[str, str | None]] = []
        for p in paths:
            naam, ok = QInputDialog.getText(self, "Naam van de speellijst",
                                            f"Naam voor {Path(p).name} (zo heet hij op het paneel):",
                                            text=Path(p).stem)
            if ok:
                files.append((p, naam.strip() or None))
        if files:
            regels = self.import_files(files)
            QMessageBox.information(self, "Speellijsten importeren", "\n".join(regels))

    def import_files(self, files: list[tuple[str, str | None]]) -> list[str]:
        regels = self.owner.import_playlists(files)
        self.refresh_lists()
        return regels

    def _delete_selected(self) -> None:
        item = self.lst.currentItem()
        if item is None:
            return
        naam = item.data(Qt.ItemDataRole.UserRole)
        antwoord = QMessageBox.question(self, "Speellijst verwijderen",
                                        f"Speellijst “{naam}” verwijderen? De films zelf blijven staan.")
        if antwoord == QMessageBox.StandardButton.Yes:
            self.owner.remove_playlist(naam)
            self.refresh_lists()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Dit venster sluiten = het programma afsluiten."""
        if not self.owner._quitting:
            event.ignore()
            self.owner.quit_app()
            return
        super().closeEvent(event)
