"""
cli.py
------
LoopClip command-line interface - mirrors LoopyCut's option style
(--similarity, --method, --start/--stop, --downsample, etc.) on top of
LoopClip's existing download/analyze/encode pipeline.

INPUT accepts either a YouTube URL (downloaded via yt-dlp first) or a
path to an existing local video file, so this can be used standalone
(no GUI, no queue) for scripting or automation.

Examples
--------
  # Automatic loop detection on a local file
  python cli.py input.mp4 output.mp4

  # From a YouTube URL, custom similarity/method
  python cli.py https://youtu.be/XXXXXXXXXXX output.mp4 --similarity 95 --method combined

  # Only analyze a specific window of a long video
  python cli.py input.mp4 output.mp4 --start 00:00:30 --stop 00:05:00 --downsample 4

  # Skip auto-detection entirely - just download/re-encode as-is
  python cli.py https://youtu.be/XXXXXXXXXXX output.mp4 --no-auto-loop
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

import downloader
import loop_detector
import processor
from version import __version__


def _print_progress(phase: str, done: int, total: int):
    pct = (done / total * 100) if total else 0
    label = "Extracting frames" if phase == "extract" else "Comparing frames"
    print(f"\r{label}: {done}/{total} ({pct:.0f}%)", end="", flush=True)
    if total and done >= total:
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopclip",
        description="Download (optional) + auto-detect a seamless loop + encode, in one command.",
    )
    parser.add_argument("input", help="YouTube URL or path to a local video file")
    parser.add_argument("output", help="Path for the output video file")
    parser.add_argument(
        "--version", action="version", version=f"LoopClip {__version__}",
    )

    parser.add_argument("--start", default="00:00:00", help="Start of the search window (HH:MM:SS)")
    parser.add_argument("--stop", default=None, help="End of the search window (HH:MM:SS); default: end of video")
    parser.add_argument(
        "--similarity", type=float, default=98.0,
        help="Match threshold, 0-100 (default: 98)",
    )
    parser.add_argument(
        "--method", choices=loop_detector.METHODS, default="combined",
        help="Comparison method (default: combined)",
    )
    parser.add_argument(
        "--downsample", type=int, default=1,
        help="Analyze every Nth frame; higher = faster, less precise (default: 1)",
    )
    parser.add_argument("--min-length", type=float, default=None, help="Minimum loop length in seconds")
    parser.add_argument("--max-length", type=float, default=None, help="Maximum loop length in seconds")

    parser.add_argument(
        "--no-auto-loop", action="store_true",
        help=(
            "Skip loop detection - the full source is still downloaded/processed "
            "through the normal pipeline (honouring --quality, --video-bitrate, "
            "--audio-bitrate, --no-audio and GPU/NVENC settings), just without "
            "trimming to a detected loop point."
        ),
    )
    parser.add_argument(
        "--crossfade", type=float, default=None,
        help="Seconds of crossfade to blend at the loop point (omit for a hard cut)",
    )

    parser.add_argument("--quality", choices=["best", "4k_hdr", "1080p"], default="best", help="Download quality mode")
    parser.add_argument("--video-bitrate", default="15", help="Target video bitrate in Mbps for re-encodes (default: 15)")
    parser.add_argument("--audio-bitrate", default="original", help="Audio bitrate: 'original' or kbps (default: original)")
    parser.add_argument("--no-audio", action="store_true", help="Strip audio from the output")
    parser.add_argument(
        "--gpu", dest="gpu", action=argparse.BooleanOptionalAction, default=True,
        help="(No-op placeholder) Frame analysis is CPU-only; encoding already tries NVENC first automatically",
    )
    parser.add_argument("--verbose", action="store_true", help="Print extra progress detail")
    return parser


def validate_args(args) -> Optional[str]:
    """
    Validates parsed CLI arguments beyond what argparse's `type=`/`choices=`
    already enforce, returning a human-readable error message (or None if
    everything checks out). Kept as a separate, pure function - takes an
    argparse Namespace, returns str|None - so it can be unit-tested without
    needing to invoke main() or touch the filesystem/network.
    """
    if not (0 <= args.similarity <= 100):
        return f"--similarity must be between 0 and 100 (got {args.similarity})."

    if args.downsample < 1:
        return f"--downsample must be a positive integer (got {args.downsample})."

    if not processor.validate_hms(args.start):
        return f"--start must be in HH:MM:SS format (got '{args.start}')."

    start_seconds = processor.hms_to_seconds(args.start)
    stop_seconds = None
    if args.stop is not None:
        if not processor.validate_hms(args.stop):
            return f"--stop must be in HH:MM:SS format (got '{args.stop}')."
        stop_seconds = processor.hms_to_seconds(args.stop)
        if stop_seconds <= start_seconds:
            return (
                f"--stop ({args.stop}) must be later than --start ({args.start})."
            )

    if args.min_length is not None and args.min_length <= 0:
        return f"--min-length must be a positive number of seconds (got {args.min_length})."

    if args.max_length is not None and args.max_length <= 0:
        return f"--max-length must be a positive number of seconds (got {args.max_length})."

    if (
        args.min_length is not None
        and args.max_length is not None
        and args.min_length >= args.max_length
    ):
        return (
            f"--min-length ({args.min_length}) must be smaller than "
            f"--max-length ({args.max_length})."
        )

    if args.crossfade is not None:
        if args.crossfade <= 0:
            return f"--crossfade must be a positive number of seconds (got {args.crossfade})."
        # Loop duration isn't known until after detection runs (or, with
        # --no-auto-loop, until the source's own length is probed), so a
        # tight upper-bound check happens later in apply_seamless_loop
        # itself, which already falls back to a plain trimmed copy with a
        # clear warning if the crossfade turns out to be too long for the
        # actual clip.

    try:
        video_bitrate_val = int(args.video_bitrate)
    except (TypeError, ValueError):
        return f"--video-bitrate must be a whole number of Mbps (got '{args.video_bitrate}')."
    if video_bitrate_val <= 0:
        return f"--video-bitrate must be a positive number of Mbps (got {args.video_bitrate})."

    if args.audio_bitrate != "original":
        try:
            audio_bitrate_val = int(args.audio_bitrate)
        except (TypeError, ValueError):
            return (
                "--audio-bitrate must be 'original' or a whole number of kbps "
                f"(got '{args.audio_bitrate}')."
            )
        if audio_bitrate_val <= 0:
            return f"--audio-bitrate must be a positive number of kbps (got {args.audio_bitrate})."

    output_parent = Path(args.output).parent
    if output_parent != Path("") and output_parent.exists() and not output_parent.is_dir():
        return f"--output's parent path exists but is not a directory: {output_parent}"

    return None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    error = validate_args(args)
    if error is not None:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print("LoopClip CLI")
    print("=" * 40)
    print(f"Input:  {args.input}")
    print(f"Output: {args.output}")

    work_dir = Path(tempfile.mkdtemp(prefix="loopclip_cli_"))
    try:
        if downloader.is_valid_youtube_url(args.input):
            print("\nDownloading source video...")

            def on_progress(info):
                if info["status"] == "downloading" and info.get("percent") is not None:
                    print(f"\r  {info['percent']:.0f}%   {info.get('speed', '-')}   ETA {info.get('eta', '-')}", end="", flush=True)
                elif info["status"] == "finished":
                    print("\r  100%  - merging streams...              ")

            result = downloader.download_video(
                url=args.input, temp_dir=str(work_dir), quality=args.quality,
                no_audio=args.no_audio, progress_callback=on_progress,
            )
            source_path = result.filepath
            title = result.title
        else:
            source_path = args.input
            if not Path(source_path).exists():
                print(f"\nError: input file not found: {source_path}", file=sys.stderr)
                return 1
            title = Path(source_path).stem

        if args.no_auto_loop:
            print("\n--no-auto-loop set - skipping loop detection; processing the full source instead.")
            print("\nEncoding output...")
            processor.process_video(
                input_path=source_path,
                output_path=args.output,
                trim_enabled=False,
                start_hms="00:00:00.000",
                duration_hms="00:00:00.000",
                audio_bitrate=args.audio_bitrate,
                video_bitrate=args.video_bitrate,
                no_audio=args.no_audio,
            )
        else:
            stop_seconds = processor.hms_to_seconds(args.stop) if args.stop else None
            start_seconds = processor.hms_to_seconds(args.start)

            print(f"\nAnalyzing for a seamless loop (method={args.method}, similarity={args.similarity}%)...")
            try:
                best = loop_detector.find_best_loop(
                    source_path, start=start_seconds, stop=stop_seconds,
                    method=args.method, similarity_threshold=args.similarity,
                    min_loop_seconds=args.min_length, max_loop_seconds=args.max_length,
                    downsample=args.downsample,
                    progress_callback=_print_progress if args.verbose else None,
                )
            except loop_detector.LoopDetectionError as exc:
                print(f"\nError: {exc}", file=sys.stderr)
                return 1

            print(
                f"\nFound loop:\n"
                f"  Time: {best.start:.2f}s - {best.end:.2f}s\n"
                f"  Duration: {best.duration:.2f}s\n"
                f"  Similarity: {best.similarity:.3f}   Quality: {best.quality:.3f}   Score: {best.score:.3f}"
            )

            print("\nEncoding output...")
            processor.process_video(
                input_path=source_path,
                output_path=args.output,
                trim_enabled=True,
                start_hms=processor.seconds_to_ffmpeg_time(best.start),
                duration_hms=processor.seconds_to_ffmpeg_time(best.duration),
                audio_bitrate=args.audio_bitrate,
                video_bitrate=args.video_bitrate,
                no_audio=args.no_audio,
            )

        if args.crossfade:
            print(f"Applying {args.crossfade}s seamless-loop crossfade...")
            crossfaded = str(work_dir / "crossfaded.mp4")
            processor.apply_seamless_loop(
                input_path=args.output, output_path=crossfaded,
                crossfade_seconds=args.crossfade, video_bitrate=args.video_bitrate,
                no_audio=args.no_audio,
            )
            shutil.move(crossfaded, args.output)

        try:
            processor.verify_output(args.output, expect_audio=not args.no_audio)
        except processor.OutputValidationError as exc:
            print(f"\nError: FFmpeg reported success, but the output file failed validation: {exc}", file=sys.stderr)
            return 1

        verb = "processed" if args.no_auto_loop else "created looped"
        print(f"\n\u2713 Successfully {verb} video: {args.output}")
        return 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
