"""The websites the corpus is built from, and how to crawl each of them.

Sites are grouped by country and, within a country, listed in the order of the
README source tables: by how much of a typical export-import question they
answer. Each :class:`CrawlSite` fills ``corpus-exim/<country>/<site>/``, so the
interface's folder filter reads as a choice of country.
"""

from __future__ import annotations

from .crawler import CrawlSection, CrawlSite

# --- Bea Cukai -----------------------------------------------------------------

_BEACUKAI_ROOT = "https://www.beacukai.go.id"


def _beacukai_pages(*slugs: str) -> tuple[str, ...]:
    """Return absolute URLs for pages of the flat beacukai.go.id address space."""
    return tuple(f"{_BEACUKAI_ROOT}/{slug}" for slug in slugs)


#: beacukai.go.id is a flat set of pages (``/impor-untuk-dipakai``,
#: ``/faq-ekspor``) listed on ``/peta-situs``. Its robots.txt is empty and
#: sitemap.xml returns "null", so the plan names the pages directly; only the
#: FAQ indexes are followed as listings. Pages whose content JavaScript fills
#: in (``/undang-undang-pabean``) are skipped by the minimum-text rule, and
#: excise (cukai) is optional because it concerns domestic goods.
BEA_CUKAI = CrawlSite(
    key="beacukai",
    country="Indonesia",
    folder="Bea Cukai",
    root=_BEACUKAI_ROOT,
    hosts=frozenset({"www.beacukai.go.id", "beacukai.go.id"}),
    content_selectors=("body",),
    # The menu repeats every page of the site; the last two blocks are a
    # "more FAQ" banner and an accessibility widget. On the FAQ index, the
    # short hero holds the site search and #chatInfo the chatbot's help text.
    furniture_selectors=("nav", "footer", "section.p-2", ".page-header.min-vh-35", "#chatInfo"),
    boilerplate_prefixes=("FAQ dan Info Selengkapnya",),
    attachment_hosts=frozenset({"oneweb-file.beacukai.go.id", "onewebfile.beacukai.go.id"}),
    attachment_path_patterns=(r"^/cdn/download/file/[0-9a-f]+$",),
    sections=(
        CrawlSection(
            folder="Impor",
            fixed_urls=_beacukai_pages(
                "impor-untuk-dipakai", "impor-sementara", "impor-barang-pmi",
                "larangan-pembatasan", "pemeriksaan-pabean", "barangkiriman", "barang-kiriman",
                "barang-penumpang", "barang-pindahan", "registrasi-kepabeanan", "imei",
            ),
            sample_attachment_limit=40,
        ),
        CrawlSection(
            folder="Ekspor",
            fixed_urls=_beacukai_pages(
                "tata-laksana-ekspor", "klinik-ekspor", "klinik-ekspor-agen-fasilitas",
                "klinik-ekspor-umkm",
            ),
            sample_attachment_limit=40,
        ),
        CrawlSection(
            folder="Fasilitas",
            fixed_urls=_beacukai_pages(
                "fasilitas-tempat-penimbunan-berikat", "fasilitas-tpb-kb", "fasilitas-tpb-gb",
                "fasilitas-tpb-plb", "fasilitas-tpb-tbb", "fasilitas-tpb-tppb",
                "fasilitas-kite", "fasilitas-aeo", "fasilitas-mita",
                "fasilitas-pembebasan-bea-masuk", "fasilitas-pembebasan-bea-masuk-industri",
                "fasilitas-pembebasan-bea-masuk-lainnya", "pembebasan-bea-masuk-penelitian-pengembangan",
                "fasilitas-pertambangan", "free-trade-zone", "kawasan-ekonomi-khusus", "fta",
            ),
            sample_attachment_limit=40,
        ),
        CrawlSection(
            folder="Tarif dan Kurs",
            fixed_urls=_beacukai_pages("btki-dan-tarif", "kurs", "asean-tariff-finder", "apec-tr"),
            sample_attachment_limit=40,
        ),
        CrawlSection(
            folder="Keberatan dan Banding",
            fixed_urls=_beacukai_pages(
                "keberatan-banding", "keberatan-penetapan-nilai-pabean", "banding-keputusan-keberatan",
            ),
            sample_attachment_limit=40,
        ),
        CrawlSection(
            folder="FAQ",
            fixed_urls=_beacukai_pages("faq"),
            # Each index links its topics as "/faq-<topic>" pages.
            listing_urls=_beacukai_pages(
                "faq", "faq-impor", "faq-ekspor", "faq-fasilitas", "faq-keberatan-banding",
            ),
            detail_pattern=r"^/faq-(?!cukai)[a-z0-9.-]+$",
            paginate=False,
            sample_limit_per_listing=100,
            sample_attachment_limit=40,
        ),
        CrawlSection(
            folder="Layanan",
            fixed_urls=_beacukai_pages(
                "ceisa40", "ceisa-care", "konsultasi-peraturan", "standar-pelayanan",
                "maklumat-pelayanan", "daftar-kantor-bea-cukai", "pengaduan", "mobile-beacukai",
                "vessel-declaration-system", "ecd", "waspada-penipuan", "bravo-beacukai",
            ),
            sample_attachment_limit=20,
        ),
        CrawlSection(
            folder="Profil",
            fixed_urls=_beacukai_pages(
                "sekilas-bea-cukai", "tugas-dan-fungsi-bea-cukai", "visi-misi-dan-strategi-bea-cukai",
                "profil-organisasi", "struktur-organisasi", "sejarah-bea-cukai",
            ),
            sample_attachment_limit=10,
        ),
        CrawlSection(
            folder="Cukai",
            fixed_urls=_beacukai_pages(
                "sekilas-cukai", "tarif-cukai-ht", "tarif-cukai-ea-mmea-kmea", "pita-cukai",
                "pengajuan-nppbkc", "fasilitas-pembebasan-cukai", "faq-cukai",
            ),
            listing_urls=_beacukai_pages("faq-cukai"),
            detail_pattern=r"^/faq-cukai-[a-z0-9.-]+$",
            paginate=False,
            sample_limit_per_listing=100,
            optional=True,
        ),
    ),
)

# --- JDIH Kemenkeu ---------------------------------------------------------------

_JDIH_KEMENKEU_ROOT = "https://jdih.kemenkeu.go.id"
_JDIH_KEMENKEU_SITEMAP = f"{_JDIH_KEMENKEU_ROOT}/sitemap.xml"

#: Title words marking a Ministry of Finance regulation as customs or trade
#: law. The unit code in "190/PMK.04/2022" says who issued a regulation until
#: 2022: .04 is Bea Cukai, all of it wanted; .010 and .011 are the fiscal
#: policy agency, whose import tariffs sit beside tax and budget rules. Since
#: 2023 regulations are numbered "PMK 96 Tahun 2023", with no unit at all, so
#: those and BKF's are kept by their title.
_CUSTOMS_TITLE = (
    r"kepabeanan|pabean|bea masuk|bea keluar|impor|ekspor|barang kiriman|"
    r"penimbunan berikat|kawasan berikat|perdagangan bebas|pelabuhan bebas|"
    r"kawasan ekonomi khusus|preferensi|anti ?dumping|tindakan pengamanan|"
    r"keasalan|surat keterangan asal|manifes|klasifikasi barang"
)

#: jdih.kemenkeu.go.id lists every regulation in its sitemap as
#: "/dok/190-pmk-04-2022" (until 2022) or "/dok/pmk-96-tahun-2023" (since).
#: A document page holds the metadata — status, dates, subject, and the
#: history of what it revokes or amends — and links the full text as a PDF,
#: beside an abstract that repeats it and is left out.
JDIH_KEMENKEU = CrawlSite(
    key="jdih-kemenkeu",
    country="Indonesia",
    folder="JDIH Kemenkeu",
    root=_JDIH_KEMENKEU_ROOT,
    hosts=frozenset({"jdih.kemenkeu.go.id"}),
    disallowed_prefixes=("/api/auth", "/api/health", "/pdfjs"),
    content_selectors=("body",),
    furniture_selectors=(
        "header.jdih-header", "footer.jdih-footer", ".copyright-area", ".jdih-breadcrumb",
        "ul.nav", '.accordion-item:-soup-contains("Kutip Dokumen Ini")',
    ),
    title_suffix=r"\s*\|\s*JDIH Kementerian Keuangan\s*$",
    # Old documents keep the abstract under /api/download/abtrak/; newer ones
    # name the file itself "ABSTRAK PMK 12.pdf".
    attachment_exclude_patterns=(r"^/api/download/abtrak/", r"(?i)abstrak"),
    sections=(
        CrawlSection(
            folder="PMK Bea Cukai",
            listing_urls=(_JDIH_KEMENKEU_SITEMAP,),
            detail_pattern=r"^/dok/\d+-pmk-04-\d{4}$",
            paginate=False,
            smallest_attachments_first=True,
        ),
        CrawlSection(
            folder="PMK Kebijakan Fiskal",
            listing_urls=(_JDIH_KEMENKEU_SITEMAP,),
            detail_pattern=r"^/dok/\d+-pmk-01[01]-\d{4}$",
            paginate=False,
            title_pattern=_CUSTOMS_TITLE,
            smallest_attachments_first=True,
        ),
        CrawlSection(
            folder="PMK Sejak 2023",
            listing_urls=(_JDIH_KEMENKEU_SITEMAP,),
            detail_pattern=r"^/dok/pmk-\d+-tahun-\d{4}$",
            paginate=False,
            title_pattern=_CUSTOMS_TITLE,
            smallest_attachments_first=True,
        ),
    ),
)

# --- Kemendag directorates ------------------------------------------------------


def _kemendag_directorate(
    key: str, folder: str, subdomain: str, faq_pages: int, robots_forbid_documents: bool
) -> CrawlSite:
    """Return the plan for one Ministry of Trade directorate site.

    The directorates share one CMS. Their publications and regulation lists
    are PDFs shown in a JavaScript flipbook, and their topic pages fill tabs
    with JavaScript, so the text a crawler can read is the FAQ, spread over
    ``?page=`` pages.
    """
    root = f"https://{subdomain}.kemendag.go.id"
    return CrawlSite(
        key=key,
        country="Indonesia",
        folder=folder,
        root=root,
        hosts=frozenset({f"{subdomain}.kemendag.go.id"}),
        content_selectors=("div.single-content", "main"),
        furniture_selectors=("ul.pagination",),
        title_suffix=r"\s*-\s*Ditjen \w+\s*$",
        # robots.txt: "Disallow: /*.pdf$" and the same for ZIP, PPTX, DOCX, WEBP.
        attachment_exclude_patterns=(
            (r"(?i)\.(pdf|zip|pptx|docx|webp)$",) if robots_forbid_documents else ()
        ),
        sections=(
            CrawlSection(
                folder="FAQ",
                fixed_urls=(f"{root}/faq",) + tuple(
                    f"{root}/faq?page={number}" for number in range(2, faq_pages + 1)
                ),
            ),
        ),
    )


#: Ditjen Perdagangan Luar Negeri: FAQ on export and import policy, licensing,
#: and origin rules. HTML only, as its robots.txt forbids the documents.
DAGLU = _kemendag_directorate(
    "daglu", "Daglu Kemendag", "ditjendaglu", faq_pages=6, robots_forbid_documents=True
)

#: Ditjen Pengembangan Ekspor Nasional: FAQ on how to start exporting,
#: market information, and export promotion programmes.
PEN = _kemendag_directorate(
    "pen", "PEN Kemendag", "ditjenpen", faq_pages=4, robots_forbid_documents=False
)

_ESKA_ROOT = "https://e-ska.kemendag.go.id"


def _eska_pages(*slugs: str) -> tuple[str, ...]:
    """Return absolute URLs of e-SKA's public pages."""
    return tuple(f"{_ESKA_ROOT}/home.php/home/{slug}" for slug in slugs)


#: e-SKA issues certificates of origin (SKA). Its public side explains them,
#: lists the preferential tariff schemes, and keeps a decade of circulars on
#: forms, rules of origin, and partner-country notices, many with a PDF.
#: Every page has the same <title>, so the title comes from the page: the
#: active menu item (skipping "Beranda", which marks the home and news pages),
#: then a circular's heading, then an information page's heading. robots.txt
#: is absent.
E_SKA = CrawlSite(
    key="e-ska",
    country="Indonesia",
    folder="e-SKA Kemendag",
    root=_ESKA_ROOT,
    hosts=frozenset({"e-ska.kemendag.go.id"}),
    # Relative links on a circular resolve below /home.php/, where the
    # server has no files; the same files are served from the root.
    path_rewrites=((r"^/home\.php/(?=uploads/|cms\.php/)", "/"),),
    # A circular sits beside a "latest news" column; the home page has no
    # columns, only the news block holding the manuals.
    content_selectors=("#news-section .col-md-8", "#solution", "#faq", "#news-section"),
    # The index lists every circular in a block of its own.
    link_region_selectors=("body",),
    title_selectors=(
        "li.cool-link.active:not(:first-child)", "#news-section .col-md-8 h3", "#solution h2",
    ),
    title_suffix=r"^\.:\s*|\s*:\.$",
    sections=(
        CrawlSection(
            folder="Informasi SKA",
            # The home page carries the user manuals and the SKA flow charts.
            fixed_urls=(f"{_ESKA_ROOT}/cms.php",)
            + _eska_pages("form", "rules", "tutorial", "preferensi", "faq"),
            sample_attachment_limit=20,
            smallest_attachments_first=True,
        ),
        CrawlSection(
            folder="Edaran dan Berita",
            listing_urls=_eska_pages("berita_index"),
            detail_pattern=r"^/home\.php/home/news_detil/\d+$",
            paginate=False,
            # System maintenance notices, holidays, and two test posts.
            title_pattern=r"^(?!.*(?:maintenance|downtime|libur|fsddfs|fdasafdafd))",
            sample_limit_per_listing=150,
            sample_attachment_limit=40,
            # Two regulations attached here exceed the size limit.
            smallest_attachments_first=True,
        ),
        CrawlSection(
            folder="Kegiatan",
            listing_urls=_eska_pages("kegiatan_index"),
            detail_pattern=r"^/home\.php/home/event_detil/\d+$",
            paginate=False,
            # Most events are the vendor's maintenance visits to issuing offices.
            title_pattern=r"^(?!.*maintenance)",
            sample_limit_per_listing=150,
        ),
    ),
)

# --- LPEI (Indonesia Eximbank) ---------------------------------------------------

_LPEI_ROOT = "https://www.indonesiaeximbank.go.id"

#: Indonesia Eximbank finances, guarantees, and insures exports. Each product
#: page explains one scheme (working capital, buyer's credit, customs bonds,
#: receivables insurance, new-exporter coaching); its banner is only a menu of
#: every product, and its "how to apply" tab is the same contact form
#: everywhere. The press releases and articles, listed in the sitemap, cover
#: export risks, programmes, and villages that export. robots.txt allows all.
LPEI = CrawlSite(
    key="lpei",
    country="Indonesia",
    folder="LPEI",
    root=_LPEI_ROOT,
    hosts=frozenset({"www.indonesiaeximbank.go.id", "indonesiaeximbank.go.id"}),
    content_selectors=("section.product-wrapper", "section#news-detail", "#accordionFaq", "main"),
    furniture_selectors=("#div-apply",),
    title_suffix=r"\s*\|\s*Lembaga Pembiayaan Ekspor Indonesia \(Eximbank\)\s*$",
    sections=(
        CrawlSection(
            folder="Produk dan Jasa",
            fixed_urls=(f"{_LPEI_ROOT}/faq",),
            listing_urls=(f"{_LPEI_ROOT}/product-services",),
            detail_pattern=r"^/product-services/[a-z0-9-]+$",
            paginate=False,
        ),
        CrawlSection(
            folder="Informasi Publik",
            listing_urls=(f"{_LPEI_ROOT}/sitemap.xml",),
            detail_pattern=r"^/public-information/[a-z0-9-]+$",
            paginate=False,
            sample_limit_per_listing=40,
        ),
    ),
)

# --- Bank Indonesia ---------------------------------------------------------------

_BI_ROOT = "https://www.bi.go.id"


def _bi_regulations(*names: str) -> tuple[str, ...]:
    """Return the pages of Bank Indonesia regulations, named as the site names them."""
    return tuple(f"{_BI_ROOT}/id/publikasi/peraturan/Pages/{name}.aspx" for name in names)


#: Bank Indonesia sets how exporters bring home and report export proceeds
#: (devisa hasil ekspor, DHE) and, for natural resources, how long they keep
#: them onshore. bi.go.id has no sitemap and its regulation index shows only
#: the newest few, so the plan names the DHE regulations and their amendments
#: (found through the site's own titles). Each page summarises its regulation
#: and links the full text and a Q&A document, both PDFs.
BANK_INDONESIA = CrawlSite(
    key="bi",
    country="Indonesia",
    folder="Bank Indonesia",
    root=_BI_ROOT,
    hosts=frozenset({"www.bi.go.id", "bi.go.id"}),
    disallowed_prefixes=(
        "/_layouts", "/Style Library", "/SiteCollectionImages", "/Banner", "/Menu Image",
        "/Lists", "/WorkflowTasks",
    ),
    content_selectors=("#DeltaPlaceHolderMain .content-text", "#DeltaPlaceHolderMain", "body"),
    # A "was this page useful?" survey, and a visit counter that changes daily.
    furniture_selectors=(".survey-wrapper", "#layout-hits"),
    sections=(
        CrawlSection(
            folder="Devisa Hasil Ekspor",
            fixed_urls=_bi_regulations(
                # PBI 7/2023 and its amendments (3/2025, 5/2026).
                "PBI_072023", "PBI_032025", "PBI_052026",
                # PADG 4/2023, which carries it out, and its amendments.
                "PADG_042023", "PADG_062024", "PADG_042025", "PADG_162026",
                # PBI 21/14/PBI/2019, which PBI 7/2023 replaced.
                "pbi_211419",
            ) + (
                f"{_BI_ROOT}/id/publikasi/ruang-media/news-release/Pages/sp_2520923.aspx",
                f"{_BI_ROOT}/id/publikasi/ruang-media/news-release/Pages/sp_229720.aspx",
            ),
            sample_attachment_limit=40,
            smallest_attachments_first=True,
        ),
    ),
)

# --- Vietnam: Công báo (Official Gazette) ------------------------------------------

_CONG_BAO_ROOT = "https://congbao.chinhphu.vn"

#: Words in the titles of Vietnamese legal documents on customs and trade
#: across the border: customs, export, import, origin, tariff schedules,
#: quarantine, transit, temporary import and re-export, bonded warehouses,
#: non-tariff zones, foreign trade, free trade, trade remedies, food safety.
_VIETNAM_TRADE_TITLE = (
    r"hải quan|xuất khẩu|nhập khẩu|xuất xứ|biểu thuế|kiểm dịch|quá cảnh|tạm nhập|"
    r"tái xuất|kho ngoại quan|phi thuế quan|ngoại thương|thương mại tự do|"
    r"phòng vệ thương mại|bán phá giá|chống trợ cấp|an toàn thực phẩm"
)


def _cong_bao_section(folder: str, listing_slug: str) -> CrawlSection:
    """Return the section for one issuing office's list in the gazette."""
    return CrawlSection(
        folder=folder,
        listing_urls=(f"{_CONG_BAO_ROOT}/van-ban-dang-cong-bao/{listing_slug}.htm",),
        detail_pattern=r"^/van-ban/[a-z0-9-]+-\d+\.htm$",
        page_url_template="{stem}/trang-{number}.htm",
        link_text_pattern=_VIETNAM_TRADE_TITLE,
        sample_limit_per_listing=20,
        sample_attachment_limit=20,
    )


#: The Official Gazette publishes every law, decree, and circular as issued,
#: listed per issuing office, ten to a page. A document page carries the dates
#: and title; the text itself is a DOCX and a PDF on the government file
#: server, which omits an intermediate certificate (see app/certificates).
#: Titles on the lists pick the customs and trade documents before any is
#: fetched. vbpl.vn is not used: its texts load from /api/, which its
#: robots.txt forbids. robots.txt here allows everything.
CONG_BAO = CrawlSite(
    key="cong-bao",
    country="Vietnam",
    folder="Cong Bao",
    root=_CONG_BAO_ROOT,
    hosts=frozenset({"congbao.chinhphu.vn"}),
    content_selectors=("div.box-section--main", "#main-content"),
    # The office's own list; a "new documents" column beside it lists every office.
    link_region_selectors=("div.box--list",),
    attachment_hosts=frozenset({"g7.cdnchinhphu.vn"}),
    attachment_path_patterns=(r"^/api/download/stream$",),
    # The DOCX and the PDF hold the same text; the DOCX reads cleanly. Older
    # documents come as a legacy DOC, which cannot be read, so their PDF is taken.
    attachment_preference=(".docx", ".pdf"),
    sections=(
        _cong_bao_section("Quoc hoi", "quoc-hoi-c31"),
        _cong_bao_section("Chinh phu", "chinh-phu-c1"),
        _cong_bao_section("Thu tuong", "thu-tuong-chinh-phu-c2"),
        _cong_bao_section("Bo Tai chinh", "bo-tai-chinh-c10"),
        _cong_bao_section("Bo Cong Thuong", "bo-cong-thuong-c3"),
        _cong_bao_section("Bo Nong nghiep va Moi truong", "bo-nong-nghiep-va-moi-truong-c167"),
        _cong_bao_section("Bo Y te", "bo-y-te-c25"),
    ),
)

# --- pajak.go.id (reference; its corpus already exists in corpus-pajak) --------

_PAJAK_ROOT = "https://www.pajak.go.id"


def _peraturan_listing(document_type_id: str) -> str:
    """Return the pajak.go.id regulation index filtered to one document type."""
    return f"{_PAJAK_ROOT}/id/peraturan?field_jenis_dokumen_target_id={document_type_id}"


PAJAK = CrawlSite(
    key="pajak",
    country="Indonesia",
    folder="Pajak",
    root=_PAJAK_ROOT,
    hosts=frozenset({"www.pajak.go.id", "pajak.go.id"}),
    disallowed_prefixes=(
        "/core/", "/profiles/", "/admin/", "/comment/reply/", "/filter/tips",
        "/node/add/", "/search/", "/user/", "/id/search/", "/en/search/",
    ),
    path_rewrites=((r"^/index\.php(?=/)", ""),),
    content_selectors=("#block-porto-content", "div.region-content", "main", "body"),
    # On index pages such as "Belajar Pajak" the entries sit beside the main block.
    link_region_selectors=("div.region-content", "body"),
    furniture_selectors=(".addtoany_list", ".statistics-counter"),
    title_suffix=r"\s*\|\s*Direktorat Jenderal Pajak\s*$",
    sections=(
        CrawlSection(
            folder="Belajar Pajak",
            listing_urls=(f"{_PAJAK_ROOT}/index-belajar-pajak/",),
            detail_pattern=(
                r"^/id/(?!artikel|berita|siaran-pers|pengumuman|peraturan|index-|faq-page)"
                r"[^/]+$"
            ),
            paginate=False,
            sample_limit_per_listing=120,
        ),
        CrawlSection(
            folder="FAQ",
            listing_urls=(f"{_PAJAK_ROOT}/id/faq-page",),
            detail_pattern=r"^/id/(?!faq-page)[^/]+$",
            sample_limit_per_listing=40,
        ),
        CrawlSection(
            folder="Peraturan",
            # Undang-Undang, Peraturan Pemerintah, PMK, Peraturan Dirjen Pajak.
            # Unfiltered, the newest entries are almost all weekly exchange-rate
            # decrees, which would crowd out the regulations people ask about.
            listing_urls=(
                _peraturan_listing("13926"),
                _peraturan_listing("13902"),
                _peraturan_listing("13899"),
                _peraturan_listing("13894"),
            ),
            detail_pattern=r"^/id/peraturan/[^/]+$",
            sample_limit_per_listing=18,
            sample_attachment_limit=20,
        ),
        CrawlSection(
            folder="Artikel",
            listing_urls=(f"{_PAJAK_ROOT}/id/artikel-page",),
            detail_pattern=r"^/id/artikel/[^/]+$",
            sample_limit_per_listing=40,
        ),
        CrawlSection(
            folder="Siaran Pers",
            listing_urls=(f"{_PAJAK_ROOT}/id/siaran-pers-page",),
            detail_pattern=r"^/id/siaran-pers/[^/]+$",
            sample_limit_per_listing=30,
        ),
        CrawlSection(
            folder="Formulir",
            fixed_urls=(f"{_PAJAK_ROOT}/id/formulir-page",),
            sample_attachment_limit=40,
        ),
    ),
)

#: Every crawlable site, by its command-line key, in order of influence.
SITES: dict[str, CrawlSite] = {
    site.key: site
    for site in (
        BEA_CUKAI, JDIH_KEMENKEU, DAGLU, E_SKA, PEN, BANK_INDONESIA, LPEI, PAJAK, CONG_BAO,
    )
}
