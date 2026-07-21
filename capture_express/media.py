from __future__ import annotations

import re
import shutil
import subprocess
import time
import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import mss
from PIL import Image, ImageDraw

from .models import CaptureFormat, CaptureRegion, LogoPosition


def capture_screenshot(
    destination: Path,
    *,
    region: CaptureRegion | None,
    capture_format: CaptureFormat,
    cursor_position: tuple[int, int] | None = None,
    draw_cursor_effect: bool = True,
    logo_path: Path | None = None,
    logo_position: LogoPosition = "top_left",
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with mss.mss() as sct:
        if region is None:
            monitor = _monitor_at_cursor(sct, cursor_position) or sct.monitors[0]
            shot = sct.grab(monitor)
            origin = (monitor["left"], monitor["top"])
        else:
            monitor = {"left": region.left, "top": region.top, "width": region.width, "height": region.height}
            shot = sct.grab(monitor)
            origin = (region.left, region.top)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
    if _looks_black(image):
        fallback = _pyautogui_screenshot(region)
        if fallback is not None and not _looks_black(fallback):
            image = fallback

    image = _resize_if_needed(image, capture_format)
    if draw_cursor_effect and cursor_position is not None:
        _draw_cursor_marker(image, cursor_position, origin, region, capture_format)
    if logo_path is not None:
        image = apply_logo_to_image(image, logo_path, position=logo_position)
    save_format = "JPEG" if destination.suffix.lower() in {".jpg", ".jpeg"} else "PNG"
    image.save(destination, save_format)


def apply_logo_to_image(
    image: Image.Image,
    logo_path: Path,
    *,
    position: LogoPosition = "top_left",
    margin: int = 24,
    max_width_ratio: float = 0.16,
) -> Image.Image:
    if not logo_path.exists():
        raise RuntimeError(f"Logo introuvable: {logo_path}")
    base = image.convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")
    max_width = max(48, int(base.width * max_width_ratio))
    if logo.width > max_width:
        height = max(1, int(logo.height * (max_width / logo.width)))
        logo = logo.resize((max_width, height), Image.Resampling.LANCZOS)
    x = margin if position == "top_left" else max(margin, base.width - logo.width - margin)
    y = margin
    base.alpha_composite(logo, (x, y))
    return base.convert(image.mode if image.mode in {"RGB", "RGBA"} else "RGB")


def screen_region_at(position: tuple[int, int] | None, *, exclude_taskbar: bool = False) -> CaptureRegion | None:
    if position is None:
        return None
    if exclude_taskbar:
        try:
            from .windowing import screen_geometry_at

            geometry = screen_geometry_at(position, exclude_taskbar=True)
            if geometry is not None:
                return CaptureRegion(*geometry)
        except Exception:
            pass
    x, y = position
    with mss.mss() as sct:
        monitor = _monitor_at_cursor(sct, position)
    if monitor is None:
        return None
    return CaptureRegion(
        left=int(monitor["left"]),
        top=int(monitor["top"]),
        width=int(monitor["width"]),
        height=int(monitor["height"]),
    )


def _monitor_at_cursor(sct: mss.mss, position: tuple[int, int] | None) -> dict[str, int] | None:
    if position is None:
        return None
    x, y = position
    for monitor in sct.monitors[1:]:
        left = int(monitor["left"])
        top = int(monitor["top"])
        width = int(monitor["width"])
        height = int(monitor["height"])
        if left <= x < left + width and top <= y < top + height:
            return monitor
    return None


def _dshow_device_arg(kind: Literal["audio", "video"], name: str) -> str:
    return f"{kind}={name}"


def _resolve_dshow_device(kind: Literal["audio", "video"], name: str) -> str:
    cleaned = name.strip()
    if cleaned and cleaned.lower() != "default":
        return cleaned
    devices = list_directshow_devices(kind)
    if devices:
        return devices[0]
    return cleaned or "default"


def _normalize_video_region(region: CaptureRegion | None) -> CaptureRegion | None:
    if region is None:
        return None
    width = region.width if region.width % 2 == 0 else region.width - 1
    height = region.height if region.height % 2 == 0 else region.height - 1
    return CaptureRegion(region.left, region.top, max(2, width), max(2, height))


@dataclass(frozen=True, slots=True)
class WebcamCaptureMode:
    width: int
    height: int
    fps: int
    pixel_format: str = "yuyv422"


@dataclass(frozen=True, slots=True)
class LiveRecordingPlan:
    screen_fps: int
    webcam_mode: WebcamCaptureMode | None
    overlay_width: int
    notes: str = ""


_WEBCAM_MODE_PATTERN = re.compile(
    r"pixel_format=(\w+)\s+min s=(\d+)x(\d+) fps=(\d+) max s=(\d+)x(\d+) fps=(\d+)"
)


def parse_webcam_modes(output: str) -> list[WebcamCaptureMode]:
    modes: list[WebcamCaptureMode] = []
    seen: set[tuple[int, int, int, str]] = set()
    for match in _WEBCAM_MODE_PATTERN.finditer(output):
        pixel_format = match.group(1)
        width = int(match.group(2))
        height = int(match.group(3))
        fps = int(match.group(7))
        key = (width, height, fps, pixel_format)
        if key in seen:
            continue
        seen.add(key)
        modes.append(WebcamCaptureMode(width=width, height=height, fps=fps, pixel_format=pixel_format))
    return modes


def probe_webcam_modes(device: str) -> list[WebcamCaptureMode]:
    ffmpeg = resolve_ffmpeg_bin()
    if ffmpeg is None or not device.strip():
        return []
    cmd = [ffmpeg, "-hide_banner", "-f", "dshow", "-list_options", "true", "-i", _dshow_device_arg("video", device)]
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    return parse_webcam_modes(completed.stdout or "")


@dataclass(frozen=True, slots=True)
class VideoEncoder:
    name: str
    options: list[str]


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    tier: Literal["cpu", "integrated", "dedicated"]
    gpu_name: str
    live_encoder: VideoEncoder
    summary: str


_HARDWARE_PROFILE_CACHE: HardwareProfile | None = None

_INTEGRATED_GPU_MARKERS = ("intel", "uhd", "iris", "arc")


def reset_hardware_profile_cache() -> None:
    global _HARDWARE_PROFILE_CACHE
    _HARDWARE_PROFILE_CACHE = None


def _list_windows_gpu_names() -> list[str]:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    return [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]


def classify_gpu_tier(gpu_names: list[str], encoders_output: str) -> Literal["cpu", "integrated", "dedicated"]:
    names_blob = " ".join(gpu_names).lower()
    encoders = encoders_output.lower()
    has_nvenc = "h264_nvenc" in encoders
    has_qsv = "h264_qsv" in encoders
    has_amf = "h264_amf" in encoders
    has_nvidia_gpu = any(marker in names_blob for marker in ("nvidia", "geforce", "rtx", "gtx", "quadro"))
    has_amd_gpu = "amd" in names_blob or "radeon" in names_blob
    has_intel_gpu = any(marker in names_blob for marker in _INTEGRATED_GPU_MARKERS)

    if has_nvenc and has_nvidia_gpu:
        return "dedicated"
    if has_amf and has_amd_gpu:
        return "dedicated"
    if has_qsv and has_intel_gpu:
        return "integrated"
    return "cpu"


def _live_encoder_for_tier(
    tier: Literal["cpu", "integrated", "dedicated"],
    encoders_output: str,
    gpu_names: list[str] | None = None,
) -> VideoEncoder:
    encoders = encoders_output.lower()
    names_blob = " ".join(gpu_names or []).lower()
    if tier == "dedicated":
        if "h264_nvenc" in encoders and any(
            marker in names_blob for marker in ("nvidia", "geforce", "rtx", "gtx", "quadro")
        ):
            return VideoEncoder(
                "h264_nvenc",
                ["-preset", "p5", "-rc", "vbr", "-cq", "17", "-b:v", "0", "-pix_fmt", "yuv420p"],
            )
        if "h264_amf" in encoders and ("amd" in names_blob or "radeon" in names_blob):
            return VideoEncoder(
                "h264_amf",
                ["-quality", "quality", "-rc", "cqp", "-qp_i", "17", "-qp_p", "17", "-pix_fmt", "yuv420p"],
            )
    if tier == "integrated" and "h264_qsv" in encoders and any(
        marker in names_blob for marker in _INTEGRATED_GPU_MARKERS
    ):
        return VideoEncoder("h264_qsv", ["-global_quality", "22", "-pix_fmt", "yuv420p"])
    crf = "20" if tier == "cpu" else "18"
    return VideoEncoder("libx264", ["-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p"])


def _hardware_summary(tier: Literal["cpu", "integrated", "dedicated"], gpu_name: str, encoder: VideoEncoder) -> str:
    tier_labels = {
        "cpu": "Profil CPU",
        "integrated": "Profil GPU integre",
        "dedicated": "Profil GPU dedie",
    }
    gpu_part = gpu_name if gpu_name else "GPU inconnu"
    return f"{tier_labels[tier]} ({gpu_part}, {encoder.name})"


def probe_hardware_profile(ffmpeg: str | None = None) -> HardwareProfile:
    global _HARDWARE_PROFILE_CACHE
    if _HARDWARE_PROFILE_CACHE is not None:
        return _HARDWARE_PROFILE_CACHE
    binary = ffmpeg or resolve_ffmpeg_bin()
    encoders_output = ""
    if binary is not None:
        try:
            completed = subprocess.run(
                [binary, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
            )
            encoders_output = completed.stdout or ""
        except Exception:
            encoders_output = ""
    gpu_names = _list_windows_gpu_names()
    primary_gpu = gpu_names[0] if gpu_names else "CPU"
    tier = classify_gpu_tier(gpu_names, encoders_output)
    live_encoder = _live_encoder_for_tier(tier, encoders_output, gpu_names)
    _HARDWARE_PROFILE_CACHE = HardwareProfile(
        tier=tier,
        gpu_name=primary_gpu,
        live_encoder=live_encoder,
        summary=_hardware_summary(tier, primary_gpu, live_encoder),
    )
    return _HARDWARE_PROFILE_CACHE


def resolve_live_video_encoder(ffmpeg: str | None = None) -> VideoEncoder:
    return probe_hardware_profile(ffmpeg).live_encoder


def _region_pixel_count(region: CaptureRegion | None) -> int:
    if region is None:
        return 1920 * 1080
    return max(1, region.width * region.height)


def _cap_screen_fps(
    user_fps: int,
    region: CaptureRegion | None,
    *,
    tier: Literal["cpu", "integrated", "dedicated"],
) -> int:
    pixels = _region_pixel_count(region)
    capped = user_fps
    if tier == "cpu":
        if pixels >= 2560 * 1440:
            capped = min(capped, 20)
        elif pixels >= 1920 * 1080:
            capped = min(capped, 24)
    elif tier == "integrated" and pixels >= 2560 * 1440:
        capped = min(capped, 24)
    return max(10, capped)


def select_webcam_mode_quality(
    modes: list[WebcamCaptureMode],
    *,
    tier: Literal["cpu", "integrated", "dedicated"] = "integrated",
) -> WebcamCaptureMode:
    if not modes:
        if tier == "dedicated":
            return WebcamCaptureMode(1280, 720, 30)
        return WebcamCaptureMode(640, 480, 30)
    if tier == "dedicated":
        preferred_sizes = ((1280, 720), (640, 480), (320, 240))
    elif tier == "cpu":
        preferred_sizes = ((640, 480), (320, 240), (320, 180))
    else:
        preferred_sizes = ((640, 480), (1280, 720), (320, 240))

    def score(mode: WebcamCaptureMode) -> tuple[int, int, int]:
        format_rank = 0 if mode.pixel_format == "nv12" else 1
        size_rank = min(
            (abs(mode.width * mode.height - target_w * target_h), index)
            for index, (target_w, target_h) in enumerate(preferred_sizes)
        )[1]
        return (format_rank, size_rank, -min(mode.fps, 30))

    return min(modes, key=score)


def plan_quality_recording(
    capture_format: CaptureFormat,
    region: CaptureRegion | None,
    *,
    with_webcam: bool,
    webcam_device: str,
    hardware_profile: HardwareProfile | None = None,
) -> LiveRecordingPlan:
    profile = hardware_profile or probe_hardware_profile()
    screen_fps = _cap_screen_fps(max(1, int(capture_format.fps)), region, tier=profile.tier)
    if not with_webcam:
        notes = f"{profile.summary} | Ecran @{screen_fps} fps"
        return LiveRecordingPlan(screen_fps=screen_fps, webcam_mode=None, overlay_width=0, notes=notes)
    selected = select_webcam_mode_quality(probe_webcam_modes(webcam_device), tier=profile.tier)
    webcam_fps = min(selected.fps, screen_fps)
    webcam_mode = WebcamCaptureMode(
        width=selected.width,
        height=selected.height,
        fps=max(15, webcam_fps),
        pixel_format=selected.pixel_format,
    )
    overlay_width = 360 if profile.tier == "dedicated" else 320
    notes = (
        f"{profile.summary} | Multitrack | Ecran @{screen_fps} fps | "
        f"Webcam {webcam_mode.width}x{webcam_mode.height} @{webcam_mode.fps} fps"
    )
    return LiveRecordingPlan(
        screen_fps=screen_fps,
        webcam_mode=webcam_mode,
        overlay_width=overlay_width,
        notes=notes,
    )


class ClipRecorder:
    def __init__(
        self,
        destination: Path,
        *,
        region: CaptureRegion | None,
        capture_format: CaptureFormat,
        with_audio: bool = False,
        audio_device: str = "default",
        with_webcam: bool = False,
        webcam_device: str = "",
    ) -> None:
        self.final_destination = destination
        self.track_paths = capture_track_paths(destination)
        self.recording_mode = "multitrack" if with_webcam else "single"
        self.region = _normalize_video_region(region)
        self.capture_format = capture_format
        self.with_audio = with_audio
        self.audio_device = _resolve_dshow_device("audio", audio_device) if with_audio else ""
        self.with_webcam = with_webcam
        self.webcam_device = _resolve_dshow_device("video", webcam_device) if with_webcam else ""
        self.hardware_profile = probe_hardware_profile()
        screen_fps = _cap_screen_fps(max(1, int(capture_format.fps)), self.region, tier=self.hardware_profile.tier)
        if self.recording_mode == "multitrack":
            self.live_plan = plan_quality_recording(
                capture_format,
                self.region,
                with_webcam=True,
                webcam_device=self.webcam_device,
                hardware_profile=self.hardware_profile,
            )
        else:
            self.live_plan = LiveRecordingPlan(
                screen_fps=screen_fps,
                webcam_mode=None,
                overlay_width=0,
                notes=f"{self.hardware_profile.summary} | Ecran @{screen_fps} fps",
            )
        self.ffmpeg_log_path = self._primary_log_path()
        self._processes: list[subprocess.Popen[bytes]] = []
        self._log_handles: list[object] = []
        self._start = 0.0
        self._paused = False
        self._paused_at = 0.0
        self._paused_total = 0.0

    @property
    def destination(self) -> Path:
        if self.recording_mode == "multitrack":
            return self.track_paths["screen"]
        return self.track_paths["combined"]

    @property
    def _process(self) -> subprocess.Popen[bytes] | None:
        return self._processes[0] if self._processes else None

    @_process.setter
    def _process(self, value: subprocess.Popen[bytes] | None) -> None:
        self._processes = [value] if value is not None else []

    @property
    def is_running(self) -> bool:
        return bool(self._processes) and any(process.poll() is None for process in self._processes)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def _primary_log_path(self) -> Path:
        if self.recording_mode == "multitrack":
            return track_log_path(self.final_destination, "screen")
        return self.track_paths["combined"].with_suffix(".ffmpeg.log")

    def start(self) -> None:
        if self._processes:
            raise RuntimeError("Clip recorder is already running")
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self._start = time.perf_counter()
        self._paused = False
        self._paused_at = 0.0
        self._paused_total = 0.0
        self._processes = self._launch_all()
        time.sleep(1.0)
        if not self._failed_processes():
            return
        details = self._tail_log()
        self.stop(force=True)
        if self.hardware_profile.live_encoder.name != "libx264":
            failed_encoder = self.hardware_profile.live_encoder.name
            self.hardware_profile = HardwareProfile(
                tier="cpu",
                gpu_name=self.hardware_profile.gpu_name,
                live_encoder=VideoEncoder(
                    "libx264",
                    ["-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"],
                ),
                summary=f"Repli CPU apres echec de {failed_encoder}",
            )
            self.live_plan = LiveRecordingPlan(
                screen_fps=self.live_plan.screen_fps,
                webcam_mode=self.live_plan.webcam_mode,
                overlay_width=self.live_plan.overlay_width,
                notes=f"{self.hardware_profile.summary} | @{self.live_plan.screen_fps} fps",
            )
            self._processes = self._launch_all()
            time.sleep(1.0)
            if not self._failed_processes():
                return
            details = self._tail_log()
            self.stop(force=True)
        if self.recording_mode == "multitrack" and self.with_webcam:
            self.with_webcam = False
            self.recording_mode = "single"
            self.live_plan = LiveRecordingPlan(
                screen_fps=max(1, int(self.capture_format.fps)),
                webcam_mode=None,
                overlay_width=0,
                notes="Fallback sans webcam",
            )
            self.ffmpeg_log_path = self._primary_log_path()
            self._processes = self._launch_all()
            time.sleep(1.0)
            if not self._failed_processes():
                return
            details = self._tail_log()
            self.stop(force=True)
        if self.with_audio:
            self.with_audio = False
            self._processes = self._launch_all()
            time.sleep(1.0)
            if not self._failed_processes():
                return
        raise RuntimeError(f"ffmpeg failed to start capture.\n{details}")

    def stop(self, *, force: bool = False) -> float:
        if not self._processes:
            return 0.0
        resume_failed = False
        if self._paused:
            if self._paused_at:
                self._paused_total += time.perf_counter() - self._paused_at
            self._paused = False
            self._paused_at = 0.0
            for process in self._processes:
                if process.poll() is None:
                    resume_failed = resume_failed or not _resume_process(process.pid)
        try:
            for process in self._processes:
                if resume_failed and process.poll() is None:
                    process.kill()
                elif not force and process.stdin is not None and process.poll() is None:
                    try:
                        process.stdin.write(b"q\n")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        pass
            for process in self._processes:
                try:
                    process.wait(timeout=15)
                except Exception:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
        finally:
            for process in self._processes:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            for handle in self._log_handles:
                try:
                    handle.close()
                except Exception:
                    pass
            self._processes = []
            self._log_handles = []
        return round(time.perf_counter() - self._start - self._paused_total, 3)

    def pause(self) -> bool:
        if not self.is_running or self._paused:
            return False
        for process in self._processes:
            if process.poll() is None and not _suspend_process(process.pid):
                return False
        self._paused = True
        self._paused_at = time.perf_counter()
        return True

    def resume(self) -> bool:
        if not self.is_running or not self._paused:
            return False
        for process in self._processes:
            if process.poll() is None and not _resume_process(process.pid):
                return False
        self._paused = False
        if self._paused_at:
            self._paused_total += time.perf_counter() - self._paused_at
        self._paused_at = 0.0
        return True

    def _failed_processes(self) -> list[subprocess.Popen[bytes]]:
        return [process for process in self._processes if process.poll() is not None]

    def _launch_all(self) -> list[subprocess.Popen[bytes]]:
        if self.recording_mode == "multitrack":
            commands = [("screen", self._build_screen_command(), self.track_paths["screen"])]
            if self.with_webcam and self.live_plan.webcam_mode is not None:
                commands.append(("webcam", self._build_webcam_command(), self.track_paths["webcam"]))
            if self.with_audio:
                commands.append(("audio", self._build_audio_command(), self.track_paths["audio"]))
        else:
            commands = [("combined", self._build_single_command(), self.track_paths["combined"])]
        processes: list[subprocess.Popen[bytes]] = []
        self._log_handles = []
        for track_name, cmd, output_path in commands:
            log_path = track_log_path(self.final_destination, track_name)
            handle = log_path.open("w", encoding="utf-8")
            handle.write("COMMAND: " + " ".join(cmd) + "\n\n")
            if self.live_plan.notes:
                handle.write("PLAN: " + self.live_plan.notes + "\n\n")
            handle.write(f"OUTPUT: {output_path}\n\n")
            handle.flush()
            self._log_handles.append(handle)
            processes.append(
                subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=handle,
                )
            )
        return processes

    def _build_single_command(self) -> list[str]:
        ffmpeg = resolve_ffmpeg_bin()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg introuvable. Installez ffmpeg ou placez-le dans bin/ffmpeg.")
        encoder = self.hardware_profile.live_encoder
        screen_fps = str(self.live_plan.screen_fps)
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "gdigrab",
            "-framerate",
            screen_fps,
            "-draw_mouse",
            "1",
        ]
        if self.region is not None:
            cmd.extend(
                [
                    "-offset_x",
                    str(self.region.left),
                    "-offset_y",
                    str(self.region.top),
                    "-video_size",
                    f"{self.region.width}x{self.region.height}",
                ]
            )
        cmd.extend(["-i", "desktop"])
        audio_input_index: int | None = None
        if self.with_audio:
            cmd.extend(
                [
                    "-thread_queue_size",
                    "2048",
                    "-f",
                    "dshow",
                    "-audio_buffer_size",
                    "100",
                    "-i",
                    _dshow_device_arg("audio", self.audio_device),
                ]
            )
            audio_input_index = 1
        cmd.extend(["-map", "0:v"])
        if self.with_audio and audio_input_index is not None:
            cmd.extend(["-map", f"{audio_input_index}:a"])
        cmd.extend(["-fps_mode", "vfr", "-c:v", encoder.name, *encoder.options])
        if self.with_audio:
            cmd.extend(["-af", "aresample=async=1:first_pts=0", "-c:a", "aac", "-b:a", "192k"])
        cmd.append(str(self.track_paths["combined"]))
        return cmd

    def _build_screen_command(self) -> list[str]:
        ffmpeg = resolve_ffmpeg_bin()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg introuvable. Installez ffmpeg ou placez-le dans bin/ffmpeg.")
        encoder = self.hardware_profile.live_encoder
        screen_fps = str(self.live_plan.screen_fps)
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "gdigrab",
            "-framerate",
            screen_fps,
            "-draw_mouse",
            "1",
        ]
        if self.region is not None:
            cmd.extend(
                [
                    "-offset_x",
                    str(self.region.left),
                    "-offset_y",
                    str(self.region.top),
                    "-video_size",
                    f"{self.region.width}x{self.region.height}",
                ]
            )
        cmd.extend(
            [
                "-i",
                "desktop",
                "-map",
                "0:v",
                "-fps_mode",
                "vfr",
                "-c:v",
                encoder.name,
                *encoder.options,
                str(self.track_paths["screen"]),
            ]
        )
        return cmd

    def _build_webcam_command(self) -> list[str]:
        ffmpeg = resolve_ffmpeg_bin()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg introuvable. Installez ffmpeg ou placez-le dans bin/ffmpeg.")
        encoder = self.hardware_profile.live_encoder
        webcam_mode = self.live_plan.webcam_mode
        if webcam_mode is None:
            raise RuntimeError("Webcam mode indisponible.")
        cmd = [
            ffmpeg,
            "-y",
            "-thread_queue_size",
            "2048",
            "-rtbufsize",
            "512M",
            "-f",
            "dshow",
            "-video_size",
            f"{webcam_mode.width}x{webcam_mode.height}",
            "-framerate",
            str(webcam_mode.fps),
        ]
        if webcam_mode.pixel_format:
            cmd.extend(["-pixel_format", webcam_mode.pixel_format])
        cmd.extend(
            [
                "-i",
                _dshow_device_arg("video", self.webcam_device),
                "-map",
                "0:v",
                "-fps_mode",
                "vfr",
                "-c:v",
                encoder.name,
                *encoder.options,
                str(self.track_paths["webcam"]),
            ]
        )
        return cmd

    def _build_audio_command(self) -> list[str]:
        ffmpeg = resolve_ffmpeg_bin()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg introuvable. Installez ffmpeg ou placez-le dans bin/ffmpeg.")
        return [
            ffmpeg,
            "-y",
            "-thread_queue_size",
            "2048",
            "-f",
            "dshow",
            "-audio_buffer_size",
            "100",
            "-i",
            _dshow_device_arg("audio", self.audio_device),
            "-map",
            "0:a",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(self.track_paths["audio"]),
        ]

    def _build_command(self) -> list[str]:
        if self.recording_mode == "multitrack":
            return self._build_screen_command()
        return self._build_single_command()

    def _tail_log(self) -> str:
        logs = [self.ffmpeg_log_path]
        if self.recording_mode == "multitrack":
            logs.extend(
                [
                    track_log_path(self.final_destination, "webcam"),
                    track_log_path(self.final_destination, "audio"),
                ]
            )
        chunks: list[str] = []
        for log_path in logs:
            if not log_path.exists():
                continue
            chunks.append(log_path.read_text(encoding="utf-8", errors="replace"))
        if not chunks:
            return ""
        return "\n".join("\n".join(chunk.splitlines()[-12:]) for chunk in chunks)


def resolve_ffmpeg_bin() -> str | None:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "ffmpeg.exe"
            if bundled.exists():
                return str(bundled)
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent
    cwd = Path.cwd()
    candidates = [
        app_dir / "ffmpeg.exe",
        app_dir / "bin" / "ffmpeg" / "ffmpeg.exe",
        app_dir / "bin" / "ffmpeg" / "bin" / "ffmpeg.exe",
        cwd / "ffmpeg.exe",
        cwd / "bin" / "ffmpeg" / "ffmpeg.exe",
        cwd / "bin" / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path(__file__).resolve().parent / "bin" / "ffmpeg" / "ffmpeg.exe",
        Path(__file__).resolve().parent / "bin" / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return str(resolved)
    return shutil.which("ffmpeg")


def _suspend_process(pid: int) -> bool:
    return _call_nt_process(pid, "NtSuspendProcess")


def _resume_process(pid: int) -> bool:
    return _call_nt_process(pid, "NtResumeProcess")


def _call_nt_process(pid: int, function_name: str) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    handle = kernel32.OpenProcess(0x0800, False, int(pid))
    if not handle:
        return False
    try:
        function = getattr(ntdll, function_name)
        return int(function(handle)) == 0
    finally:
        kernel32.CloseHandle(handle)


def list_directshow_audio_devices() -> list[str]:
    return list_directshow_devices("audio")


def list_directshow_video_devices() -> list[str]:
    return list_directshow_devices("video")


def list_directshow_devices(kind: Literal["audio", "video"]) -> list[str]:
    ffmpeg = resolve_ffmpeg_bin()
    if ffmpeg is None:
        return []
    cmd = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    return _parse_directshow_devices(completed.stderr + "\n" + completed.stdout, kind)


def _parse_directshow_devices(output: str, kind: Literal["audio", "video"]) -> list[str]:
    devices: list[str] = []
    section = ""
    section_markers = {
        "audio": "DirectShow audio devices",
        "video": "DirectShow video devices",
    }
    other_marker = "DirectShow video devices" if kind == "audio" else "DirectShow audio devices"
    for raw_line in output.splitlines():
        line = raw_line.strip()
        inline_name = _parse_directshow_inline_device(line, kind)
        if inline_name and inline_name not in devices:
            devices.append(inline_name)
            continue
        if section_markers[kind] in line:
            section = kind
            continue
        if other_marker in line:
            section = "other"
            continue
        if section != kind or "Alternative name" in line:
            continue
        first = line.find('"')
        second = line.find('"', first + 1)
        if first == -1 or second == -1:
            continue
        name = line[first + 1 : second].strip()
        if name and name not in devices:
            devices.append(name)
    return devices


def _parse_directshow_inline_device(line: str, kind: Literal["audio", "video"]) -> str:
    marker = f'" ({kind})'
    if marker not in line:
        return ""
    before = line.split(marker, 1)[0]
    quote = before.rfind('"')
    if quote < 0:
        return ""
    return before[quote + 1 :].strip()


def _resize_if_needed(image: Image.Image, capture_format: CaptureFormat) -> Image.Image:
    if not capture_format.target_width or not capture_format.target_height:
        return image
    return image.resize((capture_format.target_width, capture_format.target_height), Image.Resampling.LANCZOS)


def _looks_black(image: Image.Image) -> bool:
    try:
        sample = image.resize((1, 1), Image.Resampling.BILINEAR).convert("RGB")
        r, g, b = sample.getpixel((0, 0))
        return max(r, g, b) < 6
    except Exception:
        return False


def _pyautogui_screenshot(region: CaptureRegion | None) -> Image.Image | None:
    try:
        import pyautogui

        if region is not None:
            shot = pyautogui.screenshot(region=region.as_tuple())
        else:
            shot = pyautogui.screenshot()
        return shot.convert("RGB")
    except Exception:
        return None


def _draw_cursor_marker(
    image: Image.Image,
    cursor_position: tuple[int, int],
    origin: tuple[int, int],
    region: CaptureRegion | None,
    capture_format: CaptureFormat,
) -> None:
    x = cursor_position[0] - origin[0]
    y = cursor_position[1] - origin[1]
    source_width = region.width if region else image.width
    source_height = region.height if region else image.height
    if capture_format.target_width and capture_format.target_height:
        x = int(x * (capture_format.target_width / max(1, source_width)))
        y = int(y * (capture_format.target_height / max(1, source_height)))
    draw = ImageDraw.Draw(image, "RGBA")
    for radius, alpha in ((34, 55), (22, 90), (10, 150)):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(37, 99, 235, alpha), width=4)
    draw.line((x - 12, y, x + 12, y), fill=(37, 99, 235, 210), width=3)
    draw.line((x, y - 12, x, y + 12), fill=(37, 99, 235, 210), width=3)


def capture_recording_path(final_path: Path) -> Path:
    return final_path.with_name(f"{final_path.stem}.capture{final_path.suffix}")


def capture_track_paths(final_path: Path) -> dict[str, Path]:
    stem = final_path.stem
    parent = final_path.parent
    ext = final_path.suffix
    return {
        "combined": parent / f"{stem}.capture{ext}",
        "screen": parent / f"{stem}.screen.capture{ext}",
        "webcam": parent / f"{stem}.webcam.capture{ext}",
        "audio": parent / f"{stem}.audio.capture.m4a",
    }


def track_log_path(final_path: Path, track: str) -> Path:
    return final_path.parent / f"{final_path.stem}.{track}.ffmpeg.log"


def cleanup_capture_sidecars(final_path: Path) -> None:
    for path in capture_track_paths(final_path).values():
        path.unlink(missing_ok=True)
    stem = final_path.stem
    parent = final_path.parent
    for suffix in (".screen.ffmpeg.log", ".webcam.ffmpeg.log", ".audio.ffmpeg.log", ".combined.ffmpeg.log"):
        (parent / f"{stem}{suffix}").unlink(missing_ok=True)
    capture_recording_path(final_path).with_suffix(".ffmpeg.log").unlink(missing_ok=True)


def _final_video_encoding_options(
    capture_format: CaptureFormat,
    hardware_profile: HardwareProfile | None = None,
) -> list[str]:
    profile = hardware_profile or probe_hardware_profile()
    quality = capture_format.video_quality
    if profile.tier == "dedicated":
        presets = {
            "compact": ("medium", "26"),
            "balanced": ("slow", "22"),
            "fast": ("slow", "18"),
        }
    elif profile.tier == "cpu":
        presets = {
            "compact": ("fast", "28"),
            "balanced": ("fast", "24"),
            "fast": ("fast", "22"),
        }
    else:
        presets = {
            "compact": ("medium", "28"),
            "balanced": ("medium", "23"),
            "fast": ("medium", "20"),
        }
    preset, crf = presets.get(quality, presets["fast"])
    return ["-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]


def _build_finalize_command(
    source: Path,
    destination: Path,
    *,
    capture_format: CaptureFormat,
    with_audio: bool,
    ffmpeg: str,
) -> list[str]:
    fps = str(max(1, int(capture_format.fps)))
    cmd = [ffmpeg, "-y", "-i", str(source), "-map", "0:v"]
    scale = _ffmpeg_scale_filter(capture_format)
    if scale:
        cmd.extend(["-vf", scale])
    cmd.extend(
        [
            "-fps_mode",
            "cfr",
            "-r",
            fps,
            "-c:v",
            "libx264",
            *_final_video_encoding_options(capture_format, probe_hardware_profile(ffmpeg)),
            "-movflags",
            "+faststart",
        ]
    )
    if with_audio:
        cmd.extend(["-map", "0:a?", "-c:a", "aac", "-b:a", "192k"])
    cmd.append(str(destination))
    return cmd


def _build_multitrack_finalize_command(
    tracks: dict[str, Path],
    destination: Path,
    *,
    capture_format: CaptureFormat,
    with_audio: bool,
    with_webcam: bool,
    overlay_width: int,
    ffmpeg: str,
) -> list[str]:
    screen = tracks["screen"]
    cmd = [ffmpeg, "-y", "-i", str(screen)]
    next_index = 1
    filter_parts: list[str] = []
    video_map = "0:v"
    webcam_index: int | None = None
    audio_index: int | None = None
    scale = _ffmpeg_scale_filter(capture_format)

    if with_webcam:
        cmd.extend(["-i", str(tracks["webcam"])])
        webcam_index = next_index
        next_index += 1

    if with_audio:
        cmd.extend(["-i", str(tracks["audio"])])
        audio_index = next_index
        next_index += 1

    overlay = max(160, overlay_width)
    if webcam_index is not None:
        if scale:
            filter_parts.append(f"[0:v]{scale}[screen]")
            filter_parts.append(f"[{webcam_index}:v]scale={overlay}:-2[webcam]")
            filter_parts.append(f"[screen][webcam]overlay=W-w-24:H-h-24[vout]")
        else:
            filter_parts.append(f"[{webcam_index}:v]scale={overlay}:-2[webcam]")
            filter_parts.append(f"[0:v][webcam]overlay=W-w-24:H-h-24[vout]")
        video_map = "[vout]"
    elif scale:
        cmd.extend(["-vf", scale])

    if filter_parts:
        cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", video_map])
    else:
        cmd.extend(["-map", "0:v"])

    if with_audio and audio_index is not None:
        cmd.extend(["-map", f"{audio_index}:a"])

    fps = str(max(1, int(capture_format.fps)))
    cmd.extend(
        [
            "-fps_mode",
            "cfr",
            "-r",
            fps,
            "-c:v",
            "libx264",
            *_final_video_encoding_options(capture_format, probe_hardware_profile(ffmpeg)),
            "-movflags",
            "+faststart",
        ]
    )
    if with_audio and audio_index is not None:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    cmd.append(str(destination))
    return cmd


def finalize_video(
    final_path: Path,
    *,
    capture_format: CaptureFormat,
    with_audio: bool,
    with_webcam: bool = False,
    overlay_width: int = 320,
    logo_path: Path | None = None,
    logo_position: LogoPosition = "top_left",
) -> None:
    ffmpeg = resolve_ffmpeg_bin()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg introuvable. Installez ffmpeg ou placez-le dans bin/ffmpeg.")
    destination = final_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    tracks = capture_track_paths(final_path)
    log_path = final_path.with_suffix(".finalize.log")
    if tracks["screen"].exists() and tracks["screen"].stat().st_size > 0:
        cmd = _build_multitrack_finalize_command(
            tracks,
            destination,
            capture_format=capture_format,
            with_audio=with_audio,
            with_webcam=with_webcam,
            overlay_width=overlay_width,
            ffmpeg=ffmpeg,
        )
    else:
        source = capture_recording_path(final_path)
        if not source.exists() or source.stat().st_size == 0:
            raise RuntimeError(f"Fichier de capture introuvable ou vide: {final_path.stem}")
        cmd = _build_finalize_command(
            source,
            destination,
            capture_format=capture_format,
            with_audio=with_audio,
            ffmpeg=ffmpeg,
        )
    log_path.write_text("COMMAND: " + " ".join(cmd) + "\n\n", encoding="utf-8")
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        check=False,
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(completed.stdout or "")
    if completed.returncode != 0:
        details = "\n".join((completed.stdout or "").splitlines()[-20:])
        raise RuntimeError(f"Finalisation echouee pour {final_path.name}.\n{details}")
    if not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError(f"Fichier finalise introuvable ou vide: {destination.name}")
    if logo_path is not None:
        apply_logo_to_video(destination, logo_path, position=logo_position)


def apply_logo_to_video(final_path: Path, logo_path: Path, *, position: LogoPosition = "top_left") -> None:
    ffmpeg = resolve_ffmpeg_bin()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg introuvable. Installez ffmpeg ou placez-le dans bin/ffmpeg.")
    if not logo_path.exists():
        raise RuntimeError(f"Logo introuvable: {logo_path}")
    temp_path = final_path.with_name(f"{final_path.stem}.logo{final_path.suffix}")
    overlay = "24:24" if position == "top_left" else "W-w-24:24"
    filter_complex = f"[1:v]scale=160:-1[logo];[0:v][logo]overlay={overlay}:format=auto[v]"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(final_path),
        "-loop",
        "1",
        "-i",
        str(logo_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        "-shortest",
        "-movflags",
        "+faststart",
        str(temp_path),
    ]
    log_path = final_path.with_suffix(".logo.log")
    log_path.write_text("COMMAND: " + " ".join(cmd) + "\n\n", encoding="utf-8")
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        check=False,
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(completed.stdout or "")
    if completed.returncode != 0:
        temp_path.unlink(missing_ok=True)
        details = "\n".join((completed.stdout or "").splitlines()[-20:])
        raise RuntimeError(f"Ajout du logo echoue pour {final_path.name}.\n{details}")
    if not temp_path.exists() or temp_path.stat().st_size == 0:
        raise RuntimeError(f"Fichier avec logo introuvable ou vide: {temp_path.name}")
    temp_path.replace(final_path)


def _ffmpeg_scale_filter(capture_format: CaptureFormat) -> str:
    if capture_format.video_quality == "compact":
        return "scale=1280:720"
    if capture_format.target_width and capture_format.target_height:
        return f"scale={capture_format.target_width}:{capture_format.target_height}"
    return ""


def _video_encoding_options(capture_format: CaptureFormat, *, live_inputs: int = 1) -> list[str]:
    _ = live_inputs
    return _final_video_encoding_options(capture_format, probe_hardware_profile())
