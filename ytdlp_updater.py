"""
ytdlp_updater.py
----------------
Self-contained yt-dlp update mechanism that works whether LoopClip is
running from source or as a frozen PyInstaller .exe.

Why not just "pip install --upgrade yt-dlp"?
----------------------------------------------
LoopClip uses yt-dlp as a bundled Python library (`import yt_dlp` in
downloader.py), not an external tool. When PyInstaller builds
LoopClip.exe, yt-dlp's entire source is frozen directly into the
executable's own archive. A built .exe has no "pip" available at
runtime, and it's meant to run on machines with no Python installation
at all - so even if pip were run against some other Python environment
on the machine, it would have zero effect on the already-built .exe,
since a frozen exe never consults site-packages. A naive "just tell the
user to run pip install -U yt-dlp" is actively misleading for anyone
using the packaged .exe, which is this app's primary distribution
method.

How this actually works instead
--------------------------------
1. check_for_update() asks PyPI's JSON API for yt-dlp's latest version
   and its wheel's published sha256 digest.
2. download_update() downloads that wheel (a plain zip file) to a temp
   file and verifies its sha256 against the digest PyPI itself
   published - before anything is extracted or touched.
3. apply_update() extracts the wheel's yt_dlp/ package folder into a
   brand new, uniquely-named directory under
   %APPDATA%\\LoopClip\\yt_dlp_override\\, reads back its version.py to
   confirm it really is the version that was requested, and only then
   atomically points a small "active version" marker file at it.
   Nothing already in use is ever touched until every check above has
   passed.
4. activate_pending_override(), called once at the very top of both
   main.py and cli.py - before `import downloader` (which is what
   imports yt_dlp) - reads that marker file, if present, and prepends
   the override directory to sys.path. That makes the newer version the
   one Python actually resolves `import yt_dlp` to, taking precedence
   over whatever's frozen inside the .exe or installed via pip.

We deliberately do NOT attempt to hot-swap the already-imported yt_dlp
module inside a running process: it's a large package with many
submodules and internal state, and reload()-ing it mid-session is
unreliable. A successful update takes effect on the *next* launch,
which is what the UI tells the user - the same standard, safe pattern
most real applications use for self-updating a dependency.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
_USER_AGENT = "LoopClip-UpdateCheck/1.0"


class UpdateError(Exception):
    """Raised when a download or update step fails. The message is
    written to be shown to the user directly - see the raise sites below
    for what each one means."""


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    wheel_url: str
    sha256: str
    size_bytes: int


def _version_tuple(v: str):
    """
    Parses a yt-dlp version string (e.g. "2026.07.04" or "2026.7.4.1")
    into a tuple of ints for comparison. Deliberately tolerant of
    differing numbers of segments and leading zeros (int("07") == 7) -
    both appear in the wild: PyPI's own JSON API returns "2026.7.4" with
    no leading zero for the exact same release yt-dlp's own version.py
    reports as "2026.07.04".
    """
    parts = []
    for p in v.strip().split("."):
        digits = re.match(r"\d+", p)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def check_for_update(current_version: str, timeout: float = 5.0) -> Optional[UpdateInfo]:
    """
    Asks PyPI whether a newer yt-dlp is available. Returns None if
    there's no update, or if the check fails for any reason (no
    internet, PyPI unreachable, unexpected response shape, etc.) -
    callers should treat None as "nothing to report", not as an error;
    this is a best-effort background check that should never interrupt
    normal app use.
    """
    try:
        req = urllib.request.Request(PYPI_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)

        latest = data["info"]["version"]
        if not is_newer(latest, current_version):
            return None

        release_files = data["releases"].get(latest, [])
        wheel = next((f for f in release_files if f.get("packagetype") == "bdist_wheel"), None)
        if wheel is None:
            return None  # no wheel published for this release - nothing we can safely install

        sha256 = wheel.get("digests", {}).get("sha256")
        if not sha256:
            return None  # can't verify a download without a published checksum - don't offer it

        return UpdateInfo(
            current_version=current_version,
            latest_version=latest,
            wheel_url=wheel["url"],
            sha256=sha256,
            size_bytes=wheel.get("size", 0),
        )
    except Exception:
        return None


def download_update(
    info: UpdateInfo,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """
    Downloads info.wheel_url to a temp file, verifying its sha256
    against info.sha256 as it streams in. Returns the path to the
    verified temp file on success.

    Raises UpdateError (with a message meant to be shown directly to the
    user) on any failure - network error, size/checksum mismatch, etc -
    and always cleans up the temp file itself in that case. Nothing
    outside of a fresh temp file is touched by this step; see
    apply_update() for where the verified download actually gets
    installed.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="loopclip_ytdlp_update_", suffix=".whl")
    tmp_path = Path(tmp_path)
    try:
        hasher = hashlib.sha256()
        downloaded = 0
        req = urllib.request.Request(info.wheel_url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                with os.fdopen(tmp_fd, "wb") as out:
                    tmp_fd = None  # os.fdopen took ownership of the fd
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, info.size_bytes)
        except OSError as exc:
            raise UpdateError(f"Could not download the yt-dlp update: {exc}") from exc
        finally:
            if tmp_fd is not None:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass

        actual_sha256 = hasher.hexdigest()
        if actual_sha256 != info.sha256:
            raise UpdateError(
                "The downloaded yt-dlp update failed verification (checksum "
                "mismatch) and was discarded. This can happen if the download "
                "was interrupted or corrupted - please try again."
            )

        return tmp_path
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _override_root() -> Path:
    import settings
    return settings._appdata_base(settings.APP_NAME) / "yt_dlp_override"


def apply_update(wheel_path: Path, expected_version: str) -> None:
    """
    Extracts wheel_path's yt_dlp/ package into a new, uniquely-named
    directory, verifies its version.py actually reports
    expected_version, and only then atomically points the "active
    version" marker at it - the mechanism activate_pending_override()
    reads at the next launch.

    Raises UpdateError (safe to show to the user) on any failure -
    corrupt/unexpected wheel contents, a version mismatch, a filesystem
    error - and cleans up its own partial extraction directory in that
    case. The currently-active override (or the frozen/bundled yt-dlp,
    if there isn't one yet) is never touched unless every check here
    passes; a failure here always leaves the previously-working
    yt-dlp exactly as it was.
    """
    root = _override_root()
    root.mkdir(parents=True, exist_ok=True)

    staging_dir = root / f".staging_{expected_version}_{os.getpid()}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)

    try:
        try:
            with zipfile.ZipFile(wheel_path) as zf:
                members = [
                    n for n in zf.namelist()
                    if n.startswith("yt_dlp/") and not n.startswith("yt_dlp/__pycache__/")
                ]
                if not members:
                    raise UpdateError(
                        "The downloaded yt-dlp update doesn't look like a valid "
                        "package (no yt_dlp/ folder found inside it) - update cancelled."
                    )
                zf.extractall(staging_dir, members=members)
        except zipfile.BadZipFile as exc:
            raise UpdateError(
                f"The downloaded yt-dlp update is corrupted and couldn't be opened: {exc}"
            ) from exc

        version_file = staging_dir / "yt_dlp" / "version.py"
        if not version_file.exists():
            raise UpdateError(
                "The downloaded yt-dlp update is missing its version file - update cancelled."
            )
        version_text = version_file.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", version_text)
        actual_version = match.group(1) if match else None
        if actual_version is None or _version_tuple(actual_version) != _version_tuple(expected_version):
            raise UpdateError(
                f"The downloaded yt-dlp update reports version "
                f"'{actual_version or 'unknown'}', not the expected "
                f"'{expected_version}' - update cancelled for safety."
            )

        final_dir = root / expected_version
        if final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        os.replace(staging_dir, final_dir)

        _write_active_pointer(root, expected_version)
        _cleanup_old_versions(root, keep_version=expected_version)

    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    finally:
        try:
            wheel_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_active_pointer(root: Path, version: str) -> None:
    """Atomic write (temp file + os.replace), same pattern as
    settings.save_settings(), so an interruption mid-write can't leave
    a corrupt/partial pointer file behind."""
    pointer_path = root / "active.json"
    fd, tmp_name = tempfile.mkstemp(dir=str(root), prefix=".active_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": version}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, pointer_path)
    except OSError:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def _cleanup_old_versions(root: Path, keep_version: str) -> None:
    """Best-effort removal of previously-applied override versions,
    now that a new one is active - avoids accumulating old copies of
    yt-dlp in AppData forever. Never raises: this is tidy-up, not
    something that should fail the update if it can't complete."""
    try:
        for entry in root.iterdir():
            if entry.is_dir() and entry.name != keep_version and not entry.name.startswith("."):
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def activate_pending_override() -> Optional[str]:
    """
    Call once, at the very top of every entry point (main.py, cli.py) -
    before `import downloader` or anything else that imports yt_dlp.

    If a previously-applied update exists and passes a final sanity
    check, prepends its directory to sys.path so `import yt_dlp`
    resolves to it instead of the frozen/bundled/pip-installed copy, and
    returns the activated version string. Otherwise (no override, or it
    fails validation) does nothing and returns None - falling back
    silently to whatever yt-dlp is already available is always the safe
    default here.
    """
    try:
        root = _override_root()
        pointer_path = root / "active.json"
        if not pointer_path.exists():
            return None

        data = json.loads(pointer_path.read_text(encoding="utf-8"))
        version = data.get("version")
        if not version:
            return None

        override_dir = root / version
        version_file = override_dir / "yt_dlp" / "version.py"
        if not version_file.exists():
            return None

        version_text = version_file.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", version_text)
        if not match or _version_tuple(match.group(1)) != _version_tuple(version):
            return None  # marker and actual contents disagree - don't trust it

        sys.path.insert(0, str(override_dir))
        return version
    except Exception:
        return None
