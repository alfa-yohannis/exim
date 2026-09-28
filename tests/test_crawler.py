"""Tests for the website crawler: URL rules, page parsing, and writing the corpus.

Nothing here touches the network. Pages are small HTML strings shaped like the
real sites, and a full crawl runs against a fake fetcher, so the suite covers
what would be fetched and what would be written while staying offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import crawler as crawler_module
from app.crawl_sites import BEA_CUKAI, PAJAK
from app.crawler import (
    CorpusPageWriter,
    CrawlSection,
    CrawlSite,
    PageParser,
    PoliteFetcher,
    SiteCrawler,
    SiteUrl,
    UnsupportedAttachment,
)
from app.web_sources import ExternalPage

BEACUKAI_PAGE = """
<html><head><title>Impor Untuk Dipakai</title></head><body>
  <nav><a href="faq-ekspor">FAQ Ekspor</a><a href="peta-situs">Peta Situs</a></nav>
  <header><div class="page-header min-vh-75"><h1>Impor Untuk Dipakai</h1></div></header>
  <section class="py-4 mt-5">
    <p>Impor untuk Dipakai adalah Impor dengan tujuan untuk dipakai
       atau untuk dimiliki/dikuasai oleh Orang yang berdomisili di Indonesia.</p>
    <table><tr><td>PIB</td><td>Pemberitahuan Impor Barang</td></tr></table>
    <a href="faq-impor-untuk-dipakai">Selengkapnya</a>
    <a href="https://oneweb-file.beacukai.go.id/cdn/download/file/65000edecd0d5bae04e2e194">Unduh PMK</a>
  </section>
  <section id="chatInfo" class="d-none"><h5>Tasya AI</h5></section>
  <section class="py-5 pt-5">FAQ dan Info Selengkapnya Silakan akses: Klik Di Sini</section>
  <section class="p-2">Aksesibilitas</section>
  <footer>Hak Cipta Bea Cukai</footer>
</body></html>
"""


class TestSiteUrl:
    """Which links a crawl may follow, and in what canonical form."""

    def test_resolves_a_relative_slug_against_the_page(self):
        url = SiteUrl(BEA_CUKAI).normalise("faq-impor", "https://www.beacukai.go.id/faq")
        assert url == "https://www.beacukai.go.id/faq-impor"

    def test_bare_hostname_becomes_the_canonical_root(self):
        url = SiteUrl(BEA_CUKAI).normalise("https://beacukai.go.id/kurs", "https://www.beacukai.go.id/")
        assert url == "https://www.beacukai.go.id/kurs"

    @pytest.mark.parametrize(
        "href",
        ["https://jdih.kemenkeu.go.id/home", "mailto:info@beacukai.go.id", "#top", "javascript:void(0)"],
    )
    def test_rejects_links_that_leave_the_site(self, href):
        assert SiteUrl(BEA_CUKAI).normalise(href, "https://www.beacukai.go.id/") is None

    def test_file_cdn_contributes_downloads_only(self):
        urls = SiteUrl(BEA_CUKAI)
        download = "https://oneweb-file.beacukai.go.id/cdn/download/file/65000edecd0d5bae04e2e194"
        assert urls.normalise(download, "https://www.beacukai.go.id/") == download
        assert urls.normalise("https://oneweb-file.beacukai.go.id/", "https://www.beacukai.go.id/") is None

    def test_recognises_attachments_by_extension_or_download_path(self):
        urls = SiteUrl(BEA_CUKAI)
        assert urls.is_attachment("https://www.beacukai.go.id/files/pmk-190.pdf")
        assert urls.is_attachment("https://oneweb-file.beacukai.go.id/cdn/download/file/abc123")
        assert not urls.is_attachment("https://www.beacukai.go.id/impor-untuk-dipakai")

    def test_path_rewrites_collapse_aliases(self):
        url = SiteUrl(PAJAK).normalise("/index.php/id/faq-page", "https://www.pajak.go.id/")
        assert url == "https://www.pajak.go.id/id/faq-page"

    def test_never_requests_paths_robots_disallows(self):
        assert SiteUrl(PAJAK).normalise("/user/login", "https://www.pajak.go.id/") is None


class TestPageParser:
    """Reducing a page to the text of its content block."""

    @pytest.fixture
    def parsed(self):
        return PageParser(BEA_CUKAI).parse(BEACUKAI_PAGE, "https://www.beacukai.go.id/impor-untuk-dipakai")

    def test_keeps_the_content(self, parsed):
        assert parsed.title == "Impor Untuk Dipakai"
        assert "berdomisili di Indonesia" in parsed.text

    @pytest.mark.parametrize(
        "furniture", ["Peta Situs", "Tasya AI", "FAQ dan Info Selengkapnya", "Aksesibilitas", "Hak Cipta"]
    )
    def test_drops_menus_widgets_and_boilerplate(self, parsed, furniture):
        assert furniture not in parsed.text

    def test_flattens_line_breaks_inside_a_sentence(self, parsed):
        assert "untuk dipakai atau untuk dimiliki" in parsed.text

    def test_keeps_a_table_row_on_one_line(self, parsed):
        assert "PIB | Pemberitahuan Impor Barang |" in parsed.text

    def test_reads_links_from_the_content_not_the_menu(self, parsed):
        assert "https://www.beacukai.go.id/faq-impor-untuk-dipakai" in parsed.links
        assert "https://www.beacukai.go.id/faq-ekspor" not in parsed.links

    def test_collects_download_links_as_attachments(self, parsed):
        assert parsed.attachments == [
            ("Unduh PMK", "https://oneweb-file.beacukai.go.id/cdn/download/file/65000edecd0d5bae04e2e194")
        ]


class _Response:
    """Just the headers of an HTTP response."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class TestAttachmentFilename:
    """Naming a download whose URL carries no extension."""

    DOWNLOAD = "https://oneweb-file.beacukai.go.id/cdn/download/file/abc123"

    @pytest.fixture
    def fetcher(self, isolated_settings):
        return PoliteFetcher(isolated_settings)

    def test_uses_the_name_the_server_gives(self, fetcher):
        response = _Response({"Content-Disposition": 'attachment; filename="PMK 190 2022.pdf"'})
        assert fetcher._filename_of(response, self.DOWNLOAD) == "PMK 190 2022.pdf"

    def test_derives_the_extension_from_the_content_type(self, fetcher):
        response = _Response({"Content-Type": "application/pdf"})
        assert fetcher._filename_of(response, self.DOWNLOAD) == "abc123.pdf"

    def test_refuses_what_the_corpus_cannot_read(self, fetcher):
        with pytest.raises(UnsupportedAttachment):
            fetcher._filename_of(_Response({"Content-Type": "image/png"}), self.DOWNLOAD)


class TestCorpusPageWriter:
    """Where snapshots land, and where they may never land."""

    def test_writes_under_the_site_and_section_folders(self, isolated_settings):
        writer = CorpusPageWriter(isolated_settings, BEA_CUKAI)
        page = ExternalPage(url="https://www.beacukai.go.id/kurs", label="beacukai.go.id · Tarif dan Kurs")
        parsed = PageParser(BEA_CUKAI).parse(BEACUKAI_PAGE, page.url)
        written = writer.write_page(CrawlSection(folder="Tarif dan Kurs"), page, parsed)
        corpus = Path(isolated_settings.source_directory).resolve()
        assert written.parent == corpus / "Bea Cukai" / "Tarif dan Kurs"
        assert "URL: https://www.beacukai.go.id/kurs" in written.read_text(encoding="utf-8")

    def test_refuses_a_target_outside_the_corpus(self, isolated_settings, tmp_path):
        writer = CorpusPageWriter(isolated_settings, BEA_CUKAI)
        with pytest.raises(RuntimeError):
            writer._inside_corpus(tmp_path / "elsewhere.txt")


class _FakeFetcher:
    """Serves canned pages in place of the network."""

    PAGES = {
        "https://www.example.go.id/a": "<html><head><title>A</title></head><body><p>{}</p></body></html>".format("isi A " * 40),
        "https://www.example.go.id/b": "<html><head><title>B</title></head><body><p>{}</p></body></html>".format("isi A " * 40),
        "https://www.example.go.id/c": "<html><head><title>C</title></head><body><p>terlalu pendek</p></body></html>",
    }

    def __init__(self, _settings) -> None:
        pass

    def get_text(self, url: str) -> str:
        return self.PAGES[url]


class TestSiteCrawler:
    """One crawl from plan to corpus files."""

    SITE = CrawlSite(
        key="contoh",
        folder="Contoh",
        root="https://www.example.go.id",
        hosts=frozenset({"www.example.go.id"}),
        content_selectors=("body",),
        sections=(
            CrawlSection(folder="Halaman", fixed_urls=tuple(_FakeFetcher.PAGES)),
            CrawlSection(folder="Opsional", fixed_urls=("https://www.example.go.id/a",), optional=True),
        ),
    )

    @pytest.fixture
    def outcome(self, isolated_settings, guard, monkeypatch):
        monkeypatch.setattr(crawler_module, "PoliteFetcher", _FakeFetcher)
        return SiteCrawler(isolated_settings, guard).run(self.SITE)

    def test_writes_each_distinct_page_once(self, outcome, isolated_settings):
        files = list((Path(isolated_settings.source_directory) / "Contoh" / "Halaman").glob("*.txt"))
        assert outcome.pages_written == 1 and len(files) == 1

    def test_skips_duplicates_and_pages_without_text(self, outcome):
        assert outcome.pages_duplicate == 1
        assert outcome.pages_skipped == 1

    def test_optional_sections_wait_to_be_named(self, outcome, isolated_settings):
        assert not (Path(isolated_settings.source_directory) / "Contoh" / "Opsional").exists()

    def test_keeps_its_manifest_outside_the_corpus(self, outcome, isolated_settings):
        assert (Path(isolated_settings.data_directory) / "crawl-contoh.json").exists()

    def test_a_second_crawl_writes_nothing_again(self, outcome, isolated_settings, guard):
        # The duplicate address counts as saved too; only the page without
        # text is fetched again, and skipped again.
        again = SiteCrawler(isolated_settings, guard).run(self.SITE)
        assert again.pages_unchanged == 2 and again.pages_written == 0

    def test_rejects_an_unknown_section(self, isolated_settings, guard):
        with pytest.raises(ValueError):
            SiteCrawler(isolated_settings, guard).run(self.SITE, section_names=["Tidak Ada"])


def _page(title: str, body: str = "isi peraturan " * 30, links: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body><p>{body}</p>{links}</body></html>"


class _CountingFetcher:
    """Serves canned pages and files, and records every request made."""

    PAGES: dict[str, str] = {}
    SIZES: dict[str, int | None] = {}
    requests: list[tuple[str, str]] = []

    def __init__(self, _settings) -> None:
        pass

    def get_text(self, url: str) -> str:
        self.requests.append(("GET", url))
        return self.PAGES[url]

    def size_of(self, url: str) -> int | None:
        self.requests.append(("HEAD", url))
        return self.SIZES.get(url)

    def download(self, url, target_for, maximum_bytes):
        self.requests.append(("DOWNLOAD", url))
        target = target_for(Path(url).name)
        target.write_bytes(b"%PDF-1.4")
        return target, 8


class TestScopeAndOrdering:
    """Sitemap listings, title filters, excluded files, and smallest-first downloads."""

    ROOT = "https://www.example.go.id"
    SITEMAP = f"""<?xml version="1.0"?><urlset>
        <url><loc>{ROOT}/dok/pmk-1</loc></url>
        <url><loc>{ROOT}/dok/pmk-2</loc></url>
        <url><loc>https://elsewhere.example.com/dok/pmk-3</loc></url>
        <url><loc>{ROOT}/tentang</loc></url></urlset>"""

    SITE = CrawlSite(
        key="regulasi",
        folder="Regulasi",
        root=ROOT,
        hosts=frozenset({"www.example.go.id"}),
        content_selectors=("body",),
        attachment_exclude_patterns=(r"/abstrak/",),
        sections=(
            CrawlSection(
                folder="PMK",
                listing_urls=(f"{ROOT}/sitemap.xml",),
                detail_pattern=r"^/dok/pmk-\d+$",
                paginate=False,
                title_pattern=r"bea masuk",
                smallest_attachments_first=True,
            ),
        ),
    )

    @pytest.fixture
    def fetcher(self, monkeypatch):
        big, small = f"{self.ROOT}/files/besar.pdf", f"{self.ROOT}/files/kecil.pdf"
        _CountingFetcher.PAGES = {
            f"{self.ROOT}/sitemap.xml": self.SITEMAP,
            f"{self.ROOT}/dok/pmk-1": _page(
                "PMK 1 - Tarif Bea Masuk",
                links=f'<a href="{big}">Fulltext</a><a href="{small}">Lampiran</a>'
                      f'<a href="{self.ROOT}/abstrak/1.pdf">Abstrak</a>'
                      f'<a href="{self.ROOT}/files/terlalu-besar.pdf">Tabel</a>'
                      f'<a href="{self.ROOT}/files/tanpa-ukuran.pdf">Lain</a>',
            ),
            f"{self.ROOT}/dok/pmk-2": _page("PMK 2 - Standar Biaya Masukan"),
        }
        _CountingFetcher.SIZES = {
            big: 5_000_000, small: 40_000, f"{self.ROOT}/files/terlalu-besar.pdf": 900 * 1024 * 1024,
        }
        _CountingFetcher.requests = []
        monkeypatch.setattr(crawler_module, "PoliteFetcher", _CountingFetcher)
        return _CountingFetcher

    def test_sitemap_entries_become_detail_pages(self, isolated_settings, guard, fetcher):
        SiteCrawler(isolated_settings, guard).run(self.SITE, full=True)
        fetched = [url for method, url in fetcher.requests if method == "GET"]
        assert f"{self.ROOT}/dok/pmk-1" in fetched and f"{self.ROOT}/dok/pmk-2" in fetched
        assert not any("elsewhere" in url or url.endswith("/tentang") for url in fetched)

    def test_a_title_outside_the_scope_is_not_written(self, isolated_settings, guard, fetcher):
        outcome = SiteCrawler(isolated_settings, guard).run(self.SITE, full=True)
        written = [p.name for p in (Path(isolated_settings.source_directory) / "Regulasi" / "PMK").glob("*.txt")]
        assert outcome.pages_out_of_scope == 1
        assert len(written) == 1 and written[0].startswith("PMK 1")

    def test_an_out_of_scope_page_is_not_fetched_again(self, isolated_settings, guard, fetcher):
        SiteCrawler(isolated_settings, guard).run(self.SITE, full=True)
        fetcher.requests.clear()
        again = SiteCrawler(isolated_settings, guard).run(self.SITE, full=True)
        assert ("GET", f"{self.ROOT}/dok/pmk-2") not in fetcher.requests
        assert again.pages_out_of_scope == 1

    def test_downloads_smallest_first_and_unknown_sizes_last(self, isolated_settings, guard, fetcher):
        SiteCrawler(isolated_settings, guard).run(self.SITE, full=True)
        downloads = [Path(url).name for method, url in fetcher.requests if method == "DOWNLOAD"]
        assert downloads == ["kecil.pdf", "besar.pdf", "tanpa-ukuran.pdf"]

    def test_never_downloads_a_file_over_the_limit_or_an_excluded_one(self, isolated_settings, guard, fetcher):
        outcome = SiteCrawler(isolated_settings, guard).run(self.SITE, full=True)
        downloaded = [url for method, url in fetcher.requests if method == "DOWNLOAD"]
        assert outcome.attachments_too_large == 1
        assert not any("terlalu-besar" in url or "/abstrak/" in url for url in downloaded)
