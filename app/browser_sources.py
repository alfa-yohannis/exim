"""Reading external pages through a real browser.

Some sources cannot be fetched with a plain HTTP request:

* **PDDikti** answers a scripted request with ``403`` and a "Memverifikasi
  Browser Anda" interstitial, and its API returns ``401`` without a key.
* **SINTA** shows only the first page of a publication list to an anonymous
  visitor; the rest requires a logged-in session.
* Both render their content with JavaScript, so even a successful fetch would
  return an empty shell.

:class:`BrowserPageReader` drives a headless Chromium through Playwright, which
solves all three at once — it executes the page's scripts, keeps a cookie jar
across requests, and can sign in. It presents the same ``read(page)`` interface
as the plain HTTP reader, so :class:`app.web_sources.WebSnapshotRunner` can use
either without knowing the difference.

Credentials come from ``SINTA_USER`` / ``SINTA_PASS`` in ``.env`` and are used
only to sign in to SINTA. The browser runs headless and writes nothing to the
corpus; snapshots go through the same writer as every other source.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .web_sources import ExternalPage

LOGGER = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    """Raised when Playwright or its browser binary is not installed."""


@dataclass
class RenderedPage:
    """The readable result of visiting one URL in the browser."""

    title: str
    text: str
    #: How many paginated pages were combined into ``text``.
    pages_collected: int = 1


class BrowserPageReader:
    """Visits pages in a headless browser, optionally signed in to SINTA.

    The browser is started once and reused for every page in a run: launching
    Chromium costs a second or two, and a shared context keeps the SINTA session
    cookie alive so the login happens only once.

    Use it as a context manager so the browser is always closed, even when a
    page raises part-way through a run.
    """

    #: Tags whose contents are machinery rather than readable text.
    NON_CONTENT_TAGS = ("script", "style", "noscript", "svg", "form")

    #: SINTA renders its publication list into these views; each paginates
    #: separately, and together they make up an author's full record.
    SINTA_VIEWS = ("scopus", "garuda", "googlescholar", "rama")

    #: Upper bound on pagination, so one prolific author cannot stall a run.
    MAXIMUM_PAGES_PER_VIEW = 12

    def __init__(self, settings: Settings) -> None:
        """Record configuration; the browser starts when the context is entered."""
        self._settings = settings
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._chrome_process = None
        self._signed_in_to_sinta = False

    # --- Lifecycle ----------------------------------------------------------

    def __enter__(self) -> "BrowserPageReader":
        """Launch the browser and open a shared context."""
        self.start()
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        """Always close the browser, even if the run failed."""
        self.stop()

    def start(self) -> None:
        """Start a browser this reader can drive.

        Chrome is launched as an *ordinary process* and then attached to over
        the DevTools protocol, rather than being launched by Playwright. This
        matters: a Playwright-launched browser advertises automation, and
        PDDikti's Cloudflare check rejects it — every variant tried (bundled
        Chromium, real Chrome, Firefox, headless and headed alike) stalled on
        the interstitial. Attaching to a normally-started browser passes.

        Chrome also refuses remote debugging on its *default* profile
        directory, so the cookie-bearing files are copied to a scratch profile.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserUnavailable(
                "Playwright belum terpasang. Jalankan:\n"
                "  .venv\\Scripts\\python.exe -m pip install playwright\n"
                "  .venv\\Scripts\\python.exe -m playwright install chromium"
            )

        self._prepare_profile()
        self._launch_chrome()

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self._settings.browser_debug_port}"
            )
        except Exception as error:
            self.stop()
            raise BrowserUnavailable(f"Tidak dapat terhubung ke browser: {error}")

        self._context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else self._browser.new_context()
        )

        # A page of our own is opened and reused for every navigation. Chrome
        # exits when its last tab closes, so opening and closing a tab per URL
        # killed the browser part-way through a run. Adopting Chrome's initial
        # about:blank tab is no good either — Chrome replaces it moments after
        # start-up, which closed the session two seconds in.
        self._page = self._context.new_page()

    def _prepare_profile(self) -> None:
        """Copy the cookie-bearing files of the real profile to a scratch copy.

        Only identity files are copied — caches and history are irrelevant and
        would take gigabytes. Any Cloudflare clearance the operator's own
        browsing already earned comes along with the cookies.
        """
        import shutil

        source = Path(self._settings.browser_profile_source)
        target = Path(self._settings.browser_profile_working_copy)
        target.mkdir(parents=True, exist_ok=True)

        for relative in (
            Path("Local State"),
            Path("Default/Cookies"),
            Path("Default/Network/Cookies"),
            Path("Default/Preferences"),
            Path("Default/Secure Preferences"),
        ):
            source_file = source / relative
            if not source_file.exists():
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source_file, destination)
            except OSError as error:
                LOGGER.debug("Melewati %s: %s", relative, error)

    def _launch_chrome(self) -> None:
        """Start Chrome with remote debugging and wait for it to accept it."""
        import subprocess

        import requests

        executable = Path(self._settings.browser_executable)
        if not executable.exists():
            raise BrowserUnavailable(
                f"Chrome tidak ditemukan di {executable}. "
                f"Setel BROWSER_EXECUTABLE pada .env."
            )

        port = self._settings.browser_debug_port
        self._chrome_process = subprocess.Popen(
            [
                str(executable),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self._settings.browser_profile_working_copy}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(40):
            time.sleep(1)
            try:
                requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).raise_for_status()
                LOGGER.info("Browser siap pada porta %d", port)
                return
            except Exception:
                continue
        self.stop()
        raise BrowserUnavailable(
            f"Browser tidak membuka porta debug {port}. "
            f"Tutup Chrome yang sedang berjalan lalu ulangi."
        )

    def stop(self) -> None:
        """Detach from the browser, then close the process it was driving."""
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                LOGGER.debug("Gagal menutup koneksi browser", exc_info=True)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                LOGGER.debug("Gagal menghentikan Playwright", exc_info=True)
        if self._chrome_process is not None:
            try:
                self._chrome_process.terminate()
            except Exception:
                LOGGER.debug("Gagal menutup proses browser", exc_info=True)
        self._context = self._browser = self._playwright = None
        self._chrome_process = None
        self._signed_in_to_sinta = False

    # --- Authentication -----------------------------------------------------

    def sign_in_to_sinta(self) -> bool:
        """Sign in to SINTA if credentials are configured.

        Returns True when a session is established. Signing in is what makes
        pages beyond the first of a publication list visible; without it the
        snapshot silently captures only page one.
        """
        if self._signed_in_to_sinta:
            return True
        username = self._settings.sinta_username
        password = self._settings.sinta_password
        if not username or not password:
            LOGGER.info("Kredensial SINTA tidak diatur; hanya halaman pertama yang terbaca")
            return False

        page = self._ensure_page()
        try:
            page.goto(
                self._settings.sinta_login_url,
                wait_until="domcontentloaded",
                timeout=self._settings.browser_timeout_milliseconds,
            )
            page.fill('input[name="username"]', username)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)

            # Landing anywhere other than the login form means it was accepted.
            self._signed_in_to_sinta = "login" not in page.url.lower()
            LOGGER.info(
                "Login SINTA %s (URL: %s)",
                "berhasil" if self._signed_in_to_sinta else "GAGAL",
                page.url,
            )
            return self._signed_in_to_sinta
        except Exception as error:
            LOGGER.warning("Login SINTA gagal: %s", error)
            return False

    # --- Reading ------------------------------------------------------------

    def read(self, external_page: ExternalPage) -> tuple[str, str]:
        """Return ``(title, text)`` for a page, matching the HTTP reader's shape."""
        rendered = self.render(external_page.url, external_page.label)
        return rendered.title, rendered.text

    def render(self, url: str, label: str = "") -> RenderedPage:
        """Visit ``url`` and return its readable text.

        SINTA author profiles are expanded: every publication view is visited
        and paginated, because the single landing page shows only a summary.
        """
        if self._context is None:
            raise BrowserUnavailable("Browser belum dijalankan")

        if self._is_sinta_profile(url):
            return self._render_sinta_profile(url)
        return self._render_single(url)

    #: Navigations to retry: a single-page app redirecting on its own can
    #: interrupt the one just requested, which is transient, not a real failure.
    _INTERRUPTED_NAVIGATION = "interrupted by another navigation"

    def _ensure_page(self):
        """Return the working page, reopening it if the browser closed it.

        Chrome occasionally discards a tab — on start-up, or when a site drives
        a redirect through it. Recreating on demand keeps one lost tab from
        failing every remaining page in the run.
        """
        if self._context is None:
            raise BrowserUnavailable("Browser belum dijalankan")
        if self._page is None or self._page.is_closed():
            LOGGER.info("Membuka ulang tab kerja")
            self._page = self._context.new_page()
        return self._page

    def _open(self, url: str):
        """Navigate the shared page to ``url`` and wait for it to settle.

        Retried once: PDDikti's single-page app issues its own client-side
        navigation just after a detail page loads, which aborts whichever
        request happens to be in flight.
        """
        page = self._ensure_page()
        for attempt in range(2):
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._settings.browser_timeout_milliseconds,
                )
                self._settle(page)
                return page
            except Exception as error:
                if attempt == 0 and self._INTERRUPTED_NAVIGATION in str(error):
                    LOGGER.debug("Navigasi terputus, mencoba ulang: %s", url)
                    page.wait_for_timeout(2500)
                    continue
                raise
        return page

    def _render_single(self, url: str) -> RenderedPage:
        """Visit one URL and extract its text once the page has settled."""
        page = self._open(url)
        title = (page.title() or url).strip()
        return RenderedPage(title=title, text=self._extract_text(page))

    def _render_sinta_profile(self, profile_url: str) -> RenderedPage:
        """Collect a SINTA author's summary plus every paginated publication view."""
        base_url = profile_url.split("?")[0].rstrip("/")
        sections: list[str] = []
        title = ""
        pages_collected = 0

        summary = self._render_single(profile_url)
        title = summary.title
        sections.append(summary.text)
        pages_collected += 1

        for view in self.SINTA_VIEWS:
            view_text, view_pages = self._collect_paginated_view(f"{base_url}?view={view}")
            pages_collected += view_pages
            if view_text:
                sections.append(f"\n===== Daftar publikasi — view: {view} =====\n{view_text}")

        return RenderedPage(
            title=title,
            text="\n".join(sections),
            pages_collected=pages_collected,
        )

    def _collect_paginated_view(self, view_url: str) -> tuple[str, int]:
        """Walk a SINTA view's pagination, returning its combined text.

        Pagination is followed by clicking the "Next" control rather than by
        constructing URLs, because the page numbers are rendered by script and
        the query parameters differ between views.
        """
        collected: list[str] = []
        visited = 0
        try:
            page = self._open(view_url)
            while visited < self.MAXIMUM_PAGES_PER_VIEW:
                collected.append(self._extract_text(page))
                visited += 1
                if not self._go_to_next_page(page):
                    break
        except Exception as error:
            LOGGER.warning("Paginasi %s berhenti: %s", view_url, error)
        return "\n".join(collected), visited

    #: Labels a "next page" control might carry, across the sites involved.
    NEXT_PAGE_LABELS = ("next", "berikutnya", "selanjutnya", "›", "»", ">")

    def _go_to_next_page(self, page) -> bool:
        """Advance to the next results page, returning False at the last one.

        Written generically because the sites differ: SINTA renders Bootstrap
        ``li.page-item`` controls, while PDDikti uses its own buttons. Rather
        than encode each layout, this looks for anything that behaves like a
        next control and is not marked disabled.
        """
        for finder in (self._find_bootstrap_next, self._find_generic_next):
            try:
                control = finder(page)
            except Exception as error:
                LOGGER.debug("Pencarian kontrol halaman gagal: %s", error)
                continue
            if control is None:
                continue
            try:
                # Bounded: the default 30-second wait turns one unclickable
                # control into half a minute of dead time on every listing.
                control.click(timeout=self.NEXT_PAGE_CLICK_TIMEOUT_MS)
                page.wait_for_timeout(1200)
                self._settle(page)
                return True
            except Exception as error:
                LOGGER.debug("Tidak dapat mengklik kontrol halaman: %s", error)
        return False

    #: A click that has not taken effect within this long is not going to.
    NEXT_PAGE_CLICK_TIMEOUT_MS = 5000

    def _find_bootstrap_next(self, page):
        """Find a Bootstrap-style pagination "next" link, as SINTA renders."""
        for item in page.query_selector_all("li.page-item"):
            class_name = (item.get_attribute("class") or "").lower()
            if "disabled" in class_name or "active" in class_name:
                continue
            link = item.query_selector("a.page-link")
            if not link or not self._is_enabled(link):
                continue
            if (link.inner_text() or "").strip().lower() in self.NEXT_PAGE_LABELS:
                return link
        return None

    def _find_generic_next(self, page):
        """Find any enabled control that reads as "next", as PDDikti renders."""
        for element in page.query_selector_all("a, button"):
            if not self._is_enabled(element):
                continue
            label = (element.inner_text() or "").strip().lower()
            aria = (element.get_attribute("aria-label") or "").strip().lower()
            relationship = (element.get_attribute("rel") or "").strip().lower()
            if relationship == "next":
                return element
            if label in self.NEXT_PAGE_LABELS or aria in self.NEXT_PAGE_LABELS:
                return element
            if aria.startswith("next") or aria.startswith("berikutnya"):
                return element
        return None

    def _is_enabled(self, element) -> bool:
        """False when a control cannot actually be clicked.

        Visibility is checked as well as the disabled markers. Sites carry
        hidden "next" controls — carousel arrows kept in the DOM but styled
        away — and clicking one costs a full 30-second Playwright timeout while
        it waits for an element that will never become visible.
        """
        try:
            if not element.is_visible():
                return False
        except Exception:
            return False
        if element.get_attribute("disabled") is not None:
            return False
        if (element.get_attribute("aria-disabled") or "").lower() == "true":
            return False
        class_name = (element.get_attribute("class") or "").lower()
        return "disabled" not in class_name

    #: Wording of the Cloudflare interstitial that guards PDDikti.
    BOT_CHECK_MARKERS = ("Memverifikasi browser", "Verifikasi Memverifikasi")

    def _settle(self, page) -> None:
        """Wait for scripted content, and for any bot check to hand over.

        Polling for the interstitial to disappear is more reliable than a fixed
        delay: the check can hand over in two seconds or take twenty, and while
        it is redirecting the page cannot be read at all.
        """
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            # A page that never goes idle (polling, analytics) is still usable.
            LOGGER.debug("networkidle tidak tercapai; melanjutkan")
        page.wait_for_timeout(self._settings.browser_settle_milliseconds)

        deadline = time.time() + self._settings.browser_bot_check_seconds
        while time.time() < deadline:
            html = self._read_html(page)
            if html is None:
                # Mid-navigation: the check is handing over, which is progress.
                page.wait_for_timeout(1500)
                continue
            if not any(marker in html for marker in self.BOT_CHECK_MARKERS):
                return
            page.wait_for_timeout(2000)
        LOGGER.warning("Pemeriksaan bot belum selesai untuk %s", page.url)

    def _read_html(self, page) -> str | None:
        """Return the page HTML, or None while it is navigating.

        Playwright raises rather than returning partial content during a
        navigation, and the bot-check redirect makes that a normal occurrence
        rather than an error worth propagating.
        """
        try:
            return page.content()
        except Exception:
            return None

    def _extract_text(self, page) -> str:
        """Strip machinery from the rendered DOM and return readable lines."""
        from bs4 import BeautifulSoup

        html = self._read_html(page)
        if html is None:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        for tag_name in self.NON_CONTENT_TAGS:
            for element in soup.find_all(tag_name):
                element.decompose()
        return "\n".join(
            line.strip()
            for line in soup.get_text(separator="\n").splitlines()
            if line.strip()
        )

    def _is_sinta_profile(self, url: str) -> bool:
        """True for a SINTA author profile, which is worth expanding."""
        return bool(re.search(r"sinta\.[^/]+/authors?/profile/\d+", url, re.IGNORECASE))

    # --- Link discovery -----------------------------------------------------

    def collect_links(self, url: str, pattern: str, limit: int) -> list[str]:
        """Return up to ``limit`` links matching ``pattern``, across all pages.

        Used to reach the detail pages behind a search listing. The listing is
        paginated — the PDDikti results for one university run to several pages
        — so this walks the pagination rather than reading only page one.
        """
        if self._context is None:
            raise BrowserUnavailable("Browser belum dijalankan")

        compiled = re.compile(pattern, re.IGNORECASE)
        found: list[str] = []
        try:
            page = self._open(url)
            for page_number in range(1, self._settings.browser_maximum_pages + 1):
                added = self._collect_matching_links(page, compiled, found, limit)
                LOGGER.info(
                    "Halaman %d: %d tautan baru (total %d)", page_number, added, len(found)
                )
                if len(found) >= limit:
                    break
                if not self._go_to_next_page(page):
                    break
        except Exception as error:
            LOGGER.warning("Gagal mengumpulkan tautan dari %s: %s", url, error)
        return found[:limit]

    def _collect_matching_links(self, page, compiled, found: list[str], limit: int) -> int:
        """Add this page's matching links to ``found``; return how many were new."""
        added = 0
        for anchor in page.query_selector_all("a[href]"):
            if len(found) >= limit:
                break
            href = anchor.get_attribute("href") or ""
            absolute = self._absolutise(href, page.url)
            if compiled.search(absolute) and absolute not in found:
                found.append(absolute)
                added += 1
        return added

    def _absolutise(self, href: str, current_url: str) -> str:
        """Resolve a possibly relative href against the page it was found on."""
        from urllib.parse import urljoin

        return urljoin(current_url, href)
