"""Crawling public websites into the local corpus.

The corpus of this system is a set of public government websites on export and
import. Each one is described by a :class:`CrawlSite` in :mod:`app.crawl_sites`:
the hosts it lives on, where the content sits in its pages, and the sections to
walk. This module walks those sections and writes every page into
``SOURCE_DIR/<site>/<section>/`` as a dated plain-text snapshot, in the same
format :mod:`app.web_sources` uses. Linked documents (regulation PDFs, forms)
are downloaded beside them as the original files.

The site folder is the corpus's top level, so the folder filter in the
interface becomes a filter by source: "Bea Cukai", "JDIH Kemenkeu", …

**Why snapshots rather than live fetches.** An answer must be checkable: the
page a citation points to can change after it was quoted, so every snapshot
records its URL and the moment it was taken, and the answer cites that frozen
copy while still linking to the live page.

**The one writer of the corpus.** Everywhere else the corpus is read-only —
ingest and the web server go through :class:`~app.safety.SourceTreeGuard`
and can never modify it. The crawl is the step that *produces* the corpus,
so :class:`CorpusPageWriter` is the single component allowed to write there,
and it refuses any path outside ``SOURCE_DIR``.

**Courtesy.** Only a site's own hosts are fetched, the paths its robots.txt
disallows are never requested, and requests are spaced by ``CRAWL_DELAY``
seconds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse

from .config import SETTINGS, Settings
from .safety import SOURCE_GUARD, SourceTreeGuard
from .web_sources import ExternalPage, SnapshotWriter, WebPageReader

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrawlSection:
    """One part of a site, crawled into its own corpus folder.

    A section is either a *listing* — an index whose links matching
    ``detail_pattern`` are followed, page by page when ``paginate`` is set —
    or a fixed set of pages, or both.
    """

    folder: str
    #: Index pages whose matching links are followed.
    listing_urls: tuple[str, ...] = ()
    #: Regex on the URL path selecting which links on a listing are pages.
    detail_pattern: str = r"^$"
    #: Pages fetched as they are, regardless of any listing.
    fixed_urls: tuple[str, ...] = ()
    paginate: bool = True
    #: Detail pages taken from *each* listing in a sample crawl.
    sample_limit_per_listing: int = 20
    #: Attachments (PDF and the like) downloaded in a sample crawl.
    sample_attachment_limit: int = 10
    #: Optional sections are crawled only when named explicitly.
    optional: bool = False
    #: Regex a detail page's title must match to be kept. A page that fails it
    #: is remembered as out of scope, so later crawls do not fetch it again.
    title_pattern: str = ""
    #: Download attachments smallest first, measured with a HEAD request, so
    #: most documents arrive early and a file over the size limit is never
    #: downloaded at all.
    smallest_attachments_first: bool = False


@dataclass(frozen=True)
class CrawlSite:
    """One website: where it lives, how its pages are built, and what to crawl."""

    #: Name on the command line: ``main.py crawl --site beacukai``.
    key: str
    #: Top-level corpus folder, which doubles as the interface's folder filter.
    folder: str
    #: Canonical origin every page URL is rewritten to.
    root: str
    hosts: frozenset[str]
    sections: tuple[CrawlSection, ...]
    #: Paths robots.txt disallows; never requested.
    disallowed_prefixes: tuple[str, ...] = ()
    #: ``(regex, replacement)`` applied to every path, so aliases of one page
    #: collapse to a single URL and the page is fetched once.
    path_rewrites: tuple[tuple[str, str], ...] = ()
    #: Content containers in order of preference.
    content_selectors: tuple[str, ...] = ("main", "body")
    #: Where listing links are read from, before any furniture is removed.
    #: Empty means the content block after furniture removal.
    link_region_selectors: tuple[str, ...] = ()
    #: Page furniture removed from the content block: menus, share buttons,
    #: counters that change on every visit.
    furniture_selectors: tuple[str, ...] = ()
    #: Direct children of the content block dropped when their text starts
    #: with one of these — boilerplate that has no selector of its own.
    boilerplate_prefixes: tuple[str, ...] = ()
    #: Regex removed from the ``<title>``, typically the site-name suffix.
    title_suffix: str = ""
    #: Hosts serving the site's downloads (a file CDN). Only attachment links
    #: are taken from them.
    attachment_hosts: frozenset[str] = frozenset()
    #: Path regexes marking download links that carry no file extension.
    attachment_path_patterns: tuple[str, ...] = ()
    #: Path regexes of downloads never taken, such as an abstract published
    #: beside the full text.
    attachment_exclude_patterns: tuple[str, ...] = ()
    #: Query parameter a paginated listing takes, counted from 0.
    page_parameter: str = "page"

    @property
    def label(self) -> str:
        """Short name of the site for provenance lines: ``beacukai.go.id``."""
        return (urlparse(self.root).hostname or self.key).removeprefix("www.")


class SiteUrl:
    """Normalises links found on one site and decides which may be fetched."""

    ATTACHMENT_EXTENSIONS = (".pdf", ".docx", ".xlsx", ".pptx")

    def __init__(self, site: CrawlSite) -> None:
        """Compile the site's rewrite and attachment patterns once."""
        self._site = site
        self._rewrites = [(re.compile(pattern), replacement)
                          for pattern, replacement in site.path_rewrites]
        self._attachment_paths = [re.compile(pattern) for pattern in site.attachment_path_patterns]
        self._excluded_attachments = [re.compile(pattern) for pattern in site.attachment_exclude_patterns]

    def normalise(self, href: str, base_url: str) -> str | None:
        """Return an absolute, canonical URL on this site, or None.

        Site markup is inconsistent — links arrive as ``/index.php/id/…``, as
        bare hostnames, even with Windows separators. All of these collapse to
        one form so a page is fetched once.
        """
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return None
        absolute = urljoin(base_url, href.strip().replace("\\", "/"))
        parsed = urlparse(absolute)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"}:
            return None
        query = f"?{parsed.query}" if parsed.query else ""
        if host in self._site.attachment_hosts and host not in self._site.hosts:
            url = f"{parsed.scheme}://{host}{parsed.path}{query}"
            return url if self.is_attachment(url) else None
        if host not in self._site.hosts:
            return None
        path = parsed.path or "/"
        for pattern, replacement in self._rewrites:
            path = pattern.sub(replacement, path)
        path = path or "/"
        if any(path.startswith(prefix) for prefix in self._site.disallowed_prefixes):
            return None
        return f"{self._site.root}{path}{query}"

    @staticmethod
    def path_of(url: str) -> str:
        """Return the path of ``url`` without a trailing slash."""
        return urlparse(url).path.rstrip("/") or "/"

    def is_attachment(self, url: str) -> bool:
        """True when ``url`` points at a downloadable document file."""
        path = urlparse(url).path
        return path.lower().endswith(self.ATTACHMENT_EXTENSIONS) or any(
            pattern.search(path) for pattern in self._attachment_paths
        )

    def is_wanted_attachment(self, url: str) -> bool:
        """True when ``url`` is a document file the site's plan does not exclude."""
        path = urlparse(url).path
        return self.is_attachment(url) and not any(
            pattern.search(path) for pattern in self._excluded_attachments
        )

    def with_page(self, url: str, page_number: int) -> str:
        """Return ``url`` asking for one page of a paginated listing."""
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{self._site.page_parameter}={page_number}"


@dataclass
class ParsedPage:
    """What one fetched page contributes: its title, text and outbound links."""

    title: str
    text: str
    links: list[str] = field(default_factory=list)
    attachments: list[tuple[str, str]] = field(default_factory=list)


class PageParser:
    """Reduces a page to the text of its main content block.

    Every page of a site shares a header menu, a footer and often a sidebar.
    Indexed as-is, that boilerplate would appear in every chunk and match every
    question, so only the content block is kept.
    """

    #: Elements that start a new line; everything else flows inline.
    BLOCK_TAGS = (
        "p", "div", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "h5", "h6",
        "tr", "table", "section", "article", "blockquote", "pre", "dt", "dd",
    )

    def __init__(self, site: CrawlSite) -> None:
        """Bind the parser to the page structure of one site."""
        self._site = site
        self._urls = SiteUrl(site)
        self._title_suffix = re.compile(site.title_suffix, re.IGNORECASE) if site.title_suffix else None

    def parse(self, html: str, url: str) -> ParsedPage:
        """Return the title, readable text, links and attachments of ``html``."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title = self._title_of(soup)

        # A listing's entries can sit in a block beside the main one, so when
        # the site names a link region, links are read before anything is removed.
        region = self._first(soup, self._site.link_region_selectors)
        links = self._links_in(region, url) if region is not None else None

        content = self._first(soup, self._site.content_selectors) or soup
        for tag_name in WebPageReader.NON_CONTENT_TAGS:
            for element in content.find_all(tag_name):
                element.decompose()
        for selector in self._site.furniture_selectors:
            for element in content.select(selector):
                element.decompose()
        if self._site.boilerplate_prefixes:
            for child in content.find_all(recursive=False):
                if child.get_text(" ", strip=True).startswith(self._site.boilerplate_prefixes):
                    child.decompose()

        if links is None:
            links = self._links_in(content, url)
        attachments = [
            (label, link) for label, link in self._labelled_links_in(content, url)
            if self._urls.is_wanted_attachment(link)
        ]
        return ParsedPage(
            title=title, text=self._readable_text(content), links=links, attachments=attachments
        )

    def _first(self, soup, selectors: tuple[str, ...]):
        """Return the element matching the first selector that matches anything."""
        return next(
            (soup.select_one(selector) for selector in selectors
             if soup.select_one(selector) is not None),
            None,
        )

    def _readable_text(self, content) -> str:
        """Return the text of ``content`` with one line per block element.

        ``get_text(separator="\\n")`` would put every inline element on a line
        of its own — "wajib pajak (", "NPWP", ")" — which fragments sentences
        across chunks and buries them among provenance markers. Line breaks
        are inserted only around block elements instead, and table cells are
        separated by " | " so a row stays readable as one line. Line breaks
        inside the source text are only the author's editor wrapping a long
        line, so they are flattened first; ``<pre>`` keeps its own.
        """
        for string in content.find_all(string=True):
            if "\n" in string and string.find_parent("pre") is None:
                string.replace_with(re.sub(r"\s+", " ", str(string)))
        for element in content.find_all(self.BLOCK_TAGS):
            element.insert_before("\n")
            element.insert_after("\n")
        for cell in content.find_all(["td", "th"]):
            cell.insert_after(" | ")
        lines = (" ".join(line.split()) for line in content.get_text().splitlines())
        return "\n".join(line for line in lines if line)

    def _title_of(self, soup) -> str:
        """Return the page title without the site-name suffix."""
        raw_title = soup.title.get_text(strip=True) if soup.title else ""
        title = self._title_suffix.sub("", raw_title).strip() if self._title_suffix else raw_title
        if not title:
            heading = soup.find("h1")
            title = heading.get_text(" ", strip=True) if heading else title
        return title or "Halaman"

    def _links_in(self, element, base_url: str) -> list[str]:
        """Return the unique normalised links inside ``element``, in page order."""
        return list(dict.fromkeys(link for _label, link in self._labelled_links_in(element, base_url)))

    def _labelled_links_in(self, element, base_url: str) -> list[tuple[str, str]]:
        """Return ``(anchor text, normalised URL)`` for each fetchable link."""
        found: list[tuple[str, str]] = []
        for anchor in element.select("a[href]"):
            link = self._urls.normalise(anchor["href"], base_url)
            if link is not None:
                found.append((anchor.get_text(" ", strip=True), link))
        return found


class UnsupportedAttachment(Exception):
    """A download link that turned out not to be a document the corpus reads."""


class PoliteFetcher:
    """Polite HTTP access to one site: one session, spaced requests, retries."""

    RETRY_ATTEMPTS = 3

    #: Document types the extractors read, by the type a server declares.
    CONTENT_TYPE_SUFFIXES = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    }

    def __init__(self, settings: Settings) -> None:
        """Open a session carrying browser-like headers."""
        import requests

        self._settings = settings
        self._session = requests.Session()
        self._session.headers.update(WebPageReader.BROWSER_HEADERS)
        self._last_request_at = 0.0

    def get_text(self, url: str) -> str:
        """Return the HTML of ``url``.

        Raises:
            RuntimeError: when the page still fails after the retries.
        """
        response = self._request(url, stream=False)
        response.encoding = response.encoding or "utf-8"
        return response.text

    def size_of(self, url: str) -> int | None:
        """Return the size in bytes ``url`` declares, or None when it does not say.

        One attempt only: the answer merely orders the downloads, and a file
        whose size is unknown is still downloaded, just last.
        """
        try:
            response = self._request(url, stream=False, method="HEAD", attempts=1)
        except RuntimeError:
            return None
        length = response.headers.get("Content-Length", "")
        return int(length) if length.isdigit() and int(length) > 0 else None

    def download(
        self, url: str, target_for: Callable[[str], Path], maximum_bytes: int
    ) -> tuple[Path, int]:
        """Stream ``url`` to the path ``target_for(filename)`` returns.

        The filename comes from the server when it names one, so a download
        link without an extension still lands as ``something.pdf``. The file
        is written under a temporary name and renamed only when complete, so
        an interrupted download never leaves a truncated PDF that the next
        ingest would try to read.

        Returns:
            The path written and its size in bytes.

        Raises:
            UnsupportedAttachment: when the link is not a readable document.
            RuntimeError: on failure, or when the file exceeds ``maximum_bytes``.
        """
        response = self._request(url, stream=True)
        try:
            target = target_for(self._filename_of(response, url))
        except UnsupportedAttachment:
            response.close()
            raise
        partial = target.with_name(target.name + ".part")
        written = 0
        try:
            with open(partial, "wb") as handle:
                for block in response.iter_content(chunk_size=256 * 1024):
                    written += len(block)
                    if written > maximum_bytes:
                        raise RuntimeError(
                            f"lebih besar dari {maximum_bytes // (1024 * 1024)} MB"
                        )
                    handle.write(block)
            partial.replace(target)
        finally:
            response.close()
            partial.unlink(missing_ok=True)
        return target, written

    def _filename_of(self, response, url: str) -> str:
        """Return the document's filename, with an extension the corpus reads.

        Raises:
            UnsupportedAttachment: when neither the server nor the URL names a
                document type the extractors support.
        """
        disposition = response.headers.get("Content-Disposition", "")
        match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition, re.IGNORECASE)
        name = unquote(match.group(1)).strip() if match else unquote(Path(urlparse(url).path).name)
        suffix = Path(name).suffix.lower()
        if suffix in SiteUrl.ATTACHMENT_EXTENSIONS:
            return name
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        declared = self.CONTENT_TYPE_SUFFIXES.get(content_type)
        if declared is None:
            raise UnsupportedAttachment(content_type or "tipe tidak diketahui")
        return f"{Path(name).stem or 'lampiran'}{declared}"

    def _request(self, url: str, stream: bool, method: str = "GET", attempts: int | None = None):
        """Perform one request with courtesy spacing and retries on transient errors."""
        import requests

        attempts = attempts or self.RETRY_ATTEMPTS
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._wait_for_turn()
            try:
                response = self._session.request(
                    method, url, timeout=self._settings.crawl_timeout_seconds,
                    stream=stream, allow_redirects=True,
                )
                if response.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                last_error = error
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status is not None and status < 500:
                    break
                LOGGER.info("Percobaan %d gagal untuk %s: %s", attempt, url, error)
                if attempt < attempts:
                    time.sleep(5 * attempt)
        raise RuntimeError(f"{type(last_error).__name__}: {last_error}")

    def _wait_for_turn(self) -> None:
        """Sleep until the configured delay has passed since the last request."""
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._settings.crawl_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()


class CorpusPageWriter(SnapshotWriter):
    """Writes crawled pages and attachments into ``SOURCE_DIR/<site>/<section>/``.

    The snapshot format is inherited unchanged: a provenance header and a
    source marker repeated through the body, so every chunk carries its URL.
    """

    _PERATURAN_DATE_LINE = "Tanggal Peraturan"

    def __init__(self, settings: Settings, site: CrawlSite) -> None:
        """Bind the writer to the corpus directory and the site it fills."""
        self._settings = settings
        self._site = site
        self._directory = Path(settings.source_directory).resolve()

    def section_directory(self, section: CrawlSection) -> Path:
        """Return (creating it) the folder one section is written to."""
        directory = self._inside_corpus(self._directory / self._site.folder / section.folder)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_page(self, section: CrawlSection, page: ExternalPage, parsed: ParsedPage) -> Path:
        """Write one page snapshot and return its path."""
        text = parsed.text
        if parsed.attachments:
            listed = "\n".join(
                f"- {label or unquote(Path(urlparse(link).path).name)}: {link}"
                for label, link in parsed.attachments
            )
            text = f"{text}\nLampiran dan dokumen terkait:\n{listed}"

        target = self.section_directory(section) / self._page_filename(page, parsed)
        target.write_text(self._build_document(page, parsed.title, text), encoding="utf-8")
        return target

    def attachment_target(self, section: CrawlSection, url: str, filename: str) -> Path:
        """Return where an attachment from ``url`` named ``filename`` is saved.

        The original filename is kept so the file is recognisable, followed by
        a short hash of the URL so two uploads that share a name never collide.
        """
        stem, suffix = Path(filename).stem, Path(filename).suffix.lower()
        cleaned = self._clean(stem)[:100] or "lampiran"
        directory = self.section_directory(section) / "Lampiran"
        directory.mkdir(parents=True, exist_ok=True)
        return self._inside_corpus(directory / f"{cleaned} [{self._short_hash(url)}]{suffix}")

    def _page_filename(self, page: ExternalPage, parsed: ParsedPage) -> str:
        """Return a readable, unique filename for a page snapshot.

        A regulation is prefixed with its number — "PER-8/PJ/2026" — because
        that is how people search for and refer to it; the long all-caps title
        alone is hard to tell apart in a file list.
        """
        name = parsed.title
        lines = parsed.text.splitlines()
        if len(lines) > 2 and lines[2].strip() == self._PERATURAN_DATE_LINE:
            document_type, number = lines[0].strip(), lines[1].strip()
            # "PER-8/PJ/2026" names itself; "7 TAHUN 2021" needs its type.
            if number[:1].isdigit():
                number = f"{self._TYPE_ABBREVIATIONS.get(document_type, document_type)} {number}"
            name = f"{number} - {parsed.title}"
        return f"{self._clean(name)[:110] or 'Halaman'} [{self._short_hash(page.url)}].txt"

    #: Customary short forms of regulation types, as people search for them.
    _TYPE_ABBREVIATIONS = {
        "Undang-Undang": "UU",
        "Peraturan Pemerintah": "PP",
        "Peraturan Menteri Keuangan": "PMK",
        "Keputusan Menteri Keuangan": "KMK",
        "Peraturan Dirjen Pajak": "PER",
        "Keputusan Dirjen Pajak": "KEP",
        "Peraturan Presiden": "Perpres",
    }

    def _clean(self, name: str) -> str:
        """Make ``name`` safe as a Windows filename."""
        cleaned = self._UNSAFE_FILENAME_CHARACTERS.sub("-", name)
        return " ".join(cleaned.split()).strip(" .-")

    def _short_hash(self, url: str) -> str:
        """Return eight hex characters identifying ``url``."""
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]

    def _inside_corpus(self, path: Path) -> Path:
        """Refuse any write target that is not inside the corpus directory."""
        resolved = path.resolve()
        if resolved != self._directory and self._directory not in resolved.parents:
            raise RuntimeError(f"Menolak menulis di luar korpus: {resolved}")
        return resolved


@dataclass
class CrawlOutcome:
    """Result of one crawl, for reporting to the caller."""

    pages_written: int = 0
    pages_unchanged: int = 0
    #: Pages with no text worth indexing — embedded videos, bare file links,
    #: pages whose content only JavaScript fills in.
    pages_skipped: int = 0
    #: Pages whose text is identical to one already written — one page
    #: reachable under two addresses.
    pages_duplicate: int = 0
    #: Pages whose title falls outside the section's scope.
    pages_out_of_scope: int = 0
    attachments_written: int = 0
    #: Attachments whose declared size exceeds the limit, never downloaded.
    attachments_too_large: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class CrawlManifest:
    """Remembers which URL was saved to which file, across crawls of one site.

    Kept in the data directory rather than the corpus, where a JSON file
    would itself be indexed. It is what lets a second crawl skip pages it
    already has instead of re-downloading the whole site.
    """

    def __init__(self, settings: Settings, guard: SourceTreeGuard, site: CrawlSite) -> None:
        """Load the site's manifest, verifying its location is outside the corpus."""
        self._path = guard.assert_writable(settings.data_directory / f"crawl-{site.key}.json")
        self._corpus = Path(settings.source_directory).resolve()
        self._entries: dict[str, dict] = {}
        if self._path.exists():
            self._entries = json.loads(self._path.read_text(encoding="utf-8"))

    def has_file_for(self, url: str) -> bool:
        """True when ``url`` was saved before and the file is still present."""
        entry = self._entries.get(url) or {}
        return "file" in entry and (self._corpus / entry["file"]).exists()

    def is_out_of_scope(self, url: str) -> bool:
        """True when ``url`` was fetched before and judged outside the plan's scope."""
        return "out_of_scope" in (self._entries.get(url) or {})

    def record_out_of_scope(self, url: str, reason: str) -> None:
        """Remember that ``url`` holds nothing the corpus wants, and why."""
        self._entries[url] = {
            "out_of_scope": reason,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }

    def record(
        self, url: str, written: Path, attachments: list[tuple[str, str]] | None = None
    ) -> None:
        """Remember that ``url`` was saved to ``written``, with its attachment links."""
        entry = {
            "file": str(written.relative_to(self._corpus)),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        if attachments:
            entry["attachments"] = [[label, link] for label, link in attachments]
        self._entries[url] = entry

    def attachments_of(self, url: str) -> list[tuple[str, str]]:
        """Return the ``(label, link)`` attachments recorded for page ``url``.

        A page that is not fetched again still hands its attachments to the
        download queue this way, so one that failed on a dropped connection
        is retried by the next crawl instead of being lost.
        """
        entry = self._entries.get(url) or {}
        return [(label, link) for label, link in entry.get("attachments", [])]

    def save(self) -> None:
        """Persist the manifest."""
        self._path.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=1), encoding="utf-8"
        )


class SiteCrawler:
    """Walks one site's crawl plan and fills the corpus."""

    #: A content block shorter than this is navigation, not a page worth indexing.
    MINIMUM_TEXT_CHARACTERS = 150

    #: Pages between manifest saves, so an interrupted full crawl resumes
    #: close to where it stopped rather than at the start of the section.
    MANIFEST_SAVE_INTERVAL = 50

    def __init__(self, settings: Settings, guard: SourceTreeGuard) -> None:
        """Keep the settings and guard every crawl is bound to."""
        self._settings = settings
        self._guard = guard
        self._page_limit: int | None = None

    def run(
        self,
        site: CrawlSite,
        full: bool = False,
        section_names: list[str] | None = None,
        refresh: bool = False,
        page_limit: int | None = None,
        progress_callback=None,
    ) -> CrawlOutcome:
        """Crawl the selected sections of ``site`` and return what was written.

        Args:
            site: the website to crawl.
            full: lift the sample limits and crawl every page of each listing.
            section_names: folders to crawl; default is every non-optional one.
            refresh: fetch pages again even when a snapshot already exists.
            page_limit: overrides the sample limits — at most this many pages
                per listing, fixed pages per section, and attachments per
                section. Useful for a quick first corpus.
        """
        self._page_limit = None if full else page_limit
        fetcher = PoliteFetcher(self._settings)
        parser = PageParser(site)
        urls = SiteUrl(site)
        writer = CorpusPageWriter(self._settings, site)
        manifest = CrawlManifest(self._settings, self._guard, site)
        outcome = CrawlOutcome()
        #: Text fingerprint -> the file it was written to, for spotting one
        #: page served under two addresses.
        written_texts: dict[str, Path] = {}

        try:
            for section in self._select_sections(site, section_names):
                LOGGER.info("=== %s / %s ===", site.folder, section.folder)
                pages = self._collect_pages(site, section, fetcher, parser, urls, full)
                LOGGER.info("%s: %d halaman", section.folder, len(pages))
                attachments: dict[str, str] = {}

                for index, page in enumerate(pages, start=1):
                    if not refresh and manifest.is_out_of_scope(page.url):
                        outcome.pages_out_of_scope += 1
                        continue
                    if not refresh and manifest.has_file_for(page.url):
                        outcome.pages_unchanged += 1
                        for label, link in manifest.attachments_of(page.url):
                            attachments.setdefault(link, label)
                        continue
                    try:
                        parsed = parser.parse(fetcher.get_text(page.url), page.url)
                        self._keep_page(
                            section, page, parsed, writer, manifest, outcome, attachments, written_texts
                        )
                    except Exception as error:
                        outcome.failed += 1
                        outcome.errors.append(f"{page.url}: {error}")
                        LOGGER.warning("Gagal mengambil %s: %s", page.url, error)
                    if progress_callback is not None:
                        progress_callback(f"{site.folder}/{section.folder}", index, len(pages), page.url)
                    if index % self.MANIFEST_SAVE_INTERVAL == 0:
                        manifest.save()

                self._download_attachments(
                    section, attachments, fetcher, writer, manifest, outcome, full, refresh
                )
                manifest.save()
        finally:
            manifest.save()
        return outcome

    def _keep_page(
        self,
        section: CrawlSection,
        page: ExternalPage,
        parsed: ParsedPage,
        writer: CorpusPageWriter,
        manifest: CrawlManifest,
        outcome: CrawlOutcome,
        attachments: dict[str, str],
        written_texts: dict[str, Path],
    ) -> None:
        """Write one fetched page unless it is out of scope, empty, or a duplicate.

        An in-scope page queues its attachments even when it has too little
        text of its own, because many FAQ entries are only a PDF.
        """
        if section.title_pattern and not re.search(section.title_pattern, parsed.title, re.IGNORECASE):
            manifest.record_out_of_scope(page.url, f"judul di luar cakupan: {parsed.title}")
            outcome.pages_out_of_scope += 1
            LOGGER.info("Di luar cakupan: %s (%s)", parsed.title, page.url)
            return
        for label, link in parsed.attachments:
            attachments.setdefault(link, label)
        fingerprint = hashlib.sha1(parsed.text.encode("utf-8")).hexdigest()
        if len(parsed.text) < self.MINIMUM_TEXT_CHARACTERS:
            outcome.pages_skipped += 1
            LOGGER.info("Dilewati (tanpa teks): %s", page.url)
        elif fingerprint in written_texts:
            # Recorded against the copy already written, so a later crawl,
            # which skips that copy, still knows this address is covered and
            # does not write it again.
            manifest.record(page.url, written_texts[fingerprint])
            outcome.pages_duplicate += 1
            LOGGER.info("Dilewati (isi sama dengan halaman lain): %s", page.url)
        else:
            written = writer.write_page(section, page, parsed)
            written_texts[fingerprint] = written
            manifest.record(page.url, written, parsed.attachments)
            outcome.pages_written += 1

    def _select_sections(
        self, site: CrawlSite, section_names: list[str] | None
    ) -> list[CrawlSection]:
        """Return the sections to crawl, validating any names given."""
        if not section_names:
            return [section for section in site.sections if not section.optional]
        wanted = {name.strip().lower() for name in section_names}
        selected = [section for section in site.sections if section.folder.lower() in wanted]
        unknown = wanted - {section.folder.lower() for section in selected}
        if unknown:
            known = ", ".join(section.folder for section in site.sections)
            raise ValueError(f"Bagian tidak dikenal: {', '.join(sorted(unknown))}. Pilihan: {known}")
        return selected

    def _collect_pages(
        self,
        site: CrawlSite,
        section: CrawlSection,
        fetcher: PoliteFetcher,
        parser: PageParser,
        urls: SiteUrl,
        full: bool,
    ) -> list[ExternalPage]:
        """Return the pages of one section: its fixed pages plus listed details."""
        label = f"{site.label} · {section.folder}"
        fixed_urls = section.fixed_urls
        if self._page_limit is not None:
            fixed_urls = fixed_urls[: self._page_limit]
        pages = {url: ExternalPage(url=url, label=label, referenced_by="(tetap)") for url in fixed_urls}
        detail_pattern = re.compile(section.detail_pattern)
        limit = None if full else section.sample_limit_per_listing
        if self._page_limit is not None and limit:
            limit = min(limit, self._page_limit)

        for listing_url in section.listing_urls:
            if limit == 0:
                break
            for detail_url in self._walk_listing(
                listing_url, detail_pattern, section.paginate, limit, fetcher, parser, urls
            ):
                pages.setdefault(
                    detail_url, ExternalPage(url=detail_url, label=label, referenced_by=listing_url)
                )
        return list(pages.values())

    def _walk_listing(
        self,
        listing_url: str,
        detail_pattern: re.Pattern,
        paginate: bool,
        limit: int | None,
        fetcher: PoliteFetcher,
        parser: PageParser,
        urls: SiteUrl,
    ) -> list[str]:
        """Follow one listing page by page until ``limit`` or the last page."""
        found: list[str] = []
        seen: set[str] = set()
        page_number = 0
        while limit is None or len(found) < limit:
            url = urls.with_page(listing_url, page_number) if paginate else listing_url
            try:
                links = self._listing_links(fetcher.get_text(url), url, parser, urls)
            except Exception as error:
                LOGGER.warning("Gagal membaca daftar %s: %s", url, error)
                break

            new_links = [
                link for link in links
                if detail_pattern.match(urls.path_of(link))
                and not urls.is_attachment(link)
                and link not in seen
            ]
            # Detail links carry no query; a "?page=" link is the pager itself.
            new_links = [link for link in new_links if "?" not in link]
            if not new_links:
                break
            found.extend(new_links)
            seen.update(new_links)
            if not paginate:
                break
            page_number += 1
        return found if limit is None else found[:limit]

    @staticmethod
    def _listing_links(text: str, url: str, parser: PageParser, urls: SiteUrl) -> list[str]:
        """Return the links a listing offers: a sitemap's ``<loc>`` entries, or a page's anchors."""
        if "<urlset" in text[:2000]:
            locations = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)
            return list(dict.fromkeys(
                link for link in (urls.normalise(location, url) for location in locations) if link
            ))
        return parser.parse(text, url).links

    def _download_attachments(
        self,
        section: CrawlSection,
        attachments: dict[str, str],
        fetcher: PoliteFetcher,
        writer: CorpusPageWriter,
        manifest: CrawlManifest,
        outcome: CrawlOutcome,
        full: bool,
        refresh: bool,
    ) -> None:
        """Download the attachments linked from a section's pages."""
        maximum_bytes = int(self._settings.crawl_attachment_maximum_megabytes * 1024 * 1024)
        links = [link for link in attachments if refresh or not manifest.has_file_for(link)]
        if section.smallest_attachments_first:
            links = self._smallest_first(links, fetcher, maximum_bytes, outcome)
        if not full:
            limit = section.sample_attachment_limit
            if self._page_limit is not None:
                limit = min(limit, self._page_limit)
            links = links[:limit]

        for link in links:
            try:
                target, size = fetcher.download(
                    link, lambda name, url=link: writer.attachment_target(section, url, name),
                    maximum_bytes,
                )
                manifest.record(link, target)
                outcome.attachments_written += 1
                LOGGER.info("Lampiran tersimpan (%d KB): %s", size // 1024, target.name)
            except UnsupportedAttachment as reason:
                LOGGER.info("Lampiran dilewati (%s): %s", reason, link)
            except Exception as error:
                outcome.failed += 1
                outcome.errors.append(f"{link}: {error}")
                LOGGER.warning("Gagal mengunduh %s: %s", link, error)

    def _smallest_first(
        self, links: list[str], fetcher: PoliteFetcher, maximum_bytes: int, outcome: CrawlOutcome
    ) -> list[str]:
        """Order ``links`` by declared size, smallest first, dropping those over the limit.

        A link whose server declares no size goes last rather than being
        dropped; its download still enforces the limit.
        """
        LOGGER.info("Mengukur %d lampiran sebelum diunduh...", len(links))
        sizes: dict[str, int | None] = {}
        for index, link in enumerate(links, start=1):
            sizes[link] = fetcher.size_of(link)
            if index % 50 == 0:
                LOGGER.info("Terukur %d/%d lampiran", index, len(links))
        kept = []
        for link in links:
            size = sizes[link]
            if size is not None and size > maximum_bytes:
                outcome.attachments_too_large += 1
                LOGGER.info("Lampiran dilewati (%d MB, di atas batas): %s", size // (1024 * 1024), link)
            else:
                kept.append(link)
        known = [sizes[link] for link in kept if sizes[link] is not None]
        LOGGER.info(
            "Akan diunduh %d lampiran, total terukur %.0f MB, terkecil lebih dulu",
            len(kept), sum(known) / (1024 * 1024),
        )
        return sorted(kept, key=lambda link: (sizes[link] is None, sizes[link] or 0))


#: Shared crawler bound to the process-wide settings and guard.
SITE_CRAWLER = SiteCrawler(SETTINGS, SOURCE_GUARD)
