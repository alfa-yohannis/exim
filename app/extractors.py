"""Turning corpus files into labelled text sections.

Each supported file format has its own :class:`DocumentExtractor` subclass, and
:class:`ExtractorRegistry` picks the right one by file extension. Extractors
never raise on a malformed document: they return an :class:`ExtractionOutcome`
carrying an error message, so one corrupt PDF cannot abort an ingest run.

Every file read in this module goes through :data:`app.safety.SOURCE_GUARD`,
which physically refuses any file mode capable of modifying the corpus.
"""

from __future__ import annotations

import csv
import io
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path

from .config import SETTINGS, Settings
from .documents import ExtractedSection, ExtractionOutcome, SourceDocument
from .ocr import OpticalCharacterRecogniser
from .safety import SOURCE_GUARD, SourceTreeGuard


class TextNormaliser:
    """Cleans raw extracted text into something worth embedding.

    Extraction artefacts — hard-wrapped lines, repeated whitespace, stray null
    bytes from PDF text layers — add noise to embeddings and waste context, so
    they are collapsed before chunking.
    """

    _EXCESSIVE_BLANK_LINES = re.compile(r"\n{3,}")
    _TRAILING_SPACES = re.compile(r"[ \t]+\n")
    _REPEATED_SPACES = re.compile(r"[ \t]{2,}")
    _CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def normalise(self, raw_text: str) -> str:
        """Return ``raw_text`` with control characters and padding removed."""
        if not raw_text:
            return ""
        cleaned = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = self._CONTROL_CHARACTERS.sub(" ", cleaned)
        cleaned = self._TRAILING_SPACES.sub("\n", cleaned)
        cleaned = self._REPEATED_SPACES.sub(" ", cleaned)
        cleaned = self._EXCESSIVE_BLANK_LINES.sub("\n\n", cleaned)
        return cleaned.strip()


class DocumentExtractor(ABC):
    """Base class for every format-specific text extractor.

    Subclasses implement :meth:`extract_sections` and declare the extensions
    they handle. The public :meth:`extract` wraps that call in error handling so
    callers receive an outcome object rather than an exception.
    """

    def __init__(self, settings: Settings, guard: SourceTreeGuard) -> None:
        """Bind the extractor to configuration and the read-only guard."""
        self._settings = settings
        self._guard = guard
        self._normaliser = TextNormaliser()

    @property
    @abstractmethod
    def supported_extensions(self) -> set[str]:
        """Lowercase file extensions, including the leading dot."""

    @abstractmethod
    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Read the document and return its labelled text sections."""

    def extract(self, document: SourceDocument) -> ExtractionOutcome:
        """Extract text, converting any failure into a described outcome."""
        try:
            outcome = self.extract_sections(document)
        except Exception as error:
            return ExtractionOutcome.from_failure(error)
        outcome.sections = [
            section for section in outcome.sections if section.text.strip()
        ]
        return outcome

    def _decode_bytes(self, raw_bytes: bytes) -> str:
        """Decode text bytes, trying the encodings this corpus actually uses."""
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode("utf-8", errors="replace")


class PdfExtractor(DocumentExtractor):
    """Extracts a PDF page by page, falling back to OCR for scanned pages.

    Accreditation corpora are full of scanned decrees and certificates, so pages
    whose text layer is near-empty are counted separately and — when OCR is
    switched on — re-read from a rendered image.
    """

    def __init__(
        self,
        settings: Settings,
        guard: SourceTreeGuard,
        recogniser: OpticalCharacterRecogniser,
    ) -> None:
        """Bind the extractor, additionally taking the OCR engine to fall back to."""
        super().__init__(settings, guard)
        self._recogniser = recogniser

    @property
    def supported_extensions(self) -> set[str]:
        """PDF is the dominant format in this corpus."""
        return {".pdf"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Read every page, OCR-ing those without a usable text layer."""
        import fitz  # PyMuPDF, imported lazily to keep start-up fast

        outcome = ExtractionOutcome()
        # Read via a context manager: without one the handle stays open until
        # garbage collection, and on a network drive that holds a lock on a
        # corpus file for far longer than the read itself.
        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            raw_bytes = file_handle.read()
        with fitz.open(stream=raw_bytes, filetype="pdf") as pdf_document:
            for page_index, page in enumerate(pdf_document):
                section = self._extract_page(page, page_index, outcome)
                if section is not None:
                    outcome.sections.append(section)
        return outcome

    def _extract_page(self, page, page_index: int, outcome: ExtractionOutcome):
        """Extract one page, recording whether it was scanned or OCR-recovered."""
        page_number = page_index + 1
        label = f"Halaman {page_number}"
        page_text = self._normaliser.normalise(page.get_text("text"))

        if len(page_text) >= self._settings.ocr_page_character_threshold:
            return ExtractedSection(ordinal=page_number, label=label, text=page_text)

        # The text layer is empty or near-empty: this page is almost certainly a scan.
        outcome.scanned_section_count += 1
        may_run_ocr = (
            self._recogniser.is_available
            and outcome.ocr_section_count < self._settings.ocr_maximum_pages_per_document
        )
        if not may_run_ocr:
            return ExtractedSection(ordinal=page_number, label=label, text=page_text)

        recognised_text = self._recogniser.read_pdf_page(page, self._settings.ocr_render_dpi)
        if not recognised_text:
            return ExtractedSection(ordinal=page_number, label=label, text=page_text)

        outcome.ocr_section_count += 1
        return ExtractedSection(
            ordinal=page_number,
            label=f"{label} (OCR)",
            text=self._normaliser.normalise(recognised_text),
            produced_by_ocr=True,
        )


class WordExtractor(DocumentExtractor):
    """Extracts paragraphs and tables from a Word document as one section."""

    @property
    def supported_extensions(self) -> set[str]:
        """Only the modern XML-based Word format is readable without Office."""
        return {".docx"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Read body paragraphs first, then flatten every table into rows."""
        import docx

        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            word_document = docx.Document(io.BytesIO(file_handle.read()))

        lines = [
            paragraph.text.strip()
            for paragraph in word_document.paragraphs
            if paragraph.text.strip()
        ]
        for table in word_document.tables:
            lines.extend(self._flatten_table(table))

        body_text = self._normaliser.normalise("\n".join(lines))
        section = ExtractedSection(ordinal=1, label="Dokumen", text=body_text)
        return ExtractionOutcome(sections=[section])

    def _flatten_table(self, table) -> list[str]:
        """Render a Word table as pipe-separated rows so structure survives."""
        rows: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        return rows


class SpreadsheetExtractor(DocumentExtractor):
    """Extracts each worksheet of a workbook as its own labelled section.

    Workbooks are opened in openpyxl's read-only streaming mode, which never
    holds a writable handle on the file and keeps memory flat on large sheets.
    """

    #: Stop reading a worksheet after this many rows to bound ingest time.
    MAXIMUM_ROWS_PER_SHEET = 4000

    @property
    def supported_extensions(self) -> set[str]:
        """Modern Excel formats; legacy .xls needs a converter and is skipped."""
        return {".xlsx", ".xlsm"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Render every non-empty worksheet as tab-separated rows."""
        import openpyxl

        outcome = ExtractionOutcome()
        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            workbook_bytes = io.BytesIO(file_handle.read())

        workbook = openpyxl.load_workbook(
            workbook_bytes, read_only=True, data_only=True, keep_links=False
        )
        try:
            for sheet_index, worksheet in enumerate(workbook.worksheets, start=1):
                sheet_text = self._render_worksheet(worksheet)
                if sheet_text:
                    outcome.sections.append(
                        ExtractedSection(
                            ordinal=sheet_index,
                            label=f"Sheet: {worksheet.title}",
                            text=sheet_text,
                        )
                    )
        finally:
            workbook.close()
        return outcome

    def _render_worksheet(self, worksheet) -> str:
        """Convert a worksheet's populated cells into tab-separated text rows."""
        rendered_rows: list[str] = []
        for row_index, row_values in enumerate(worksheet.iter_rows(values_only=True)):
            if row_index >= self.MAXIMUM_ROWS_PER_SHEET:
                rendered_rows.append("... (baris selanjutnya tidak diindeks)")
                break
            cells = [self._render_cell(value) for value in row_values]
            if any(cell for cell in cells):
                rendered_rows.append("\t".join(cells).rstrip("\t"))
        return self._normaliser.normalise("\n".join(rendered_rows))

    def _render_cell(self, value) -> str:
        """Stringify a single cell, treating blanks as the empty string."""
        if value is None:
            return ""
        return str(value).strip().replace("\n", " ")


class PresentationExtractor(DocumentExtractor):
    """Extracts each slide of a PowerPoint deck as its own section."""

    @property
    def supported_extensions(self) -> set[str]:
        """Modern PowerPoint format only."""
        return {".pptx"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Collect the text of every shape on every slide, slide by slide."""
        from pptx import Presentation

        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            presentation = Presentation(io.BytesIO(file_handle.read()))

        outcome = ExtractionOutcome()
        for slide_index, slide in enumerate(presentation.slides, start=1):
            slide_text = self._render_slide(slide)
            if slide_text:
                outcome.sections.append(
                    ExtractedSection(
                        ordinal=slide_index,
                        label=f"Slide {slide_index}",
                        text=slide_text,
                    )
                )
        return outcome

    def _render_slide(self, slide) -> str:
        """Join the text of every text-bearing shape on one slide."""
        fragments = [
            shape.text.strip()
            for shape in slide.shapes
            if hasattr(shape, "text") and shape.text.strip()
        ]
        return self._normaliser.normalise("\n".join(fragments))


class HtmlExtractor(DocumentExtractor):
    """Strips markup from an HTML file, keeping only readable body text."""

    #: Tags whose contents are machine directives rather than prose.
    NON_CONTENT_TAGS = ("script", "style", "noscript", "head", "meta", "link")

    @property
    def supported_extensions(self) -> set[str]:
        """Saved web pages and exported reports."""
        return {".html", ".htm"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Parse the page and return its visible text as a single section."""
        from bs4 import BeautifulSoup

        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            markup = self._decode_bytes(file_handle.read())

        # html.parser is used instead of lxml so the project needs no C extension.
        soup = BeautifulSoup(markup, "html.parser")
        for tag_name in self.NON_CONTENT_TAGS:
            for element in soup.find_all(tag_name):
                element.decompose()

        body_text = self._normaliser.normalise(soup.get_text(separator="\n"))
        section = ExtractedSection(ordinal=1, label="Halaman web", text=body_text)
        return ExtractionOutcome(sections=[section])


class DelimitedTextExtractor(DocumentExtractor):
    """Extracts a CSV file, preserving its row and column structure."""

    #: Stop after this many rows to bound ingest time on exported datasets.
    MAXIMUM_ROWS = 4000

    @property
    def supported_extensions(self) -> set[str]:
        """Comma-separated exports."""
        return {".csv"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Read rows with a sniffed dialect and render them tab-separated."""
        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            decoded_text = self._decode_bytes(file_handle.read())

        rendered_rows: list[str] = []
        reader = csv.reader(io.StringIO(decoded_text))
        for row_index, row in enumerate(reader):
            if row_index >= self.MAXIMUM_ROWS:
                rendered_rows.append("... (baris selanjutnya tidak diindeks)")
                break
            if any(field.strip() for field in row):
                rendered_rows.append("\t".join(field.strip() for field in row))

        body_text = self._normaliser.normalise("\n".join(rendered_rows))
        section = ExtractedSection(ordinal=1, label="Tabel CSV", text=body_text)
        return ExtractionOutcome(sections=[section])


class JsonExtractor(DocumentExtractor):
    """Renders a JSON file as indented text so keys stay searchable."""

    @property
    def supported_extensions(self) -> set[str]:
        """Configuration and exported data files."""
        return {".json"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Pretty-print valid JSON; fall back to the raw text when malformed."""
        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            decoded_text = self._decode_bytes(file_handle.read())

        try:
            parsed = json.loads(decoded_text)
            rendered = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            rendered = decoded_text

        section = ExtractedSection(
            ordinal=1, label="Data JSON", text=self._normaliser.normalise(rendered)
        )
        return ExtractionOutcome(sections=[section])


class PlainTextExtractor(DocumentExtractor):
    """Reads a plain-text file directly, with encoding detection."""

    @property
    def supported_extensions(self) -> set[str]:
        """Notes, markdown and scripts left alongside the documents."""
        return {".txt", ".md", ".py"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Decode the file and return its contents as one section."""
        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            decoded_text = self._decode_bytes(file_handle.read())

        section = ExtractedSection(
            ordinal=1, label="Teks", text=self._normaliser.normalise(decoded_text)
        )
        return ExtractionOutcome(sections=[section])


class ImageExtractor(DocumentExtractor):
    """Reads text out of a standalone image using OCR.

    Only reachable when ``OCR_ENABLED`` is set; without OCR these files are not
    indexable at all and the registry never routes to this extractor.
    """

    def __init__(
        self,
        settings: Settings,
        guard: SourceTreeGuard,
        recogniser: OpticalCharacterRecogniser,
    ) -> None:
        """Bind the extractor, additionally taking the OCR engine it depends on."""
        super().__init__(settings, guard)
        self._recogniser = recogniser

    @property
    def supported_extensions(self) -> set[str]:
        """Scanned certificates and photographed documents."""
        return {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

    def extract_sections(self, document: SourceDocument) -> ExtractionOutcome:
        """Run OCR over the image, returning nothing when OCR is unavailable."""
        if not self._recogniser.is_available:
            return ExtractionOutcome(error_message="OCR tidak tersedia untuk berkas gambar")

        with self._guard.open_for_reading(document.absolute_path, "rb") as file_handle:
            image_bytes = file_handle.read()

        recognised_text = self._recogniser.read_image_bytes(image_bytes)
        if not recognised_text:
            return ExtractionOutcome(scanned_section_count=1)

        section = ExtractedSection(
            ordinal=1,
            label="Gambar (OCR)",
            text=self._normaliser.normalise(recognised_text),
            produced_by_ocr=True,
        )
        return ExtractionOutcome(sections=[section], ocr_section_count=1)


class ExtractorRegistry:
    """Routes each document to the extractor that understands its format.

    Built once at start-up; :meth:`find_for` is a dictionary lookup on the file
    extension, and :meth:`supports` lets the scanner skip unsupported files
    before they ever reach the pipeline.
    """

    def __init__(self, settings: Settings, guard: SourceTreeGuard) -> None:
        """Instantiate every extractor and index them by supported extension."""
        recogniser = OpticalCharacterRecogniser(settings)
        self._recogniser = recogniser
        self._extractors: list[DocumentExtractor] = [
            PdfExtractor(settings, guard, recogniser),
            WordExtractor(settings, guard),
            SpreadsheetExtractor(settings, guard),
            PresentationExtractor(settings, guard),
            HtmlExtractor(settings, guard),
            DelimitedTextExtractor(settings, guard),
            JsonExtractor(settings, guard),
            PlainTextExtractor(settings, guard),
            ImageExtractor(settings, guard, recogniser),
        ]
        self._by_extension: dict[str, DocumentExtractor] = {}
        for extractor in self._extractors:
            for extension in extractor.supported_extensions:
                self._by_extension[extension] = extractor

    @property
    def ocr_recogniser(self) -> OpticalCharacterRecogniser:
        """The shared OCR engine, exposed so callers can report availability."""
        return self._recogniser

    def supports(self, extension: str) -> bool:
        """True when some extractor handles this extension."""
        return extension.lower() in self._by_extension

    def find_for(self, document: SourceDocument) -> DocumentExtractor | None:
        """Return the extractor for ``document``, or None when unsupported."""
        return self._by_extension.get(document.extension.lower())

    def extract(self, document: SourceDocument) -> ExtractionOutcome:
        """Extract ``document`` using its registered extractor."""
        extractor = self.find_for(document)
        if extractor is None:
            return ExtractionOutcome(
                error_message=f"Tidak ada extractor untuk ekstensi {document.extension!r}"
            )
        return extractor.extract(document)


#: Shared registry bound to the active settings and read-only guard.
EXTRACTOR_REGISTRY = ExtractorRegistry(SETTINGS, SOURCE_GUARD)
