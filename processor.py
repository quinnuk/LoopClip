"""
processor.py
------------
Wraps FFmpeg to trim the downloaded video and/or re-encode its audio track
into the final output file. Stream-copies whenever possible (no quality
loss, no re-encoding) and only falls back to re-encoding the video when a
stream copy can't cut cleanly at the requested trim point.
"""

import os
import re
import subprocess
import sys
import tempfile
import uuid
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


def hms_to_seconds(hms: str) -> int:
    """Converts 'HH:MM:SS' to total seconds."""
    parts = [int(p) for p in hms.strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def seconds_to_hms(total_seconds: int) -> str:
    """Converts total seconds to 'HH:MM:SS' (inverse of hms_to_seconds)."""
    total_seconds = max(0, int(total_seconds))
    h, remainder = divmod(total_seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


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
    minutes = hms_to_seconds(duration_hms) // 60
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
        "-vf", "scale=3840:2160,format=nv12",
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
    returncode, nvenc_stderr = _run_ffmpeg(hevc_nvenc_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return
    nvenc_tail = "\n".join((nvenc_stderr or "").strip().splitlines()[-4:])

    # CPU fallback (no Nvidia GPU available, or NVENC failed for some
    # other reason) - same H.265/bitrate/framerate/resolution target via libx265.
    # Thread count is deliberately capped: libx265 at 4K with its default
    # (unlimited) thread pools can use a very large amount of RAM, enough
    # to make the whole machine unresponsive on systems that don't have a
    # lot of headroom free. This trades some encode speed for a hard
    # ceiling on memory use.
    hevc_cpu_cmd = [
        "ffmpeg", "-y",
        *seek_args,
        "-i", input_path,
        *duration_args,
        "-vf", "scale=3840:2160",
        "-c:v", "libx265",
        "-preset", "veryfast",
        "-threads", "4",
        "-x265-params", "pools=4:frame-threads=2",
        "-b:v", vbitrate,
        "-maxrate", vbitrate,
        "-bufsize", f"{int(video_bitrate) * 2}M",
        "-r", "30",
        *audio_args,
        output_path,
    ]
    returncode, cpu_stderr = _run_ffmpeg(hevc_cpu_cmd, process_holder, cancel_check)
    if returncode != 0:
        cpu_tail = "\n".join((cpu_stderr or "").strip().splitlines()[-6:])
        raise RuntimeError(
            "FFmpeg reported an error while processing this video.\n\n"
            f"GPU (NVENC) attempt failed with:\n{nvenc_tail}\n\n"
            f"CPU fallback then also failed with:\n{cpu_tail}"
        )


def get_duration_seconds(path: str) -> float:
    """Returns the duration of a media file in seconds, via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=_NO_WINDOW,
        )
        return float(result.stdout.strip())
    except (ValueError, OSError, FileNotFoundError) as exc:
        raise RuntimeError(
            "Could not determine the video's duration (needed for the seamless "
            "loop crossfade)."
        ) from exc


def apply_seamless_loop(
    input_path: str,
    output_path: str,
    crossfade_seconds: float,
    video_bitrate: str,
    no_audio: bool,
    process_holder: Optional[dict] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    """
    Makes a clip loop seamlessly by cross-fading its last `crossfade_seconds`
    into its first `crossfade_seconds`, so a background loop doesn't have a
    visible jump-cut where it repeats. The output is `crossfade_seconds`
    shorter than the input, since the overlapping ends are blended into
    one shared segment rather than simply appended one after another.

    Built as four small, independent steps rather than one big filter
    graph spanning the whole clip:
      1. Extract the head (first x seconds) and tail (last x seconds) as
         their own tiny standalone files.
      2. Blend just those two tiny clips together with `xfade`/
         `acrossfade` into a single x-second transition clip.
      3. Encode the (potentially long) middle section on its own, to the
         same H.265/4K/bitrate spec as everything else.
      4. Join [middle][transition] with the concat demuxer - a plain,
         sequential file join, not a frame-level filter.

    This matters because `xfade` needs matching frames from both ends of
    the clip at the same time - if the head/middle/tail are all pulled as
    time-shifted branches of one shared decode (as an earlier version of
    this function did), FFmpeg has to hold the *entire* middle section in
    raw, uncompressed memory while it waits for the tail to finish
    decoding. For a multi-minute 4K clip that's tens of gigabytes, easily
    enough to exhaust even a well-specced machine's RAM. Keeping each
    step small and independent avoids that entirely - nothing here ever
    needs to hold more than a few seconds of video in memory at once.
    """
    duration = get_duration_seconds(input_path)
    x = crossfade_seconds
    if x * 2 >= duration:
        raise RuntimeError(
            f"The crossfade duration ({x:.0f}s) is too long for this "
            f"{duration:.0f}s clip - it must be less than half the clip's length."
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    end_start = duration - x

    work_dir = Path(output_path).parent
    uid = uuid.uuid4().hex[:8]
    head_path = str(work_dir / f"_loophead_{uid}.mp4")
    tail_path = str(work_dir / f"_looptail_{uid}.mp4")
    middle_path = str(work_dir / f"_loopmid_{uid}.mp4")
    transition_path = str(work_dir / f"_looptrans_{uid}.mp4")
    concat_list_path = str(work_dir / f"_loopconcat_{uid}.txt")

    try:
        _quick_trim(input_path, head_path, 0, x, no_audio, process_holder, cancel_check)
        _quick_trim(input_path, tail_path, end_start, duration, no_audio, process_holder, cancel_check)
        _crossfade_two_clips(
            tail_path, head_path, transition_path, x, video_bitrate, no_audio,
            process_holder, cancel_check,
        )
        _encode_segment(
            input_path, middle_path, x, end_start, video_bitrate, no_audio,
            process_holder, cancel_check, error_context="the main body of the clip",
        )

        with open(concat_list_path, "w", encoding="utf-8") as f:
            f.write(f"file '{Path(middle_path).as_posix()}'\n")
            f.write(f"file '{Path(transition_path).as_posix()}'\n")
        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list_path, "-c", "copy", output_path,
        ]
        returncode, stderr = _run_ffmpeg(concat_cmd, process_holder, cancel_check)
        if returncode != 0 or not Path(output_path).exists():
            tail_err = "\n".join((stderr or "").strip().splitlines()[-6:])
            raise RuntimeError(
                f"FFmpeg reported an error while joining the seamless loop segments:\n{tail_err}"
            )
    finally:
        for p in (head_path, tail_path, middle_path, transition_path, concat_list_path):
            try:
                if Path(p).exists():
                    os.remove(p)
            except OSError:
                pass


def _quick_trim(
    input_path: str, output_path: str, start: float, end: float, no_audio: bool,
    process_holder: Optional[dict], cancel_check: Optional[Callable[[], bool]],
) -> None:
    """Extracts [start, end) of input_path into a small standalone file -
    used for pulling out the tiny head/tail pieces before blending them.
    Tries a fast stream copy first, falling back to a quick re-encode only
    if the copy fails; either way these pieces are small (a few seconds),
    so this step is cheap regardless of how long the source clip is."""
    dur = end - start
    audio_args = [] if no_audio else ["-c:a", "copy"]
    copy_cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(dur),
        "-c:v", "copy", *audio_args, "-avoid_negative_ts", "make_zero", output_path,
    ]
    returncode, _ = _run_ffmpeg(copy_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return

    reencode_cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(dur),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        *([] if no_audio else ["-c:a", "aac", "-b:a", "192k"]),
        output_path,
    ]
    returncode, stderr = _run_ffmpeg(reencode_cmd, process_holder, cancel_check)
    if returncode != 0:
        tail_err = "\n".join((stderr or "").strip().splitlines()[-6:])
        raise RuntimeError(f"FFmpeg reported an error while extracting a loop segment:\n{tail_err}")


def _crossfade_two_clips(
    tail_path: str, head_path: str, output_path: str, x: float, video_bitrate: str,
    no_audio: bool, process_holder: Optional[dict], cancel_check: Optional[Callable[[], bool]],
) -> None:
    """Blends tail_path into head_path into a single x-second transition
    clip. Both inputs are already tiny standalone files at this point, so
    this stays cheap no matter how long the original source video was."""
    vbitrate = f"{video_bitrate}M"
    video_graph = f"[0:v][1:v]xfade=transition=fade:duration={x}:offset=0,format=nv12[vout]"

    if no_audio:
        filter_complex = video_graph
        map_args = ["-map", "[vout]"]
        audio_out_args = []
    else:
        audio_graph = f"[0:a][1:a]acrossfade=d={x}[aout]"
        filter_complex = video_graph + ";" + audio_graph
        map_args = ["-map", "[vout]", "-map", "[aout]"]
        audio_out_args = ["-c:a", "aac", "-b:a", "192k"]

    nvenc_cmd = [
        "ffmpeg", "-y", "-i", tail_path, "-i", head_path,
        "-filter_complex", filter_complex, *map_args,
        "-c:v", "hevc_nvenc", "-preset", "p4", "-rc", "vbr",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        "-r", "30", *audio_out_args, output_path,
    ]
    returncode, nvenc_stderr = _run_ffmpeg(nvenc_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return
    nvenc_tail = "\n".join((nvenc_stderr or "").strip().splitlines()[-4:])

    cpu_cmd = [
        "ffmpeg", "-y", "-i", tail_path, "-i", head_path,
        "-filter_complex", filter_complex, *map_args,
        "-c:v", "libx265", "-preset", "veryfast", "-threads", "4",
        "-x265-params", "pools=4:frame-threads=2",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        "-r", "30", *audio_out_args, output_path,
    ]
    returncode, cpu_stderr = _run_ffmpeg(cpu_cmd, process_holder, cancel_check)
    if returncode != 0:
        cpu_tail = "\n".join((cpu_stderr or "").strip().splitlines()[-6:])
        raise RuntimeError(
            "FFmpeg reported an error while blending the loop transition.\n\n"
            f"GPU (NVENC) attempt failed with:\n{nvenc_tail}\n\n"
            f"CPU fallback then also failed with:\n{cpu_tail}"
        )


def _encode_segment(
    input_path: str, output_path: str, start: float, end: float, video_bitrate: str,
    no_audio: bool, process_holder: Optional[dict], cancel_check: Optional[Callable[[], bool]],
    error_context: str,
) -> None:
    """Encodes [start, end) of input_path to the standard H.265/4K/bitrate/
    30fps spec used elsewhere, so it can be joined to another clip of the
    same spec via a cheap stream-copy concat afterward. This is a plain,
    ordinary trim+encode (no cross-branch filtering), so it's exactly as
    memory-cheap as the app's normal main trim step regardless of length."""
    dur = end - start
    vbitrate = f"{video_bitrate}M"
    audio_args = [] if no_audio else ["-c:a", "aac", "-b:a", "192k"]

    nvenc_cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(dur),
        "-vf", "scale=3840:2160,format=nv12",
        "-c:v", "hevc_nvenc", "-preset", "p4", "-rc", "vbr",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        "-r", "30", *audio_args, output_path,
    ]
    returncode, nvenc_stderr = _run_ffmpeg(nvenc_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return
    nvenc_tail = "\n".join((nvenc_stderr or "").strip().splitlines()[-4:])

    cpu_cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(dur),
        "-vf", "scale=3840:2160",
        "-c:v", "libx265", "-preset", "veryfast", "-threads", "4",
        "-x265-params", "pools=4:frame-threads=2",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        "-r", "30", *audio_args, output_path,
    ]
    returncode, cpu_stderr = _run_ffmpeg(cpu_cmd, process_holder, cancel_check)
    if returncode != 0:
        cpu_tail = "\n".join((cpu_stderr or "").strip().splitlines()[-6:])
        raise RuntimeError(
            f"FFmpeg reported an error while encoding {error_context}.\n\n"
            f"GPU (NVENC) attempt failed with:\n{nvenc_tail}\n\n"
            f"CPU fallback then also failed with:\n{cpu_tail}"
        )


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