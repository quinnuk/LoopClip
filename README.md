<div align="center">

# LoopClip

**Download YouTube videos and turn them into seamless, looping 4K screensavers — automatically.**



![Platform](https://img.shields.io/badge/platform-Windows%2011-0078D6)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

[![LoopClip Screenshot](https://github.com/quinnuk/LoopClip/raw/master/screenshot.png)](/quinnuk/LoopClip/blob/master/screenshot.png)

<a href="https://buymeacoffee.com/quinnuk" target="_blank">
  <img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="60" width="217">
</a>

<sub>If LoopClip saves you time, a coffee is always appreciated ☕</sub>

</div>

---

## Overview

LoopClip is a Windows desktop app that downloads high-quality YouTube videos — aquariums, aquascapes, fireplaces, rain, nature, or anything else — and automatically trims and re-encodes them into ready-to-use looping video screensavers.

It was originally built for **Projectivy Launcher** on a 4K TV, but works for any similar screensaver or ambient-display setup.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Getting Started](#getting-started)
- [How to Use](#how-to-use)
- [Auto-Detect Seamless Loop](#auto-detect-seamless-loop)
- [Command-Line Interface](#command-line-interface)
- [Building a Standalone .exe](#building-a-standalone-exe)
- [Project Structure](#project-structure)
- [Quality Handling Notes](#quality-handling-notes)
- [About the Rename](#about-the-rename)
- [Support This Project](#support-this-project)
- [License](#license)

## Features

**Core workflow**
- Paste a URL, click **Add to Queue** — with clipboard auto-detect for YouTube links
- Queue-based downloads — add several videos, then let them process unattended
- Per-item progress (percent / speed / ETA) that updates smoothly, even with a full queue
- **Clear URL** and **Clear Queue** buttons for quickly resetting either

**Quality & encoding**
- Quality modes: Best Available, 4K HDR Preferred, or 1080p Compatible
- Stream-copy trimming (no re-encode, no quality loss), with automatic re-encode fallback if a codec can't be cut cleanly
- Re-encode fallback targets H.265/HEVC, forced 4K UHD (3840×2160), 30fps, with a user-selectable bitrate (8 / 15 / 25 / 40 Mbps)
- GPU-accelerated encoding via NVIDIA NVENC when available, with automatic CPU (libx265) fallback

**Looping & trimming**
- Trim by entering Hours / Minutes / Seconds in simple digit boxes — no free-text time formats to get wrong
- **Auto-detect seamless loop** — analyzes a video (or a window within it) and automatically finds the best-matching loop point for you. See [Auto-Detect Seamless Loop](#auto-detect-seamless-loop) below.
- Optional **seamless loop crossfade** that blends a clip's end into its start, with adjustable length (1 / 2 / 3 sec)

**Command line**
- Full CLI (`cli.py`) for scripting/automation — download-or-local-file input, auto-detect loop, crossfade, and encode, all from one command with no GUI required. See [Command-Line Interface](#command-line-interface).

**Quality of life**
- Adjustable output audio bitrate, or mute entirely
- Settings remembered between runs, and auto-migrated if upgrading from Aquarium Downloader
- Friendly error messages for missing tools, bad URLs, network failures, and low disk space
- Dark, Windows 11–style UI (CustomTkinter)

> **Not yet included:** drag-and-drop, a video library manager, playlist/channel downloads, GPU-accelerated frame analysis for auto-detect (currently CPU-only — see [Requirements](#requirements)).

## Requirements

| Requirement | Notes |
|---|---|
| Windows 11 | |
| Python 3.10+ | Only needed if running from source |
| [FFmpeg](https://ffmpeg.org/download.html) | Must be installed and on your system PATH — the app checks and will tell you if it's missing |
| NVIDIA GPU (optional) | Enables fast NVENC encoding; falls back to CPU automatically if unavailable. CPU encoding at forced 4K can be significantly slower, especially with seamless loop enabled |
| — | Auto-detect seamless loop's frame analysis currently runs on CPU only. Long search windows analyzed at "Every frame" can take a while on slower machines — narrowing the search window helps. GPU-accelerated analysis may come in a future update |

## Getting Started

### Setup (development / running from source)

```bash
cd LoopClip
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

If your Python install already has the required packages available globally, you can skip the virtual environment and go straight to `pip install -r requirements.txt` followed by `python main.py`.

### Running without building an .exe

Once dependencies are installed, any of these will start the app:

- Double-click **`LoopClip.vbs`** (silent launch, no console window)
- Double-click **`Run LoopClip.bat`** (also silent)
- From a terminal: `python main.py` (or `pythonw main.py` for no console window)

## How to Use

1. **Paste a YouTube URL** into the box at the top, or just copy one from your browser — LoopClip detects it and fills the box in automatically.
2. **Set your options** — output folder, download quality, video/audio bitrate, and optionally an automatic trim (start time + duration). Turn on **seamless loop crossfade** if you want the clip to fade into itself at the loop point, and **auto-detect seamless loop** if you want LoopClip to find that loop point for you.
3. Click **Add to Queue**. Repeat for as many videos as you like — each one remembers whatever options were set at the moment it was added. If you change a setting (e.g. Similarity %) after an item is already queued, re-add it rather than relying on **Retry** to pick up the change.
4. Click **Start Queue**. Videos download and process one at a time; watch progress in the list below. **Cancel** stops the current item immediately and halts the rest of the queue.
5. Finished files land in your chosen output folder, ready to point Projectivy (or any other screensaver/wallpaper tool) at.

> **Trimming tip:** each time box is Hours / Minutes / Seconds, in that order — type digits and it auto-advances to the next box. A start time of `00:03:00` with a duration of `00:05:00` gives you a 5-minute clip starting 3 minutes into the source video.

## Auto-Detect Seamless Loop

Turning on **Auto-detect seamless loop** tells LoopClip to search a window of the source video for the two frames that look most alike, and use that as the loop's start/end point — rather than you having to find one by eye.

**Options**

| Option | What it does |
|---|---|
| **Method** — `combined` (default) | Blend of structural similarity (SSIM) and color/lighting (histogram). Good general-purpose choice. |
| **Method** — `ssim` | Structural similarity only. Stricter about exact positioning/shapes matching; best for otherwise-static scenes (locked-off camera, minimal movement). |
| **Method** — `histogram` | Color/lighting only. More forgiving of things that have *moved* between frames, as long as the overall look stays consistent — a better fit for footage with subjects that never sit perfectly still (fish, foliage in wind, people). |
| **Method** — `hash` | Fast, rough perceptual comparison. Least precise; useful mainly for quick tests or near-static footage. |
| **Similarity %** | How close a match has to be to count as a loop point. Higher = stricter = fewer (but better) candidates. |
| **Analyze** | How many frames get examined — "Every frame" is most thorough but slowest to extract; skipping frames is faster but can miss a slightly better match a frame or two away. |
| **Limit search window** | Restricts analysis to a From/To range instead of the whole video. Recommended for long source videos — faster, and lets you point the search at a section you already know looks good. |

**Tips for getting good results**

- **Give the search window real slack.** Don't set it equal to (or barely more than) the loop length you want — LoopClip needs room to actually search for a match. Aim for at least 30–60 extra seconds beyond your target minimum loop length.
- **Read a failed search's error message before changing anything blind.** It reports the closest match actually found (e.g. *"closest match was 97.2% similar"*), which tells you almost exactly where to set Similarity % next time.
- **Moving subjects (fish, water, foliage, people) will rarely hit a very high similarity score — that's normal.** For this kind of footage:
  - Start with a noticeably lower Similarity % (try ~85–90 rather than inching down from 98)
  - Try `histogram` instead of `combined` — it won't penalize a moving subject being in a slightly different position as harshly
  - Lengthen the seamless loop crossfade to 2–3 seconds; it hides a slightly imperfect match better than chasing a higher similarity score does
- **Calmer/static footage** (landscapes, fireplaces, slow color-cycling scenes) can comfortably use a high Similarity % (95–98+), since there's far less movement to prevent a near-perfect frame match.
- If you're unsure what's realistic for a clip, run it once with a middling Similarity % (~92) to see the actual best match, then adjust from there.

## Command-Line Interface

For scripting, automation, or anyone who'd rather skip the GUI entirely, `cli.py` exposes the same download → auto-detect loop → encode pipeline as a single command. It accepts either a YouTube URL (downloaded via yt-dlp first) or a path to an existing local video file.

```bash
# Automatic loop detection on a local file
python cli.py input.mp4 output.mp4

# From a YouTube URL, with a custom similarity threshold and method
python cli.py https://youtu.be/XXXXXXXXXXX output.mp4 --similarity 95 --method histogram

# Only analyze a specific window of a long video
python cli.py input.mp4 output.mp4 --start 00:00:30 --stop 00:05:00 --downsample 4

# Skip auto-detection entirely - just download/re-encode as-is
python cli.py https://youtu.be/XXXXXXXXXXX output.mp4 --no-auto-loop
```

**Options**

| Flag | Description |
|---|---|
| `input` | YouTube URL or path to a local video file |
| `output` | Path for the output video file |
| `--start`, `--stop` | Search window for loop detection (`HH:MM:SS`); default is the full video |
| `--similarity` | Match threshold, 0–100 (default: `98`) |
| `--method` | Comparison method: `combined`, `ssim`, `histogram`, or `hash` (default: `combined`) |
| `--downsample` | Analyze every Nth frame — higher is faster but less precise (default: `1`) |
| `--min-length`, `--max-length` | Minimum/maximum loop length in seconds |
| `--no-auto-loop` | Skip loop detection entirely; just download/re-encode the source as-is |
| `--crossfade` | Seconds of crossfade to blend at the loop point (omit for a hard cut) |
| `--quality` | Download quality mode: `best`, `4k_hdr`, or `1080p` (default: `best`) |
| `--video-bitrate` | Target video bitrate in Mbps for re-encodes (default: `15`) |
| `--audio-bitrate` | Audio bitrate: `original` or a kbps value (default: `original`) |
| `--no-audio` | Strip audio from the output |
| `--verbose` | Print extra progress detail during frame analysis |

Run `python cli.py --help` at any time to see this listed directly from the tool.

## Building a Standalone .exe

Once everything runs correctly from source, the easiest way is to just run:

```bash
build_exe.bat
```

This installs/updates the dependencies (including PyInstaller if it's missing), builds the exe, and tells you clearly whether it succeeded.

If you'd rather run the build command yourself:

```bash
python -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --name LoopClip main.py
```

`python -m PyInstaller` avoids relying on a separate `pyinstaller.exe` being on your PATH. If `python` isn't recognized either, try `py -m PyInstaller ...` instead.

The finished executable lands at `LoopClip.exe` in the project root (PyInstaller also leaves an identical copy in `dist\LoopClip.exe` — you can delete the `dist`/`build` folders afterward). Users can run the .exe directly without installing Python. FFmpeg still needs to be installed separately and on PATH — this is standard for yt-dlp-based tools and keeps the .exe small; the app shows a clear message if it's missing.

## Project Structure

```
LoopClip/
├── main.py                  # GUI + orchestration (CustomTkinter)
├── cli.py                    # Command-line interface: download/analyze/encode without the GUI
├── downloader.py             # yt-dlp wrapper: URL validation, format selection, progress
├── processor.py              # FFmpeg wrapper: trimming, re-encoding, seamless loop crossfade
├── loop_detector.py          # Auto-detect seamless loop: frame extraction + similarity search
├── queue_manager.py          # Owns the download queue and its background worker thread
├── settings.py               # Persisted settings (JSON in %APPDATA%\LoopClip)
├── tool_check.py              # Detects yt-dlp / FFmpeg availability
├── LoopClip.vbs               # Silent launcher (no console window)
├── Run LoopClip.bat           # Alternative silent launcher
├── build_exe.bat              # One-click rebuild of LoopClip.exe
└── requirements.txt
```

## Quality Handling Notes

- **Best Available** lets yt-dlp pick the actual best video+audio streams regardless of codec (AV1/VP9/H.264).
- Trimming uses `ffmpeg -c copy` (stream copy) first, so no quality is lost and processing is nearly instant. It only falls back to a re-encode if the source codec doesn't allow a clean copy-cut at that timestamp.
- When a re-encode is needed, it targets H.265/HEVC at your chosen bitrate, always at full 4K UHD resolution regardless of source resolution, and tries NVIDIA NVENC (GPU) first before falling back to CPU.
- The seamless loop crossfade step, if enabled, always re-encodes (it can't be done as a stream copy), using the same H.265/NVENC-first/4K approach, and runs as a final pass after trimming.

## About the Rename

This project started life as **Aquarium Downloader**, aimed narrowly at aquarium screensaver footage. Since it works equally well for any looping background video, it's now **LoopClip**. If you're upgrading from an older Aquarium Downloader install, your existing settings carry over automatically on first run — nothing to do manually.

## Support This Project

LoopClip is free and built in my spare time — if it's useful to you, consider buying me a coffee.

<p align="center">
  <a href="https://buymeacoffee.com/quinnuk">
    <img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee">
  </a>
</p>

## License

Released under the [MIT License](https://github.com/quinnuk/LoopClip/blob/master/License.txt). You are free to use, modify, and share this software.
