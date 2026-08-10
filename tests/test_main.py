import shutil

import pytest

tkinter_available = True
try:
    import tkinter  # noqa: F401
except ImportError:
    tkinter_available = False

requires_tkinter = pytest.mark.skipif(
    not tkinter_available, reason="tkinter not available on this machine"
)


@requires_tkinter
def test_safe_float_valid_number():
    import main
    assert main._safe_float("55.5", 98.0) == 55.5


@requires_tkinter
def test_safe_float_falls_back_on_garbage():
    """Regression test: QueueItem construction and settings-saving both
    previously called float() directly on the similarity entry's raw
    text, unconditionally - even when auto-loop was off and the field
    was never validated - crashing the 'Add to Queue' button handler on
    non-numeric input. _safe_float() must never raise."""
    import main
    assert main._safe_float("not a number", 98.0) == 98.0


@requires_tkinter
def test_safe_float_falls_back_on_empty_string():
    import main
    assert main._safe_float("", 98.0) == 98.0


@requires_tkinter
def test_safe_float_falls_back_on_none():
    import main
    assert main._safe_float(None, 98.0) == 98.0
