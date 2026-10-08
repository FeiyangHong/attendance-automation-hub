from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import time
from pathlib import Path
from uuid import uuid4

from ..paths import PROJECT_DIR

DEFAULT_WINDOW_FILE = PROJECT_DIR / "config" / "morning_window.json"


@dataclass(frozen=True)
class MorningWindow:
    start: str = "09:00"
    end: str = "09:30"

    def __post_init__(self) -> None:
        for value in (self.start, self.end):
            if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise ValueError("Use HH:mm in 24-hour format.")
        if self.start >= self.end:
            raise ValueError("The start must be earlier than the end on the same day.")
        if self.end >= "23:55":
            raise ValueError("The window must end before 23:55 to avoid crossing midnight.")

    @property
    def end_time(self) -> time:
        return time.fromisoformat(self.end)

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def load_morning_window(path: Path = DEFAULT_WINDOW_FILE) -> MorningWindow:
    if not path.exists():
        return MorningWindow()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return MorningWindow(start=data["start"], end=data["end"])


def save_morning_window(window: MorningWindow, path: Path = DEFAULT_WINDOW_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(window.as_dict(), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
