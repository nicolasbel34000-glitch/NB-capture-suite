from __future__ import annotations

import sys
from pathlib import Path


def install_paths() -> None:
    package_dir = Path(__file__).resolve().parents[1]
    parent_dir = package_dir.parent
    for path in (str(package_dir), str(parent_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)
