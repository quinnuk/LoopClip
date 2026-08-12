<div align="center">

# 🎧 Atmos Track Splitter

**Split a ripped Blu-ray concert/music disc into individual, chapter-named Dolby Atmos song files — without ever re-encoding the audio.**

![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

[![Atmos Track Splitter Screenshot](https://github.com/quinnuk/AtmosTrackSplitter/raw/main/screenshot.png)](https://github.com/quinnuk/AtmosTrackSplitter/blob/main/screenshot.png)

<a href="https://buymeacoffee.com/quinnuk" target="_blank">
  <img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="60" width="217">
</a>

<sub>If Atmos Track Splitter saves you time, a coffee is always appreciated ☕</sub>

</div>

---

## Table of Contents

- [Why this exists](#why-this-exists)
- [Features](#features)
- [How it works](#how-it-works)
- [Download](#download)
- [Requirements](#requirements)
- [Usage](#usage)
- [Building a standalone .exe](#building-a-standalone-exe)
- [Project layout](#project-layout)
- [Notes & known limitations](#notes--known-limitations)
- [Roadmap](#roadmap)
- [Reporting Issues](#reporting-issues)
- [Support This Project](#support-this-project)
- [License](#license)

## Why this exists

Concert Blu-rays with a Dolby Atmos mix are usually one giant file with chapter markers per song. Most tools either:
- flatten/transcode the audio (losing the Atmos object metadata), or
- give you the whole disc as one file with no easy way to split it, or
- require you to hand-build `mkvmerge`/`ffmpeg` commands per chapter.

Atmos Track Splitter automates the whole thing: point it at the ripped disc folder, find the Atmos playlist, type in the track names, and get one clean, bit-exact file per song.

## Features

- 🏆 **Scores every playlist as a candidate** — instead of silently guessing which `.mpls` is "the" concert, it ranks every playlist by Atmos presence, chapter count, duration, and video track, and shows the reasoning so you can confirm (or override) the pick
- 🔁 **Flags likely duplicates** — playlists that look like the same content re-packaged (matching duration/chapters/tracks — alternate angles, region variants, etc.) are marked so you're not choosing between near-identical entries
- 🏷️ **Reads embedded chapter names** — prefills track name fields straight from the disc's own chapter titles when the disc has them, instead of starting from blank
- ⚠️ **Checks for MKVToolNix/ffmpeg on startup** — if anything's missing, a dialog lets you **Download** it or **Locate...** an existing install and save the path — no PATH editing or manual config required
- ✂️ **No re-encoding** — stream-copies video + Atmos audio only; the object-based Atmos mix stays bit-exact
- 📺 **Keeps the video track** — so playback on a TV shows the concert, not a black screen
- 📝 **Fast track naming** — paste a whole tracklist at once and it fills every chapter field in order (strips leading `1.`, `01 -`, etc.)
- 🛡️ **Never overwrites silently** — checks for existing output files before extraction starts and asks before overwriting anything
- 🧯 **Crash-safe writes** — each track is written to a temp file and only renamed into place once it's fully written, so an interrupted run never leaves a truncated file sitting at the real track name
- 📁 **Clean output** — creates a sensibly-named album folder and one file per song, named from your chapter titles
- 💾 **Remembers your settings** — last-used folders and tool paths persist between runs
- 🪟 **Runs standalone** — build a windowed `.exe` with no attached console, so closing the terminal (or just double-clicking it) never kills a mid-extraction job

## How it works

1. Point the app at a folder containing a ripped disc — it must have the standard, unmodified `BDMV/PLAYLIST/*.mpls` structure (not a flattened single MKV).
2. It inspects every playlist and scores each one as a candidate for the concert feature — Atmos track presence, chapter count, duration, video track — auto-selecting the best match while showing the reasoning behind it, and flagging any that look like duplicate/alternate-angle playlists of another candidate.
3. You review the track names — prefilled automatically from the disc's own embedded chapter titles where available — and type or paste in the rest.
4. Before touching anything slow, it checks whether any planned output file already exists and asks before overwriting. It then extracts the video track plus just the Atmos audio (stream-copy, no transcoding) via `mkvmerge`, dropping the other audio tracks (AC-3, DTS-HD, etc). It reads the exact chapter timestamps from the extracted file and splits it into one file per song with `ffmpeg`, writing each to a temp file first so a crash mid-split never leaves a corrupt file at the real track name.

## Download

Prebuilt Windows `.exe`s are attached to each [release](https://github.com/quinnuk/AtmosTrackSplitter/releases) — grab the latest one if you don't want to build from source. You'll still need MKVToolNix and ffmpeg installed separately (see [Requirements](#requirements) below).

> The exe isn't code-signed, so Windows SmartScreen may show a "Windows protected your PC" warning the first time you run it — this is normal for small unsigned tools. Click **More info** → **Run anyway**.

## Requirements

**External tools** (must be installed and either on your `PATH`, or located via the app's startup **Missing required tools** dialog — click **Locate...** to point it at an existing install without editing `PATH` or config files by hand):

| Tool | Provides | Link |
|---|---|---|
| MKVToolNix | `mkvmerge`, `mkvextract` | https://mkvtoolnix.download/ |
| ffmpeg | `ffmpeg`, `ffprobe` | https://ffmpeg.org/ |

**Python packages:**

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

> **Note:** if you launch it from PowerShell/cmd, the app is a child process of that terminal — closing the terminal window kills the app too, even mid-extraction. Run it with `pythonw main.py` (fully detached, no console) instead, or build the standalone `.exe` below.

## Building a standalone .exe

```bash
pip install pyinstaller
build_exe.bat
```

This produces `AtmosTrackSplitter.exe` — a windowed exe with no attached console, so it survives closing the terminal it was launched from (or just double-click it directly).

> The exe bundles the Python app itself, but **not** `mkvmerge`, `mkvextract`, `ffmpeg`, or `ffprobe` — those still need to be installed separately, either on `PATH` or located via the app's **Locate...** button (see [Requirements](#requirements)).

## Project layout

```
AtmosTrackSplitter/
├── main.py                    GUI (customtkinter)
├── extractor.py                Core logic: scanning, extraction, chapters, splitting
├── settings.py                 Persisted settings (tool paths, last-used folders)
├── split_now.py                 CLI: split an already-extracted Atmos MKV by hand
├── AtmosTrackSplitter.spec     PyInstaller build spec
├── build_exe.bat               One-click .exe build script
└── requirements.txt
```

`extractor.py` has no GUI dependencies, so it can be imported and used on its own — either via `split_now.py` or your own scripts.

### Recovering from an interrupted run

If extraction succeeds but the app is closed or crashes before splitting finishes, the intermediate `_atmos_extracted.mkv` is left in the work folder instead of being cleaned up. Rather than re-running the (slow) extraction step again, split that file directly:

```bash
python split_now.py "path\to\_atmos_extracted.mkv" "path\to\output folder" --names-file tracks.txt
```

`tracks.txt` is just one track name per line, in chapter order — the same tracklist you'd paste into the GUI. Leading numbering (`1.`, `01 -`) is stripped automatically. Add `--list-only` (and skip `--names-file`) to just see how many chapters a file has before committing to names.

## Notes & known limitations

- Assumes chapters map 1:1 to songs. This holds for most concert/live-album Blu-rays, but always sanity-check the chapter count against the actual tracklist before running the paste-to-fill step.
- If a disc has no Atmos track, or the Atmos track isn't chaptered per song, this tool won't help — it depends on both being true.
- Output format defaults to `.mkv` (TrueHD Atmos in an MKV container) to preserve the object-based Atmos metadata — converting to a plain audio container like FLAC would discard it.
- Folder-in, folder-out only — no CLI or disc-drive/ripping support. This tool works on discs you've **already ripped yourself**; it doesn't rip or decrypt anything.

## Roadmap

- [ ] Folder-watch mode (auto-detect new rips dropped into a monitored folder and prompt for naming)
- [ ] Standalone settings screen to review/change tool paths anytime — currently the **Locate...** option only surfaces when the startup check finds something missing
- [ ] MusicBrainz lookup as a fallback for track names

## Reporting Issues

Found a bug or have a suggestion? Please open an issue rather than a Reddit comment, so it doesn't get lost:

**[Open an issue](https://github.com/quinnuk/AtmosTrackSplitter/issues/new/choose)**

A bug report with `mkvmerge -J` output for the affected playlist is the most useful thing you can include — it's normally enough to track down what's actually going wrong.

## Support This Project

Atmos Track Splitter is free and built in my spare time — if it's useful to you, consider buying me a coffee.

<p align="center">
  <a href="https://buymeacoffee.com/quinnuk">
    <img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee">
  </a>
</p>

## License

Released under the [MIT License](LICENSE). You are free to use, modify, and share this software.
