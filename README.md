# NB Capture Suite

NB Capture Suite contains two Windows desktop tools:

- **NB Capture**: screenshots, screen recordings, microphone audio, webcam overlay, annotations, scripts/prompter, and optional logo overlay.
- **NB Sous-titres**: subtitle generation, manual `.srt` correction, subtitle review, and video export with burned-in subtitles.

The project is published as GPL software so contributors can port it to macOS and Linux.

## Platform Status

- Windows: supported.
- macOS: not supported yet. Contributions welcome.
- Linux: not supported yet. Contributions welcome.

See `MACOS_PORTING.md` for porting notes.

## User Installation

The recommended user install path is GitHub Releases.

1. Open the repository Releases page.
2. Download the Windows executables.
3. Run `NBCapture.exe` or `NBSousTitres.exe`.

Current release status: beta and unsigned. Windows SmartScreen may show a warning.

## Developer Installation

Requirements:

- Windows 10/11
- Python 3.11 or newer
- `ffmpeg` on `PATH`, or `bin/ffmpeg/ffmpeg.exe` inside the project

Setup:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

## Run NB Capture

```powershell
python launch.py
```

Windows launcher:

```bat
run_capture_express.bat
```

## Run NB Sous-titres

Full GitHub build with local-engine hooks:

```powershell
python launch_subtitles_express.py
```

Cloud-only build:

```powershell
python launch_subtitles_express_cloud.py
```

Windows launcher:

```bat
run_subtitles_express.bat
```

## NB Sous-titres Engines

Supported first-pass transcription engines:

- OpenAI audio transcription. Use `whisper-1` when you need timestamped `.srt` output.
- Mistral Voxtral transcription.
- Optional local Whisper through `faster-whisper`.

Claude and Ollama are treated as correction/review engines for an existing `.srt`; they are not direct speech-to-text engines in this app. Google Speech-to-Text is listed for future integration because production use usually needs Google Cloud project credentials rather than only a pasted API key.

Distribution plan:

- GitHub GPL build: cloud engines plus optional local hooks for users with enough hardware.
- Downloadable Windows exe: cloud-only workflow, with local engines hidden to keep setup simple.

Local engine guidance:

- 8 GB RAM or less: use Whisper `tiny` or `base` in CPU/int8 mode; expect slower processing.
- 8-16 GB RAM: Whisper `base` or `small` can be practical depending on video length.
- Ollama is useful for correcting an existing SRT with small 1.5B/3B models. It is not the speech-to-text engine.
- 7B+ local LLMs are not recommended for the average low-memory machine.

## Test

```powershell
python -m unittest discover -s tests
```

## Build Windows

Build NB Capture:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -App capture -Name NBCapture
```

Build the cloud-only NB Sous-titres exe:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -App subtitles-cloud -Name NBSousTitres
```

Build the GitHub/dev NB Sous-titres exe with local-engine hooks visible:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -App subtitles-github -Name NBSousTitres-GitHub
```

Expected outputs:

```text
release/NBCapture.exe
release/NBSousTitres.exe
```

## NB Capture Shortcuts

- `Espace`: screenshot.
- Hold `Espace`: record video while held.
- `Ctrl+Espace`: start/stop long video.
- `Shift+Ctrl+Espace`: start/stop long video with audio.
- `Ctrl+drag`: define a capture frame.
- Short `Alt`: add or finish a text annotation.
- Hold `Alt`: highlighter mode.
- `Echap`: leave active capture mode.

## Project Structure

```text
capture_express/app.py         NB Capture PyQt UI and active capture overlays
capture_express/media.py       screenshots, ffmpeg recording/finalization, devices
capture_express/subtitles_app.py NB Sous-titres PyQt UI
capture_express/subtitle_engine.py subtitle transcription, SRT, review, and burn-in helpers
capture_express/windowing.py   Windows monitor/window helpers
capture_express/session.py     session manifests and timeline events
capture_express/models.py      shared dataclasses
launch.py                      NB Capture launcher
launch_subtitles_express.py    NB Sous-titres GitHub launcher
launch_subtitles_express_cloud.py NB Sous-titres cloud-only launcher
tests/                         Python regression tests
scripts/                       build scripts
```

## Privacy

API keys used by NB Sous-titres are stored through the operating system credential manager when available. They are not stored in this repository.

Generated recordings, screenshots, build outputs, release outputs, virtual environments, and `.env` files are ignored by Git.

## Contributing

Issues and pull requests are welcome. Read `CONTRIBUTING.md` before opening a PR.

For security issues, do not open a public issue. See `SECURITY.md`.

## License

GPL-3.0-or-later. See `LICENSE`.
