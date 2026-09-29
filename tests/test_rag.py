"""Tests for prompt construction and the question-answering event stream.

Retrieval and generation are replaced with test doubles so these tests assert
orchestration — event order, grounding rules, citation numbering, failure
handling — without loading an embedding model or contacting Ollama.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from app.documents import RetrievedPassage
from app.language_models import BackendStatus, LanguageModelBackend
from app.rag import PromptBuilder, QuestionRequest, RagEngine


class StubBackend(LanguageModelBackend):
    """A backend that replays scripted fragments, or raises on demand."""

    def __init__(self, settings, fragments=None, failure=None):
        """Record what this stub should emit, and capture the prompts it sees."""
        super().__init__(settings)
        self._fragments = fragments if fragments is not None else ["Jawaban ", "[1]."]
        self._failure = failure
        self.received_system_prompt = None
        self.received_user_prompt = None

    @property
    def name(self) -> str:
        return "stub"

    @property
    def model_name(self) -> str:
        return "stub-model"

    @property
    def runs_locally(self) -> bool:
        return True

    def check_status(self) -> BackendStatus:
        return BackendStatus(True, self.name, self.model_name, "siap", runs_locally=True)

    def generate_stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Record the prompts, then emit fragments or raise the scripted error."""
        self.received_system_prompt = system_prompt
        self.received_user_prompt = user_prompt
        if self._failure is not None:
            raise self._failure
        yield from self._fragments


class StubFactory:
    """A factory that always returns one prepared backend."""

    def __init__(self, backend, error=None):
        """Hold the backend to hand out, or the error to raise instead."""
        self._backend = backend
        self._error = error

    def create(self, backend_name=None):
        """Return the prepared backend, or raise the configured error."""
        if self._error is not None:
            raise self._error
        return self._backend


class StubRetriever:
    """A retriever that returns fixed passages and records its arguments."""

    def __init__(self, passages=None, error=None):
        """Hold the passages to return, or the error to raise."""
        self._passages = passages if passages is not None else []
        self._error = error
        self.received_question = None
        self.received_folder_filter = None

    def retrieve(self, question, folder_filter=None, passage_count=None):
        """Record the call and return the prepared passages."""
        if self._error is not None:
            raise self._error
        self.received_question = question
        self.received_folder_filter = folder_filter
        for index, passage in enumerate(self._passages, start=1):
            passage.citation_number = index
        return self._passages


def build_passage(number: int, text: str = "Rata-rata EWMP adalah 12,5 SKS.", ocr=False):
    """Construct a retrieval result suitable for prompt assembly."""
    return RetrievedPassage(
        chunk_id=number,
        document_id=f"doc-{number}",
        document_name=f"berkas{number}.pdf",
        relative_path=f"1. Budaya Mutu/berkas{number}.pdf",
        absolute_path=f"/corpus/1. Budaya Mutu/berkas{number}.pdf",
        section_label="Halaman 3",
        text=text,
        produced_by_ocr=ocr,
        dense_score=0.7,
        citation_number=number,
    )


def collect_events(engine: RagEngine, request: QuestionRequest) -> list:
    """Drain the answer stream into a list of events."""
    return list(engine.stream_answer(request))


def event_names(events: list) -> list[str]:
    """Extract the ordered event names from a drained stream."""
    return [event.name for event in events]


def joined_text(events: list) -> str:
    """Concatenate the text of every token event."""
    return "".join(event.payload["text"] for event in events if event.name == "token")


class TestPromptBuilder:
    """Assembling the grounded prompt."""

    def test_system_prompt_forbids_outside_knowledge(self, isolated_settings):
        prompt = PromptBuilder(isolated_settings).build_system_prompt()
        assert "HANYA berdasarkan KONTEKS" in prompt

    def test_system_prompt_requires_citations(self, isolated_settings):
        assert "[1][3]" in PromptBuilder(isolated_settings).build_system_prompt()

    def test_prompts_show_no_citation_placeholder(self, isolated_settings):
        """Shown "[n]", the model copied it into its answer as "[n2]"."""
        builder = PromptBuilder(isolated_settings)
        user_prompt = builder.build_user_prompt("Berapa EWMP?", [build_passage(1)])
        assert "[n" not in builder.build_system_prompt()
        assert "[n" not in user_prompt

    def test_system_prompt_demands_verbatim_identifiers(self, isolated_settings):
        """Decree numbers and figures are audited; paraphrasing them is a defect."""
        assert "PERSIS" in PromptBuilder(isolated_settings).build_system_prompt()

    def test_user_prompt_numbers_each_passage(self, isolated_settings):
        prompt = PromptBuilder(isolated_settings).build_user_prompt(
            "Berapa EWMP?", [build_passage(1), build_passage(2)]
        )
        assert "[1] berkas1.pdf" in prompt
        assert "[2] berkas2.pdf" in prompt

    def test_user_prompt_includes_the_question(self, isolated_settings):
        prompt = PromptBuilder(isolated_settings).build_user_prompt(
            "Berapa EWMP?", [build_passage(1)]
        )
        assert "Berapa EWMP?" in prompt

    def test_user_prompt_records_each_passage_location(self, isolated_settings):
        prompt = PromptBuilder(isolated_settings).build_user_prompt(
            "Berapa EWMP?", [build_passage(1)]
        )
        assert "1. Budaya Mutu/berkas1.pdf" in prompt

    def test_ocr_passages_are_marked_in_the_context(self, isolated_settings):
        prompt = PromptBuilder(isolated_settings).build_user_prompt(
            "Berapa EWMP?", [build_passage(1, ocr=True)]
        )
        assert "(OCR)" in prompt

    def test_context_is_truncated_to_the_character_budget(self, isolated_settings):
        isolated_settings.maximum_context_characters = 500
        passages = [build_passage(index, text="x" * 400) for index in range(1, 6)]
        prompt = PromptBuilder(isolated_settings).build_user_prompt("Pertanyaan?", passages)
        assert len(prompt) < 1600

    def test_empty_context_still_produces_a_valid_prompt(self, isolated_settings):
        prompt = PromptBuilder(isolated_settings).build_user_prompt("Pertanyaan?", [])
        assert "PERTANYAAN" in prompt

    @pytest.mark.parametrize(
        ("language", "named", "instruction"),
        [
            ("id", "Bahasa Indonesia", "Jawab dalam Bahasa Indonesia."),
            ("en", "bahasa Inggris (English)", "Answer in English."),
            ("vi", "bahasa Vietnam (Tiếng Việt)", "Trả lời bằng tiếng Việt."),
        ],
    )
    def test_answer_language_follows_the_interface(self, isolated_settings, language, named, instruction):
        builder = PromptBuilder(isolated_settings)
        assert f"dalam {named} yang formal" in builder.build_system_prompt(language)
        assert builder.build_user_prompt("Pertanyaan?", [], language=language).endswith(instruction)

    def test_an_unknown_language_falls_back_to_indonesian(self, isolated_settings):
        prompt = PromptBuilder(isolated_settings).build_system_prompt("fr")
        assert "dalam Bahasa Indonesia yang formal" in prompt


class TestAnswerStreamOrdering:
    """The sequence of events a question produces."""

    @pytest.fixture
    def engine(self, isolated_settings):
        """An engine wired to stub retrieval and a stub backend."""
        backend = StubBackend(isolated_settings)
        retriever = StubRetriever([build_passage(1), build_passage(2)])
        engine = RagEngine(isolated_settings, retriever, StubFactory(backend))
        engine.stub_backend = backend
        engine.stub_retriever = retriever
        return engine

    def test_sources_arrive_before_any_answer_text(self, engine):
        """The evidence panel must render while the model is still writing."""
        names = event_names(collect_events(engine, QuestionRequest("Berapa EWMP?")))
        assert names.index("sources") < names.index("token")

    def test_stream_always_ends_with_done(self, engine):
        names = event_names(collect_events(engine, QuestionRequest("Berapa EWMP?")))
        assert names[-1] == "done"

    def test_citations_are_numbered_from_one(self, engine):
        events = collect_events(engine, QuestionRequest("Berapa EWMP?"))
        sources = next(event for event in events if event.name == "sources")
        numbers = [item["citation_number"] for item in sources.payload["citations"]]
        assert numbers == [1, 2]

    def test_answer_text_is_streamed_in_order(self, engine):
        events = collect_events(engine, QuestionRequest("Berapa EWMP?"))
        assert joined_text(events) == "Jawaban [1]."

    def test_done_reports_the_passage_count_and_backend(self, engine):
        events = collect_events(engine, QuestionRequest("Berapa EWMP?"))
        summary = events[-1].payload
        assert summary["passage_count"] == 2
        assert summary["backend_name"] == "stub"

    def test_the_question_reaches_the_retriever(self, engine):
        collect_events(engine, QuestionRequest("Berapa EWMP?"))
        assert engine.stub_retriever.received_question == "Berapa EWMP?"

    def test_the_folder_filter_reaches_the_retriever(self, engine):
        collect_events(
            engine, QuestionRequest("Berapa EWMP?", folder_filter="1. Budaya Mutu")
        )
        assert engine.stub_retriever.received_folder_filter == "1. Budaya Mutu"

    def test_the_backend_receives_the_grounded_prompts(self, engine):
        collect_events(engine, QuestionRequest("Berapa EWMP?"))
        assert "KONTEKS" in engine.stub_backend.received_user_prompt
        assert "HANYA berdasarkan KONTEKS" in engine.stub_backend.received_system_prompt


class _StubRewriter:
    """Restates every question for Vietnam, and no other country."""

    RESTATED = "Quy định nhập khẩu cà phê vào Việt Nam"

    def supports(self, country):
        return country == "Vietnam"

    def rewrite(self, question, country):
        return self.RESTATED


class TestCountryRules:
    """A question about one country's rules, chosen under "Aturan negara"."""

    QUESTION = "Syarat ekspor kopi ke Vietnam?"

    @pytest.fixture
    def engine(self, isolated_settings):
        backend = StubBackend(isolated_settings)
        retriever = StubRetriever([build_passage(1)])
        engine = RagEngine(
            isolated_settings, retriever, StubFactory(backend), query_rewriter=_StubRewriter()
        )
        engine.stub_backend = backend
        engine.stub_retriever = retriever
        return engine

    def test_the_restated_question_is_searched_in_that_country(self, engine):
        collect_events(engine, QuestionRequest(self.QUESTION, folder_filter="Vietnam"))
        assert engine.stub_retriever.received_question == _StubRewriter.RESTATED
        assert engine.stub_retriever.received_folder_filter == "Vietnam"

    def test_the_reader_is_shown_what_was_searched(self, engine):
        events = collect_events(
            engine, QuestionRequest(self.QUESTION, folder_filter="Vietnam", language="en")
        )
        status = next(event for event in events if event.name == "status")
        sources = next(event for event in events if event.name == "sources")
        assert status.payload["message"] == "Preparing the search in Vietnam's rules..."
        assert sources.payload["search_query"] == _StubRewriter.RESTATED
        assert sources.payload["country"] == "Vietnam"

    def test_the_answer_keeps_to_that_country_and_the_question_as_asked(self, engine):
        collect_events(engine, QuestionRequest(self.QUESTION, folder_filter="Vietnam"))
        prompt = engine.stub_backend.received_user_prompt
        assert "Jawab hanya tentang ketentuan Vietnam" in prompt
        assert self.QUESTION in prompt

    def test_the_answer_is_told_which_side_of_the_trade_applies(self, engine):
        collect_events(engine, QuestionRequest(self.QUESTION, folder_filter="Vietnam"))
        assert "ketentuan impor Vietnam" in engine.stub_backend.received_user_prompt

    def test_without_a_country_the_question_is_searched_as_typed(self, engine):
        events = collect_events(engine, QuestionRequest("Berapa EWMP?"))
        assert engine.stub_retriever.received_question == "Berapa EWMP?"
        sources = next(event for event in events if event.name == "sources")
        assert "search_query" not in sources.payload

    def test_country_names_follow_the_reader(self, isolated_settings):
        builder = PromptBuilder(isolated_settings)
        assert builder.country_name("Vietnam", "vi") == "Việt Nam"
        assert builder.country_name("Vietnam", "en") == "Vietnam"


class TestCitationContent:
    """What the evidence cards carry."""

    def test_excerpts_are_shortened_for_display(self, isolated_settings):
        long_passage = build_passage(1, text="kata " * 400)
        engine = RagEngine(
            isolated_settings,
            StubRetriever([long_passage]),
            StubFactory(StubBackend(isolated_settings)),
        )
        events = collect_events(engine, QuestionRequest("Pertanyaan?"))
        sources = next(event for event in events if event.name == "sources")
        assert len(sources.payload["citations"][0]["excerpt"]) < 700

    def test_ocr_provenance_is_reported_to_the_interface(self, isolated_settings):
        engine = RagEngine(
            isolated_settings,
            StubRetriever([build_passage(1, ocr=True)]),
            StubFactory(StubBackend(isolated_settings)),
        )
        events = collect_events(engine, QuestionRequest("Pertanyaan?"))
        sources = next(event for event in events if event.name == "sources")
        assert sources.payload["citations"][0]["produced_by_ocr"] is True

    def test_retrieval_methods_are_reported(self, isolated_settings):
        engine = RagEngine(
            isolated_settings,
            StubRetriever([build_passage(1)]),
            StubFactory(StubBackend(isolated_settings)),
        )
        events = collect_events(engine, QuestionRequest("Pertanyaan?"))
        sources = next(event for event in events if event.name == "sources")
        assert sources.payload["citations"][0]["retrieval_methods"] == ["semantic"]


class TestGroundingBehaviour:
    """What happens when the corpus has nothing to offer."""

    def test_no_results_skips_the_language_model(self, isolated_settings):
        backend = StubBackend(isolated_settings)
        engine = RagEngine(isolated_settings, StubRetriever([]), StubFactory(backend))
        collect_events(engine, QuestionRequest("Pertanyaan tanpa jawaban"))
        assert backend.received_user_prompt is None

    def test_no_results_returns_the_not_found_message(self, isolated_settings):
        engine = RagEngine(
            isolated_settings, StubRetriever([]), StubFactory(StubBackend(isolated_settings))
        )
        events = collect_events(engine, QuestionRequest("Pertanyaan tanpa jawaban"))
        assert "tidak ditemukan" in joined_text(events).lower()

    @pytest.mark.parametrize(
        ("language", "phrase"),
        [("en", "not found in the indexed documents"), ("vi", "Không tìm thấy thông tin")],
    )
    def test_not_found_message_follows_the_language(self, isolated_settings, language, phrase):
        engine = RagEngine(
            isolated_settings, StubRetriever([]), StubFactory(StubBackend(isolated_settings))
        )
        events = collect_events(engine, QuestionRequest("Question?", language=language))
        assert phrase in joined_text(events)

    def test_status_line_follows_the_language(self, isolated_settings):
        engine = RagEngine(
            isolated_settings,
            StubRetriever([build_passage(1)]),
            StubFactory(StubBackend(isolated_settings)),
        )
        events = collect_events(engine, QuestionRequest("Question?", language="en"))
        status = next(event for event in events if event.name == "status")
        assert status.payload["message"].startswith("Writing the answer with stub")

    def test_no_results_still_terminates_the_stream(self, isolated_settings):
        engine = RagEngine(
            isolated_settings, StubRetriever([]), StubFactory(StubBackend(isolated_settings))
        )
        assert event_names(collect_events(engine, QuestionRequest("x")))[-1] == "done"


class TestFailureHandling:
    """Failures must terminate the stream, never escape into the web layer."""

    def test_retrieval_failure_becomes_an_error_event(self, isolated_settings):
        engine = RagEngine(
            isolated_settings,
            StubRetriever(error=RuntimeError("indeks rusak")),
            StubFactory(StubBackend(isolated_settings)),
        )
        names = event_names(collect_events(engine, QuestionRequest("Pertanyaan?")))
        assert names == ["error", "done"]

    def test_generation_failure_keeps_the_citations(self, isolated_settings):
        backend = StubBackend(isolated_settings, failure=RuntimeError("model mati"))
        engine = RagEngine(
            isolated_settings, StubRetriever([build_passage(1)]), StubFactory(backend)
        )
        names = event_names(collect_events(engine, QuestionRequest("Pertanyaan?")))
        assert "sources" in names
        assert "error" in names
        assert names[-1] == "done"

    def test_unknown_backend_becomes_an_error_event(self, isolated_settings):
        engine = RagEngine(
            isolated_settings,
            StubRetriever([build_passage(1)]),
            StubFactory(None, error=ValueError("Backend 'x' tidak dikenal")),
        )
        events = collect_events(engine, QuestionRequest("Pertanyaan?"))
        error_event = next(event for event in events if event.name == "error")
        assert "tidak dikenal" in error_event.payload["message"]

    def test_a_failing_stream_never_raises(self, isolated_settings):
        """The web layer relies on this: a raise would truncate the SSE response."""
        engine = RagEngine(
            isolated_settings,
            StubRetriever(error=OSError("disk penuh")),
            StubFactory(StubBackend(isolated_settings)),
        )
        collect_events(engine, QuestionRequest("Pertanyaan?"))
