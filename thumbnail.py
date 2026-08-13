"""
thumbnail.py
------------
Loads video/playlist preview info and thumbnails on background threads.

Every preview request is tagged with an ID; if a newer request has started
by the time an older one's background thread finishes, the older result is
dropped rather than delivered - this is what fixes the classic bug where
"preview A, immediately preview B" can let A's slow thumbnail land on B.
"""

import threading
from io import BytesIO
from urllib.request import urlopen

import downloader

try:
    from PIL import Image
except ImportError:
    Image = None


class PreviewLoader:
    def __init__(self, root):
        self.root = root
        self._current_id = 0

    def request(self, url: str, on_success, on_error, cookies_from_browser: str = "none") -> None:
        """Start a new preview lookup. Any in-flight older request is
        implicitly superseded - its result will be dropped when it arrives.
        """
        self._current_id += 1
        request_id = self._current_id
        threading.Thread(
            target=self._load, args=(url, request_id, on_success, on_error, cookies_from_browser), daemon=True,
        ).start()

    def load_thumbnail(self, thumbnail_url: str, on_loaded) -> None:
        """Fetch a thumbnail for whichever preview request is currently
        active. `on_loaded` receives a PIL Image; the caller decides how to
        turn that into a displayable widget image.
        """
        if Image is None:
            return
        request_id = self._current_id
        threading.Thread(
            target=self._load_thumbnail, args=(thumbnail_url, request_id, on_loaded), daemon=True,
        ).start()

    def _load(self, url: str, request_id: int, on_success, on_error, cookies_from_browser: str = "none") -> None:
        try:
            info = downloader.get_video_info(url, cookies_from_browser=cookies_from_browser)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._deliver, request_id, on_error, exc)
            return
        self.root.after(0, self._deliver, request_id, on_success, info)

    def _load_thumbnail(self, thumbnail_url: str, request_id: int, on_loaded) -> None:
        try:
            image = Image.open(BytesIO(urlopen(thumbnail_url, timeout=10).read())).convert("RGBA")
        except Exception:  # noqa: BLE001 - a missing thumbnail is never worth surfacing an error for
            return
        self.root.after(0, self._deliver, request_id, on_loaded, image)

    def _deliver(self, request_id: int, callback, payload) -> None:
        if request_id != self._current_id:
            return  # superseded by a newer preview request
        callback(payload)
