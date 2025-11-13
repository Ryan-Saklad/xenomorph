"""
Notification chime task (lean version).

Platforms:
- macOS: afplay system sounds (Glass/Ping/Sosumi/Tink)
- Windows: winsound.MessageBeep
- POSIX/Linux: terminal BEL via printf
"""

import platform
import subprocess
from shutil import which as find_exe

from xenohooks.common.exec import run_command
from xenohooks.common.types import Action


def _resolve_sound_for_context(payload: dict) -> tuple[str, list[str]]:
    """Return (label, mac_paths) chosen from payload context."""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    # notif = str(tool_input.get("notification_type") or "").lower()
    event = str(payload.get("hook_event_name") or "").lower()

    if event in {"stop", "sessionend"}:
        return "completion", ["/System/Library/Sounds/Glass.aiff"]

    return "notification", ["/System/Library/Sounds/Ping.aiff"]


def _mac_play(sound_files: list[str]) -> tuple[bool, str]:
    if find_exe("afplay") is None:
        return False, "afplay not found"
    for path in sound_files:
        try:
            subprocess.Popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, f"Started {path}"
        except Exception:
            continue
    return False, "no playable system sound"


def _windows_beep() -> tuple[bool, str]:
    try:
        import winsound  # type: ignore
        winsound.MessageBeep()  # type: ignore[attr-defined]
        return True, "MessageBeep"
    except Exception as e:
        return False, f"winsound: {e}"


def _posix_bell() -> tuple[bool, str]:
    r = run_command(r"printf '\a'", shell=True, timeout_seconds=1)
    return (r.code == 0, "BEL" if r.code == 0 else (r.err or "BEL failed"))


def run(payload: dict) -> Action:
    """
    Play a platform-appropriate chime. Never BLOCKs; returns OK/WARN.
    """
    label, mac_paths = _resolve_sound_for_context(payload)
    sysname = platform.system().lower()

    played: bool = False
    info: str = ""

    if "darwin" in sysname or sysname == "macos" or sysname == "mac os x":
        ok, msg = _mac_play(mac_paths or [])
        played, info = ok, msg
    elif "windows" in sysname:
        ok, msg = _windows_beep()
        played, info = ok, msg
    else:
        ok, msg = _posix_bell()
        played, info = ok, msg

    if played:
        return Action()

    return Action()
