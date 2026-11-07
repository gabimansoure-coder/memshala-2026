"""Load and save state.yaml (round-trip, so comments and layout survive automated edits)."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state.yaml"
AUTOMATION_PATH = ROOT / "data" / "automation.json"
REGISTRY_PATH = ROOT / "data" / "ics_registry.json"
CONFIG_PATH = ROOT / "config.yaml"
CACHE_DIR = ROOT / ".cache"


def _yaml() -> YAML:
    y = YAML()  # round-trip, YAML 1.2 ("17:00" stays a string)
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_state(path: Path = STATE_PATH) -> Any:
    with open(path, encoding="utf-8") as f:
        return _yaml().load(f)


def dump_state(state: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(state, buf)
    return buf.getvalue()


def save_state(state: Any, path: Path = STATE_PATH) -> None:
    path.write_text(dump_state(state), encoding="utf-8")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_config(path: Path = CONFIG_PATH) -> Any:
    with open(path, encoding="utf-8") as f:
        return YAML(typ="safe").load(f)
