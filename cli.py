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
  python cli.py https://youtu.be/XXXXXXXXXXX output.mp4 --similarity 95 --method hybrid_off_ssim

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

import downloader
import loop_detector
import processor


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
        help="Skip loop detection entirely - just download/re-encode the source as-is",
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


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

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
            print("\n--no-auto-loop set - skipping loop detection.")
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, args.output)
            print(f"\n\u2713 Saved: {args.output}")
            return 0

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

        print(f"\n\u2713 Successfully created looped video: {args.output}")
        return 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
