"""
ui.py
-----
The application window. Builds widgets and wires user actions to the
non-UI layers (QueueManager, DownloadManager, ClipboardMonitor,
PreviewLoader) rather than containing queue or download logic itself.

Architecture: UI -> Queue Manager -> Download Manager -> Downloader -> yt-dlp.
"""

import json
import os
import threading
import tkinter as tk
from pathlib import Path

from tkinter import filedialog, messagebox

import customtkinter as ctk

import downloader
import history
import logging_setup
import notifications
import settings as settings_module
import update_check
import version
from clipboard import ClipboardMonitor
from download_manager import DownloadCallbacks, DownloadManager
from models import DownloadItem, Status
from queue_manager import QueueManager
from thumbnail import PreviewLoader
from tool_check import missing_tools_message
from utils import COLOR_ACCENT, COLOR_ACCENT_HOVER, COLOR_BG, COLOR_DANGER, COLOR_DANGER_HOVER, COLOR_PANEL, REPO_URL, parse_range_spec

# Drag-and-drop is optional: if tkinterdnd2 isn't installed, the app runs
# exactly as before, just without the ability to drag a link onto the window.
HAS_DND = False
try:
    from tkinterdnd2 import DND_TEXT, TkinterDnD
    HAS_DND = True
except ImportError:
    pass

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

if HAS_DND:
    # Standard recipe for combining customtkinter's CTk with tkinterdnd2's
    # drop-target support: mix in TkinterDnD's wrapper and initialize its
    # Tcl extension alongside CTk's own __init__.
    class _AppBase(TkinterDnD.DnDWrapper, ctk.CTk):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            try:
                self.TkdndVersion = TkinterDnD._require(self)
            except Exception:  # noqa: BLE001 - DnD is optional; never let it block startup
                self.TkdndVersion = None
else:
    _AppBase = ctk.CTk


class VideoDownloaderApp(_AppBase):
    def __init__(self):
        super().__init__()
        self.configure(fg_color=COLOR_BG)
        self.title("Video Downloader")
        self.geometry("720x900")
        self.minsize(660, 760)

        self.app_logger = logging_setup.setup_logging()
        self.app_logger.info("Application started")

        self.cfg = settings_module.load_settings()
        self.queue_manager = QueueManager()
        self.download_manager = DownloadManager(self, self.queue_manager, self.app_logger)
        self.clipboard_monitor = ClipboardMonitor(self, self._on_clipboard_url)
        self.preview_loader = PreviewLoader(self)

        self.item_progress: dict[str, str] = {}
        self.last_successful_folder: str | None = None
        self.preview_url = ""
        self.preview_info: dict | None = None
        self.preview_image = None
        self._closing = False

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_menu()
        self._build_ui()
        self._setup_drag_and_drop()
        self._bind_shortcuts()
        self._maybe_resume_queue()
        self.clipboard_monitor.start()

        missing = missing_tools_message()
        if missing:
            self.after(300, lambda: messagebox.showwarning("Missing requirements", missing))

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        pad_x = 20
        ctk.CTkLabel(self, text="Video Downloader", font=ctk.CTkFont(size=22, weight="bold")).pack(
            pady=(16, 2)
        )
        ctk.CTkLabel(
            self, text="Paste links, preview them, then download them one at a time.",
            font=ctk.CTkFont(size=11), text_color="gray60",
        ).pack(pady=(0, 12))

        ctk.CTkLabel(self, text="Video URL", anchor="w").pack(fill="x", padx=pad_x)
        url_row = ctk.CTkFrame(self, fg_color="transparent")
        url_row.pack(fill="x", padx=pad_x, pady=(2, 0))
        self.url_entry = ctk.CTkEntry(url_row, placeholder_text="https://...", fg_color=COLOR_PANEL)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Button-3>", self._show_url_context_menu)
        ctk.CTkButton(url_row, text="Paste", width=62, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._paste_url).pack(side="left", padx=(8, 0))
        ctk.CTkButton(url_row, text="Clear", width=62, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._clear_url).pack(side="left", padx=(6, 0))
        ctk.CTkButton(url_row, text="Preview", width=70, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._preview_clicked).pack(side="left", padx=(6, 0))

        preview = ctk.CTkFrame(self, fg_color=COLOR_PANEL)
        preview.pack(fill="x", padx=pad_x, pady=(8, 6))
        self.thumbnail_label = ctk.CTkLabel(preview, text="", width=120, height=68)
        self.thumbnail_label.pack(side="left", padx=(8, 10), pady=8)
        self.preview_label = ctk.CTkLabel(
            preview, text="Preview a link to check its title before adding it to the queue.",
            justify="left", anchor="w", wraplength=480,
        )
        self.preview_label.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=8)

        options = ctk.CTkFrame(self, fg_color="transparent")
        options.pack(fill="x", padx=pad_x)
        left = ctk.CTkFrame(options, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True)
        right = ctk.CTkFrame(options, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))

        ctk.CTkLabel(left, text="Output Folder", anchor="w").pack(fill="x")
        folder_row = ctk.CTkFrame(left, fg_color="transparent")
        folder_row.pack(fill="x", pady=(2, 8))
        self.folder_entry = ctk.CTkEntry(folder_row, fg_color=COLOR_PANEL)
        self.folder_entry.pack(side="left", fill="x", expand=True)
        self.folder_entry.insert(0, self.cfg.get("output_folder", str(Path.home() / "Downloads")))
        ctk.CTkButton(folder_row, text="Browse", width=68, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._browse_folder).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(left, text="Download Quality", anchor="w").pack(fill="x")
        self.quality_var = ctk.StringVar(value=self.cfg.get("quality", "best"))
        self.quality_labels = {
            "best": "Best Available",
            "2160p": "2160p / 4K",
            "1440p": "1440p",
            "1080p": "1080p",
            "720p": "720p",
            "480p": "480p",
            "360p": "360p",
            "audio_only": "Audio Only (MP3)",
        }
        quality_values_by_label = {label: value for value, label in self.quality_labels.items()}
        self.quality_display_var = ctk.StringVar(
            value=self.quality_labels.get(self.quality_var.get(), "Best Available")
        )
        ctk.CTkOptionMenu(
            left, variable=self.quality_display_var,
            values=list(self.quality_labels.values()),
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER, button_hover_color=COLOR_ACCENT,
            command=lambda label: self.quality_var.set(quality_values_by_label[label]),
        ).pack(fill="x", pady=(2, 6))
        self.mute_var = ctk.BooleanVar(value=not self.cfg.get("include_audio", True))
        self.include_audio_checkbox = ctk.CTkCheckBox(
            left, text="No sound (mute video)", variable=self.mute_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        )
        self.include_audio_checkbox.pack(anchor="w", pady=(5, 0))

        bitrate_row = ctk.CTkFrame(left, fg_color="transparent")
        bitrate_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(bitrate_row, text="MP3 Bitrate", anchor="w").pack(side="left")
        self.bitrate_var = ctk.StringVar(value=self.cfg.get("audio_bitrate", "192"))
        self.bitrate_menu = ctk.CTkOptionMenu(
            bitrate_row, variable=self.bitrate_var,
            values=["128", "192", "256", "320"],
            width=90, fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER, button_hover_color=COLOR_ACCENT,
        )
        self.bitrate_menu.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(bitrate_row, text="kbps", anchor="w", text_color="gray60").pack(side="left", padx=(4, 0))

        format_row = ctk.CTkFrame(left, fg_color="transparent")
        format_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(format_row, text="Format", anchor="w").pack(side="left")
        self.format_labels = {"best": "Best Available", "mp4": "MP4", "mkv": "MKV", "webm": "WEBM"}
        format_values_by_label = {label: value for value, label in self.format_labels.items()}
        self.format_var = ctk.StringVar(value=self.cfg.get("format_container", "best"))
        self.format_display_var = ctk.StringVar(value=self.format_labels.get(self.format_var.get(), "Best Available"))
        self.format_menu = ctk.CTkOptionMenu(
            format_row, variable=self.format_display_var,
            values=list(self.format_labels.values()), width=140,
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER, button_hover_color=COLOR_ACCENT,
            command=lambda label: self.format_var.set(format_values_by_label[label]),
        )
        self.format_menu.pack(side="left", padx=(8, 0))

        subtitle_row = ctk.CTkFrame(left, fg_color="transparent")
        subtitle_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(subtitle_row, text="Subtitles", anchor="w").pack(side="left")
        self.subtitle_labels = {"none": "None", "english": "English", "all": "All Available"}
        subtitle_values_by_label = {label: value for value, label in self.subtitle_labels.items()}
        self.subtitle_var = ctk.StringVar(value=self.cfg.get("subtitle_mode", "none"))
        self.subtitle_display_var = ctk.StringVar(value=self.subtitle_labels.get(self.subtitle_var.get(), "None"))
        self.subtitle_menu = ctk.CTkOptionMenu(
            subtitle_row, variable=self.subtitle_display_var,
            values=list(self.subtitle_labels.values()), width=140,
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER, button_hover_color=COLOR_ACCENT,
            command=lambda label: self.subtitle_var.set(subtitle_values_by_label[label]),
        )
        self.subtitle_menu.pack(side="left", padx=(8, 0))
        self.embed_subs_var = ctk.BooleanVar(value=self.cfg.get("embed_subs", False))
        self.embed_subs_checkbox = ctk.CTkCheckBox(
            left, text="Embed subtitles (otherwise saved as a separate file)", variable=self.embed_subs_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        )
        self.embed_subs_checkbox.pack(anchor="w", pady=(4, 0))

        cookies_row = ctk.CTkFrame(left, fg_color="transparent")
        cookies_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(cookies_row, text="Browser Cookies", anchor="w").pack(side="left")
        self.cookie_labels = {"none": "None", "chrome": "Chrome", "edge": "Edge", "firefox": "Firefox", "brave": "Brave"}
        cookie_values_by_label = {label: value for value, label in self.cookie_labels.items()}
        self.cookies_var = ctk.StringVar(value=self.cfg.get("cookies_from_browser", "none"))
        self.cookies_display_var = ctk.StringVar(value=self.cookie_labels.get(self.cookies_var.get(), "None"))
        ctk.CTkOptionMenu(
            cookies_row, variable=self.cookies_display_var,
            values=list(self.cookie_labels.values()), width=120,
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER, button_hover_color=COLOR_ACCENT,
            command=lambda label: self.cookies_var.set(cookie_values_by_label[label]),
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            left, text="Uses that browser's saved sign-in for sites that need one. "
            "Cookie data is read by yt-dlp directly and is never displayed, logged, or sent anywhere by this app.",
            anchor="w", justify="left", wraplength=280, font=ctk.CTkFont(size=10), text_color="gray60",
        ).pack(anchor="w", pady=(2, 0))

        self.quality_var.trace_add("write", self._update_audio_option)
        self._update_audio_option()

        ctk.CTkLabel(right, text="If a file already exists", anchor="w").pack(fill="x")
        self.duplicate_var = ctk.StringVar(value=self.cfg.get("duplicate_mode", "Rename automatically"))
        ctk.CTkOptionMenu(
            right, variable=self.duplicate_var,
            values=["Rename automatically", "Overwrite", "Ask me"],
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER, button_hover_color=COLOR_ACCENT,
        ).pack(fill="x", pady=(2, 12))
        self.keep_original_var = ctk.BooleanVar(value=self.cfg.get("keep_original", False))
        ctk.CTkCheckBox(
            right, text="Keep original video when making MP3", variable=self.keep_original_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)
        self.open_folder_var = ctk.BooleanVar(value=self.cfg.get("open_folder_when_finished", True))
        ctk.CTkCheckBox(
            right, text="Open folder when queue finishes", variable=self.open_folder_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)
        self.remove_completed_var = ctk.BooleanVar(value=self.cfg.get("remove_completed", False))
        ctk.CTkCheckBox(
            right, text="Remove completed items automatically", variable=self.remove_completed_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)

        ctk.CTkLabel(right, text="Download Speed Limit", anchor="w").pack(fill="x", pady=(10, 0))
        speed_row = ctk.CTkFrame(right, fg_color="transparent")
        speed_row.pack(fill="x", pady=(2, 0))
        self.speed_limit_var = ctk.StringVar(value=self.cfg.get("speed_limit", "Unlimited"))
        self.speed_limit_menu = ctk.CTkOptionMenu(
            speed_row, variable=self.speed_limit_var,
            values=["Unlimited", "1 MB/s", "2 MB/s", "5 MB/s", "10 MB/s", "Custom"],
            width=120, fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER, button_hover_color=COLOR_ACCENT,
            command=lambda _label: self._update_speed_limit_entry(),
        )
        self.speed_limit_menu.pack(side="left")
        self.speed_limit_custom_var = ctk.StringVar(value=str(self.cfg.get("speed_limit_custom_mbps", 5)))
        self.speed_limit_custom_entry = ctk.CTkEntry(speed_row, width=60, fg_color=COLOR_PANEL)
        self.speed_limit_custom_entry.insert(0, self.speed_limit_custom_var.get())
        self.speed_limit_custom_entry.pack(side="left", padx=(8, 4))
        ctk.CTkLabel(speed_row, text="MB/s", anchor="w", text_color="gray60").pack(side="left")
        self._update_speed_limit_entry()

        ctk.CTkLabel(right, text="Simultaneous Downloads", anchor="w").pack(fill="x", pady=(10, 0))
        self.simultaneous_var = ctk.StringVar(value=str(self.cfg.get("simultaneous_downloads", 1)))
        ctk.CTkOptionMenu(
            right, variable=self.simultaneous_var, values=["1", "2", "3", "4"], width=90,
            fg_color=COLOR_ACCENT, button_color=COLOR_ACCENT_HOVER, button_hover_color=COLOR_ACCENT,
        ).pack(anchor="w", pady=(2, 0))

        ctk.CTkLabel(right, text="Notifications", anchor="w").pack(fill="x", pady=(10, 0))
        self.notify_complete_var = ctk.BooleanVar(value=self.cfg.get("notify_on_complete", False))
        ctk.CTkCheckBox(
            right, text="Notify when a download completes", variable=self.notify_complete_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)
        self.notify_failure_var = ctk.BooleanVar(value=self.cfg.get("notify_on_failure", True))
        ctk.CTkCheckBox(
            right, text="Notify when a download fails", variable=self.notify_failure_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)
        self.notify_queue_var = ctk.BooleanVar(value=self.cfg.get("notify_on_queue_complete", True))
        ctk.CTkCheckBox(
            right, text="Notify when the queue completes", variable=self.notify_queue_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)
        self.play_sound_var = ctk.BooleanVar(value=self.cfg.get("play_sound_on_queue_complete", False))
        ctk.CTkCheckBox(
            right, text="Play a sound when the queue finishes", variable=self.play_sound_var,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        ).pack(anchor="w", pady=2)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=pad_x, pady=(12, 8))
        self.add_btn = ctk.CTkButton(actions, text="Add to Queue", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._add_to_queue)
        self.add_btn.pack(side="left", fill="x", expand=True)
        self.start_btn = ctk.CTkButton(actions, text="Start Queue", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._start_queue)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=8)
        self.cancel_btn = ctk.CTkButton(actions, text="Stop Queue", fg_color="#9b6b30", command=self._stop_queue)
        self.cancel_btn.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(self, text="Download Queue  (double-click a failed item for details)", anchor="w").pack(fill="x", padx=pad_x)
        list_frame = ctk.CTkFrame(self, fg_color=COLOR_PANEL)
        list_frame.pack(fill="both", expand=True, padx=pad_x, pady=(3, 6))
        self.queue_listbox = tk.Listbox(
            list_frame, selectmode=tk.SINGLE, activestyle="none", bg=COLOR_PANEL, fg="#eeeeee",
            selectbackground=COLOR_ACCENT, selectforeground="#ffffff", borderwidth=0,
            highlightthickness=0, font=("Segoe UI", 10),
        )
        self.queue_listbox.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.queue_listbox.bind("<Double-Button-1>", self._on_item_double_click)
        scrollbar = tk.Scrollbar(list_frame, command=self.queue_listbox.yview)
        scrollbar.pack(side="right", fill="y", pady=8, padx=(0, 8))
        self.queue_listbox.configure(yscrollcommand=scrollbar.set)

        reorder_actions = ctk.CTkFrame(self, fg_color="transparent")
        reorder_actions.pack(fill="x", padx=pad_x, pady=(0, 4))
        ctk.CTkButton(reorder_actions, text="\u2191 Move Up", width=95, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._move_up).pack(side="left")
        ctk.CTkButton(reorder_actions, text="\u2193 Move Down", width=105, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._move_down).pack(side="left", padx=8)
        ctk.CTkButton(reorder_actions, text="\u23f8 Pause Selected", width=130, fg_color="#9b6b30", command=self._pause_selected).pack(side="left")
        ctk.CTkButton(reorder_actions, text="History", width=90, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._open_history_window).pack(side="right")

        history_actions = ctk.CTkFrame(self, fg_color="transparent")
        history_actions.pack(fill="x", padx=pad_x, pady=(0, 4))
        ctk.CTkButton(history_actions, text="Remove Selected", width=125, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._remove_selected).pack(side="left")
        ctk.CTkButton(history_actions, text="Open Selected File", width=135, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._open_selected_file).pack(side="left", padx=8)
        ctk.CTkButton(history_actions, text="Open Selected Folder", width=145, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._open_selected_folder).pack(side="left")
        ctk.CTkButton(history_actions, text="Clear Completed", width=120, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._clear_completed).pack(side="right")

        failed_actions = ctk.CTkFrame(self, fg_color="transparent")
        failed_actions.pack(fill="x", padx=pad_x, pady=(0, 8))
        ctk.CTkButton(failed_actions, text="Retry Failed", width=110, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._retry_selected_failed).pack(side="left")
        ctk.CTkButton(failed_actions, text="Retry All Failed", width=130, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=self._retry_all_failed).pack(side="left", padx=8)
        ctk.CTkButton(failed_actions, text="Clear Failed", width=110, fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER, command=self._clear_failed).pack(side="right")

        self.stats_label = ctk.CTkLabel(self, text="Queue is empty.", anchor="w", font=ctk.CTkFont(size=11), text_color="gray60")
        self.stats_label.pack(fill="x", padx=pad_x, pady=(0, 4))

        self.progress_bar = ctk.CTkProgressBar(self, progress_color=COLOR_ACCENT)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=pad_x, pady=(2, 3))
        self.status_label = ctk.CTkLabel(self, text="Add a link to begin.", anchor="w")
        self.status_label.pack(fill="x", padx=pad_x, pady=(0, 12))

    # ------------------------------------------------------------------ #
    # Menu bar, drag & drop, keyboard shortcuts
    # ------------------------------------------------------------------ #
    def _build_menu(self):
        menubar = tk.Menu(self)

        settings_menu = tk.Menu(menubar, tearoff=False)
        settings_menu.add_command(label="Export Settings...", command=self._export_settings)
        settings_menu.add_command(label="Import Settings...", command=self._import_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="Reset Settings...", command=self._reset_settings)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Troubleshooting & Shortcuts", command=self._open_help_window)
        help_menu.add_command(label="Check for yt-dlp Updates", command=self._check_for_updates)
        help_menu.add_separator()
        help_menu.add_command(label="About", command=self._open_about_window)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    def _setup_drag_and_drop(self):
        if not HAS_DND or not getattr(self, "TkdndVersion", None):
            return
        try:
            self.drop_target_register(DND_TEXT)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:  # noqa: BLE001 - drag & drop is optional, never block startup over it
            pass

    def _on_drop(self, event):
        # Browsers commonly wrap a dragged link in braces or quotes.
        data = (event.data or "").strip().strip("{}").strip('"')
        candidate = data.split()[0] if data and not downloader.is_valid_url(data) else data
        if not downloader.is_valid_url(candidate):
            return
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, candidate)
        self.status_label.configure(text="Link dropped — click Preview or Add to Queue.")

    def _bind_shortcuts(self):
        self.url_entry.bind("<Return>", lambda e: self._preview_clicked())
        self.bind_all("<Control-Return>", lambda e: self._add_to_queue())
        self.bind_all("<Control-r>", lambda e: (self._retry_all_failed(), None)[-1])
        self.bind_all("<Control-v>", self._shortcut_paste)
        self.bind_all("<Control-a>", self._shortcut_select_all)
        self.queue_listbox.bind("<space>", self._shortcut_pause_resume)
        self.queue_listbox.bind("<Delete>", lambda e: self._remove_selected())

    def _shortcut_paste(self, event):
        # Let a focused Entry/Text widget handle Ctrl+V itself; only take
        # over when nothing else would (e.g. focus is on a button or the list).
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, tk.Text)):
            return
        self._paste_url()

    def _shortcut_select_all(self, event):
        focused = self.focus_get()
        if isinstance(focused, tk.Entry):
            focused.select_range(0, "end")
            return "break"
        return None

    def _shortcut_pause_resume(self, event):
        item = self._selected_item()
        if item is None:
            return "break"
        if item.status == Status.DOWNLOADING:
            self._pause_selected()
        elif item.status in (Status.PAUSED, Status.QUEUED):
            self._start_queue()
        return "break"

    def _open_about_window(self):
        dialog = tk.Toplevel(self)
        dialog.title("About Video Downloader Pro")
        dialog.configure(bg=COLOR_BG)
        dialog.geometry("360x300")
        dialog.transient(self)
        ctk.CTkLabel(dialog, text="Video Downloader Pro", font=ctk.CTkFont(size=17, weight="bold")).pack(pady=(20, 4))
        ctk.CTkLabel(dialog, text=f"Version {version.APP_VERSION}", text_color="gray60").pack()
        ctk.CTkLabel(dialog, text="Powered by yt-dlp\nand FFmpeg", justify="center").pack(pady=(14, 4))
        ctk.CTkLabel(dialog, text=f"yt-dlp {update_check.installed_version()}", text_color="gray60", font=ctk.CTkFont(size=11)).pack()
        ctk.CTkLabel(dialog, text="Copyright \u00a9 2026", text_color="gray60").pack(pady=(10, 14))
        ctk.CTkButton(
            dialog, text="GitHub Repository", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=lambda: __import__("webbrowser").open(REPO_URL),
        ).pack()
        ctk.CTkButton(dialog, text="Close", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=dialog.destroy).pack(pady=(14, 0))

    def _check_for_updates(self):
        self.status_label.configure(text="Checking for yt-dlp updates...")
        threading.Thread(target=self._do_check_for_updates, daemon=True).start()

    def _do_check_for_updates(self):
        result = update_check.check()
        self.after(0, self._show_update_result, result)

    def _show_update_result(self, result: dict):
        self.status_label.configure(text="Add a link to begin.")
        installed = result["installed"]
        latest = result["latest"]
        if latest is None:
            messagebox.showinfo(
                "Check for Updates",
                f"Couldn't reach PyPI to check for updates.\n\nInstalled yt-dlp version: {installed}",
            )
        elif result["up_to_date"]:
            messagebox.showinfo("Check for Updates", f"You're on the latest version of yt-dlp ({installed}).")
        else:
            messagebox.showinfo(
                "Check for Updates",
                f"A newer version of yt-dlp is available.\n\nInstalled: {installed}\nLatest: {latest}\n\n"
                "If running from source: pip install -U yt-dlp\n"
                "If using the installer, check for a newer app release.",
            )

    def _export_settings(self):
        self._save_settings(self.folder_entry.get().strip())
        path = filedialog.asksaveasfilename(
            title="Export Settings", defaultextension=".json",
            filetypes=[("JSON files", "*.json")], initialfile="video-downloader-settings.json",
        )
        if not path:
            return
        # Nothing sensitive is ever in these settings: cookies_from_browser is
        # just a browser name (e.g. "chrome"), never actual cookie/session data.
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except OSError as exc:
            messagebox.showerror("Export Settings", f"Couldn't save settings: {exc}")
            return
        messagebox.showinfo("Export Settings", "Settings exported successfully.")

    def _import_settings(self):
        path = filedialog.askopenfilename(title="Import Settings", filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                imported = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("Import Settings", f"Couldn't read that file: {exc}")
            return
        if not isinstance(imported, dict):
            messagebox.showerror("Import Settings", "That file doesn't look like a Video Downloader settings export.")
            return
        merged = dict(settings_module.DEFAULTS)
        merged.update(imported)
        settings_module.save_settings(merged)
        messagebox.showinfo("Import Settings", "Settings imported. Restart the app for all changes to take effect.")

    def _reset_settings(self):
        if not messagebox.askyesno(
            "Reset Settings", "Reset all Video Downloader settings?\n\nThis cannot be undone.",
        ):
            return
        settings_module.save_settings(dict(settings_module.DEFAULTS))
        messagebox.showinfo("Reset Settings", "Settings have been reset. Restart the app for all changes to take effect.")

    def _open_help_window(self):
        dialog = tk.Toplevel(self)
        dialog.title("Troubleshooting & Shortcuts")
        dialog.configure(bg=COLOR_BG)
        dialog.geometry("520x480")
        dialog.transient(self)

        ctk.CTkLabel(dialog, text="Troubleshooting", font=ctk.CTkFont(size=15, weight="bold")).pack(padx=16, pady=(16, 6), anchor="w")
        box = ctk.CTkTextbox(dialog, height=280)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        faq = (
            "Why did my download fail?\n"
            "Double-click the failed item in the queue for the specific reason and "
            "suggested fixes. Common causes: the video is private, region-locked, "
            "requires sign-in, or the site changed something yt-dlp needs updating for.\n\n"
            "Why is FFmpeg required?\n"
            "Many sites serve video and audio as separate streams; FFmpeg merges them "
            "and handles MP3 extraction. The installer bundles it, so you shouldn't "
            "need to install it separately.\n\n"
            "Why is the video unavailable?\n"
            "It may have been removed, made private, or restricted in your region "
            "since the link was shared.\n\n"
            "How do I update yt-dlp?\n"
            "If running from source: pip install -U yt-dlp. If using the installer, "
            "check for a newer release of the app.\n\n"
            "Where are my downloads saved?\n"
            "Wherever the Output Folder box points to. Use 'Open Selected Folder' on "
            "any queue item to jump straight there.\n\n"
            "How do I enable cookies?\n"
            "Browser cookie support for sites that require sign-in isn't available yet "
            "- it's on the roadmap.\n"
        )
        box.insert("1.0", faq)
        box.configure(state="disabled")

        ctk.CTkLabel(dialog, text="Keyboard Shortcuts", font=ctk.CTkFont(size=15, weight="bold")).pack(padx=16, pady=(0, 6), anchor="w")
        shortcuts_text = (
            "Ctrl+V — Paste URL      Enter — Preview      Ctrl+Enter — Add to Queue\n"
            "Space — Pause/Resume selected      Delete — Remove selected\n"
            "Ctrl+A — Select all text in a field      Ctrl+R — Retry all failed"
        )
        ctk.CTkLabel(dialog, text=shortcuts_text, justify="left", anchor="w", text_color="gray70", wraplength=490).pack(padx=16, pady=(0, 12), anchor="w")
        ctk.CTkButton(dialog, text="Close", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=dialog.destroy).pack(pady=(0, 14))

    # ------------------------------------------------------------------ #
    # Small UI helpers
    # ------------------------------------------------------------------ #
    def _browse_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder_entry.get() or str(Path.home()))
        if folder:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, folder)

    def _paste_url(self):
        try:
            clipboard_text = self.clipboard_get()
            self.url_entry.insert("insert", clipboard_text)
        except tk.TclError:
            pass

    def _clear_url(self):
        self.url_entry.delete(0, "end")
        self.url_entry.focus()

    def _show_url_context_menu(self, event):
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Paste", command=self._paste_url)
        menu.add_command(label="Copy", command=lambda: self.url_entry.event_generate("<<Copy>>"))
        menu.add_command(label="Cut", command=lambda: self.url_entry.event_generate("<<Cut>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: self.url_entry._entry.select_range(0, "end"))
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()

    def _update_audio_option(self, *_args):
        is_audio_only = self.quality_var.get() == "audio_only"
        self.include_audio_checkbox.configure(state="disabled" if is_audio_only else "normal")
        self.bitrate_menu.configure(state="normal" if is_audio_only else "disabled")
        # Format container and subtitles are meaningless for an audio-only extraction.
        self.format_menu.configure(state="disabled" if is_audio_only else "normal")
        self.subtitle_menu.configure(state="disabled" if is_audio_only else "normal")
        self.embed_subs_checkbox.configure(state="disabled" if is_audio_only else "normal")

    def _update_speed_limit_entry(self):
        is_custom = self.speed_limit_var.get() == "Custom"
        self.speed_limit_custom_entry.configure(state="normal" if is_custom else "disabled")

    def _resolve_speed_limit_bytes(self) -> int | None:
        label = self.speed_limit_var.get()
        fixed_mbps = {"Unlimited": None, "1 MB/s": 1, "2 MB/s": 2, "5 MB/s": 5, "10 MB/s": 10}
        if label == "Custom":
            try:
                mbps = float(self.speed_limit_custom_entry.get())
            except ValueError:
                return None
        else:
            mbps = fixed_mbps.get(label)
        return int(mbps * 1024 * 1024) if mbps else None

    # ------------------------------------------------------------------ #
    # Clipboard monitoring (stops cleanly when the app closes)
    # ------------------------------------------------------------------ #
    def _on_clipboard_url(self, clip: str, last_value: str):
        current = self.url_entry.get().strip()
        # Only auto-fill if the box is empty or still holds the last clip we
        # auto-filled, so we never overwrite something the user typed themselves.
        if not current or current == last_value:
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, clip)

    # ------------------------------------------------------------------ #
    # Preview
    # ------------------------------------------------------------------ #
    def _preview_clicked(self):
        url = self.url_entry.get().strip()
        if not downloader.is_valid_url(url):
            messagebox.showerror("Invalid URL", "Paste a valid http:// or https:// video link first.")
            return
        self.preview_label.configure(text="Looking up video information...")
        self.preview_loader.request(
            url, lambda info: self._show_preview(url, info), self._show_preview_error,
            cookies_from_browser=self.cookies_var.get(),
        )

    def _show_preview_error(self, exc: Exception):
        self.preview_label.configure(text=f"Preview unavailable: {exc}")

    def _show_preview(self, url: str, info: dict):
        self.preview_url = url
        self.preview_info = info
        if info.get("is_playlist"):
            count = len(info.get("entries") or [])
            self.thumbnail_label.configure(image=None, text="")
            self.preview_image = None
            self.preview_label.configure(
                text=f"Playlist: {info.get('playlist_title', 'Playlist')}\n{count} videos found"
            )
            return
        duration = info.get("duration")
        minutes = f" • {duration // 60}:{duration % 60:02d}" if isinstance(duration, int) else ""
        uploader = f"\n{info.get('uploader')}" if info.get("uploader") else ""
        self.preview_label.configure(text=f"{info.get('title', 'Video')}{uploader}{minutes}")
        thumbnail = info.get("thumbnail")
        if thumbnail:
            self.preview_loader.load_thumbnail(thumbnail, self._show_thumbnail)

    def _show_thumbnail(self, pil_image):
        display_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(120, 68))
        self.preview_image = display_image
        self.thumbnail_label.configure(image=display_image, text="")

    # ------------------------------------------------------------------ #
    # Adding to the queue (with duplicate-URL detection)
    # ------------------------------------------------------------------ #
    def _add_to_queue(self):
        url = self.url_entry.get().strip()
        if not downloader.is_valid_url(url):
            messagebox.showerror("Invalid URL", "Paste a valid http:// or https:// video link first.")
            return
        output_folder = self.folder_entry.get().strip()
        try:
            Path(output_folder).mkdir(parents=True, exist_ok=True)
        except OSError:
            messagebox.showerror("Folder Error", "The output folder path is not valid.")
            return

        previewed = self.preview_url == url and self.preview_info

        if previewed and self.preview_info.get("is_playlist"):
            entries = self.preview_info.get("entries") or []
            if not entries:
                messagebox.showwarning(
                    "Empty Playlist", "No downloadable videos were found in this playlist."
                )
                return
            playlist_title = self.preview_info.get("playlist_title", "this playlist")
            choice = self._ask_playlist_choice(playlist_title, len(entries))
            if choice == "all":
                skipped = 0
                for entry in entries:
                    if self.queue_manager.find_duplicate(entry["url"]) is not None:
                        skipped += 1
                        continue
                    self.queue_manager.add(self._build_download_item(entry["url"], output_folder, entry.get("title", "Video link")))
                self._save_settings(output_folder)
                self._refresh_queue()
                self.queue_manager.persist()
                added = len(entries) - skipped
                message = f"Added {added} video(s) from the playlist to the queue."
                if skipped:
                    message += f" Skipped {skipped} already in the queue."
                self.status_label.configure(text=message)
                self._clear_url()
            elif choice == "select":
                self._save_settings(output_folder)
                self._open_playlist_selection_window(entries, output_folder)
            return  # "all", "select", and "cancel" are all fully handled above

        duplicate = self.queue_manager.find_duplicate(url)
        if duplicate is not None:
            proceed = messagebox.askyesno(
                "Already in queue",
                f"\"{duplicate.title}\" (or a version of this link with different "
                "tracking parameters) is already in the queue.\n\nAdd it again anyway?",
            )
            if not proceed:
                return

        title = self.preview_info.get("title", "Video link") if previewed and not self.preview_info.get("is_playlist") else "Video link"
        self.queue_manager.add(self._build_download_item(url, output_folder, title))
        self._save_settings(output_folder)
        self._refresh_queue()
        self.queue_manager.persist()
        self.status_label.configure(text="Added to queue. You can add another link or start downloading.")
        self._clear_url()

    def _build_download_item(self, url: str, output_folder: str, title: str) -> DownloadItem:
        return DownloadItem(
            url=url, output_folder=output_folder, quality=self.quality_var.get(),
            include_audio=not self.mute_var.get(), keep_original=self.keep_original_var.get(),
            duplicate_mode=self.duplicate_var.get(), audio_bitrate=self.bitrate_var.get(),
            format_container=self.format_var.get(), subtitle_mode=self.subtitle_var.get(),
            embed_subs=self.embed_subs_var.get(), speed_limit_bytes=self._resolve_speed_limit_bytes(),
            cookies_from_browser=self.cookies_var.get(), title=title,
        )

    def _ask_playlist_choice(self, playlist_title: str, count: int) -> str:
        """Modal 3-way choice: 'all', 'select', or 'cancel'."""
        result = {"choice": "cancel"}
        dialog = tk.Toplevel(self)
        dialog.title("Playlist Detected")
        dialog.configure(bg=COLOR_BG)
        dialog.geometry("380x210")
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Playlist Detected", font=ctk.CTkFont(size=15, weight="bold")).pack(pady=(18, 6))
        ctk.CTkLabel(dialog, text=f"\"{playlist_title}\"\n{count} videos found.", justify="center", wraplength=340).pack(pady=(0, 6))
        ctk.CTkLabel(dialog, text="What would you like to do?", text_color="gray60").pack(pady=(0, 12))
        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(pady=(0, 16))

        def choose(value):
            result["choice"] = value
            dialog.destroy()

        ctk.CTkButton(btn_row, text="Add All", width=100, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=lambda: choose("all")).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="Select Videos", width=120, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=lambda: choose("select")).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="Cancel", width=90, fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER, command=lambda: choose("cancel")).pack(side="left", padx=6)
        dialog.wait_window()
        return result["choice"]

    def _open_playlist_selection_window(self, entries: list, output_folder: str):
        win = tk.Toplevel(self)
        win.title("Select Videos")
        win.configure(bg=COLOR_BG)
        win.geometry("480x520")
        win.transient(self)

        ctk.CTkLabel(win, text=f"Select Videos  ({len(entries)} total)", font=ctk.CTkFont(size=15, weight="bold")).pack(padx=16, pady=(16, 8), anchor="w")

        range_row = ctk.CTkFrame(win, fg_color="transparent")
        range_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(range_row, text="Range:", anchor="w").pack(side="left")
        range_entry = ctk.CTkEntry(range_row, placeholder_text="e.g. 1,3,7,10-15", fg_color=COLOR_PANEL)
        range_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))

        list_frame = ctk.CTkFrame(win, fg_color=COLOR_PANEL)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        listbox = tk.Listbox(
            list_frame, selectmode=tk.EXTENDED, activestyle="none", bg=COLOR_PANEL, fg="#eeeeee",
            selectbackground=COLOR_ACCENT, selectforeground="#ffffff", borderwidth=0,
            highlightthickness=0, font=("Segoe UI", 10),
        )
        listbox.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        scrollbar = tk.Scrollbar(list_frame, command=listbox.yview)
        scrollbar.pack(side="right", fill="y", pady=8, padx=(0, 8))
        listbox.configure(yscrollcommand=scrollbar.set)
        for i, entry in enumerate(entries):
            listbox.insert(tk.END, f"{i + 1}. {entry.get('title', 'Video')}")
        listbox.selection_set(0, tk.END)  # everything selected by default, like a fully-checked list

        def apply_range():
            indices = parse_range_spec(range_entry.get(), len(entries))
            if not indices:
                messagebox.showinfo("Select Videos", "That range didn't match any videos - check the format (e.g. 1,3,7,10-15).")
                return
            listbox.selection_clear(0, tk.END)
            for i in indices:
                listbox.selection_set(i)

        ctk.CTkButton(range_row, text="Select Range", width=100, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=apply_range).pack(side="left")

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkButton(btn_row, text="Select All", width=90, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=lambda: listbox.selection_set(0, tk.END)).pack(side="left")
        ctk.CTkButton(btn_row, text="Select None", width=100, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=lambda: listbox.selection_clear(0, tk.END)).pack(side="left", padx=8)

        def add_selected():
            indices = listbox.curselection()
            if not indices:
                messagebox.showinfo("Select Videos", "Select at least one video first.")
                return
            skipped = 0
            added = 0
            for i in indices:
                entry = entries[i]
                if self.queue_manager.find_duplicate(entry["url"]) is not None:
                    skipped += 1
                    continue
                self.queue_manager.add(self._build_download_item(entry["url"], output_folder, entry.get("title", "Video link")))
                added += 1
            self._refresh_queue()
            self.queue_manager.persist()
            message = f"Added {added} selected video(s) from the playlist to the queue."
            if skipped:
                message += f" Skipped {skipped} already in the queue."
            self.status_label.configure(text=message)
            self._clear_url()
            win.destroy()

        ctk.CTkButton(win, text="Add Selected", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=add_selected).pack(pady=(0, 14))

    def _save_settings(self, output_folder: str):
        self.cfg.update({
            "output_folder": output_folder, "quality": self.quality_var.get(),
            "include_audio": not self.mute_var.get(), "keep_original": self.keep_original_var.get(),
            "duplicate_mode": self.duplicate_var.get(), "open_folder_when_finished": self.open_folder_var.get(),
            "remove_completed": self.remove_completed_var.get(), "audio_bitrate": self.bitrate_var.get(),
            "format_container": self.format_var.get(), "subtitle_mode": self.subtitle_var.get(),
            "embed_subs": self.embed_subs_var.get(), "speed_limit": self.speed_limit_var.get(),
            "speed_limit_custom_mbps": self.speed_limit_custom_entry.get(),
            "simultaneous_downloads": self.simultaneous_var.get(),
            "notify_on_complete": self.notify_complete_var.get(),
            "notify_on_failure": self.notify_failure_var.get(),
            "notify_on_queue_complete": self.notify_queue_var.get(),
            "play_sound_on_queue_complete": self.play_sound_var.get(),
            "cookies_from_browser": self.cookies_var.get(),
        })
        settings_module.save_settings(self.cfg)

    def _refresh_queue(self):
        self.queue_listbox.delete(0, tk.END)
        for item in self.queue_manager.items:
            label = item.title if item.title != "Video link" else item.url
            icon = Status.ICONS.get(item.status, "")
            line = f"{icon} [{item.status}]  {label}"
            if item.status == Status.DOWNLOADING and item.id in self.item_progress:
                line += f"  \u2014  {self.item_progress[item.id]}"
            self.queue_listbox.insert(tk.END, line)
        self._update_stats()
        self._update_window_title()

    def _update_window_title(self):
        total = len(self.queue_manager.items)
        if total == 0:
            self.title("Video Downloader")
            return
        counts = self.queue_manager.stats()
        completed = counts[Status.COMPLETED]
        downloading = counts[Status.DOWNLOADING]
        if downloading:
            self.title(f"Video Downloader — {completed}/{total} done, {downloading} downloading")
        elif completed:
            self.title(f"Video Downloader — {completed}/{total} done")
        else:
            self.title("Video Downloader")

    def _update_stats(self):
        total = len(self.queue_manager.items)
        if total == 0:
            self.stats_label.configure(text="Queue is empty.")
            return
        counts = self.queue_manager.stats()
        overall = int(counts[Status.COMPLETED] / total * 100)
        self.stats_label.configure(text=(
            f"Queue: {total} items   "
            f"Completed: {counts[Status.COMPLETED]}   "
            f"Downloading: {counts[Status.DOWNLOADING]}   "
            f"Queued: {counts[Status.QUEUED]}   "
            f"Paused: {counts[Status.PAUSED]}   "
            f"Failed: {counts[Status.FAILED]}   "
            f"Overall progress: {overall}%"
        ))

    def _maybe_resume_queue(self):
        loaded = self.queue_manager.load_persisted()
        if not loaded:
            return
        resume = messagebox.askyesno(
            "Previous downloads found",
            f"Found {len(loaded)} queued/paused download(s) from your last session.\n\n"
            "Resume queue?",
        )
        if resume:
            self.queue_manager.replace_with(loaded)
            self._refresh_queue()
            self.status_label.configure(text=f"Restored {len(loaded)} item(s) from your last session.")
        else:
            self.queue_manager.discard_persisted()

    def _move_selected(self, direction: int):
        selected = self.queue_listbox.curselection()
        if not selected:
            return
        index = selected[0]
        item = self.queue_manager.items[index]
        if item.status == Status.DOWNLOADING:
            messagebox.showinfo("Queue", "Wait for the current download to pause or finish before reordering it.")
            return
        if not self.queue_manager.move(item, direction):
            return
        self._refresh_queue()
        self.queue_listbox.selection_set(index + direction)
        self.queue_manager.persist()

    def _move_up(self):
        self._move_selected(-1)

    def _move_down(self):
        self._move_selected(1)

    # ------------------------------------------------------------------ #
    # Queue processing: delegated to DownloadManager. This layer only
    # decides how many workers to run and what to do with the callbacks.
    # ------------------------------------------------------------------ #
    def _start_queue(self):
        if self.download_manager.is_running():
            return
        if not self.queue_manager.has_runnable():
            self.status_label.configure(text="There are no queued or paused downloads in the queue.")
            return
        missing = missing_tools_message()
        if missing:
            messagebox.showerror("Missing requirements", missing)
            return
        try:
            worker_count = max(1, min(4, int(self.simultaneous_var.get())))
        except ValueError:
            worker_count = 1
        callbacks = DownloadCallbacks(
            on_item_start=self._dm_on_item_start,
            on_progress=self._dm_on_progress,
            on_item_finished=self._dm_on_item_finished,
            on_queue_finished=self._dm_on_queue_finished,
            ask_duplicate_action=self._dm_ask_duplicate_action,
        )
        self.download_manager.start(worker_count, callbacks)

    def _stop_queue(self):
        if not self.download_manager.is_running():
            return
        self.download_manager.stop()
        self.status_label.configure(
            text="Stopping... active downloads will pause where they are and resume from Start Queue."
        )

    def _pause_selected(self):
        item = self._selected_item()
        if item is None or item.status != Status.DOWNLOADING:
            messagebox.showinfo("Pause", "Select a downloading item first.")
            return
        if self.download_manager.pause_item(item.id):
            self.status_label.configure(text=f"Pausing {item.title}...")

    def _dm_on_item_start(self, item: DownloadItem):
        downloading = self.queue_manager.stats()[Status.DOWNLOADING]
        self.status_label.configure(
            text=f"Downloading: {item.title}" if downloading <= 1 else f"Downloading {downloading} items..."
        )
        self._refresh_queue()

    def _dm_on_progress(self, item: DownloadItem, info: dict):
        if info["status"] == "downloading":
            pct = info.get("percent")
            text = f"{pct:.0f}%  \u2022  {info['speed']}  \u2022  ETA {info['eta']}" if pct is not None else f"downloading...  \u2022  {info['speed']}"
        else:
            text = "processing..."
        self.item_progress[item.id] = text
        self._refresh_queue()
        total = len(self.queue_manager.items)
        if total:
            completed = self.queue_manager.stats()[Status.COMPLETED]
            self.progress_bar.set(completed / total)

    def _dm_on_item_finished(self, item: DownloadItem):
        self.item_progress.pop(item.id, None)
        if item.status == Status.COMPLETED:
            self.last_successful_folder = item.output_folder
            if self.notify_complete_var.get():
                notifications.show_toast("Download Complete", item.title)
        elif item.status == Status.FAILED and self.notify_failure_var.get():
            notifications.show_toast("Download Failed", f"{item.title}\n{item.error}")
        self._refresh_queue()

    def _dm_on_queue_finished(self, was_stopped: bool):
        if was_stopped:
            self.status_label.configure(
                text="Queue stopped. Click Start Queue to resume any paused downloads."
            )
        else:
            self.status_label.configure(text="Queue finished.")
            if self.notify_queue_var.get():
                notifications.show_toast("Queue Complete", "All downloads in the queue have finished.")
            if self.play_sound_var.get():
                notifications.play_sound()
            # Open the folder for the last download that actually succeeded,
            # never the last queue item regardless of whether it failed.
            if self.open_folder_var.get() and self.last_successful_folder:
                try:
                    os.startfile(self.last_successful_folder)
                except OSError:
                    pass
        if self.remove_completed_var.get():
            self.queue_manager.clear_completed()
        self._refresh_queue()
        self.queue_manager.persist()

    def _dm_ask_duplicate_action(self, filepath: str) -> str:
        """Ask on the UI thread while a download-manager worker waits for the answer."""
        response: dict[str, str] = {}
        answered = threading.Event()

        def ask():
            answer = messagebox.askyesnocancel(
                "File already exists",
                f"{Path(filepath).name} already exists.\n\n"
                "Yes: overwrite it\nNo: save a second copy with a unique name\nCancel: skip this download",
            )
            response["action"] = "overwrite" if answer is True else "rename" if answer is False else "skip"
            answered.set()

        self.after(0, ask)
        answered.wait()
        return response["action"]

    # ------------------------------------------------------------------ #
    # Queue item actions
    # ------------------------------------------------------------------ #
    def _selected_item(self) -> DownloadItem | None:
        selected = self.queue_listbox.curselection()
        return self.queue_manager.items[selected[0]] if selected else None

    def _remove_selected(self):
        item = self._selected_item()
        if item is None:
            return
        if item.status == Status.DOWNLOADING:
            # Removing an in-progress item cancels (abandons) it rather than
            # leaving it half-downloaded; the partial file is deleted once
            # the download manager notices the cancel request.
            self.download_manager.cancel_item(item.id)
            self.status_label.configure(text=f"Cancelling {item.title}...")
            return
        self.queue_manager.remove(item)
        self._refresh_queue()
        self.queue_manager.persist()

    def _clear_completed(self):
        self.queue_manager.clear_completed()
        self._refresh_queue()
        self.queue_manager.persist()

    def _clear_failed(self):
        self.queue_manager.clear_failed()
        self._refresh_queue()
        self.queue_manager.persist()

    def _retry_selected_failed(self):
        item = self._selected_item()
        if item is None or item.status != Status.FAILED:
            messagebox.showinfo("Retry Failed", "Select a failed download first.")
            return
        self.queue_manager.retry(item)
        self._refresh_queue()
        self.queue_manager.persist()
        self.status_label.configure(text=f"Requeued: {item.title}")

    def _retry_all_failed(self):
        count = self.queue_manager.retry_all_failed()
        self._refresh_queue()
        if count:
            self.queue_manager.persist()
            self.status_label.configure(text=f"Requeued {count} failed download(s).")
        else:
            self.status_label.configure(text="No failed downloads to retry.")

    def _on_item_double_click(self, _event):
        item = self._selected_item()
        if item is None:
            return
        if item.status == Status.FAILED:
            self._show_error_dialog(item)
        elif item.status == Status.COMPLETED:
            self._open_selected_file()

    def _suggest_solutions(self, reason: str) -> list[str]:
        lowered = (reason or "").lower()
        if "format" in lowered:
            return ["Try Best Available quality", "Try a different format (MP4/MKV/WEBM)", "Update yt-dlp"]
        if "sign in" in lowered or "login" in lowered:
            return ["Enable Browser Cookies for this site and retry", "Check that the video isn't private or members-only"]
        if "region" in lowered:
            return ["The video may not be available in your region", "Try again later in case this is temporary"]
        if "too long" in lowered:
            return ["Choose a shorter output folder path", "Shorten the video title if possible"]
        if "network" in lowered or "rate" in lowered or "timed out" in lowered:
            return ["Check your internet connection", "Wait a few minutes and retry"]
        if "unavailable" in lowered or "private" in lowered or "removed" in lowered:
            return ["Double-check the link still works in a browser", "The video may have been removed or made private"]
        return ["Try Best Available quality", "Update yt-dlp", "Check your internet connection"]

    def _show_error_dialog(self, item: DownloadItem):
        dialog = tk.Toplevel(self)
        dialog.title("Download Failed")
        dialog.configure(bg=COLOR_BG)
        dialog.geometry("480x420")
        dialog.transient(self)

        ctk.CTkLabel(
            dialog, text="\u274c Failed", font=ctk.CTkFont(size=16, weight="bold"), text_color="#e05a4e",
        ).pack(pady=(14, 4), padx=14, anchor="w")
        ctk.CTkLabel(dialog, text=item.title, wraplength=440, anchor="w", justify="left").pack(padx=14, anchor="w")
        ctk.CTkLabel(dialog, text="Reason:", anchor="w", font=ctk.CTkFont(weight="bold")).pack(padx=14, pady=(10, 0), anchor="w")
        ctk.CTkLabel(dialog, text=item.error or "Unknown error", wraplength=440, anchor="w", justify="left").pack(padx=14, anchor="w")

        solutions = self._suggest_solutions(item.error or "")
        ctk.CTkLabel(dialog, text="Possible solutions:", anchor="w", font=ctk.CTkFont(weight="bold")).pack(padx=14, pady=(10, 0), anchor="w")
        for solution in solutions:
            ctk.CTkLabel(dialog, text=f"\u2022 {solution}", anchor="w", justify="left", wraplength=440).pack(padx=18, anchor="w")

        detail_box = ctk.CTkTextbox(dialog, height=90)
        detail_box.insert("1.0", item.error_detail or item.error or "")
        detail_box.configure(state="disabled")
        detail_shown = {"value": False}

        def toggle_details():
            if detail_shown["value"]:
                detail_box.pack_forget()
                toggle_btn.configure(text="Show Technical Details")
            else:
                detail_box.pack(fill="both", expand=True, padx=14, pady=(6, 4), before=btn_row)
                toggle_btn.configure(text="Hide Technical Details")
            detail_shown["value"] = not detail_shown["value"]

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(10, 14), side="bottom")
        toggle_btn = ctk.CTkButton(
            dialog, text="Show Technical Details", fg_color="transparent", border_width=1,
            border_color=COLOR_ACCENT, hover_color=COLOR_PANEL, command=toggle_details,
        )
        toggle_btn.pack(padx=14, pady=(10, 0), anchor="w")

        def do_retry():
            self.queue_manager.retry(item)
            self._refresh_queue()
            self.queue_manager.persist()
            dialog.destroy()

        def do_copy():
            self.clipboard_clear()
            self.clipboard_append(item.error_detail or item.error or "")

        ctk.CTkButton(btn_row, text="Retry", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=do_retry).pack(side="left")
        ctk.CTkButton(btn_row, text="Copy Error", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=do_copy).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Close", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=dialog.destroy).pack(side="right")

        lowered_reason = (item.error or "").lower()
        if "sign in" in lowered_reason or "login" in lowered_reason:
            def do_retry_with_cookies():
                if self.cookies_var.get() == "none":
                    self.cookies_var.set("chrome")
                    self.cookies_display_var.set(self.cookie_labels["chrome"])
                item.cookies_from_browser = self.cookies_var.get()
                self.queue_manager.retry(item)
                self._refresh_queue()
                self.queue_manager.persist()
                dialog.destroy()

            ctk.CTkButton(
                dialog, text="Retry with Browser Cookies", fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                command=do_retry_with_cookies,
            ).pack(padx=14, pady=(0, 8), anchor="w", before=btn_row)

    def _redownload_from_history(self, entry: "history.HistoryEntry"):
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, entry.url)
        if entry.quality in self.quality_labels:
            self.quality_var.set(entry.quality)
            self.quality_display_var.set(self.quality_labels[entry.quality])
        self.status_label.configure(text="Link loaded from history — click Add to Queue to redownload.")

    def _open_history_window(self):
        entries = history.load_history()
        win = tk.Toplevel(self)
        win.title("Download History")
        win.configure(bg=COLOR_BG)
        win.geometry("560x420")
        win.transient(self)

        ctk.CTkLabel(win, text="Download History", font=ctk.CTkFont(size=16, weight="bold")).pack(
            pady=(12, 6), padx=14, anchor="w"
        )

        list_frame = ctk.CTkFrame(win, fg_color=COLOR_PANEL)
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        listbox = tk.Listbox(
            list_frame, selectmode=tk.SINGLE, activestyle="none", bg=COLOR_PANEL, fg="#eeeeee",
            selectbackground=COLOR_ACCENT, selectforeground="#ffffff", borderwidth=0,
            highlightthickness=0, font=("Segoe UI", 10),
        )
        listbox.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        scrollbar = tk.Scrollbar(list_frame, command=listbox.yview)
        scrollbar.pack(side="right", fill="y", pady=8, padx=(0, 8))
        listbox.configure(yscrollcommand=scrollbar.set)

        def refresh_list():
            listbox.delete(0, tk.END)
            for entry in entries:
                icon = "\u2713" if entry.status == Status.COMPLETED else "\u274c"
                listbox.insert(tk.END, f"{icon} {entry.title}   ({entry.timestamp})")

        refresh_list()

        def selected_entry():
            sel = listbox.curselection()
            return entries[sel[0]] if sel else None

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 6))

        def do_open_file():
            entry = selected_entry()
            if not entry or not entry.filepath:
                messagebox.showinfo("Open File", "No file is on record for this entry.")
                return
            try:
                os.startfile(entry.filepath)
            except OSError:
                messagebox.showerror("Open File", "The file could not be opened (it may have been moved or deleted).")

        def do_open_folder():
            entry = selected_entry()
            if not entry or not entry.filepath:
                messagebox.showinfo("Open Folder", "No file is on record for this entry.")
                return
            try:
                os.startfile(str(Path(entry.filepath).parent))
            except OSError:
                messagebox.showerror("Open Folder", "The folder could not be opened.")

        def do_copy_url():
            entry = selected_entry()
            if entry:
                self.clipboard_clear()
                self.clipboard_append(entry.url)

        def do_redownload():
            entry = selected_entry()
            if entry:
                self._redownload_from_history(entry)
                win.destroy()

        def do_remove():
            entry = selected_entry()
            if entry:
                history.remove_entry(entry.id)
                entries.remove(entry)
                refresh_list()

        def do_clear_all():
            if messagebox.askyesno("Clear History", "Clear the entire download history? This cannot be undone."):
                history.clear_history()
                entries.clear()
                refresh_list()

        ctk.CTkButton(btn_row, text="Open File", width=95, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=do_open_file).pack(side="left")
        ctk.CTkButton(btn_row, text="Open Folder", width=105, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=do_open_folder).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="Copy URL", width=90, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=do_copy_url).pack(side="left")
        ctk.CTkButton(btn_row, text="Redownload", width=105, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, command=do_redownload).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="Remove", width=90, fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER, command=do_remove).pack(side="left")

        ctk.CTkButton(win, text="Clear History", fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER, command=do_clear_all).pack(pady=(0, 12))

    def _open_selected_file(self):
        item = self._selected_item()
        if not item or not item.filepath:
            messagebox.showinfo("Open File", "Select a completed download first.")
            return
        try:
            os.startfile(item.filepath)
        except OSError:
            messagebox.showerror("Open File", "The file could not be opened.")

    def _open_selected_folder(self):
        item = self._selected_item()
        if item is None:
            messagebox.showinfo("Open Folder", "Select a queue item first.")
            return
        try:
            os.startfile(item.output_folder)
        except OSError:
            messagebox.showerror("Open Folder", "The folder could not be opened.")

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #
    def _on_close(self):
        self._closing = True
        self.clipboard_monitor.stop()
        if self.download_manager.is_running():
            self.download_manager.stop_requested.set()
            self.download_manager.cancel_all_active()
        self.queue_manager.persist()
        self.app_logger.info("Application closing")
        self.destroy()
