# LoopClip

*(Formerly Aquarium Downloader)*

A simple Windows app that downloads high-quality YouTube videos (aquariums,
aquascapes, fireplaces, rain, nature, or anything else) and automatically
trims and re-encodes them into ready-to-use looping video screensavers -
originally built for Projectivy Launcher on a 4K TV, but works for any
similar setup.

## Screenshot

![LoopClip Screenshot](screenshot.png)

## What's implemented

- Paste-a-URL, click-Add-to-Queue workflow with clipboard auto-detect
- **Queue-based** - add several videos, then start the queue and let them
  process one after another unattended
- Quality modes: Best Available / 4K HDR Preferred / 1080p Compatible
- Automatic trim, entered via three simple digit boxes (Hours / Minutes /
  Seconds) instead of free-text - no more "invalid time format" errors
- Stream-copy trimming (no re-encode / no quality loss) with automatic
  re-encode fallback if a codec can't be cut cleanly
- Re-encode fallback targets **H.265/HEVC**, forced **4K UHD (3840x2160)**
  output, **30fps**, and a **user-selectable target bitrate** (8 / 15 / 25 /
  40 Mbps)
- **GPU-accelerated** re-encoding via NVIDIA NVENC when available, with an
  automatic CPU (libx265) fallback if no compatible Nvidia GPU is present
- Adjustable output audio bitrate, or mute entirely
- "Clear URL" and "Clear Queue" buttons for quickly resetting either
- Settings remembered between runs (folder, quality, trim, bitrates,
  checkboxes)
- Per-item progress in the queue list: percent / speed / ETA, updated
  smoothly without flickering even with several items queued at once
- Friendly error messages for missing tools, bad URLs, network failures,
  and low disk space
- Dark, Windows-11-style UI (CustomTkinter)

**Not included** (can be added later): drag-and-drop, video library
manager, playlist/channel downloads.

## Requirements

- Windows 11
- Python 3.10+
- **FFmpeg** installed and on your system PATH (the app checks for this
  and will tell you clearly if it's missing) - download from
  https://ffmpeg.org/download.html
- An NVIDIA GPU is recommended for fast trimming/re-encoding (NVENC), but
  not required - the app automatically falls back to CPU encoding if one
  isn't available.

## Setup (development / running from source)

```powershell
cd LoopClip
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Building a standalone LoopClip.exe

Once everything runs correctly from source:

```powershell
pyinstaller --noconfirm --onefile --windowed ^
    --name LoopClip ^
    main.py
```

The finished executable will be in `dist\LoopClip.exe`. Users can run this
directly - they do not need Python installed. FFmpeg still needs to be
installed separately and on PATH (this is standard for yt-dlp-based tools
and keeps the .exe small; the app will show a clear message if it's
missing).

## Project structure

```
LoopClip/
├── main.py            # GUI + orchestration (CustomTkinter)
├── downloader.py       # yt-dlp wrapper: URL validation, format selection, progress
├── processor.py        # FFmpeg wrapper: trimming, re-encoding, filename building
├── queue_manager.py     # Owns the download queue and its background worker thread
├── settings.py         # Persisted settings (JSON in %APPDATA%\LoopClip)
├── tool_check.py        # Detects yt-dlp / FFmpeg availability
└── requirements.txt
```

## Notes on quality handling

- "Best Available" lets yt-dlp pick the actual best video+audio streams
  regardless of codec (AV1/VP9/H.264).
- Trimming uses `ffmpeg -c copy` (stream copy) first so no quality is lost
  and processing is nearly instant; it only falls back to a re-encode if
  the source codec doesn't allow a clean copy-cut at that timestamp.
- When a re-encode is needed, it targets H.265/HEVC at your chosen
  bitrate, always at full 4K UHD resolution regardless of the source
  resolution, and tries NVIDIA NVENC (GPU) first for speed before falling
  back to a CPU encode.

## A note on the rename

This project started life as **Aquarium Downloader**, aimed narrowly at
aquarium screensaver footage. Since it works equally well for any looping
background video, it's now **LoopClip**. If you're upgrading from an
older Aquarium Downloader install, your existing settings are carried
over automatically on first run - nothing to do manually.

## 📜 License

This project is released under the [MIT License](LICENSE).

You are free to use, modify, and share this software.
