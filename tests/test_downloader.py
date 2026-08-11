import pytest

import downloader


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "http://youtube.com/watch?v=dQw4w9WgXcQ",
    "youtube.com/watch?v=dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
])
def test_valid_youtube_urls(url):
    assert downloader.is_valid_youtube_url(url) is True


@pytest.mark.parametrize("url", [
    "",
    None,
    "not a url at all",
    "https://vimeo.com/12345",
    "C:\\Videos\\my_video.mp4",
    "/home/user/video.mp4",
    "https://www.youtube.com/",  # no video id
    "https://www.youtube.com/channel/UCxxxxx",
    "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
])
def test_invalid_youtube_urls(url):
    assert downloader.is_valid_youtube_url(url) is False
