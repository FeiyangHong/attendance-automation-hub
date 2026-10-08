import json
import os
import struct
import subprocess
import tkinter as tk
from pathlib import Path

import pytest

from attendance_hub.desktop import appearance
from attendance_hub.desktop.control_panel import ControlPanel
from attendance_hub.paths import PROJECT_DIR

POWERSHELL = str(
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32/WindowsPowerShell/v1.0/powershell.exe"
)

def test_all_candidate_assets_are_bundled():
    assert [item.id for item in appearance.ICON_CHOICES] == ["c2", "d", "e"]
    for item in appearance.ICON_CHOICES:
        assert appearance.icon_path(item.id, ".png").read_bytes().startswith(b"\x89PNG")
        assert appearance.icon_path(item.id, "-preview.png").is_file()
        header = appearance.icon_path(item.id).read_bytes()[:6]
        reserved, kind, count = struct.unpack("<HHH", header)
        assert (reserved, kind, count) == (0, 1, 7)


@pytest.mark.parametrize("value", ["../other", "b1", "default", "", None])
def test_selection_rejects_unknown_ids(value):
    with pytest.raises(ValueError):
        appearance.icon_choice(value)


def test_preferences_handle_missing_and_invalid_settings(tmp_path):
    assert appearance.load_icon_selection(tmp_path) is None
    path = appearance.preference_path(tmp_path)
    path.parent.mkdir()
    for contents in ("not json", "[]", '{"icon": "../other"}', '{"icon": null}'):
        path.write_text(contents, encoding="utf-8")
        assert appearance.load_icon_selection(tmp_path) is None


def make_project_stub(tmp_path):
    script = tmp_path / "scripts/operations/set_desktop_icon.ps1"
    script.parent.mkdir(parents=True)
    script.touch()
    return appearance.preference_path(tmp_path)


def test_apply_saves_selection_without_overwriting_other_preferences(tmp_path, monkeypatch):
    path = make_project_stub(tmp_path)
    path.parent.mkdir()
    path.write_text('{"extra": true}', encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="Updated 1 shortcut.", stderr="")

    monkeypatch.setattr(appearance.subprocess, "run", fake_run)
    result = appearance.apply_icon_selection("d", tmp_path)
    assert "D" in result
    assert json.loads(path.read_text()) == {"extra": True, "icon": "d"}
    assert appearance.load_icon_selection(tmp_path) == "d"
    command, kwargs = calls[0]
    assert command[-2:] == ["-IconId", "d"]
    assert command[-3].endswith("set_desktop_icon.ps1")
    assert kwargs["cwd"] == tmp_path
    assert not kwargs.get("shell")


@pytest.mark.parametrize("has_previous", [False, True])
def test_failed_apply_restores_previous_settings(tmp_path, monkeypatch, has_previous):
    path = make_project_stub(tmp_path)
    previous = b'{"icon": "e", "other": 3}\n'
    if has_previous:
        path.parent.mkdir()
        path.write_bytes(previous)

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Shortcut failed")

    monkeypatch.setattr(appearance.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Shortcut failed"):
        appearance.apply_icon_selection("c2", tmp_path)
    if has_previous:
        assert path.read_bytes() == previous
    else:
        assert not path.exists()


def test_tk_picker_loads_actual_previews_without_starting_phone_or_tasks():
    root = tk.Tk()
    root.withdraw()
    try:
        panel = object.__new__(ControlPanel)
        panel.root = root
        panel.icon_picker_window = None
        panel.icon_apply_running = False
        panel.selected_icon = None
        panel.open_icon_picker()
        panel.icon_picker_window.withdraw()
        root.update_idletasks()
        assert len(panel.icon_picker_images) == 3
        assert all((img.width(), img.height()) == (128, 128) for img in panel.icon_picker_images)
        assert panel.icon_picker_var.get() == "c2"
        panel.selected_icon = "d"
        panel._set_app_icon("d")
        panel._refresh_icon_picker()
        assert panel.app_icon_image.width() == 128
        assert "D" in panel.icon_picker_current.cget("text")
    finally:
        root.destroy()


def powershell(command):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def shortcut_helper(folder, icon_id="d"):
    script = PROJECT_DIR / "scripts/operations/set_desktop_icon.ps1"
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-IconId", icon_id, "-ShortcutDirectory", str(folder)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows shortcut integration")
def test_shortcut_helper_changes_only_matching_shortcuts_in_isolated_folder(tmp_path):
    own = tmp_path / "Hub renamed.lnk"
    unrelated = tmp_path / "Other app.lnk"
    launcher = PROJECT_DIR / "Attendance Hub.vbs"
    setup = powershell(
        "$ErrorActionPreference='Stop'; $s=New-Object -ComObject WScript.Shell; "
        f"$l=$s.CreateShortcut({ps_quote(own)}); "
        f"$l.TargetPath={ps_quote(launcher)}; $l.Arguments='preserve'; "
        f"$l.WorkingDirectory={ps_quote(PROJECT_DIR)}; "
        "$l.Description='preserve description'; $l.Save(); "
        f"$l=$s.CreateShortcut({ps_quote(unrelated)}); "
        "$l.TargetPath='C:\\Windows\\notepad.exe'; $l.Save()"
    )
    assert setup.returncode == 0, setup.stderr
    original_unrelated = unrelated.read_bytes()
    result = shortcut_helper(tmp_path)
    assert result.returncode == 0, result.stderr
    assert unrelated.read_bytes() == original_unrelated
    read = powershell(
        "$s=New-Object -ComObject WScript.Shell; "
        f"$l=$s.CreateShortcut({ps_quote(own)}); "
        "$l | Select-Object TargetPath,Arguments,WorkingDirectory,Description,IconLocation "
        "| ConvertTo-Json -Compress"
    )
    assert read.returncode == 0, read.stderr
    link = json.loads(read.stdout)
    assert Path(link["TargetPath"]) == launcher
    assert link["Arguments"] == "preserve"
    assert Path(link["WorkingDirectory"]) == PROJECT_DIR
    assert link["Description"] == "preserve description"
    assert link["IconLocation"] == f"{appearance.icon_path('d')},0"


@pytest.mark.skipif(os.name != "nt", reason="Windows shortcut integration")
def test_shortcut_helper_creates_missing_link_in_isolated_folder(tmp_path):
    result = shortcut_helper(tmp_path, "e")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "Attendance Hub.lnk").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows shortcut integration")
def test_shortcut_helper_refuses_to_overwrite_unrelated_same_name(tmp_path):
    unrelated = tmp_path / "Attendance Hub.lnk"
    setup = powershell(
        "$s=New-Object -ComObject WScript.Shell; "
        f"$l=$s.CreateShortcut({ps_quote(unrelated)}); "
        "$l.TargetPath='C:\\Windows\\notepad.exe'; $l.Save()"
    )
    assert setup.returncode == 0, setup.stderr
    previous = unrelated.read_bytes()
    result = shortcut_helper(tmp_path)
    assert result.returncode != 0
    assert unrelated.read_bytes() == previous
