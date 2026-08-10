import numpy as np
import pytest

import loop_detector as ld
from conftest import requires_ffmpeg


def _make_frame(time, seed=0, size=32, noise=0):
    """Builds a synthetic _Frame directly (no video decode needed) - a
    reproducible pseudo-random spatial pattern keyed by `seed`, optionally
    perturbed with per-frame noise. Solid-color frames are deliberately
    avoided here: dhash only looks at *relative* brightness between
    adjacent pixels, so two different solid colors hash identically
    (there's no internal contrast to compare) and would make every
    "different frame" test degenerate. A seeded random pattern gives real,
    comparable spatial structure instead.
    """
    pattern_rng = np.random.default_rng(seed)
    base = pattern_rng.integers(0, 256, size=(size, size, 3)).astype(np.uint8)
    if noise:
        noise_rng = np.random.default_rng(int(time * 1000) + seed * 7919)
        perturb = noise_rng.integers(-noise, noise + 1, size=base.shape)
        base = np.clip(base.astype(int) + perturb, 0, 255).astype(np.uint8)
    gray = np.mean(base, axis=2).astype(np.uint8)
    hash_bits = ld._dhash(gray)
    return ld._Frame(time=time, small=base, gray=gray, hash_bits=hash_bits)


# --- similarity functions ---------------------------------------------------

def test_hash_similarity_identical_frames_is_1():
    f1 = _make_frame(0.0, seed=1)
    f2 = _make_frame(1.0, seed=1)
    assert ld._hash_similarity(f1, f2) == pytest.approx(1.0)


def test_hash_similarity_very_different_frames_is_low():
    # A single random pattern pair can occasionally land close to chance
    # agreement (~50%) just by luck, so average across several independent
    # pairs for a statistically stable result instead of asserting on one.
    scores = [
        ld._hash_similarity(_make_frame(0.0, seed=seed), _make_frame(1.0, seed=seed + 1))
        for seed in range(0, 40, 2)
    ]
    assert (sum(scores) / len(scores)) < 0.65


def test_histogram_similarity_identical_frames_is_1():
    f1 = _make_frame(0.0, seed=5)
    f2 = _make_frame(1.0, seed=5)
    assert ld._histogram_similarity(f1, f2) == pytest.approx(1.0, abs=0.01)


def test_ssim_similarity_identical_frames_is_1():
    f1 = _make_frame(0.0, seed=7)
    f2 = _make_frame(1.0, seed=7)
    assert ld._ssim_similarity(f1, f2) == pytest.approx(1.0, abs=0.01)


def test_combined_similarity_is_between_0_and_1():
    f1 = _make_frame(0.0, seed=3, noise=20)
    f2 = _make_frame(1.0, seed=9, noise=20)
    score = ld._combined_similarity(f1, f2)
    assert 0.0 <= score <= 1.0


# --- find_loop_candidates (constructed frame sequences) ---------------------

def _static_scene_frames(n=40, fps=10, size=32):
    """n frames of a near-static scene (same underlying pattern, tiny
    per-frame noise, like a real static shot with sensor/compression
    noise) - should produce a strong loop match almost anywhere."""
    return [_make_frame(i / fps, seed=100, size=size, noise=2) for i in range(n)]


def test_find_loop_candidates_static_scene_finds_a_match():
    frames = _static_scene_frames(n=60, fps=10)  # 6 seconds of near-static footage
    candidates = ld.find_loop_candidates(
        frames, method="hash", similarity_threshold=90.0,
        min_loop_seconds=1.0, max_loop_seconds=5.0,
    )
    assert len(candidates) > 0
    assert candidates[0].similarity >= 0.90


def test_find_loop_candidates_no_match_returns_empty():
    # Every frame is a totally different random pattern - nothing should
    # be anywhere close to a 99.5% match.
    frames = [_make_frame(i / 10, seed=i, size=32) for i in range(30)]
    candidates = ld.find_loop_candidates(
        frames, method="hash", similarity_threshold=99.5,
        min_loop_seconds=0.5, max_loop_seconds=2.0,
    )
    assert candidates == []


def test_find_loop_candidates_respects_min_max_duration():
    frames = _static_scene_frames(n=100, fps=10)  # 10 seconds
    candidates = ld.find_loop_candidates(
        frames, method="hash", similarity_threshold=90.0,
        min_loop_seconds=3.0, max_loop_seconds=4.0,
    )
    for c in candidates:
        assert 3.0 - 1e-6 <= c.duration <= 4.0 + 1e-6


def test_find_loop_candidates_invalid_method_raises():
    frames = _static_scene_frames(n=10)
    with pytest.raises(ValueError):
        ld.find_loop_candidates(frames, method="not_a_real_method")


def test_find_loop_candidates_min_ge_max_raises():
    frames = _static_scene_frames(n=10)
    with pytest.raises(ld.LoopDetectionError):
        ld.find_loop_candidates(
            frames, min_loop_seconds=5.0, max_loop_seconds=5.0,
        )


def test_find_loop_candidates_returns_non_overlapping_results():
    frames = _static_scene_frames(n=80, fps=10)
    candidates = ld.find_loop_candidates(
        frames, method="hash", similarity_threshold=90.0,
        min_loop_seconds=1.0, max_loop_seconds=3.0, max_candidates=5,
    )
    for i, c1 in enumerate(candidates):
        for c2 in candidates[i + 1:]:
            overlaps = not (c1.end <= c2.start or c1.start >= c2.end)
            assert not overlaps


# --- extract_frames (needs real ffmpeg-decodable video) ---------------------

@requires_ffmpeg
def test_extract_frames_short_video_raises(make_test_clip):
    # A single-frame-ish clip shouldn't have enough frames for analysis.
    clip = make_test_clip("tiny.mp4", width=64, height=64, fps=1, duration=1)
    with pytest.raises(ld.LoopDetectionError):
        ld.extract_frames(clip, downsample=100)  # downsample so hard nothing survives


@requires_ffmpeg
def test_extract_frames_downsample_reduces_frame_count(make_test_clip):
    clip = make_test_clip("ds.mp4", width=64, height=64, fps=10, duration=2)
    full = ld.extract_frames(clip, downsample=1)
    sparse = ld.extract_frames(clip, downsample=4)
    assert len(sparse) < len(full)


@requires_ffmpeg
def test_extract_frames_respects_start_stop_window(make_test_clip):
    clip = make_test_clip("window.mp4", width=64, height=64, fps=10, duration=4)
    frames = ld.extract_frames(clip, start=1.0, stop=2.0)
    assert all(0.9 <= f.time <= 2.1 for f in frames)


@requires_ffmpeg
def test_find_best_loop_on_real_static_clip(make_test_clip):
    clip = make_test_clip("static.mp4", width=64, height=64, fps=10, duration=5, pattern="color=c=gray")
    best = ld.find_best_loop(
        clip, method="hash", similarity_threshold=90.0,
        min_loop_seconds=1.0, max_loop_seconds=3.0,
    )
    assert best.similarity >= 0.90
    assert 1.0 - 1e-6 <= best.duration <= 3.0 + 1e-6


@requires_ffmpeg
def test_find_best_loop_no_match_raises_with_helpful_message(make_test_clip):
    # testsrc has continuously scrolling/changing content, and a strict
    # near-100% threshold over a short clip should find nothing usable.
    clip = make_test_clip("moving.mp4", width=64, height=64, fps=10, duration=3, pattern="testsrc")
    with pytest.raises(ld.LoopDetectionError):
        ld.find_best_loop(
            clip, method="hash", similarity_threshold=99.99,
            min_loop_seconds=0.5, max_loop_seconds=1.5,
        )
