from pathlib import Path

from attendance_hub import system_status


def test_tailscale_can_be_found_beside_custom_service_binary(monkeypatch, tmp_path):
    install = tmp_path / "Custom Tailscale"
    install.mkdir()
    executable = install / "tailscale.exe"
    executable.write_bytes(b"test")

    monkeypatch.setattr(system_status.shutil, "which", lambda _name: None)
    monkeypatch.setattr(system_status.os, "name", "nt")
    monkeypatch.setattr(
        system_status.os.path,
        "expandvars",
        lambda _value: str(install / "tailscaled.exe"),
    )

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeWinreg:
        HKEY_LOCAL_MACHINE = object()

        @staticmethod
        def OpenKey(*_args):
            return FakeKey()

        @staticmethod
        def QueryValueEx(_key, _name):
            return (str(install / "tailscaled.exe"), 1)

    monkeypatch.setitem(__import__("sys").modules, "winreg", FakeWinreg)
    assert Path(system_status._find_tailscale()) == executable
