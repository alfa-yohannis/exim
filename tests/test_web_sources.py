"""Tests for external link detection, URL harvesting and snapshot writing.

Nothing here touches the network: the harvester reads from a throwaway index
and the writer writes to a temporary directory, so the suite stays offline and
fast while still covering the rules that decide *what* would be fetched.
"""

from __future__ import annotations

import pytest

from app.documents import ExternalLinkExtractor
from app.storage import DocumentCatalogue
from app.web_sources import ExternalPage, ExternalUrlHarvester, SnapshotWriter

SINTA_URL = "https://sinta.kemdiktisaintek.go.id/authors/profile/120902"
SCHOLAR_PROFILE = "https://scholar.google.com/citations?hl=en&user=GzKCr4YAAAAJ&sortby=pubdate"
SCHOLAR_ARTICLE = (
    "https://scholar.google.com/citations?view_op=view_citation&hl=en"
    "&user=GzKCr4YAAAAJ&citation_for_view=GzKCr4YAAAAJ:abc"
)


class TestExternalLinkExtractor:
    """Finding outbound links inside a passage."""

    def test_finds_a_plain_url(self):
        links = ExternalLinkExtractor().extract(f"Profil: {SINTA_URL}")
        assert links[0]["url"] == SINTA_URL

    @pytest.mark.parametrize(
        "url,expected",
        [
            (SINTA_URL, "SINTA"),
            (SCHOLAR_PROFILE, "Google Scholar"),
            ("https://orcid.org/0000-0002", "ORCID"),
            # The ministry renamed its domain; both spellings must resolve.
            ("https://pddikti.kemdiktisaintek.go.id/detail-pt/abc", "PDDikti"),
            ("https://pddikti.kemdikbud.go.id/detail-pt/abc", "PDDikti"),
            ("https://www.pradita.ac.id/", "Universitas Pradita"),
        ],
    )
    def test_labels_known_hosts_readably(self, url, expected):
        assert ExternalLinkExtractor().describe(url) == expected

    def test_falls_back_to_the_hostname(self):
        assert ExternalLinkExtractor().describe("https://www.example.org/x") == "example.org"

    def test_deduplicates_repeated_urls(self):
        links = ExternalLinkExtractor().extract(f"{SINTA_URL} dan lagi {SINTA_URL}")
        assert len(links) == 1

    def test_strips_trailing_sentence_punctuation(self):
        """A URL ending a sentence must not capture the full stop."""
        links = ExternalLinkExtractor().extract(f"Lihat {SINTA_URL}.")
        assert links[0]["url"] == SINTA_URL

    def test_ignores_text_without_links(self):
        assert ExternalLinkExtractor().extract("Tidak ada tautan di sini") == []

    def test_handles_empty_text(self):
        assert ExternalLinkExtractor().extract("") == []

    def test_caps_the_number_of_links(self):
        """One link-heavy spreadsheet row must not flood a citation card."""
        crowded = " ".join(f"https://example.org/{index}" for index in range(40))
        assert len(ExternalLinkExtractor().extract(crowded)) <= ExternalLinkExtractor.MAXIMUM_LINKS


class TestUrlHarvesting:
    """Deciding which referenced URLs are worth fetching."""

    @pytest.fixture
    def harvester(self, isolated_settings, guard):
        catalogue = DocumentCatalogue(isolated_settings, guard)
        yield ExternalUrlHarvester(catalogue), catalogue
        catalogue.close()

    def is_allowed(self, harvester, url: str) -> bool:
        """Convenience wrapper over the private allow-list check."""
        return harvester._is_allowed(url)

    def test_accepts_a_sinta_profile(self, harvester):
        assert self.is_allowed(harvester[0], SINTA_URL) is True

    def test_accepts_a_scholar_profile(self, harvester):
        assert self.is_allowed(harvester[0], SCHOLAR_PROFILE) is True

    def test_rejects_a_single_scholar_article(self, harvester):
        """The corpus holds hundreds of these; following them triggers a 429."""
        assert self.is_allowed(harvester[0], SCHOLAR_ARTICLE) is False

    def test_rejects_hosts_outside_the_allow_list(self, harvester):
        """Without this the harvester would become a general web crawler."""
        assert self.is_allowed(harvester[0], "https://example.org/anything") is False

    def test_rejects_a_non_profile_path_on_an_allowed_host(self, harvester):
        assert self.is_allowed(harvester[0], "https://sinta.kemdiktisaintek.go.id/departments") is False

    def test_harvests_links_out_of_indexed_chunks(self, harvester, isolated_settings):
        from tests.test_storage import build_chunks, build_document

        extractor, catalogue = harvester
        document = build_document("corpus-lokal/data_dosen_sinta.xlsx")
        catalogue.save_document(
            document,
            build_chunks(document.document_id, [f"HANDRI SANTOSO\t{SINTA_URL}\t{SCHOLAR_PROFILE}"]),
            section_count=1, character_count=120,
            scanned_section_count=0, ocr_section_count=0, status="indexed",
        )
        harvested = {page.url for page in extractor.harvest()}
        assert SINTA_URL in harvested
        assert SCHOLAR_PROFILE in harvested

    def test_harvest_is_empty_for_an_empty_index(self, harvester):
        assert harvester[0].harvest() == []


class TestSeedParsing:
    """Reading the per-seed link pattern out of a configured entry."""

    @pytest.fixture
    def runner(self, isolated_settings, guard, tmp_path):
        from app.web_sources import WebSnapshotRunner

        isolated_settings.web_snapshot_directory = tmp_path / "eksternal"
        catalogue = DocumentCatalogue(isolated_settings, guard)
        yield WebSnapshotRunner(isolated_settings, catalogue, guard), isolated_settings
        catalogue.close()

    def test_entry_carrying_its_own_pattern(self, runner):
        instance, _ = runner
        url, pattern = instance._parse_seed("https://example.org/list::/detail-x/")
        assert url == "https://example.org/list"
        assert pattern == "/detail-x/"

    def test_bare_entry_falls_back_to_the_default_pattern(self, runner):
        instance, settings = runner
        url, pattern = instance._parse_seed("https://example.org/list")
        assert url == "https://example.org/list"
        assert pattern == settings.browser_detail_link_pattern

    def test_separator_is_not_confused_with_the_scheme_colon(self, runner):
        """"https://" contains a colon; only "::" may split the entry."""
        instance, settings = runner
        url, pattern = instance._parse_seed("https://example.org/a")
        assert url.startswith("https://")
        assert pattern == settings.browser_detail_link_pattern

    def test_empty_pattern_after_separator_falls_back(self, runner):
        instance, settings = runner
        _url, pattern = instance._parse_seed("https://example.org/list::")
        assert pattern == settings.browser_detail_link_pattern

    def test_default_seeds_cover_both_sites_with_distinct_patterns(self, runner):
        """A single shared pattern would silently follow nothing on one site.

        Checked as pairs rather than a map by host, because the university site
        contributes more than one seed and a map would keep only the last.
        """
        instance, settings = runner
        parsed = [instance._parse_seed(entry) for entry in settings.browser_seed_urls]

        assert any(
            "pddikti" in url and "detail-" in pattern for url, pattern in parsed
        ), "PDDikti seed harus mengikuti tautan /detail-.../"
        assert any(
            "pradita.ac.id" in url and "praktisi-akademisi/dosen" in pattern
            for url, pattern in parsed
        ), "Seed Pradita harus mengikuti halaman dosen"

    def test_labels_each_seed_by_its_site(self, runner):
        instance, _ = runner
        assert instance._label_for("https://pddikti.kemdiktisaintek.go.id/x") == "PDDikti"
        assert instance._label_for("https://www.pradita.ac.id/x") == "Universitas Pradita"


class TestSnapshotWriter:
    """Writing a fetched page into the local corpus."""

    @pytest.fixture
    def writer(self, isolated_settings, guard, tmp_path):
        isolated_settings.web_snapshot_directory = tmp_path / "eksternal"
        return SnapshotWriter(isolated_settings, guard)

    @pytest.fixture
    def page(self):
        return ExternalPage(url=SINTA_URL, label="SINTA", referenced_by="data_dosen.xlsx")

    def test_writes_a_file_named_for_the_source(self, writer, page):
        written = writer.write(page, "Profil Dosen", "isi halaman")
        assert written.exists()
        assert written.name.startswith("SINTA - ")

    def test_header_records_url_and_capture_time(self, writer, page):
        content = writer.write(page, "Profil", "isi").read_text(encoding="utf-8")
        assert f"URL: {SINTA_URL}" in content
        assert "Diambil pada:" in content

    def test_url_repeats_through_the_body(self, writer, page):
        """Retrieval works on chunks, so every chunk must carry its provenance."""
        body = "\n".join(f"baris {index}" for index in range(120))
        content = writer.write(page, "Profil", body).read_text(encoding="utf-8")
        assert content.count(SINTA_URL) > 3

    def test_rerunning_replaces_rather_than_accumulates(self, writer, page):
        first = writer.write(page, "Profil", "versi satu")
        second = writer.write(page, "Profil", "versi dua")
        assert first == second
        assert "versi dua" in second.read_text(encoding="utf-8")

    def test_unsafe_filename_characters_are_removed(self, writer, page):
        written = writer.write(page, 'Judul: dengan / karakter * aneh?', "isi")
        assert not any(character in written.name for character in '<>:"/\\|?*')

    def test_refuses_a_directory_inside_the_corpus(self, isolated_settings, guard):
        from app.safety import SourceWriteAttempt

        isolated_settings.web_snapshot_directory = (
            isolated_settings.source_directory / "eksternal"
        )
        with pytest.raises(SourceWriteAttempt):
            SnapshotWriter(isolated_settings, guard)
