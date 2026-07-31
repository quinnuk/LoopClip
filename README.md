# Aquarium Downloader

A simple Windows app that downloads high-quality YouTube videos (aquariums,
aquascapes, fireplaces, rain, nature) and automatically trims them into
ready-to-use video screensavers for Projectivy Launcher on a 4K TV.

## Screenshot

![Aquarium Downloader Screenshot](screenshot.png)

## What's implemented (v1)

- Paste-a-URL, click-Download workflow with clipboard auto-detect
- Quality modes: Best Available / 4K HDR Preferred / 1080p Compatibility
- Automatic trim (default: skip first 5 min, keep next 15 min)
- Stream-copy trimming (no re-encode / no quality loss) with automatic
  re-encode fallback if a codec can't be cut cleanly
- Settings remembered between runs (folder, quality, trim, checkboxes)
- Progress bar with percent / speed / ETA
- Friendly error messages for missing tools, bad URLs, network failures,
  and low disk space
- Dark, Windows-11-style UI (CustomTkinter)

**Not included in v1** (marked "Future feature" in the spec, can be added
later): drag-and-drop, video library manager, batch URL downloads,
playlist/channel downloads.

## Requirements

- Windows 11
- Python 3.10+
- **FFmpeg** installed and on your system PATH (the app checks for this
  and will tell you clearly if it's missing) - download from
  https://ffmpeg.org/download.html

## Setup (development / running from source)

```powershell
cd AquariumDownloader
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Building a standalone AquariumDownloader.exe

Once everything runs correctly from source:

```powershell
pyinstaller --noconfirm --onefile --windowed ^
    --name AquariumDownloader ^
    main.py
```

The finished executable will be in `dist\AquariumDownloader.exe`. Users can
run this directly — they do not need Python installed. FFmpeg still needs
to be installed separately and on PATH (this is standard for yt-dlp-based
tools and keeps the .exe small; the app will show a clear message if it's
missing).

## Project structure

```
AquariumDownloader/
├── main.py            # GUI + orchestration (CustomTkinter)
├── downloader.py       # yt-dlp wrapper: URL validation, format selection, progress
├── processor.py        # FFmpeg wrapper: trimming, filename building
├── settings.py         # Persisted settings (JSON in %APPDATA%)
├── tool_check.py        # Detects yt-dlp / FFmpeg availability
└── requirements.txt
```

## Notes on quality handling

- "Best Available" lets yt-dlp pick the actual best video+audio streams
  regardless of codec (AV1/VP9/H.264), matching the "no unnecessary
  re-encoding, maximum quality retained" goal in the spec.
- Trimming uses `ffmpeg -c copy` (stream copy) first so no quality is lost
  and processing is nearly instant; it only falls back to a slower
  re-encode if the source codec doesn't allow a clean copy-cut at that
  timestamp.
