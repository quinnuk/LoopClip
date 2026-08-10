import tool_check
from conftest import requires_ffmpeg


def test_check_ytdlp_true_when_installed():
    assert tool_check.check_ytdlp() is True


@requires_ffmpeg
def test_check_ffmpeg_true_when_on_path():
    assert tool_check.check_ffmpeg() is True


@requires_ffmpeg
def test_get_ffmpeg_version_returns_a_version_string():
    version = tool_check.get_ffmpeg_version()
    assert version is not None
    assert version[0].isdigit()  # e.g. "6.1.1-3ubuntu5"


def test_get_ffmpeg_version_none_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(tool_check, "check_ffmpeg", lambda: False)
    assert tool_check.get_ffmpeg_version() is None


@requires_ffmpeg
def test_check_nvenc_returns_tuple_of_bool_and_message():
    available, message = tool_check.check_nvenc()
    assert isinstance(available, bool)
    assert isinstance(message, str) and len(message) > 0


def test_check_nvenc_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(tool_check, "check_ffmpeg", lambda: False)
    available, message = tool_check.check_nvenc()
    assert available is False
    assert "FFmpeg" in message


def test_missing_tools_message_none_when_everything_present():
    # In this test environment, yt-dlp/ffmpeg/opencv/numpy are all
    # installed (required for the rest of the suite to run at all), so
    # this should report nothing missing.
    assert tool_check.missing_tools_message() is None


def test_missing_tools_message_reports_missing_ffmpeg(monkeypatch):
    monkeypatch.setattr(tool_check, "check_ffmpeg", lambda: False)
    message = tool_check.missing_tools_message()
    assert message is not None
    assert "FFmpeg" in message
