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


def test_check_nvenc_surfaces_real_root_cause_not_generic_message(monkeypatch):
    """
    Regression test for a real bug: check_nvenc() used to return a fixed
    generic "no compatible NVIDIA GPU/driver detected" message on any
    failure, regardless of the actual cause - reported by a user with a
    GTX 1070 (which genuinely supports NVENC HEVC encoding in hardware),
    where that generic message was actively misleading since the real
    problem was something else entirely (verified directly in this
    sandbox: the true root cause line - "Cannot load libcuda.so.1" - was
    the FIRST relevant line of ffmpeg's stderr, not the last, and a
    naive "just take stderr's last line" fix would have shown a useless
    downstream symptom like "Nothing was written into output file..."
    instead).
    """
    class FakeResult:
        returncode = 255
        stdout = ""
        stderr = (
            "[hevc_nvenc @ 0x564057c2a240] Cannot load libcuda.so.1\n"
            "[vost#0:0/hevc_nvenc @ 0x564057c29e40] Error while opening encoder"
            " - maybe incorrect parameters such as bit_rate, rate, width or height.\n"
            "Error while filtering: Operation not permitted\n"
            "[out#0/null @ 0x564057c28b40] Nothing was written into output file,"
            " because at least one of its streams received no packets.\n"
        )

    monkeypatch.setattr(tool_check, "check_ffmpeg", lambda: True)
    monkeypatch.setattr(tool_check.subprocess, "run", lambda *a, **k: FakeResult())

    available, message = tool_check.check_nvenc()
    assert available is False
    assert "Cannot load libcuda.so.1" in message
    assert "Nothing was written" not in message  # the unhelpful downstream symptom
    assert "[hevc_nvenc @" not in message  # internal pointer-address prefix stripped


def test_check_nvenc_windows_driver_version_message(monkeypatch):
    """Same regression, but for the specific real-world Windows failure
    mode a user would actually hit with an outdated NVIDIA driver -
    ffmpeg reports a clear, actionable message for this that the old
    generic text would have hidden."""
    class FakeResult:
        returncode = 255
        stdout = ""
        stderr = (
            "[hevc_nvenc @ 000001d3a1b2c3d0] Driver does not support the required "
            "nvenc API version. Required: 12.1 Found: 11.1\n"
            "[hevc_nvenc @ 000001d3a1b2c3d0] The minimum required Nvidia driver "
            "for nvenc is 471.41 or newer\n"
        )

    monkeypatch.setattr(tool_check, "check_ffmpeg", lambda: True)
    monkeypatch.setattr(tool_check.subprocess, "run", lambda *a, **k: FakeResult())

    available, message = tool_check.check_nvenc()
    assert available is False
    assert "471.41" in message or "does not support" in message


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
