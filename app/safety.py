"""Read-only enforcement for the source corpus.

The corpus lives on a shared Google Drive that colleagues depend on, so "never
modify the source" has to be an enforced invariant rather than a convention.
:class:`SourceTreeGuard` is the single choke point that makes it one:

* :meth:`SourceTreeGuard.open_for_reading` is the only sanctioned way to read a
  corpus file, and it refuses any file mode capable of mutating.
* :meth:`SourceTreeGuard.assert_writable` is called before every disk write, so
  a mis-configured ``DATA_DIR`` cannot silently land inside the corpus.

The shared instance is published as :data:`SOURCE_GUARD`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO

from .config import SETTINGS, Settings


class SourceWriteAttempt(RuntimeError):
    """Raised when any code path attempts to write inside the source tree."""


class SourceTreeGuard:
    """Enforces that the configured source tree is only ever read.

    The guard resolves symlinks and relative segments before comparing paths,
    so tricks such as ``data/../G:/Shared drives/...`` cannot slip past it.
    """

    #: File-mode characters that permit mutation of the opened file.
    MUTATING_MODE_FLAGS = ("w", "a", "x", "+")

    def __init__(self, settings: Settings) -> None:
        """Bind the guard to the source tree described by ``settings``."""
        self._settings = settings

    def _resolve(self, path: os.PathLike[str] | str) -> Path:
        """Expand ``~`` and normalise a path to an absolute, symlink-free form."""
        return Path(path).expanduser().resolve()

    def is_inside_source(self, path: os.PathLike[str] | str) -> bool:
        """Return True when ``path`` is the source tree or lives beneath it."""
        try:
            source_root = self._resolve(self._settings.source_directory)
            candidate = self._resolve(path)
        except (OSError, ValueError):
            # An unresolvable path cannot be proven safe, but neither is it a
            # source path; callers treat this as "not inside".
            return False
        return candidate == source_root or source_root in candidate.parents

    def assert_writable(self, path: os.PathLike[str] | str) -> Path:
        """Validate a write target and return its resolved form.

        Raises:
            SourceWriteAttempt: if ``path`` falls inside the source tree.
        """
        resolved = self._resolve(path)
        if self.is_inside_source(resolved):
            raise SourceWriteAttempt(
                f"Refusing to write inside the read-only source tree: {resolved}"
            )
        return resolved

    def open_for_reading(self, path: os.PathLike[str] | str, mode: str = "rb", **options) -> IO:
        """Open a corpus file for reading, rejecting any mutating mode.

        Raises:
            SourceWriteAttempt: if ``mode`` could modify the file.
        """
        if any(flag in mode for flag in self.MUTATING_MODE_FLAGS):
            raise SourceWriteAttempt(
                f"Source files may only be opened read-only (received mode {mode!r}): {path}"
            )
        return open(path, mode, **options)

    def verify_data_directory_isolation(self) -> None:
        """Startup check that generated artefacts cannot land in the corpus.

        Raises:
            SourceWriteAttempt: if the data directory overlaps the source tree.
        """
        self.assert_writable(self._settings.data_directory)


#: Process-wide guard bound to the active settings.
SOURCE_GUARD = SourceTreeGuard(SETTINGS)
