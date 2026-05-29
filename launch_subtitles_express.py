from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    package_dir = Path(__file__).resolve().parent
    parent_dir = package_dir.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    from capture_express.subtitles_app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
