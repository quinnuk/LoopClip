import hashlib
import json
import socket
import zipfile
from pathlib import Path

import pytest

import ytdlp_updater as upd


def _network_available() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=3).close()
        return True
    except OSError:
        return False


requires_network = pytest.mark.skipif(
    not _network_available(), reason="no network access to pypi.org from this machine"
)


# --- _version_tuple / is_newer (pure, no network) ---------------------------

@pytest.mark.parametrize("latest, current, expected", [
    ("2026.07.04", "2026.07.03", True),
    ("2026.7.4", "2026.07.04", False),      # same version, different zero-padding
    ("2026.07.04", "2026.07.04", False),    # identical
    ("2025.12.31", "2026.01.01", False),    # latest is actually older
    ("2026.07.04.1", "2026.07.04", True),   # extra trailing segment = newer
    ("2026.07.04", "2026.07.04.1", False),
    ("2027.01.01", "2020.01.01", True),
])
def test_is_newer(latest, current, expected):
    assert upd.is_newer(latest, current) is expected


# --- check_for_update (real network, skip-safe) -----------------------------

@requires_network
def test_check_for_update_finds_a_real_update():
    info = upd.check_for_update(current_version="2020.01.01")
    assert info is not None
    assert info.latest_version
    assert info.wheel_url.startswith("https://")
    assert len(info.sha256) == 64  # sha256 hex digest length
    assert info.size_bytes > 0


@requires_network
def test_check_for_update_none_when_already_current():
    info = upd.check_for_update(current_version="2020.01.01")
    assert info is not None
    already_current = upd.check_for_update(current_version=info.latest_version)
    assert already_current is None


def test_check_for_update_none_on_unreachable_host(monkeypatch):
    monkeypatch.setattr(upd, "PYPI_URL", "https://pypi.invalid.does-not-exist.example/json")
    assert upd.check_for_update(current_version="2020.01.01", timeout=2) is None


# --- download_update (real network for the happy path; synthetic for failure) --

@requires_network
def test_download_update_verifies_checksum_and_succeeds():
    info = upd.check_for_update(current_version="2020.01.01")
    calls = []
    path = upd.download_update(info, progress_callback=lambda d, t: calls.append((d, t)))
    try:
        assert path.exists()
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == info.sha256
        assert len(calls) > 0
    finally:
        path.unlink(missing_ok=True)


@requires_network
def test_download_update_rejects_checksum_mismatch():
    info = upd.check_for_update(current_version="2020.01.01")
    tampered = upd.UpdateInfo(
        current_version=info.current_version, latest_version=info.latest_version,
        wheel_url=info.wheel_url, sha256="0" * 64, size_bytes=info.size_bytes,
    )
    with pytest.raises(upd.UpdateError, match="checksum"):
        upd.download_update(tampered)


# --- apply_update (synthetic wheels - fast, no network) ---------------------

def _make_fake_wheel(tmp_path, version: str, include_version_file=True, corrupt=False) -> Path:
    wheel_path = tmp_path / f"fake_{version}.whl"
    if corrupt:
        wheel_path.write_bytes(b"this is not a zip file")
        return wheel_path

    with zipfile.ZipFile(wheel_path, "w") as zf:
        if include_version_file:
            zf.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
        zf.writestr("yt_dlp/__init__.py", "# fake package\n")
        zf.writestr(f"yt_dlp-{version}.dist-info/METADATA", "fake metadata\n")
    return wheel_path


def test_apply_update_succeeds_with_valid_wheel(isolated_appdata, tmp_path):
    wheel = _make_fake_wheel(tmp_path, "9999.01.01")
    upd.apply_update(wheel, "9999.01.01")

    root = upd._override_root()
    assert (root / "9999.01.01" / "yt_dlp" / "version.py").exists()
    active = json.loads((root / "active.json").read_text())
    assert active["version"] == "9999.01.01"
    assert not wheel.exists()  # temp wheel cleaned up


def test_apply_update_rejects_corrupt_zip(isolated_appdata, tmp_path):
    wheel = _make_fake_wheel(tmp_path, "9999.01.02", corrupt=True)
    with pytest.raises(upd.UpdateError, match="corrupted"):
        upd.apply_update(wheel, "9999.01.02")

    root = upd._override_root()
    assert not (root / "9999.01.02").exists()


def test_apply_update_rejects_missing_yt_dlp_folder(isolated_appdata, tmp_path):
    wheel_path = tmp_path / "empty.whl"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr("not_yt_dlp/file.txt", "wrong package entirely\n")
    with pytest.raises(upd.UpdateError, match="valid package"):
        upd.apply_update(wheel_path, "9999.01.03")


def test_apply_update_rejects_missing_version_file(isolated_appdata, tmp_path):
    wheel = _make_fake_wheel(tmp_path, "9999.01.04", include_version_file=False)
    with pytest.raises(upd.UpdateError, match="version file"):
        upd.apply_update(wheel, "9999.01.04")


def test_apply_update_rejects_version_mismatch(isolated_appdata, tmp_path):
    # Wheel genuinely reports "9999.01.05" internally, but caller claims
    # to be applying "9999.01.06" - must be rejected rather than trusted.
    wheel = _make_fake_wheel(tmp_path, "9999.01.05")
    with pytest.raises(upd.UpdateError, match="expected"):
        upd.apply_update(wheel, "9999.01.06")

    root = upd._override_root()
    assert not (root / "9999.01.06").exists()
    assert not (root / "active.json").exists()  # nothing was ever activated


def test_apply_update_failure_does_not_disturb_existing_active_version(isolated_appdata, tmp_path):
    good_wheel = _make_fake_wheel(tmp_path, "9999.02.01")
    upd.apply_update(good_wheel, "9999.02.01")

    root = upd._override_root()
    before = json.loads((root / "active.json").read_text())

    bad_wheel = _make_fake_wheel(tmp_path, "9999.02.02", corrupt=True)
    with pytest.raises(upd.UpdateError):
        upd.apply_update(bad_wheel, "9999.02.02")

    after = json.loads((root / "active.json").read_text())
    assert after == before
    assert (root / "9999.02.01" / "yt_dlp" / "version.py").exists()


def test_apply_update_cleans_up_old_versions(isolated_appdata, tmp_path):
    wheel1 = _make_fake_wheel(tmp_path, "9999.03.01")
    upd.apply_update(wheel1, "9999.03.01")
    wheel2 = _make_fake_wheel(tmp_path, "9999.03.02")
    upd.apply_update(wheel2, "9999.03.02")

    root = upd._override_root()
    version_dirs = sorted(
        p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
    )
    assert version_dirs == ["9999.03.02"]  # the older one was cleaned up


# --- activate_pending_override (fast, synthetic, no network) ---------------

def test_activate_pending_override_none_when_nothing_applied(isolated_appdata):
    assert upd.activate_pending_override() is None


def test_activate_pending_override_activates_a_real_applied_update(isolated_appdata, tmp_path, monkeypatch):
    wheel = _make_fake_wheel(tmp_path, "9999.04.01")
    upd.apply_update(wheel, "9999.04.01")

    import sys
    before_path = list(sys.path)
    try:
        result = upd.activate_pending_override()
        assert result == "9999.04.01"
        root = upd._override_root()
        assert str(root / "9999.04.01") == sys.path[0]
    finally:
        sys.path[:] = before_path


def test_activate_pending_override_none_on_corrupt_pointer(isolated_appdata):
    root = upd._override_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "active.json").write_text("not valid json{{{")
    assert upd.activate_pending_override() is None


def test_activate_pending_override_none_when_version_dir_missing(isolated_appdata):
    root = upd._override_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "active.json").write_text(json.dumps({"version": "9999.05.01"}))
    assert upd.activate_pending_override() is None


def test_activate_pending_override_none_when_tampered(isolated_appdata):
    """The version.py actually found on disk doesn't match what the
    pointer file claims - must not be trusted."""
    root = upd._override_root()
    bad_dir = root / "9999.06.01" / "yt_dlp"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "version.py").write_text("__version__ = '1111.11.11'")
    (root / "active.json").write_text(json.dumps({"version": "9999.06.01"}))
    assert upd.activate_pending_override() is None


# --- full real end-to-end pipeline (real network, skip-safe) ---------------

@requires_network
def test_full_real_update_pipeline_end_to_end(isolated_appdata):
    """
    The most important test in this file: exercises the entire real
    pipeline against the actual PyPI/files.pythonhosted.org, exactly as
    it runs in the app - check, download+verify, apply, then activate in
    a way that a real `import yt_dlp` afterward would actually resolve
    to the updated version. Not mocked at any step.
    """
    info = upd.check_for_update(current_version="2020.01.01")
    assert info is not None

    wheel_path = upd.download_update(info)
    upd.apply_update(wheel_path, info.latest_version)

    activated = upd.activate_pending_override()
    assert activated == info.latest_version

    root = upd._override_root()
    version_file = root / info.latest_version / "yt_dlp" / "version.py"
    assert version_file.exists()
    content = version_file.read_text()
    assert "__version__" in content
