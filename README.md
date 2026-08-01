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
- **Seamless loop crossfade** (optional) - blends the last couple of
  seconds of a clip into its first couple of seconds, so the loop point
  fades instead of jump-cutting when Projectivy repeats it. Adjustable
  crossfade length (1 / 2 / 3 sec).
- **GPU-accelerated** re-encoding via NVIDIA NVENC when available, with an
  automatic CPU (libx265) fallback if no compatible Nvidia GPU is present
- Adjustable output audio bitrate, or mute entirely
- "Clear URL" and "Clear Queue" buttons for quickly resetting either
- Settings remembered between runs (folder, quality, trim, bitrates,
  loop settings, checkboxes) - and automatically migrated forward if
  you're upgrading from the old Aquarium Downloader name
- Per-item progress in the queue list: percent / speed / ETA, updated
  smoothly without flickering even with several items queued at once
- Friendly error messages for missing tools, bad URLs, network failures,
  and low disk space
- Dark, Windows-11-style UI (CustomTkinter)

**Not included** (can be added later): drag-and-drop, video library
manager, playlist/channel downloads.

## How to use it

1. **Paste a YouTube URL** into the box at the top - or just copy one
   from your browser, LoopClip will detect it and fill the box in
   automatically.
2. **Set your options** - Output Folder, Download Quality, Video/Audio
   Bitrate, and (optionally) an automatic Trim with a Start time and
   Duration. Turn on **Seamless loop crossfade** if you want the clip to
   fade into itself at the loop point rather than jump-cutting.
3. Click **Add to Queue**. Repeat for as many videos as you like - each
   one remembers whatever options were set at the moment you added it.
4. Click **Start Queue**. Videos download and process one at a time;
   watch progress in the list below. **Cancel** stops the current item
   immediately and halts the rest of the queue.
5. Finished files land in your chosen Output Folder, ready to point
   Projectivy (or any other screensaver/wallpaper tool) at.

**Trimming tip:** each time box is Hours / Minutes / Seconds, in that
order - type digits and it auto-advances to the next box. A start time of
`00:03:00` and a duration of `00:05:00` gives you a 5-minute clip starting
3 minutes into the source video.

## Requirements

- Windows 11
- Python 3.10+
- **FFmpeg** installed and on your system PATH (the app checks for this
  and will tell you clearly if it's missing) - download from
  https://ffmpeg.org/download.html
- An NVIDIA GPU is recommended for fast trimming/re-encoding (NVENC), but
  not required - the app automatically falls back to CPU encoding if one
  isn't available. CPU encoding at forced 4K resolution can be
  significantly slower, especially with the seamless loop feature
  enabled (it always re-encodes, since crossfading can't be done as a
  stream copy).

## Setup (development / running from source)

If you're using a Python install that already has PyInstaller and other
packages available globally (no virtual environment), you can skip
straight to `pip install -r requirements.txt` and `python main.py`.
Using a virtual environment is optional but recommended if you want to
keep this project's dependencies separate from other Python projects:

```powershell
cd LoopClip
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### Running without building an exe

Once dependencies are installed, any of these will start the app:

- Double-click **LoopClip.vbs** (silent launch, no console window)
- Double-click **Run LoopClip.bat** (also silent)
- From a terminal: `python main.py` (or `pythonw main.py` for no console
  window)

## Building a standalone LoopClip.exe

Once everything runs correctly from source:

```powershell
python -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --name LoopClip main.py
```

(`python -m PyInstaller` avoids relying on a separate `pyinstaller.exe`
being on your PATH - useful if the bare `pyinstaller` command isn't
recognized. If `python` itself isn't recognized either, try `py -m
PyInstaller ...` instead.)

The finished executable will be at `LoopClip.exe` (PyInstaller also
leaves a copy in `dist\LoopClip.exe` - either works, they're identical;
most people just use the one in the project root and can delete the
`dist`/`build` folders afterward). Users can run this directly - they do
not need Python installed. FFmpeg still needs to be installed separately
and on PATH (this is standard for yt-dlp-based tools and keeps the .exe
small; the app will show a clear message if it's missing).

## Project structure

```
LoopClip/
├── main.py              # GUI + orchestration (CustomTkinter)
├── downloader.py         # yt-dlp wrapper: URL validation, format selection, progress
├── processor.py          # FFmpeg wrapper: trimming, re-encoding, seamless loop crossfade
├── queue_manager.py      # Owns the download queue and its background worker thread
├── settings.py           # Persisted settings (JSON in %APPDATA%\LoopClip)
├── tool_check.py         # Detects yt-dlp / FFmpeg availability
├── LoopClip.vbs          # Silent launcher (no console window)
├── Run LoopClip.bat      # Alternative silent launcher
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
- The seamless loop crossfade step, if enabled, always re-encodes (it's
  not possible as a stream copy), using the same H.265/NVENC-first/4K
  approach as above, and runs as a final pass after trimming.

## A note on the rename

This project started life as **Aquarium Downloader**, aimed narrowly at
aquarium screensaver footage. Since it works equally well for any looping
background video, it's now **LoopClip**. If you're upgrading from an
older Aquarium Downloader install, your existing settings are carried
over automatically on first run - nothing to do manually.

## 📜 License

This project is released under the [MIT License](License.txt).

You are free to use, modify, and share this software.