"""
main.py
-------
LoopClip - main application window.

Queue-based dark-themed UI (restyled to match the Video Downloader Pro
look: charcoal background, teal accent buttons, header + subtitle, and a
two-column options layout with a segmented quality selector):
  1. Paste a YouTube URL (auto-filled from clipboard if one is copied).
  2. Choose quality / audio / trim / output settings.
  3. Click "Add to Queue" as many times as you like for different videos.
  4. Click "Start Queue" - videos download and process one at a time.
  5. "Cancel" stops the current video immediately and halts the queue.
"""

import os
import platform
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

# Must run before any import that pulls in yt_dlp (queue_manager ->
# downloader -> yt_dlp) - if a previously-downloaded yt-dlp update was
# applied on a prior run, this makes `import yt_dlp` resolve to it
# instead of whatever's frozen into the .exe / pip-installed. A no-op if
# no update has ever been applied.
import ytdlp_updater
ytdlp_updater.activate_pending_override()

import processor
import settings as settings_module
from queue_manager import QueueItem, QueueManager, QueueState
from tool_check import check_ffmpeg, check_nvenc, get_ffmpeg_version, missing_tools_message
from version import __version__

try:
    import pyperclip
    HAS_CLIPBOARD = True
except ImportError:
    HAS_CLIPBOARD = False

# Importing downloader only for URL validation - queue_manager owns the
# actual download/process calls so UI code never talks to yt-dlp/FFmpeg
# directly.
import downloader

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

# Same palette as Video Downloader Pro: lifted charcoal background with a
# teal accent, kept distinct from the "Cancel" and per-item state colors
# used in the queue list.
COLOR_BG = "#1c1c1e"
COLOR_PANEL = "#26262a"
COLOR_ACCENT = "#0f6e56"
COLOR_ACCENT_HOVER = "#0c5744"
COLOR_CANCEL = "#8B2C2C"
COLOR_CANCEL_HOVER = "#6E2323"

TEMP_ROOT = Path(tempfile.gettempdir()) / "LoopClip" / "Temp"

CLIPBOARD_POLL_MS = 150  # near-instant clipboard pickup


def _safe_float(text: str, default: float) -> float:
    """
    Parses `text` as a float, falling back to `default` instead of raising
    if it isn't a valid number.

    Used for fields whose value only matters when a related option is
    enabled (e.g. the similarity box when auto-loop detection is off) -
    _validate_loop_settings() already blocks "Add to Queue" with a clear
    message when that field's value *does* matter and is invalid, but
    when it doesn't matter, silently falling back here (rather than still
    hard-parsing it) avoids the field crashing item creation over a value
    the user isn't even using.
    """
    try:
        return float(text)
    except (TypeError, ValueError):
        return default

STATE_COLORS = {
    QueueState.QUEUED: "#9AA0A6",
    QueueState.DOWNLOADING: "#3B8EEA",
    QueueState.ANALYZING: "#3BB8EA",
    QueueState.TRIMMING: "#E8A33D",
    QueueState.LOOPING: "#B47EE8",
    QueueState.FINISHED: "#3FB950",
    QueueState.ERROR: "#F85149",
    QueueState.CANCELLED: "#8B8F98",
}

# LoopyCut-style method options for automatic loop detection.
LOOP_METHOD_LABELS = [
    ("combined", "Combined (recommended)"),
    ("ssim", "SSIM (structural)"),
    ("histogram", "Histogram (color)"),
    ("hash", "Hash (fastest)"),
]

DOWNSAMPLE_LABELS = [
    ("1", "Every frame"),
    ("2", "Every 2nd frame"),
    ("4", "Every 4th frame"),
    ("8", "Every 8th frame"),
]

AUDIO_BITRATE_LABELS = [
    ("original", "Original"),
    ("128", "128 kbps"),
    ("192", "192 kbps"),
    ("256", "256 kbps"),
    ("320", "320 kbps"),
]

VIDEO_BITRATE_LABELS = [
    ("8", "8 Mbps"),
    ("15", "15 Mbps"),
    ("25", "25 Mbps"),
    ("40", "40 Mbps"),
]

CROSSFADE_LABELS = [
    ("1", "1 sec"),
    ("2", "2 sec"),
    ("3", "3 sec"),
]

QUALITY_LABELS = {
    "best": "Best Available",
    "4k_hdr": "4K HDR Preferred",
    "1080p": "1080p Compatible",
}


class _TimeEntry(ctk.CTkFrame):
    """
    A compact HH:MM:SS input made of three linked digit boxes instead of a
    single free-text field. This removes the most common source of the
    "invalid start time / duration" error dialog - wrong separators,
    missing leading zeros, stray spaces - since each box only ever accepts
    digits, auto-advances to the next box once two digits are typed, and
    self-corrects out-of-range values (minutes/seconds above 59) when you
    click away. get()/set() still work with plain "HH:MM:SS" strings so
    the rest of the app doesn't need to change.
    """

    def __init__(self, master, initial: str = "00:00:00", on_change=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_change = on_change
        vcmd = (self.register(self._validate_digits), "%P")

        box_kwargs = dict(
            width=40, justify="center", fg_color=COLOR_PANEL,
            validate="key", validatecommand=vcmd,
        )
        caption_kwargs = dict(font=ctk.CTkFont(size=9), text_color="gray55")

        def _box_with_caption(caption_text: str) -> ctk.CTkEntry:
            col = ctk.CTkFrame(self, fg_color="transparent")
            col.pack(side="left")
            box = ctk.CTkEntry(col, **box_kwargs)
            box.pack()
            ctk.CTkLabel(col, text=caption_text, **caption_kwargs).pack()
            return box

        self.hh = _box_with_caption("H")
        ctk.CTkLabel(self, text=":", width=6).pack(side="left", pady=(0, 14))
        self.mm = _box_with_caption("M")
        ctk.CTkLabel(self, text=":", width=6).pack(side="left", pady=(0, 14))
        self.ss = _box_with_caption("S")

        self.set(initial)

        def _changed(e=None):
            if self._on_change is not None:
                self._on_change()

        self.hh.bind("<KeyRelease>", lambda e: (self._maybe_advance(self.hh, self.mm), _changed()))
        self.mm.bind("<KeyRelease>", lambda e: (self._maybe_advance(self.mm, self.ss), _changed()))
        self.ss.bind("<KeyRelease>", _changed)
        self.hh.bind("<FocusOut>", lambda e: (self._pad(self.hh), _changed()))
        self.mm.bind("<FocusOut>", lambda e: (self._clamp(self.mm), _changed()))
        self.ss.bind("<FocusOut>", lambda e: (self._clamp(self.ss), _changed()))

    @staticmethod
    def _validate_digits(proposed: str) -> bool:
        """Tk 'validate=key' callback - only allow up to 2 digits per box,
        nothing else (no letters, colons, symbols)."""
        return proposed == "" or (proposed.isdigit() and len(proposed) <= 2)

    @staticmethod
    def _maybe_advance(box: ctk.CTkEntry, next_box: ctk.CTkEntry):
        if len(box.get()) >= 2:
            next_box.focus_set()
            next_box.select_range(0, "end")

    @staticmethod
    def _pad(box: ctk.CTkEntry):
        value = box.get().strip()
        box.delete(0, "end")
        box.insert(0, value.zfill(2) if value else "00")

    @staticmethod
    def _clamp(box: ctk.CTkEntry):
        """Minutes/seconds are capped at 59 rather than left invalid."""
        value = box.get().strip()
        num = max(0, min(59, int(value) if value.isdigit() else 0))
        box.delete(0, "end")
        box.insert(0, f"{num:02d}")

    def get(self) -> str:
        h = self.hh.get().strip()
        m = self.mm.get().strip()
        s = self.ss.get().strip()
        h_val = int(h) if h.isdigit() else 0
        m_val = max(0, min(59, int(m) if m.isdigit() else 0))
        s_val = max(0, min(59, int(s) if s.isdigit() else 0))
        return f"{h_val:02d}:{m_val:02d}:{s_val:02d}"

    def set(self, value: str):
        try:
            h, m, s = value.strip().split(":")
        except (ValueError, AttributeError):
            h, m, s = "0", "0", "0"
        for box, v in ((self.hh, h), (self.mm, m), (self.ss, s)):
            v = v.strip() if v.strip().isdigit() else "0"
            box.delete(0, "end")
            box.insert(0, v.zfill(2))

    def configure_state(self, state: str):
        for box in (self.hh, self.mm, self.ss):
            box.configure(state=state)


class _ToolTip:
    """
    A small hover tooltip for any widget - shows `text` in a borderless
    popup near the cursor after a short delay, and hides it again on
    mouse-leave or click. CustomTkinter has no built-in tooltip widget,
    so this is a minimal one built directly on Tk's <Enter>/<Leave>
    events rather than adding a third-party dependency for something
    this small.

    The delay (400ms) exists so tooltips don't flash into view while the
    cursor just passes over a widget on its way somewhere else - only
    genuinely hovering (pausing) triggers it, matching how tooltips
    behave in most desktop apps.
    """

    DELAY_MS = 400

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._after_id = None
        self._tip_window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel_pending()
        self._after_id = self.widget.after(self.DELAY_MS, self._show)

    def _cancel_pending(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip_window is not None or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6

        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)  # no title bar/borders - just the tip
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=COLOR_ACCENT)
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass

        label = ctk.CTkLabel(
            tw, text=self.text, font=ctk.CTkFont(size=11),
            fg_color=COLOR_PANEL, text_color="white",
            corner_radius=6, justify="left", wraplength=280,
            padx=10, pady=6,
        )
        label.pack()
        self._tip_window = tw

    def _hide(self, _event=None):
        self._cancel_pending()
        if self._tip_window is not None:
            try:
                self._tip_window.destroy()
            except Exception:
                pass
            self._tip_window = None


def _add_tooltip(widget, text: str) -> _ToolTip:
    """Convenience wrapper - see _ToolTip for behavior."""
    return _ToolTip(widget, text)


class LoopClipApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.configure(fg_color=COLOR_BG)

        self.title(f"LoopClip {__version__}")
        self.geometry("700x820")
        self.minsize(640, 700)
        self.resizable(True, True)
        self._set_app_icon()

        self.cfg = settings_module.load_settings()
        self.queue_manager = QueueManager(on_update=self._on_queue_update, temp_root=TEMP_ROOT)

        self._build_ui()

        self._last_seen_clip = None
        self._poll_clipboard()

        missing = missing_tools_message()
        if missing:
            self.after(300, lambda: messagebox.showwarning("Missing requirements", missing))

        self.update_notice_frame = None  # created lazily, only if an update is found
        threading.Thread(target=self._check_yt_dlp_update, daemon=True).start()

    def _set_app_icon(self):
        """Sets the window/taskbar icon from icon.ico if it's present next
        to main.py. Missing or unreadable icon files are non-fatal - the
        app should still run fine with the default icon."""
        icon_path = Path(__file__).resolve().parent / "icon.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass

    def _show_help_dialog(self):
        """
        A single reference dialog explaining what each of the less
        self-explanatory settings actually does, for anyone who wants
        the full picture in one place rather than hovering over each
        field's tooltip individually. Uses a CTkTextbox (scrollable,
        read-only) rather than trying to fit everything in a fixed-size
        dialog - long-form help content should always be expected to
        need scrolling on some screen/font combination, so this doesn't
        try to guess a height that fits everything (see
        _show_about_dialog's history for why that guess is fragile
        across different machines - here the answer is just "always
        scrollable" instead of "measure and hope").
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title("LoopClip Help")
        dialog.geometry("480x560")
        dialog.minsize(360, 300)
        dialog.configure(fg_color=COLOR_BG)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text="What these settings do",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(16, 10))

        textbox = ctk.CTkTextbox(
            dialog, fg_color=COLOR_PANEL, wrap="word",
            font=ctk.CTkFont(size=12),
        )
        textbox.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        # Bold section headers via the underlying tkinter Text widget's
        # tag system - CTkTextbox doesn't expose rich-text formatting
        # directly, but wraps a real Text widget that does.
        heading_font = ctk.CTkFont(size=12, weight="bold")
        textbox._textbox.tag_configure("heading", font=heading_font, foreground=COLOR_ACCENT)

        sections = [
            ("Download Quality", (
                "Best Available picks whichever video+audio streams YouTube "
                "offers at the highest quality, regardless of codec.\n"
                "4K HDR Preferred favors an HDR-capable 4K stream when the "
                "source has one.\n"
                "1080p Compatible caps downloads at 1080p, for smaller files "
                "and broader device compatibility."
            )),
            ("Video Bitrate / Audio Bitrate", (
                "Video Bitrate only applies to the H.265 re-encode fallback - "
                "used when a clean stream-copy trim isn't possible for the "
                "source. Higher means better quality and larger files.\n\n"
                "Audio Bitrate's 'Original' keeps the source audio as-is "
                "without re-encoding it; a specific value re-encodes the "
                "audio track to that target instead."
            )),
            ("Auto-detect seamless loop", (
                "When enabled, LoopClip searches the video for a point where "
                "it can loop cleanly, instead of you picking trim points "
                "manually.\n\n"
                "Method controls how frames get compared: Combined "
                "(recommended) blends structural and color matching for the "
                "most reliable results; SSIM checks structural similarity "
                "only; Histogram checks color distribution only; Hash is "
                "fastest but least precise.\n\n"
                "Similarity % sets how closely the start and end of a "
                "candidate loop need to match (0-100) - higher demands a "
                "closer match; lower is more lenient.\n\n"
                "Analyze controls how many frames get compared - 'Every "
                "frame' is most thorough but slowest; skipping frames "
                "analyzes faster at some cost to precision, useful for very "
                "long videos."
            )),
            ("Limit search window", (
                "Restricts the loop search to a specific From/To portion of "
                "the video instead of scanning the whole thing - useful for "
                "skipping intros/outros, or speeding up analysis on long "
                "videos."
            )),
            ("Seamless loop crossfade", (
                "Blends the end of the clip into the start over the chosen "
                "duration, smoothing out any visible seam at the loop point. "
                "Leave this off for a hard cut instead - it's optional, and "
                "auto-detect can already find clean loop points without it."
            )),
            ("After Download", (
                "Delete original large file removes the full downloaded "
                "source once the clip has been created, keeping only the "
                "final output.\n\n"
                "Open folder when finished opens the output folder in "
                "Explorer automatically once a queued video is done."
            )),
            ("Command line", (
                "Everything here is also available without the GUI via "
                "cli.py, for scripting or automation - run "
                "'python cli.py --help' for the full list of options, or "
                "see the README's Command-Line Usage section."
            )),
        ]

        for heading, body in sections:
            textbox.insert("end", heading + "\n", "heading")
            textbox.insert("end", body + "\n\n")

        textbox.configure(state="disabled")  # read-only after populating

        ctk.CTkButton(
            dialog, text="Close", width=100, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            # Deferred by a tick rather than dialog.destroy directly:
            # confirmed directly (isolated, reproducible minimal repro)
            # that a CTkToplevel containing a CTkTextbox destroyed
            # synchronously inside a button's own click handler can hit
            # a genuine CustomTkinter-internal timing bug - some
            # widget's deferred internal callback (most likely the
            # textbox's scrollbar) is still pending when everything gets
            # torn down, and fires afterward against an already-destroyed
            # canvas ("invalid command name ...canvas"). A CTkButton
            # alone, with no CTkTextbox present, does not trigger this -
            # it's specific to that combination. Giving pending internal
            # callbacks one more event-loop tick to complete before
            # destroying reliably avoids it (verified across 5 repeated
            # runs of the isolated repro with this fix applied).
            command=lambda: dialog.after(50, dialog.destroy),
        ).pack(pady=(0, 16))

    def _show_about_dialog(self):
        """
        Shows version/environment diagnostics: LoopClip version, Python
        runtime, FFmpeg availability, yt-dlp version, NVENC status, and a
        link to the GitHub project. Meant to make troubleshooting
        ("what version are you on? Is FFmpeg actually found? Is NVENC
        working?") a lot easier than asking the user to dig through logs
        or run commands manually.

        The NVENC check involves a real (tiny, ~0.1s) test encode, so
        it's run on a background thread and the dialog fills in that one
        row once it completes, rather than blocking the whole dialog from
        opening.
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title("About LoopClip")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLOR_BG)
        dialog.transient(self)
        dialog.grab_set()
        # Deliberately NOT calling dialog.geometry("WIDTHxHEIGHT") with a
        # hardcoded pixel size here: a fixed height picked against one
        # machine's font rendering/DPI scaling can end up too short on a
        # different one, clipping the bottom of the dialog (confirmed
        # directly - this happened on a real Windows machine with the
        # Close button cut off). Leaving geometry unset lets Tk size the
        # window to whatever its packed content actually needs once
        # rendered, which adapts correctly regardless of font metrics or
        # display scaling.
        dialog_width = 420

        ctk.CTkLabel(
            dialog, text=f"LoopClip {__version__}",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(20, 4))
        ctk.CTkLabel(
            dialog, text="Seamless-loop 4K clips from YouTube videos.",
            font=ctk.CTkFont(size=11), text_color="gray60",
        ).pack(pady=(0, 16))

        info_frame = ctk.CTkFrame(dialog, fg_color=COLOR_PANEL)
        info_frame.pack(fill="x", padx=20)

        ffmpeg_version = get_ffmpeg_version()
        ffmpeg_text = f"Found (v{ffmpeg_version})" if ffmpeg_version else \
            ("Found" if check_ffmpeg() else "Not found")
        try:
            ytdlp_version = downloader.get_installed_version()
        except Exception:
            ytdlp_version = "unknown"

        rows = [
            ("Python", platform.python_version()),
            ("FFmpeg", ffmpeg_text),
            ("yt-dlp", ytdlp_version),
            ("NVENC (GPU encoding)", "Checking..."),
        ]
        self._about_nvenc_label = None
        for label, value in rows:
            row = ctk.CTkFrame(info_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=6)
            # Label above value (not side-by-side) so a long value - the
            # NVENC status in particular can be a full sentence, e.g.
            # "Not available (no compatible NVIDIA GPU/driver detected) -
            # using CPU encoding" - has room to wrap onto multiple lines
            # instead of overlapping the label. Confirmed directly: the
            # previous side-by-side layout visibly collided the label and
            # value text together on a real machine once the value was
            # longer than a couple of words.
            ctk.CTkLabel(
                row, text=label, anchor="w", text_color="gray70",
                font=ctk.CTkFont(size=11),
            ).pack(fill="x")
            value_label = ctk.CTkLabel(
                row, text=value, anchor="w", justify="left",
                wraplength=dialog_width - 64,
            )
            value_label.pack(fill="x")
            if label.startswith("NVENC"):
                self._about_nvenc_label = value_label

        def _update_nvenc_status():
            # Runs on a background thread (the NVENC check does a real,
            # if tiny, subprocess encode) - Tkinter widgets can only be
            # safely touched from the main thread, so the actual UI
            # update is handed to self.after(0, ...) rather than calling
            # .configure() here directly (which intermittently raises
            # "main thread is not in main loop", confirmed by actually
            # running this against a live Tk app rather than assuming
            # the same pattern used elsewhere in this file would apply).
            available, message = check_nvenc()
            color = "#3FB950" if available else "gray70"

            def _apply():
                # Dialog may already be closed by the time this runs -
                # guard against updating a destroyed widget.
                try:
                    self._about_nvenc_label.configure(text=message, text_color=color)
                    # The real NVENC message can be noticeably longer
                    # than the "Checking..." placeholder the dialog was
                    # originally sized against, and wraplength may now
                    # push this row onto two lines instead of one -
                    # re-measure and re-apply the dialog's height so that
                    # doesn't get clipped at the bottom the same way the
                    # original fixed-size dialog did.
                    dialog.update_idletasks()
                    dialog.geometry(f"{dialog_width}x{dialog.winfo_reqheight()}")
                except tk.TclError:
                    pass

            self.after(0, _apply)

        threading.Thread(target=_update_nvenc_status, daemon=True).start()

        auto_update_var = tk.BooleanVar(value=self.cfg.get("auto_check_ytdlp_updates", True))

        def _on_auto_update_toggle():
            self.cfg["auto_check_ytdlp_updates"] = auto_update_var.get()
            settings_module.save_settings(self.cfg)

        ctk.CTkCheckBox(
            dialog, text="Automatically check for yt-dlp updates",
            variable=auto_update_var, command=_on_auto_update_toggle,
            font=ctk.CTkFont(size=11), fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(pady=(16, 0), padx=20, anchor="w")

        link_label = ctk.CTkLabel(
            dialog, text="github.com/quinnuk/LoopClip", text_color=COLOR_ACCENT,
            cursor="hand2", font=ctk.CTkFont(size=12, underline=True),
        )
        link_label.pack(pady=(12, 4))
        link_label.bind(
            "<Button-1>",
            lambda _e: webbrowser.open("https://github.com/quinnuk/LoopClip"),
        )

        ctk.CTkButton(
            dialog, text="Close", width=100, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, command=dialog.destroy,
        ).pack(pady=(12, 20))

        # Force layout now (rather than waiting for the next idle cycle)
        # so the width constraint below applies to the dialog's actual
        # first paint, not a stale/zero size.
        dialog.update_idletasks()
        dialog.geometry(f"{dialog_width}x{dialog.winfo_reqheight()}")
        dialog.minsize(dialog_width, dialog.winfo_reqheight())

    def _check_yt_dlp_update(self):
        """
        Runs on a background thread at startup (never blocks the UI).
        Checks whether a newer yt-dlp is available on PyPI - this matters
        because YouTube changes things fairly often, and an outdated
        yt-dlp is the single most common reason downloads suddenly start
        failing. Never raises - any failure here (no internet, PyPI
        unreachable, etc.) is silently ignored, since this is a
        nice-to-have notice, not something that should ever interrupt
        the app.
        """
        if not self.cfg.get("auto_check_ytdlp_updates", True):
            return
        try:
            installed = downloader.get_installed_version()
            info = ytdlp_updater.check_for_update(installed)
            if info is None:
                return
            if info.latest_version == self.cfg.get("skipped_ytdlp_version", ""):
                return  # user already explicitly declined this exact version
            self.after(0, lambda: self._show_update_notice(info))
        except Exception:
            pass  # Best-effort only - never surface this to the user as an error.

    def _show_update_notice(self, info):
        if self.update_notice_frame is not None:
            return  # already shown

        self.update_notice_frame = ctk.CTkFrame(self, fg_color=COLOR_PANEL)
        self.update_notice_frame.pack(fill="x", padx=20, pady=(0, 8))

        self._update_notice_label = ctk.CTkLabel(
            self.update_notice_frame,
            text=f"A newer yt-dlp is available ({info.current_version} \u2192 {info.latest_version}).",
            font=ctk.CTkFont(size=11), text_color="#E8A33D", anchor="w",
        )
        self._update_notice_label.pack(side="left", padx=(10, 6), pady=6)

        button_row = ctk.CTkFrame(self.update_notice_frame, fg_color="transparent")
        button_row.pack(side="right", padx=(6, 10), pady=4)

        self._update_now_btn = ctk.CTkButton(
            button_row, text="Update Now", width=90, height=24, font=ctk.CTkFont(size=11),
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=lambda: self._start_ytdlp_update(info),
        )
        self._update_now_btn.pack(side="left", padx=(0, 6))

        self._update_skip_btn = ctk.CTkButton(
            button_row, text="Skip", width=56, height=24, font=ctk.CTkFont(size=11),
            fg_color="transparent", hover_color=COLOR_PANEL, text_color="gray60",
            command=lambda: self._skip_ytdlp_update(info),
        )
        self._update_skip_btn.pack(side="left")

    def _skip_ytdlp_update(self, info):
        # Remembered so this exact version doesn't get offered again on
        # every future launch - a newer version later still will be.
        self.cfg["skipped_ytdlp_version"] = info.latest_version
        settings_module.save_settings(self.cfg)
        if self.update_notice_frame is not None:
            self.update_notice_frame.destroy()
            self.update_notice_frame = None

    def _start_ytdlp_update(self, info):
        """
        Downloads and applies the update on a background thread - never
        blocks the UI, and the rest of the app keeps working normally
        while it runs (queueing/processing videos, etc. are untouched;
        this only affects which yt-dlp gets used on the *next* launch,
        never the one already running in this process - see
        ytdlp_updater.py's docstring for why an in-process hot-swap isn't
        attempted).
        """
        self._update_now_btn.configure(state="disabled", text="Updating...")
        self._update_skip_btn.configure(state="disabled")

        def do_update():
            try:
                wheel_path = ytdlp_updater.download_update(info)
                ytdlp_updater.apply_update(wheel_path, info.latest_version)
                self.after(0, lambda: self._on_ytdlp_update_success(info))
            except ytdlp_updater.UpdateError as exc:
                # Capture the message into a plain local NOW, not inside
                # the lambda: Python implicitly deletes the "as exc" name
                # at the end of an except block (to avoid a traceback
                # reference cycle), but self.after(0, ...) only calls
                # this lambda later, on the main thread - by then `exc`
                # is already unbound, and referencing it would raise
                # "cannot access free variable 'exc'" instead of showing
                # the actual error to the user. Confirmed directly: this
                # is exactly what happened before capturing the message
                # as its own variable here.
                message = str(exc)
                self.after(0, lambda: self._on_ytdlp_update_failure(message))
            except Exception as exc:
                message = f"An unexpected error occurred: {exc}"
                self.after(0, lambda: self._on_ytdlp_update_failure(message))

        threading.Thread(target=do_update, daemon=True).start()

    def _on_ytdlp_update_success(self, info):
        if self.update_notice_frame is None:
            return  # dialog/window already gone
        self._update_notice_label.configure(
            text=(
                f"yt-dlp {info.latest_version} downloaded and verified. "
                "Restart LoopClip to start using it."
            ),
            text_color="#3FB950",
        )
        self._update_now_btn.destroy()
        self._update_skip_btn.configure(state="normal", text="Dismiss")
        self._update_skip_btn.configure(
            command=lambda: (self.update_notice_frame.destroy(), setattr(self, "update_notice_frame", None))
        )

    def _on_ytdlp_update_failure(self, message: str):
        if self.update_notice_frame is not None:
            self.update_notice_frame.destroy()
            self.update_notice_frame = None
        messagebox.showerror(
            "yt-dlp update failed",
            f"Could not update yt-dlp:\n\n{message}\n\n"
            "LoopClip will keep working normally with the current version.",
        )

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        pad_x = 20

        # Subtitle, with a small unobtrusive "About" link in the top-right
        # corner - no full menu bar needed for a single dialog like this.
        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.pack(fill="x", padx=pad_x, pady=(10, 0))
        ctk.CTkButton(
            header_row, text="About", width=56, height=22, font=ctk.CTkFont(size=11),
            fg_color="transparent", hover_color=COLOR_PANEL, text_color="gray60",
            command=self._show_about_dialog,
        ).pack(side="right")
        ctk.CTkButton(
            header_row, text="Help", width=56, height=22, font=ctk.CTkFont(size=11),
            fg_color="transparent", hover_color=COLOR_PANEL, text_color="gray60",
            command=self._show_help_dialog,
        ).pack(side="right", padx=(0, 4))

        ctk.CTkLabel(
            self, text="Paste a YouTube link, set your options, then queue it up.",
            font=ctk.CTkFont(size=11), text_color="gray60",
        ).pack(pady=(2, 12))

        # URL
        ctk.CTkLabel(self, text="YouTube Video URL", anchor="w").pack(fill="x", padx=pad_x)
        url_row = ctk.CTkFrame(self, fg_color="transparent")
        url_row.pack(fill="x", padx=pad_x, pady=(2, 0))
        self.url_entry = ctk.CTkEntry(
            url_row, placeholder_text="https://www.youtube.com/watch?v=...", fg_color=COLOR_PANEL
        )
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.insert(0, self.cfg.get("last_url", ""))
        self._add_context_menu(self.url_entry)
        ctk.CTkButton(
            url_row, text="Clear", width=62, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._clear_url,
        ).pack(side="left", padx=(8, 0))

        # Options, two columns side by side to make better use of horizontal space
        options = ctk.CTkFrame(self, fg_color="transparent")
        options.pack(fill="x", padx=pad_x, pady=(12, 0))
        left = ctk.CTkFrame(options, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True)
        right = ctk.CTkFrame(options, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))

        # Output folder (left column)
        ctk.CTkLabel(left, text="Output Folder", anchor="w").pack(fill="x")
        folder_row = ctk.CTkFrame(left, fg_color="transparent")
        folder_row.pack(fill="x", pady=(2, 8))
        self.folder_entry = ctk.CTkEntry(folder_row, fg_color=COLOR_PANEL)
        self.folder_entry.pack(side="left", fill="x", expand=True)
        self.folder_entry.insert(0, self.cfg.get("output_folder"))
        self._add_context_menu(self.folder_entry)
        ctk.CTkButton(
            folder_row, text="Browse", width=68, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, command=self._browse_folder,
        ).pack(side="left", padx=(6, 0))
        self.recent_folders_btn = ctk.CTkButton(
            folder_row, text="Recent", width=68, fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER, command=self._show_recent_folders_menu,
        )
        self.recent_folders_btn.pack(side="left", padx=(6, 0))

        # Download quality (left column) - segmented button, same control
        # style as Video Downloader Pro's quality selector.
        quality_label = ctk.CTkLabel(left, text="Download Quality", anchor="w")
        quality_label.pack(fill="x")
        _add_tooltip(
            quality_label,
            "Best Available picks whichever video+audio streams YouTube offers "
            "at the highest quality, any codec. 4K HDR Preferred favors an "
            "HDR-capable 4K stream when the source has one. 1080p Compatible "
            "caps at 1080p for smaller files and broader compatibility.",
        )
        self.quality_var = ctk.StringVar(value=self.cfg.get("quality", "best"))
        quality_values_by_label = {label: value for value, label in QUALITY_LABELS.items()}
        self.quality_display_var = ctk.StringVar(
            value=QUALITY_LABELS.get(self.quality_var.get(), "Best Available")
        )
        ctk.CTkSegmentedButton(
            left,
            values=list(QUALITY_LABELS.values()),
            variable=self.quality_display_var,
            selected_color=COLOR_ACCENT,
            selected_hover_color=COLOR_ACCENT_HOVER,
            command=lambda label: self.quality_var.set(quality_values_by_label[label]),
        ).pack(fill="x", pady=(2, 6))

        video_bitrate_row = ctk.CTkFrame(left, fg_color="transparent")
        video_bitrate_row.pack(fill="x", pady=(2, 0))
        video_bitrate_label = ctk.CTkLabel(video_bitrate_row, text="Video Bitrate", anchor="w")
        video_bitrate_label.pack(side="left")
        _add_tooltip(
            video_bitrate_label,
            "Target bitrate for the H.265 re-encode fallback, used only when "
            "a clean stream-copy trim isn't possible for the source. Higher "
            "means better quality and larger files.",
        )
        self.video_bitrate_var = ctk.StringVar(
            value=self._video_bitrate_to_label(self.cfg.get("video_bitrate", "15"))
        )
        ctk.CTkOptionMenu(
            video_bitrate_row,
            values=[label for _, label in VIDEO_BITRATE_LABELS],
            variable=self.video_bitrate_var,
            width=110, fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER,
            button_hover_color=COLOR_ACCENT,
            command=lambda _label: self._update_size_estimate(),
        ).pack(side="left", padx=(19, 0))

        self.no_audio_var = ctk.BooleanVar(value=self.cfg.get("no_audio", False))
        ctk.CTkCheckBox(
            left, text="No sound (mute video)", variable=self.no_audio_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._on_audio_option_changed,
        ).pack(anchor="w", pady=(5, 0))

        bitrate_row = ctk.CTkFrame(left, fg_color="transparent")
        bitrate_row.pack(fill="x", pady=(6, 0))
        audio_bitrate_label = ctk.CTkLabel(bitrate_row, text="Audio Bitrate", anchor="w")
        audio_bitrate_label.pack(side="left")
        _add_tooltip(
            audio_bitrate_label,
            "Original keeps the source audio as-is, without re-encoding it. "
            "Choosing a specific bitrate re-encodes the audio track to that "
            "target instead.",
        )
        self.audio_bitrate_var = ctk.StringVar(
            value=self._bitrate_to_label(self.cfg.get("audio_bitrate", "original"))
        )
        self.audio_bitrate_menu = ctk.CTkOptionMenu(
            bitrate_row,
            values=[label for _, label in AUDIO_BITRATE_LABELS],
            variable=self.audio_bitrate_var,
            width=110, fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER,
            button_hover_color=COLOR_ACCENT,
            command=lambda _label: self._update_size_estimate(),
        )
        self.audio_bitrate_menu.pack(side="left", padx=(8, 0))
        self._toggle_audio_fields()

        # Video processing (right column) - automatic seamless-loop
        # detection (LoopyCut-style: method + similarity threshold over an
        # analyzed frame window) instead of manually typed trim points.
        ctk.CTkLabel(right, text="Video Processing", anchor="w").pack(fill="x")
        self.auto_loop_var = ctk.BooleanVar(value=self.cfg.get("auto_loop", True))
        ctk.CTkCheckBox(
            right, text="Auto-detect seamless loop", variable=self.auto_loop_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._on_auto_loop_toggled,
        ).pack(anchor="w", pady=(2, 6))

        method_row = ctk.CTkFrame(right, fg_color="transparent")
        method_row.pack(fill="x")
        method_label = ctk.CTkLabel(method_row, text="Method", anchor="w", width=90)
        method_label.pack(side="left")
        _add_tooltip(
            method_label,
            "How LoopClip compares frames while searching for a loop point.\n\n"
            "Combined (recommended): blends structural + color matching for "
            "the most reliable results.\n"
            "SSIM: structural similarity only.\n"
            "Histogram: color distribution only.\n"
            "Hash: fastest, least precise.",
        )
        method_values_by_label = {label: value for value, label in LOOP_METHOD_LABELS}
        self.loop_method_var = ctk.StringVar(
            value=self._loop_method_to_label(self.cfg.get("loop_method", "combined"))
        )
        self.loop_method_menu = ctk.CTkOptionMenu(
            method_row, values=[label for _, label in LOOP_METHOD_LABELS],
            variable=self.loop_method_var, width=170,
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER,
            button_hover_color=COLOR_ACCENT,
            command=lambda label: self.loop_method_var.set(label),
        )
        self.loop_method_menu.pack(side="left", padx=(6, 0))

        similarity_row = ctk.CTkFrame(right, fg_color="transparent")
        similarity_row.pack(fill="x", pady=(6, 0))
        similarity_label = ctk.CTkLabel(similarity_row, text="Similarity %", anchor="w", width=90)
        similarity_label.pack(side="left")
        _add_tooltip(
            similarity_label,
            "How closely the start and end of a candidate loop need to match, "
            "0-100. Higher demands a closer match (fewer, more seamless "
            "candidates); lower is more lenient (more candidates found, "
            "possibly less seamless).",
        )
        self.similarity_entry = ctk.CTkEntry(similarity_row, width=60, fg_color=COLOR_PANEL)
        self.similarity_entry.insert(0, str(self.cfg.get("similarity", 98)))
        self.similarity_entry.bind("<FocusOut>", lambda e: self._clamp_similarity())
        self.similarity_entry.pack(side="left", padx=(6, 0))

        downsample_row = ctk.CTkFrame(right, fg_color="transparent")
        downsample_row.pack(fill="x", pady=(6, 0))
        analyze_label = ctk.CTkLabel(downsample_row, text="Analyze", anchor="w", width=90)
        analyze_label.pack(side="left")
        _add_tooltip(
            analyze_label,
            "How many frames get compared during the search. 'Every frame' "
            "is most thorough but slowest. Skipping frames analyzes faster "
            "at some cost to precision - useful for very long videos.",
        )
        downsample_values_by_label = {label: value for value, label in DOWNSAMPLE_LABELS}
        self.downsample_var = ctk.StringVar(
            value=self._downsample_to_label(str(self.cfg.get("downsample", 1)))
        )
        self.downsample_menu = ctk.CTkOptionMenu(
            downsample_row, values=[label for _, label in DOWNSAMPLE_LABELS],
            variable=self.downsample_var, width=170,
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER,
            button_hover_color=COLOR_ACCENT,
            command=lambda label: self.downsample_var.set(label),
        )
        self.downsample_menu.pack(side="left", padx=(6, 0))

        self.limit_search_var = ctk.BooleanVar(value=bool(self.cfg.get("search_stop", "")))
        limit_search_cb = ctk.CTkCheckBox(
            right, text="Limit search window", variable=self.limit_search_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._on_limit_search_toggled,
        )
        limit_search_cb.pack(anchor="w", pady=(8, 4))
        _add_tooltip(
            limit_search_cb,
            "Restrict the loop search to a specific portion of the video "
            "(From/To) instead of scanning the whole thing - useful for "
            "skipping intros/outros, or speeding up analysis on long videos.",
        )

        search_row = ctk.CTkFrame(right, fg_color="transparent")
        search_row.pack(fill="x")
        search_start_col = ctk.CTkFrame(search_row, fg_color="transparent")
        search_start_col.pack(side="left")
        ctk.CTkLabel(search_start_col, text="From (H:M:S)", anchor="w").pack(fill="x")
        self.search_start_entry = _TimeEntry(
            search_start_col, initial=self.cfg.get("search_start", "00:00:00")
        )
        self.search_start_entry.pack(anchor="w")

        search_stop_col = ctk.CTkFrame(search_row, fg_color="transparent")
        search_stop_col.pack(side="left", padx=(16, 0))
        ctk.CTkLabel(search_stop_col, text="To (H:M:S)", anchor="w").pack(fill="x")
        self.search_stop_entry = _TimeEntry(
            search_stop_col, initial=self.cfg.get("search_stop") or "00:01:00"
        )
        self.search_stop_entry.pack(anchor="w")
        self._toggle_search_fields()
        self._toggle_loop_fields()

        loop_row = ctk.CTkFrame(right, fg_color="transparent")
        loop_row.pack(fill="x", pady=(10, 0))
        self.seamless_loop_var = ctk.BooleanVar(value=self.cfg.get("seamless_loop", False))
        seamless_loop_cb = ctk.CTkCheckBox(
            loop_row, text="Seamless loop crossfade", variable=self.seamless_loop_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._on_seamless_loop_toggled,
        )
        seamless_loop_cb.pack(side="left")
        _add_tooltip(
            seamless_loop_cb,
            "Blends the end of the clip into the start over the chosen "
            "duration, smoothing out any visible seam at the loop point. "
            "Leave unchecked for a hard cut instead.",
        )
        self.crossfade_var = ctk.StringVar(
            value=self._crossfade_to_label(self.cfg.get("crossfade_seconds", "2"))
        )
        self.crossfade_menu = ctk.CTkOptionMenu(
            loop_row, values=[label for _, label in CROSSFADE_LABELS],
            variable=self.crossfade_var, width=80,
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER,
            button_hover_color=COLOR_ACCENT,
            command=lambda _label: self._update_size_estimate(),
        )
        self.crossfade_menu.pack(side="left", padx=(8, 0))
        self._toggle_crossfade_field()

        self.size_estimate_label = ctk.CTkLabel(
            right, text="", anchor="w", font=ctk.CTkFont(size=11), text_color="gray60",
        )
        self.size_estimate_label.pack(fill="x", pady=(6, 0))
        self._update_size_estimate()

        # After download (right column)
        ctk.CTkLabel(right, text="After Download", anchor="w").pack(fill="x", pady=(12, 0))
        self.delete_original_var = ctk.BooleanVar(value=self.cfg.get("delete_original", True))
        ctk.CTkCheckBox(
            right, text="Delete original large file", variable=self.delete_original_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)
        self.open_folder_var = ctk.BooleanVar(value=self.cfg.get("open_folder_when_finished", True))
        ctk.CTkCheckBox(
            right, text="Open folder when finished", variable=self.open_folder_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)

        # Actions row
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=pad_x, pady=(14, 8))
        ctk.CTkButton(
            actions, text="Add to Queue", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._on_add_to_queue,
        ).pack(side="left", fill="x", expand=True)
        self.start_queue_btn = ctk.CTkButton(
            actions, text="Start Queue", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._on_start_queue_clicked,
        )
        self.start_queue_btn.pack(side="left", fill="x", expand=True, padx=8)
        self.cancel_btn = ctk.CTkButton(
            actions, text="Cancel", fg_color=COLOR_CANCEL, hover_color=COLOR_CANCEL_HOVER,
            command=self._on_cancel_clicked, state="disabled",
        )
        self.cancel_btn.pack(side="left", fill="x", expand=True)

        # Queue list
        queue_header = ctk.CTkFrame(self, fg_color="transparent")
        queue_header.pack(fill="x", padx=pad_x)
        ctk.CTkLabel(queue_header, text="Queue", anchor="w").pack(side="left")
        self.clear_queue_btn = ctk.CTkButton(
            queue_header, text="Clear Queue", width=100, fg_color=COLOR_CANCEL,
            hover_color=COLOR_CANCEL_HOVER, command=self._on_clear_queue_clicked,
        )
        self.clear_queue_btn.pack(side="right")
        list_frame = ctk.CTkFrame(self, fg_color=COLOR_PANEL)
        list_frame.pack(fill="both", expand=True, padx=pad_x, pady=(3, 6))
        self.queue_frame = ctk.CTkScrollableFrame(list_frame, fg_color="transparent")
        self.queue_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.empty_queue_label = ctk.CTkLabel(
            self.queue_frame, text="No videos queued yet.", text_color="#9AA0A6"
        )
        self.empty_queue_label.pack(anchor="w", pady=8, padx=4)

        # Status / progress
        self.progress_bar = ctk.CTkProgressBar(self, progress_color=COLOR_ACCENT)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=pad_x, pady=(2, 3))
        self.status_label = ctk.CTkLabel(
            self, text="Ready. Add a video to the queue to begin.", anchor="w"
        )
        self.status_label.pack(fill="x", padx=pad_x, pady=(0, 12))

    # --------------------------------------------------------- UI helpers

    def _bitrate_to_label(self, value: str) -> str:
        for key, label in AUDIO_BITRATE_LABELS:
            if key == value:
                return label
        return "Original"

    def _label_to_bitrate(self, label: str) -> str:
        for key, lbl in AUDIO_BITRATE_LABELS:
            if lbl == label:
                return key
        return "original"

    def _video_bitrate_to_label(self, value: str) -> str:
        for key, label in VIDEO_BITRATE_LABELS:
            if key == value:
                return label
        return "15 Mbps"

    def _label_to_video_bitrate(self, label: str) -> str:
        for key, lbl in VIDEO_BITRATE_LABELS:
            if lbl == label:
                return key
        return "15"

    def _loop_method_to_label(self, value: str) -> str:
        for key, label in LOOP_METHOD_LABELS:
            if key == value:
                return label
        return "Combined (recommended)"

    def _label_to_loop_method(self, label: str) -> str:
        for key, lbl in LOOP_METHOD_LABELS:
            if lbl == label:
                return key
        return "combined"

    def _downsample_to_label(self, value: str) -> str:
        for key, label in DOWNSAMPLE_LABELS:
            if key == value:
                return label
        return "Every frame"

    def _label_to_downsample(self, label: str) -> str:
        for key, lbl in DOWNSAMPLE_LABELS:
            if lbl == label:
                return key
        return "1"

    def _clamp_similarity(self):
        value = self.similarity_entry.get().strip()
        try:
            num = max(0, min(100, float(value)))
        except ValueError:
            num = 98
        self.similarity_entry.delete(0, "end")
        self.similarity_entry.insert(0, str(num))
        self._update_size_estimate()

    def _crossfade_to_label(self, value: str) -> str:
        for key, label in CROSSFADE_LABELS:
            if key == value:
                return label
        return "2 sec"

    def _label_to_crossfade(self, label: str) -> str:
        for key, lbl in CROSSFADE_LABELS:
            if lbl == label:
                return key
        return "2"

    def _toggle_crossfade_field(self):
        state = "normal" if self.seamless_loop_var.get() else "disabled"
        self.crossfade_menu.configure(state=state)

    def _toggle_audio_fields(self):
        state = "disabled" if self.no_audio_var.get() else "normal"
        self.audio_bitrate_menu.configure(state=state)

    def _toggle_loop_fields(self):
        state = "normal" if self.auto_loop_var.get() else "disabled"
        self.loop_method_menu.configure(state=state)
        self.similarity_entry.configure(state=state)
        self.downsample_menu.configure(state=state)
        self._toggle_search_fields()

    def _toggle_search_fields(self):
        # Independent of auto-loop: setting a search window and leaving
        # auto-loop off now trims the clip to exactly that window (a
        # fast, manual trim), rather than requiring loop detection to be
        # on for the window to have any effect.
        state = "normal" if self.limit_search_var.get() else "disabled"
        self.search_start_entry.configure_state(state)
        self.search_stop_entry.configure_state(state)

    def _on_audio_option_changed(self):
        self._toggle_audio_fields()
        self._update_size_estimate()

    def _on_auto_loop_toggled(self):
        self._toggle_loop_fields()
        self._update_size_estimate()

    def _on_limit_search_toggled(self):
        self._toggle_search_fields()
        self._update_size_estimate()

    def _on_seamless_loop_toggled(self):
        self._toggle_crossfade_field()
        self._update_size_estimate()

    def _update_size_estimate(self):
        """
        Duration is no longer known ahead of time when auto loop-detection
        is on (it depends on where the analysis finds the loop), so this
        just shows an informational note rather than a computed estimate.
        """
        if not hasattr(self, "size_estimate_label"):
            return  # called before the label exists yet (initial setup order)

        if self.auto_loop_var.get():
            video_mbps = self._label_to_video_bitrate(self.video_bitrate_var.get())
            self.size_estimate_label.configure(
                text=(
                    f"Loop length is determined automatically during analysis "
                    f"(~{video_mbps} Mbps once found)."
                )
            )
        elif self.limit_search_var.get():
            self.size_estimate_label.configure(
                text="Trimmed to the window set below - only that part is downloaded."
            )
        else:
            self.size_estimate_label.configure(text="Full video length - final size varies.")

    def _browse_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder_entry.get() or str(Path.home()))
        if folder:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, folder)

    def _show_recent_folders_menu(self):
        recent = self.cfg.get("recent_output_folders", [])
        menu = tk.Menu(self, tearoff=0)
        if not recent:
            menu.add_command(label="(no recent folders yet)", state="disabled")
        else:
            for folder in recent:
                menu.add_command(
                    label=folder, command=lambda f=folder: self._use_recent_folder(f)
                )
        # Position the menu just under the button that opened it.
        x = self.recent_folders_btn.winfo_rootx()
        y = self.recent_folders_btn.winfo_rooty() + self.recent_folders_btn.winfo_height()
        menu.tk_popup(x, y)

    def _use_recent_folder(self, folder: str):
        self.folder_entry.delete(0, "end")
        self.folder_entry.insert(0, folder)

    def _remember_output_folder(self, folder: str):
        """Adds `folder` to the front of the recent-folders list (deduped,
        capped at 5), called whenever a folder is actually used for a
        successfully-queued download."""
        recent = [f for f in self.cfg.get("recent_output_folders", []) if f != folder]
        recent.insert(0, folder)
        self.cfg["recent_output_folders"] = recent[:5]

    def _clear_url(self):
        self.url_entry.delete(0, "end")
        # Treat the cleared value as "already seen" so the clipboard poller
        # doesn't immediately re-fill it from whatever's still on the
        # clipboard.
        self._last_seen_clip = self._read_clipboard()
        self.url_entry.focus_set()

    def _poll_clipboard(self):
        """
        Runs frequently for the life of the app. Whenever a *new* clipboard
        value appears (i.e. the user just copied something) and it's a
        valid YouTube URL, auto-fill the URL field with it - unless the
        queue is currently running, the clipboard hasn't actually changed,
        or the URL field already shows that value.
        """
        clip = self._read_clipboard()

        if clip is not None and clip != self._last_seen_clip:
            self._last_seen_clip = clip
            clip = clip.strip()
            already_shown = clip == self.url_entry.get().strip()
            if (
                clip
                and downloader.is_valid_youtube_url(clip)
                and not self.queue_manager.is_running()
                and not already_shown
            ):
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, clip)
                self._set_status("URL detected from clipboard.")

        self.after(CLIPBOARD_POLL_MS, self._poll_clipboard)

    def _read_clipboard(self):
        """
        Reads the clipboard, preferring pyperclip (works even when the app
        isn't focused) but falling back to Tk's own clipboard_get() if
        pyperclip is unavailable or hits a transient read error - this is a
        known intermittent issue on Windows ("OpenClipboard failed") when
        another app briefly holds the clipboard lock.
        """
        if HAS_CLIPBOARD:
            try:
                return pyperclip.paste()
            except Exception:
                pass
        try:
            return self.clipboard_get()
        except tk.TclError:
            return None

    def _add_context_menu(self, entry: ctk.CTkEntry):
        """Adds a right-click (and Shift+F10) Cut/Copy/Paste/Select-All menu
        to a CTkEntry.

        CTkEntry wraps a plain tkinter.Entry internally and (like base Tk)
        doesn't provide a right-click menu out of the box - Ctrl+V works,
        right-click doesn't, unless we add this ourselves.
        """
        # The virtual <<Cut>>/<<Copy>>/<<Paste>> events only exist on the
        # real internal tk.Entry widget (its class bindtag is "Entry") - a
        # CTkEntry is a Frame-based wrapper with no such bindtag, so firing
        # them on `entry` itself is a silent no-op. Everything below must
        # target the real widget, not the CTkEntry wrapper.
        target = getattr(entry, "_entry", entry)

        menu = tk.Menu(entry, tearoff=0)
        menu.add_command(label="Cut", command=lambda: target.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: target.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: target.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: target.select_range(0, "end"))

        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)

        # Right-click (Button-3) and the standard Windows "context menu"
        # keyboard shortcut, Shift+F10, both open the same menu.
        target.bind("<Button-3>", show_menu)
        target.bind("<Shift-F10>", show_menu)

    # -------------------------------------------------------------- Logic

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

    def _set_progress(self, fraction: float):
        self.progress_bar.set(max(0.0, min(1.0, fraction)))

    def _validate_loop_settings(self) -> bool:
        if self.auto_loop_var.get():
            try:
                similarity = float(self.similarity_entry.get().strip())
                if not (0 <= similarity <= 100):
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Invalid Similarity",
                    "Similarity must be a number between 0 and 100.",
                )
                return False

        if not self.limit_search_var.get():
            return True

        start = self.search_start_entry.get().strip()
        stop = self.search_stop_entry.get().strip()
        if not processor.validate_hms(start):
            messagebox.showerror(
                "Invalid Search Start",
                f"'{start}' is not a valid time.\nPlease use the format HH:MM:SS.",
            )
            return False
        if not processor.validate_hms(stop):
            messagebox.showerror(
                "Invalid Search End",
                f"'{stop}' is not a valid time.\nPlease use the format HH:MM:SS.",
            )
            return False
        if processor.hms_to_seconds(stop) <= processor.hms_to_seconds(start):
            messagebox.showerror(
                "Invalid Search Window",
                "The search window's end time must be after its start time.",
            )
            return False
        return True

    def _on_add_to_queue(self):
        raw = self.url_entry.get().strip()
        if not raw:
            messagebox.showerror("Invalid URL", "Please enter a valid YouTube URL.")
            return

        # Supports pasting several URLs at once (space or newline separated -
        # a single-line entry collapses pasted newlines to spaces anyway, so
        # splitting on whitespace handles both cases identically).
        candidates = raw.split()
        valid_urls = [u for u in candidates if downloader.is_valid_youtube_url(u)]
        invalid_count = len(candidates) - len(valid_urls)

        if not valid_urls:
            messagebox.showerror("Invalid URL", "Please enter a valid YouTube URL.")
            return

        missing = missing_tools_message()
        if missing:
            messagebox.showerror("Missing requirements", missing)
            return

        output_folder = self.folder_entry.get().strip()
        try:
            Path(output_folder).mkdir(parents=True, exist_ok=True)
        except OSError:
            messagebox.showerror("Folder Error", "The output folder path is not valid.")
            return

        if not self._validate_loop_settings():
            return

        duplicates = [u for u in valid_urls if self.queue_manager.has_url(u)]
        if duplicates:
            word = "URL is" if len(duplicates) == 1 else "URLs are"
            if not messagebox.askyesno(
                "Already in Queue",
                f"{len(duplicates)} {word} already in the queue. Add again anyway?",
            ):
                valid_urls = [u for u in valid_urls if u not in duplicates]

        added = 0
        for url in valid_urls:
            item = QueueItem(
                url=url,
                quality=self.quality_var.get(),
                no_audio=self.no_audio_var.get(),
                audio_bitrate=self._label_to_bitrate(self.audio_bitrate_var.get()),
                video_bitrate=self._label_to_video_bitrate(self.video_bitrate_var.get()),
                auto_loop=self.auto_loop_var.get(),
                loop_method=self._label_to_loop_method(self.loop_method_var.get()),
                similarity=_safe_float(self.similarity_entry.get().strip(), 98.0),
                search_start=(
                    self.search_start_entry.get().strip() if self.limit_search_var.get() else "00:00:00"
                ),
                search_stop=(
                    self.search_stop_entry.get().strip() if self.limit_search_var.get() else ""
                ),
                downsample=int(self._label_to_downsample(self.downsample_var.get())),
                min_loop_seconds=None,
                max_loop_seconds=None,
                output_folder=output_folder,
                delete_original=self.delete_original_var.get(),
                seamless_loop=self.seamless_loop_var.get(),
                crossfade_seconds=self._label_to_crossfade(self.crossfade_var.get()),
            )
            self.queue_manager.add_item(item)
            added += 1

        self._remember_output_folder(output_folder)
        self._save_current_settings(output_folder)

        self.url_entry.delete(0, "end")
        status = f"Added {added} item(s) to queue ({len(self.queue_manager.items)} total)."
        if invalid_count:
            status += f" Skipped {invalid_count} invalid entr{'y' if invalid_count == 1 else 'ies'}."
        self._set_status(status)

    def _on_start_queue_clicked(self):
        if not any(i.state == QueueState.QUEUED for i in self.queue_manager.items):
            messagebox.showinfo("Queue Empty", "Add at least one video to the queue first.")
            return

        missing = missing_tools_message()
        if missing:
            messagebox.showerror("Missing requirements", missing)
            return

        self.queue_manager.start()
        self.start_queue_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self._set_status("Queue started...")

    def _on_cancel_clicked(self):
        self.queue_manager.cancel()
        self.cancel_btn.configure(state="disabled")
        self._set_status("Cancelling...")

    def _on_clear_queue_clicked(self):
        if not self.queue_manager.items:
            return
        if self.queue_manager.is_running():
            messagebox.showinfo(
                "Queue Running",
                "Cancel the running download first, then clear the queue.",
            )
            return
        if not messagebox.askyesno(
            "Clear Queue", f"Remove all {len(self.queue_manager.items)} item(s) from the queue?"
        ):
            return
        self.queue_manager.clear_all()
        self._set_status("Queue cleared.")

    def _on_remove_queue_item(self, item_id: str):
        if self.queue_manager.remove_item(item_id):
            self._set_status("Removed item from queue.")

    def _on_retry_queue_item(self, item_id: str):
        if self.queue_manager.retry_item(item_id):
            self._set_status("Item re-queued.")

    def _save_current_settings(self, output_folder: str):
        self.cfg.update({
            "output_folder": output_folder,
            "quality": self.quality_var.get(),
            "no_audio": self.no_audio_var.get(),
            "audio_bitrate": self._label_to_bitrate(self.audio_bitrate_var.get()),
            "video_bitrate": self._label_to_video_bitrate(self.video_bitrate_var.get()),
            "auto_loop": self.auto_loop_var.get(),
            "loop_method": self._label_to_loop_method(self.loop_method_var.get()),
            "similarity": _safe_float(self.similarity_entry.get().strip(), 98.0),
            "search_start": (
                self.search_start_entry.get().strip() if self.limit_search_var.get() else "00:00:00"
            ),
            "search_stop": (
                self.search_stop_entry.get().strip() if self.limit_search_var.get() else ""
            ),
            "downsample": int(self._label_to_downsample(self.downsample_var.get())),
            "delete_original": self.delete_original_var.get(),
            "open_folder_when_finished": self.open_folder_var.get(),
            "seamless_loop": self.seamless_loop_var.get(),
            "crossfade_seconds": self._label_to_crossfade(self.crossfade_var.get()),
            "last_url": "",
        })
        settings_module.save_settings(self.cfg)

    # --------------------------------------------------------- Queue view

    def _on_queue_update(self):
        """
        Called from the worker thread, possibly many times per second (the
        download progress hook fires very frequently). Hops back onto the
        UI thread, but throttled to a fixed max refresh rate - rebuilding
        the whole queue display on every single progress tick is what was
        causing the flashing and the CPU/resource spike with multiple
        queued items.
        """
        self.after(0, self._maybe_refresh_queue_display)

    def _maybe_refresh_queue_display(self):
        now = time.monotonic()
        elapsed = now - getattr(self, "_last_queue_refresh", 0.0)
        min_interval = 0.15  # ~6-7 redraws/sec max - smooth, not wasteful

        if elapsed >= min_interval:
            self._last_queue_refresh = now
            self._queue_refresh_scheduled = False
            self._refresh_queue_display()
        elif not getattr(self, "_queue_refresh_scheduled", False):
            self._queue_refresh_scheduled = True
            self.after(int((min_interval - elapsed) * 1000), self._maybe_refresh_queue_display)

    def _destroy_row_frame(self, frame):
        """
        Deferred by one event-loop tick rather than frame.destroy()
        directly - matches the fix already established for the Help
        dialog's Close button (see that commit for the full story): a
        CTkScrollableFrame's row containing widgets like CTkProgressBar/
        CTkLabel, destroyed synchronously during a frequent refresh
        cycle, can hit the same CustomTkinter-internal timing bug where
        some widget's deferred internal redraw callback is still pending
        when the frame gets torn down, and fires afterward against an
        already-destroyed canvas ("invalid command name ...canvas").

        Confirmed as a genuine (if intermittent - it didn't reproduce in
        isolated test runs, only under full-test-suite timing/load)
        crash, not just a theoretical risk: this method exists because a
        real crash was caught here, in exactly the queue list refresh
        path that runs several times a second during active downloads.
        """
        frame.after(50, frame.destroy)

    def _refresh_queue_display(self):
        items = self.queue_manager.items
        if not hasattr(self, "_queue_rows"):
            self._queue_rows: dict[str, dict] = {}

        if not items:
            for row in self._queue_rows.values():
                self._destroy_row_frame(row["frame"])
            self._queue_rows.clear()
            if not self.empty_queue_label.winfo_exists():
                self.empty_queue_label = ctk.CTkLabel(
                    self.queue_frame, text="No videos queued yet.", text_color="#9AA0A6"
                )
            self.empty_queue_label.pack(anchor="w", pady=8, padx=4)
        else:
            if self.empty_queue_label.winfo_exists():
                self.empty_queue_label.pack_forget()

            current_ids = {item.id for item in items}
            # Drop rows for items no longer in the queue (e.g. after "Clear Completed").
            for stale_id in list(self._queue_rows.keys()):
                if stale_id not in current_ids:
                    self._destroy_row_frame(self._queue_rows.pop(stale_id)["frame"])

            for idx, item in enumerate(items, start=1):
                if item.id not in self._queue_rows:
                    self._queue_rows[item.id] = self._create_queue_row(idx, item)
                self._update_queue_row(self._queue_rows[item.id], idx, item)

        self._refresh_status_and_progress(items)
        self._refresh_buttons()

    def _create_queue_row(self, idx: int, item: QueueItem) -> dict:
        """Builds the (initially empty/placeholder) widgets for one queue
        row once. Values are filled in by _update_queue_row, which is also
        what's called on every subsequent refresh - the row's own widgets
        are never destroyed and recreated while the item is still queued,
        only their text/values are updated in place."""
        row = ctk.CTkFrame(self.queue_frame, fg_color="#2B2B2F", corner_radius=6)
        row.pack(fill="x", pady=3, padx=2)

        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(6, 2))
        name_label = ctk.CTkLabel(top, text="", anchor="w")
        name_label.pack(side="left", fill="x", expand=True)
        action_btn = ctk.CTkButton(
            top, text="", width=64, height=22, font=ctk.CTkFont(size=11), command=lambda: None,
        )
        state_label = ctk.CTkLabel(top, text="", font=ctk.CTkFont(weight="bold"))
        state_label.pack(side="right", padx=(8, 0))

        detail = ctk.CTkFrame(row, fg_color="transparent")
        bar = ctk.CTkProgressBar(detail, height=8, progress_color=COLOR_ACCENT)
        bar.pack(fill="x", pady=(0, 4))
        info_label = ctk.CTkLabel(detail, text="", anchor="w", font=ctk.CTkFont(size=11))
        info_label.pack(anchor="w")

        message_label = ctk.CTkLabel(
            row, text="", anchor="w", font=ctk.CTkFont(size=11), wraplength=520,
        )

        return {
            "frame": row, "name_label": name_label, "state_label": state_label,
            "action_btn": action_btn, "action_kind": None,
            "detail": detail, "bar": bar, "info_label": info_label,
            "message_label": message_label, "shown_extra": None,
        }

    def _update_queue_row(self, widgets: dict, idx: int, item: QueueItem):
        display_name = item.title or item.url
        if len(display_name) > 52:
            display_name = display_name[:49] + "..."
        widgets["name_label"].configure(text=f"{idx}. {display_name}")
        widgets["state_label"].configure(
            text=item.state, text_color=STATE_COLORS.get(item.state, "#FFFFFF")
        )

        if item.state == QueueState.QUEUED:
            desired_action = "remove"
        elif item.state in (QueueState.ERROR, QueueState.CANCELLED):
            desired_action = "retry"
        else:
            desired_action = None

        if widgets["action_kind"] != desired_action:
            widgets["action_kind"] = desired_action
            if desired_action == "remove":
                widgets["action_btn"].configure(
                    text="Remove", fg_color=COLOR_CANCEL, hover_color=COLOR_CANCEL_HOVER,
                    command=lambda item_id=item.id: self._on_remove_queue_item(item_id),
                )
                widgets["action_btn"].pack(side="right")
            elif desired_action == "retry":
                widgets["action_btn"].configure(
                    text="Retry", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                    command=lambda item_id=item.id: self._on_retry_queue_item(item_id),
                )
                widgets["action_btn"].pack(side="right")
            else:
                widgets["action_btn"].pack_forget()

        # Only pack/unpack the extra area (progress bar or message) when the
        # kind of content actually needs to change - not on every refresh.
        if item.state in (
            QueueState.DOWNLOADING, QueueState.ANALYZING, QueueState.TRIMMING, QueueState.LOOPING,
        ):
            if widgets["shown_extra"] != "detail":
                widgets["message_label"].pack_forget()
                widgets["detail"].pack(fill="x", padx=10, pady=(0, 8))
                widgets["shown_extra"] = "detail"
            widgets["bar"].set(max(0.0, min(1.0, item.percent / 100.0)))
            info_text = item.operation
            if item.state == QueueState.DOWNLOADING:
                info_text += f"  {item.percent:.0f}%   {item.speed}   ETA {item.eta}"
            widgets["info_label"].configure(text=info_text)
        elif item.state == QueueState.ERROR and item.error_message:
            if widgets["shown_extra"] != "message":
                widgets["detail"].pack_forget()
                widgets["message_label"].pack(fill="x", padx=10, pady=(0, 8))
                widgets["shown_extra"] = "message"
            widgets["message_label"].configure(text=item.error_message, text_color="#F85149")
        elif item.state == QueueState.FINISHED and item.output_name:
            if widgets["shown_extra"] != "message":
                widgets["detail"].pack_forget()
                widgets["message_label"].pack(fill="x", padx=10, pady=(0, 8))
                widgets["shown_extra"] = "message"
            if item.warning_message:
                # Finished, but something degraded along the way (e.g. the
                # seamless-loop crossfade couldn't be built, so a plain
                # trimmed clip was saved instead) - show that instead of
                # just the filename so it isn't mistaken for a clean run.
                widgets["message_label"].configure(
                    text=f"{item.output_name}  -  {item.warning_message}",
                    text_color="#E3B341",
                )
            else:
                widgets["message_label"].configure(text=item.output_name, text_color="#9AA0A6")
        else:
            if widgets["shown_extra"] is not None:
                widgets["detail"].pack_forget()
                widgets["message_label"].pack_forget()
                widgets["shown_extra"] = None

    def _refresh_status_and_progress(self, items):
        active = next(
            (i for i in items
             if i.state in (QueueState.DOWNLOADING, QueueState.ANALYZING, QueueState.TRIMMING)),
            None,
        )
        if active is not None:
            idx = items.index(active) + 1
            position = f"Item {idx} of {len(items)}"
            if active.state == QueueState.DOWNLOADING:
                text = (
                    f"Downloading...   {active.percent:.0f}%   {active.speed}   "
                    f"ETA {active.eta}   -   {position}"
                )
            else:
                text = f"{active.operation}   -   {position}"
            self._set_status(text)
            self._set_progress(active.percent / 100.0)
            return

        if self.queue_manager.is_running():
            self._set_status("Preparing next item...")
            return

        if not items:
            self._set_status("Ready. Add a video to the queue to begin.")
            self._set_progress(0)
            return

        if any(i.state == QueueState.CANCELLED for i in items):
            self._set_status("Queue cancelled.")
        elif any(i.state == QueueState.ERROR for i in items):
            self._set_status("Queue finished with errors - see the list above for details.")
        else:
            self._set_status("Queue finished.")
        self._set_progress(1.0 if all(i.state == QueueState.FINISHED for i in items) else 0.0)

        if items and items[-1].state == QueueState.FINISHED and self.open_folder_var.get():
            self._maybe_open_folder(items[-1].output_folder)

    def _maybe_open_folder(self, folder: str):
        # Only open once per finished run - guard with a simple flag.
        if getattr(self, "_last_opened_folder", None) == folder:
            return
        self._last_opened_folder = folder
        try:
            os.startfile(folder)  # Windows only
        except (AttributeError, OSError):
            pass

    def _refresh_buttons(self):
        running = self.queue_manager.is_running()
        self.start_queue_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self.clear_queue_btn.configure(state="disabled" if running else "normal")


if __name__ == "__main__":
    app = LoopClipApp()
    app.mainloop()
