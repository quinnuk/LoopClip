"""
loop_detector.py
-----------------
Automatic seamless-loop detection, mirroring LoopyCut's approach: extract
frames from a search window, compare candidate (start, end) frame pairs
within an allowed loop-length range using a chosen method, and rank the
results by how visually similar the start and end frames are.

Methods (matching LoopyCut's naming):
  "hash"       - perceptual difference-hash comparison. Fastest, best for
                 near-identical frames (e.g. static screen recordings).
  "histogram"  - color histogram correlation. Good when lighting/color is
                 the main signal (less sensitive to small motion).
  "ssim"       - structural similarity. Best at catching structural
                 changes hash/histogram would miss.
  "combined"   - weighted SSIM + histogram blend (default, matches
                 LoopyCut's default method).

CPU-only. LoopyCut's GPU acceleration path targets Apple Silicon via
Numba, which doesn't apply here - a CUDA-accelerated backend (cupy/torch)
could be added later for very long videos by swapping out the similarity
functions below; every call site already goes through _SIMILARITY_FUNCS.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

import cv2
import numpy as np

try:
    from skimage.metrics import structural_similarity as _skimage_ssim
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

METHODS = ("combined", "ssim", "histogram", "hash")
ANALYSIS_MAX_DIM = 480  # frames are downscaled to this max dimension before comparison


class LoopDetectionError(RuntimeError):
    """Raised when frames can't be extracted, or no loop meets the threshold."""


@dataclass
class LoopCandidate:
    start: float      # seconds, relative to the start of the analyzed clip
    end: float        # seconds
    duration: float
    quality: float     # 0-1, structural quality of the match
    similarity: float  # 0-1, similarity score under the requested method
    score: float        # 0-1, final ranking score (similarity + quality blend)


@dataclass
class _Frame:
    time: float
    small: np.ndarray       # downscaled BGR frame - histogram/ssim input
    gray: np.ndarray        # downscaled grayscale - hash input
    hash_bits: np.ndarray    # 64-bit perceptual hash, used for cheap pre-filtering


def _resize_max_dim(frame: np.ndarray, max_dim: int) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = max_dim / max(h, w)
    if scale >= 1:
        return frame
    return cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _dhash(gray: np.ndarray, hash_size: int = 8) -> np.ndarray:
    """Difference hash: cheap, effective near-duplicate pre-filter."""
    small = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    return (small[:, 1:] > small[:, :-1]).flatten()


def extract_frames(
    video_path: str,
    start: float = 0.0,
    stop: Optional[float] = None,
    downsample: int = 1,
    max_dim: int = ANALYSIS_MAX_DIM,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[_Frame]:
    """
    Extracts frames from [start, stop) of video_path, keeping every Nth
    frame (downsample) and downscaling each to max_dim on its long edge,
    so memory/compute stay bounded regardless of source resolution.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise LoopDetectionError(f"Could not open video for analysis: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        start_frame = max(0, int(start * fps))
        stop_frame = int(stop * fps) if stop is not None else total_frames
        if total_frames:
            stop_frame = min(stop_frame, total_frames)

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        frames: List[_Frame] = []
        idx = start_frame
        expected = max(1, (stop_frame - start_frame) // max(1, downsample))
        while stop_frame <= 0 or idx < stop_frame:
            ok, frame = cap.read()
            if not ok:
                break
            if (idx - start_frame) % downsample == 0:
                small = _resize_max_dim(frame, max_dim)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                frames.append(_Frame(time=idx / fps, small=small, gray=gray, hash_bits=_dhash(gray)))
                if progress_callback is not None:
                    progress_callback(len(frames), expected)
            idx += 1
    finally:
        cap.release()

    if len(frames) < 2:
        raise LoopDetectionError(
            "Not enough frames extracted for loop analysis - the search window "
            "may be too short, or the video failed to decode."
        )
    return frames


def _hash_similarity(a: _Frame, b: _Frame) -> float:
    """1.0 = identical, 0.0 = maximally different (normalized Hamming distance)."""
    dist = int(np.count_nonzero(a.hash_bits != b.hash_bits))
    return 1.0 - (dist / a.hash_bits.size)


def _histogram_similarity(a: _Frame, b: _Frame) -> float:
    hist_a = cv2.calcHist([a.small], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
    hist_b = cv2.calcHist([b.small], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    correlation = cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL)
    return max(0.0, min(1.0, (correlation + 1) / 2))  # [-1, 1] -> [0, 1]


def _ssim_similarity(a: _Frame, b: _Frame) -> float:
    if not HAS_SKIMAGE:
        return _hash_similarity(a, b)  # graceful degrade if scikit-image is missing
    score = _skimage_ssim(a.gray, b.gray)
    return max(0.0, min(1.0, score))


def _combined_similarity(a: _Frame, b: _Frame) -> float:
    # Matches LoopyCut's default "combined" method: structure + color/lighting.
    return 0.6 * _ssim_similarity(a, b) + 0.4 * _histogram_similarity(a, b)


_SIMILARITY_FUNCS: dict = {
    "hash": _hash_similarity,
    "histogram": _histogram_similarity,
    "ssim": _ssim_similarity,
    "combined": _combined_similarity,
}


def find_loop_candidates(
    frames: List[_Frame],
    method: str = "combined",
    similarity_threshold: float = 98.0,
    min_loop_seconds: Optional[float] = None,
    max_loop_seconds: Optional[float] = None,
    max_candidates: int = 5,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[LoopCandidate]:
    """
    Compares every valid (start, end) frame pair within
    [min_loop_seconds, max_loop_seconds] and returns the best-scoring
    non-overlapping candidates, best first.
    """
    if method not in _SIMILARITY_FUNCS:
        raise ValueError(f"Unknown method '{method}'. Choose from: {', '.join(METHODS)}")
    sim_func = _SIMILARITY_FUNCS[method]
    threshold = similarity_threshold / 100.0

    total_span = frames[-1].time - frames[0].time
    min_len = min_loop_seconds if min_loop_seconds else max(1.0, total_span * 0.1)
    max_len = max_loop_seconds if max_loop_seconds else total_span

    # Cheap hash pre-filter before running the (possibly pricier) requested
    # method - mirrors LoopyCut's "hybrid" idea of hash pre-filter + verify.
    prefilter_threshold = max(0.0, threshold - 0.15)

    n = len(frames)
    raw: List[LoopCandidate] = []
    for i in range(n):
        if progress_callback is not None and i % max(1, n // 50) == 0:
            progress_callback(i, n)
        for j in range(i + 1, n):
            duration = frames[j].time - frames[i].time
            if duration < min_len:
                continue
            if duration > max_len:
                break  # frames are time-ordered - later j only grows duration
            if _hash_similarity(frames[i], frames[j]) < prefilter_threshold:
                continue
            similarity = sim_func(frames[i], frames[j])
            if similarity < threshold:
                continue
            quality = similarity if method == "ssim" else _ssim_similarity(frames[i], frames[j])
            score = 0.5 * similarity + 0.5 * quality
            raw.append(LoopCandidate(
                start=frames[i].time, end=frames[j].time, duration=duration,
                quality=quality, similarity=similarity, score=score,
            ))

    raw.sort(key=lambda c: c.score, reverse=True)

    selected: List[LoopCandidate] = []
    for cand in raw:
        overlaps = any(
            not (cand.end <= existing.start or cand.start >= existing.end)
            for existing in selected
        )
        if not overlaps:
            selected.append(cand)
        if len(selected) >= max_candidates:
            break
    return selected


def find_best_loop(
    video_path: str,
    start: float = 0.0,
    stop: Optional[float] = None,
    method: str = "combined",
    similarity_threshold: float = 98.0,
    min_loop_seconds: Optional[float] = None,
    max_loop_seconds: Optional[float] = None,
    downsample: int = 1,
    max_dim: int = ANALYSIS_MAX_DIM,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> LoopCandidate:
    """
    High-level entry point: extracts frames, finds candidates, returns the
    single best one. Raises LoopDetectionError if nothing meets the
    similarity threshold - same "no loops found" case LoopyCut's README
    troubleshooting section covers (lower --similarity, try another
    --method, or widen the search window).
    """
    frames = extract_frames(
        video_path, start=start, stop=stop, downsample=downsample, max_dim=max_dim,
        progress_callback=(lambda i, n: progress_callback("extract", i, n)) if progress_callback else None,
    )
    candidates = find_loop_candidates(
        frames, method=method, similarity_threshold=similarity_threshold,
        min_loop_seconds=min_loop_seconds, max_loop_seconds=max_loop_seconds,
        progress_callback=(lambda i, n: progress_callback("compare", i, n)) if progress_callback else None,
    )
    if not candidates:
        raise LoopDetectionError(
            "No seamless loop found at this similarity threshold. Try lowering "
            "the similarity, widening the search window, or a different method."
        )
    return candidates[0]
