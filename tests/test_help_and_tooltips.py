import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_main import _can_run_gui, _wrap_for_display

requires_display = pytest.mark.skipif(
    not _can_run_gui(), reason="no real or virtual display available for a GUI test on this machine"
)


def _run_gui_script(script: str, tmp_path, timeout=30) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["APPDATA"] = str(tmp_path / "AppData")
    return subprocess.run(
        _wrap_for_display([sys.executable, "-c", script]),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(Path(__file__).resolve().parent.parent), timeout=timeout, env=env,
    )


@requires_display
def test_help_button_opens_dialog_with_content_and_visible_close_button(tmp_path):
    script = '''
import sys, threading, time
sys.path.insert(0, ".")
import main
import tkinter as tk

app = main.LoopClipApp()

errors = []
def report_callback_exception(self, exc, val, tb):
    errors.append(str(val))
main.LoopClipApp.report_callback_exception = report_callback_exception

def find_widget(widget, text):
    for child in widget.winfo_children():
        try:
            if child.cget("text") == text:
                return child
        except Exception:
            pass
        found = find_widget(child, text)
        if found:
            return found
    return None

def do_test():
    time.sleep(0.5)
    help_btn = find_widget(app, "Help")
    help_btn.invoke()
    time.sleep(0.3)
    app.update()

    dialog = next((w for w in app.winfo_children() if isinstance(w, tk.Toplevel)), None)
    close_btn = find_widget(dialog, "Close") if dialog else None
    content_ok = False
    close_visible = False
    if dialog is not None and close_btn is not None:
        text_widgets = [c for c in dialog.winfo_children() if isinstance(c, __import__("customtkinter").CTkTextbox)]
        if text_widgets:
            content = text_widgets[0].get("1.0", "end")
            content_ok = "Download Quality" in content and "Similarity" in content and "crossfade" in content.lower()
        btn_bottom = close_btn.winfo_y() + close_btn.winfo_height()
        close_visible = btn_bottom <= dialog.winfo_height()
        close_btn.invoke()

    time.sleep(0.3)
    app.update()
    app.quit()

    print("RESULT|||dialog=" + str(dialog is not None) + "|||content_ok=" + str(content_ok) + "|||close_visible=" + str(close_visible))

threading.Thread(target=do_test, daemon=True).start()
app.mainloop()

if errors:
    print("ERRORS:" + "; ".join(errors))
'''
    result = _run_gui_script(script, tmp_path)
    assert "ERRORS:" not in result.stdout, f"{result.stdout}\n{result.stderr}"
    line = next((l for l in result.stdout.splitlines() if l.startswith("RESULT|||")), None)
    assert line is not None, f"{result.stdout}\n{result.stderr}"
    assert "dialog=True" in line
    assert "content_ok=True" in line
    assert "close_visible=True" in line


@requires_display
def test_tooltip_appears_on_hover_and_disappears_on_leave(tmp_path):
    """
    Uses label._canvas.event_generate(...) rather than
    label.event_generate(...) - CTkLabel.bind() forwards bindings to its
    internal canvas/label sub-widgets (confirmed by reading
    customtkinter's own source), which is what a real mouse cursor
    actually lands on, not the outer CTkLabel wrapper object. Targeting
    the outer object directly doesn't reach the binding at all - this
    matters for the test to be a faithful simulation of a real hover,
    not just for it to pass.
    """
    script = '''
import sys, threading, time
sys.path.insert(0, ".")
import main
import tkinter as tk

app = main.LoopClipApp()

errors = []
def report_callback_exception(self, exc, val, tb):
    errors.append(str(val))
main.LoopClipApp.report_callback_exception = report_callback_exception

def find_widget(widget, text):
    for child in widget.winfo_children():
        try:
            if child.cget("text") == text:
                return child
        except Exception:
            pass
        found = find_widget(child, text)
        if found:
            return found
    return None

def do_test():
    time.sleep(0.5)
    label = find_widget(app, "Similarity %")
    label._canvas.event_generate("<Enter>")
    time.sleep(0.6)  # past the 400ms show delay
    app.update()
    tip_visible = len(app.tk.call("wm", "stackorder", ".")) > 1

    label._canvas.event_generate("<Leave>")
    time.sleep(0.3)
    app.update()
    tip_gone = len(app.tk.call("wm", "stackorder", ".")) == 1

    app.quit()
    print("RESULT|||tip_visible=" + str(tip_visible) + "|||tip_gone=" + str(tip_gone))

threading.Thread(target=do_test, daemon=True).start()
app.mainloop()

if errors:
    print("ERRORS:" + "; ".join(errors))
'''
    result = _run_gui_script(script, tmp_path)
    assert "ERRORS:" not in result.stdout, f"{result.stdout}\n{result.stderr}"
    line = next((l for l in result.stdout.splitlines() if l.startswith("RESULT|||")), None)
    assert line is not None, f"{result.stdout}\n{result.stderr}"
    assert "tip_visible=True" in line
    assert "tip_gone=True" in line


@requires_display
def test_tooltip_does_not_appear_before_the_delay_elapses(tmp_path):
    """A quick pass-through hover (shorter than the 400ms delay) should
    not show a tooltip - this is what stops tooltips flashing into view
    while the cursor is just passing over a widget on its way elsewhere."""
    script = '''
import sys, threading, time
sys.path.insert(0, ".")
import main

app = main.LoopClipApp()

def find_widget(widget, text):
    for child in widget.winfo_children():
        try:
            if child.cget("text") == text:
                return child
        except Exception:
            pass
        found = find_widget(child, text)
        if found:
            return found
    return None

def do_test():
    time.sleep(0.5)
    label = find_widget(app, "Similarity %")
    label._canvas.event_generate("<Enter>")
    time.sleep(0.1)  # well under the 400ms delay
    label._canvas.event_generate("<Leave>")
    time.sleep(0.5)  # now past when it WOULD have shown, if not cancelled
    app.update()
    tip_never_shown = len(app.tk.call("wm", "stackorder", ".")) == 1
    app.quit()
    print("RESULT|||tip_never_shown=" + str(tip_never_shown))

threading.Thread(target=do_test, daemon=True).start()
app.mainloop()
'''
    result = _run_gui_script(script, tmp_path)
    assert "tip_never_shown=True" in result.stdout, f"{result.stdout}\n{result.stderr}"
