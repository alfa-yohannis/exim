"""Optional optical character recognition for scanned pages and images.

Accreditation corpora are full of scanned decrees, certificates and signed
forms that carry no PDF text layer. :class:`OpticalCharacterRecogniser` wraps
RapidOCR — a pip-installable ONNX engine that needs no system-level Tesseract
install — and degrades gracefully: when the package is absent, every method
reports unavailability instead of raising, and the pipeline simply indexes
whatever text layer exists.

Enable it with ``OCR_ENABLED=1`` after installing the optional extra:

    pip install rapidocr-onnxruntime
"""

from __future__ import annotations

import logging

from .config import Settings

LOGGER = logging.getLogger(__name__)


class OpticalCharacterRecogniser:
    """Reads text out of rendered pages and image files, when OCR is available.

    The underlying engine is created lazily on first use, because loading the
    ONNX detection and recognition models costs a few seconds and most ingest
    runs over a text-native corpus never need it.
    """

    #: Recognition results scoring below this are discarded as noise.
    MINIMUM_CONFIDENCE = 0.5

    def __init__(self, settings: Settings) -> None:
        """Record configuration; the engine itself is loaded on first use."""
        self._settings = settings
        self._engine = None
        self._load_attempted = False
        self._unavailable_reason: str | None = None

    @property
    def is_available(self) -> bool:
        """True when OCR is switched on and the engine loaded successfully."""
        if not self._settings.ocr_enabled:
            return False
        self._ensure_engine_loaded()
        return self._engine is not None

    @property
    def unavailable_reason(self) -> str | None:
        """Human-readable explanation of why OCR cannot run, if it cannot."""
        if not self._settings.ocr_enabled:
            return "OCR dinonaktifkan (OCR_ENABLED=0)"
        self._ensure_engine_loaded()
        return self._unavailable_reason

    def _ensure_engine_loaded(self) -> None:
        """Import and construct the RapidOCR engine exactly once."""
        if self._load_attempted:
            return
        self._load_attempted = True

        engine_class = self._import_engine_class()
        if engine_class is None:
            self._unavailable_reason = (
                "Paket OCR belum terpasang. Jalankan: pip install rapidocr-onnxruntime"
            )
            return

        try:
            self._engine = engine_class()
            LOGGER.info("Mesin OCR siap digunakan")
        except Exception as error:
            self._unavailable_reason = f"Gagal memuat mesin OCR: {error}"
            LOGGER.warning(self._unavailable_reason)

    def _import_engine_class(self):
        """Return the RapidOCR class from whichever package name is installed."""
        try:
            from rapidocr_onnxruntime import RapidOCR

            return RapidOCR
        except ImportError:
            pass
        try:
            from rapidocr import RapidOCR

            return RapidOCR
        except ImportError:
            return None

    def read_pdf_page(self, page, render_dpi: int) -> str:
        """Render a PyMuPDF page to a bitmap and recognise its text.

        Returns an empty string when OCR is unavailable or finds nothing.
        """
        if not self.is_available:
            return ""
        try:
            pixel_map = page.get_pixmap(dpi=render_dpi)
            return self.read_image_bytes(pixel_map.tobytes("png"))
        except Exception as error:
            LOGGER.warning("OCR gagal pada halaman PDF: %s", error)
            return ""

    def read_image_bytes(self, image_bytes: bytes) -> str:
        """Recognise text in an encoded image, returning newline-joined lines."""
        if not self.is_available:
            return ""
        try:
            recognition_result, _timing = self._engine(image_bytes)
        except Exception as error:
            LOGGER.warning("OCR gagal pada berkas gambar: %s", error)
            return ""
        return self._collect_confident_lines(recognition_result)

    def _collect_confident_lines(self, recognition_result) -> str:
        """Filter raw RapidOCR output down to confidently recognised text lines.

        RapidOCR returns a list of ``[bounding_box, text, confidence]`` entries,
        or None when the image contains no detectable text.
        """
        if not recognition_result:
            return ""
        accepted_lines: list[str] = []
        for entry in recognition_result:
            if len(entry) < 3:
                continue
            _bounding_box, recognised_text, confidence = entry[0], entry[1], entry[2]
            if not recognised_text or float(confidence) < self.MINIMUM_CONFIDENCE:
                continue
            accepted_lines.append(str(recognised_text).strip())
        return "\n".join(line for line in accepted_lines if line)
