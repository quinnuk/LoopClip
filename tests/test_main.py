import platform
import shutil
import sys
from pathlib import Path

import pytest

tkinter_available = True
try:
    import tkinter  # noqa: F401
except ImportError:
    tkinter_available = False

requires_tkinter = pytest.mark.skipif(
    not tkinter_available, reason="tkinter not available on this machine"
)


@requires_tkinter
def test_safe_float_valid_number():
    import main
    assert main._safe_float("55.5", 98.0) == 55.5


@requires_tkinter
def test_safe_float_falls_back_on_garbage():
    """Regression test: QueueItem construction and settings-saving both
    previously called float() directly on the similarity entry's raw
    text, unconditionally - even when auto-loop was off and the field
    was never validated - crashing the 'Add to Queue' button handler on
    non-numeric input. _safe_float() must never raise."""
    import main
    assert main._safe_float("not a number", 98.0) == 98.0


@requires_tkinter
def test_safe_float_falls_back_on_empty_string():
    import main
    assert main._safe_float("", 98.0) == 98.0


@requires_tkinter
def test_safe_float_falls_back_on_none():
    import main
    assert main._safe_float(None, 98.0) == 98.0


def _can_run_gui():
    """
    Whether a real Tk display is available to actually run (not just
    import) GUI code in this run:
    - Windows CI (windows-latest) always has an interactive desktop
      session available, so GUI code can run directly there.
    - Linux (this sandbox, most local dev machines, self-hosted CI) has
      no display by default and needs Xvfb as a virtual one.
    - macOS is assumed to have a display (headless macOS CI is rare and
      not a target platform for this Windows-focused app).
    """
    if not tkinter_available:
        return False
    if platform.system() == "Windows" or platform.system() == "Darwin":
        return True
    return shutil.which("Xvfb") is not None and shutil.which("xvfb-run") is not None


requires_display = pytest.mark.skipif(
    not _can_run_gui(), reason="no real or virtual display available for a GUI test on this machine"
)


def _wrap_for_display(cmd: list) -> list:
    """Runs `cmd` directly on Windows/macOS (a real display is already
    available there); wraps it with xvfb-run on Linux, where one isn't."""
    if platform.system() in ("Windows", "Darwin"):
        return cmd
    return ["xvfb-run", "-a", *cmd]


@requires_display
def test_about_dialog_nvenc_update_is_thread_safe():
    """
    Smoke test for a real bug found while building the About dialog: its
    background NVENC check originally called a CTkLabel's .configure()
    directly from the background thread that runs the check. Tkinter/Tcl
    is not thread-safe - touching a widget from any thread other than the
    one running mainloop() is undefined behavior, confirmed directly by
    reproducing a "main thread is not in main loop" RuntimeError from
    this exact code before the fix (self.after(0, ...) to marshal the
    update back onto the main thread, matching the pattern already used
    elsewhere in this file for the same reason).

    Note: this specific failure is a genuine race condition, not
    deterministic - across repeated manual runs, both the original buggy
    code and this test sometimes crashed and sometimes didn't, depending
    on exact thread scheduling. So this test won't reliably catch a
    reintroduction of the same bug every single run; treat it as a smoke
    test that the dialog + threaded check work end-to-end without error
    in this run, not as a guaranteed regression gate. The important
    protection here is the code pattern itself (self.after everywhere a
    background thread needs to touch a widget), not this test.
    """
    import subprocess
    script = '''
import sys, threading, time
sys.path.insert(0, ".")
import main

app = main.LoopClipApp()
app._show_about_dialog()

errors = []
def report_callback_exception(self, exc, val, tb):
    errors.append(str(val))
main.LoopClipApp.report_callback_exception = report_callback_exception

def run_and_quit():
    time.sleep(2.0)
    app.quit()

threading.Thread(target=run_and_quit, daemon=True).start()
app.mainloop()

if errors:
    print("THREAD_SAFETY_ERROR:" + "; ".join(errors))
else:
    print("OK")
'''
    result = subprocess.run(
        _wrap_for_display([sys.executable, "-c", script]),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(Path(__file__).resolve().parent.parent), timeout=30,
    )
    assert "THREAD_SAFETY_ERROR" not in result.stdout, (
        f"Widget updated unsafely from a background thread:\n{result.stdout}\n{result.stderr}"
    )
    assert "OK" in result.stdout, f"Test script didn't complete normally:\n{result.stdout}\n{result.stderr}"
