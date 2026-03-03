import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("footballia")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(LOG_DIR / "footballia.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


logger = setup_logging()


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def get_openai_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "")
    return key


def get_gemini_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "")
    return key


def format_time(seconds: float) -> str:
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_time(time_str: str) -> float:
    parts = time_str.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return float(parts[0])


def get_active_categories(task: dict) -> list[str]:
    """Extract category value list from a task template.
    Handles both plain string lists and dict lists with 'value' keys."""
    cats = task.get("categories", [])
    if not cats:
        return []
    if isinstance(cats[0], str):
        return list(cats)
    return [c["value"] for c in cats]


def get_active_category_descriptions(task: dict) -> dict[str, str]:
    """Extract {value: label} mapping from a task template.
    Handles both plain string lists and dict lists with 'value'/'label' keys."""
    cats = task.get("categories", [])
    if not cats:
        return {}
    if isinstance(cats[0], str):
        descs = task.get("category_descriptions", {})
        return {c: descs.get(c, c) for c in cats}
    return {c["value"]: c.get("label", c["value"]) for c in cats}


# Default categories — used when no task is loaded (backward compat)
DEFAULT_CATEGORIES = [
    "WIDE_CENTER",
    "WIDE_LEFT",
    "WIDE_RIGHT",
    "MEDIUM",
    "CLOSEUP",
    "BEHIND_GOAL",
    "AERIAL",
    "OTHER",
]

DEFAULT_CATEGORY_DESCRIPTIONS = {
    "WIDE_CENTER": "Main broadcast camera, full pitch view",
    "WIDE_LEFT": "Broadcast camera panned to follow left side",
    "WIDE_RIGHT": "Broadcast camera panned to follow right side",
    "MEDIUM": "Tighter zone shot, 3-7 players",
    "CLOSEUP": "Player faces, celebrations, reactions",
    "BEHIND_GOAL": "View from behind the goal line",
    "AERIAL": "Spider cam, overhead bird's eye view",
    "OTHER": "Crowd, graphics, scoreboard, replays",
}

# Backward compatibility aliases
CAMERA_TYPES = DEFAULT_CATEGORIES
CAMERA_DESCRIPTIONS = DEFAULT_CATEGORY_DESCRIPTIONS
