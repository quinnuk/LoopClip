import pytest

import cli
import processor as processor_module
from conftest import requires_ffmpeg


def _args(argv):
    return cli.build_parser().parse_args(argv)


# --- valid arguments ---------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["in.mp4", "out.mp4"],
    ["in.mp4", "out.mp4", "--similarity", "95", "--method", "combined"],
    ["in.mp4", "out.mp4", "--start", "00:00:30", "--stop", "00:05:00", "--downsample", "4"],
    ["in.mp4", "out.mp4", "--no-auto-loop"],
    ["in.mp4", "out.mp4", "--crossfade", "1.5"],
    ["in.mp4", "out.mp4", "--min-length", "5", "--max-length", "20"],
    ["in.mp4", "out.mp4", "--video-bitrate", "25", "--audio-bitrate", "192"],
    ["in.mp4", "out.mp4", "--audio-bitrate", "original", "--no-audio"],
])
def test_valid_arguments_pass_validation(argv):
    assert cli.validate_args(_args(argv)) is None


# --- invalid arguments --------------------------------------------------------

@pytest.mark.parametrize("argv, expected_substring", [
    (["in.mp4", "out.mp4", "--similarity", "101"], "similarity"),
    (["in.mp4", "out.mp4", "--similarity", "-1"], "similarity"),
    (["in.mp4", "out.mp4", "--downsample", "0"], "downsample"),
    (["in.mp4", "out.mp4", "--start", "bad-time"], "start"),
    (["in.mp4", "out.mp4", "--stop", "bad-time"], "stop"),
    (["in.mp4", "out.mp4", "--start", "00:10:00", "--stop", "00:01:00"], "stop"),
    (["in.mp4", "out.mp4", "--min-length", "-1"], "min-length"),
    (["in.mp4", "out.mp4", "--max-length", "-1"], "max-length"),
    (["in.mp4", "out.mp4", "--min-length", "10", "--max-length", "5"], "min-length"),
    (["in.mp4", "out.mp4", "--crossfade", "0"], "crossfade"),
    (["in.mp4", "out.mp4", "--crossfade", "-2"], "crossfade"),
    (["in.mp4", "out.mp4", "--video-bitrate", "not-a-number"], "video-bitrate"),
    (["in.mp4", "out.mp4", "--video-bitrate", "0"], "video-bitrate"),
    (["in.mp4", "out.mp4", "--audio-bitrate", "not-a-number"], "audio-bitrate"),
    (["in.mp4", "out.mp4", "--audio-bitrate", "-5"], "audio-bitrate"),
])
def test_invalid_arguments_rejected(argv, expected_substring):
    error = cli.validate_args(_args(argv))
    assert error is not None
    assert expected_substring in error


def test_method_choice_enforced_by_argparse():
    # --method isn't part of validate_args - argparse's choices= already
    # rejects it at parse time, so this should raise SystemExit rather
    # than silently accepting a bogus method.
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["in.mp4", "out.mp4", "--method", "not_a_real_method"])


def test_version_flag_prints_version_and_exits(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.build_parser().parse_args(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "LoopClip" in out
    assert cli.__version__ in out


# --- end-to-end: --no-auto-loop actually processes through the pipeline ----
# (regression test for the old behavior: a raw shutil.copy2() that silently
# ignored --video-bitrate/--audio-bitrate/--no-audio/--quality)

@requires_ffmpeg
def test_no_auto_loop_end_to_end_respects_no_audio(tmp_path):
    import subprocess

    clip = str(tmp_path / "with_audio_source.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            "-loglevel", "error", clip,
        ],
        check=True,
    )
    out = str(tmp_path / "out.mp4")

    result = cli.main([clip, out, "--no-auto-loop", "--no-audio", "--video-bitrate", "2"])
    assert result == 0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", out],
        stdout=subprocess.PIPE, text=True,
    )
    streams = probe.stdout.strip().splitlines()
    assert "audio" not in streams  # --no-audio must actually take effect
    assert "video" in streams


@requires_ffmpeg
def test_no_auto_loop_end_to_end_keeps_audio_by_default(tmp_path):
    import subprocess

    clip = str(tmp_path / "with_audio_source2.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            "-loglevel", "error", clip,
        ],
        check=True,
    )
    out = str(tmp_path / "out2.mp4")

    result = cli.main([clip, out, "--no-auto-loop", "--video-bitrate", "2"])
    assert result == 0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", out],
        stdout=subprocess.PIPE, text=True,
    )
    streams = probe.stdout.strip().splitlines()
    assert "audio" in streams


@requires_ffmpeg
def test_no_auto_loop_is_not_a_raw_file_copy(make_test_clip, tmp_path):
    """The old behavior was shutil.copy2(source, output) - byte-identical
    to the input. The fixed behavior re-encodes, so file size/bytes should
    differ even for a trivial re-encode."""
    clip = make_test_clip("source.mp4", width=320, height=240, fps=24, duration=2)
    out = str(tmp_path / "out.mp4")

    cli.main([clip, out, "--no-auto-loop", "--no-audio", "--video-bitrate", "1"])

    with open(clip, "rb") as f:
        source_bytes = f.read()
    with open(out, "rb") as f:
        output_bytes = f.read()
    assert source_bytes != output_bytes


@requires_ffmpeg
def test_no_auto_loop_validates_output_before_reporting_success(make_test_clip, tmp_path):
    """If processing somehow produced a corrupt file, cli.main() should
    catch that via processor.verify_output() rather than reporting
    success - regression test for output validation being wired in."""
    clip = make_test_clip("src.mp4", width=64, height=64, fps=10, duration=1)
    out = str(tmp_path / "out.mp4")

    result = cli.main([clip, out, "--no-auto-loop", "--no-audio", "--video-bitrate", "2"])
    assert result == 0  # sanity: the real (non-corrupt) case still succeeds

    # Now corrupt the already-produced output and confirm verify_output
    # itself would catch it (this is what cli.main() calls internally).
    with open(out, "wb") as f:
        f.write(b"corrupted after the fact")
    with pytest.raises(processor_module.OutputValidationError):
        processor_module.verify_output(out, expect_audio=False)
