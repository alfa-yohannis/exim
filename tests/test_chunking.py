"""Tests for text normalisation and token-aware chunking."""

from __future__ import annotations

import pytest

from app.chunking import CharacterRatioTokenCounter, TokenAwareChunker, TokenCounter
from app.documents import ExtractedSection
from app.extractors import TextNormaliser


class WordTokenCounter(TokenCounter):
    """Counts whitespace-separated words, so chunk sizes are exact in tests."""

    def count(self, text: str) -> int:
        """Return the number of words in ``text``."""
        return max(1, len(text.split()))


@pytest.fixture
def word_chunker(isolated_settings):
    """A chunker whose token budget is measured in plain words."""
    isolated_settings.chunk_token_count = 20
    isolated_settings.chunk_overlap_token_count = 5
    isolated_settings.minimum_chunk_characters = 5
    return TokenAwareChunker(isolated_settings, WordTokenCounter())


class TestTextNormaliser:
    """Cleaning raw extractor output."""

    def test_collapses_runs_of_blank_lines(self):
        assert TextNormaliser().normalise("a\n\n\n\n\nb") == "a\n\nb"

    def test_collapses_repeated_spaces(self):
        assert TextNormaliser().normalise("kata      lain") == "kata lain"

    def test_removes_control_characters_from_pdf_text_layers(self):
        assert TextNormaliser().normalise("teks\x00\x07bersih") == "teks bersih"

    def test_normalises_windows_line_endings(self):
        assert TextNormaliser().normalise("baris\r\nkedua") == "baris\nkedua"

    def test_trims_leading_and_trailing_whitespace(self):
        assert TextNormaliser().normalise("   isi   ") == "isi"

    def test_returns_empty_string_for_empty_input(self):
        assert TextNormaliser().normalise("") == ""


class TestCharacterRatioTokenCounter:
    """The tokenizer-free fallback counter."""

    def test_estimates_proportionally_to_length(self):
        counter = CharacterRatioTokenCounter()
        assert counter.count("x" * 360) == pytest.approx(100, abs=2)

    def test_never_returns_zero(self):
        """A one-character chunk still occupies a token."""
        assert CharacterRatioTokenCounter().count("a") == 1


class TestChunkBoundaries:
    """How sections are cut into chunks."""

    def test_short_section_produces_one_chunk(self, word_chunker):
        section = ExtractedSection(ordinal=1, label="Halaman 1", text="satu dua tiga empat lima")
        chunks = word_chunker.chunk_sections("doc-1", [section])
        assert len(chunks) == 1
        assert chunks[0].text == "satu dua tiga empat lima"

    def test_long_section_is_split_into_several_chunks(self, word_chunker):
        paragraphs = "\n\n".join(" ".join(f"kata{index}" for index in range(15)) for _ in range(5))
        section = ExtractedSection(ordinal=1, label="Halaman 1", text=paragraphs)
        chunks = word_chunker.chunk_sections("doc-1", [section])
        assert len(chunks) > 1

    def test_every_chunk_carries_its_section_label(self, word_chunker):
        section = ExtractedSection(ordinal=3, label="Sheet: Dosen", text="a b c d e f g")
        chunks = word_chunker.chunk_sections("doc-1", [section])
        assert all(chunk.section_label == "Sheet: Dosen" for chunk in chunks)

    def test_ordinals_are_sequential_across_sections(self, word_chunker):
        sections = [
            ExtractedSection(ordinal=1, label="Halaman 1", text="alpha beta gamma delta"),
            ExtractedSection(ordinal=2, label="Halaman 2", text="epsilon zeta eta theta"),
        ]
        chunks = word_chunker.chunk_sections("doc-1", sections)
        assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))

    def test_ocr_provenance_propagates_to_chunks(self, word_chunker):
        section = ExtractedSection(
            ordinal=1, label="Halaman 1 (OCR)", text="hasil pindai dokumen", produced_by_ocr=True
        )
        chunks = word_chunker.chunk_sections("doc-1", [section])
        assert all(chunk.produced_by_ocr for chunk in chunks)

    def test_chunks_shorter_than_the_minimum_are_dropped(self, isolated_settings):
        """Page numbers and stray headers must not become searchable chunks."""
        isolated_settings.minimum_chunk_characters = 40
        chunker = TokenAwareChunker(isolated_settings, WordTokenCounter())
        section = ExtractedSection(ordinal=1, label="Halaman 1", text="hal. 3")
        assert chunker.chunk_sections("doc-1", [section]) == []

    def test_empty_section_produces_no_chunks(self, word_chunker):
        section = ExtractedSection(ordinal=1, label="Halaman 1", text="   ")
        assert word_chunker.chunk_sections("doc-1", [section]) == []


class TestChunkOverlap:
    """Overlap between neighbouring chunks."""

    def test_neighbouring_chunks_share_content(self, word_chunker):
        paragraphs = "\n\n".join(f"paragraf{index} " + " ".join(["isi"] * 15) for index in range(4))
        section = ExtractedSection(ordinal=1, label="Halaman 1", text=paragraphs)
        chunks = word_chunker.chunk_sections("doc-1", [section])

        assert len(chunks) >= 2
        # The tail of one chunk should reappear at the head of the next.
        first_chunk_words = set(chunks[0].text.split())
        second_chunk_words = set(chunks[1].text.split())
        assert first_chunk_words & second_chunk_words

    def test_packing_always_advances(self, word_chunker):
        """Overlap must never consume a whole chunk, or chunking would loop."""
        paragraphs = "\n\n".join(f"blok{index}" for index in range(30))
        section = ExtractedSection(ordinal=1, label="Halaman 1", text=paragraphs)
        chunks = word_chunker.chunk_sections("doc-1", [section])
        assert 0 < len(chunks) < 30

    def test_zero_overlap_is_honoured(self, isolated_settings):
        isolated_settings.chunk_token_count = 10
        isolated_settings.chunk_overlap_token_count = 0
        isolated_settings.minimum_chunk_characters = 1
        chunker = TokenAwareChunker(isolated_settings, WordTokenCounter())

        paragraphs = "\n\n".join(f"unik{index}" for index in range(25))
        section = ExtractedSection(ordinal=1, label="Halaman 1", text=paragraphs)
        chunks = chunker.chunk_sections("doc-1", [section])

        combined_word_count = sum(len(chunk.text.split()) for chunk in chunks)
        assert combined_word_count == 25


class TestOversizedContent:
    """Content that cannot be split on paragraph boundaries."""

    def test_a_long_paragraph_is_split_at_sentence_boundaries(self, word_chunker):
        sentences = " ".join(f"Kalimat nomor {index} berisi data penting." for index in range(12))
        section = ExtractedSection(ordinal=1, label="Halaman 1", text=sentences)
        chunks = word_chunker.chunk_sections("doc-1", [section])
        assert len(chunks) > 1

    def test_unpunctuated_text_falls_back_to_word_splitting(self, word_chunker):
        """Spreadsheet rows and OCR output can run on with no punctuation."""
        run_on_text = " ".join(f"sel{index}" for index in range(200))
        section = ExtractedSection(ordinal=1, label="Sheet: Data", text=run_on_text)
        chunks = word_chunker.chunk_sections("doc-1", [section])

        assert len(chunks) > 1
        assert all(chunk.text.strip() for chunk in chunks)

    def test_token_counts_are_recorded_on_each_chunk(self, word_chunker):
        section = ExtractedSection(ordinal=1, label="Halaman 1", text="satu dua tiga empat")
        chunks = word_chunker.chunk_sections("doc-1", [section])
        assert chunks[0].token_count == 4
