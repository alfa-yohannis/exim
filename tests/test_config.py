"""Tests for environment parsing and settings resolution."""

from __future__ import annotations

import pytest

from app.config import EnvironmentReader, Settings


class TestEnvironmentReaderText:
    """Reading string values."""

    def test_returns_fallback_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_VALUE", raising=False)
        assert EnvironmentReader().text("TEST_VALUE", "cadangan") == "cadangan"

    def test_returns_fallback_when_blank(self, monkeypatch):
        """A blank entry in .env must not produce an empty setting."""
        monkeypatch.setenv("TEST_VALUE", "   ")
        assert EnvironmentReader().text("TEST_VALUE", "cadangan") == "cadangan"

    def test_strips_surrounding_whitespace(self, monkeypatch):
        monkeypatch.setenv("TEST_VALUE", "  qwen3:8b  ")
        assert EnvironmentReader().text("TEST_VALUE", "x") == "qwen3:8b"


class TestEnvironmentReaderNumbers:
    """Reading integers and floats."""

    def test_parses_an_integer(self, monkeypatch):
        monkeypatch.setenv("TEST_NUMBER", "512")
        assert EnvironmentReader().integer("TEST_NUMBER", 8) == 512

    def test_falls_back_on_malformed_integer(self, monkeypatch):
        monkeypatch.setenv("TEST_NUMBER", "bukan-angka")
        assert EnvironmentReader().integer("TEST_NUMBER", 8) == 8

    def test_parses_a_float(self, monkeypatch):
        monkeypatch.setenv("TEST_DECIMAL", "0.35")
        assert EnvironmentReader().decimal("TEST_DECIMAL", 0.2) == pytest.approx(0.35)

    def test_falls_back_on_malformed_float(self, monkeypatch):
        monkeypatch.setenv("TEST_DECIMAL", "")
        assert EnvironmentReader().decimal("TEST_DECIMAL", 0.2) == pytest.approx(0.2)


class TestEnvironmentReaderFlags:
    """Reading booleans."""

    @pytest.mark.parametrize("raw_value", ["1", "true", "TRUE", "Yes", "on", "enabled"])
    def test_recognises_truthy_words(self, monkeypatch, raw_value):
        monkeypatch.setenv("TEST_FLAG", raw_value)
        assert EnvironmentReader().flag("TEST_FLAG", False) is True

    @pytest.mark.parametrize("raw_value", ["0", "false", "no", "off", "sembarang"])
    def test_treats_everything_else_as_false(self, monkeypatch, raw_value):
        monkeypatch.setenv("TEST_FLAG", raw_value)
        assert EnvironmentReader().flag("TEST_FLAG", True) is False

    def test_uses_fallback_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_FLAG", raising=False)
        assert EnvironmentReader().flag("TEST_FLAG", True) is True


class TestEnvironmentReaderSets:
    """Reading comma-separated lists."""

    def test_splits_and_lowercases(self, monkeypatch):
        monkeypatch.setenv("TEST_SET", ".PDF, .Docx ,.xlsx")
        assert EnvironmentReader().lowercase_set("TEST_SET", set()) == {".pdf", ".docx", ".xlsx"}

    def test_ignores_empty_entries(self, monkeypatch):
        monkeypatch.setenv("TEST_SET", "a,,  ,b")
        assert EnvironmentReader().lowercase_set("TEST_SET", set()) == {"a", "b"}

    def test_returns_a_copy_of_the_fallback(self, monkeypatch):
        """Mutating the result must not corrupt the class-level default."""
        monkeypatch.delenv("TEST_SET", raising=False)
        fallback = {"a", "b"}
        result = EnvironmentReader().lowercase_set("TEST_SET", fallback)
        result.add("c")
        assert fallback == {"a", "b"}


class TestSettingsDefaults:
    """The resolved configuration object."""

    def test_excludes_the_developer_directory_by_default(self, isolated_settings):
        """`zdev` holds scratch scripts, not accreditation material."""
        assert "zdev" in isolated_settings.excluded_directories

    def test_skips_video_and_shortcut_files(self, isolated_settings):
        assert ".mov" in isolated_settings.skipped_extensions
        assert ".lnk" in isolated_settings.skipped_extensions
        assert ".gsheet" in isolated_settings.skipped_extensions

    def test_reranking_runs_on_cpu_to_protect_gpu_memory(self, isolated_settings):
        assert isolated_settings.reranking_device == "cpu"

    def test_server_binds_to_loopback_by_default(self, isolated_settings):
        assert isolated_settings.server_host == "127.0.0.1"


class TestSettingsDerivedValues:
    """Paths and helper methods derived from the base settings."""

    def test_index_artefacts_live_under_the_data_directory(self, isolated_settings):
        assert isolated_settings.database_path.parent == isolated_settings.data_directory
        assert isolated_settings.vector_store_path.parent == isolated_settings.data_directory
        assert isolated_settings.model_cache_directory.parent == isolated_settings.data_directory

    def test_images_are_excluded_when_ocr_is_off(self, isolated_settings):
        isolated_settings.ocr_enabled = False
        assert ".jpg" not in isolated_settings.indexable_extensions()
        assert ".pdf" in isolated_settings.indexable_extensions()

    def test_images_become_indexable_when_ocr_is_on(self, isolated_settings):
        isolated_settings.ocr_enabled = True
        assert ".jpg" in isolated_settings.indexable_extensions()

    def test_relocating_the_data_directory_moves_every_artefact(self, isolated_settings, tmp_path):
        relocated = tmp_path / "elsewhere"
        isolated_settings.data_directory = relocated
        assert isolated_settings.database_path == relocated / "index.db"
        assert isolated_settings.vector_store_path == relocated / "vectors.npz"


class TestSettingsOverrides:
    """Environment variables reaching the settings object."""

    def test_environment_overrides_the_source_directory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path / "lain"))
        settings = Settings(EnvironmentReader())
        assert settings.source_directory == tmp_path / "lain"

    def test_environment_overrides_the_excluded_directories(self, monkeypatch):
        monkeypatch.setenv("EXCLUDE_DIRS", "arsip,sementara")
        settings = Settings(EnvironmentReader())
        assert settings.excluded_directories == {"arsip", "sementara"}
