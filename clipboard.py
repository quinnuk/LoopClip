"""
clipboard.py
------------
Polls the system clipboard for a new URL and hands it to a callback. The
UI owns the decision of whether to actually fill the URL box (it knows
what the user has typed); this module only owns the polling loop and
knows how to stop cleanly when the app closes.
"""

import downloader

try:
    import pyperclip
    HAS_CLIPBOARD = True
except ImportError:
    HAS_CLIPBOARD = False


class ClipboardMonitor:
    def __init__(self, root, on_url_detected, poll_ms: int = 1500):
        self.root = root
        self.on_url_detected = on_url_detected
        self.poll_ms = poll_ms
        self.last_value = ""
        self._stopped = False

    def start(self) -> None:
        self._poll()

    def stop(self) -> None:
        self._stopped = True

    def _poll(self) -> None:
        if self._stopped or not HAS_CLIPBOARD:
            return
        try:
            clip = pyperclip.paste().strip()
        except Exception:  # noqa: BLE001 - clipboard access can fail in odd ways; never crash the poll loop
            clip = None
        if clip and clip != self.last_value and downloader.is_valid_url(clip):
            self.on_url_detected(clip, self.last_value)
        if clip is not None:
            self.last_value = clip
        if self._stopped:
            return
        # Keep checking so links copied after launch are picked up too.
        self.root.after(self.poll_ms, self._poll)
