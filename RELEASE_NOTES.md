# Release Notes

## NB Capture Suite v0.1.2-beta

Recommended tag: `v0.1.2`

Status: beta

Supported system:

- Windows 10/11

Not currently supported:

- macOS
- Linux

## Release Assets

Recommended Windows assets:

```text
NBCapture.exe
NBSousTitres.exe
```

The GitHub release workflow packages both executables into:

```text
nb-capture-suite-windows.zip
```

## Build Commands

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -App capture -Name NBCapture
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -App subtitles-cloud -Name NBSousTitres
```

The expected output is under:

```text
release/
```

## Signature Status

Unsigned.

Windows SmartScreen may warn users until the binaries are signed and reputation is established.

## Known Limitations

- NB Capture currently uses Windows capture APIs.
- Video/audio processing requires `ffmpeg`.
- Audio/webcam capture uses DirectShow.
- macOS and Linux support require dedicated ports and have not been tested.
