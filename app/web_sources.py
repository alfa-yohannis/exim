"""Snapshotting external pages so they can be indexed alongside the documents.

Several indexed documents are really directories of external resources — the
lecturer mapping holds a SINTA and a Google Scholar profile URL for each person.
Those profile pages carry the publication and citation record that accreditation
actually asks about, but they live on the web, not in the shared drive.

This module fetches such pages and writes them as dated plain-text snapshots
into the local corpus, where the ordinary ingest picks them up.

**Why snapshots rather than live fetches.** Accreditation evidence is audited:
an assessor must be able to see the same page you cited. A live URL can change
or vanish between the claim and the review, so every snapshot records the URL
and the moment it was taken, and the answer cites that frozen copy.

Only hosts on :attr:`ExternalUrlHarvester.ALLOWED_HOSTS` are ever fetched, so
this can never turn into a general web crawler.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import SETTINGS, Settings
from .documents import ExternalLinkExtractor
from .safety import SOURCE_GUARD, SourceTreeGuard
from .storage import DOCUMENT_CATALOGUE, DocumentCatalogue

LOGGER = logging.getLogger(__name__)


@dataclass
class ExternalPage:
    """One external page to snapshot, with where it was referenced from."""

    url: str
    label: str
    #: Document in the corpus that pointed at this page.
    referenced_by: str = ""

    @property
    def identifier(self) -> str:
        """Trailing path or query segment, used to disambiguate filenames."""
        tail = self.url.rstrip("/").split("/")[-1]
        return re.sub(r"[^A-Za-z0-9_-]+", "-", tail)[:40] or "halaman"


@dataclass
class SnapshotOutcome:
    """Result of one snapshot run, for reporting to the caller."""

    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    written_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dictionary(self) -> dict:
        """Represent the outcome as JSON-serialisable data."""
        return {
            "fetched": self.fetched,
            "skipped": self.skipped,
            "failed": self.failed,
            "written_paths": self.written_paths,
            "errors": self.errors,
        }


class ExternalUrlHarvester:
    """Collects fetchable external URLs already referenced by indexed documents.

    Reading the URLs out of the index rather than from a hard-coded list means
    that adding a lecturer to the mapping spreadsheet is enough — re-index, run
    the snapshot, and their profile is included.
    """

    #: The only hosts this system will ever fetch. Everything else is ignored,
    #: which is what stops a stray URL in a document turning this into a crawler.
    ALLOWED_HOSTS = {
        "sinta.kemdiktisaintek.go.id",
        "sinta.kemdikbud.go.id",
        "sinta.ristekbrin.go.id",
        "scholar.google.com",
        "scholar.google.co.id",
        "garuda.kemdiktisaintek.go.id",
        "garuda.kemdikbud.go.id",
    }

    #: Only *profile* pages are fetched, never individual publication links.
    #: The corpus already contains hundreds of per-article Scholar URLs from
    #: earlier scraping; following those means hundreds of requests and an
    #: immediate rate-limit, while adding nothing a profile page does not list.
    PROFILE_PATTERNS = (
        re.compile(r"^/authors/profile/\d+", re.IGNORECASE),
        re.compile(r"^/author/profile/\d+", re.IGNORECASE),
    )

    #: Scholar profiles are identified by query parameters rather than a path.
    SCHOLAR_PROFILE_HOSTS = ("scholar.google.",)

    def __init__(self, catalogue: DocumentCatalogue) -> None:
        """Bind the harvester to the index it reads links from."""
        self._catalogue = catalogue
        self._link_extractor = ExternalLinkExtractor()

    def harvest(self) -> list[ExternalPage]:
        """Return every allow-listed external page referenced by the corpus."""
        pages: dict[str, ExternalPage] = {}
        for document_name, chunk_text in self._catalogue.find_texts_containing("http"):
            for link in self._link_extractor.extract(chunk_text):
                url = link["url"]
                if url in pages or not self._is_allowed(url):
                    continue
                pages[url] = ExternalPage(
                    url=url, label=link["label"], referenced_by=document_name
                )
        return sorted(pages.values(), key=self._sort_key)

    def _sort_key(self, page: ExternalPage) -> str:
        """Order by label then URL, so runs are reproducible."""
        return f"{page.label}|{page.url}"

    def _is_allowed(self, url: str) -> bool:
        """True when the URL is an allow-listed host *and* a profile page."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        on_allowed_host = any(
            host == allowed or host.endswith("." + allowed)
            for allowed in self.ALLOWED_HOSTS
        )
        return on_allowed_host and self._is_profile_page(parsed)

    def _is_profile_page(self, parsed) -> bool:
        """True when the URL points at an author profile rather than one item."""
        host = (parsed.hostname or "").lower()

        if any(host.startswith(prefix) or f".{prefix}" in f".{host}"
               for prefix in self.SCHOLAR_PROFILE_HOSTS):
            query = parse_qs(parsed.query)
            # A Scholar profile carries `user` but not `view_op=view_citation`,
            # which is what marks a single publication page.
            return "user" in query and "view_citation" not in query.get("view_op", [])

        return any(pattern.match(parsed.path or "") for pattern in self.PROFILE_PATTERNS)


class WebPageReader:
    """Fetches one page and reduces it to a title and readable text."""

    #: A real browser string: SINTA and Scholar both serve reduced or blocked
    #: content to obvious scripts.
    BROWSER_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    #: Tags whose contents are machinery rather than readable content.
    NON_CONTENT_TAGS = ("script", "style", "noscript", "svg", "form")

    def __init__(self, settings: Settings) -> None:
        """Bind the reader to the configured request timeout."""
        self._settings = settings

    def read(self, page: ExternalPage) -> tuple[str, str]:
        """Return ``(title, text)`` for ``page``.

        Raises:
            RuntimeError: when the page cannot be fetched or carries no text.
        """
        import requests
        from bs4 import BeautifulSoup

        try:
            response = requests.get(
                page.url,
                headers=self.BROWSER_HEADERS,
                timeout=self._settings.web_snapshot_timeout_seconds,
            )
            response.raise_for_status()
        except Exception as error:
            raise RuntimeError(f"{type(error).__name__}: {error}")

        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else page.identifier
        for tag_name in self.NON_CONTENT_TAGS:
            for element in soup.find_all(tag_name):
                element.decompose()

        text = "\n".join(
            line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()
        )
        if len(text) < self._settings.web_snapshot_minimum_characters:
            raise RuntimeError(
                f"halaman hanya menghasilkan {len(text)} karakter teks "
                f"(mungkin diblokir atau memerlukan login)"
            )
        return title, text


class SnapshotWriter:
    """Writes a fetched page into the local corpus as a dated text file.

    Plain text rather than raw HTML: the extractor would only strip the markup
    again, and a text file keeps the provenance header readable to a human
    opening it directly.
    """

    _UNSAFE_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

    def __init__(self, settings: Settings, guard: SourceTreeGuard) -> None:
        """Bind the writer to the snapshot directory, verifying it is writable."""
        self._settings = settings
        self._directory: Path = guard.assert_writable(settings.web_snapshot_directory)

    @property
    def directory(self) -> Path:
        """Where snapshots are written."""
        return self._directory

    def write(self, page: ExternalPage, title: str, text: str) -> Path:
        """Write one snapshot and return the path it was saved to."""
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._directory / self._build_filename(page, title)
        target.write_text(self._build_document(page, title, text), encoding="utf-8")
        return target

    def _build_filename(self, page: ExternalPage, title: str) -> str:
        """Build a stable, readable filename for this page.

        The identifier is included so two lecturers with similar page titles
        cannot overwrite one another, and so a re-run replaces the previous
        snapshot of the same URL rather than accumulating copies.
        """
        cleaned_title = self._UNSAFE_FILENAME_CHARACTERS.sub(" ", title).strip()
        cleaned_title = " ".join(cleaned_title.split())[:70] or "Halaman"
        return f"{page.label} - {cleaned_title} [{page.identifier}].txt"

    #: Body lines between repeats of the provenance marker.
    PROVENANCE_INTERVAL_LINES = 25

    def _build_document(self, page: ExternalPage, title: str, text: str) -> str:
        """Render the snapshot with provenance repeated through the body.

        A header alone is not enough. Retrieval works on chunks, and a chunk cut
        from the middle of a long profile would carry no trace of where it came
        from — so the answer could not offer a link back to the live page, and
        the model would not know the passage was a SINTA record at all.
        Repeating a short marker makes every chunk self-describing.
        """
        captured_at = datetime.now().isoformat(timespec="seconds")
        header = (
            f"{title}\n"
            f"Sumber: {page.label}\n"
            f"URL: {page.url}\n"
            f"Diambil pada: {captured_at}\n"
            f"Dirujuk oleh: {page.referenced_by}\n"
            f"{'-' * 70}\n"
        )
        return header + self._interleave_provenance(page, title, text)

    def _interleave_provenance(self, page: ExternalPage, title: str, text: str) -> str:
        """Insert a compact source marker every so many lines of body text."""
        marker = f"[{page.label} · {title[:60]} · {page.url}]"
        lines = text.splitlines()
        rendered: list[str] = []

        for index, line in enumerate(lines):
            if index and index % self.PROVENANCE_INTERVAL_LINES == 0:
                rendered.append(marker)
            rendered.append(line)
        rendered.append(marker)
        return "\n".join(rendered) + "\n"


class WebSnapshotRunner:
    """Harvests URLs, fetches each page, and stores it in the local corpus."""

    def __init__(
        self,
        settings: Settings,
        catalogue: DocumentCatalogue,
        guard: SourceTreeGuard,
    ) -> None:
        """Compose the harvester, reader and writer this run needs."""
        self._settings = settings
        self._harvester = ExternalUrlHarvester(catalogue)
        self._reader = WebPageReader(settings)
        self._writer = SnapshotWriter(settings, guard)

    @property
    def snapshot_directory(self) -> Path:
        """Where snapshots are written."""
        return self._writer.directory

    def run_with_browser(self, progress_callback=None) -> SnapshotOutcome:
        """Snapshot through a real browser: SINTA signed in, plus seed pages.

        This is the complete path. It signs in to SINTA so paginated
        publication lists are visible, expands each configured seed into the
        detail pages behind it, and renders everything in a browser so
        script-built pages and bot checks are handled.
        """
        from .browser_sources import BrowserPageReader, BrowserUnavailable

        outcome = SnapshotOutcome()
        try:
            reader = BrowserPageReader(self._settings)
        except BrowserUnavailable as error:
            outcome.errors.append(str(error))
            outcome.failed += 1
            return outcome

        with reader:
            reader.sign_in_to_sinta()
            pages = self._harvester.harvest() + self._expand_seeds(reader)
            LOGGER.info("Total %d halaman untuk diambil lewat browser", len(pages))

            for index, page in enumerate(pages, start=1):
                try:
                    title, text = reader.read(page)
                    written = self._writer.write(page, title, text)
                    outcome.fetched += 1
                    outcome.written_paths.append(str(written))
                    LOGGER.info("Tersimpan: %s", written.name)
                except Exception as error:
                    outcome.failed += 1
                    outcome.errors.append(f"{page.label} {page.url}: {error}")
                    LOGGER.warning("Gagal mengambil %s: %s", page.url, error)

                if progress_callback is not None:
                    progress_callback(index, len(pages), f"{page.label} — {page.identifier}")
        return outcome

    def _parse_seed(self, entry: str) -> tuple[str, str]:
        """Split a seed entry into its URL and the link pattern to follow.

        Entries take the form ``url`` or ``url::regex``. The separator is ``::``
        rather than a single colon so it cannot be confused with the one in
        ``https://``.
        """
        url, separator, pattern = entry.partition("::")
        if separator and pattern.strip():
            return url.strip(), pattern.strip()
        return url.strip(), self._settings.browser_detail_link_pattern

    def _expand_seeds(self, reader) -> list[ExternalPage]:
        """Turn each configured seed into itself plus the pages it links to.

        A listing page is rarely the substance. A PDDikti search result indexes
        per-lecturer and per-programme pages; the university's
        ``/praktisi-akademisi`` page indexes one page per lecturer. Each seed
        therefore carries its own pattern for which links are worth following.
        """
        expanded: list[ExternalPage] = []
        for entry in self._settings.browser_seed_urls:
            seed_url, pattern = self._parse_seed(entry)
            if not seed_url:
                continue
            label = self._label_for(seed_url)
            expanded.append(ExternalPage(url=seed_url, label=label, referenced_by="(seed)"))

            try:
                detail_urls = reader.collect_links(
                    seed_url, pattern, self._settings.browser_maximum_detail_pages
                )
            except Exception as error:
                LOGGER.warning("Gagal menelusuri tautan dari %s: %s", seed_url, error)
                continue

            LOGGER.info(
                "%s (%s): %d halaman detail ditemukan", label, pattern, len(detail_urls)
            )
            expanded.extend(
                ExternalPage(url=detail_url, label=label, referenced_by=seed_url)
                for detail_url in detail_urls
            )
        return expanded

    def _label_for(self, url: str) -> str:
        """Return a readable source label for a seed URL."""
        host = urlparse(url).hostname or ""
        if "pddikti" in host:
            return "PDDikti"
        if "pradita" in host:
            return "Universitas Pradita"
        return host.removeprefix("www.") or "Web"

    def run(self, progress_callback=None) -> SnapshotOutcome:
        """Snapshot every allow-listed page the corpus references, over HTTP.

        Kept for sources that do not need a browser; :meth:`run_with_browser`
        is the complete path.
        """
        pages = self._harvester.harvest()
        outcome = SnapshotOutcome()
        LOGGER.info("Menemukan %d halaman eksternal untuk diambil", len(pages))

        for index, page in enumerate(pages, start=1):
            try:
                title, text = self._reader.read(page)
                written = self._writer.write(page, title, text)
                outcome.fetched += 1
                outcome.written_paths.append(str(written))
                LOGGER.info("Tersimpan: %s", written.name)
            except RuntimeError as error:
                outcome.failed += 1
                outcome.errors.append(f"{page.label} {page.url}: {error}")
                LOGGER.warning("Gagal mengambil %s: %s", page.url, error)

            if progress_callback is not None:
                progress_callback(index, len(pages), f"{page.label} — {page.identifier}")

            # A courtesy pause: these are public services, and Scholar in
            # particular starts refusing requests that arrive back to back.
            if index < len(pages):
                time.sleep(self._settings.web_snapshot_delay_seconds)

        return outcome


#: Shared runner bound to the process-wide index and guard.
WEB_SNAPSHOT_RUNNER = WebSnapshotRunner(SETTINGS, DOCUMENT_CATALOGUE, SOURCE_GUARD)
