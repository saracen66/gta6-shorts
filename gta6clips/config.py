from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent      # code (the installed skill)
# Workspace: config.yaml, .env, secrets, downloads, renders, DB, logs. Kept outside the skill
# folder so the skill can be updated/reinstalled without touching user data.
ROOT = Path(os.environ.get("GTA6_SHORTS_HOME") or Path.home() / "gta6-shorts").expanduser()
DATA = ROOT / "data"
RAW = DATA / "raw"
WORK = DATA / "work"
OUT = DATA / "out"
SECRETS = ROOT / "secrets"
LOGS = ROOT / "logs"
DB_PATH = DATA / "state.db"

for _d in (DATA, RAW, WORK, OUT, SECRETS, LOGS):
    _d.mkdir(parents=True, exist_ok=True)


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / "config.yaml"
    if not path.exists():
        path = SKILL_DIR / "config.example.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging() -> logging.Logger:
    log = logging.getLogger("gta6clips")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.FileHandler(LOGS / "pipeline.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log
