"""
utils.py
--------
Small constants shared across the UI layer. Kept separate from ui.py so
other modules (and any future dialog helpers) can use the same palette
without importing the whole application window.
"""

# Custom palette: lifted charcoal background (instead of near-black) with a
# teal accent, kept distinct from the amber "Pause" and red "danger" colors
# used for status/action buttons so they stay easy to tell apart.
COLOR_BG = "#1c1c1e"
COLOR_PANEL = "#26262a"
COLOR_ACCENT = "#0f6e56"
COLOR_ACCENT_HOVER = "#0c5744"
COLOR_DANGER = "#8a3b34"
COLOR_DANGER_HOVER = "#6f2f2a"

REPO_URL = "https://github.com/quinnuk/VideoDownloader"


def parse_range_spec(spec: str, count: int) -> list[int]:
    """Parse a playlist range spec like "1,3,7,10-15" into a sorted list of
    0-based indices within [0, count). Unparseable or out-of-range pieces
    are silently skipped rather than raising, since this drives UI selection.
    """
    indices: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, _, end_str = part.partition("-")
            try:
                start, end = int(start_str), int(end_str)
            except ValueError:
                continue
            for n in range(start, end + 1):
                if 1 <= n <= count:
                    indices.add(n - 1)
        else:
            try:
                n = int(part)
            except ValueError:
                continue
            if 1 <= n <= count:
                indices.add(n - 1)
    return sorted(indices)
