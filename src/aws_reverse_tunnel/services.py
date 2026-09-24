from __future__ import annotations

import json
from pathlib import Path


def load_services(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text()))


def save_services(services: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(services, indent=2, sort_keys=True) + "\n")
