from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from path_setup import install_paths

install_paths()

from PyQt6.QtWidgets import QApplication
from PIL import Image

import capture_express.app as app_module
import capture_express.media as media_module
from capture_express.app import CaptureExpressWindow, FloatingCaptureMenu, RegionOverlay, _audio_capture_requested
from capture_express.media import (
    ClipRecorder,
    HardwareProfile,
    LiveRecordingPlan,
    VideoEncoder,
    WebcamCaptureMode,
    _build_finalize_command,
    _build_multitrack_finalize_command,
    _cap_screen_fps,
    _parse_directshow_devices,
    _resolve_dshow_device,
    apply_logo_to_image,
    capture_recording_path,
    capture_track_paths,
    classify_gpu_tier,
    parse_webcam_modes,
    plan_quality_recording,
    reset_hardware_profile_cache,
)
from capture_express.models import CaptureFormat, CaptureRegion
from capture_express.windowing import WindowInfo


_QT_APP: QApplication | None = None


def _app() -> QApplication:
    global _QT_APP
    instance = QApplication.instance()
    if instance is not None:
        _QT_APP = instance
    if _QT_APP is None:
        _QT_APP = QApplication([])
    return _QT_APP


class CaptureExpressRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_hardware_profile_cache()

    def tearDown(self) -> None:
        reset_hardware_profile_cache()

    def test_logo_can_be_applied_to_top_left_or_right(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logo_path = Path(tmp) / "logo.png"
            Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(logo_path)
            base = Image.new("RGB", (200, 100), (255, 255, 255))

            left = apply_logo_to_image(base, logo_path, position="top_left", margin=10)
            right = apply_logo_to_image(base, logo_path, position="top_right", margin=10)

            self.assertEqual(left.getpixel((10, 10)), (255, 0, 0))
            self.assertEqual(right.getpixel((180, 10)), (255, 0, 0))

    def test_regular_video_does_not_enable_microphone(self) -> None:
        self.assertFalse(_audio_capture_requested(audio_enabled=True, with_audio=False))
        self.assertTrue(_audio_capture_requested(audio_enabled=True, with_audio=True))

    def test_region_preview_converts_both_physical_corners(self) -> None:
        _app()
        overlay = RegionOverlay()
        with mock.patch.object(overlay, "_to_local", side_effect=[(10, 20), (160, 120)]):
            rect = overlay._local_region_rect(CaptureRegion(100, 200, 300, 200))
        self.assertEqual((rect.left(), rect.top(), rect.width(), rect.height()), (10, 20, 150, 100))
        overlay.close()

    def test_prompter_excludes_paused_time_from_scroll_elapsed(self) -> None:
        _app()
        menu = FloatingCaptureMenu()
        values = iter([100.0, 105.0, 115.0, 116.0, 116.0, 116.0])

        def fake_perf_counter() -> float:
            return next(values, 116.0)

        with mock.patch.object(app_module.time, "perf_counter", side_effect=fake_perf_counter):
            menu.update_status(
                recording=True,
                paused=False,
                region_active=False,
                script_html="<p>Script</p>",
                script_visible=True,
                output_dir="x",
            )
            menu.update_status(
                recording=True,
                paused=True,
                region_active=False,
                script_html="<p>Script</p>",
                script_visible=True,
                output_dir="x",
            )
            menu.update_status(
                recording=True,
                paused=False,
                region_active=False,
                script_html="<p>Script</p>",
                script_visible=True,
                output_dir="x",
            )
            self.assertEqual(menu._scroll_paused_total, 10.0)
        menu._prompter_timer.stop()
        menu.close()

    def test_window_selection_resolves_current_region_from_handle(self) -> None:
        _app()
        window = CaptureExpressWindow()
        window.source_combo.setCurrentIndex(window.source_combo.findData("window"))
        window.window_combo.clear()
        window.window_combo.addItem("Test window", 1234)
        with mock.patch.object(
            app_module,
            "window_info_by_handle",
            return_value=WindowInfo("Test window", CaptureRegion(10, 20, 300, 200), 1234),
        ):
            region = window._initial_capture_region()
        self.assertEqual(region, CaptureRegion(10, 20, 300, 200))
        window.close()

    def test_ctrl_zone_armed_click_starts_real_drag(self) -> None:
        _app()
        window = CaptureExpressWindow()
        window.capture_active = True
        window.session = SimpleNamespace(capture_region=None)
        with mock.patch.object(app_module, "cursor_position", return_value=(100, 100)):
            window._poll_zone_keys(ctrl=True, left=False, now_ms=1000)
            self.assertTrue(window.ctrl_zone_armed)
            window._poll_zone_keys(ctrl=True, left=True, now_ms=1045)
        self.assertFalse(window.ctrl_zone_armed)
        self.assertTrue(window.ctrl_drag_active)
        self.assertEqual(window.ctrl_drag_start, (100, 100))
        self.assertTrue(window.zone_left_down)
        if window.region_selector is not None:
            window.region_selector.close()
        window.close()

    def test_stopping_paused_recorder_kills_if_resume_fails(self) -> None:
        class DummyProcess:
            pid = 123
            stdin = None

            def __init__(self) -> None:
                self.killed = False

            def poll(self) -> None:
                return None

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout: int) -> None:
                return None

        recorder = ClipRecorder(Path("dummy.mp4"), region=None, capture_format=CaptureFormat())
        process = DummyProcess()
        recorder._process = process  # type: ignore[assignment]
        recorder._start = 100.0
        recorder._paused = True
        recorder._paused_at = 105.0
        with (
            mock.patch.object(media_module, "_resume_process", return_value=False),
            mock.patch.object(media_module.time, "perf_counter", return_value=110.0),
        ):
            duration = recorder.stop()
        self.assertTrue(process.killed)
        self.assertEqual(duration, 5.0)

    def test_video_region_is_normalized_to_even_dimensions(self) -> None:
        recorder = ClipRecorder(Path("dummy.mp4"), region=CaptureRegion(10, 20, 301, 201), capture_format=CaptureFormat())
        self.assertEqual(recorder.region, CaptureRegion(10, 20, 300, 200))

    def test_video_start_falls_back_to_software_encoder(self) -> None:
        hardware = HardwareProfile(
            tier="dedicated",
            gpu_name="GPU test",
            live_encoder=VideoEncoder("h264_nvenc", []),
            summary="GPU",
        )
        with mock.patch.object(media_module, "probe_hardware_profile", return_value=hardware):
            recorder = ClipRecorder(Path("dummy.mp4"), region=None, capture_format=CaptureFormat())
        failed_process = mock.Mock()
        with (
            mock.patch.object(recorder, "_launch_all", side_effect=[[failed_process], [mock.Mock()]]),
            mock.patch.object(recorder, "_failed_processes", side_effect=[[failed_process], []]),
            mock.patch.object(recorder, "_tail_log", return_value="encoder failed"),
            mock.patch.object(recorder, "stop"),
            mock.patch.object(media_module.time, "sleep"),
        ):
            recorder.start()
        self.assertEqual(recorder.hardware_profile.live_encoder.name, "libx264")

    def test_multitrack_recording_uses_separate_ffmpeg_processes(self) -> None:
        with mock.patch.object(media_module, "resolve_ffmpeg_bin", return_value="ffmpeg"):
            recorder = ClipRecorder(
                Path("videos/video-audio-001.mp4"),
                region=CaptureRegion(10, 20, 300, 200),
                capture_format=CaptureFormat(fps=30),
                with_audio=True,
                audio_device="Microphone",
                with_webcam=True,
                webcam_device="Camera",
            )
            recorder.live_plan = LiveRecordingPlan(
                screen_fps=30,
                webcam_mode=WebcamCaptureMode(640, 480, 30, "nv12"),
                overlay_width=320,
                notes="test plan",
            )
            screen_cmd = recorder._build_screen_command()
            webcam_cmd = recorder._build_webcam_command()
            audio_cmd = recorder._build_audio_command()
        self.assertEqual(recorder.recording_mode, "multitrack")
        self.assertIn("gdigrab", screen_cmd)
        self.assertNotIn("dshow", " ".join(screen_cmd))
        self.assertEqual(Path(screen_cmd[-1]), Path("videos/video-audio-001.screen.capture.mp4"))
        self.assertIn("video=Camera", webcam_cmd)
        self.assertIn("640x480", webcam_cmd)
        self.assertEqual(Path(webcam_cmd[-1]), Path("videos/video-audio-001.webcam.capture.mp4"))
        self.assertIn("audio=Microphone", audio_cmd)
        self.assertEqual(Path(audio_cmd[-1]), Path("videos/video-audio-001.audio.capture.m4a"))
        self.assertNotIn("filter_complex", " ".join(screen_cmd + webcam_cmd + audio_cmd))

    def test_plan_quality_recording_keeps_full_fps(self) -> None:
        profile = HardwareProfile(
            tier="dedicated",
            gpu_name="NVIDIA GeForce RTX 3070",
            live_encoder=VideoEncoder("h264_nvenc", []),
            summary="Profil GPU dedie",
        )
        with mock.patch.object(
            media_module,
            "probe_webcam_modes",
            return_value=[WebcamCaptureMode(1280, 720, 30, "nv12")],
        ):
            plan = plan_quality_recording(
                CaptureFormat(fps=30),
                CaptureRegion(0, 0, 2560, 1600),
                with_webcam=True,
                webcam_device="Camera",
                hardware_profile=profile,
            )
        self.assertEqual(plan.screen_fps, 30)
        self.assertEqual(plan.webcam_mode.width, 1280)  # type: ignore[union-attr]

    def test_cpu_profile_caps_screen_fps_on_large_display(self) -> None:
        fps = _cap_screen_fps(30, CaptureRegion(0, 0, 2560, 1600), tier="cpu")
        self.assertEqual(fps, 20)

    def test_classify_gpu_tier_detects_dedicated_nvidia(self) -> None:
        tier = classify_gpu_tier(["NVIDIA GeForce RTX 3070"], " V..... h264_nvenc")
        self.assertEqual(tier, "dedicated")

    def test_classify_gpu_tier_falls_back_to_cpu_without_encoders(self) -> None:
        tier = classify_gpu_tier(["Microsoft Basic Display Adapter"], "")
        self.assertEqual(tier, "cpu")

    def test_parse_webcam_modes_reads_ffmpeg_options(self) -> None:
        output = '''
        pixel_format=nv12  min s=320x240 fps=30 max s=320x240 fps=30
        pixel_format=yuyv422  min s=640x480 fps=30 max s=640x480 fps=30
        '''
        modes = parse_webcam_modes(output)
        self.assertEqual(len(modes), 2)
        self.assertEqual(modes[0].pixel_format, "nv12")

    def test_audio_only_recording_maps_video_and_audio_streams(self) -> None:
        recorder = ClipRecorder(
            Path("dummy.mp4"),
            region=CaptureRegion(10, 20, 300, 200),
            capture_format=CaptureFormat(fps=30),
            with_audio=True,
            audio_device="Microphone",
        )
        with mock.patch.object(media_module, "resolve_ffmpeg_bin", return_value="ffmpeg"):
            cmd = recorder._build_command()
        self.assertIn("0:v", cmd)
        self.assertIn("1:a", cmd)
        self.assertIn("audio=Microphone", cmd)
        self.assertEqual(cmd[cmd.index("-fps_mode") + 1], "vfr")
        self.assertIn("aresample=async=1:first_pts=0", " ".join(cmd))

    def test_default_dshow_device_resolves_to_first_detected_device(self) -> None:
        with mock.patch.object(media_module, "list_directshow_devices", return_value=["Microphone Realtek"]):
            self.assertEqual(_resolve_dshow_device("audio", "default"), "Microphone Realtek")

    def test_capture_recording_path_uses_sidecar_suffix(self) -> None:
        self.assertEqual(
            capture_recording_path(Path("videos/video-audio-001.mp4")),
            Path("videos/video-audio-001.capture.mp4"),
        )

    def test_live_capture_uses_quality_encoder_without_finalize_scaling(self) -> None:
        profile = HardwareProfile(
            tier="cpu",
            gpu_name="CPU",
            live_encoder=VideoEncoder("libx264", ["-preset", "veryfast", "-crf", "20"]),
            summary="Profil CPU",
        )
        with (
            mock.patch.object(media_module, "resolve_ffmpeg_bin", return_value="ffmpeg"),
            mock.patch.object(media_module, "probe_hardware_profile", return_value=profile),
        ):
            recorder = ClipRecorder(
                Path("dummy.mp4"),
                region=CaptureRegion(10, 20, 300, 200),
                capture_format=CaptureFormat(fps=30, video_quality="compact", target_width=1920, target_height=1080),
                with_audio=True,
                audio_device="Microphone",
            )
            cmd = recorder._build_command()
        self.assertIn("-crf", cmd)
        self.assertNotIn("scale=1280:720", " ".join(cmd))
        self.assertEqual(cmd[-1], "dummy.capture.mp4")

    def test_finalize_command_applies_quality_profile_and_audio(self) -> None:
        profile = HardwareProfile(
            tier="integrated",
            gpu_name="Intel UHD",
            live_encoder=VideoEncoder("h264_qsv", []),
            summary="Profil GPU integre",
        )
        with mock.patch.object(media_module, "probe_hardware_profile", return_value=profile):
            cmd = _build_finalize_command(
                Path("videos/video-audio-001.capture.mp4"),
                Path("videos/video-audio-001.mp4"),
                capture_format=CaptureFormat(fps=30, video_quality="compact"),
                with_audio=True,
                ffmpeg="ffmpeg",
            )
        self.assertIn("scale=1280:720", " ".join(cmd))
        self.assertEqual(cmd[cmd.index("-crf") + 1], "28")
        self.assertIn("0:a?", cmd)
        self.assertEqual(cmd[cmd.index("-fps_mode") + 1], "cfr")
        self.assertIn("+faststart", cmd)

    def test_multitrack_finalize_composites_screen_webcam_and_audio(self) -> None:
        tracks = capture_track_paths(Path("videos/video-audio-001.mp4"))
        cmd = _build_multitrack_finalize_command(
            tracks,
            Path("videos/video-audio-001.mp4"),
            capture_format=CaptureFormat(fps=30),
            with_audio=True,
            with_webcam=True,
            overlay_width=320,
            ffmpeg="ffmpeg",
        )
        joined = " ".join(cmd)
        self.assertIn(str(tracks["screen"]), joined)
        self.assertIn(str(tracks["webcam"]), joined)
        self.assertIn(str(tracks["audio"]), joined)
        self.assertIn("overlay=W-w-24:H-h-24", joined)
        self.assertIn("2:a", joined)

    def test_audio_device_defaults_to_first_real_directshow_device(self) -> None:
        _app()
        window = CaptureExpressWindow()
        window.audio_device_input.setEditText("default")
        with mock.patch.object(app_module, "list_directshow_audio_devices", return_value=["Microphone Realtek"]):
            window._populate_audio_devices()
        self.assertEqual(window.audio_device_input.currentText(), "Microphone Realtek")
        window.close()

    def test_frozen_onefile_resolves_bundled_ffmpeg_from_meipass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meipass = Path(tmp)
            ffmpeg = meipass / "ffmpeg.exe"
            ffmpeg.write_text("", encoding="utf-8")
            with (
                mock.patch.object(media_module.sys, "frozen", True, create=True),
                mock.patch.object(media_module.sys, "_MEIPASS", str(meipass), create=True),
                mock.patch.object(media_module.sys, "executable", str(meipass / "CaptureExpress.exe")),
                mock.patch.object(media_module.shutil, "which", return_value=None),
            ):
                self.assertEqual(Path(media_module.resolve_ffmpeg_bin() or "").resolve(), ffmpeg.resolve())

    def test_frozen_app_resolves_ffmpeg_next_to_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            ffmpeg = exe_dir / "ffmpeg.exe"
            ffmpeg.write_text("", encoding="utf-8")
            with (
                mock.patch.object(media_module.sys, "frozen", True, create=True),
                mock.patch.object(media_module.sys, "executable", str(exe_dir / "CaptureExpress.exe")),
                mock.patch.object(media_module.shutil, "which", return_value=None),
            ):
                self.assertEqual(Path(media_module.resolve_ffmpeg_bin() or "").resolve(), ffmpeg.resolve())

    def test_ffmpeg_8_directshow_device_format_is_parsed(self) -> None:
        output = '''
        [dshow @ 000] "Integrated Camera" (video)
        [dshow @ 000]   Alternative name "@device_pnp_..."
        [dshow @ 000] "Reseau de microphones (Realtek(R) Audio)" (audio)
        [dshow @ 000]   Alternative name "@device_cm_..."
        '''
        self.assertEqual(_parse_directshow_devices(output, "video"), ["Integrated Camera"])
        self.assertEqual(
            _parse_directshow_devices(output, "audio"),
            ["Reseau de microphones (Realtek(R) Audio)"],
        )


if __name__ == "__main__":
    unittest.main()
