"""
main.py
-------
Application entry point. Everything else lives in dedicated modules:

    ui.py               - the window and all user-facing widgets/dialogs
    queue_manager.py     - the live download queue (list ops, persistence)
    download_manager.py  - runs downloads concurrently against the queue
    downloader.py        - the yt-dlp wrapper itself
    models.py             - the DownloadItem/Status data model
    settings.py, history.py, queue_store.py, logging_setup.py, notifications.py,
    clipboard.py, thumbnail.py, tool_check.py, utils.py, version.py
                          - supporting, single-purpose modules

Architecture: UI -> Queue Manager -> Download Manager -> Downloader -> yt-dlp.
"""

import os
import sys
from pathlib import Path

# When packaged as an .exe, make bundled tools such as FFmpeg discoverable.
if getattr(sys, "frozen", False):
    os.environ["PATH"] = str(Path(sys._MEIPASS)) + os.pathsep + os.environ.get("PATH", "")

from ui import VideoDownloaderApp

if __name__ == "__main__":
    app = VideoDownloaderApp()
    app.mainloop()
