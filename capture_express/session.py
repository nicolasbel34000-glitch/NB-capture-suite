from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    CaptureArtifact,
    CaptureExpressConfig,
    CaptureRegion,
    TimelineEvent,
)


class CaptureExpressSession:
    def __init__(self, config: CaptureExpressConfig, *, now: datetime | None = None) -> None:
        self.config = config
        self.started_at = now or datetime.now()
        self.session_id = self.started_at.strftime("session-%Y%m%d-%H%M%S")
        self.output_dir = config.output_root / f"{self.session_id}-{_slugify(config.session_title)}"
        self.screenshots_dir = self.output_dir / "screenshots"
        self.videos_dir = self.output_dir / "videos"
        self.audio_dir = self.output_dir / "audio"
        self.transcript_dir = self.output_dir / "transcript"
        self.manifest_path = self.output_dir / "manifest.json"
        self.timeline_path = self.output_dir / "timeline.json"
        self._artifacts: list[CaptureArtifact] = []
        self._timeline: list[TimelineEvent] = []
        self.capture_region = config.initial_region

    @property
    def artifacts(self) -> list[CaptureArtifact]:
        return list(self._artifacts)

    def start(self) -> None:
        for directory in (
            self.screenshots_dir,
            self.videos_dir,
            self.audio_dir,
            self.transcript_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._write_transcript_placeholder()
        self.log_event("session_start", "start")
        self.flush()

    def set_capture_region(self, region: CaptureRegion | None) -> None:
        self.capture_region = region
        self.log_event(
            "capture_region",
            "set" if region is not None else "clear",
            metadata={"region": asdict(region) if region is not None else None},
        )
        self.flush()

    def next_screenshot_path(self) -> tuple[str, Path]:
        return self._next_artifact_path("screenshot", self.screenshots_dir, self.config.capture_format.screenshot_format)

    def next_video_path(self, *, with_audio: bool) -> tuple[str, Path]:
        prefix = "video-audio" if with_audio else "video"
        return self._next_artifact_path(prefix, self.videos_dir, "mp4")

    def add_artifact(self, artifact: CaptureArtifact) -> None:
        self._artifacts.append(artifact)
        self.log_event(
            "artifact",
            artifact.kind,
            artifact_id=artifact.artifact_id,
            metadata={"path": artifact.path, "source": artifact.source},
        )
        self.flush()

    def log_event(
        self,
        event_type: str,
        action: str,
        *,
        artifact_id: str = "",
        x: int | None = None,
        y: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        elapsed = (datetime.now() - self.started_at).total_seconds()
        self._timeline.append(
            TimelineEvent(
                timestamp_s=round(elapsed, 3),
                event_type=event_type,
                action=action,
                artifact_id=artifact_id,
                x=x,
                y=y,
                metadata=metadata or {},
            )
        )

    def flush(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "app": "capture_express",
            "session_id": self.session_id,
            "session_title": self.config.session_title,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "output_dir": str(self.output_dir),
            "settings": {
                "format": asdict(self.config.capture_format),
                "cursor_effect": self.config.cursor_effect,
                "audio_enabled": self.config.audio_enabled,
                "audio_device": self.config.audio_device,
                "webcam_enabled": self.config.webcam_enabled,
                "webcam_device": self.config.webcam_device,
                "exclude_taskbar": self.config.exclude_taskbar,
                "logo_enabled": self.config.logo_enabled,
                "logo_path": self.config.logo_path,
                "logo_position": self.config.logo_position,
            },
            "capture_region": asdict(self.capture_region) if self.capture_region else None,
            "artifacts": [asdict(item) for item in self._artifacts],
        }
        self.manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        self.timeline_path.write_text(
            json.dumps([asdict(item) for item in self._timeline], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _next_artifact_path(self, prefix: str, directory: Path, suffix: str) -> tuple[str, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        index = len([item for item in self._artifacts if item.artifact_id.startswith(prefix)]) + 1
        artifact_id = f"{prefix}-{index:03d}"
        return artifact_id, directory / f"{artifact_id}.{suffix.lower().lstrip('.')}"

    def _write_transcript_placeholder(self) -> None:
        placeholder = self.transcript_dir / "README.txt"
        if not placeholder.exists():
            placeholder.write_text(
                "Transcription automatique non active dans cette version.\n"
                "Les fichiers audio/video sont conserves pour transcription ulterieure.\n",
                encoding="utf-8",
            )


def _slugify(text: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return token or "capture"
