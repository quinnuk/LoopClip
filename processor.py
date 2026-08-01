"""
processor.py
------------
Wraps FFmpeg to trim the downloaded video and/or re-encode its audio track
into the final output file. Stream-copies whenever possible (no quality
loss, no re-encoding) and only falls back to re-encoding the video when a
stream copy can't cut cleanly at the requested trim point.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

# Same rationale as in downloader.py - lets FFmpeg's child process start
# reliably even when the app itself has no console (pythonw.exe).
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

AUDIO_BITRATES = ["original", "128", "192", "256", "320"]


class CancelledError(Exception):
    """Raised when an FFmpeg operation is aborted because of a user Cancel."""


_HMS_RE = re.compile(r"^(\d{1,2}):([0-5]?\d):([0-5]?\d)$")


def validate_hms(value: str) -> bool:
    """
    Returns True if `value` is a valid HH:MM:SS duration/timestamp
    (minutes and seconds must be 00-59; hours can be any non-negative
    number of digits).
    """
    if value is None:
        return False
    return bool(_HMS_RE.match(value.strip()))


def _hms_to_seconds(hms: str) -> int:
    """Converts 'HH:MM:SS' to total seconds."""
    parts = [int(p) for p in hms.strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def sanitize_title(title: str) -> str:
    """Removes characters that are illegal in Windows filenames."""
    return re.sub(r'[\\/:*?"<>|]', "", title).strip()


def build_output_filename(title: str, duration_hms: str) -> str:
    """
    Builds the final output filename, e.g.:
    'Wide Fantasy Valley Aquascape 3hours HDR.mp4'
      -> 'Wide Fantasy Valley Aquascape 3hours HDR - 15min.mp4'
    """
    clean_title = sanitize_title(title)
    minutes = _hms_to_seconds(duration_hms) // 60
    return f"{clean_title} - {minutes}min.mp4"


def _audio_args(audio_bitrate: str, no_audio: bool) -> list:
    if no_audio:
        return []
    if audio_bitrate == "original":
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", f"{audio_bitrate}k"]


def process_video(
    input_path: str,
    output_path: str,
    trim_enabled: bool,
    start_hms: str,
    duration_hms: str,
    audio_bitrate: str,
    video_bitrate: str,
    no_audio: bool,
    process_holder: Optional[dict] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    """
    Produces the final output file from input_path, applying trimming
    (if trim_enabled) and/or an audio re-encode (if audio_bitrate is not
    "original" and no_audio is False) in a single FFmpeg pass.

    Tries a fast stream copy of the video track first. If that fails (some
    codecs don't allow copy at arbitrary cut points), falls back to a
    re-encode of the video with high-quality settings. The audio track is
    only ever re-encoded if the user asked for a specific bitrate.

    video_bitrate is a plain string of Mbps, e.g. "15" - only used by the
    re-encode fallback path (stream copy has no bitrate to control).
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    audio_args = _audio_args(audio_bitrate, no_audio)
    vbitrate = f"{video_bitrate}M"

    seek_args = ["-ss", start_hms] if trim_enabled else []
    duration_args = ["-t", duration_hms] if trim_enabled else []

    copy_cmd = [
        "ffmpeg", "-y",
        *seek_args,
        "-i", input_path,
        *duration_args,
        "-c:v", "copy",
        *audio_args,
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    returncode, stderr = _run_ffmpeg(copy_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return

    # Fallback: re-encode the video (needed if stream copy can't cut
    # cleanly on this codec). Target spec: H.265/HEVC, forced 4K UHD
    # output (3840x2160) regardless of source resolution, user-selected
    # target bitrate, 30fps. Tries NVIDIA NVENC (GPU) first, and only
    # falls back to slower CPU encoding (libx265) if no Nvidia GPU/driver
    # is available on the machine running this.
    hevc_nvenc_cmd = [
        "ffmpeg", "-y",
        *seek_args,
        "-i", input_path,
        *duration_args,
        "-vf", "scale=3840:2160",
        "-c:v", "hevc_nvenc",
        "-preset", "p4",       # NVENC speed/quality preset (p1 fastest - p7 slowest)
        "-rc", "vbr",
        "-b:v", vbitrate,
        "-maxrate", vbitrate,
        "-bufsize", f"{int(video_bitrate) * 2}M",
        "-r", "30",
        *audio_args,
        output_path,
    ]
    returncode, stderr = _run_ffmpeg(hevc_nvenc_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return

    # CPU fallback (no Nvidia GPU available, or NVENC failed for some
    # other reason) - same H.265/bitrate/framerate/resolution target via libx265.
    hevc_cpu_cmd = [
        "ffmpeg", "-y",
        *seek_args,
        "-i", input_path,
        *duration_args,
        "-vf", "scale=3840:2160",
        "-c:v", "libx265",
        "-preset", "veryfast",
        "-b:v", vbitrate,
        "-maxrate", vbitrate,
        "-bufsize", f"{int(video_bitrate) * 2}M",
        "-r", "30",
        *audio_args,
        output_path,
    ]
    returncode, stderr = _run_ffmpeg(hevc_cpu_cmd, process_holder, cancel_check)
    if returncode != 0:
        tail = "\n".join((stderr or "").strip().splitlines()[-8:])
        raise RuntimeError(f"FFmpeg reported an error while processing this video:\n{tail}")


def _run_ffmpeg(
    cmd: list,
    process_holder: Optional[dict],
    cancel_check: Optional[Callable[[], bool]],
):
    """Runs an FFmpeg command via Popen so it can be cancelled immediately.

    FFmpeg's own stderr is sent to a temp file rather than a PIPE. FFmpeg
    writes a continuous stream of per-frame progress text to stderr; if
    that's piped but nobody reads it while the process is running (we only
    used to read it via communicate() after the process exited), the pipe's
    OS buffer fills up and FFmpeg blocks trying to write more - a classic
    subprocess deadlock that looks like FFmpeg has silently frozen (0% CPU
    in Task Manager, no progress, never finishes). Writing to a file instead
    has no such buffer limit, so this can't happen.
    """
    stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            text=True,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        stderr_file.close()
        raise RuntimeError(
            "FFmpeg executable was not found. Please install FFmpeg or add it "
            "to your system PATH."
        ) from exc

    if process_holder is not None:
        process_holder["proc"] = proc

    try:
        while True:
            if cancel_check is not None and cancel_check():
                _terminate(proc)
                raise CancelledError("FFmpeg operation cancelled by user.")
            try:
                proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        stderr_file.seek(0)
        stderr = stderr_file.read()
        return proc.returncode, stderr
    finally:
        if process_holder is not None:
            process_holder["proc"] = None
        stderr_file.close()


def _terminate(proc: subprocess.Popen):
    """Terminates a subprocess promptly, escalating to kill() if needed."""
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    except Exception:
        pass
