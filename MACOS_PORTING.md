# macOS Porting Analysis

Current status: not supported.

No macOS build has been tested. Do not advertise macOS compatibility until a build and capture workflow have been verified on real macOS hardware.

## Current Compatibility Level

- UI: potentially portable because PyQt can run on macOS.
- Core capture behavior: Windows-specific.
- Packaging: Windows-only workflow currently prepared.

## Windows-Only Components

- `windowing.py`
  - Win32 monitor/window APIs through `ctypes`
  - virtual-key polling
  - `SetWindowDisplayAffinity`
- `media.py`
  - `ffmpeg -f gdigrab`
  - DirectShow device listing for audio/webcam
- `run_capture_express.bat`
  - Windows launcher only

## Modules to Adapt

- `windowing.py`: replace with macOS screen/window enumeration and keyboard state handling.
- `media.py`: replace `gdigrab` and `dshow` with macOS capture/device inputs.
- `app.py`: audit global shortcuts, overlay behavior, and permission prompts.
- Packaging scripts: add macOS bundle generation and signing workflow.

## macOS Capture Strategy

Possible approaches:

- Use ffmpeg `avfoundation` for screen, audio, and camera capture.
- Use platform-specific APIs through PyObjC for permissions, screen metadata, and window handling.
- Add a platform abstraction layer so Windows and macOS capture backends can coexist.

## Permissions

macOS requires user-granted permissions for:

- Screen Recording
- Microphone
- Camera
- Accessibility/Input Monitoring if global keyboard state is needed

## Packaging Strategy

Candidate tools:

- PyInstaller `.app` bundle
- Briefcase or Nuitka if PyInstaller proves fragile

Distribution outside the App Store will likely require:

- Apple Developer ID certificate
- Code signing
- Notarization
- Stapling the notarization ticket

## Effort Estimate

High.

The UI may transfer, but capture and input behavior are platform-specific and central to the product. A clean port should introduce backend interfaces rather than scattering platform checks through the UI.

## Concrete Porting Steps

1. Add a `capture_backends/` abstraction for screen, window, keyboard, and media-device operations.
2. Move current Win32 behavior behind a Windows backend.
3. Prototype ffmpeg `avfoundation` recording on macOS.
4. Prototype macOS screen/window enumeration and permission detection.
5. Replace global `Alt`, `Ctrl`, and `Space` polling with a cross-platform shortcut strategy or separate platform implementations.
6. Build a signed `.app` locally.
7. Test screenshots, framed videos, audio, webcam, annotations, and pause/resume on macOS.
8. Add macOS CI only after local build commands are stable.
