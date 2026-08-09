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

Search strategy
----------------
Comparing every extracted frame against every other frame is O(n^2) in
the number of frames. With "Analyze: Every frame" over a multi-minute
search window that's easily many thousands of frames, so n^2 lands in
the millions of pairs - and for calm/slow-moving footage (aquariums,
static shots) a large fraction of those pairs pass even a fairly tight
pre-filter, each one triggering a genuinely expensive SSIM/histogram
comparison. That combination is what made this step take a very long
time (not actually hung - just doing an enormous number of comparisons).

To keep runtime bounded regardless of clip length or the "Analyze"
setting, the search runs in two stages:
  1. Coarse pass - the frame list is internally sub-sampled down to a
     capped size (_MAX_COARSE_FRAMES) and every valid pair within that
     capped set is screened using a fully-vectorized (NumPy) hash
     comparison - fast even at hundreds of frames, and totally
     independent of how many frames were actually extracted.
  2. Refine pass - only the best few coarse matches (_COARSE_CANDIDATES_TO_REFINE)
     get a frame-accurate refinement, searching a small local window
     (bounded by the coarse stride) around each with the requested,
     potentially more expensive similarity method.
This turns an unbounded O(n^2) full-resolution search into a small,
fixed-size problem, so detection time no longer scales with clip length
or frame-analysis density.
"""

import math
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

# Default floor for loop length when the caller doesn't specify
# min_loop_seconds. Matching frames get statistically much easier to find
# the shorter the gap between them, so leaving this at a small fraction of
# the search window (as an earlier version of this file did) biases every
# search toward the shortest duration that clears the similarity
# threshold - not necessarily what anyone actually wants. 120s is a more
# useful general-purpose default for a "looping background clip" use case.
DEFAULT_MIN_LOOP_SECONDS = 120.0

# Size of the frame set the coarse search stage runs its O(n^2) comparison
# against. Capped regardless of clip length or the "Analyze" setting, so
# that stage is always a fast, fixed-size problem rather than one that
# scales with how many frames were extracted.
_MAX_COARSE_FRAMES = 500

# How many of the coarse stage's best matches get a fine, frame-accurate
# refinement pass. Kept small since each one costs real (SSIM-grade) work.
_COARSE_CANDIDATES_TO_REFINE = 12


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


def _hash_similarity_matrix(frames_a: List[_Frame], frames_b: List[_Frame]) -> np.ndarray:
    """
    Vectorized Hamming-similarity matrix between two (small) frame lists,
    shaped (len(frames_a), len(frames_b)). Only ever called on frame lists
    capped to a few hundred frames at most, so the full matrix stays cheap
    in both time and memory.
    """
    bits_a = np.stack([f.hash_bits for f in frames_a])
    bits_b = np.stack([f.hash_bits for f in frames_b])
    hamming = np.count_nonzero(bits_a[:, None, :] != bits_b[None, :, :], axis=2)
    return 1.0 - (hamming / bits_a.shape[1])


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


def _coarse_subsample(frames: List[_Frame]):
    """Sub-samples frames down to at most _MAX_COARSE_FRAMES, evenly spaced
    by index. Returns (coarse_frames, stride) - stride is how many original
    frames separate each coarse sample, and is also the refinement margin
    used afterward (the true best pair can be up to `stride` frames away
    from a coarse index in either direction)."""
    n = len(frames)
    stride = max(1, math.ceil(n / _MAX_COARSE_FRAMES))
    return frames[::stride], stride


def find_loop_candidates(
    frames: List[_Frame],
    method: str = "combined",
    similarity_threshold: float = 98.0,
    min_loop_seconds: Optional[float] = None,
    max_loop_seconds: Optional[float] = None,
    max_candidates: int = 5,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    stats: Optional[dict] = None,
) -> List[LoopCandidate]:
    """
    Finds the best-scoring non-overlapping (start, end) loop pairs within
    [min_loop_seconds, max_loop_seconds], best first.

    Runs as a coarse-then-refine search (see module docstring) rather than
    an exhaustive all-pairs comparison, so runtime stays bounded regardless
    of how many frames were extracted.

    If `stats` is given, it's populated with stats["best_similarity"] - the
    highest similarity score seen among all pairs actually evaluated with
    the requested method, whether or not it cleared `similarity_threshold`.
    Doesn't change the return value; lets callers report a useful "closest
    match was X%" message instead of a bare "nothing found" when the clip
    genuinely doesn't have a match this good (common for real footage,
    where subtle motion/compression noise keeps even a great loop point
    well under a strict threshold like 98%).
    """
    if method not in _SIMILARITY_FUNCS:
        raise ValueError(f"Unknown method '{method}'. Choose from: {', '.join(METHODS)}")
    sim_func = _SIMILARITY_FUNCS[method]
    threshold = similarity_threshold / 100.0
    prefilter_threshold = max(0.0, threshold - 0.15)

    n = len(frames)
    total_span = frames[-1].time - frames[0].time
    if min_loop_seconds is not None:
        min_len = min_loop_seconds
    else:
        # Auto default: DEFAULT_MIN_LOOP_SECONDS (120s) is fine for longer
        # clips, but must never be allowed to collapse the search window
        # to zero. For any clip shorter than ~4 minutes, capping at half
        # the total span (instead of the raw clip length) guarantees at
        # least that much room remains between min_len and max_len, so a
        # short clip - e.g. a 90s video - can still find a loop instead of
        # always hitting the "leaves no room to search" error below.
        min_len = min(DEFAULT_MIN_LOOP_SECONDS, total_span * 0.5)
    max_len = max_loop_seconds if max_loop_seconds else total_span

    if min_len >= max_len:
        raise LoopDetectionError(
            f"The minimum loop length ({min_len:.0f}s) leaves no room to search within "
            f"this {total_span:.0f}s window - the detector would need the very first and "
            "very last analyzed frame to match almost exactly, which essentially never "
            "happens. Widen the search window so it's comfortably longer than the minimum "
            "loop length you want (e.g. at least 30-60s of extra slack above it)."
        )

    # --- Stage 1: coarse search --------------------------------------------
    coarse_frames, stride = _coarse_subsample(frames)
    cn = len(coarse_frames)
    coarse_times = np.array([f.time for f in coarse_frames])
    duration_matrix = coarse_times[None, :] - coarse_times[:, None]
    valid = (duration_matrix >= min_len) & (duration_matrix <= max_len)
    valid &= np.triu(np.ones((cn, cn), dtype=bool), k=1)

    if progress_callback is not None:
        progress_callback(0, 100)

    if not valid.any():
        if stats is not None:
            stats["best_similarity"] = 0.0
        return []

    coarse_hash_sim = _hash_similarity_matrix(coarse_frames, coarse_frames)
    shortlist_mask = valid & (coarse_hash_sim >= prefilter_threshold)
    shortlist_idx = np.argwhere(shortlist_mask)
    if shortlist_idx.size == 0:
        # Nothing was even similar enough to pass the cheap hash pre-filter -
        # report the best coarse hash score seen as a rough (likely
        # optimistic) upper bound, so the error message still has something
        # concrete rather than looking identical to any other failure.
        if stats is not None:
            stats["best_similarity"] = float(coarse_hash_sim[valid].max()) if valid.any() else 0.0
        return []

    best_similarity = 0.0

    scores = coarse_hash_sim[shortlist_idx[:, 0], shortlist_idx[:, 1]]
    order = np.argsort(-scores)
    top_coarse = shortlist_idx[order[:_COARSE_CANDIDATES_TO_REFINE]]

    if progress_callback is not None:
        progress_callback(50, 100)

    # --- Stage 2: fine, frame-accurate refinement around each coarse hit ---
    raw: List[LoopCandidate] = []
    for count, (ci, cj) in enumerate(top_coarse):
        if progress_callback is not None:
            progress_callback(50 + int(50 * count / max(1, len(top_coarse))), 100)

        i_center = int(ci) * stride
        j_center = int(cj) * stride
        i_start, i_end = max(0, i_center - stride), min(n, i_center + stride + 1)
        j_start, j_end = max(0, j_center - stride), min(n, j_center + stride + 1)

        local_i = frames[i_start:i_end]
        local_j = frames[j_start:j_end]
        if not local_i or not local_j:
            continue

        local_hash_sim = _hash_similarity_matrix(local_i, local_j)
        local_mask = local_hash_sim >= prefilter_threshold
        for li, lj in zip(*np.where(local_mask)):
            i = i_start + int(li)
            j = j_start + int(lj)
            if j <= i:
                continue
            duration = frames[j].time - frames[i].time
            if duration < min_len or duration > max_len:
                continue
            similarity = sim_func(frames[i], frames[j])
            best_similarity = max(best_similarity, similarity)
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

    if stats is not None:
        stats["best_similarity"] = best_similarity
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
    stats: dict = {}
    candidates = find_loop_candidates(
        frames, method=method, similarity_threshold=similarity_threshold,
        min_loop_seconds=min_loop_seconds, max_loop_seconds=max_loop_seconds,
        progress_callback=(lambda i, n: progress_callback("compare", i, n)) if progress_callback else None,
        stats=stats,
    )
    if not candidates:
        best_similarity = stats.get("best_similarity", 0.0)
        if best_similarity > 0:
            suggested = max(50, round(best_similarity * 100) - 2)
            raise LoopDetectionError(
                f"No seamless loop found at {similarity_threshold:.0f}% similarity. "
                f"The closest match in this clip was {best_similarity * 100:.1f}% similar - "
                f"try lowering the similarity to around {suggested}%, widening the search "
                "window, or a different method."
            )
        raise LoopDetectionError(
            "No seamless loop found at this similarity threshold. Try lowering "
            "the similarity, widening the search window, or a different method."
        )
    return candidates[0]