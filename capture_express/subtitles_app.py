from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .subtitle_engine import (
    PROVIDERS,
    SubtitleSegment,
    burn_subtitles,
    extract_audio,
    parse_srt,
    review_srt_with_ollama,
    segments_to_srt,
    transcribe_audio,
    validate_audio_for_transcription,
    write_outputs,
)
from .windowing import configure_process_dpi_awareness


KEYRING_SERVICE = "Sous-titres Express"


class Worker(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, job: Callable[[Callable[[str], None]], object]) -> None:
        super().__init__()
        self._job = job

    def run(self) -> None:
        try:
            self.succeeded.emit(self._job(self.status.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


class SubtitlesExpressWindow(QMainWindow):
    def __init__(self, *, distribution_mode: str = "github") -> None:
        super().__init__()
        self.distribution_mode = distribution_mode
        self.setWindowTitle("Sous-titres Express")
        self.resize(940, 700)
        self.setMinimumSize(620, 460)
        self.video_path: Path | None = None
        self.output_dir: Path | None = None
        self.current_srt_path: Path | None = None
        self.worker: Worker | None = None
        self.provider_api_keys: dict[str, str] = {}
        self.current_provider_key = ""
        self.key_storage_available = True
        self._build_ui()
        self._load_saved_api_keys()
        self._restore_current_api_key()
        self._apply_style()
        self._refresh_provider_state()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel("Sous-titres Express")
        title.setObjectName("title")
        subtitle = QLabel("Creation, correction et assemblage de sous-titres pour vos videos.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        top = QGridLayout()
        self.video_field = QLineEdit()
        self.video_field.setReadOnly(True)
        browse_btn = QPushButton("Choisir video")
        browse_btn.clicked.connect(self.choose_video)
        self.output_field = QLineEdit()
        self.output_field.setReadOnly(True)
        output_btn = QPushButton("Dossier sortie")
        output_btn.clicked.connect(self.choose_output_dir)
        top.addWidget(QLabel("Video"), 0, 0)
        top.addWidget(self.video_field, 0, 1)
        top.addWidget(browse_btn, 0, 2)
        top.addWidget(QLabel("Sortie"), 1, 0)
        top.addWidget(self.output_field, 1, 1)
        top.addWidget(output_btn, 1, 2)
        layout.addLayout(top)

        settings = QVBoxLayout()
        settings.setSpacing(10)
        settings.addWidget(self._provider_box())
        self.local_box = self._local_box()
        settings.addWidget(self.local_box)
        layout.addLayout(settings)

        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        self.generate_btn = QPushButton("Generer les sous-titres")
        self.generate_btn.clicked.connect(self.generate_subtitles)
        self.open_srt_btn = QPushButton("Ouvrir un SRT")
        self.open_srt_btn.clicked.connect(self.open_srt)
        self.save_srt_btn = QPushButton("Sauver corrections")
        self.save_srt_btn.clicked.connect(self.save_srt)
        self.review_btn = QPushButton("Corriger avec Ollama")
        self.review_btn.clicked.connect(self.review_with_ollama)
        self.burn_btn = QPushButton("Assembler video + sous-titres")
        self.burn_btn.clicked.connect(self.burn_current_subtitles)
        actions.addWidget(self.generate_btn, 0, 0)
        actions.addWidget(self.open_srt_btn, 0, 1)
        actions.addWidget(self.save_srt_btn, 0, 2)
        actions.addWidget(self.review_btn, 1, 0)
        actions.addWidget(self.burn_btn, 1, 1, 1, 2)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status_label = QLabel("Pret.")
        self.status_label.setObjectName("status")
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)

        tabs = QTabWidget()
        self.srt_editor = QPlainTextEdit()
        self.srt_editor.setPlaceholderText("Le fichier .srt apparaitra ici pour correction manuelle.")
        self.notes_editor = QPlainTextEdit()
        self.notes_editor.setReadOnly(True)
        self.notes_editor.setPlainText(self._provider_notes())
        tabs.addTab(self.srt_editor, "Correction SRT")
        tabs.addTab(self.notes_editor, "Moteurs")
        layout.addWidget(tabs, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)

    def _provider_box(self) -> QGroupBox:
        box = QGroupBox("Moteur de creation")
        form = QFormLayout(box)
        self.provider_combo = QComboBox()
        for key in self._provider_keys():
            provider = PROVIDERS[key]
            self.provider_combo.addItem(provider.label, key)
        self.current_provider_key = str(self.provider_combo.currentData() or "")
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.api_key_field = QLineEdit()
        self.api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_field.textChanged.connect(self._remember_current_api_key)
        self.model_field = QLineEdit()
        self.language_field = QLineEdit("fr")
        form.addRow("Fournisseur", self.provider_combo)
        form.addRow("Cle API", self.api_key_field)
        form.addRow("Modele", self.model_field)
        form.addRow("Langue", self.language_field)
        return box

    def _local_box(self) -> QGroupBox:
        box = QGroupBox("Correction locale")
        form = QFormLayout(box)
        self.ollama_model_field = QLineEdit("llama3.2:3b")
        self.max_minutes_field = QSpinBox()
        self.max_minutes_field.setRange(1, 240)
        self.max_minutes_field.setValue(30)
        form.addRow("Modele Ollama", self.ollama_model_field)
        form.addRow("Limite pratique", self.max_minutes_field)
        return box

    def _provider_keys(self) -> tuple[str, ...]:
        if self.distribution_mode == "cloud":
            return ("openai", "mistral", "google", "claude")
        return ("openai", "mistral", "local_whisper", "google", "claude", "ollama")

    def choose_video(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir une video",
            "",
            "Videos (*.mp4 *.mov *.mkv *.avi *.webm);;Tous les fichiers (*.*)",
        )
        if not filename:
            return
        self.video_path = Path(filename)
        self.video_field.setText(str(self.video_path))
        if self.output_dir is None:
            self.output_dir = self.video_path.with_suffix("").with_name(f"{self.video_path.stem}-subtitles")
            self.output_field.setText(str(self.output_dir))

    def choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir le dossier de sortie")
        if directory:
            self.output_dir = Path(directory)
            self.output_field.setText(str(self.output_dir))

    def open_srt(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Ouvrir un SRT", "", "SRT (*.srt);;Tous les fichiers (*.*)")
        if not filename:
            return
        self.current_srt_path = Path(filename)
        self.srt_editor.setPlainText(self.current_srt_path.read_text(encoding="utf-8", errors="replace"))
        self.status_label.setText(f"SRT charge: {self.current_srt_path}")

    def save_srt(self) -> None:
        if self.current_srt_path is None:
            filename, _ = QFileDialog.getSaveFileName(self, "Sauver le SRT", "", "SRT (*.srt)")
            if not filename:
                return
            self.current_srt_path = Path(filename)
        text = self.srt_editor.toPlainText().strip() + "\n"
        parse_srt(text)
        self.current_srt_path.write_text(text, encoding="utf-8")
        self.status_label.setText(f"Corrections sauvees: {self.current_srt_path}")

    def generate_subtitles(self) -> None:
        if self.video_path is None or self.output_dir is None:
            QMessageBox.warning(self, "Sous-titres Express", "Choisissez une video et un dossier de sortie.")
            return
        provider_key = str(self.provider_combo.currentData())
        self._remember_current_api_key()
        self._save_current_api_key()
        provider = PROVIDERS[provider_key]
        if not provider.can_transcribe:
            QMessageBox.information(
                self,
                "Moteur non disponible",
                f"{provider.label} ne sert pas de moteur de transcription direct dans cette version.",
            )
            return

        def job(status: Callable[[str], None]) -> dict[str, object]:
            assert self.video_path is not None
            assert self.output_dir is not None
            status("Extraction audio...")
            audio_path = extract_audio(self.video_path, self.output_dir / f"{self.video_path.stem}.wav")
            status("Verification de l'audio...")
            validate_audio_for_transcription(audio_path)
            status("Transcription IA...")
            segments = transcribe_audio(
                audio_path,
                provider=provider_key,  # type: ignore[arg-type]
                api_key=self.api_key_field.text(),
                language=self.language_field.text(),
                model=self.model_field.text(),
            )
            status("Ecriture des fichiers...")
            paths = write_outputs(segments, self.output_dir, self.video_path.stem)
            return {"segments": segments, "paths": paths}

        self._run_job(job, self._on_generated)

    def review_with_ollama(self) -> None:
        if self.distribution_mode == "cloud":
            QMessageBox.information(
                self,
                "Version cloud",
                "La correction Ollama est disponible dans la version GitHub avec moteur local.",
            )
            return
        text = self.srt_editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Sous-titres Express", "Chargez ou genereez un SRT avant correction.")
            return

        def job(status: Callable[[str], None]) -> str:
            status("Correction avec Ollama...")
            return review_srt_with_ollama(text, model=self.ollama_model_field.text().strip() or "llama3.2:3b")

        self._run_job(job, self._on_reviewed)

    def burn_current_subtitles(self) -> None:
        if self.video_path is None:
            QMessageBox.warning(self, "Sous-titres Express", "Choisissez une video.")
            return
        if self.current_srt_path is None:
            self.save_srt()
            if self.current_srt_path is None:
                return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter la video sous-titree",
            str(self.video_path.with_name(f"{self.video_path.stem}-subtitled.mp4")),
            "MP4 (*.mp4)",
        )
        if not destination:
            return

        def job(status: Callable[[str], None]) -> Path:
            assert self.video_path is not None
            assert self.current_srt_path is not None
            status("Assemblage video + sous-titres...")
            return burn_subtitles(self.video_path, self.current_srt_path, Path(destination))

        self._run_job(job, lambda path: self.status_label.setText(f"Video exportee: {path}"))

    def _run_job(self, job: Callable[[Callable[[str], None]], object], on_success: Callable[[object], None]) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self._set_busy(True)
        self.worker = Worker(job)
        self.worker.status.connect(self.status_label.setText)
        self.worker.succeeded.connect(lambda result: self._finish_success(result, on_success))
        self.worker.failed.connect(self._finish_error)
        self.worker.start()

    def _finish_success(self, result: object, on_success: Callable[[object], None]) -> None:
        self._set_busy(False)
        on_success(result)

    def _finish_error(self, message: str) -> None:
        self._set_busy(False)
        self.status_label.setText("Erreur.")
        QMessageBox.critical(self, "Sous-titres Express", message)

    def _on_generated(self, result: object) -> None:
        data = result if isinstance(result, dict) else {}
        segments = data.get("segments", [])
        paths = data.get("paths", {})
        if isinstance(segments, list) and all(isinstance(item, SubtitleSegment) for item in segments):
            self.srt_editor.setPlainText(segments_to_srt(segments))
        if isinstance(paths, dict):
            srt_path = paths.get("srt")
            if isinstance(srt_path, Path):
                self.current_srt_path = srt_path
                self.status_label.setText(f"Sous-titres generes: {srt_path}")

    def _on_reviewed(self, result: object) -> None:
        text = str(result).strip()
        if text:
            self.srt_editor.setPlainText(text + "\n")
            self.status_label.setText("Correction Ollama terminee.")

    def _set_busy(self, busy: bool) -> None:
        self.progress.setRange(0, 0 if busy else 1)
        self.progress.setValue(0 if busy else 1)
        for button in (
            self.generate_btn,
            self.open_srt_btn,
            self.save_srt_btn,
            self.review_btn,
            self.burn_btn,
        ):
            button.setEnabled(not busy)

    def _refresh_provider_state(self) -> None:
        if not hasattr(self, "provider_combo"):
            return
        provider = PROVIDERS[str(self.provider_combo.currentData())]
        self.api_key_field.setEnabled(provider.requires_key)
        self.model_field.setPlaceholderText(self._model_placeholder(provider.key))
        if hasattr(self, "local_box"):
            local_enabled = self.distribution_mode != "cloud"
            self.local_box.setVisible(local_enabled)
            self.review_btn.setVisible(local_enabled)
        self.status_label.setText(provider.note if hasattr(self, "status_label") else "")

    def _provider_changed(self) -> None:
        if self.current_provider_key and self.api_key_field.text():
            self.provider_api_keys[self.current_provider_key] = self.api_key_field.text()
            self._save_api_key(self.current_provider_key, self.api_key_field.text())
        self.current_provider_key = str(self.provider_combo.currentData() or "")
        self._restore_current_api_key()
        self._refresh_provider_state()

    def _remember_current_api_key(self) -> None:
        if not hasattr(self, "provider_combo") or not hasattr(self, "api_key_field"):
            return
        provider_key = str(self.provider_combo.currentData() or "")
        if not provider_key:
            return
        value = self.api_key_field.text()
        if value:
            self.provider_api_keys[provider_key] = value

    def _save_current_api_key(self) -> None:
        provider_key = str(self.provider_combo.currentData() or "")
        value = self.api_key_field.text()
        if provider_key and value:
            self._save_api_key(provider_key, value)

    def _restore_current_api_key(self) -> None:
        provider_key = str(self.provider_combo.currentData() or "")
        value = self.provider_api_keys.get(provider_key, "")
        if self.api_key_field.text() == value:
            return
        previous_state = self.api_key_field.blockSignals(True)
        self.api_key_field.setText(value)
        self.api_key_field.blockSignals(previous_state)

    def _load_saved_api_keys(self) -> None:
        try:
            import keyring
        except ImportError:
            self.key_storage_available = False
            return
        for key in self._provider_keys():
            provider = PROVIDERS[key]
            if not provider.requires_key:
                continue
            try:
                value = keyring.get_password(KEYRING_SERVICE, key)
            except Exception:
                self.key_storage_available = False
                return
            if value:
                self.provider_api_keys[key] = value

    def _save_api_key(self, provider_key: str, value: str) -> None:
        if not value.strip():
            return
        try:
            import keyring
        except ImportError:
            self.key_storage_available = False
            return
        try:
            keyring.set_password(KEYRING_SERVICE, provider_key, value)
        except Exception:
            self.key_storage_available = False

    def closeEvent(self, event: object) -> None:
        self._remember_current_api_key()
        self._save_current_api_key()
        super().closeEvent(event)  # type: ignore[arg-type]

    def _model_placeholder(self, provider_key: str) -> str:
        defaults = {
            "openai": "whisper-1",
            "mistral": "voxtral-mini-latest",
            "local_whisper": "tiny / base / small",
            "ollama": "llama3.2:3b",
        }
        return defaults.get(provider_key, "modele")

    def _provider_notes(self) -> str:
        lines = [
            "Principe:",
            "- OpenAI, Mistral et Whisper local peuvent creer des sous-titres depuis l'audio.",
            "- Claude et Ollama servent ici a corriger/reformater un SRT deja genere.",
            "- Google Speech-to-Text demandera une integration Google Cloud plus stricte qu'une simple cle collee.",
            "- Les cles API collees sont sauvegardees dans le gestionnaire d'identifiants du systeme.",
            "",
            "Distribution:",
            "- Version GitHub GPL: moteurs cloud + branchements locaux pour machines compatibles.",
            "- Version exe grand public: experience 100% cloud, sans dependance locale lourde.",
            "",
            "Pour un PC 8 Go:",
            "- Whisper tiny/base en CPU int8 est le choix local le plus raisonnable.",
            "- Les petits modeles Ollama 1.5B/3B peuvent corriger un SRT, mais seront lents sur long fichier.",
            "- Les modeles 7B et plus risquent d'etre inconfortables avec moins de 8 Go.",
        ]
        return "\n".join(lines)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { font-size: 13px; }
            #title { font-size: 28px; font-weight: 700; }
            #subtitle { color: #555; }
            #status { color: #333; }
            QPlainTextEdit { font-family: Consolas, monospace; font-size: 13px; }
            QPushButton { padding: 8px 10px; }
            QGroupBox { font-weight: 600; }
            """
        )


def main(distribution_mode: str | None = None) -> int:
    configure_process_dpi_awareness()
    app = QApplication(sys.argv)
    mode = distribution_mode or os.environ.get("SUBTITLES_EXPRESS_MODE", "github")
    window = SubtitlesExpressWindow(distribution_mode=mode)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
