from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


CaptureKind = Literal["screenshot", "video", "video_audio"]
LogoPosition = Literal["top_left", "top_right"]


@dataclass(slots=True)
class CaptureFormat:
    video_profile: str = "source"
    video_quality: str = "fast"
    screenshot_format: str = "png"
    fps: int = 30
    target_width: int | None = None
    target_height: int | None = None


@dataclass(slots=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height


@dataclass(slots=True)
class CaptureExpressConfig:
    output_root: Path
    session_title: str = "capture"
    capture_format: CaptureFormat = field(default_factory=CaptureFormat)
    capture_source: str = "screen"
    cursor_effect: bool = True
    audio_enabled: bool = False
    audio_device: str = "default"
    webcam_enabled: bool = False
    webcam_device: str = ""
    exclude_taskbar: bool = False
    initial_region: CaptureRegion | None = None
    logo_enabled: bool = False
    logo_path: str = ""
    logo_position: LogoPosition = "top_left"


@dataclass(slots=True)
class CaptureArtifact:
    artifact_id: str
    kind: CaptureKind
    path: str
    timestamp: str
    region: CaptureRegion | None
    source: str
    duration_s: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TimelineEvent:
    timestamp_s: float
    event_type: str
    action: str
    artifact_id: str = ""
    x: int | None = None
    y: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def dataclass_to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    return value
