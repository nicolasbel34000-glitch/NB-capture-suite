from __future__ import annotations

import os
from dataclasses import dataclass

from .models import CaptureRegion


@dataclass(slots=True)
class WindowInfo:
    title: str
    region: CaptureRegion
    handle: int = 0


def configure_process_dpi_awareness() -> bool:
    """Use physical pixels across mixed-DPI Windows monitors."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        # PER_MONITOR_AWARE_V2 keeps Qt, cursor, and Win32 monitor rectangles
        # in the same coordinate space on mixed-resolution/scaled displays.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass
    try:
        import ctypes

        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return True
    except Exception:
        pass
    try:
        import ctypes

        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception:
        return False


def monitor_regions() -> list[tuple[str, CaptureRegion]]:
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
                ("szDevice", ctypes.c_wchar * 32),
            ]

        monitors: list[tuple[str, CaptureRegion, bool]] = []

        def _callback(hmonitor: object, _hdc: object, _rect: object, _data: object) -> int:
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if not ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                return 1
            rect = info.rcMonitor
            region = CaptureRegion(
                int(rect.left),
                int(rect.top),
                int(rect.right - rect.left),
                int(rect.bottom - rect.top),
            )
            monitors.append((str(info.szDevice).replace("\\\\.\\", ""), region, bool(info.dwFlags & 1)))
            return 1

        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(RECT),
            wintypes.LPARAM,
        )
        if not ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback_type(_callback), 0):
            return []
        monitors.sort(key=lambda item: (item[1].left, item[1].top, item[1].width))
        labels: list[tuple[str, CaptureRegion]] = []
        for index, (device, region, primary) in enumerate(monitors, start=1):
            role = "Ecran principal" if primary else f"Ecran {index}"
            labels.append((f"{role} ({region.width}x{region.height} @ {region.left},{region.top}) - {device}", region))
        return labels
    except Exception:
        return []


def list_window_infos() -> list[WindowInfo]:
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        windows: list[WindowInfo] = []

        def _callback(hwnd: int, _lparam: int) -> int:
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return 1
            if ctypes.windll.user32.IsIconic(hwnd):
                return 1
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return 1
            buffer = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = str(buffer.value).strip()
            if not title:
                return 1
            rect = wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return 1
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 80 or height < 60:
                return 1
            windows.append(
                WindowInfo(
                    title=title,
                    region=CaptureRegion(int(rect.left), int(rect.top), width, height),
                    handle=int(hwnd),
                )
            )
            return 1

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)
        ctypes.windll.user32.EnumWindows(callback_type(_callback), 0)
        windows.sort(key=lambda item: item.title.lower())
        return windows
    except Exception:
        return []


def window_info_by_handle(handle: int) -> WindowInfo | None:
    if os.name != "nt" or not handle:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(handle)
        if not ctypes.windll.user32.IsWindow(hwnd):
            return None
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return None
        if ctypes.windll.user32.IsIconic(hwnd):
            return None
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return None
        return WindowInfo(
            title=str(buffer.value or "Fenetre choisie"),
            region=CaptureRegion(int(rect.left), int(rect.top), width, height),
            handle=hwnd,
        )
    except Exception:
        return None


def cursor_position() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return int(point.x), int(point.y)
    except Exception:
        return None


def screen_geometry_at(position: tuple[int, int] | None, *, exclude_taskbar: bool = False) -> tuple[int, int, int, int] | None:
    """Return the monitor rectangle containing position, in desktop coordinates."""
    if os.name != "nt" or position is None:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        point = wintypes.POINT(position[0], position[1])
        monitor = ctypes.windll.user32.MonitorFromPoint(point, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return None

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        rect = info.rcWork if exclude_taskbar else info.rcMonitor
        return int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)
    except Exception:
        return None


def is_virtual_key_down(vk: int) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def exclude_widget_from_windows_capture(widget: object) -> bool:
    """Best-effort: keep floating controls out of Windows capture APIs."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        hwnd = int(widget.winId())  # type: ignore[attr-defined]
        # WDA_EXCLUDEFROMCAPTURE. Falls back to WDA_MONITOR on older Windows.
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11))
    except Exception:
        return False
