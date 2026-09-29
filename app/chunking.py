"""Splitting extracted sections into embeddable chunks.

Retrieval quality depends heavily on chunk boundaries: cut too finely and a
fact loses the context that explains it, too coarsely and the embedding becomes
an average of unrelated topics. :class:`TokenAwareChunker` packs whole
paragraphs up to a token budget and repeats a short overlap between neighbours,
so a fact that straddles a boundary still appears intact in one chunk.

Token counting is abstracted behind :class:`TokenCounter` so chunking can run
with the embedding model's real tokenizer when it is loaded, or with a cheap
character-ratio estimate when it is not.
"""

from __future__ import annotations

import contextlib
import re
from abc import ABC, abstractmethod

from .config import Settings
from .documents import ExtractedSection, TextChunk


class TokenCounter(ABC):
    """Measures how many model tokens a piece of text occupies."""

    @abstractmethod
    def count(self, text: str) -> int:
        """Return the number of tokens ``text`` occupies."""


class CharacterRatioTokenCounter(TokenCounter):
    """Estimates token count from character count.

    Indonesian prose averages close to 3.6 characters per token under the
    SentencePiece vocabularies used by multilingual embedding models, which is
    accurate enough to place chunk boundaries without loading a tokenizer.
    """

    #: Average characters per token observed on Indonesian text.
    CHARACTERS_PER_TOKEN = 3.6

    def count(self, text: str) -> int:
        """Return the estimated token count for ``text``."""
        return max(1, int(len(text) / self.CHARACTERS_PER_TOKEN))


class TransformerTokenCounter(TokenCounter):
    """Counts tokens exactly, using a HuggingFace tokenizer."""

    def __init__(self, tokenizer, lock=None) -> None:
        """Wrap an already-loaded tokenizer, and the lock its other users hold.

        The tokenizer belongs to the embedding model, which answers questions
        while this counter chunks new files; see :class:`TextEmbedder`.
        """
        self._tokenizer = tokenizer
        self._lock = lock or contextlib.nullcontext()

    def count(self, text: str) -> int:
        """Return the exact token count, excluding special tokens."""
        with self._lock:
            return len(self._tokenizer.encode(text, add_special_tokens=False))


class TokenAwareChunker:
    """Cuts document sections into overlapping, token-bounded chunks.

    The algorithm works in three passes so that boundaries fall at the most
    natural place available:

    1. Split each section into paragraph blocks on blank lines.
    2. Split any block that alone exceeds the budget at sentence boundaries,
       and any remaining oversized fragment at word boundaries.
    3. Greedily pack blocks into chunks, carrying a tail of trailing blocks
       into the next chunk as overlap.
    """

    _PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")
    _SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?:;])\s+")

    def __init__(self, settings: Settings, token_counter: TokenCounter | None = None) -> None:
        """Bind chunk sizing to configuration and choose a token counter."""
        self._settings = settings
        self._token_counter = token_counter or CharacterRatioTokenCounter()

    def chunk_sections(
        self, document_id: str, sections: list[ExtractedSection]
    ) -> list[TextChunk]:
        """Convert every section of one document into a flat list of chunks."""
        chunks: list[TextChunk] = []
        for section in sections:
            for chunk_text in self._split_section_text(section.text):
                if len(chunk_text) < self._settings.minimum_chunk_characters:
                    continue
                chunks.append(
                    TextChunk(
                        document_id=document_id,
                        ordinal=len(chunks),
                        section_label=section.label,
                        text=chunk_text,
                        token_count=self._token_counter.count(chunk_text),
                        produced_by_ocr=section.produced_by_ocr,
                    )
                )
        return chunks

    def _split_section_text(self, section_text: str) -> list[str]:
        """Pack one section's blocks into overlapping token-bounded chunks."""
        blocks = self._build_blocks(section_text)
        if not blocks:
            return []

        budget = self._settings.chunk_token_count
        chunks: list[str] = []
        current_blocks: list[str] = []
        current_tokens = 0

        for block in blocks:
            block_tokens = self._token_counter.count(block)
            exceeds_budget = current_tokens + block_tokens > budget
            if exceeds_budget and current_blocks:
                chunks.append("\n\n".join(current_blocks))
                current_blocks = self._build_overlap_tail(current_blocks)
                current_tokens = sum(
                    self._token_counter.count(item) for item in current_blocks
                )
            current_blocks.append(block)
            current_tokens += block_tokens

        if current_blocks:
            chunks.append("\n\n".join(current_blocks))
        return [chunk.strip() for chunk in chunks if chunk.strip()]

    def _build_blocks(self, section_text: str) -> list[str]:
        """Split text into paragraph blocks, subdividing oversized ones."""
        budget = self._settings.chunk_token_count
        blocks: list[str] = []
        for paragraph in self._PARAGRAPH_BOUNDARY.split(section_text):
            stripped = paragraph.strip()
            if not stripped:
                continue
            if self._token_counter.count(stripped) <= budget:
                blocks.append(stripped)
            else:
                blocks.extend(self._split_oversized_block(stripped, budget))
        return blocks

    def _split_oversized_block(self, block: str, budget: int) -> list[str]:
        """Break a paragraph longer than the budget at sentence boundaries."""
        fragments: list[str] = []
        pending: list[str] = []
        pending_tokens = 0

        for sentence in self._SENTENCE_BOUNDARY.split(block):
            stripped = sentence.strip()
            if not stripped:
                continue
            sentence_tokens = self._token_counter.count(stripped)
            if sentence_tokens > budget:
                if pending:
                    fragments.append(" ".join(pending))
                    pending, pending_tokens = [], 0
                fragments.extend(self._split_by_words(stripped, budget))
                continue
            if pending_tokens + sentence_tokens > budget and pending:
                fragments.append(" ".join(pending))
                pending, pending_tokens = [], 0
            pending.append(stripped)
            pending_tokens += sentence_tokens

        if pending:
            fragments.append(" ".join(pending))
        return fragments

    def _split_by_words(self, text: str, budget: int) -> list[str]:
        """Last-resort hard split for text with no usable sentence boundaries.

        Reached by dense spreadsheet rows and OCR output, which can run for
        thousands of characters without punctuation.
        """
        words = text.split()
        if not words:
            return []

        # Convert the token budget into a word budget using the observed ratio.
        sample = " ".join(words[: min(len(words), 200)])
        tokens_per_word = self._token_counter.count(sample) / max(1, len(words[:200]))
        words_per_chunk = max(20, int(budget / max(0.1, tokens_per_word)))

        fragments: list[str] = []
        for start_index in range(0, len(words), words_per_chunk):
            fragments.append(" ".join(words[start_index : start_index + words_per_chunk]))
        return fragments

    def _build_overlap_tail(self, packed_blocks: list[str]) -> list[str]:
        """Return the trailing blocks to repeat at the start of the next chunk."""
        overlap_budget = self._settings.chunk_overlap_token_count
        if overlap_budget <= 0:
            return []

        tail: list[str] = []
        accumulated_tokens = 0
        for block in reversed(packed_blocks):
            block_tokens = self._token_counter.count(block)
            if accumulated_tokens + block_tokens > overlap_budget and tail:
                break
            tail.insert(0, block)
            accumulated_tokens += block_tokens
            if accumulated_tokens >= overlap_budget:
                break
        # Never repeat the entire chunk, or packing would fail to advance.
        if len(tail) >= len(packed_blocks):
            tail = tail[1:]
        return tail
