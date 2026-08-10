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
