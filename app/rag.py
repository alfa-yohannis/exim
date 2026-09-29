"""Question answering over the indexed corpus.

:class:`RagEngine` is the orchestration layer the web server talks to. One
question flows through four steps: retrieve candidate passages, assemble them
into a numbered context block, stream an answer from the configured language
model, and hand back the citations that back it up.

The prompt in :class:`PromptBuilder` is written in Indonesian and is deliberately
strict about grounding. Trade rules are enforced at the border, so an answer
that quietly invents a regulation number is worse than no answer at all — the
model is instructed to say when the corpus does not contain something, and to
quote identifiers verbatim rather than paraphrase them. The answer itself is
written in the language the interface is set to: Indonesian, English, or
Vietnamese.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterator

from .config import SETTINGS, Settings
from .conversations import ChatTurn, ConversationStore
from .documents import ExternalLinkExtractor, RetrievedPassage
from .language_models import LanguageModelFactory
from .query_rewriting import CountryQueryRewriter, trade_direction
from .retrieval import HybridRetriever

LOGGER = logging.getLogger(__name__)


@dataclass
class QuestionRequest:
    """Everything the engine needs to answer one question."""

    question: str
    #: Restricts retrieval to a single top-level folder, or None for the whole corpus.
    folder_filter: str | None = None
    #: Overrides the configured backend for this question only.
    backend_name: str | None = None
    #: Overrides how many passages are placed in the context.
    passage_count: int | None = None
    #: Conversation to continue and record this exchange in, or None for a
    #: one-off question that is not saved.
    session_id: str | None = None
    #: Language the interface is set to, and the answer is written in:
    #: ``id``, ``en`` or ``vi``.
    language: str = "id"


@dataclass
class StreamEvent:
    """One message in the answer stream, delivered to the browser as SSE.

    ``name`` is the event type the browser listens for: ``sources`` carries the
    citations, ``token`` carries answer text, ``done`` closes the stream and
    ``error`` reports a failure the interface should surface.
    """

    name: str
    payload: dict


@dataclass
class AnswerCitation:
    """A passage shown beneath the answer as supporting evidence."""

    citation_number: int
    document_name: str
    relative_path: str
    document_id: str
    section_label: str
    excerpt: str
    produced_by_ocr: bool
    retrieval_methods: list[str] = field(default_factory=list)
    relevance_score: float = 0.0
    #: Live pages this passage points at — a SINTA or Scholar profile, say —
    #: so a reader can go to the source itself rather than the local copy.
    external_links: list[dict] = field(default_factory=list)

    def as_dictionary(self) -> dict:
        """Represent the citation as JSON-serialisable data."""
        return {
            "citation_number": self.citation_number,
            "document_name": self.document_name,
            "relative_path": self.relative_path,
            "document_id": self.document_id,
            "section_label": self.section_label,
            "excerpt": self.excerpt,
            "produced_by_ocr": self.produced_by_ocr,
            "retrieval_methods": self.retrieval_methods,
            "relevance_score": round(self.relevance_score, 4),
            "external_links": self.external_links,
        }


class PromptBuilder:
    """Assembles the system and user prompts for one grounded question.

    The context block is numbered so that a citation marker in the answer maps
    directly onto a passage card in the interface, and truncated to a character
    budget so a long retrieval can never overflow the model's context window.
    """

    #: The answer language, named inside the Indonesian instructions, and the
    #: same instruction in that language, which a small model follows more
    #: reliably than a request made in another language.
    ANSWER_LANGUAGES = {
        "id": ("Bahasa Indonesia", "Jawab dalam Bahasa Indonesia."),
        "en": ("bahasa Inggris (English)", "Answer in English."),
        "vi": ("bahasa Vietnam (Tiếng Việt)", "Trả lời bằng tiếng Việt."),
    }

    SYSTEM_PROMPT = (
        "Anda adalah asisten informasi ekspor-impor untuk Indonesia dan Vietnam. "
        "Sumber Anda adalah salinan halaman dan dokumen publik situs pemerintah "
        "kedua negara: di Indonesia antara lain Direktorat Jenderal Bea dan Cukai "
        "(beacukai.go.id), JDIH Kementerian Keuangan, Kementerian Perdagangan, "
        "Bank Indonesia, dan LPEI; di Vietnam antara lain Hải quan Việt Nam "
        "(customs.gov.vn), Công báo, dan kementerian terkait. Lokasi setiap sumber "
        "di KONTEKS diawali nama negaranya. Anda bukan layanan resmi pemerintah.\n"
        "\n"
        "ATURAN MENJAWAB:\n"
        "1. Jawab HANYA berdasarkan KONTEKS yang diberikan. Jangan menambahkan "
        "pengetahuan dari luar konteks tersebut.\n"
        # Concrete numbers only: shown a placeholder "[n]", the model wrote
        # "[n2]" on 30 September 2026, which the interface cannot link.
        "2. Sertakan sitasi pada setiap pernyataan faktual: nomor sumber di "
        "KONTEKS dalam kurung siku, misalnya [2]. Boleh lebih dari satu, "
        "misalnya [1][3]. Di dalam kurung hanya angka, tanpa huruf atau kata.\n"
        "3. Jika KONTEKS tidak memuat jawabannya, nyatakan dengan jelas bahwa "
        "informasi tersebut tidak ditemukan pada dokumen yang terindeks. "
        "Kemudian sarankan di mana informasi itu mungkin tersedia, misalnya "
        "contact center Bravo Bea Cukai 1500225 atau kantor bea cukai setempat "
        "untuk Indonesia, kantor hải quan setempat untuk Vietnam, atau instansi "
        "penerbit izin yang terkait.\n"
        "4. Untuk tarif, angka, tanggal, nomor peraturan, pasal, dan ayat: kutip "
        "PERSIS seperti tertulis pada sumber. Jangan membulatkan, menerjemahkan, "
        "atau menyingkat. Sebutkan nomor peraturan yang menjadi dasarnya.\n"
        "5. Tulis jawaban dalam {language} yang formal dan ringkas, apa pun bahasa "
        "pertanyaan dan dokumen sumbernya. Terjemahkan isi sumber berbahasa lain, "
        "tetapi tulis nomor peraturan, nama dokumen resmi, dan angka persis seperti "
        "sumbernya.\n"
        "6. Bila sumber-sumber saling bertentangan, tunjukkan perbedaannya "
        "beserta sitasi masing-masing, jangan memilih salah satu secara diam-diam.\n"
        "7. Sumber bertanda (OCR) berasal dari pemindaian dan mungkin mengandung "
        "salah baca; beri catatan bila Anda mengutip angka dari sumber tersebut.\n"
        "8. Bila diminta alamat, tautan, URL, formulir, atau halaman layanan, "
        "tuliskan URL LENGKAP persis seperti pada KONTEKS — jangan disingkat, "
        "jangan diringkas menjadi nama situs saja, dan jangan direka. Tulis URL "
        "apa adanya agar dapat langsung diklik.\n"
        "9. Bila peraturan-peraturan di KONTEKS saling mengubah atau mencabut, "
        "utamakan yang terbaru dan sebutkan bahwa ketentuan lama telah diubah. "
        "Untuk keputusan atas kasus tertentu, sarankan konfirmasi ke kantor bea "
        "cukai atau instansi terkait di negara yang bersangkutan.\n"
        "10. Bila pertanyaan menyangkut dua negara, misalnya ekspor dari Indonesia "
        "ke Vietnam, pisahkan ketentuan negara asal dan negara tujuan, dan "
        "sebutkan negara pada setiap ketentuan."
    )

    #: Shown when retrieval returns nothing, instead of calling the model.
    EMPTY_RESULT_MESSAGES = {
        "id": (
            "Informasi tersebut tidak ditemukan pada dokumen yang terindeks.\n\n"
            "Saran:\n"
            "- Periksa ejaan atau gunakan istilah lain (misalnya singkatan resmi "
            "seperti PIB, PEB, HS, KITE, TPB).\n"
            "- Bila yang ditanyakan aturan negara lain, pilih negara itu di "
            "\"Aturan negara\".\n"
            "- Pastikan proses indexing sudah dijalankan dan selesai."
        ),
        "en": (
            "That information was not found in the indexed documents.\n\n"
            "Suggestions:\n"
            "- Check the spelling or try other terms, such as official abbreviations "
            "(PIB, PEB, HS, KITE, TPB).\n"
            "- If you are asking about another country's rules, choose that country "
            "under \"Rules of\".\n"
            "- Make sure indexing has been run and has finished."
        ),
        "vi": (
            "Không tìm thấy thông tin này trong các tài liệu đã lập chỉ mục.\n\n"
            "Gợi ý:\n"
            "- Kiểm tra chính tả hoặc dùng thuật ngữ khác, chẳng hạn các từ viết tắt "
            "chính thức (HS, C/O, PIB, PEB).\n"
            "- Nếu bạn hỏi về quy định của nước khác, hãy chọn nước đó ở mục "
            "\"Quy định của\".\n"
            "- Đảm bảo quá trình lập chỉ mục đã chạy xong."
        ),
    }

    #: Stream messages the interface shows while it waits, by language.
    STATUS_MESSAGES = {
        "restating": {
            "id": "Menyiapkan pencarian di aturan {country}...",
            "en": "Preparing the search in {country}'s rules...",
            "vi": "Đang chuẩn bị tìm trong quy định của {country}...",
        },
        "composing": {
            "id": "Menyusun jawaban dengan {backend} ({model})...",
            "en": "Writing the answer with {backend} ({model})...",
            "vi": "Đang soạn câu trả lời bằng {backend} ({model})...",
        },
        "search_failed": {
            "id": "Pencarian gagal: {error}",
            "en": "Search failed: {error}",
            "vi": "Tìm kiếm thất bại: {error}",
        },
        "model_failed": {
            "id": "Model bahasa gagal ({backend}): {error}",
            "en": "The language model failed ({backend}): {error}",
            "vi": "Mô hình ngôn ngữ gặp lỗi ({backend}): {error}",
        },
    }

    def __init__(self, settings: Settings) -> None:
        """Bind the builder to the configured context budget."""
        self._settings = settings

    @classmethod
    def supported_language(cls, language: str | None) -> str:
        """Return ``language`` if an answer can be written in it, else Indonesian."""
        return language if language in cls.ANSWER_LANGUAGES else "id"

    def build_system_prompt(self, language: str = "id") -> str:
        """Return the grounding instructions given to every backend."""
        language_name, _ = self.ANSWER_LANGUAGES[self.supported_language(language)]
        return self.SYSTEM_PROMPT.replace("{language}", language_name)

    def empty_result_message(self, language: str = "id") -> str:
        """Return the not-found answer in ``language``."""
        return self.EMPTY_RESULT_MESSAGES[self.supported_language(language)]

    def status_message(self, name: str, language: str = "id", **values: str) -> str:
        """Return one stream message in ``language``, with ``values`` filled in."""
        return self.STATUS_MESSAGES[name][self.supported_language(language)].format(**values)

    #: Country names that differ from the corpus folder name, by language.
    COUNTRY_NAMES = {"Vietnam": {"vi": "Việt Nam"}}

    def country_name(self, country: str, language: str = "id") -> str:
        """Return the corpus folder ``country`` as a reader of ``language`` writes it."""
        return self.COUNTRY_NAMES.get(country, {}).get(self.supported_language(language), country)

    def build_user_prompt(
        self,
        question: str,
        passages: list[RetrievedPassage],
        history: list[ChatTurn] | None = None,
        language: str = "id",
        country: str | None = None,
    ) -> str:
        """Return the conversation so far, the numbered context, and the question.

        History comes first and is explicitly labelled as *not* a source, so the
        model can resolve a pronoun against it without treating an earlier
        answer as evidence — otherwise a mistake in turn one would be cited as
        fact in turn three.
        """
        sections: list[str] = []
        history_block = self._render_history(history or [])
        if history_block:
            sections.append(
                "RIWAYAT PERCAKAPAN (untuk memahami rujukan saja, BUKAN sumber fakta)\n"
                "===================================================================\n"
                f"{history_block}"
            )
        sections.append(
            "KONTEKS\n"
            "=======\n"
            f"{self._render_context(passages)}"
        )
        language_name, instruction = self.ANSWER_LANGUAGES[self.supported_language(language)]
        if country:
            # The reader chose whose rules they are asking about; the context
            # holds only that country's documents, and the answer stays there.
            side = {
                "import": f" Barang dalam pertanyaan masuk ke {country}, jadi yang "
                          f"ditanyakan adalah ketentuan impor {country}.",
                "export": f" Barang dalam pertanyaan keluar dari {country}, jadi yang "
                          f"ditanyakan adalah ketentuan ekspor {country}.",
            }.get(trade_direction(question, country), "")
            sections.append(
                "NEGARA\n"
                "======\n"
                f"Pengguna bertanya tentang aturan {country}.{side} Jawab hanya tentang "
                f"ketentuan {country}; jangan menambahkan ketentuan negara lain. Bila "
                f"yang ditanyakan menyangkut negara lain, sebutkan bahwa jawabannya "
                f"perlu dicari dengan memilih negara itu."
            )
        sections.append(
            "PERTANYAAN\n"
            "==========\n"
            f"{question}\n\n"
            # Concrete numbers, not "[n]": placed last, a placeholder was copied
            # into an answer verbatim on 29 September 2026.
            f"Jawab sesuai aturan di atas, dengan nomor sumber sebagai sitasi, misalnya "
            f"[1] atau [2][3], dalam {language_name}. {instruction}"
        )
        return "\n\n".join(sections)

    def _render_history(self, history: list[ChatTurn]) -> str:
        """Render prior turns newest-last, within the history character budget."""
        if not history:
            return ""

        rendered: list[str] = []
        remaining = self._settings.maximum_history_characters
        # Walk backwards so that, when the budget runs out, it is the oldest
        # turns that are dropped rather than the most relevant recent ones.
        for turn in reversed(history):
            speaker = "Pengguna" if turn.is_question else "Asisten"
            body = " ".join(turn.content.split())
            if len(body) > 400:
                body = body[:400] + " ..."
            entry = f"{speaker}: {body}"
            if len(entry) > remaining:
                break
            rendered.insert(0, entry)
            remaining -= len(entry)
        return "\n".join(rendered)

    def _render_context(self, passages: list[RetrievedPassage]) -> str:
        """Render passages as numbered blocks within the character budget."""
        rendered_blocks: list[str] = []
        remaining_characters = self._settings.maximum_context_characters

        for passage in passages:
            block = self._render_passage(passage)
            if len(block) > remaining_characters:
                # Include a truncated tail of this passage rather than nothing,
                # unless there is no meaningful room left at all.
                if remaining_characters > 400:
                    rendered_blocks.append(block[:remaining_characters] + "\n[...dipotong...]")
                break
            rendered_blocks.append(block)
            remaining_characters -= len(block)

        return "\n\n".join(rendered_blocks)

    def _render_passage(self, passage: RetrievedPassage) -> str:
        """Render one passage with its citation number and provenance header."""
        ocr_marker = " (OCR)" if passage.produced_by_ocr else ""
        return (
            f"[{passage.citation_number}] {passage.document_name} — "
            f"{passage.section_label}{ocr_marker}\n"
            f"Lokasi: {passage.relative_path}\n"
            f"{passage.text}"
        )


class FollowUpQueryExpander:
    """Rewrites a short follow-up into something worth searching for.

    Retrieval sees only the current question, so a follow-up such as *"siapa
    saja mereka?"* carries almost no searchable signal and would return
    arbitrary passages. Prefixing the previous question restores the subject.

    The rule is deliberately conservative: it fires only on very short
    questions, because a fully-formed question is better retrieved on its own
    terms than diluted with an earlier, different topic.
    """

    def __init__(self, settings: Settings) -> None:
        """Bind the expander to the configured follow-up length threshold."""
        self._settings = settings

    def expand(self, question: str, history: list[ChatTurn]) -> str:
        """Return the query to search with, expanded only when warranted."""
        if not history:
            return question
        if len(question.split()) >= self._settings.follow_up_word_threshold:
            return question

        previous_question = self._find_previous_question(history)
        if not previous_question:
            return question
        return f"{previous_question} {question}"

    def _find_previous_question(self, history: list[ChatTurn]) -> str:
        """Return the most recent user question in the history, if any."""
        for turn in reversed(history):
            if turn.is_question and turn.content.strip():
                return " ".join(turn.content.split())
        return ""


class RagEngine:
    """Answers questions against the corpus, streaming results as they arrive.

    Citations are emitted *before* the first answer token, so the interface can
    render the evidence panel while the model is still writing. That ordering
    also means a user can start checking sources during generation, which is the
    slowest part of the request on a local model.
    """

    def __init__(
        self,
        settings: Settings,
        retriever: HybridRetriever,
        model_factory: LanguageModelFactory,
        conversation_store: ConversationStore | None = None,
        query_rewriter: CountryQueryRewriter | None = None,
    ) -> None:
        """Wire the engine to retrieval, the backend factory and chat storage.

        ``conversation_store`` is optional so the engine can be exercised — and
        used for one-off command-line questions — without persisting anything.
        Without ``query_rewriter``, questions are searched as typed.
        """
        self._settings = settings
        self._retriever = retriever
        self._model_factory = model_factory
        self._conversation_store = conversation_store
        self._query_rewriter = query_rewriter
        self._prompt_builder = PromptBuilder(settings)
        self._query_expander = FollowUpQueryExpander(settings)
        self._link_extractor = ExternalLinkExtractor()

    def stream_answer(self, request: QuestionRequest) -> Iterator[StreamEvent]:
        """Answer one question, yielding stream events in display order.

        The generator never raises into the web layer: a failure becomes an
        ``error`` event followed by ``done``, so the browser always sees a
        terminated stream and can re-enable its input box.
        """
        started_at = time.time()
        history = self._load_history(request.session_id)
        self._record_question(request)

        search_query = self._query_expander.expand(request.question, history)
        if search_query != request.question:
            LOGGER.info("Pertanyaan lanjutan diperluas untuk pencarian: %r", search_query)

        # One country's documents are searched in that country's language and
        # from its side of the trade: see app.query_rewriting.
        country = request.folder_filter
        restated_query = None
        if self._query_rewriter is not None and self._query_rewriter.supports(country):
            yield StreamEvent("status", {"message": self._prompt_builder.status_message(
                "restating", request.language,
                country=self._prompt_builder.country_name(country, request.language),
            )})
            rewritten = self._query_rewriter.rewrite(search_query, country)
            if rewritten != search_query:
                restated_query = search_query = rewritten

        try:
            passages = self._retriever.retrieve(
                search_query, request.folder_filter, request.passage_count
            )
        except Exception as error:
            LOGGER.exception("Pencarian gagal")
            message = self._prompt_builder.status_message(
                "search_failed", request.language, error=str(error)
            )
            yield StreamEvent("error", {"message": message})
            yield StreamEvent("done", {"elapsed_seconds": round(time.time() - started_at, 2)})
            return

        citations = self._build_citations(passages)
        citation_payload = [item.as_dictionary() for item in citations]
        sources_payload: dict = {"citations": citation_payload}
        if restated_query:
            # Shown beside the answer, so the reader can see what was searched.
            sources_payload.update({"search_query": restated_query, "country": country})
        yield StreamEvent("sources", sources_payload)

        if not passages:
            message = self._prompt_builder.empty_result_message(request.language)
            self._record_answer(request, message, [], "", "", time.time() - started_at)
            yield StreamEvent("token", {"text": message})
            yield StreamEvent(
                "done",
                {
                    "elapsed_seconds": round(time.time() - started_at, 2),
                    "passage_count": 0,
                    "backend_name": request.backend_name or self._settings.language_model_backend,
                    "session_id": request.session_id,
                },
            )
            return

        yield from self._stream_generated_answer(
            request, passages, citation_payload, history, started_at
        )

    def _stream_generated_answer(
        self,
        request: QuestionRequest,
        passages: list[RetrievedPassage],
        citation_payload: list[dict],
        history: list[ChatTurn],
        started_at: float,
    ) -> Iterator[StreamEvent]:
        """Run the language model, relay its output, and store the answer."""
        try:
            backend = self._model_factory.create(request.backend_name)
        except ValueError as error:
            yield StreamEvent("error", {"message": str(error)})
            yield StreamEvent("done", {"elapsed_seconds": round(time.time() - started_at, 2)})
            return

        system_prompt = self._prompt_builder.build_system_prompt(request.language)
        user_prompt = self._prompt_builder.build_user_prompt(
            request.question, passages, history, request.language, request.folder_filter
        )
        yield StreamEvent(
            "status",
            {
                "message": self._prompt_builder.status_message(
                    "composing", request.language,
                    backend=backend.name, model=backend.model_name,
                )
            },
        )

        token_count = 0
        answer_parts: list[str] = []
        try:
            for text_fragment in backend.generate_stream(system_prompt, user_prompt):
                if not text_fragment:
                    continue
                token_count += 1
                answer_parts.append(text_fragment)
                yield StreamEvent("token", {"text": text_fragment})
        except Exception as error:
            LOGGER.exception("Backend model bahasa gagal")
            message = self._prompt_builder.status_message(
                "model_failed", request.language, backend=backend.name, error=str(error)
            )
            yield StreamEvent("error", {"message": message})

        elapsed = time.time() - started_at
        # Stored even when generation failed part-way: a partial answer with its
        # sources is more use on resuming than a gap in the transcript.
        self._record_answer(
            request,
            "".join(answer_parts),
            citation_payload,
            backend.name,
            backend.model_name,
            elapsed,
        )

        yield StreamEvent(
            "done",
            {
                "elapsed_seconds": round(elapsed, 2),
                "passage_count": len(passages),
                "fragment_count": token_count,
                "backend_name": backend.name,
                "model_name": backend.model_name,
                "effort": getattr(backend, "effort", ""),
                "session_id": request.session_id,
            },
        )

    # --- Conversation persistence -------------------------------------------

    def _load_history(self, session_id: str | None) -> list[ChatTurn]:
        """Return prior turns of the session, or an empty list when unsaved."""
        if not session_id or self._conversation_store is None:
            return []
        return self._conversation_store.recent_turns(
            session_id, self._settings.conversation_history_turns
        )

    def _record_question(self, request: QuestionRequest) -> None:
        """Store the user's question and title the session if it is new."""
        if not request.session_id or self._conversation_store is None:
            return
        self._conversation_store.append_turn(
            request.session_id, ChatTurn(role="user", content=request.question)
        )
        self._conversation_store.ensure_titled(request.session_id, request.question)

    def _record_answer(
        self,
        request: QuestionRequest,
        answer_text: str,
        citations: list[dict],
        backend_name: str,
        model_name: str,
        elapsed_seconds: float,
    ) -> None:
        """Store the assistant's answer together with the sources behind it."""
        if not request.session_id or self._conversation_store is None:
            return
        self._conversation_store.append_turn(
            request.session_id,
            ChatTurn(
                role="assistant",
                content=answer_text,
                citations=citations,
                backend_name=backend_name,
                model_name=model_name,
                elapsed_seconds=elapsed_seconds,
            ),
        )

    def _build_citations(self, passages: list[RetrievedPassage]) -> list[AnswerCitation]:
        """Convert retrieved passages into the citation records shown in the UI."""
        return [
            AnswerCitation(
                citation_number=passage.citation_number,
                document_name=passage.document_name,
                relative_path=passage.relative_path,
                document_id=passage.document_id,
                section_label=passage.section_label,
                excerpt=self._build_excerpt(passage.text),
                produced_by_ocr=passage.produced_by_ocr,
                retrieval_methods=passage.retrieval_methods,
                relevance_score=passage.ranking_score,
                # Scanned from the full passage, not the shortened excerpt, so a
                # link is still offered when it falls outside the visible part.
                external_links=self._link_extractor.extract(passage.text),
            )
            for passage in passages
        ]

    def _build_excerpt(self, text: str, maximum_characters: int = 600) -> str:
        """Shorten passage text for the evidence card without cutting mid-word."""
        if len(text) <= maximum_characters:
            return text
        truncated = text[:maximum_characters]
        last_space = truncated.rfind(" ")
        if last_space > maximum_characters * 0.6:
            truncated = truncated[:last_space]
        return truncated + " ..."


#: Shared factory used by the server and the engine.
LANGUAGE_MODEL_FACTORY = LanguageModelFactory(SETTINGS)
