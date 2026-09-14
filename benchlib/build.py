"""Rebuild and build status. P0 only reports whether a build stamp exists; P2 completes it."""

from __future__ import annotations

from benchlib.config import Paths

NO_BUILD = "no build yet"


def build_status(paths: Paths) -> str:
    """Status line for the current files compared with the last build stamp."""
    if not paths.build_stamp.exists():
        return NO_BUILD
    raise NotImplementedError("comparing files with the build stamp is implemented in P2")
