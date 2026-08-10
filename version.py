"""
version.py
----------
Single source of truth for LoopClip's version number.

Import this wherever a version string is needed - GUI title/About dialog,
CLI (--version), log output, build scripts - instead of hardcoding the
number separately in each place. Bumping a release is then a one-line
change here rather than a hunt through the codebase.
"""

__version__ = "2.0.0"
