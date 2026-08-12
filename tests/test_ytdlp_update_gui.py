import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from test_main import _can_run_gui, _wrap_for_display  # reuse the established display-detection helpers

requires_display = pytest.mark.skipif(
    not _can_run_gui(), reason="no real or virtual display available for a GUI test on this machine"
)


def _network_available() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=3).close()
        return True
    except OSError:
        return False


requires_display_and_network = pytest.mark.skipif(
    not (_can_run_gui() and _network_available()),
    reason="needs both a display and network access to pypi.org",
)


def _run_gui_script(script: str, tmp_path, timeout=40) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["APPDATA"] = str(tmp_path / "AppData")
    return subprocess.run(
        _wrap_for_display([sys.executable, "-c", script]),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(Path(__file__).resolve().parent.parent), timeout=timeout, env=env,
    )


@requires_display_and_network
def test_update_now_button_performs_a_real_verified_update(tmp_path):
    """
    End-to-end regression test for the yt-dlp update feature: clicking
    "Update Now" on a real update banner actually downloads, verifies,
    and applies a real update via ytdlp_updater.py, without crashing the
    UI thread (this exercises the exact self.after(0, ...) cross-thread
    pattern that previously caused a real 'main thread is not in main
    loop' error elsewhere in this app - see test_about_dialog_* above).
    """
    script = '''
import sys, os, time, threading
sys.path.insert(0, ".")
import main
import ytdlp_updater as upd

app = main.LoopClipApp()
info = upd.check_for_update(current_version="2020.01.01")
if info is None:
    print("RESULT|||SKIP|||no update available to test against")
    sys.exit(0)

app._show_update_notice(info)

errors = []
def report_callback_exception(self, exc, val, tb):
    errors.append(str(val))
main.LoopClipApp.report_callback_exception = report_callback_exception

results = {}
def do_test():
    time.sleep(0.5)
    app._start_ytdlp_update(info)
    time.sleep(6)
    results["notice_text"] = app._update_notice_label.cget("text")
    root = upd._override_root()
    active_path = root / "active.json"
    import json as json_module
    results["active_version"] = (
        json_module.loads(active_path.read_text()).get("version", "")
        if active_path.exists() else ""
    )
    app.quit()

threading.Thread(target=do_test, daemon=True).start()
app.mainloop()

SEP = "|||"
if errors:
    print("RESULT" + SEP + "ERROR" + SEP + "; ".join(errors))
else:
    print("RESULT" + SEP + "OK" + SEP + results["notice_text"] + SEP + results["active_version"] + SEP + info.latest_version)
'''
    result = _run_gui_script(script, tmp_path)
    line = next((l for l in result.stdout.splitlines() if l.startswith("RESULT|||")), None)
    assert line is not None, f"script didn't complete:\n{result.stdout}\n{result.stderr}"

    if line.startswith("RESULT|||SKIP|||"):
        pytest.skip(line)

    assert line.startswith("RESULT|||OK|||"), f"UI thread error during update:\n{line}"
    _, _, notice_text, active_version, latest_version = line.split("|||", 4)
    assert "Restart LoopClip" in notice_text
    assert active_version == latest_version


@requires_display
def test_skip_button_dismisses_banner_and_persists_choice(tmp_path):
    script = '''
import sys
sys.path.insert(0, ".")
import main
import settings as settings_module
from ytdlp_updater import UpdateInfo

app = main.LoopClipApp()
fake_info = UpdateInfo(
    current_version="1.0.0", latest_version="9999.99.99",
    wheel_url="https://example.invalid/fake.whl", sha256="0" * 64, size_bytes=100,
)
app._show_update_notice(fake_info)
assert app.update_notice_frame is not None

app._skip_ytdlp_update(fake_info)
assert app.update_notice_frame is None, "banner should be dismissed"

reloaded = settings_module.load_settings()
assert reloaded["skipped_ytdlp_version"] == "9999.99.99"

# Re-checking for the SAME version should not show the banner again
app.cfg = reloaded
import ytdlp_updater as upd
orig_check = upd.check_for_update
upd.check_for_update = lambda *a, **k: fake_info
try:
    app._check_yt_dlp_update()
finally:
    upd.check_for_update = orig_check
app.update()
assert app.update_notice_frame is None

app.destroy()
print("RESULT:OK")
'''
    result = _run_gui_script(script, tmp_path)
    assert "RESULT:OK" in result.stdout, f"{result.stdout}\n{result.stderr}"


@requires_display
def test_about_dialog_checkbox_persists_setting(tmp_path):
    script = '''
import sys
sys.path.insert(0, ".")
import main
import settings as settings_module
import tkinter as tk
import customtkinter as ctk

app = main.LoopClipApp()
assert app.cfg.get("auto_check_ytdlp_updates", True) is True

app._show_about_dialog()
dialog = next(w for w in app.winfo_children() if isinstance(w, tk.Toplevel))

def find_checkbox(widget):
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkCheckBox):
            return child
        found = find_checkbox(child)
        if found:
            return found
    return None

cb = find_checkbox(dialog)
assert cb is not None, "checkbox not found in About dialog"
assert "yt-dlp updates" in cb.cget("text")

cb.deselect()
cb.cget("command")()

reloaded = settings_module.load_settings()
assert reloaded["auto_check_ytdlp_updates"] is False

app.destroy()
print("RESULT:OK")
'''
    result = _run_gui_script(script, tmp_path)
    assert "RESULT:OK" in result.stdout, f"{result.stdout}\n{result.stderr}"


@requires_display
def test_update_check_skipped_entirely_when_disabled(tmp_path):
    script = '''
import sys
sys.path.insert(0, ".")
import main
import ytdlp_updater as upd
from ytdlp_updater import UpdateInfo

app = main.LoopClipApp()
app.cfg["auto_check_ytdlp_updates"] = False

fake_info = UpdateInfo(
    current_version="1.0.0", latest_version="9999.99.98",
    wheel_url="https://example.invalid/fake.whl", sha256="0" * 64, size_bytes=100,
)
orig_check = upd.check_for_update
upd.check_for_update = lambda *a, **k: fake_info  # would definitely trigger a banner if not gated
try:
    app._check_yt_dlp_update()
finally:
    upd.check_for_update = orig_check
app.update()

assert app.update_notice_frame is None, "update check should be a no-op when disabled in settings"
app.destroy()
print("RESULT:OK")
'''
    result = _run_gui_script(script, tmp_path)
    assert "RESULT:OK" in result.stdout, f"{result.stdout}\n{result.stderr}"


@requires_display
def test_update_failure_shows_error_and_resets_banner(tmp_path, monkeypatch):
    """Regression check that a real UpdateError during the download/apply
    step results in a clean failure state (banner removed, no crash) -
    not an unhandled exception on the background thread. messagebox is
    monkeypatched within the subprocess script to avoid blocking on a
    real native dialog during an automated test."""
    script = '''
import sys, time, threading
sys.path.insert(0, ".")
import main
from ytdlp_updater import UpdateInfo, UpdateError

shown_errors = []
main.messagebox.showerror = lambda title, msg: shown_errors.append((title, msg))

app = main.LoopClipApp()
fake_info = UpdateInfo(
    current_version="1.0.0", latest_version="9999.99.97",
    wheel_url="https://pypi.org/nonexistent-path-that-will-404", sha256="0" * 64, size_bytes=100,
)
app._show_update_notice(fake_info)

errors = []
def report_callback_exception(self, exc, val, tb):
    errors.append(str(val))
main.LoopClipApp.report_callback_exception = report_callback_exception

def do_test():
    time.sleep(0.5)
    app._start_ytdlp_update(fake_info)
    time.sleep(4)
    app.quit()

threading.Thread(target=do_test, daemon=True).start()
app.mainloop()

if errors:
    print("RESULT:THREAD_ERROR:" + "; ".join(errors))
elif app.update_notice_frame is not None:
    print("RESULT:BANNER_NOT_CLEARED")
elif not shown_errors:
    print("RESULT:NO_ERROR_SHOWN")
else:
    print("RESULT:OK")
'''
    result = _run_gui_script(script, tmp_path)
    assert "RESULT:OK" in result.stdout, f"{result.stdout}\n{result.stderr}"
