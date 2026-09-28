"""Value objects shared by extraction, indexing and retrieval.

These are deliberately small, immutable-ish carriers with no behaviour beyond
formatting and derived properties. They give the pipeline a common vocabulary:

``SourceDocument`` → ``ExtractedSection`` → ``TextChunk`` → ``RetrievedPassage``

which mirrors the order in which the system processes a file: discovered on
disk, split into labelled sections, cut into embeddable chunks, and finally
returned as a scored, citable passage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class SourceDocument:
    """A single file discovered in the corpus, described without reading it.

    ``document_id`` is derived from the path so that re-running an ingest maps a
    file back onto its previous entry, while ``content_signature`` changes
    whenever the file is edited, driving incremental re-indexing.
    """

    absolute_path: Path
    relative_path: str
    display_name: str
    extension: str
    size_bytes: int
    modified_timestamp: float

    @property
    def document_id(self) -> str:
        """Stable identifier for this file, derived from its relative path."""
        digest = hashlib.sha1(self.relative_path.encode("utf-8", "replace"))
        return digest.hexdigest()[:16]

    @property
    def content_signature(self) -> str:
        """Cheap change detector combining file size and modification time."""
        return f"{self.size_bytes}:{int(self.modified_timestamp)}"

    @property
    def top_level_folder(self) -> str:
        """First path segment below the corpus root, used as a facet filter."""
        parts = Path(self.relative_path).parts
        return parts[0] if len(parts) > 1 else "(root)"

    @property
    def modified_at(self) -> datetime:
        """Modification time as a datetime, for display in the interface."""
        return datetime.fromtimestamp(self.modified_timestamp)

    @property
    def size_megabytes(self) -> float:
        """File size in megabytes, rounded for display."""
        return round(self.size_bytes / (1024 * 1024), 2)


@dataclass
class ExtractedSection:
    """One labelled span of text pulled out of a document.

    A section is a PDF page, a spreadsheet worksheet, a slide, or the whole body
    of a flowing document. ``label`` is what a reader would recognise, and is
    surfaced verbatim in citations.
    """

    ordinal: int
    label: str
    text: str
    #: True when the text came from OCR rather than an embedded text layer.
    produced_by_ocr: bool = False

    @property
    def character_count(self) -> int:
        """Length of the extracted text, used to detect empty or scanned pages."""
        return len(self.text)


@dataclass
class ExtractionOutcome:
    """The result of running an extractor over one document.

    A failed extraction is represented by an outcome carrying ``error_message``
    and no sections, rather than by an exception, so a single unreadable file
    never aborts a whole ingest run.
    """

    sections: list[ExtractedSection] = field(default_factory=list)
    #: Pages that appeared to be scanned images with no usable text layer.
    scanned_section_count: int = 0
    #: Sections whose text was recovered by OCR.
    ocr_section_count: int = 0
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """True when extraction produced at least one section without error."""
        return self.error_message is None and bool(self.sections)

    @property
    def total_characters(self) -> int:
        """Combined length of every extracted section."""
        return sum(section.character_count for section in self.sections)

    @classmethod
    def from_failure(cls, error: BaseException) -> "ExtractionOutcome":
        """Build an outcome describing an extraction that raised."""
        return cls(error_message=f"{type(error).__name__}: {error}")


@dataclass
class TextChunk:
    """An embeddable slice of a document, carrying enough context to cite it."""

    document_id: str
    ordinal: int
    section_label: str
    text: str
    token_count: int
    #: True when this chunk's text originated from OCR, shown as a caveat.
    produced_by_ocr: bool = False


class ExternalLinkExtractor:
    """Finds outbound links inside a retrieved passage.

    Some indexed documents are really directories of external resources — the
    lecturer mapping, for instance, holds a SINTA and a Google Scholar URL for
    each person. When such a passage is cited, the destination a reader wants is
    that profile page, not the spreadsheet it happened to be listed in.

    Known hosts get a readable label so the interface can say "SINTA" rather
    than showing a long identifier-laden URL.
    """

    #: Trailing punctuation is excluded so a URL at the end of a sentence, or
    #: inside a spreadsheet cell, does not capture the delimiter after it.
    URL_PATTERN = re.compile(r"https?://[^\s\"'<>,;)\]}]+")

    #: Hostnames worth naming explicitly, matched on the registrable suffix.
    HOST_LABELS = {
        "sinta.kemdiktisaintek.go.id": "SINTA",
        "sinta.kemdikbud.go.id": "SINTA",
        "sinta.ristekbrin.go.id": "SINTA",
        "scholar.google.com": "Google Scholar",
        "scholar.google.co.id": "Google Scholar",
        "garuda.kemdikbud.go.id": "Garuda",
        "garuda.kemdiktisaintek.go.id": "Garuda",
        "pddikti.kemdikbud.go.id": "PDDikti",
        "pddikti.kemdiktisaintek.go.id": "PDDikti",
        "pradita.ac.id": "Universitas Pradita",
        "orcid.org": "ORCID",
        "doi.org": "DOI",
        "scopus.com": "Scopus",
        "webofscience.com": "Web of Science",
        "researchgate.net": "ResearchGate",
    }

    #: Cap so one link-heavy spreadsheet row cannot flood a citation card.
    MAXIMUM_LINKS = 8

    def extract(self, text: str) -> list[dict]:
        """Return unique links found in ``text``, each with a display label."""
        if not text:
            return []

        found: list[dict] = []
        seen: set[str] = set()
        for raw_url in self.URL_PATTERN.findall(text):
            url = raw_url.rstrip(".")
            if url in seen:
                continue
            seen.add(url)
            found.append({"url": url, "label": self.describe(url)})
            if len(found) >= self.MAXIMUM_LINKS:
                break
        return found

    def describe(self, url: str) -> str:
        """Return a short, human label for ``url``."""
        host = self._host_of(url)
        if not host:
            return "Tautan"
        for known_host, label in self.HOST_LABELS.items():
            if host == known_host or host.endswith("." + known_host):
                return label
        return host.removeprefix("www.")

    def _host_of(self, url: str) -> str:
        """Extract the lowercase hostname from a URL, or "" when malformed."""
        try:
            return urlparse(url).hostname or ""
        except ValueError:
            return ""


@dataclass
class RetrievedPassage:
    """A chunk returned by search, annotated with how it was found and ranked.

    The three scores are kept separate rather than collapsed so the interface
    can explain *why* a passage surfaced: vector similarity, keyword match, or
    the reranker's judgement.
    """

    chunk_id: int
    document_id: str
    document_name: str
    relative_path: str
    absolute_path: str
    section_label: str
    text: str
    produced_by_ocr: bool = False
    #: Cosine similarity from the vector index, when the passage matched densely.
    dense_score: float | None = None
    #: BM25 score from the keyword index, when the passage matched lexically.
    sparse_score: float | None = None
    #: Cross-encoder relevance score, present only when reranking ran.
    reranked_score: float | None = None
    #: Reciprocal-rank-fusion score used to order candidates before reranking.
    fused_score: float = 0.0
    #: 1-based position in the final answer context, matching the "[n]" citation.
    citation_number: int = 0

    @property
    def retrieval_methods(self) -> list[str]:
        """Which indexes surfaced this passage, for display as badges."""
        methods: list[str] = []
        if self.dense_score is not None:
            methods.append("semantic")
        if self.sparse_score is not None:
            methods.append("keyword")
        return methods

    @property
    def ranking_score(self) -> float:
        """Best available relevance score, preferring the reranker's verdict."""
        if self.reranked_score is not None:
            return self.reranked_score
        return self.fused_score
