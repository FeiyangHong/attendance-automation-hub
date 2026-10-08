"""Desktop-only appearance settings; never touches attendance or scheduled tasks."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from attendance_hub.paths import PACKAGE_DIR, PROJECT_DIR


@dataclass(frozen=True)
class IconChoice:
    id: str
    label: str
    description: str


ICON_CHOICES = (
    IconChoice("c2", "C2 · 杏色计划", "温暖杏色，简洁的计划卡"),
    IconChoice("d", "D · 双端连接", "白蓝相扣，表达远程连接"),
    IconChoice("e", "E · 双时段计划", "两行日历，表达上下班计划"),
)
ICON_DIR = PACKAGE_DIR / "desktop" / "assets" / "icons"
_SAVE_LOCK = threading.Lock()


def icon_choice(icon_id: str) -> IconChoice:
    for choice in ICON_CHOICES:
        if choice.id == icon_id:
            return choice
    raise ValueError("Unknown desktop icon choice")


def icon_path(icon_id: str, suffix: str = ".ico") -> Path:
    icon_choice(icon_id)
    if suffix not in {".ico", ".png", "-preview.png"}:
        raise ValueError("Unsupported icon format")
    return ICON_DIR / f"{icon_id}{suffix}"


def preference_path(project_dir: Path = PROJECT_DIR) -> Path:
    return project_dir / "config" / "desktop_appearance.json"


def load_icon_selection(project_dir: Path = PROJECT_DIR) -> str | None:
    try:
        data = json.loads(preference_path(project_dir).read_text(encoding="utf-8"))
        selected = data.get("icon")
        icon_choice(selected)
        return selected
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _replace_bytes(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def apply_icon_selection(icon_id: str, project_dir: Path = PROJECT_DIR) -> str:
    """Persist a choice and update only shortcuts pointing to this checkout's launcher."""
    choice = icon_choice(icon_id)
    script = project_dir / "scripts" / "operations" / "set_desktop_icon.ps1"
    if not script.is_file() or not icon_path(icon_id).is_file():
        raise FileNotFoundError("Desktop icon assets or shortcut helper are missing")
    with _SAVE_LOCK:
        path = preference_path(project_dir)
        previous = path.read_bytes() if path.exists() else None
        try:
            data = json.loads(previous) if previous else {}
            if not isinstance(data, dict):
                data = {}
        except ValueError:
            data = {}
        data["icon"] = icon_id
        _replace_bytes(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
        try:
            powershell = (
                Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            result = subprocess.run(
                [
                    str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                    "Bypass", "-File", str(script), "-IconId", icon_id,
                ],
                cwd=project_dir, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Unable to update desktop shortcut")
        except Exception:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _replace_bytes(path, previous)
            raise
    return f"Desktop icon updated: {choice.label}. {result.stdout.strip()}"
