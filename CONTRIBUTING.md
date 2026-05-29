# Contributing

Thanks for helping improve NB Capture Suite.

## Before You Start

- Open an issue for substantial feature work.
- Keep pull requests focused and small.
- Do not include real `.env` files, credentials, private keys, certificates, logs, generated capture sessions, or local personal paths.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Run the app:

```powershell
python launch.py
```

Run tests:

```powershell
python -m unittest discover -s tests
```

Build Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

## Pull Request Checklist

- Tests pass locally.
- User-visible behavior changes are documented.
- New generated files are not committed.
- Security-sensitive changes include notes in `SECURITY_NOTES.md` when relevant.
- macOS compatibility is not claimed unless tested on macOS.

## Coding Notes

- Keep Windows-specific capture code isolated where practical.
- Avoid `shell=True` for subprocess calls.
- Do not add telemetry or network calls without explicit documentation and consent.
