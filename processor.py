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

# Re-encode fallback paths cap resolution at this width (LoopClip's stated
# target is 4K UHD screensaver/live-wallpaper output) but never *upscale*
# a smaller source up to it - that just inflates file size/encode time for
# no real quality gain. A single named constant here means raising the
# cap later (e.g. for 8K sources) is a one-line change.
MAX_OUTPUT_WIDTH = 3840


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


def seconds_to_ffmpeg_time(total_seconds: float) -> str:
    """
    Converts a (possibly fractional) seconds value to 'HH:MM:SS.mmm', which
    FFmpeg's -ss/-t accept directly. Used for loop points coming out of
    loop_detector, which are frame-accurate rather than whole-second - a
    plain seconds_to_hms() would silently truncate them back to the
    nearest second and lose that precision.
    """
    total_seconds = max(0.0, float(total_seconds))
    whole = int(total_seconds)
    ms = round((total_seconds - whole) * 1000)
    if ms == 1000:  # rounding carried into the next second
        whole += 1
        ms = 0
    h, remainder = divmod(whole, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def sanitize_title(title: str) -> str:
    """Removes characters that are illegal in Windows filenames."""
    return re.sub(r'[\\/:*?"<>|]', "", title).strip()


def build_output_filename(title: str, duration_seconds: float) -> str:
    """
    Builds the final output filename, e.g.:
    'Wide Fantasy Valley Aquascape 3hours HDR.mp4'
    -> 'Wide Fantasy Valley Aquascape 3hours HDR - 15s loop.mp4'

    Takes duration in seconds (not an HH:MM:SS string) since detected loop
    durations are fractional; shown in whole seconds for short loops and
    minutes for longer ones, rounded for readability.
    """
    clean_title = sanitize_title(title)
    duration_seconds = max(0, round(duration_seconds))
    if duration_seconds < 60:
        length_label = f"{duration_seconds}s loop"
    else:
        length_label = f"{duration_seconds // 60}min loop"
    return f"{clean_title} - {length_label}.mp4"


def _audio_args(audio_bitrate: str, no_audio: bool) -> list:
    if no_audio:
        # "-an" is required to actually drop the audio stream. Simply
        # omitting a "-c:a ..." flag does NOT do this - ffmpeg still maps
        # and encodes the source's audio track with its own default codec
        # choice when no explicit codec or stream selection is given, so
        # an empty args list here previously left --no-audio silently
        # doing nothing.
        return ["-an"]
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
    # cleanly on this codec). Target spec: H.265/HEVC, capped at
    # MAX_OUTPUT_WIDTH wide (downscaled only if the source is larger,
    # never upscaled), user-selected target bitrate, source frame rate
    # preserved (no forced -r). Tries NVIDIA NVENC (GPU) first, and only
    # falls back to slower CPU encoding (libx265) if no Nvidia GPU/driver
    # is available on the machine running this.
    hevc_nvenc_cmd = [
        "ffmpeg", "-y",
        *seek_args,
        "-i", input_path,
        *duration_args,
        "-vf", f"{_scale_filter()},format=nv12",
        "-c:v", "hevc_nvenc",
        "-preset", "p4",  # NVENC speed/quality preset (p1 fastest - p7 slowest)
        "-rc", "vbr",
        "-b:v", vbitrate,
        "-maxrate", vbitrate,
        "-bufsize", f"{int(video_bitrate) * 2}M",
        *audio_args,
        output_path,
    ]
    returncode, nvenc_stderr = _run_ffmpeg(hevc_nvenc_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return

    nvenc_tail = "\n".join((nvenc_stderr or "").strip().splitlines()[-4:])

    # CPU fallback (no Nvidia GPU available, or NVENC failed for some
    # other reason) - same H.265/bitrate/resolution-cap target via libx265.
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
        "-vf", _scale_filter(),
        "-c:v", "libx265",
        "-preset", "veryfast",
        "-threads", "4",
        "-x265-params", "pools=4:frame-threads=2",
        "-b:v", vbitrate,
        "-maxrate", vbitrate,
        "-bufsize", f"{int(video_bitrate) * 2}M",
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


def _scale_filter(max_width: int = MAX_OUTPUT_WIDTH) -> str:
    """
    FFmpeg scale expression that downscales only if the source is wider
    than max_width, and otherwise leaves resolution untouched - never
    upscales a smaller source up to the cap. Height is derived with -2
    (nearest even number) to preserve aspect ratio, since most encoders
    require even dimensions.
    """
    return f"scale='min({max_width},iw)':-2"


def get_fps(path: str) -> float:
    """
    Returns the source's native frame rate via ffprobe (r_frame_rate,
    e.g. "30000/1001" for 29.97fps). Used so re-encode fallbacks can
    preserve the source's actual frame rate instead of forcing a fixed
    one. Falls back to 30.0 if it can't be determined (e.g. ffprobe
    missing or the stream has no frame rate info) rather than raising -
    callers already have a sensible default to fall back to.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=_NO_WINDOW,
        )
        raw = result.stdout.strip()
        if "/" in raw:
            num, den = raw.split("/", 1)
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        else:
            fps = float(raw)
        return fps if fps > 0 else 30.0
    except (ValueError, OSError, FileNotFoundError):
        return 30.0


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


def _warn(on_warning: Optional[Callable[[str], None]], message: str) -> None:
    """Best-effort call to an optional warning callback - never lets a
    problem in the callback itself interrupt processing."""
    if on_warning is not None:
        try:
            on_warning(message)
        except Exception:
            pass


def apply_seamless_loop(
    input_path: str,
    output_path: str,
    crossfade_seconds: float,
    video_bitrate: str,
    no_audio: bool,
    process_holder: Optional[dict] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    on_warning: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Makes a clip loop seamlessly by cross-fading its last `crossfade_seconds`
    into its first `crossfade_seconds`, so a background loop doesn't have a
    visible jump-cut where it repeats. The output is `crossfade_seconds`
    shorter than the input, since the overlapping ends are blended into
    one shared segment rather than simply appended one after another.

    If the crossfade can't be built - the clip is too short for the
    requested crossfade length, or any of the FFmpeg steps below fail -
    this falls back to saving a plain trimmed copy with no crossfade
    instead of raising and aborting the whole queue item. `on_warning`,
    if given, is called with a human-readable message describing the
    fallback so the caller can surface it (e.g. a toast/log line) rather
    than losing the source file for nothing. A user-triggered cancel
    (CancelledError) is never turned into a fallback - it always
    propagates so Cancel keeps working as expected.

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

    The head/tail pieces are always fully re-encoded (never stream-copied)
    to a fixed 30fps CFR timebase before being handed to `xfade`. YouTube
    sources are frequently variable frame rate (VFR), and a stream copy
    preserves that VFR timing exactly - `xfade`/`acrossfade` can then
    stall indefinitely (0% CPU, no output, never exits) because it never
    receives frames on the steady cadence it expects. Re-encoding here is
    cheap (these pieces are only a couple of seconds long) and removes
    that failure mode entirely.
    """
    duration = get_duration_seconds(input_path)
    x = crossfade_seconds
    if x * 2 >= duration:
        _warn(
            on_warning,
            f"Crossfade duration ({x:.0f}s) is too long for this {duration:.0f}s "
            "clip, so it was saved without a seamless loop instead.",
        )
        _plain_copy_fallback(
            input_path, output_path, video_bitrate, no_audio, process_holder, cancel_check,
        )
        return

    # Every piece fed into the xfade/concat pipeline below (head, tail,
    # transition, middle) is normalized to this single fixed CFR rate -
    # using the source's own native fps (instead of a hardcoded 30) means
    # e.g. a 60fps source still loops at 60fps rather than losing half its
    # temporal quality. See _quick_trim/_crossfade_two_clips/_encode_segment
    # docstrings for why a fixed, matching rate is required here at all.
    fps = get_fps(input_path)

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
        try:
            _quick_trim(
                input_path, head_path, 0, x, no_audio, process_holder, cancel_check,
                force_reencode=True, fps=fps,
            )
            _quick_trim(
                input_path, tail_path, end_start, duration, no_audio, process_holder, cancel_check,
                force_reencode=True, fps=fps,
            )
            _crossfade_two_clips(
                tail_path, head_path, transition_path, x, video_bitrate, no_audio,
                process_holder, cancel_check, fps=fps,
            )
            _encode_segment(
                input_path, middle_path, x, end_start, video_bitrate, no_audio,
                process_holder, cancel_check, error_context="the main body of the clip", fps=fps,
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
        except CancelledError:
            raise
        except Exception as exc:
            _warn(
                on_warning,
                f"Could not build the seamless loop crossfade ({exc}); "
                "saved as a plain trimmed clip instead.",
            )
            _plain_copy_fallback(
                input_path, output_path, video_bitrate, no_audio, process_holder, cancel_check,
            )
    finally:
        for p in (head_path, tail_path, middle_path, transition_path, concat_list_path):
            try:
                if Path(p).exists():
                    os.remove(p)
            except OSError:
                pass


def _plain_copy_fallback(
    input_path: str,
    output_path: str,
    video_bitrate: str,
    no_audio: bool,
    process_holder: Optional[dict],
    cancel_check: Optional[Callable[[], bool]],
) -> None:
    """Saves the full, un-blended clip as-is - used when the seamless loop
    crossfade can't be built. This is what apply_seamless_loop falls back
    to instead of raising, so a queue item still ends up with a usable
    output file (and 'Delete original large file' can safely remove the
    source afterward) rather than the whole item aborting.

    Tries a fast stream copy first, same as process_video's main path,
    then falls back to an NVENC/CPU re-encode at the standard H.265/4K
    spec only if a straight copy isn't possible for this source.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    # "-an" (not just omitting -c:a) is required to actually drop audio -
    # see _audio_args' docstring note for why.
    audio_args = ["-an"] if no_audio else ["-c:a", "copy"]

    copy_cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "copy", *audio_args,
        "-avoid_negative_ts", "make_zero", output_path,
    ]
    returncode, _ = _run_ffmpeg(copy_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return

    vbitrate = f"{video_bitrate}M"
    reencode_audio_args = ["-an"] if no_audio else ["-c:a", "aac", "-b:a", "192k"]

    nvenc_cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"{_scale_filter()},format=nv12",
        "-c:v", "hevc_nvenc", "-preset", "p4", "-rc", "vbr",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        *reencode_audio_args, output_path,
    ]
    returncode, nvenc_stderr = _run_ffmpeg(nvenc_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return

    nvenc_tail = "\n".join((nvenc_stderr or "").strip().splitlines()[-4:])

    cpu_cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", _scale_filter(),
        "-c:v", "libx265", "-preset", "veryfast", "-threads", "4",
        "-x265-params", "pools=4:frame-threads=2",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        *reencode_audio_args, output_path,
    ]
    returncode, cpu_stderr = _run_ffmpeg(cpu_cmd, process_holder, cancel_check)
    if returncode != 0:
        cpu_tail = "\n".join((cpu_stderr or "").strip().splitlines()[-6:])
        raise RuntimeError(
            "FFmpeg reported an error while saving the clip without a seamless "
            "loop (the crossfade attempt also failed earlier).\n\n"
            f"GPU (NVENC) attempt failed with:\n{nvenc_tail}\n\n"
            f"CPU fallback then also failed with:\n{cpu_tail}"
        )


def _quick_trim(
    input_path: str, output_path: str, start: float, end: float, no_audio: bool,
    process_holder: Optional[dict], cancel_check: Optional[Callable[[], bool]],
    force_reencode: bool = False, fps: float = 30.0,
) -> None:
    """Extracts [start, end) of input_path into a small standalone file -
    used for pulling out the tiny head/tail pieces before blending them.

    Tries a fast stream copy first, falling back to a quick re-encode only
    if the copy fails; either way these pieces are small (a few seconds),
    so this step is cheap regardless of how long the source clip is.

    Pass force_reencode=True when the result is going straight into the
    `xfade`/`acrossfade` filter graph (as it is from apply_seamless_loop):
    a stream copy preserves the source's original frame timing verbatim,
    including any variable frame rate, which can make those filters hang
    indefinitely. Re-encoding to a fixed CFR timebase up front avoids
    that, and costs almost nothing since these clips are only a couple of
    seconds long. `fps` should be the source's own native frame rate
    (see get_fps) so a 60fps source still loops at 60fps rather than
    being knocked down to a fixed 30 regardless of source - it just needs
    to be *some* fixed, matching rate shared by every piece that gets fed
    into xfade/concat later.
    """
    dur = end - start
    # "-an" (not just omitting -c:a) is required to actually drop audio -
    # see _audio_args' docstring note for why.
    audio_args = ["-an"] if no_audio else ["-c:a", "copy"]

    if not force_reencode:
        copy_cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(dur),
            "-c:v", "copy", *audio_args, "-avoid_negative_ts", "make_zero", output_path,
        ]
        returncode, _ = _run_ffmpeg(copy_cmd, process_holder, cancel_check)
        if returncode == 0 and Path(output_path).exists():
            return

    reencode_cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(dur),
        "-vf", f"fps={fps}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        *(["-an"] if no_audio else ["-c:a", "aac", "-b:a", "192k", "-af", "aresample=async=1"]),
        output_path,
    ]
    returncode, stderr = _run_ffmpeg(reencode_cmd, process_holder, cancel_check)
    if returncode != 0:
        tail_err = "\n".join((stderr or "").strip().splitlines()[-6:])
        raise RuntimeError(f"FFmpeg reported an error while extracting a loop segment:\n{tail_err}")


def _crossfade_two_clips(
    tail_path: str, head_path: str, output_path: str, x: float, video_bitrate: str,
    no_audio: bool, process_holder: Optional[dict], cancel_check: Optional[Callable[[], bool]],
    fps: float = 30.0,
) -> None:
    """Blends tail_path into head_path into a single x-second transition
    clip. Both inputs are already tiny standalone files at this point, so
    this stays cheap no matter how long the original source video was.

    Both video streams are forced to a fixed CFR timebase (and audio is
    nudged into sync with aresample=async) immediately before the
    xfade/acrossfade filters. `xfade` in particular expects frames to
    arrive on a steady, predictable cadence from both inputs - if either
    input is VFR (as head/tail pieces stream-copied straight from a
    YouTube source often are), it can stall indefinitely instead of
    erroring out, which is what produced the "hangs forever" behaviour.
    Normalizing here means this holds even if _quick_trim's inputs ever
    change upstream. `fps` should match the source's native rate (see
    get_fps) and the rate _quick_trim/_encode_segment normalized their
    outputs to, so a 60fps source loops at 60fps CFR instead of being
    knocked down to a fixed 30 - the actual value just needs to be
    consistent across every piece that gets fed into this filter graph
    and the concat step afterward.
    """
    vbitrate = f"{video_bitrate}M"
    video_graph = (
        f"[0:v]fps={fps},format=nv12[v0];"
        f"[1:v]fps={fps},format=nv12[v1];"
        f"[v0][v1]xfade=transition=fade:duration={x}:offset=0,format=nv12[vout]"
    )

    if no_audio:
        filter_complex = video_graph
        map_args = ["-map", "[vout]"]
        audio_out_args = []
    else:
        audio_graph = (
            "[0:a]aresample=async=1[a0];"
            "[1:a]aresample=async=1[a1];"
            f"[a0][a1]acrossfade=d={x}[aout]"
        )
        filter_complex = video_graph + ";" + audio_graph
        map_args = ["-map", "[vout]", "-map", "[aout]"]
        audio_out_args = ["-c:a", "aac", "-b:a", "192k"]

    nvenc_cmd = [
        "ffmpeg", "-y", "-i", tail_path, "-i", head_path,
        "-filter_complex", filter_complex, *map_args,
        "-c:v", "hevc_nvenc", "-preset", "p4", "-rc", "vbr",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        *audio_out_args, output_path,
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
        *audio_out_args, output_path,
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
    error_context: str, fps: float = 30.0,
) -> None:
    """Encodes [start, end) of input_path to the standard H.265/resolution-
    cap/bitrate spec used elsewhere, at a fixed `fps` (matching the
    source's native rate - see get_fps), so it can be joined to another
    clip of the same spec via a cheap stream-copy concat afterward. This
    is a plain, ordinary trim+encode (no cross-branch filtering), so it's
    exactly as memory-cheap as the app's normal main trim step regardless
    of length."""
    dur = end - start
    vbitrate = f"{video_bitrate}M"
    # "-an" (not just omitting -c:a) is required to actually drop audio -
    # see _audio_args' docstring note for why.
    audio_args = ["-an"] if no_audio else ["-c:a", "aac", "-b:a", "192k"]

    nvenc_cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(dur),
        "-vf", f"{_scale_filter()},fps={fps},format=nv12",
        "-c:v", "hevc_nvenc", "-preset", "p4", "-rc", "vbr",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        *audio_args, output_path,
    ]
    returncode, nvenc_stderr = _run_ffmpeg(nvenc_cmd, process_holder, cancel_check)
    if returncode == 0 and Path(output_path).exists():
        return

    nvenc_tail = "\n".join((nvenc_stderr or "").strip().splitlines()[-4:])

    cpu_cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(dur),
        "-vf", f"{_scale_filter()},fps={fps}",
        "-c:v", "libx265", "-preset", "veryfast", "-threads", "4",
        "-x265-params", "pools=4:frame-threads=2",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", f"{int(video_bitrate) * 2}M",
        *audio_args, output_path,
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
    finally:
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