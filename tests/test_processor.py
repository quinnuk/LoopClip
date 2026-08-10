import pytest

import processor
from conftest import requires_ffmpeg


# --- hms_to_seconds / seconds_to_hms ---------------------------------------

@pytest.mark.parametrize("hms, expected_seconds", [
    ("00:00:00", 0),
    ("00:00:30", 30),
    ("00:01:00", 60),
    ("01:00:00", 3600),
    ("01:02:03", 3723),
    ("0:0:0", 0),
])
def test_hms_to_seconds(hms, expected_seconds):
    assert processor.hms_to_seconds(hms) == expected_seconds


def test_hms_to_seconds_invalid_raises():
    with pytest.raises(ValueError):
        processor.hms_to_seconds("not-a-time")


@pytest.mark.parametrize("seconds, expected_hms", [
    (0, "00:00:00"),
    (30, "00:00:30"),
    (60, "00:01:00"),
    (3600, "01:00:00"),
    (3723, "01:02:03"),
    (-5, "00:00:00"),  # clamped to zero, never negative
])
def test_seconds_to_hms(seconds, expected_hms):
    assert processor.seconds_to_hms(seconds) == expected_hms


# --- seconds_to_ffmpeg_time (fractional) ------------------------------------

@pytest.mark.parametrize("seconds, expected", [
    (0, "00:00:00.000"),
    (0.5, "00:00:00.500"),
    (90.25, "00:01:30.250"),
    (-3, "00:00:00.000"),        # clamped to zero
    (0.9995, "00:00:01.000"),    # rounding carries into the next second
])
def test_seconds_to_ffmpeg_time(seconds, expected):
    assert processor.seconds_to_ffmpeg_time(seconds) == expected


# --- validate_hms ------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("00:00:00", True),
    ("01:02:03", True),
    ("99:59:59", True),    # up to 2 hour digits is fine
    ("100:59:59", False),  # more than 2 hour digits is not accepted
    ("00:60:00", False),   # minutes must be 00-59
    ("00:00:60", False),   # seconds must be 00-59
    ("not-a-time", False),
    ("", False),
    (None, False),
])
def test_validate_hms(value, expected):
    assert processor.validate_hms(value) is expected


# --- sanitize_title / build_output_filename ---------------------------------

def test_sanitize_title_removes_illegal_windows_characters():
    dirty = 'My<Video>:Title"With/Illegal\\Chars*?|'
    clean = processor.sanitize_title(dirty)
    for ch in '\\/:*?"<>|':
        assert ch not in clean


def test_sanitize_title_preserves_safe_characters():
    assert processor.sanitize_title("Aquarium 4K - 60fps (HDR)") == "Aquarium 4K - 60fps (HDR)"


def test_sanitize_title_strips_control_characters():
    dirty = "Video\x00Title\x1fWith\x07Control\x1bChars"
    clean = processor.sanitize_title(dirty)
    assert all(ord(c) >= 0x20 for c in clean)


def test_sanitize_title_strips_trailing_dots_and_spaces():
    # Windows silently drops these when the file is actually created, so
    # sanitize_title should match what Windows would keep, not leave a
    # trailing "..." that would quietly disappear.
    assert processor.sanitize_title("My Video...   ") == "My Video"


@pytest.mark.parametrize("reserved", ["CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT9"])
def test_sanitize_title_prefixes_windows_reserved_names(reserved):
    result = processor.sanitize_title(reserved)
    assert result != reserved  # must not be usable as a bare filename
    assert result.upper() not in processor._WINDOWS_RESERVED_NAMES


@pytest.mark.parametrize("empty_ish", ["", "   ", "***", "///", "...", None])
def test_sanitize_title_falls_back_to_default_when_nothing_usable_remains(empty_ish):
    result = processor.sanitize_title(empty_ish)
    assert result == "video"


def test_sanitize_title_caps_length():
    long_title = "A" * 300
    result = processor.sanitize_title(long_title)
    assert len(result) <= processor._MAX_SANITIZED_TITLE_LENGTH


# --- unique_output_path -------------------------------------------------------

def test_unique_output_path_returns_unchanged_when_no_collision(tmp_path):
    target = str(tmp_path / "Aquarium 4K.mp4")
    assert processor.unique_output_path(target) == target


def test_unique_output_path_avoids_a_single_collision(tmp_path):
    target = tmp_path / "Aquarium 4K.mp4"
    target.write_bytes(b"existing user file - must not be silently overwritten")
    result = processor.unique_output_path(str(target))
    assert result == str(tmp_path / "Aquarium 4K (2).mp4")


def test_unique_output_path_skips_past_multiple_collisions(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "clip (2).mp4").write_bytes(b"x")
    (tmp_path / "clip (3).mp4").write_bytes(b"x")
    result = processor.unique_output_path(str(tmp_path / "clip.mp4"))
    assert result == str(tmp_path / "clip (4).mp4")


@pytest.mark.parametrize("duration, expected_suffix", [
    (15, "15s loop"),
    (45.2, "45s loop"),
    (59.4, "59s loop"),     # rounds down, stays under the 60s cutoff
    (59.9, "1min loop"),    # rounds up to 60 -> crosses into minute format
    (60, "1min loop"),
    (150, "2min loop"),
])
def test_build_output_filename(duration, expected_suffix):
    name = processor.build_output_filename("My Video", duration)
    assert name == f"My Video - {expected_suffix}.mp4"


def test_build_output_filename_sanitizes_title():
    name = processor.build_output_filename('Bad:Title/Name', 10)
    assert ":" not in name and "/" not in name


# --- _scale_filter -----------------------------------------------------------

def test_scale_filter_expression_uses_max_width():
    expr = processor._scale_filter(1920)
    assert "1920" in expr
    assert "min(" in expr


# --- get_fps / get_duration_seconds (need real ffmpeg/ffprobe) -------------

@requires_ffmpeg
def test_get_fps_matches_source(make_test_clip):
    clip = make_test_clip("fps_test.mp4", fps=25, duration=1)
    assert processor.get_fps(clip) == pytest.approx(25.0, abs=0.01)


@requires_ffmpeg
def test_get_fps_missing_file_falls_back_to_default():
    assert processor.get_fps("/nonexistent/path/does_not_exist.mp4") == 30.0


@requires_ffmpeg
def test_get_duration_seconds(make_test_clip):
    clip = make_test_clip("duration_test.mp4", duration=2)
    duration = processor.get_duration_seconds(clip)
    assert duration == pytest.approx(2.0, abs=0.2)


@requires_ffmpeg
def test_get_duration_seconds_missing_file_raises():
    with pytest.raises(RuntimeError):
        processor.get_duration_seconds("/nonexistent/path/does_not_exist.mp4")


# --- resolution cap / fps preservation, via the seamless-loop pipeline -----
# (apply_seamless_loop always re-encodes every piece - unlike process_video,
# which may take a stream-copy fast path that never touches the scale/fps
# filters at all - so it's a reliable way to exercise them with real ffmpeg.)

@requires_ffmpeg
def test_seamless_loop_does_not_upscale_below_cap(make_test_clip, tmp_path):
    clip = make_test_clip("small.mp4", width=640, height=360, fps=30, duration=3)
    out = str(tmp_path / "out.mp4")
    processor.apply_seamless_loop(
        input_path=clip, output_path=out, crossfade_seconds=1.0,
        video_bitrate="2", no_audio=True,
    )
    import subprocess
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", out],
        stdout=subprocess.PIPE, text=True,
    )
    width, height = (int(v) for v in result.stdout.strip().split(","))
    assert width == 640 and height == 360


@requires_ffmpeg
def test_seamless_loop_caps_resolution_above_max_width(make_test_clip, tmp_path):
    clip = make_test_clip("big.mp4", width=processor.MAX_OUTPUT_WIDTH + 640, height=1440, fps=24, duration=3)
    out = str(tmp_path / "out.mp4")
    processor.apply_seamless_loop(
        input_path=clip, output_path=out, crossfade_seconds=1.0,
        video_bitrate="2", no_audio=True,
    )
    import subprocess
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width", "-of", "csv=p=0", out],
        stdout=subprocess.PIPE, text=True,
    )
    width = int(result.stdout.strip())
    assert width == processor.MAX_OUTPUT_WIDTH


@requires_ffmpeg
def test_seamless_loop_preserves_source_fps(make_test_clip, tmp_path):
    clip = make_test_clip("fps60.mp4", width=320, height=240, fps=60, duration=3)
    out = str(tmp_path / "out.mp4")
    processor.apply_seamless_loop(
        input_path=clip, output_path=out, crossfade_seconds=1.0,
        video_bitrate="2", no_audio=True,
    )
    assert processor.get_fps(out) == pytest.approx(60.0, abs=0.01)


# --- verify_output ------------------------------------------------------------

@requires_ffmpeg
def test_verify_output_accepts_a_real_valid_clip(make_test_clip):
    clip = make_test_clip("valid.mp4", width=64, height=64, fps=10, duration=1)
    processor.verify_output(clip, expect_audio=False)  # should not raise


def test_verify_output_rejects_missing_file():
    with pytest.raises(processor.OutputValidationError, match="not created"):
        processor.verify_output("/nonexistent/path/nope.mp4", expect_audio=False)


def test_verify_output_rejects_empty_file(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(processor.OutputValidationError, match="empty"):
        processor.verify_output(str(empty), expect_audio=False)


@requires_ffmpeg
def test_verify_output_rejects_corrupt_file(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"this is not a real video file, just junk bytes")
    with pytest.raises(processor.OutputValidationError):
        processor.verify_output(str(junk), expect_audio=False)


@requires_ffmpeg
def test_verify_output_rejects_missing_expected_audio(make_test_clip):
    clip = make_test_clip("no_audio.mp4", width=64, height=64, fps=10, duration=1)
    with pytest.raises(processor.OutputValidationError, match="audio"):
        processor.verify_output(clip, expect_audio=True)


# --- partial output cleanup on failure -----------------------------------

@requires_ffmpeg
def test_failed_encode_segment_cleans_up_partial_output(make_test_clip, tmp_path):
    """A failed FFmpeg run must never leave a corrupt/partial file sitting
    at the destination path - regression test for a real gap found during
    hardening (encode fallbacks raised on failure without removing
    whatever was already written to output_path)."""
    clip = make_test_clip("src.mp4", width=64, height=64, fps=10, duration=2)
    out = tmp_path / "broken_out.mp4"
    # Simulate a leftover partial file from a previous crashed run, to
    # make sure the failure path actually removes it rather than just
    # never creating one.
    out.write_bytes(b"leftover partial data from a previous failed run")

    with pytest.raises(RuntimeError):
        processor._encode_segment(
            clip, str(out), start=5.0, end=1.0,  # invalid: end < start
            video_bitrate="2", no_audio=True,
            process_holder=None, cancel_check=None,
            error_context="test segment",
        )

    assert not out.exists(), "partial/leftover output was not cleaned up after failure"
