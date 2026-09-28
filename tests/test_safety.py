"""Tests for the read-only guarantee over the source corpus.

These are the highest-value tests in the suite: the corpus lives on a shared
drive that colleagues depend on, and a regression here would be silent and
destructive. Each test pins one way a write could reach the source tree.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.safety import SourceTreeGuard, SourceWriteAttempt


class TestPathContainment:
    """Deciding whether a path falls inside the protected source tree."""

    def test_recognises_the_source_root_itself(self, guard, isolated_settings):
        assert guard.is_inside_source(isolated_settings.source_directory) is True

    def test_recognises_a_nested_file(self, guard, isolated_settings):
        nested_file = isolated_settings.source_directory / "a" / "b" / "berkas.pdf"
        assert guard.is_inside_source(nested_file) is True

    def test_rejects_an_unrelated_directory(self, guard, isolated_settings):
        assert guard.is_inside_source(isolated_settings.data_directory) is False

    def test_resolves_relative_traversal_before_deciding(self, guard, isolated_settings):
        """A path that walks out of data/ and back into the corpus is caught."""
        disguised = isolated_settings.data_directory / ".." / "corpus" / "berkas.pdf"
        assert guard.is_inside_source(disguised) is True

    def test_sibling_directory_with_shared_prefix_is_outside(self, guard, isolated_settings):
        """`corpus-backup` must not be mistaken for a child of `corpus`."""
        sibling = isolated_settings.source_directory.parent / "corpus-backup" / "x.pdf"
        assert guard.is_inside_source(sibling) is False


class TestWriteRefusal:
    """Guarding write targets."""

    def test_allows_writing_inside_the_data_directory(self, guard, isolated_settings):
        target = isolated_settings.data_directory / "index.db"
        assert guard.assert_writable(target) == target.resolve()

    def test_refuses_writing_inside_the_source_tree(self, guard, isolated_settings):
        target = isolated_settings.source_directory / "hasil.txt"
        with pytest.raises(SourceWriteAttempt):
            guard.assert_writable(target)

    def test_refuses_writing_to_the_source_root(self, guard, isolated_settings):
        with pytest.raises(SourceWriteAttempt):
            guard.assert_writable(isolated_settings.source_directory)

    def test_data_directory_isolation_check_passes_when_separate(self, guard):
        guard.verify_data_directory_isolation()

    def test_data_directory_isolation_check_fails_when_nested(self, isolated_settings):
        """A DATA_DIR mis-configured to sit inside the corpus is rejected."""
        isolated_settings.data_directory = isolated_settings.source_directory / "index"
        nested_guard = SourceTreeGuard(isolated_settings)
        with pytest.raises(SourceWriteAttempt):
            nested_guard.verify_data_directory_isolation()


class TestReadOnlyOpen:
    """Opening corpus files."""

    def test_reads_an_existing_file(self, guard, isolated_settings):
        source_file = isolated_settings.source_directory / "catatan.txt"
        source_file.write_text("isi dokumen", encoding="utf-8")
        with guard.open_for_reading(source_file, "rb") as handle:
            assert handle.read() == b"isi dokumen"

    @pytest.mark.parametrize("mutating_mode", ["w", "wb", "a", "ab", "x", "r+", "rb+", "w+"])
    def test_refuses_every_mutating_mode(self, guard, isolated_settings, mutating_mode):
        source_file = isolated_settings.source_directory / "catatan.txt"
        source_file.write_text("isi dokumen", encoding="utf-8")
        with pytest.raises(SourceWriteAttempt):
            guard.open_for_reading(source_file, mutating_mode)

    def test_read_mode_leaves_the_file_untouched(self, guard, isolated_settings):
        source_file = isolated_settings.source_directory / "catatan.txt"
        source_file.write_text("isi asli", encoding="utf-8")
        original_stat = source_file.stat()

        with guard.open_for_reading(source_file, "rb") as handle:
            handle.read()

        assert source_file.read_text(encoding="utf-8") == "isi asli"
        assert source_file.stat().st_size == original_stat.st_size
