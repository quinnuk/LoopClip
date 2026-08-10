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


def _xvfb_available():
    return shutil.which("Xvfb") is not None or shutil.which("xvfb-run") is not None


requires_display = pytest.mark.skipif(
    not (tkinter_available and _xvfb_available()),
    reason="tkinter/Xvfb not available for a real GUI test on this machine",
)


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
        ["xvfb-run", "-a", sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(Path(__file__).resolve().parent.parent), timeout=30,
    )
    assert "THREAD_SAFETY_ERROR" not in result.stdout, (
        f"Widget updated unsafely from a background thread:\n{result.stdout}\n{result.stderr}"
    )
    assert "OK" in result.stdout, f"Test script didn't complete normally:\n{result.stdout}\n{result.stderr}"
