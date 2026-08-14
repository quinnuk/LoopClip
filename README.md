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

LoopClip is a Windows desktop application for turning YouTube videos — aquariums, aquascapes, fireplaces, rain, nature, or anything else — into ready-to-use looping video clips.

It can download a source, automatically find a good loop point, trim the video, optionally crossfade the loop point, and encode when necessary. It is designed with 4K screensavers and ambient-display setups in mind, including Projectivy Launcher, but the resulting videos can be used anywhere a normal video file is supported.

LoopClip can also process local video files without downloading anything.

## Features

### Core workflow

- Paste a YouTube URL and click **Add to Queue**.
- Automatically pick up YouTube URLs from the clipboard.
- **Drag and drop** a YouTube URL onto the application.
- **Drag and drop local video files** directly into the queue; local files skip the download step.
- Paste **playlist or channel URLs** and LoopClip expands them into individual queue items.
- Queue multiple videos and process them one at a time.
- Per-item progress, speed, ETA, and processing state.
- **Clear URL** and **Clear Queue** controls.
- **Retry** failed items without unnecessarily downloading the source again when a usable downloaded source is already available.

### Quality and encoding

- **Best Available** — lets yt-dlp choose the highest-quality video/audio streams available.
- **4K HDR Preferred** — favors an HDR-capable 4K stream when one is available.
- **1080p Compatible** — caps the selected download at 1080p for smaller files and broader compatibility.
- Stream-copy trimming is attempted first, avoiding a re-encode when the source allows a clean cut.
- When re-encoding is necessary, LoopClip uses H.265/HEVC at a selectable bitrate:
  - 8 Mbps
  - 15 Mbps
  - 25 Mbps
  - 40 Mbps
- Source resolution and frame rate are preserved where possible. Sources wider than 4K are downscaled to the 4K width limit rather than upscaling smaller sources.
- NVIDIA **NVENC** is tried automatically for supported H.265 encoding, with CPU/libx265 fallback when NVENC cannot be used.
- Audio can be kept at the original bitrate or re-encoded to:
  - 128 kbps
  - 192 kbps
  - 256 kbps
  - 320 kbps
- Audio can also be removed completely.

### Automatic seamless loop detection

LoopClip can search a video for two frames that make a good loop boundary, so you do not have to find the loop point manually.

Available comparison methods:

- **Combined** — combines structural and color matching; recommended for general use.
- **SSIM** — structural similarity; useful for relatively static footage.
- **Histogram** — color/lighting similarity; useful when subjects move but the overall scene remains similar.
- **Hash** — fast perceptual comparison; useful for quick tests and near-static footage.

Additional controls include:

- **Similarity %** — how close the candidate frames must be.
- **Analyze** — every frame, every 2nd, 4th, or 8th frame.
- **Limit search window** — restrict analysis to a From/To range.
- Optional minimum and maximum loop lengths.

### GPU-accelerated loop analysis

If a compatible NVIDIA/CUDA setup and CuPy are available, the GUI can optionally use GPU acceleration for the frame-comparison stage of loop analysis.

This is separate from NVENC video encoding.

The GPU analysis option is experimental and is disabled automatically when a compatible setup is not detected. CPU analysis remains the normal fallback.

For long searches, narrowing the search window can make a larger difference than GPU acceleration because video decoding itself can still be the slow part.

### Seamless crossfade

You can optionally blend the end of the clip into its beginning using:

- 1 second
- 2 seconds
- 3 seconds

Crossfade requires a re-encode. If crossfade creation fails but the underlying trim can still be produced, LoopClip falls back to a normal trimmed clip and reports that a workaround was used rather than losing the whole item.

### Built-in Help, About and Library

The GUI includes:

- **Help** — a scrollable explanation of the less-obvious settings.
- **About** — shows the LoopClip version, Python version, FFmpeg status/version, yt-dlp version, and NVENC status.
- **Library** — keeps a persistent record of completed videos, including their title, completion date, and output path.

From the Library you can:

- Open the folder containing a finished video.
- Remove an entry from the Library.

Removing a Library entry does **not** delete the actual video file.

### yt-dlp updates

LoopClip can check for a newer yt-dlp release automatically when it starts, and you can also check on demand at any time.

**Automatic check (on startup):** if an update is available, the GUI shows an inline notice offering:

- **Update Now**
- **Skip**

You can turn this automatic check off from the **About** dialog. If you skip a particular version, LoopClip remembers that exact version and will not repeatedly offer it; a newer version can still be offered later.

**Manual check (on demand):** click **Check for Updates** in the header, next to Help and About, to check right away. Unlike the automatic check, this always reports a result — "you're up to date," an update available with a **Download & Install** button, or that PyPI couldn't be reached — so you can confirm the update mechanism is working without waiting for a real new release.

Either way, updates are downloaded and applied in the background so the application remains usable, and the downloaded package is verified against its published checksum before it's installed. A successfully applied update takes effect after restarting LoopClip.

### Settings and quality of life

LoopClip remembers settings between runs, including:

- Output folder
- Recent output folders
- Download quality
- Video bitrate
- Audio bitrate
- Mute setting
- Loop detection settings
- Crossfade setting
- After-download behavior
- yt-dlp update preferences

After processing, you can choose to:

- Delete the original downloaded source file.
- Automatically open the output folder.

Settings from the old **Aquarium Downloader** application are migrated automatically when upgrading.

## Requirements

| Requirement | Notes |
|---|---|
| Windows 11 | Supported desktop platform |
| Python 3.10+ | Needed when running LoopClip from source |
| FFmpeg | Required; must be installed and available on `PATH` |
| NVIDIA GPU | Optional; enables NVENC encoding when supported |
| CuPy + compatible NVIDIA/CUDA setup | Optional; enables experimental GPU-accelerated loop analysis |

### Python dependencies

The runtime dependencies in `requirements.txt` are:

- `customtkinter`
- `yt-dlp`
- `pyperclip`
- `opencv-python-headless`
- `numpy`
- `scikit-image`

`scikit-image` enables the SSIM and Combined loop-detection methods. The loop detector can fall back to a simpler hash-based comparison when the optional image-processing capability is unavailable.

For development/testing, `requirements-dev.txt` adds `pytest`.

### FFmpeg

FFmpeg is not bundled into the application. It must be installed separately and available on the system `PATH`.

LoopClip checks for FFmpeg and reports when it is missing.

The About dialog also reports the detected FFmpeg version.

### NVIDIA / NVENC

An NVIDIA GPU is not required to use LoopClip.

When NVENC is unavailable or a real NVENC encode test fails, LoopClip falls back to CPU encoding.

The **About** dialog performs an actual small NVENC capability check and reports the result, which is more useful than simply checking whether an encoder name appears in FFmpeg.

## Download

The easiest way to use LoopClip is to download the latest pre-built release:

**[Download the latest release](https://github.com/quinnuk/LoopClip/releases)**

The standalone executable does not require Python to be installed.

FFmpeg is still required separately and must be available on `PATH`.

## Getting Started

### Run from source

```bash
cd LoopClip
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

If you already have the required packages installed globally, you can skip the virtual environment.

### Launch without a console

After installing the dependencies, either of these launchers can be used:

- **`LoopClip.vbs`** — silent launch.
- **`Run LoopClip.bat`** — silent launch.

You can also run:

```bash
python main.py
```

or, if you specifically want Python to run without a console window:

```bash
pythonw main.py
```

## How to Use

1. **Add a source.**
   - Paste a YouTube URL.
   - Copy a YouTube URL and let clipboard detection fill the URL box.
   - Drag a YouTube URL onto the window.
   - Drag one or more local video files onto the window.
   - Paste a playlist or channel URL to queue its individual videos.

2. **Choose your options.**
   - Output folder.
   - Download quality.
   - Video bitrate.
   - Audio bitrate or mute.
   - Automatic loop detection.
   - Loop method and similarity.
   - Analysis sampling.
   - Search window.
   - Minimum/maximum loop length.
   - Optional seamless crossfade.
   - Optional GPU-accelerated loop analysis.
   - After-download behavior.

3. **Click Add to Queue.**

   Each queued item captures the options selected when it is added. If you change an option after an item is already queued, re-add the item if you want the changed setting to apply.

4. **Click Start Queue.**

   Videos are processed one at a time. The queue shows the current state and progress.

5. **Check the result.**

   Finished files are placed in the selected output folder and recorded in the Library.

### Processing states

Queue items can move through states such as:

- **Queued**
- **Downloading**
- **Analyzing**
- **Trimming**
- **Creating Loop**
- **Finished**
- **Error**
- **Cancelled**

### Cancelling

**Cancel** stops the current processing operation and halts the remaining queue.

### Retrying

When an item fails after its source has already been downloaded successfully, **Retry** can reuse that downloaded source rather than downloading the same video again.

## Auto-Detect Seamless Loop

Auto-detect searches a selected portion of the source video for a suitable pair of matching frames and uses those points as the loop boundary.

### Comparison methods

| Method | Best suited to |
|---|---|
| `combined` | General-purpose use; combines structural and color information |
| `ssim` | Static or nearly static scenes where structure matters most |
| `histogram` | Moving subjects where overall color/lighting is more stable than exact positions |
| `hash` | Fast tests and near-static footage |

### Similarity

Higher similarity values require a closer match.

For relatively static footage, values around 95–98% or higher may be reasonable.

For moving footage — fish, foliage, water, people, etc. — a lower threshold can produce much more realistic results. Histogram comparison can also be preferable because it is less sensitive to an object's exact position.

If a search fails, the error reports the closest match it found. That value is useful when choosing the next similarity threshold.

### Analysis sampling

The GUI offers:

- Every frame
- Every 2nd frame
- Every 4th frame
- Every 8th frame

Every frame is the most thorough but can take longer.

### Search window

For long videos, limiting the search window is often the best way to speed up analysis.

Give the search enough room to find a match. A search window that is only barely larger than the desired loop length may leave too little room for the detector to find a useful pair.

## Command-Line Interface

`cli.py` provides the download → loop detection → encode workflow without the GUI.

The input can be:

- A YouTube URL, which is downloaded with yt-dlp.
- A path to an existing local video file.

### Examples

Automatic loop detection on a local file:

```bash
python cli.py input.mp4 output.mp4
```

YouTube URL with a custom similarity threshold and method:

```bash
python cli.py https://youtu.be/XXXXXXXXXXX output.mp4 --similarity 95 --method histogram
```

Analyze only part of a long video:

```bash
python cli.py input.mp4 output.mp4 --start 00:00:30 --stop 00:05:00 --downsample 4
```

Skip automatic loop detection:

```bash
python cli.py https://youtu.be/XXXXXXXXXXX output.mp4 --no-auto-loop
```

Show the CLI version:

```bash
python cli.py --version
```

Show all options:

```bash
python cli.py --help
```

### CLI options

| Option | Description |
|---|---|
| `input` | YouTube URL or local video path |
| `output` | Output video path |
| `--version` | Print the LoopClip version and exit |
| `--start` | Start of the loop-search window in `HH:MM:SS` |
| `--stop` | End of the loop-search window in `HH:MM:SS` |
| `--similarity` | Match threshold from 0–100; default `98` |
| `--method` | `combined`, `ssim`, `histogram`, or `hash` |
| `--downsample` | Analyze every Nth frame; default `1` |
| `--min-length` | Minimum loop length in seconds |
| `--max-length` | Maximum loop length in seconds |
| `--no-auto-loop` | Skip automatic loop detection and process the full source |
| `--crossfade` | Crossfade duration in seconds |
| `--quality` | `best`, `4k_hdr`, or `1080p` |
| `--video-bitrate` | H.265 re-encode target in Mbps; default `15` |
| `--audio-bitrate` | `original` or a numeric kbps value |
| `--no-audio` | Remove audio |
| `--gpu` / `--no-gpu` | Compatibility flag; CLI frame analysis is CPU-only, while encoding automatically tries NVENC first |
| `--verbose` | Print additional analysis progress |

All CLI arguments are validated before processing begins. Invalid times, ranges, thresholds, loop lengths, bitrate values, and output paths are rejected with a specific error message.

## Building a Standalone .exe

The easiest build method is:

```bash
build_exe.bat
```

The build script installs/updates the required build dependency when needed and runs the PyInstaller build.

You can also run PyInstaller directly:

```bash
python -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --name LoopClip main.py
```

Using `python -m PyInstaller` avoids depending on a separate `pyinstaller.exe` being on `PATH`.

If `python` is not recognized, try:

```bash
py -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --name LoopClip main.py
```

The resulting executable is produced as `LoopClip.exe`, with PyInstaller also creating its normal `build` and `dist` output.

The standalone executable does not require Python, but FFmpeg remains an external requirement.

## Project Structure

```text
LoopClip/
├── main.py                  # GUI, settings UI, queue orchestration, Help/About/Library
├── cli.py                   # Command-line download/analyze/encode workflow
├── downloader.py            # yt-dlp integration, URL handling, format selection and progress
├── processor.py             # FFmpeg trimming, encoding, crossfade and output verification
├── loop_detector.py         # Automatic loop detection and similarity methods
├── queue_manager.py         # Queue items, processing worker and state management
├── library.py               # Persistent completed-video Library
├── settings.py              # Persistent application settings
├── tool_check.py            # FFmpeg/yt-dlp checks and NVENC capability diagnostics
├── ytdlp_updater.py         # yt-dlp update checking, download, verification and installation
├── version.py               # Single source of truth for the LoopClip version
├── icon.ico                 # Application icon
├── screenshot.png           # README screenshot
├── LoopClip.spec            # PyInstaller configuration
├── build_exe.bat            # One-click executable build
├── LoopClip.vbs              # Silent launcher
├── Run LoopClip.bat         # Alternative silent launcher
├── cleanup.ps1               # Build/output cleanup helper
├── setup-issue-templates.ps1 # GitHub issue-template setup helper
├── requirements.txt         # Runtime dependencies
├── requirements-dev.txt     # Development/test dependencies
└── License.txt              # MIT license
```

## Data and Settings

LoopClip stores persistent application settings under the user's application-data area.

The settings include things such as:

- Output folder and recent folders
- Download quality
- Audio/video settings
- Loop detection preferences
- Crossfade preferences
- Post-download behavior
- yt-dlp update preferences

Library entries are stored separately from the queue so completed videos remain discoverable after the queue is cleared or the application is restarted.

## Quality Handling Notes

- **Best Available** lets yt-dlp select the highest-quality available video/audio streams rather than forcing a particular codec.
- LoopClip tries a stream-copy trim first, avoiding unnecessary quality loss and re-encoding.
- When a re-encode is required, H.265/HEVC is used with the selected video bitrate.
- NVENC is attempted automatically when available; CPU encoding is used as a fallback.
- Resolution is preserved up to the 4K width limit rather than upscaling smaller sources.
- Frame rate is not unnecessarily forced to a fixed value.
- Crossfade requires re-encoding because the transition is created with FFmpeg filtering.
- Output files are verified after processing so an FFmpeg success code alone is not treated as proof that the final file is usable.

## Error and Warning Messages

LoopClip reports specific errors at the queue-item level rather than hiding everything behind a generic failure.

Common examples include:

| Message / symptom | Meaning | What to try |
|---|---|---|
| **FFmpeg was not found** | FFmpeg is missing or unavailable on `PATH` | Install FFmpeg and make sure it can be run from PowerShell/Command Prompt |
| **No seamless loop found...** | Nothing met the selected similarity threshold | Lower Similarity %, try `histogram`, or widen the search window |
| **Leaves no room to search** | The requested loop/search limits leave too little usable source duration | Widen the search window or reduce the minimum loop length |
| **Private / unavailable / age-restricted** | yt-dlp cannot access the source normally | Try another video or use an account-supported source where applicable |
| **Not enough free disk space** | There is insufficient space for temporary/output files | Free disk space and retry |
| **NVENC unavailable** | NVIDIA encoding could not be used | LoopClip automatically falls back to CPU encoding |
| **Crossfade failed / finished with note** | The blended loop could not be created | The normal trimmed output may still be usable; retry if necessary |

The **About** dialog is especially useful when troubleshooting because it shows the installed versions of Python and yt-dlp, FFmpeg availability/version, and the result of the NVENC capability check.

## About the Rename

This project started life as **Aquarium Downloader**, aimed specifically at aquarium screensaver footage.

It became **LoopClip** because the workflow works equally well for aquariums, aquascapes, fireplaces, rain, nature scenes, ambient displays, and other looping background videos.

Existing Aquarium Downloader settings are migrated automatically when upgrading.

## Reporting Issues

Found a bug or have a suggestion?

**[Open an issue](https://github.com/quinnuk/LoopClip/issues/new/choose)**

When reporting a problem, include:

- LoopClip version
- What you were trying to process
- The relevant error/status message
- Your settings
- Whether the source was a YouTube URL or local file
- FFmpeg/yt-dlp/NVENC information from the About dialog when relevant

The more of this information you provide, the easier it is to reproduce and fix the problem.

## Support This Project

LoopClip is free and built in spare time.

If it saves you time, consider buying me a coffee:

<a href="https://buymeacoffee.com/quinnuk">
  <img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee">
</a>

## License

Released under the [MIT License](https://github.com/quinnuk/LoopClip/blob/master/License.txt).

You are free to use, modify, and share this software.
