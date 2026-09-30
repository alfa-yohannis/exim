"""Tests for the website crawler: URL rules, page parsing, and writing the corpus.

Nothing here touches the network. Pages are small HTML strings shaped like the
real sites, and a full crawl runs against a fake fetcher, so the suite covers
what would be fetched and what would be written while staying offline.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from app import crawler as crawler_module
from app.crawl_sites import BANK_INDONESIA, BEA_CUKAI, BPOM, DAGLU, E_BPOM, E_SKA, LPEI, PAJAK, PEN
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


ESKA_CIRCULAR = """
<html><head><title>.:E-Ska (Electronic Certificate of Origin Service):.</title></head><body>
  <section id="header-top"><h1>hidden</h1></section>
  <ul>
    <li class="cool-link active"><a href="/home.php">Beranda</a></li>
    <li class="cool-link"><a href="/home.php/home/faq">FAQ</a></li>
  </ul>
  <section id="news-section"><div class="row">
    <div class="col-md-8">
      <h3>Edaran Pemberlakuan e-SKA Form E Untuk Barang Ekspor Indonesia ke China</h3>
      <p>SKA Form E tujuan China dikirimkan secara elektronik.</p>
      <!-- <p><a href="<div style="border:1px solid #990000;">
      <h4>A PHP Error was encountered</h4>
      </div>">Tonton di Youtube</a></p> -->
      <a href="http://e-ska.kemendag.go.id/uploads/SE700.pdf">SE Direktur Fasilitasi Ekspor dan Impor</a>
    </div>
    <div class="col-md-4"><h3>Berita Terakhir</h3><h4>fsddfs</h4></div>
  </div></section>
</body></html>
"""


class TestPageTitles:
    """Titles for a site whose <title> is the same on every page."""

    URL = "https://e-ska.kemendag.go.id/home.php/home/news_detil/808"

    def test_takes_the_title_from_the_page_heading(self):
        parsed = PageParser(E_SKA).parse(ESKA_CIRCULAR, self.URL)
        assert parsed.title == "Edaran Pemberlakuan e-SKA Form E Untuk Barang Ekspor Indonesia ke China"

    def test_takes_the_active_menu_item_on_a_page_without_a_heading(self):
        html = ESKA_CIRCULAR.replace('"cool-link active"', '"cool-link"').replace(
            '<li class="cool-link"><a href="/home.php/home/faq">', '<li class="cool-link active"><a href="#">'
        )
        assert PageParser(E_SKA).parse(html, self.URL).title == "FAQ"

    def test_falls_back_to_the_cleaned_site_title(self):
        html = ESKA_CIRCULAR.replace("<h3>Edaran Pemberlakuan", "<p>Edaran Pemberlakuan")
        assert PageParser(E_SKA).parse(html, self.URL).title == "E-Ska (Electronic Certificate of Origin Service)"

    def test_drops_the_latest_news_column(self):
        text = PageParser(E_SKA).parse(ESKA_CIRCULAR, self.URL).text
        assert "Berita Terakhir" not in text and "fsddfs" not in text

    def test_commented_out_markup_never_becomes_text(self):
        text = PageParser(E_SKA).parse(ESKA_CIRCULAR, self.URL).text
        assert "PHP Error" not in text and "Tonton" not in text

    def test_attachment_links_move_to_the_canonical_origin(self):
        parsed = PageParser(E_SKA).parse(ESKA_CIRCULAR, self.URL)
        assert parsed.attachments == [
            ("SE Direktur Fasilitasi Ekspor dan Impor", "https://e-ska.kemendag.go.id/uploads/SE700.pdf")
        ]

    @pytest.mark.parametrize(
        ("href", "expected"),
        [
            ("../uploads/SE700.pdf", "https://e-ska.kemendag.go.id/uploads/SE700.pdf"),
            ("../cms.php/home/view_file_upload/SE.pdf",
             "https://e-ska.kemendag.go.id/cms.php/home/view_file_upload/SE.pdf"),
        ],
    )
    def test_relative_downloads_resolve_where_the_server_keeps_them(self, href, expected):
        assert SiteUrl(E_SKA).normalise(href, "https://e-ska.kemendag.go.id/home.php/home/x") == expected


class TestSitePlans:
    """Details of individual site plans."""

    def test_zero_width_spaces_never_split_a_word(self):
        html = "<html><head><title>PADG</title></head><body><p>Peraturan An​ggota Dewan</p></body></html>"
        url = "https://www.bi.go.id/id/publikasi/peraturan/Pages/PADG_162026.aspx"
        assert "Peraturan Anggota Dewan" in PageParser(BANK_INDONESIA).parse(html, url).text

    def test_lpei_drops_the_contact_form_every_product_repeats(self):
        html = (
            '<html><head><title>PMKE | Lembaga Pembiayaan Ekspor Indonesia (Eximbank)</title></head><body>'
            '<section class="product-wrapper"><div id="div-overview">Fasilitas Pembiayaan Modal Kerja '
            'Ekspor untuk pengadaan bahan baku dan bahan penolong bagi eksportir.</div>'
            '<div id="div-apply">Nama Lengkap Email Telepon Kirim Pesan</div></section></body></html>'
        )
        parsed = PageParser(LPEI).parse(html, "https://www.indonesiaeximbank.go.id/product-services/pmke")
        assert parsed.title == "PMKE"
        assert "Modal Kerja" in parsed.text and "Kirim Pesan" not in parsed.text


class TestKemendagDirectorates:
    """The directorate sites share a plan; robots.txt differs per site."""

    def test_daglu_never_downloads_what_its_robots_txt_forbids(self):
        assert not SiteUrl(DAGLU).is_wanted_attachment("https://ditjendaglu.kemendag.go.id/storage/a.pdf")

    def test_pen_downloads_documents(self):
        assert SiteUrl(PEN).is_wanted_attachment("https://ditjenpen.kemendag.go.id/storage/a.pdf")

    def test_faq_spans_its_pages(self):
        assert DAGLU.sections[0].fixed_urls[-1] == "https://ditjendaglu.kemendag.go.id/faq?page=6"


class TestBpom:
    """BPOM's legal database and its permit service, export and import only."""

    VIEW = "https://jdih.pom.go.id/view/slide/1746/6/2026/c92a10324374fac681719d63979d00fe"
    DOWNLOAD = "https://jdih.pom.go.id/download/rule/1746/6/2026/Peraturan%20Nomor%206%20Tahun%202026"

    def test_a_regulation_page_is_a_detail_page_and_its_preview_is_not(self):
        pattern = re.compile(BPOM.sections[0].detail_pattern)
        assert pattern.match(SiteUrl.path_of(self.VIEW))
        assert not pattern.match(SiteUrl.path_of(self.VIEW.replace("/view/", "/preview/")))

    def test_the_second_page_of_results_is_numbered_in_the_path(self):
        section = BPOM.sections[0]
        listing = section.listing_urls[0]
        assert listing.endswith("/all/all/pemasukan/all/all")
        assert SiteCrawler._listing_page_url(listing, section, 1, SiteUrl(BPOM)) == f"{listing}/2"

    def test_the_title_is_the_regulation_not_the_site(self):
        html = (
            "<html><head><title>JDIH Biro Hukum dan Organisasi - Badan POM RI</title></head><body>"
            '<section class="ftco-section contact-section"><div class="col-md-5">'
            '<h2>Detil Peraturan</h2><div><span>Judul :</span> <span>Peraturan Badan Pengawas Obat '
            "dan Makanan Nomor 6 Tahun 2026 tentang Tata Cara Penerbitan Surat Keterangan Ekspor "
            "Pangan Olahan</span></div><div><span>Status:</span> <span>Berlaku</span></div>"
            "</div></section></body></html>"
        )
        parsed = PageParser(BPOM).parse(html, self.VIEW)
        assert parsed.title.startswith("Peraturan Badan Pengawas Obat dan Makanan Nomor 6 Tahun 2026")
        assert "Status: Berlaku" in parsed.text

    def test_an_english_translation_is_out_of_scope(self):
        pattern = BPOM.sections[0].title_pattern
        assert not re.search(pattern, "Regulation of the Indonesian Food and Drug Authority Number 28 of 2023")
        assert re.search(pattern, "Peraturan Badan Pengawas Obat dan Makanan Nomor 28 Tahun 2023")

    def test_takes_the_regulation_but_not_drafting_papers_or_translations(self):
        urls = SiteUrl(BPOM)
        assert urls.is_wanted_attachment(self.DOWNLOAD)
        assert not urls.is_wanted_attachment("https://jdih.pom.go.id/download/detilprod/1746/0")
        assert not urls.is_wanted_attachment(
            "https://jdih.pom.go.id/download/rule/1611/28/2023/Regulation%20Of%20The%20Indonesia"
        )

    def test_e_bpom_links_resolve_against_the_application_root(self):
        link = SiteUrl(E_BPOM).normalise("etc/doc/faster.pdf", "https://e-bpom.pom.go.id/tpl/content/download.php")
        assert link == "https://e-bpom.pom.go.id/etc/doc/faster.pdf"

    def test_e_bpom_leaves_out_company_lists_and_outdated_files(self):
        urls = SiteUrl(E_BPOM)
        assert urls.is_wanted_attachment("https://e-bpom.pom.go.id/etc/doc/form_pengajuan_ski_otsk.pdf")
        assert not urls.is_wanted_attachment("https://e-bpom.pom.go.id/etc/doc/sk_prioritas8pangan.pdf")
        assert not urls.is_wanted_attachment("https://e-bpom.pom.go.id/etc/doc/pintar.pdf")

    def test_both_write_into_one_bpom_folder(self):
        assert (BPOM.country, BPOM.folder) == (E_BPOM.country, E_BPOM.folder) == ("Indonesia", "BPOM")


class _Response:
    """Just the headers of an HTTP response."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _StreamedResponse(_Response):
    """Headers and a body served in blocks, as a streamed download."""

    def __init__(self, headers: dict[str, str], blocks: list[bytes]) -> None:
        super().__init__(headers)
        self._blocks = blocks
        self.closed = False

    def iter_content(self, chunk_size: int):
        yield from self._blocks

    def close(self) -> None:
        self.closed = True


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

    def test_takes_the_name_from_the_query_and_keeps_the_regulation_number(self, fetcher):
        stream = "https://g7.cdnchinhphu.vn/api/download/stream?Url=x&file_name=2026_523_136%2F2026%2FTT-BTC.docx"
        name = fetcher._filename_of(_Response({"Content-Type": "application/octet-stream"}), stream)
        assert name == "2026_523_136-2026-TT-BTC.docx"

    def test_recognises_an_undeclared_pdf_by_its_first_bytes(self, fetcher):
        """JDIH BPOM sends its regulation PDFs with no Content-Type at all."""
        link = "https://jdih.pom.go.id/download/rule/1746/6/2026/Peraturan%20Nomor%206%20Tahun%202026"
        name = fetcher._filename_of(_Response({}), link, b"%PDF-1.7\n%\xe2\xe3")
        assert name == "Peraturan Nomor 6 Tahun 2026.pdf"

    def test_a_generic_type_is_decided_by_the_first_bytes_too(self, fetcher):
        response = _Response({"Content-Type": "application/octet-stream"})
        assert fetcher._filename_of(response, self.DOWNLOAD, b"%PDF-1.4") == "abc123.pdf"

    def test_an_undeclared_file_that_is_not_a_pdf_is_refused(self, fetcher):
        with pytest.raises(UnsupportedAttachment):
            fetcher._filename_of(_Response({}), self.DOWNLOAD, b"<!DOCTYPE html>")

    def test_a_declared_type_is_not_second_guessed(self, fetcher):
        with pytest.raises(UnsupportedAttachment):
            fetcher._filename_of(_Response({"Content-Type": "text/html"}), self.DOWNLOAD, b"%PDF-1.4")

    def test_downloads_an_undeclared_pdf_whole(self, fetcher, monkeypatch, tmp_path):
        """The block read to recognise the file is written, not lost."""
        response = _StreamedResponse({}, [b"%PDF-1.7 first", b" second", b" third"])
        monkeypatch.setattr(fetcher, "_request", lambda url, stream: response)
        target, size = fetcher.download(
            "https://jdih.pom.go.id/download/rule/1/6/2026/Peraturan", lambda name: tmp_path / name, 10_000
        )
        assert target == tmp_path / "Peraturan.pdf"
        assert target.read_bytes() == b"%PDF-1.7 first second third" and size == 27
        assert response.closed

    def test_a_server_name_with_slashes_keeps_its_number(self, fetcher):
        response = _Response({"Content-Disposition": 'attachment; filename="136/2026/TT-BTC.docx"'})
        assert fetcher._filename_of(response, self.DOWNLOAD) == "136-2026-TT-BTC.docx"

    def test_trusts_the_intermediates_servers_leave_out(self, fetcher):
        bundle = Path(fetcher._session.verify).read_text(encoding="utf-8")
        assert PoliteFetcher.EXTRA_CERTIFICATES.read_text(encoding="utf-8") in bundle
        assert bundle.count("BEGIN CERTIFICATE") > 100  # certifi's roots are kept


class TestQueryDownloads:
    """File servers that name the file in the query rather than the path."""

    SITE = CrawlSite(
        key="berkas",
        country="Negara",
        folder="Berkas",
        root="https://www.example.gov",
        hosts=frozenset({"www.example.gov"}),
        attachment_hosts=frozenset({"files.example.gov"}),
        attachment_path_patterns=(r"^/api/download/stream$",),
        attachment_exclude_patterns=(r"(?i)file_name=[^&]*\.pdf$",),
        sections=(),
    )
    STREAM = "https://files.example.gov/api/download/stream?Url=abc&file_name=1%2F2026%2FTT.{}"

    def test_a_download_keeps_its_query(self):
        url = self.STREAM.format("docx")
        assert SiteUrl(self.SITE).normalise(url, "https://www.example.gov/van-ban/1.htm") == url

    def test_exclusions_see_the_file_named_in_the_query(self):
        urls = SiteUrl(self.SITE)
        assert urls.is_wanted_attachment(self.STREAM.format("docx"))
        assert not urls.is_wanted_attachment(self.STREAM.format("pdf"))

    def test_takes_the_docx_and_else_the_pdf_of_each_document(self):
        site = replace(self.SITE, attachment_exclude_patterns=(), attachment_preference=(".docx", ".pdf"))
        stream = "https://files.example.gov/api/download/stream?Url={}&file_name={}"
        links = "".join(
            f'<a href="{stream.format(index, name)}">{name}</a>'
            for index, name in enumerate(["A.docx", "A.pdf", "B.doc", "B.pdf"])
        )
        html = f"<html><body><main><p>{'isi ' * 60}</p>{links}</main></body></html>"
        parsed = PageParser(site).parse(html, "https://www.example.gov/van-ban/1.htm")
        assert [SiteUrl.file_name_of(link) for _label, link in parsed.attachments] == ["A.docx", "B.pdf"]


class TestCorpusPageWriter:
    """Where snapshots land, and where they may never land."""

    def test_writes_under_the_country_site_and_section_folders(self, isolated_settings):
        writer = CorpusPageWriter(isolated_settings, BEA_CUKAI)
        page = ExternalPage(url="https://www.beacukai.go.id/kurs", label="beacukai.go.id · Tarif dan Kurs")
        parsed = PageParser(BEA_CUKAI).parse(BEACUKAI_PAGE, page.url)
        written = writer.write_page(CrawlSection(folder="Tarif dan Kurs"), page, parsed)
        corpus = Path(isolated_settings.source_directory).resolve()
        assert written.parent == corpus / "Indonesia" / "Bea Cukai" / "Tarif dan Kurs"
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
        country="Negara",
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
        files = list((Path(isolated_settings.source_directory) / "Negara" / "Contoh" / "Halaman").glob("*.txt"))
        assert outcome.pages_written == 1 and len(files) == 1

    def test_skips_duplicates_and_pages_without_text(self, outcome):
        assert outcome.pages_duplicate == 1
        assert outcome.pages_skipped == 1

    def test_optional_sections_wait_to_be_named(self, outcome, isolated_settings):
        assert not (Path(isolated_settings.source_directory) / "Negara" / "Contoh" / "Opsional").exists()

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
        country="Negara",
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
        written = [p.name for p in (Path(isolated_settings.source_directory) / "Negara" / "Regulasi" / "PMK").glob("*.txt")]
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


class TestNumberedPagesAndLinkText:
    """A gazette-style listing: pages numbered in the path, titles as the filter."""

    ROOT = "https://congbao.example.vn"
    LISTING = f"{ROOT}/van-ban-dang-cong-bao/bo-c10.htm"

    SITE = CrawlSite(
        key="gazette",
        country="Vietnam",
        folder="Cong Bao",
        root=ROOT,
        hosts=frozenset({"congbao.example.vn"}),
        content_selectors=("body",),
        sections=(
            CrawlSection(
                folder="Bo",
                listing_urls=(LISTING,),
                detail_pattern=r"^/van-ban/[a-z0-9-]+-\d+\.htm$",
                page_url_template="{stem}/trang-{number}.htm",
                link_text_pattern=r"hải quan",
            ),
        ),
    )

    @staticmethod
    def _listing(*documents: tuple[str, str]) -> str:
        anchors = "".join(f'<a href="/van-ban/{slug}.htm">{title}</a>' for slug, title in documents)
        return f"<html><body>{anchors}</body></html>"

    @pytest.fixture
    def fetcher(self, monkeypatch):
        _CountingFetcher.PAGES = {
            self.LISTING: self._listing(("thong-tu-1-101", "Thông tư 1 về thủ tục Hải quan"),
                                        ("thong-tu-2-102", "Thông tư 2 về ngân sách")),
            f"{self.ROOT}/van-ban-dang-cong-bao/bo-c10/trang-2.htm":
                self._listing(("thong-tu-3-103", "Thông tư 3 về ngân sách")),
            f"{self.ROOT}/van-ban-dang-cong-bao/bo-c10/trang-3.htm":
                self._listing(("thong-tu-4-104", "Thông tư 4 về kiểm tra hải quan")),
            f"{self.ROOT}/van-ban-dang-cong-bao/bo-c10/trang-4.htm": self._listing(),
            f"{self.ROOT}/van-ban/thong-tu-1-101.htm": _page("Thông tư 1", body="thủ tục hải quan " * 20),
            f"{self.ROOT}/van-ban/thong-tu-4-104.htm": _page("Thông tư 4", body="kiểm tra hải quan " * 20),
        }
        _CountingFetcher.SIZES = {}
        _CountingFetcher.requests = []
        monkeypatch.setattr(crawler_module, "PoliteFetcher", _CountingFetcher)
        return _CountingFetcher

    def test_walks_numbered_pages_past_one_with_nothing_in_scope(self, isolated_settings, guard, fetcher):
        SiteCrawler(isolated_settings, guard).run(self.SITE, full=True)
        fetched = [url for method, url in fetcher.requests if method == "GET"]
        assert f"{self.ROOT}/van-ban-dang-cong-bao/bo-c10/trang-4.htm" in fetched
        assert f"{self.ROOT}/van-ban/thong-tu-4-104.htm" in fetched

    def test_fetches_only_documents_whose_title_matches(self, isolated_settings, guard, fetcher):
        outcome = SiteCrawler(isolated_settings, guard).run(self.SITE, full=True)
        fetched = [url for method, url in fetcher.requests if method == "GET"]
        assert outcome.pages_written == 2
        assert not any("thong-tu-2" in url or "thong-tu-3" in url for url in fetched)
