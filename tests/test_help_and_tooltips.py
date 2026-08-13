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


@requires_display
def test_queue_list_row_add_remove_clear_does_not_crash(tmp_path):
    """
    Investigative test prompted by a real bug found elsewhere (the Help
    dialog: destroying a CTkToplevel containing a CTkTextbox synchronously
    inside its own button's click handler intermittently crashed with
    'invalid command name' - see the Help-dialog commit for the full
    story). The queue list also uses a scrollbar-family widget
    (CTkScrollableFrame) and destroys/recreates its row frames on every
    refresh, so it was worth checking for the same class of bug.

    Verdict from testing this directly (not just reasoning about it): a
    real, genuine crash WAS found here - it didn't reproduce in early
    isolated test runs, only under full-test-suite timing/load, which is
    exactly the non-deterministic signature this whole bug category has
    (same as the Help dialog's version of it). Fixed the same way:
    _destroy_row_frame() defers frame.destroy() by one event-loop tick
    instead of destroying synchronously inside the refresh cycle that
    runs several times a second during active downloads.

    This test exercises the fix under deliberately heavy, adversarial
    load - many rounds of rapid add/clear/partial-remove cycles with
    zero delay between them - specifically because the original crash
    only showed up under load, not in a single relaxed pass.
    """
    script = '''
import sys, threading, time
sys.path.insert(0, ".")
import main
from queue_manager import QueueItem, QueueState

app = main.LoopClipApp()

errors = []
def report_callback_exception(self, exc, val, tb):
    errors.append(str(val))
main.LoopClipApp.report_callback_exception = report_callback_exception

def make_item(state, title):
    return QueueItem(
        url="https://youtu.be/fake", quality="best", no_audio=False,
        audio_bitrate="original", video_bitrate="15",
        auto_loop=True, loop_method="combined", similarity=98.0,
        search_start="00:00:00", search_stop="", downsample=1,
        min_loop_seconds=None, max_loop_seconds=None,
        output_folder="C:\\\\fake", delete_original=False,
        seamless_loop=False, crossfade_seconds="3",
        state=state, title=title,
    )

def find_button_in(widget, text):
    for child in widget.winfo_children():
        try:
            if child.cget("text") == text:
                return child
        except Exception:
            pass
        found = find_button_in(child, text)
        if found:
            return found
    return None

def do_test():
    time.sleep(0.3)

    # A normal, realistic pass first: click a real rows own button.
    for it in [make_item(QueueState.QUEUED, f"Item {i}") for i in range(5)]:
        app.queue_manager.items.append(it)
    app._refresh_queue_display()
    app.update()
    remove_btn = find_button_in(app.queue_frame, "Remove")
    if remove_btn:
        remove_btn.invoke()
    app._refresh_queue_display()
    app.update()

    # Then deliberately heavy, adversarial load: many rounds of rapid
    # add/clear/partial-remove cycles with zero delay between them -
    # this is the specific condition that reproduced the original crash.
    for round_num in range(30):
        for i in range(8):
            app.queue_manager.items.append(make_item(QueueState.QUEUED, f"R{round_num}-{i}"))
        app._refresh_queue_display()
        app.queue_manager.items.clear()
        app._refresh_queue_display()
        for i in range(8):
            app.queue_manager.items.append(make_item(QueueState.QUEUED, f"R{round_num}b-{i}"))
        app._refresh_queue_display()
        while len(app.queue_manager.items) > 3:
            app.queue_manager.items.pop()
            app._refresh_queue_display()

    app.update()
    time.sleep(1.0)  # let any deferred (.after(50, ...)) destroys actually fire
    app.update()
    app.queue_manager.items.clear()
    app._refresh_queue_display()
    time.sleep(0.5)
    app.update()

    app.quit()
    print("RESULT|||done")

threading.Thread(target=do_test, daemon=True).start()
app.mainloop()

if errors:
    print("ERRORS:" + "; ".join(errors))
'''
    result = _run_gui_script(script, tmp_path, timeout=60)
    assert "ERRORS:" not in result.stdout, f"{result.stdout}\n{result.stderr}"
    assert "RESULT|||done" in result.stdout, f"{result.stdout}\n{result.stderr}"
