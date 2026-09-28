"""The websites the corpus is built from, and how to crawl each of them.

Sites are listed in the order of README "Sumber yang di-crawl": by how much of
a typical export-import question they answer. Each :class:`CrawlSite` becomes
one top-level corpus folder, so the interface's folder filter reads as a
choice of source.
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

# --- pajak.go.id (reference; its corpus already exists in corpus-pajak) --------

_PAJAK_ROOT = "https://www.pajak.go.id"


def _peraturan_listing(document_type_id: str) -> str:
    """Return the pajak.go.id regulation index filtered to one document type."""
    return f"{_PAJAK_ROOT}/id/peraturan?field_jenis_dokumen_target_id={document_type_id}"


PAJAK = CrawlSite(
    key="pajak",
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
SITES: dict[str, CrawlSite] = {site.key: site for site in (BEA_CUKAI, PAJAK)}
