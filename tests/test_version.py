import re

from version import __version__


def test_version_is_semver_like():
    assert re.match(r"^\d+\.\d+\.\d+$", __version__), (
        f"__version__ should look like MAJOR.MINOR.PATCH, got '{__version__}'"
    )
