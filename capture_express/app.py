from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap, QTextCharFormat, QTransform
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .media import (
    ClipRecorder,
    capture_recording_path,
    capture_screenshot,
    capture_track_paths,
    cleanup_capture_sidecars,
    finalize_video,
    list_directshow_audio_devices,
    list_directshow_video_devices,
    screen_region_at,
)
from .models import CaptureArtifact, CaptureExpressConfig, CaptureFormat, CaptureRegion
from .session import CaptureExpressSession
from .windowing import (
    cursor_position,
    configure_process_dpi_awareness,
    exclude_widget_from_windows_capture,
    is_virtual_key_down,
    list_window_infos,
    screen_geometry_at,
    window_info_by_handle,
)


def _asset_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    packaged = base / "capture_express" / "assets" / name
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parent / "assets" / name


class CaptureExpressWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NB Capture")
        self.setWindowIcon(QIcon(str(_asset_path("nb_capture.ico"))))
        self.resize(820, 540)
        self.setMinimumSize(620, 460)
        self.session: CaptureExpressSession | None = None
        self.overlay: FloatingCaptureMenu | None = None
        self.region_overlay: RegionOverlay | None = None
        self.region_selector: RegionSelectorOverlay | None = None
        self.annotation_overlay: AnnotationOverlay | None = None
        self.effect_overlay: ClickEffectOverlay | None = None
        self.active_recorder: ClipRecorder | None = None
        self.active_video_artifact_id = ""
        self.active_video_source = ""
        self.active_video_started_at = datetime.now()
        self.active_video_paused = False
        self.capture_active = False
        self.space_down = False
        self.space_pressed_ms = 0
        self.space_hold_started = False
        self.ctrl_space_down = False
        self.shift_ctrl_space_down = False
        self.ctrl_down_since_ms: int | None = None
        self.ctrl_clear_triggered = False
        self.ctrl_drag_start: tuple[int, int] | None = None
        self.ctrl_drag_active = False
        self.ctrl_zone_armed = False
        self.ctrl_zone_completed_this_hold = False
        self.alt_down = False
        self.alt_pressed_ms = 0
        self.alt_highlight_started = False
        self.zone_left_down = False
        self.click_left_down = False
        self.right_down = False
        self.annotation_text_color = QColor(17, 24, 39)
        self.annotation_highlight_color = QColor(250, 204, 21)
        self.annotation_text_size = 18
        self.annotation_text_weight = 400
        self.annotation_highlight_thickness = 18
        self._refresh_icons: dict[QPushButton, QPixmap] = {}
        self._refresh_animation_steps: dict[QPushButton, int] = {}
        self._build_ui()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(45)
        self.poll_timer.timeout.connect(self._poll_keys)
        self.poll_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("NB Capture")
        title.setStyleSheet("font-size: 24px; font-weight: 800; color: #111827;")
        layout.addWidget(title)

        form = QGroupBox("Session")
        form.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form_layout = QGridLayout(form)
        form_layout.setSpacing(8)
        self.output_input = QLineEdit(str((Path.cwd() / "runs" / "capture-express").resolve()))
        browse_btn = QPushButton("Choisir")
        self._stabilize_button(browse_btn)
        browse_btn.clicked.connect(self._choose_output_dir)
        self.title_input = QLineEdit("capture")
        form_layout.addWidget(QLabel("Dossier des captures"), 0, 0)
        form_layout.addWidget(self.output_input, 0, 1)
        form_layout.addWidget(browse_btn, 0, 2)
        form_layout.addWidget(QLabel("Titre session"), 1, 0)
        form_layout.addWidget(self.title_input, 1, 1, 1, 2)
        layout.addWidget(form)

        settings = QGroupBox("Format et capture")
        settings.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        settings_layout = QGridLayout(settings)
        settings_layout.setHorizontalSpacing(12)
        settings_layout.setVerticalSpacing(8)
        settings_layout.setColumnStretch(1, 1)
        settings_layout.setColumnStretch(3, 1)
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(150)
        self.source_combo.addItem("Ecran sous la souris", "screen")
        self.source_combo.addItem("Fenetre choisie", "window")
        self.source_combo.addItem("Zone libre avec Ctrl + glisser", "free_zone")
        self.source_combo.currentIndexChanged.connect(self._sync_capture_source_controls)
        self.window_label = QLabel("Fenetre")
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(150)
        self.refresh_windows_btn = self._icon_button("Rafraichir la liste des fenetres")
        self.refresh_windows_btn.clicked.connect(
            lambda: self._run_refresh_action(self.refresh_windows_btn, self._populate_window_combo)
        )
        self.window_picker = self._combo_with_icon(self.window_combo, self.refresh_windows_btn)
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("Taille source", "source")
        self.profile_combo.addItem("HD 1280x720", "720p")
        self.profile_combo.addItem("Full HD 1920x1080", "1080p")
        self.screenshot_combo = QComboBox()
        self.screenshot_combo.addItems(["png", "jpg"])
        self.screenshot_combo.setCurrentText("jpg")
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 60)
        self.fps_spin.setValue(30)
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("Rapide (gros fichiers)", "fast")
        self.quality_combo.addItem("Equilibre", "balanced")
        self.quality_combo.addItem("Compact (720p)", "compact")
        self.cursor_effect_check = QCheckBox("Effet visuel curseur/clic")
        self.cursor_effect_check.setChecked(True)
        self.logo_check = QCheckBox("Ajouter un logo")
        self.logo_check.stateChanged.connect(self._sync_option_visibility)
        self.logo_path_input = QLineEdit()
        self.logo_path_input.setPlaceholderText("PNG/JPG")
        self.logo_browse_btn = QPushButton("Choisir")
        self._stabilize_button(self.logo_browse_btn)
        self.logo_browse_btn.clicked.connect(self._choose_logo)
        self.logo_picker = self._combo_with_icon(self.logo_path_input, self.logo_browse_btn)
        self.logo_position_combo = QComboBox()
        self.logo_position_combo.addItem("Haut gauche", "top_left")
        self.logo_position_combo.addItem("Haut droite", "top_right")
        self.logo_position_label = QLabel("Position logo")
        self.annotation_text_color_btn = QPushButton("Texte")
        self._stabilize_button(self.annotation_text_color_btn)
        self.annotation_text_color_btn.clicked.connect(self._choose_annotation_text_color)
        self.annotation_highlight_color_btn = QPushButton("Surligneur")
        self._stabilize_button(self.annotation_highlight_color_btn)
        self.annotation_highlight_color_btn.clicked.connect(self._choose_annotation_highlight_color)
        annotation_color_row = QHBoxLayout()
        annotation_color_row.setContentsMargins(0, 0, 0, 0)
        annotation_color_row.setSpacing(6)
        annotation_color_row.addWidget(self.annotation_text_color_btn)
        annotation_color_row.addWidget(self.annotation_highlight_color_btn)
        annotation_color_row.addStretch(1)
        annotation_color_widget = QWidget()
        annotation_color_widget.setLayout(annotation_color_row)
        self.annotation_text_size_spin = QSpinBox()
        self.annotation_text_size_spin.setRange(10, 72)
        self.annotation_text_size_spin.setValue(self.annotation_text_size)
        self.annotation_text_size_spin.setSuffix(" px")
        self.annotation_text_size_spin.valueChanged.connect(self._annotation_style_changed)
        self.annotation_text_weight_combo = QComboBox()
        self.annotation_text_weight_combo.addItem("Normal", 400)
        self.annotation_text_weight_combo.addItem("Demi-gras", 600)
        self.annotation_text_weight_combo.addItem("Gras", 700)
        self.annotation_text_weight_combo.addItem("Tres gras", 800)
        self.annotation_text_weight_combo.currentIndexChanged.connect(self._annotation_style_changed)
        self.annotation_highlight_thickness_spin = QSpinBox()
        self.annotation_highlight_thickness_spin.setRange(6, 48)
        self.annotation_highlight_thickness_spin.setValue(self.annotation_highlight_thickness)
        self.annotation_highlight_thickness_spin.setSuffix(" px")
        self.annotation_highlight_thickness_spin.valueChanged.connect(self._annotation_style_changed)
        self._sync_annotation_color_buttons()
        self.exclude_taskbar_check = QCheckBox("Masquer la barre des taches en mode tout l'ecran")
        self.exclude_taskbar_check.setChecked(False)
        self.audio_check = QCheckBox("Autoriser audio micro")
        self.audio_check.setChecked(True)
        self.audio_check.stateChanged.connect(self._sync_option_visibility)
        self.audio_label = QLabel("Micro")
        self.audio_device_input = QComboBox()
        self.audio_device_input.setMinimumWidth(150)
        self.audio_device_input.setEditable(True)
        self.refresh_audio_btn = self._icon_button("Detecter les micros")
        self.refresh_audio_btn.clicked.connect(
            lambda: self._run_refresh_action(self.refresh_audio_btn, self._populate_audio_devices)
        )
        self.audio_picker = self._combo_with_icon(self.audio_device_input, self.refresh_audio_btn)
        self.webcam_check = QCheckBox("Webcam sur videos longues")
        self.webcam_check.stateChanged.connect(self._sync_option_visibility)
        self.webcam_label = QLabel("Webcam")
        self.webcam_device_input = QComboBox()
        self.webcam_device_input.setMinimumWidth(150)
        self.webcam_device_input.setEditable(True)
        self.refresh_webcam_btn = self._icon_button("Detecter les webcams")
        self.refresh_webcam_btn.clicked.connect(
            lambda: self._run_refresh_action(self.refresh_webcam_btn, self._populate_webcam_devices)
        )
        self.webcam_picker = self._combo_with_icon(self.webcam_device_input, self.refresh_webcam_btn)
        settings_layout.addWidget(QLabel("Source de capture"), 0, 0)
        settings_layout.addWidget(self.source_combo, 0, 1)
        settings_layout.addWidget(self.window_label, 0, 2)
        settings_layout.addWidget(self.window_picker, 0, 3)
        settings_layout.addWidget(QLabel("Taille de sortie"), 1, 0)
        settings_layout.addWidget(self.profile_combo, 1, 1)
        settings_layout.addWidget(QLabel("Screenshot"), 1, 2)
        settings_layout.addWidget(self.screenshot_combo, 1, 3)
        settings_layout.addWidget(QLabel("FPS"), 2, 0)
        settings_layout.addWidget(self.fps_spin, 2, 1)
        settings_layout.addWidget(QLabel("Qualite video"), 2, 2)
        settings_layout.addWidget(self.quality_combo, 2, 3)
        settings_layout.addWidget(self.cursor_effect_check, 3, 0, 1, 4)
        settings_layout.addWidget(self.logo_check, 4, 0)
        settings_layout.addWidget(self.logo_picker, 4, 1)
        settings_layout.addWidget(self.logo_position_label, 4, 2)
        settings_layout.addWidget(self.logo_position_combo, 4, 3)
        settings_layout.addWidget(QLabel("Couleur annotations"), 5, 0)
        settings_layout.addWidget(annotation_color_widget, 5, 1, 1, 3)
        settings_layout.addWidget(QLabel("Taille texte"), 6, 0)
        settings_layout.addWidget(self.annotation_text_size_spin, 6, 1)
        settings_layout.addWidget(QLabel("Graisse texte"), 6, 2)
        settings_layout.addWidget(self.annotation_text_weight_combo, 6, 3)
        settings_layout.addWidget(QLabel("Graisse surligneur"), 7, 0)
        settings_layout.addWidget(self.annotation_highlight_thickness_spin, 7, 1)
        settings_layout.addWidget(self.exclude_taskbar_check, 8, 0, 1, 4)
        settings_layout.addWidget(self.audio_check, 9, 0, 1, 2)
        settings_layout.addWidget(self.audio_label, 9, 2)
        settings_layout.addWidget(self.audio_picker, 9, 3)
        settings_layout.addWidget(self.webcam_check, 10, 0, 1, 2)
        settings_layout.addWidget(self.webcam_label, 10, 2)
        settings_layout.addWidget(self.webcam_picker, 10, 3)
        settings.setMinimumHeight(settings.sizeHint().height())
        layout.addWidget(settings)
        self._populate_window_combo()
        self._populate_audio_devices()
        self._populate_webcam_devices()
        self._sync_capture_source_controls()

        script = QGroupBox("Script video (Optionnel)")
        script.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        script_layout = QVBoxLayout(script)
        toolbar = QHBoxLayout()
        bold_btn = QPushButton("B")
        italic_btn = QPushButton("I")
        underline_btn = QPushButton("U")
        color_btn = QPushButton("Couleur")
        bigger_btn = QPushButton("A+")
        smaller_btn = QPushButton("A-")
        for btn in (bold_btn, italic_btn, underline_btn, color_btn, bigger_btn, smaller_btn):
            self._stabilize_button(btn, compact=True)
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        self.script_input = QTextEdit()
        self.script_input.setAcceptRichText(True)
        self.script_input.setPlaceholderText("Texte a lire pendant la video. Mise en forme libre.")
        self.script_input.setMinimumHeight(130)
        self.script_input.setStyleSheet(
            "font-size: 14px; border: 1px solid #cbd5e1; border-radius: 5px; "
            "background: #ffffff; color: #111827;"
        )
        self.show_script_check = QCheckBox("Afficher le script dans le menu flottant")
        self.show_script_check.setChecked(False)
        self.show_script_check.stateChanged.connect(lambda _state: self._update_overlay_status())
        bold_btn.clicked.connect(lambda: self._toggle_text_format("bold"))
        italic_btn.clicked.connect(lambda: self._toggle_text_format("italic"))
        underline_btn.clicked.connect(lambda: self._toggle_text_format("underline"))
        color_btn.clicked.connect(self._choose_script_color)
        bigger_btn.clicked.connect(lambda: self._adjust_script_font_size(1))
        smaller_btn.clicked.connect(lambda: self._adjust_script_font_size(-1))
        script_layout.addLayout(toolbar)
        script_layout.addWidget(self.script_input)
        script_layout.addWidget(self.show_script_check)
        script.setMinimumHeight(script.sizeHint().height())
        layout.addWidget(script)

        shortcuts = QLabel(
            "Espace court: screenshot | maintenir Espace: video courte | Ctrl+Espace: video longue | "
            "Shift+Ctrl+Espace: video + son | webcam optionnelle sur video longue | Ctrl+glisser: zone | "
            "Alt court: texte | maintenir Alt: surligneur | croix zone: plein ecran | "
            "Espace pendant REC: pause/reprise | Echap: stop"
        )
        shortcuts.setWordWrap(True)
        shortcuts.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        shortcuts.setStyleSheet("color: #475569;")
        layout.addWidget(shortcuts)

        buttons = QHBoxLayout()
        start_btn = QPushButton("Lancer mode capture active")
        self._stabilize_button(start_btn)
        start_btn.setObjectName("primary")
        start_btn.clicked.connect(self.start_capture_mode)
        quit_btn = QPushButton("Quitter")
        self._stabilize_button(quit_btn)
        quit_btn.clicked.connect(self.close)
        buttons.addWidget(start_btn)
        buttons.addStretch(1)
        buttons.addWidget(quit_btn)
        layout.addLayout(buttons)
        layout.addStretch(1)

        root.setStyleSheet(
            """
            QWidget { background: #ffffff; color: #111827; font-family: Segoe UI, Arial; font-size: 13px; }
            QGroupBox { background: #ffffff; color: #111827; font-weight: 700; border: 1px solid #d8dee9; border-radius: 6px; margin-top: 8px; padding: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QPushButton { padding: 7px 12px; min-height: 32px; border-radius: 5px; border: 1px solid #94a3b8; background: #ffffff; color: #111827; }
            QPushButton:hover { background: #f1f5f9; }
            QPushButton#primary { background: #2563eb; color: white; border-color: #1d4ed8; font-weight: 700; }
            QLineEdit, QComboBox, QSpinBox { padding: 5px; border: 1px solid #cbd5e1; border-radius: 4px; color: #111827; background: #ffffff; }
            """
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        root.setMinimumWidth(760)
        root.adjustSize()

    def _icon_button(self, tooltip: str) -> QPushButton:
        button = QPushButton()
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        pixmap = icon.pixmap(18, 18)
        self._refresh_icons[button] = pixmap
        button.setIcon(QIcon(pixmap))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(28, 28)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                padding: 4px;
            }
            QPushButton:hover {
                background: transparent;
                border: none;
            }
            """
        )
        return button

    def _stabilize_button(self, button: QPushButton, *, compact: bool = False) -> None:
        button.setMinimumHeight(30 if compact else 36)
        button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _combo_with_icon(self, combo: QComboBox, button: QPushButton) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(combo, 1)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return wrapper

    def _run_refresh_action(self, button: QPushButton, action: object) -> None:
        self._spin_refresh_icon(button)
        if callable(action):
            action()

    def _spin_refresh_icon(self, button: QPushButton) -> None:
        self._refresh_animation_steps[button] = 0
        self._advance_refresh_spin(button)

    def _advance_refresh_spin(self, button: QPushButton) -> None:
        pixmap = self._refresh_icons.get(button)
        if pixmap is None:
            return
        step = self._refresh_animation_steps.get(button, 0)
        angle = step * 30
        rotated = pixmap.transformed(QTransform().rotate(angle), Qt.TransformationMode.SmoothTransformation)
        button.setIcon(QIcon(rotated))
        if step >= 12:
            button.setIcon(QIcon(pixmap))
            self._refresh_animation_steps.pop(button, None)
            return
        self._refresh_animation_steps[button] = step + 1
        QTimer.singleShot(28, lambda: self._advance_refresh_spin(button))

    def start_capture_mode(self) -> None:
        config = self._read_config()
        if config.capture_source == "window" and config.initial_region is None:
            QMessageBox.warning(
                self,
                "Fenetre",
                "La fenetre choisie n'est plus disponible. Rafraichissez la liste et selectionnez-la a nouveau.",
            )
            return
        self.session = CaptureExpressSession(config)
        self.session.start()
        script_html = self.script_input.toHtml()
        script_plain = self.script_input.toPlainText().strip()
        if script_plain:
            (self.session.output_dir / "script.html").write_text(script_html, encoding="utf-8")
            (self.session.output_dir / "script.txt").write_text(script_plain, encoding="utf-8")
        self.capture_active = True
        if config.initial_region is not None:
            self.session.set_capture_region(config.initial_region)
        self.overlay = FloatingCaptureMenu()
        self.overlay.screenshotRequested.connect(self.capture_screenshot)
        self.overlay.videoRequested.connect(lambda: self.toggle_long_video(with_audio=False))
        self.overlay.videoAudioRequested.connect(lambda: self.toggle_long_video(with_audio=True))
        self.overlay.zoneRequested.connect(self.start_zone_selector)
        self.overlay.clearZoneRequested.connect(self._clear_capture_region)
        self.overlay.clearAnnotationsRequested.connect(self._clear_annotations)
        self.overlay.artifactOpenRequested.connect(self._show_artifact_lightbox)
        self.overlay.artifactFolderRequested.connect(self._open_artifact_folder)
        self.overlay.sessionFolderRequested.connect(self._open_session_folder)
        self.overlay.stopVideoRequested.connect(self.stop_video)
        self.overlay.stopRequested.connect(self.stop_capture_mode)
        self.overlay.show()
        exclude_widget_from_windows_capture(self.overlay)
        self.effect_overlay = ClickEffectOverlay()
        self.effect_overlay.show()
        self.effect_overlay.lower()
        self.annotation_overlay = AnnotationOverlay(
            text_color=self.annotation_text_color,
            highlight_color=self.annotation_highlight_color,
            text_size=self.annotation_text_size,
            text_weight=self.annotation_text_weight,
            highlight_thickness=self.annotation_highlight_thickness,
        )
        self.annotation_overlay.show()
        self.annotation_overlay.raise_()
        self.overlay.raise_()
        self._update_overlay_status()
        self.hide()

    def stop_capture_mode(self) -> None:
        if self.active_recorder is not None:
            self.stop_video()
        self.capture_active = False
        finalize_errors: list[str] = []
        if self.session is not None:
            self.session.log_event("session_stop", "stop")
            finalize_errors = self._finalize_pending_videos()
            self.session.flush()
        if self.overlay is not None:
            self.overlay.close()
            self.overlay = None
        if self.region_overlay is not None:
            self.region_overlay.close()
            self.region_overlay = None
        if self.region_selector is not None:
            self.region_selector.close()
            self.region_selector = None
        if self.annotation_overlay is not None:
            self.annotation_overlay.close()
            self.annotation_overlay = None
        if self.effect_overlay is not None:
            self.effect_overlay.close()
            self.effect_overlay = None
        self.show()
        if self.session is not None:
            if finalize_errors:
                QMessageBox.warning(
                    self,
                    "NB Capture",
                    "Session terminee avec avertissements:\n"
                    f"{self.session.output_dir}\n\n"
                    + "\n".join(finalize_errors),
                )
            else:
                QMessageBox.information(self, "NB Capture", f"Session terminee:\n{self.session.output_dir}")

    def capture_screenshot(self) -> None:
        if not self.capture_active or self.session is None:
            return
        artifact_id, path = self.session.next_screenshot_path()
        self._hide_overlay_briefly()
        pos = cursor_position()
        screenshot_region = self.session.capture_region or screen_region_at(
            pos,
            exclude_taskbar=self.session.config.exclude_taskbar,
        )
        capture_screenshot(
            path,
            region=screenshot_region,
            capture_format=self.session.config.capture_format,
            cursor_position=pos,
            draw_cursor_effect=self.session.config.cursor_effect,
            logo_path=Path(self.session.config.logo_path) if self.session.config.logo_enabled else None,
            logo_position=self.session.config.logo_position,
        )
        artifact = CaptureArtifact(
            artifact_id=artifact_id,
            kind="screenshot",
            path=str(path.relative_to(self.session.output_dir).as_posix()),
            timestamp=datetime.now().isoformat(timespec="seconds"),
            region=screenshot_region,
            source="space_or_button",
        )
        self.session.add_artifact(artifact)
        if self.overlay is not None:
            self.overlay.set_preview(path)
            self._refresh_recent_artifacts()
        if self.effect_overlay is not None and pos is not None:
            self.effect_overlay.pulse(pos)
        self._update_overlay_status()

    def toggle_long_video(self, *, with_audio: bool) -> None:
        if self.active_recorder is not None:
            self.stop_video()
        else:
            self.start_video(with_audio=with_audio, source="toggle")

    def start_video(self, *, with_audio: bool, source: str) -> None:
        if not self.capture_active or self.session is None or self.active_recorder is not None:
            return
        audio_requested = self.session.config.audio_enabled and (with_audio or source == "toggle")
        webcam_requested = self.session.config.webcam_enabled and source != "space_hold"
        artifact_id, path = self.session.next_video_path(with_audio=audio_requested)
        recorder_region = self.session.capture_region
        if recorder_region is None:
            recorder_region = screen_region_at(cursor_position(), exclude_taskbar=self.session.config.exclude_taskbar)
        recorder = ClipRecorder(
            path,
            region=recorder_region,
            capture_format=self.session.config.capture_format,
            with_audio=audio_requested,
            audio_device=self.session.config.audio_device,
            with_webcam=webcam_requested,
            webcam_device=self.session.config.webcam_device,
        )
        try:
            recorder.start()
        except Exception as exc:
            QMessageBox.warning(self, "Video", str(exc))
            return
        notices = []
        if webcam_requested and recorder.with_webcam and recorder.live_plan.notes:
            notices.append(recorder.live_plan.notes)
        if audio_requested and not recorder.with_audio:
            notices.append("Audio non actif: choisissez un autre micro.")
        if webcam_requested and not recorder.with_webcam:
            notices.append("Webcam non active: verifiez le peripherique.")
        if self.overlay is not None:
            self.overlay.set_notice(" ".join(notices))
        self.active_recorder = recorder
        self.active_video_artifact_id = artifact_id
        self.active_video_source = source
        self.active_video_started_at = datetime.now()
        self.active_video_paused = False
        self.session.log_event(
            "video",
            "start",
            artifact_id=artifact_id,
            metadata={
                "with_audio": with_audio,
                "audio_requested": audio_requested,
                "with_webcam": recorder.with_webcam,
                "webcam_requested": webcam_requested,
                "live_plan": recorder.live_plan.notes,
                "source": source,
            },
        )
        self.session.flush()
        self._update_overlay_status()

    def stop_video(self) -> None:
        if self.session is None or self.active_recorder is None:
            return
        recorder = self.active_recorder
        duration = recorder.stop()
        kind = "video_audio" if recorder.with_audio else "video"
        artifact = CaptureArtifact(
            artifact_id=self.active_video_artifact_id,
            kind=kind,
            path=str(recorder.final_destination.relative_to(self.session.output_dir).as_posix()),
            timestamp=self.active_video_started_at.isoformat(timespec="seconds"),
            region=recorder.region,
            source=self.active_video_source,
            duration_s=duration,
            metadata={
                "audio_active": recorder.with_audio,
                "webcam_active": recorder.with_webcam,
                "recording_mode": recorder.recording_mode,
                "overlay_width": recorder.live_plan.overlay_width,
                "live_plan": recorder.live_plan.notes,
                "hardware_tier": recorder.hardware_profile.tier,
                "hardware_summary": recorder.hardware_profile.summary,
                "pending_finalize": True,
            },
        )
        self.session.add_artifact(artifact)
        self.active_recorder = None
        self.active_video_artifact_id = ""
        self.active_video_source = ""
        self.active_video_paused = False
        self._refresh_recent_artifacts()
        self._update_overlay_status()

    def _finalize_pending_videos(self) -> list[str]:
        if self.session is None:
            return []
        pending = [
            artifact
            for artifact in self.session.artifacts
            if artifact.kind in {"video", "video_audio"}
        ]
        if not pending:
            return []

        progress = QProgressDialog("Finalisation des videos...", "Annuler", 0, len(pending), self)
        progress.setWindowTitle("NB Capture")
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        errors: list[str] = []

        for index, artifact in enumerate(pending):
            if progress.wasCanceled():
                errors.append("Finalisation annulee par l'utilisateur.")
                break
            progress.setLabelText(f"Finalisation de {artifact.artifact_id}...")
            progress.setValue(index)
            QApplication.processEvents()

            final_path = self.session.output_dir / artifact.path
            tracks = capture_track_paths(final_path)
            raw_path = capture_recording_path(final_path)
            has_capture = (
                (tracks["screen"].exists() and tracks["screen"].stat().st_size > 0)
                or (raw_path.exists() and raw_path.stat().st_size > 0)
            )
            if not has_capture:
                if final_path.exists() and final_path.stat().st_size > 0:
                    artifact.metadata["pending_finalize"] = False
                    progress.setValue(index + 1)
                    continue
                errors.append(f"{artifact.artifact_id}: fichier capture introuvable.")
                progress.setValue(index + 1)
                continue

            try:
                finalize_video(
                    final_path,
                    capture_format=self.session.config.capture_format,
                    with_audio=artifact.kind == "video_audio",
                    with_webcam=bool(artifact.metadata.get("webcam_active")),
                    overlay_width=int(artifact.metadata.get("overlay_width", 320) or 320),
                    logo_path=Path(self.session.config.logo_path) if self.session.config.logo_enabled else None,
                    logo_position=self.session.config.logo_position,
                )
                cleanup_capture_sidecars(final_path)
                artifact.metadata["pending_finalize"] = False
            except Exception as exc:
                errors.append(f"{artifact.artifact_id}: {exc}")

            progress.setValue(index + 1)
            QApplication.processEvents()

        progress.setValue(len(pending))
        return errors

    def start_zone_selector(self, *, armed_by_ctrl: bool = False) -> None:
        if not self.capture_active or self.session is None:
            return
        if self.region_selector is not None:
            self.region_selector.close()
        self.ctrl_zone_armed = armed_by_ctrl
        self.region_selector = RegionSelectorOverlay(cursor_position())
        self.region_selector.regionSelected.connect(self._apply_selected_region)
        self.region_selector.cancelled.connect(self._close_region_selector)
        self.region_selector.show()
        exclude_widget_from_windows_capture(self.region_selector)
        self.region_selector.raise_()
        self.region_selector.activateWindow()

    def _apply_selected_region(self, region: CaptureRegion) -> None:
        if self.session is None:
            return
        self.ctrl_zone_armed = False
        self.ctrl_zone_completed_this_hold = True
        self.session.set_capture_region(region)
        self._update_region_preview(
            (region.left, region.top),
            (region.left + region.width, region.top + region.height),
            fixed=True,
        )
        if self.region_overlay is not None:
            self.region_overlay.show_clear_control(True)
        self._close_region_selector()
        self._update_overlay_status()

    def _close_region_selector(self) -> None:
        if self.ctrl_zone_armed:
            self.ctrl_zone_completed_this_hold = True
        self.ctrl_zone_armed = False
        if self.region_selector is not None:
            self.region_selector.close()
            self.region_selector = None

    def _refresh_recent_artifacts(self) -> None:
        if self.overlay is None or self.session is None:
            return
        items = [(artifact, self.session.output_dir / artifact.path) for artifact in self.session.artifacts[-3:]]
        self.overlay.set_recent_artifacts(items)

    def _show_artifact_lightbox(self, artifact: CaptureArtifact, path: Path) -> None:
        dialog = ArtifactLightbox(artifact, path, self)
        dialog.exec()

    def _open_artifact_folder(self, _artifact: CaptureArtifact, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _open_session_folder(self) -> None:
        if self.session is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.output_dir)))

    def _poll_keys(self) -> None:
        if not self.capture_active:
            return
        now = int(datetime.now().timestamp() * 1000)
        esc = is_virtual_key_down(0x1B)
        ctrl = is_virtual_key_down(0x11) or is_virtual_key_down(0xA2) or is_virtual_key_down(0xA3)
        shift = is_virtual_key_down(0x10)
        alt = is_virtual_key_down(0x12) or is_virtual_key_down(0xA4) or is_virtual_key_down(0xA5)
        space = is_virtual_key_down(0x20)
        left = is_virtual_key_down(0x01)
        right = is_virtual_key_down(0x02)

        if esc:
            self.stop_capture_mode()
            return

        if ctrl and space and shift:
            if not self.shift_ctrl_space_down:
                self.toggle_long_video(with_audio=True)
            self.shift_ctrl_space_down = True
            self.space_down = True
            return
        self.shift_ctrl_space_down = False

        if ctrl and space:
            if not self.ctrl_space_down:
                self.toggle_long_video(with_audio=False)
            self.ctrl_space_down = True
            self.space_down = True
            return
        self.ctrl_space_down = False

        self._poll_annotation_keys(alt=alt, left=left, now_ms=now)
        self._poll_zone_keys(ctrl=ctrl, left=left, now_ms=now)
        self._poll_space_key(space=space, now_ms=now)
        if self.region_selector is None:
            self._poll_click_effect(left=left, right=right)

    def _poll_annotation_keys(self, *, alt: bool, left: bool, now_ms: int) -> None:
        if self.annotation_overlay is None:
            return
        if alt and not self.alt_down:
            self.alt_pressed_ms = now_ms
            self.alt_highlight_started = False
        elif alt and self.alt_down and not self.alt_highlight_started:
            if now_ms - self.alt_pressed_ms >= 260:
                self.annotation_overlay.start_highlighter()
                self.alt_highlight_started = True
        elif not alt and self.alt_down:
            if self.alt_highlight_started:
                self.annotation_overlay.stop_highlighter()
            else:
                self.annotation_overlay.toggle_text_cursor(cursor_position())
            self.alt_pressed_ms = 0
            self.alt_highlight_started = False
        if alt and self.alt_highlight_started:
            pos = cursor_position()
            if left and pos is not None:
                self.annotation_overlay.add_highlight_point(pos)
            else:
                self.annotation_overlay.end_highlight_stroke()
        self.alt_down = alt

    def _poll_space_key(self, *, space: bool, now_ms: int) -> None:
        if self.active_recorder is not None and self.active_video_source == "toggle":
            if space and not self.space_down:
                self.toggle_video_pause()
            self.space_down = space
            self.space_pressed_ms = 0
            self.space_hold_started = False
            return
        if space and not self.space_down:
            self.space_pressed_ms = now_ms
            self.space_hold_started = False
        elif space and self.space_down and not self.space_hold_started:
            if now_ms - self.space_pressed_ms >= 450:
                self.start_video(with_audio=False, source="space_hold")
                self.space_hold_started = True
        elif not space and self.space_down:
            if self.space_hold_started:
                if self.active_recorder is not None and self.active_video_source == "space_hold":
                    self.stop_video()
            else:
                self.capture_screenshot()
            self.space_pressed_ms = 0
            self.space_hold_started = False
        self.space_down = space

    def _poll_zone_keys(self, *, ctrl: bool, left: bool, now_ms: int) -> None:
        if self.session is None:
            return
        if ctrl and self.ctrl_down_since_ms is None:
            self.ctrl_down_since_ms = now_ms
        if not ctrl:
            if (self.ctrl_drag_active or self.ctrl_zone_armed) and self.region_selector is not None:
                self._close_region_selector()
            self.ctrl_down_since_ms = None
            self.ctrl_clear_triggered = False
            self.ctrl_drag_start = None
            self.ctrl_drag_active = False
            self.ctrl_zone_completed_this_hold = False
            self.zone_left_down = False
            return
        pos = cursor_position()
        if (
            self.region_selector is None
            and not left
            and not self.ctrl_clear_triggered
            and not self.ctrl_zone_completed_this_hold
        ):
            self.start_zone_selector(armed_by_ctrl=True)
        if self.ctrl_zone_armed and self.region_selector is not None:
            if left and not self.zone_left_down and pos is not None:
                self.ctrl_zone_armed = False
                self.ctrl_drag_start = pos
                self.ctrl_drag_active = True
                if not self.region_selector.contains_global(pos):
                    self.region_selector.close()
                    self.region_selector = RegionSelectorOverlay(pos)
                    self.region_selector.regionSelected.connect(self._apply_selected_region)
                    self.region_selector.cancelled.connect(self._close_region_selector)
                    self.region_selector.show()
                    exclude_widget_from_windows_capture(self.region_selector)
                    self.region_selector.raise_()
                self.region_selector.begin_from_global(pos)
                self.zone_left_down = True
                return
            else:
                self.zone_left_down = left
                return
        if ctrl and left and not self.zone_left_down and pos is not None:
            self.ctrl_drag_start = pos
            self.ctrl_drag_active = True
            self.start_zone_selector()
            if self.region_selector is not None:
                self.region_selector.begin_from_global(pos)
        elif ctrl and left and self.ctrl_drag_active and self.ctrl_drag_start and pos is not None:
            if self.region_selector is not None:
                self.region_selector.update_from_global(pos)
        elif ctrl and not left and self.zone_left_down and self.ctrl_drag_active and self.ctrl_drag_start and pos is not None:
            if self.region_selector is not None:
                region = self.region_selector.finish_from_global(pos)
            else:
                region = _region_from_points(self.ctrl_drag_start, pos)
            if region.width >= 20 and region.height >= 20:
                self._apply_selected_region(region)
            else:
                self._close_region_selector()
            self.ctrl_drag_start = None
            self.ctrl_drag_active = False
            self._update_overlay_status()
        self.zone_left_down = left

    def _poll_click_effect(self, *, left: bool, right: bool) -> None:
        if self.session is None:
            return
        if left and not self.click_left_down:
            pos = cursor_position()
            if pos is not None:
                self.session.log_event("mouse", "left_click", x=pos[0], y=pos[1])
                if self.effect_overlay is not None and self.session.config.cursor_effect:
                    self.effect_overlay.pulse(pos)
        if right and not self.right_down:
            pos = cursor_position()
            if pos is not None:
                self.session.log_event("mouse", "right_click", x=pos[0], y=pos[1])
        self.click_left_down = left
        self.right_down = right

    def _read_config(self) -> CaptureExpressConfig:
        profile = str(self.profile_combo.currentData() or "source")
        width = height = None
        if profile == "720p":
            width, height = 1280, 720
        elif profile == "1080p":
            width, height = 1920, 1080
        return CaptureExpressConfig(
            output_root=Path(self.output_input.text().strip() or "runs/capture-express"),
            session_title=self.title_input.text().strip() or "capture",
            capture_format=CaptureFormat(
                video_profile=profile,
                video_quality=str(self.quality_combo.currentData() or "fast"),
                screenshot_format=self.screenshot_combo.currentText().strip() or "png",
                fps=int(self.fps_spin.value()),
                target_width=width,
                target_height=height,
            ),
            cursor_effect=self.cursor_effect_check.isChecked(),
            audio_enabled=self.audio_check.isChecked(),
            audio_device=self.audio_device_input.currentText().strip() or "default",
            webcam_enabled=self.webcam_check.isChecked(),
            webcam_device=self.webcam_device_input.currentText().strip(),
            exclude_taskbar=self.exclude_taskbar_check.isChecked(),
            capture_source=str(self.source_combo.currentData() or "screen"),
            initial_region=self._initial_capture_region(),
            logo_enabled=self.logo_check.isChecked() and bool(self.logo_path_input.text().strip()),
            logo_path=self.logo_path_input.text().strip(),
            logo_position=str(self.logo_position_combo.currentData() or "top_left"),  # type: ignore[arg-type]
        )

    def _toggle_text_format(self, mode: str) -> None:
        cursor = self.script_input.textCursor()
        fmt = QTextCharFormat()
        current = self.script_input.currentCharFormat()
        if mode == "bold":
            fmt.setFontWeight(400 if current.fontWeight() > 400 else 700)
        elif mode == "italic":
            fmt.setFontItalic(not current.fontItalic())
        elif mode == "underline":
            fmt.setFontUnderline(not current.fontUnderline())
        cursor.mergeCharFormat(fmt)
        self.script_input.mergeCurrentCharFormat(fmt)
        self.script_input.setTextCursor(cursor)
        self.script_input.setFocus()

    def _choose_script_color(self) -> None:
        color = QColorDialog.getColor(self.script_input.textColor(), self, "Couleur du texte")
        if not color.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self.script_input.textCursor().mergeCharFormat(fmt)
        self.script_input.mergeCurrentCharFormat(fmt)
        self.script_input.setFocus()

    def _choose_annotation_text_color(self) -> None:
        color = QColorDialog.getColor(self.annotation_text_color, self, "Couleur du texte d'annotation")
        if not color.isValid():
            return
        self.annotation_text_color = color
        self._sync_annotation_color_buttons()
        self._apply_annotation_colors()

    def _choose_annotation_highlight_color(self) -> None:
        color = QColorDialog.getColor(self.annotation_highlight_color, self, "Couleur du surligneur")
        if not color.isValid():
            return
        self.annotation_highlight_color = color
        self._sync_annotation_color_buttons()
        self._apply_annotation_colors()

    def _annotation_style_changed(self) -> None:
        self.annotation_text_size = int(self.annotation_text_size_spin.value())
        self.annotation_text_weight = int(self.annotation_text_weight_combo.currentData() or 400)
        self.annotation_highlight_thickness = int(self.annotation_highlight_thickness_spin.value())
        self._apply_annotation_style()

    def _sync_annotation_color_buttons(self) -> None:
        self.annotation_text_color_btn.setStyleSheet(self._annotation_color_button_style(self.annotation_text_color))
        self.annotation_highlight_color_btn.setStyleSheet(
            self._annotation_color_button_style(self.annotation_highlight_color)
        )

    def _annotation_color_button_style(self, color: QColor) -> str:
        text_color = "#ffffff" if color.lightness() < 140 else "#111827"
        return (
            f"background: {color.name()}; color: {text_color}; border: 1px solid #64748b; "
            "border-radius: 5px; padding: 6px 10px; font-weight: 700;"
        )

    def _apply_annotation_colors(self) -> None:
        if self.annotation_overlay is not None:
            self.annotation_overlay.set_annotation_colors(
                text_color=self.annotation_text_color,
                highlight_color=self.annotation_highlight_color,
            )

    def _apply_annotation_style(self) -> None:
        if self.annotation_overlay is not None:
            self.annotation_overlay.set_annotation_style(
                text_size=self.annotation_text_size,
                text_weight=self.annotation_text_weight,
                highlight_thickness=self.annotation_highlight_thickness,
            )

    def _adjust_script_font_size(self, delta: int) -> None:
        current = self.script_input.currentFont().pointSize()
        if current <= 0:
            current = 14
        self.script_input.setFontPointSize(max(8, min(36, current + delta)))

    def toggle_video_pause(self) -> None:
        if self.session is None or self.active_recorder is None:
            return
        if self.active_recorder.is_paused:
            if self.active_recorder.resume():
                self.active_video_paused = False
                self.session.log_event("video", "resume", artifact_id=self.active_video_artifact_id)
        else:
            if self.active_recorder.pause():
                self.active_video_paused = True
                self.session.log_event("video", "pause", artifact_id=self.active_video_artifact_id)
        self.session.flush()
        self._update_overlay_status()

    def _populate_window_combo(self) -> None:
        current = self.window_combo.currentData()
        self.window_combo.clear()
        windows = list_window_infos()
        if not windows:
            self.window_combo.addItem("Aucune fenetre detectee", None)
            return
        for info in windows:
            label = f"{info.title} ({info.region.width}x{info.region.height})"
            self.window_combo.addItem(label, info.handle)
        if current is not None:
            index = self.window_combo.findData(current)
            if index >= 0:
                self.window_combo.setCurrentIndex(index)

    def _populate_audio_devices(self) -> None:
        current = self.audio_device_input.currentText().strip() or "default"
        self.audio_device_input.clear()
        devices = list_directshow_audio_devices()
        if devices:
            for device in devices:
                self.audio_device_input.addItem(device)
        else:
            self.audio_device_input.addItem("default")
        if current == "default" and devices:
            self.audio_device_input.setCurrentIndex(0)
            return
        index = self.audio_device_input.findText(current)
        if index >= 0:
            self.audio_device_input.setCurrentIndex(index)
        else:
            self.audio_device_input.setEditText(current)

    def _populate_webcam_devices(self) -> None:
        current = self.webcam_device_input.currentText().strip()
        self.webcam_device_input.clear()
        devices = list_directshow_video_devices()
        if devices:
            for device in devices:
                self.webcam_device_input.addItem(device)
        else:
            self.webcam_device_input.addItem("default")
        if current:
            index = self.webcam_device_input.findText(current)
            if index >= 0:
                self.webcam_device_input.setCurrentIndex(index)
            else:
                self.webcam_device_input.setEditText(current)

    def _sync_capture_source_controls(self) -> None:
        self._sync_option_visibility()

    def _sync_option_visibility(self) -> None:
        source = str(self.source_combo.currentData() or "screen")
        window_enabled = source == "window"
        screen_enabled = source == "screen"
        audio_enabled = self.audio_check.isChecked()
        webcam_enabled = self.webcam_check.isChecked()
        logo_enabled = self.logo_check.isChecked()
        self.window_label.setVisible(window_enabled)
        self.window_picker.setVisible(window_enabled)
        self.exclude_taskbar_check.setVisible(screen_enabled)
        self.audio_label.setVisible(audio_enabled)
        self.audio_picker.setVisible(audio_enabled)
        self.webcam_label.setVisible(webcam_enabled)
        self.webcam_picker.setVisible(webcam_enabled)
        self.logo_picker.setVisible(logo_enabled)
        self.logo_position_label.setVisible(logo_enabled)
        self.logo_position_combo.setVisible(logo_enabled)

    def _initial_capture_region(self) -> CaptureRegion | None:
        source = str(self.source_combo.currentData() or "screen")
        if source == "window":
            handle = self.window_combo.currentData()
            info = window_info_by_handle(int(handle or 0))
            return info.region if info is not None else None
        return None

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Dossier des captures", self.output_input.text())
        if directory:
            self.output_input.setText(directory)

    def _choose_logo(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir un logo",
            self.logo_path_input.text().strip(),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;Tous les fichiers (*.*)",
        )
        if filename:
            self.logo_path_input.setText(filename)
            self.logo_check.setChecked(True)
            self._sync_option_visibility()

    def _hide_overlay_briefly(self) -> None:
        if self.overlay is not None:
            self.overlay.hide()
            QApplication.processEvents()
            QTimer.singleShot(160, self.overlay.show)

    def _update_overlay_status(self) -> None:
        if self.overlay is None:
            return
        region = self.session.capture_region if self.session is not None else None
        self.overlay.update_status(
            recording=self.active_recorder is not None,
            paused=self.active_video_paused,
            region_active=region is not None,
            script_html=self.script_input.toHtml() if self.script_input.toPlainText().strip() else "",
            script_visible=self.show_script_check.isChecked(),
            output_dir=str(self.session.output_dir) if self.session is not None else "",
        )

    def _ensure_region_overlay(self) -> None:
        if self.region_overlay is None:
            self.region_overlay = RegionOverlay(cursor_position())
            self.region_overlay.clearRequested.connect(self._clear_capture_region)
            self.region_overlay.show()
            exclude_widget_from_windows_capture(self.region_overlay)
        else:
            self.region_overlay.move_to_cursor_screen(cursor_position())

    def _update_region_preview(self, start: tuple[int, int], end: tuple[int, int], *, fixed: bool = False) -> None:
        self._ensure_region_overlay()
        if self.region_overlay is not None:
            self.region_overlay.set_region(_region_from_points(start, end), fixed=fixed)

    def _show_clear_region_control(self, region: CaptureRegion) -> None:
        self._ensure_region_overlay()
        if self.region_overlay is not None:
            self.region_overlay.set_region(region, fixed=True)
            self.region_overlay.show_clear_control(True)

    def _clear_capture_region(self) -> None:
        if self.session is not None:
            self.session.set_capture_region(None)
        self.ctrl_zone_completed_this_hold = False
        self.ctrl_clear_triggered = False
        if self.region_overlay is not None:
            self.region_overlay.show_clear_control(False)
            self.region_overlay.clear_region()
        if self.annotation_overlay is not None:
            self.annotation_overlay.clear_annotations()
        self._update_overlay_status()

    def _clear_annotations(self) -> None:
        if self.annotation_overlay is not None:
            self.annotation_overlay.clear_annotations()


class FloatingCaptureMenu(QWidget):
    screenshotRequested = pyqtSignal()
    videoRequested = pyqtSignal()
    videoAudioRequested = pyqtSignal()
    zoneRequested = pyqtSignal()
    clearZoneRequested = pyqtSignal()
    clearAnnotationsRequested = pyqtSignal()
    artifactOpenRequested = pyqtSignal(object, object)
    artifactFolderRequested = pyqtSignal(object, object)
    sessionFolderRequested = pyqtSignal()
    stopRequested = pyqtSignal()
    stopVideoRequested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("NB Capture")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._script_key = ""
        self._scroll_started_at: float | None = None
        self._scroll_paused_at: float | None = None
        self._scroll_paused_total = 0.0
        self._script_scroll_duration_s = 45.0
        self._prompter_timer = QTimer(self)
        self._prompter_timer.setInterval(120)
        self._prompter_timer.timeout.connect(self._advance_script_scroll)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.status = QLabel("Capture active")
        self.status.setStyleSheet("font-weight: 700; color: #f8fafc;")
        self.recording_badge = QLabel("● ENREGISTREMENT EN COURS")
        self.recording_badge.setStyleSheet("font-weight: 800; color: #ef4444;")
        self.recording_badge.hide()
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color: #fbbf24; font-weight: 700;")
        self.notice.hide()
        self.output = QLabel("")
        self.output.setWordWrap(True)
        self.output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_row = QHBoxLayout()
        zone_row = QHBoxLayout()
        screenshot_btn = QPushButton("Screenshot")
        video_btn = QPushButton("Enregistrer video")
        video_audio_btn = QPushButton("Video + son")
        zone_btn = QPushButton("Definir zone")
        clear_zone_btn = QPushButton("Effacer zone")
        clear_annotations_btn = QPushButton("Effacer annotations")
        open_session_btn = QPushButton("Dossier session")
        stop_btn = QPushButton("Quitter capture active")
        self.stop_record_btn = QPushButton("Stop enregistrement")
        self.stop_record_btn.setStyleSheet("background: #7f1d1d; border-color: #ef4444; color: #ffffff; font-weight: 800;")
        self.stop_record_btn.hide()
        screenshot_btn.clicked.connect(self.screenshotRequested.emit)
        video_btn.clicked.connect(self.videoRequested.emit)
        video_audio_btn.clicked.connect(self.videoAudioRequested.emit)
        zone_btn.clicked.connect(self.zoneRequested.emit)
        clear_zone_btn.clicked.connect(self.clearZoneRequested.emit)
        clear_annotations_btn.clicked.connect(self.clearAnnotationsRequested.emit)
        open_session_btn.clicked.connect(self.sessionFolderRequested.emit)
        stop_btn.clicked.connect(self.stopRequested.emit)
        self.stop_record_btn.clicked.connect(self.stopVideoRequested.emit)
        for btn in (screenshot_btn, video_btn, video_audio_btn):
            btn_row.addWidget(btn)
        for btn in (zone_btn, clear_zone_btn, clear_annotations_btn, open_session_btn, self.stop_record_btn, stop_btn):
            zone_row.addWidget(btn)
        layout.addWidget(self.status)
        layout.addWidget(self.recording_badge)
        layout.addWidget(self.notice)
        layout.addLayout(btn_row)
        layout.addLayout(zone_row)
        layout.addWidget(self.output)
        self.script_label = QLabel("Script video")
        self.script_label.setStyleSheet("font-weight: 700; color: #f8fafc;")
        self.script_view = QTextEdit()
        self.script_view.setReadOnly(True)
        self.script_view.setMinimumHeight(130)
        self.script_view.setMaximumHeight(220)
        self.script_view.setStyleSheet(
            "background: #020617; border: 1px solid #334155; border-radius: 6px; "
            "color: #f8fafc; font-size: 15px; line-height: 1.35;"
        )
        self.script_label.hide()
        self.script_view.hide()
        layout.addWidget(self.script_label)
        layout.addWidget(self.script_view)
        self.setStyleSheet(
            """
            QWidget { background: #0f172a; color: #e5e7eb; border: 1px solid #334155; border-radius: 8px; font-family: Segoe UI; }
            QPushButton { padding: 6px 8px; border: 1px solid #475569; border-radius: 5px; background: #1e293b; color: #f8fafc; }
            QPushButton:hover { background: #334155; border-color: #60a5fa; }
            QLabel { border: none; color: #e5e7eb; }
            QFrame#recentTile { background: #111827; border: 1px solid #334155; border-radius: 6px; }
            QLabel#muted { color: #94a3b8; }
            """
        )
        self.move(40, 40)
        self.preview = QLabel("Aucun screenshot")
        self.preview.setMinimumSize(220, 124)
        self.preview.setMaximumSize(260, 150)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background: #020617; border: 1px solid #334155; color: #94a3b8;")
        layout.addWidget(self.preview)
        recent_title = QLabel("3 dernieres captures")
        recent_title.setStyleSheet("font-weight: 700; color: #f8fafc;")
        layout.addWidget(recent_title)
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setSpacing(5)
        layout.addLayout(self.recent_layout)
        self.recent_empty = QLabel("Aucune capture")
        self.recent_empty.setObjectName("muted")
        self.recent_layout.addWidget(self.recent_empty)

    def update_status(
        self,
        *,
        recording: bool,
        paused: bool,
        region_active: bool,
        script_html: str,
        script_visible: bool,
        output_dir: str,
    ) -> None:
        state = "PAUSE" if paused else ("REC" if recording else "Capture active")
        region = "zone ON" if region_active else "fenetre/source"
        self.status.setText(f"{state} | {region}")
        self.recording_badge.setVisible(recording and not paused)
        self.stop_record_btn.setVisible(recording)
        self.output.setText(output_dir)
        self._set_script(script_html, script_visible)
        script_shown = not self.script_view.isHidden()
        if recording and not paused and script_shown:
            if self._scroll_started_at is None:
                self._scroll_started_at = time.perf_counter()
                self._scroll_paused_total = 0.0
            if self._scroll_paused_at is not None:
                self._scroll_paused_total += time.perf_counter() - self._scroll_paused_at
                self._scroll_paused_at = None
                self._prompter_timer.start()
            elif not self._prompter_timer.isActive():
                self._prompter_timer.start()
        else:
            self._prompter_timer.stop()
            if paused and recording and script_shown and self._scroll_paused_at is None:
                self._scroll_paused_at = time.perf_counter()
            elif not paused:
                self._scroll_started_at = None
                self._scroll_paused_at = None
                self._scroll_paused_total = 0.0

    def set_notice(self, text: str) -> None:
        self.notice.setText(text)
        self.notice.setVisible(bool(text.strip()))

    def _set_script(self, html: str, requested_visible: bool) -> None:
        cleaned_plain = html.strip()
        visible = requested_visible and bool(cleaned_plain)
        key = html if visible else ""
        if key != self._script_key:
            self._script_key = key
            self._scroll_started_at = None
            self._scroll_paused_at = None
            self._scroll_paused_total = 0.0
            self.script_view.setHtml(html)
            self.script_view.verticalScrollBar().setValue(0)
        self.script_label.setVisible(visible)
        self.script_view.setVisible(visible)

    def _advance_script_scroll(self) -> None:
        if self._scroll_started_at is None:
            return
        scrollbar = self.script_view.verticalScrollBar()
        maximum = scrollbar.maximum()
        if maximum <= 0:
            return
        elapsed = time.perf_counter() - self._scroll_started_at - self._scroll_paused_total
        progress = min(1.0, elapsed / self._script_scroll_duration_s)
        scrollbar.setValue(int(maximum * progress))
        if progress >= 1.0:
            self._prompter_timer.stop()

    def set_preview(self, image_path: Path) -> None:
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.preview.setText("Preview indisponible")
            return
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def set_recent_artifacts(self, items: list[tuple[CaptureArtifact, Path]]) -> None:
        while self.recent_layout.count():
            child = self.recent_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        if not items:
            self.recent_empty = QLabel("Aucune capture")
            self.recent_empty.setObjectName("muted")
            self.recent_layout.addWidget(self.recent_empty)
            return
        for artifact, path in reversed(items):
            tile = RecentArtifactTile(artifact, path)
            tile.openRequested.connect(self.artifactOpenRequested.emit)
            tile.folderRequested.connect(self.artifactFolderRequested.emit)
            self.recent_layout.addWidget(tile)


class RecentArtifactTile(QFrame):
    openRequested = pyqtSignal(object, object)
    folderRequested = pyqtSignal(object, object)

    def __init__(self, artifact: CaptureArtifact, path: Path) -> None:
        super().__init__()
        self.artifact = artifact
        self.path = path
        self.setObjectName("recentTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(7)

        thumb = QLabel()
        thumb.setFixedSize(76, 44)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("background: #020617; border: 1px solid #334155; border-radius: 4px; color: #93c5fd;")
        if artifact.kind == "screenshot":
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                thumb.setPixmap(
                    pixmap.scaled(
                        thumb.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                thumb.setText("IMG")
        else:
            thumb.setText("VIDEO")

        info = QLabel(f"{artifact.artifact_id}\n{artifact.kind}")
        info.setWordWrap(True)
        info.setStyleSheet("color: #e5e7eb; border: none;")
        folder_btn = QPushButton("Dossier")
        folder_btn.setFixedWidth(70)
        folder_btn.clicked.connect(lambda: self.folderRequested.emit(self.artifact, self.path))

        layout.addWidget(thumb)
        layout.addWidget(info, 1)
        layout.addWidget(folder_btn)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit(self.artifact, self.path)
        super().mousePressEvent(event)


class ArtifactLightbox(QDialog):
    def __init__(self, artifact: CaptureArtifact, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.artifact = artifact
        self.path = path
        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None
        self.setWindowTitle(f"Apercu - {artifact.artifact_id}")
        self.resize(860, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel(f"{artifact.artifact_id} | {artifact.kind} | {path.name}")
        title.setStyleSheet("font-weight: 700; color: #f8fafc;")
        layout.addWidget(title)

        if artifact.kind == "screenshot":
            viewer = QLabel("Image indisponible")
            viewer.setAlignment(Qt.AlignmentFlag.AlignCenter)
            viewer.setMinimumSize(760, 420)
            viewer.setStyleSheet("background: #020617; border: 1px solid #334155; color: #94a3b8;")
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                viewer.setPixmap(
                    pixmap.scaled(
                        viewer.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            layout.addWidget(viewer, 1)
        else:
            video_widget = QVideoWidget()
            video_widget.setMinimumSize(760, 420)
            video_widget.setStyleSheet("background: #020617; border: 1px solid #334155;")
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.setVideoOutput(video_widget)
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            layout.addWidget(video_widget, 1)
            QTimer.singleShot(120, self.player.play)

        controls = QHBoxLayout()
        if artifact.kind != "screenshot":
            play_btn = QPushButton("Lecture / pause")
            play_btn.clicked.connect(self._toggle_playback)
            controls.addWidget(play_btn)
        open_file_btn = QPushButton("Ouvrir fichier")
        open_file_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        folder_btn = QPushButton("Ouvrir dossier")
        folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))))
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.accept)
        controls.addStretch(1)
        controls.addWidget(open_file_btn)
        controls.addWidget(folder_btn)
        controls.addWidget(close_btn)
        layout.addLayout(controls)

        self.setStyleSheet(
            """
            QDialog { background: #0f172a; color: #e5e7eb; font-family: Segoe UI, Arial; }
            QLabel { color: #e5e7eb; }
            QPushButton { padding: 7px 10px; border: 1px solid #475569; border-radius: 5px; background: #1e293b; color: #f8fafc; }
            QPushButton:hover { background: #334155; border-color: #60a5fa; }
            """
        )

    def _toggle_playback(self) -> None:
        if self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        if self.player is not None:
            self.player.stop()
        super().closeEvent(event)


class RegionSelectorOverlay(QWidget):
    regionSelected = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(self, cursor_pos: tuple[int, int] | None = None) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("Selection zone")
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.start_point: QPoint | None = None
        self.current_point: QPoint | None = None
        self.shortcut_controlled = False
        self._move_to_cursor_screen(cursor_pos)

    def _move_to_cursor_screen(self, cursor_pos: tuple[int, int] | None) -> None:
        geometry = _qt_screen_geometry_at_physical(cursor_pos)
        if geometry is not None:
            self.setGeometry(geometry)
            return
        qt_pos = _qt_point_from_physical(cursor_pos or (0, 0))
        screen = QApplication.screenAt(qt_pos) or QApplication.primaryScreen()
        geometry = screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self.setGeometry(geometry)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if self.shortcut_controlled:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.start_point = _event_pos(event)
        self.current_point = self.start_point
        self.update()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
        if self.shortcut_controlled:
            return
        if self.start_point is None:
            return
        self.current_point = _event_pos(event)
        self.update()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        if self.shortcut_controlled:
            return
        if event.button() != Qt.MouseButton.LeftButton or self.start_point is None:
            return
        self.current_point = _event_pos(event)
        region = self._selected_region()
        if region is not None and region.width >= 20 and region.height >= 20:
            self.regionSelected.emit(region)
        else:
            self.cancelled.emit()

    def begin_from_global(self, position: tuple[int, int]) -> None:
        self.shortcut_controlled = True
        self.start_point = self._local_from_global(position)
        self.current_point = self.start_point
        self.update()

    def update_from_global(self, position: tuple[int, int]) -> None:
        if self.start_point is None:
            self.begin_from_global(position)
            return
        self.current_point = self._local_from_global(position)
        self.update()

    def finish_from_global(self, position: tuple[int, int]) -> CaptureRegion:
        self.update_from_global(position)
        region = self._selected_region()
        self.shortcut_controlled = False
        return region or CaptureRegion(position[0], position[1], 0, 0)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(15, 23, 42, 70))
        painter.setPen(QPen(QColor(255, 255, 255, 215), 1))
        painter.drawText(18, 28, "Glissez pour definir la zone. Echap ou clic droit pour annuler.")
        if self.start_point is not None and self.current_point is not None:
            rect = QRect(self.start_point, self.current_point).normalized()
            painter.fillRect(rect, QColor(37, 99, 235, 28))
            painter.setPen(QPen(QColor(37, 99, 235, 230), 3))
            painter.drawRect(rect)
        painter.end()

    def _selected_region(self) -> CaptureRegion | None:
        if self.start_point is None or self.current_point is None:
            return None
        geom = self.geometry()
        start = _physical_point_from_qt(QPoint(geom.left() + self.start_point.x(), geom.top() + self.start_point.y()))
        end = _physical_point_from_qt(QPoint(geom.left() + self.current_point.x(), geom.top() + self.current_point.y()))
        return _region_from_points(start, end)

    def _local_from_global(self, position: tuple[int, int]) -> QPoint:
        geom = self.geometry()
        qt_pos = _qt_point_from_physical(position)
        return QPoint(qt_pos.x() - geom.left(), qt_pos.y() - geom.top())

    def contains_global(self, position: tuple[int, int]) -> bool:
        return self.geometry().contains(_qt_point_from_physical(position))


class RegionOverlay(QWidget):
    clearRequested = pyqtSignal()

    def __init__(self, cursor_pos: tuple[int, int] | None = None) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.move_to_cursor_screen(cursor_pos)
        self.region: CaptureRegion | None = None
        self.fixed = False
        self.clear_button = QPushButton("X")
        self.clear_button.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.clear_button.setFixedSize(28, 28)
        self.clear_button.setToolTip("Revenir en capture plein ecran")
        self.clear_button.setStyleSheet(
            """
            QPushButton {
                background: #111827;
                border: 2px solid #ffffff;
                border-radius: 14px;
                color: #ffffff;
                font-weight: 800;
            }
            QPushButton:hover {
                background: #dc2626;
            }
            """
        )
        self.clear_button.clicked.connect(self.clearRequested.emit)
        self.clear_button.hide()

    def move_to_cursor_screen(self, cursor_pos: tuple[int, int] | None) -> None:
        virtual_geometry = _virtual_desktop_geometry()
        if virtual_geometry is not None:
            self.setGeometry(virtual_geometry)
            return
        geometry_tuple = screen_geometry_at(cursor_pos)
        if geometry_tuple is not None:
            left, top, width, height = geometry_tuple
            self.setGeometry(QRect(left, top, width, height))
            return
        screen = QApplication.screenAt(QPoint(*(cursor_pos or (0, 0)))) or QApplication.primaryScreen()
        geometry = screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self.setGeometry(geometry)

    def set_region(self, region: CaptureRegion, *, fixed: bool) -> None:
        self.region = region
        self.fixed = fixed
        self.update()

    def clear_region(self) -> None:
        self.region = None
        self.clear_button.hide()
        self.update()

    def show_clear_control(self, visible: bool) -> None:
        if self.region is None:
            return
        qt_pos = _qt_point_from_physical((self.region.left + self.region.width, self.region.top))
        x = max(self.geometry().left(), qt_pos.x() - self.clear_button.width() // 2)
        y = max(self.geometry().top(), qt_pos.y() - self.clear_button.height() // 2)
        self.clear_button.move(x, y)
        self.clear_button.setVisible(visible)
        if visible:
            self.clear_button.raise_()

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        self.clear_button.close()
        super().closeEvent(event)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        if self.region is None:
            return
        painter = QPainter(self)
        color = QColor(37, 99, 235, 210 if self.fixed else 150)
        pen = QPen(color, 3)
        painter.setPen(pen)
        x, y = self._to_local(self.region.left, self.region.top)
        painter.drawRect(x, y, self.region.width, self.region.height)
        painter.end()

    def _to_local(self, x: int, y: int) -> tuple[int, int]:
        geom = self.geometry()
        qt_pos = _qt_point_from_physical((x, y))
        return qt_pos.x() - geom.left(), qt_pos.y() - geom.top()


class AnnotationOverlay(QWidget):
    def __init__(
        self,
        *,
        text_color: QColor,
        highlight_color: QColor,
        text_size: int,
        text_weight: int,
        highlight_thickness: int,
    ) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        screen = QApplication.primaryScreen()
        geometry = screen.virtualGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self.setGeometry(geometry)
        self.text_color = QColor(text_color)
        self.highlight_color = QColor(highlight_color)
        self.text_size = text_size
        self.text_weight = text_weight
        self.highlight_thickness = highlight_thickness
        self.text_boxes: list[QTextEdit] = []
        self.active_editor: QTextEdit | None = None
        self.highlighting = False
        self.highlight_strokes: list[list[QPoint]] = []
        self._last_highlight_point: QPoint | None = None
        self.clear_button = QPushButton("X")
        self.clear_button.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.clear_button.setFixedSize(24, 24)
        self.clear_button.setToolTip("Effacer les annotations")
        self.clear_button.setStyleSheet(
            """
            QPushButton {
                background: #111827;
                border: 2px solid #ffffff;
                border-radius: 12px;
                color: #ffffff;
                font-weight: 800;
                padding: 0;
            }
            QPushButton:hover {
                background: #dc2626;
            }
            """
        )
        self.clear_button.clicked.connect(self.clear_annotations)
        self.clear_button.hide()
        self._position_clear_button()

    def set_annotation_colors(self, *, text_color: QColor, highlight_color: QColor) -> None:
        self.text_color = QColor(text_color)
        self.highlight_color = QColor(highlight_color)
        for editor in self.text_boxes:
            editor.setTextColor(self.text_color)
            self._apply_editor_font(editor)
            if editor is self.active_editor:
                self._style_active_editor(editor)
            else:
                self._style_finished_editor(editor)
        self.update()

    def set_annotation_style(self, *, text_size: int, text_weight: int, highlight_thickness: int) -> None:
        self.text_size = text_size
        self.text_weight = text_weight
        self.highlight_thickness = highlight_thickness
        for editor in self.text_boxes:
            self._apply_editor_font(editor)
            self._resize_editor(editor)
        self.update()

    def toggle_text_cursor(self, position: tuple[int, int] | None) -> None:
        if self.active_editor is not None:
            self._deactivate_editor()
            return
        if position is None:
            position = _physical_point_from_qt(QCursor.pos())
        self._activate_new_editor(position)

    def start_highlighter(self) -> None:
        self._deactivate_editor()
        self.highlighting = True
        self._last_highlight_point = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.raise_()
        self._raise_clear_button()

    def stop_highlighter(self) -> None:
        self.highlighting = False
        self._last_highlight_point = None
        self.unsetCursor()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.active_editor is None)
        self._sync_clear_button()
        self.update()

    def add_highlight_point(self, position: tuple[int, int]) -> None:
        point = self._local_from_physical(position)
        if self._last_highlight_point is not None:
            dx = point.x() - self._last_highlight_point.x()
            dy = point.y() - self._last_highlight_point.y()
            if dx * dx + dy * dy < 9:
                return
        if not self.highlight_strokes or self._last_highlight_point is None:
            self.highlight_strokes.append([])
        self.highlight_strokes[-1].append(point)
        self._last_highlight_point = point
        self._sync_clear_button()
        self.update()

    def end_highlight_stroke(self) -> None:
        self._last_highlight_point = None

    def clear_annotations(self) -> None:
        self.highlight_strokes.clear()
        self._last_highlight_point = None
        for editor in self.text_boxes:
            editor.close()
            editor.deleteLater()
        self.text_boxes.clear()
        self.active_editor = None
        self.highlighting = False
        self.unsetCursor()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.clear_button.hide()
        self.update()

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        self.clear_button.close()
        super().closeEvent(event)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        if not self.highlight_strokes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(self.highlight_color)
        color.setAlpha(130)
        pen = QPen(color, self.highlight_thickness)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for stroke in self.highlight_strokes:
            if len(stroke) == 1:
                painter.drawPoint(stroke[0])
            elif len(stroke) > 1:
                painter.drawPolyline(stroke)
        painter.end()

    def _activate_new_editor(self, position: tuple[int, int]) -> None:
        editor = QTextEdit(self)
        editor.setAcceptRichText(False)
        self._apply_editor_font(editor)
        editor.setTextColor(self.text_color)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._style_active_editor(editor)
        point = self._local_from_physical(position)
        editor.setGeometry(point.x(), point.y(), 340, 92)
        editor.textChanged.connect(lambda editor=editor: self._resize_editor(editor))
        editor.show()
        editor.raise_()
        editor.setFocus()
        self.text_boxes.append(editor)
        self.active_editor = editor
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.raise_()
        self._sync_clear_button()

    def _deactivate_editor(self) -> None:
        if self.active_editor is None:
            return
        editor = self.active_editor
        if not editor.toPlainText().strip():
            editor.close()
            editor.deleteLater()
            if editor in self.text_boxes:
                self.text_boxes.remove(editor)
        else:
            editor.setReadOnly(True)
            editor.clearFocus()
            self._style_finished_editor(editor)
        self.active_editor = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not self.highlighting)
        self._sync_clear_button()

    def _resize_editor(self, editor: QTextEdit) -> None:
        document_size = editor.document().size().toSize()
        width = min(520, max(180, document_size.width() + 28))
        height = min(220, max(52, document_size.height() + 18))
        editor.resize(width, height)

    def _apply_editor_font(self, editor: QTextEdit) -> None:
        font = QFont("Segoe UI", self.text_size)
        font.setWeight(QFont.Weight(self.text_weight))
        editor.setFont(font)

    def _style_active_editor(self, editor: QTextEdit) -> None:
        border_color = QColor(self.text_color)
        border_color.setAlpha(160)
        editor.setStyleSheet(
            f"""
            QTextEdit {{
                background: rgba(255, 255, 255, 18);
                border: 1px solid rgba({border_color.red()}, {border_color.green()}, {border_color.blue()}, 160);
                color: {self.text_color.name()};
                font-size: {self.text_size}px;
                font-weight: {self.text_weight};
                padding: 3px;
            }}
            """
        )

    def _style_finished_editor(self, editor: QTextEdit) -> None:
        editor.setStyleSheet(
            f"""
            QTextEdit {{
                background: transparent;
                border: 0;
                color: {self.text_color.name()};
                font-size: {self.text_size}px;
                font-weight: {self.text_weight};
                padding: 3px;
            }}
            """
        )

    def _position_clear_button(self) -> None:
        x = max(self.geometry().left() + 48, self.geometry().right() - self.clear_button.width() - 64)
        y = self.geometry().top() + 64
        self.clear_button.move(x, y)

    def _sync_clear_button(self) -> None:
        visible = bool(self.highlight_strokes or self.text_boxes)
        self._position_clear_button()
        self.clear_button.setVisible(visible)
        if visible:
            self._raise_clear_button()

    def _raise_clear_button(self) -> None:
        if self.clear_button.isVisible():
            self.clear_button.raise_()

    def _local_from_physical(self, position: tuple[int, int]) -> QPoint:
        geom = self.geometry()
        qt_pos = _qt_point_from_physical(position)
        return QPoint(qt_pos.x() - geom.left(), qt_pos.y() - geom.top())


class ClickEffectOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        screen = QApplication.primaryScreen()
        geometry = screen.virtualGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self.setGeometry(geometry)
        self.pulses: list[tuple[QPoint, int]] = []
        self.timer = QTimer(self)
        self.timer.setInterval(35)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def pulse(self, position: tuple[int, int]) -> None:
        geom = self.geometry()
        self.pulses.append((QPoint(position[0] - geom.left(), position[1] - geom.top()), 0))
        self.raise_()
        self.update()

    def _tick(self) -> None:
        self.pulses = [(point, age + 1) for point, age in self.pulses if age < 16]
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        if not self.pulses:
            return
        painter = QPainter(self)
        for point, age in self.pulses:
            radius = 10 + age * 4
            alpha = max(0, 150 - age * 9)
            painter.setPen(QPen(QColor(37, 99, 235, alpha), 4))
            painter.drawEllipse(point, radius, radius)
        painter.end()


def _region_from_points(start: tuple[int, int], end: tuple[int, int]) -> CaptureRegion:
    left = min(start[0], end[0])
    top = min(start[1], end[1])
    return CaptureRegion(left=left, top=top, width=abs(end[0] - start[0]), height=abs(end[1] - start[1]))


def _event_pos(event: Any) -> QPoint:
    position = getattr(event, "position", None)
    if callable(position):
        return position().toPoint()
    return event.pos()


def _qt_screen_geometry_at_physical(position: tuple[int, int] | None) -> QRect | None:
    if position is None:
        return None
    screen = _qt_screen_at_physical(position)
    return screen.geometry() if screen is not None else None


def _qt_screen_at_physical(position: tuple[int, int]):
    for screen in QApplication.screens():
        if _physical_rect_for_screen(screen).contains(QPoint(position[0], position[1])):
            return screen
    return None


def _qt_point_from_physical(position: tuple[int, int]) -> QPoint:
    screen = _qt_screen_at_physical(position)
    if screen is None:
        return QPoint(position[0], position[1])
    geometry = screen.geometry()
    physical = _physical_rect_for_screen(screen)
    scale = max(0.01, float(screen.devicePixelRatio()))
    return QPoint(
        int(round(geometry.left() + (position[0] - physical.left()) / scale)),
        int(round(geometry.top() + (position[1] - physical.top()) / scale)),
    )


def _physical_point_from_qt(position: QPoint) -> tuple[int, int]:
    screen = QApplication.screenAt(position) or _qt_screen_nearest(position)
    if screen is None:
        return int(position.x()), int(position.y())
    geometry = screen.geometry()
    physical = _physical_rect_for_screen(screen)
    scale = max(0.01, float(screen.devicePixelRatio()))
    return (
        int(round(physical.left() + (position.x() - geometry.left()) * scale)),
        int(round(physical.top() + (position.y() - geometry.top()) * scale)),
    )


def _qt_screen_nearest(position: QPoint):
    screens = QApplication.screens()
    if not screens:
        return None

    def distance(screen: Any) -> int:
        rect = screen.geometry()
        dx = max(rect.left() - position.x(), 0, position.x() - rect.right())
        dy = max(rect.top() - position.y(), 0, position.y() - rect.bottom())
        return dx * dx + dy * dy

    return min(screens, key=distance)


def _physical_rect_for_screen(screen: Any) -> QRect:
    geometry = screen.geometry()
    scale = max(0.01, float(screen.devicePixelRatio()))
    return QRect(
        geometry.left(),
        geometry.top(),
        int(round(geometry.width() * scale)),
        int(round(geometry.height() * scale)),
    )


def _virtual_desktop_geometry() -> QRect | None:
    screen = QApplication.primaryScreen()
    if screen is None:
        return None
    return screen.virtualGeometry()


def main() -> int:
    configure_process_dpi_awareness()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(
        """
        QWidget {
            background: #ffffff;
            color: #111827;
            font-family: Segoe UI, Arial;
            font-size: 13px;
        }
        QLabel {
            background: transparent;
            color: #111827;
        }
        QGroupBox {
            background: #ffffff;
            color: #111827;
            font-weight: 700;
            border: 1px solid #d8dee9;
            border-radius: 6px;
            margin-top: 8px;
            padding: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            background: #ffffff;
            color: #111827;
        }
        QPushButton {
            padding: 7px 12px;
            border-radius: 5px;
            border: 1px solid #94a3b8;
            background: #ffffff;
            color: #111827;
        }
        QPushButton:hover {
            background: #f1f5f9;
        }
        QPushButton#primary {
            background: #2563eb;
            color: #ffffff;
            border-color: #1d4ed8;
            font-weight: 700;
        }
        QLineEdit, QComboBox, QSpinBox {
            padding: 5px;
            border: 1px solid #cbd5e1;
            border-radius: 4px;
            color: #111827;
            background: #ffffff;
            selection-background-color: #bfdbfe;
            selection-color: #111827;
        }
        QCheckBox {
            background: transparent;
            color: #111827;
        }
        """
    )
    window = CaptureExpressWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
