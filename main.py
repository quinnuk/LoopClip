"""
main.py
-------
Aquarium Downloader - main application window.

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
import tempfile
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

import processor
import settings as settings_module
from queue_manager import QueueItem, QueueManager, QueueState
from tool_check import missing_tools_message

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

TEMP_ROOT = Path(tempfile.gettempdir()) / "AquariumDownloader" / "Temp"

CLIPBOARD_POLL_MS = 150  # near-instant clipboard pickup

STATE_COLORS = {
    QueueState.QUEUED: "#9AA0A6",
    QueueState.DOWNLOADING: "#3B8EEA",
    QueueState.TRIMMING: "#E8A33D",
    QueueState.FINISHED: "#3FB950",
    QueueState.ERROR: "#F85149",
    QueueState.CANCELLED: "#8B8F98",
}

AUDIO_BITRATE_LABELS = [
    ("original", "Original"),
    ("128", "128 kbps"),
    ("192", "192 kbps"),
    ("256", "256 kbps"),
    ("320", "320 kbps"),
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

    def __init__(self, master, initial: str = "00:00:00", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        vcmd = (self.register(self._validate_digits), "%P")

        box_kwargs = dict(
            width=40, justify="center", fg_color=COLOR_PANEL,
            validate="key", validatecommand=vcmd,
        )
        self.hh = ctk.CTkEntry(self, **box_kwargs)
        self.hh.pack(side="left")
        ctk.CTkLabel(self, text=":", width=6).pack(side="left")
        self.mm = ctk.CTkEntry(self, **box_kwargs)
        self.mm.pack(side="left")
        ctk.CTkLabel(self, text=":", width=6).pack(side="left")
        self.ss = ctk.CTkEntry(self, **box_kwargs)
        self.ss.pack(side="left")

        self.set(initial)

        self.hh.bind("<KeyRelease>", lambda e: self._maybe_advance(self.hh, self.mm))
        self.mm.bind("<KeyRelease>", lambda e: self._maybe_advance(self.mm, self.ss))
        self.hh.bind("<FocusOut>", lambda e: self._pad(self.hh))
        self.mm.bind("<FocusOut>", lambda e: self._clamp(self.mm))
        self.ss.bind("<FocusOut>", lambda e: self._clamp(self.ss))

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


class AquariumDownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.configure(fg_color=COLOR_BG)

        self.title("Aquarium Downloader")
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

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        pad_x = 20

        # Subtitle only - no big title label, per user preference.
        ctk.CTkLabel(
            self, text="Paste a YouTube link, set your options, then queue it up.",
            font=ctk.CTkFont(size=11), text_color="gray60",
        ).pack(pady=(16, 12))

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

        # Download quality (left column) - segmented button, same control
        # style as Video Downloader Pro's quality selector.
        ctk.CTkLabel(left, text="Download Quality", anchor="w").pack(fill="x")
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

        self.no_audio_var = ctk.BooleanVar(value=self.cfg.get("no_audio", False))
        ctk.CTkCheckBox(
            left, text="No sound (mute video)", variable=self.no_audio_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._toggle_audio_fields,
        ).pack(anchor="w", pady=(5, 0))

        bitrate_row = ctk.CTkFrame(left, fg_color="transparent")
        bitrate_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(bitrate_row, text="Audio Bitrate", anchor="w").pack(side="left")
        self.audio_bitrate_var = ctk.StringVar(
            value=self._bitrate_to_label(self.cfg.get("audio_bitrate", "original"))
        )
        self.audio_bitrate_menu = ctk.CTkOptionMenu(
            bitrate_row,
            values=[label for _, label in AUDIO_BITRATE_LABELS],
            variable=self.audio_bitrate_var,
            width=110, fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER,
            button_hover_color=COLOR_ACCENT,
        )
        self.audio_bitrate_menu.pack(side="left", padx=(8, 0))
        self._toggle_audio_fields()

        # Video processing / trim (right column)
        ctk.CTkLabel(right, text="Video Processing", anchor="w").pack(fill="x")
        self.trim_var = ctk.BooleanVar(value=self.cfg.get("trim_enabled", True))
        ctk.CTkCheckBox(
            right, text="Trim video automatically", variable=self.trim_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._toggle_trim_fields,
        ).pack(anchor="w", pady=(2, 6))

        trim_row = ctk.CTkFrame(right, fg_color="transparent")
        trim_row.pack(fill="x")
        start_col = ctk.CTkFrame(trim_row, fg_color="transparent")
        start_col.pack(side="left")
        ctk.CTkLabel(start_col, text="Start (H:M:S)", anchor="w").pack(fill="x")
        self.start_entry = _TimeEntry(start_col, initial=self.cfg.get("trim_start", "00:05:00"))
        self.start_entry.pack(anchor="w")

        dur_col = ctk.CTkFrame(trim_row, fg_color="transparent")
        dur_col.pack(side="left", padx=(16, 0))
        ctk.CTkLabel(dur_col, text="Duration (H:M:S)", anchor="w").pack(fill="x")
        self.duration_entry = _TimeEntry(dur_col, initial=self.cfg.get("trim_duration", "00:15:00"))
        self.duration_entry.pack(anchor="w")
        self._toggle_trim_fields()

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
        ctk.CTkLabel(self, text="Queue", anchor="w").pack(fill="x", padx=pad_x)
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

    def _toggle_audio_fields(self):
        state = "disabled" if self.no_audio_var.get() else "normal"
        self.audio_bitrate_menu.configure(state=state)

    def _toggle_trim_fields(self):
        state = "normal" if self.trim_var.get() else "disabled"
        self.start_entry.configure_state(state)
        self.duration_entry.configure_state(state)

    def _browse_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder_entry.get() or str(Path.home()))
        if folder:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, folder)

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

    def _validate_trim_values(self) -> bool:
        if not self.trim_var.get():
            return True
        start = self.start_entry.get().strip()
        duration = self.duration_entry.get().strip()
        if not processor.validate_hms(start):
            messagebox.showerror(
                "Invalid Start Time",
                f"'{start}' is not a valid start time.\nPlease use the format HH:MM:SS, "
                "e.g. 00:05:00.",
            )
            return False
        if not processor.validate_hms(duration):
            messagebox.showerror(
                "Invalid Duration",
                f"'{duration}' is not a valid duration.\nPlease use the format HH:MM:SS, "
                "e.g. 00:15:00.",
            )
            return False
        return True

    def _on_add_to_queue(self):
        url = self.url_entry.get().strip()
        if not downloader.is_valid_youtube_url(url):
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

        if not self._validate_trim_values():
            return

        item = QueueItem(
            url=url,
            quality=self.quality_var.get(),
            no_audio=self.no_audio_var.get(),
            audio_bitrate=self._label_to_bitrate(self.audio_bitrate_var.get()),
            trim_enabled=self.trim_var.get(),
            trim_start=self.start_entry.get().strip(),
            trim_duration=self.duration_entry.get().strip(),
            output_folder=output_folder,
            delete_original=self.delete_original_var.get(),
        )
        self.queue_manager.add_item(item)
        self._save_current_settings(output_folder)

        self.url_entry.delete(0, "end")
        self._set_status(f"Added to queue ({len(self.queue_manager.items)} item(s) total).")

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

    def _save_current_settings(self, output_folder: str):
        self.cfg.update({
            "output_folder": output_folder,
            "quality": self.quality_var.get(),
            "no_audio": self.no_audio_var.get(),
            "audio_bitrate": self._label_to_bitrate(self.audio_bitrate_var.get()),
            "trim_enabled": self.trim_var.get(),
            "trim_start": self.start_entry.get().strip(),
            "trim_duration": self.duration_entry.get().strip(),
            "delete_original": self.delete_original_var.get(),
            "open_folder_when_finished": self.open_folder_var.get(),
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

    def _refresh_queue_display(self):
        items = self.queue_manager.items
        if not hasattr(self, "_queue_rows"):
            self._queue_rows: dict[str, dict] = {}

        if not items:
            for row in self._queue_rows.values():
                row["frame"].destroy()
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
                    self._queue_rows.pop(stale_id)["frame"].destroy()

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
        state_label = ctk.CTkLabel(top, text="", font=ctk.CTkFont(weight="bold"))
        state_label.pack(side="right")

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

        # Only pack/unpack the extra area (progress bar or message) when the
        # kind of content actually needs to change - not on every refresh.
        if item.state in (QueueState.DOWNLOADING, QueueState.TRIMMING):
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
            widgets["message_label"].configure(text=item.output_name, text_color="#9AA0A6")
        else:
            if widgets["shown_extra"] is not None:
                widgets["detail"].pack_forget()
                widgets["message_label"].pack_forget()
                widgets["shown_extra"] = None

    def _refresh_status_and_progress(self, items):
        active = next(
            (i for i in items if i.state in (QueueState.DOWNLOADING, QueueState.TRIMMING)), None
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


if __name__ == "__main__":
    app = AquariumDownloaderApp()
    app.mainloop()