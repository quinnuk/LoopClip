import json

import settings


def test_defaults_when_no_file_exists(isolated_appdata):
    data = settings.load_settings()
    assert data == settings.DEFAULTS
    # load_settings() must return a copy, not the live DEFAULTS dict,
    # or a caller mutating it would corrupt every future load.
    data["quality"] = "1080p"
    assert settings.DEFAULTS["quality"] == "best"


def test_missing_keys_filled_from_defaults(isolated_appdata):
    path = isolated_appdata / "LoopClip" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Simulate a settings file saved by an older app version that predates
    # some of today's keys.
    path.write_text(json.dumps({"quality": "1080p"}), encoding="utf-8")

    data = settings.load_settings()
    assert data["quality"] == "1080p"          # preserved
    assert data["similarity"] == settings.DEFAULTS["similarity"]  # filled in
    assert data["auto_loop"] == settings.DEFAULTS["auto_loop"]    # filled in


def test_corrupted_json_falls_back_to_defaults(isolated_appdata):
    path = isolated_appdata / "LoopClip" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{this is not valid json", encoding="utf-8")

    data = settings.load_settings()
    assert data == settings.DEFAULTS


def test_unreadable_directory_does_not_crash(isolated_appdata):
    # settings.json exists but is empty (not even valid JSON) - shouldn't
    # raise, should fall back to defaults.
    path = isolated_appdata / "LoopClip" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")

    data = settings.load_settings()
    assert data == settings.DEFAULTS


def test_save_then_load_roundtrip(isolated_appdata):
    data = settings.load_settings()
    data["quality"] = "4k_hdr"
    data["similarity"] = 87
    data["recent_output_folders"] = ["C:\\a", "C:\\b"]
    settings.save_settings(data)

    reloaded = settings.load_settings()
    assert reloaded["quality"] == "4k_hdr"
    assert reloaded["similarity"] == 87
    assert reloaded["recent_output_folders"] == ["C:\\a", "C:\\b"]


def test_save_is_atomic_no_leftover_temp_files(isolated_appdata):
    data = settings.load_settings()
    settings.save_settings(data)

    base = isolated_appdata / "LoopClip"
    leftovers = [p.name for p in base.iterdir() if p.name.startswith(".settings_")]
    assert leftovers == []


def test_aquarium_downloader_migration(isolated_appdata):
    old_dir = isolated_appdata / settings._OLD_APP_NAME
    old_dir.mkdir(parents=True, exist_ok=True)
    old_settings = dict(settings.DEFAULTS)
    old_settings["quality"] = "1080p"
    old_settings["last_url"] = "https://youtu.be/legacy"
    (old_dir / "settings.json").write_text(json.dumps(old_settings), encoding="utf-8")

    data = settings.load_settings()
    assert data["quality"] == "1080p"
    assert data["last_url"] == "https://youtu.be/legacy"


def test_migration_does_not_overwrite_existing_new_settings(isolated_appdata):
    old_dir = isolated_appdata / settings._OLD_APP_NAME
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "settings.json").write_text(
        json.dumps({**settings.DEFAULTS, "quality": "1080p"}), encoding="utf-8"
    )

    new_dir = isolated_appdata / settings.APP_NAME
    new_dir.mkdir(parents=True, exist_ok=True)
    (new_dir / "settings.json").write_text(
        json.dumps({**settings.DEFAULTS, "quality": "4k_hdr"}), encoding="utf-8"
    )

    data = settings.load_settings()
    # The already-existing new-location settings must win - migration is
    # one-time-only and must never clobber a real, current config.
    assert data["quality"] == "4k_hdr"
