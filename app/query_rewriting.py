"""Restating a question for the country whose rules it asks about.

The corpus is divided by country, and each country's documents are written in
its own language. A question typed in another language, or put from the other
side of the border, finds them poorly. Searched as typed, "syarat ekspor kopi
ke Vietnam" pulls Vietnam's rules for *exporting* coffee, when Vietnam's side
of that trade is the import. So before one country's documents are searched,
the local language model restates the question as that country's rules would
put it: in that country's language, with the official terms they use.

Measured on 29 September 2026, as the reranker score of the best passage in
the Vietnamese documents: the Indonesian question as typed scored 0.13, the
same question restated in Vietnamese 0.998.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from typing import Callable

from .config import SETTINGS, Settings

LOGGER = logging.getLogger(__name__)

#: The language each country's documents are written in, by corpus folder.
DOCUMENT_LANGUAGES = {
    "Indonesia": "Indonesian (Bahasa Indonesia)",
    "Vietnam": "Vietnamese (tiếng Việt)",
}

#: How each country is written in the three interface languages.
COUNTRY_NAME_PATTERNS = {
    "Indonesia": r"indonesia",
    "Vietnam": r"vi[eệ]t\s*nam",
}

#: Words putting goods on their way to a country, or away from it, in
#: Indonesian, English and Vietnamese: "ke Vietnam", "to Vietnam",
#: "sang Việt Nam"; "dari Indonesia", "from Indonesia", "từ Indonesia".
_TOWARD_WORDS = r"ke|kepada|menuju|to|into|sang|vào|tới|đến"
_AWAY_WORDS = r"dari|from|out of|từ"

#: The trade verbs of each country's document language, as (import, export).
_TRADE_VERBS = {
    "Indonesia": ("impor", "ekspor"),
    "Vietnam": ("nhập khẩu", "xuất khẩu"),
}

#: Sends chat messages to a model and returns its reply.
Completion = Callable[[list[dict[str, str]]], str]


def keep_direction(query: str, country: str, direction: str | None) -> str:
    """Return ``query`` with its trade verb on ``country``'s side of the trade.

    The local model translates well but keeps the question's own verb even
    when told otherwise: asked about Vietnam's side of "ekspor kopi ke
    Vietnam", it wrote "xuất khẩu cà phê sang Việt Nam". The direction is
    already known from the question's wording, so the verb is set here:
    "xuất khẩu" becomes "nhập khẩu", "impor" becomes "ekspor", and
    "importir" becomes "eksportir" with it.
    """
    verbs = _TRADE_VERBS.get(country)
    if direction is None or verbs is None:
        return query
    import_verb, export_verb = verbs
    wrong, right = (export_verb, import_verb) if direction == "import" else (import_verb, export_verb)

    def replace(match: re.Match) -> str:
        return right.capitalize() if match.group(0)[0].isupper() else right

    query = re.sub(rf"\b{wrong}", replace, query, flags=re.IGNORECASE)
    if country == "Vietnam" and direction == "import":
        # Goods are imported "vào Việt Nam"; "sang" only goes with exports.
        query = re.sub(r"\bsang\s+(?=vi[eệ]t\s*nam)", "vào ", query, flags=re.IGNORECASE)
    return query


def trade_direction(question: str, country: str) -> str | None:
    """Return "import" or "export": which of ``country``'s rules the question needs.

    Read from the words around the country's name rather than left to the
    model, which on 29 September 2026 restated "ekspor kopi ke Vietnam" as
    Vietnam's rules for *exporting* coffee. Goods going to a country are its
    imports; goods coming from it are its exports. None when the question
    says neither, or both.
    """
    name = COUNTRY_NAME_PATTERNS.get(country)
    if name is None:
        return None
    text = question.lower()
    toward = re.search(rf"\b(?:{_TOWARD_WORDS})\s+(?:negara\s+|nước\s+)?(?:{name})\b", text)
    away = re.search(rf"\b(?:{_AWAY_WORDS})\s+(?:negara\s+|nước\s+)?(?:{name})\b", text)
    if toward and not away:
        return "import"
    if away and not toward:
        return "export"
    return None


class CountryQueryRewriter:
    """Restates a question as a search of one country's regulations."""

    SYSTEM_PROMPT = (
        "You turn a question about export and import rules into a search query "
        "for the official regulations of one country.\n"
        "- Write the query in that country's language.\n"
        "- Put it from that country's side: goods sent to the country are imports "
        "into it; goods sent from it are exports from it. When a Direction line "
        "is given, follow it exactly.\n"
        "- Add the official terms that country's regulations use for the "
        "procedures, documents, permits and taxes involved.\n"
        "- Keep numbers, HS codes, regulation numbers and names exactly as given, "
        "and never add any the question does not contain.\n"
        "- Never answer the question, not even with yes or no: only restate it.\n"
        "Reply with the query only: one or two sentences, no explanation."
    )

    #: Worked examples: a small model follows a shown pattern more reliably
    #: than a described one.
    EXAMPLES = (
        (
            "Vietnam",
            "Syarat ekspor kopi ke Vietnam apa saja?",
            "Quy định nhập khẩu cà phê vào Việt Nam: thủ tục hải quan, thuế nhập khẩu, "
            "kiểm dịch thực vật, an toàn thực phẩm",
        ),
        (
            "Indonesia",
            "What documents do I need to export coffee from Indonesia?",
            "Dokumen dan ketentuan ekspor kopi dari Indonesia: pemberitahuan ekspor "
            "barang (PEB), surat keterangan asal, sertifikat karantina tumbuhan",
        ),
        # A yes-or-no question is restated too, never answered.
        (
            "Vietnam",
            "Apakah impor ke Vietnam bisa bebas bea masuk?",
            "Miễn thuế nhập khẩu đối với hàng hóa nhập khẩu vào Việt Nam: các trường "
            "hợp được miễn thuế, thuế suất ưu đãi đặc biệt ATIGA",
        ),
    )

    #: A reply opening with a bare yes or no ("Không, …", "Ya, …") answers the
    #: question instead of restating it. The punctuation matters: a query may
    #: well start "Không chịu thuế…" (not taxable).
    _ANSWER_OPENING = re.compile(
        r"^(?:yes|no|ya|tidak|bukan|bisa|có|không|được)\s*[,.:;!]", re.IGNORECASE
    )

    #: Restatements kept, so asking the same question again costs no model call.
    CACHE_SIZE = 256

    def __init__(self, settings: Settings, complete: Completion | None = None) -> None:
        """Bind the rewriter to the local model, or to ``complete`` in tests."""
        self._settings = settings
        self._complete = complete or self._complete_with_ollama
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._cache_lock = threading.Lock()

    def supports(self, country: str | None) -> bool:
        """True when ``country`` has documents in a known language to restate for."""
        return bool(self._settings.query_rewrite_enabled and country in DOCUMENT_LANGUAGES)

    def rewrite(self, question: str, country: str) -> str:
        """Return ``question`` restated for ``country``'s regulations.

        Any failure — Ollama down, a timeout, an empty reply — returns the
        question unchanged: searching as typed finds less, but never nothing.
        """
        key = (country, question)
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        try:
            reply = self._complete(self._messages(question, country))
        except Exception as error:
            LOGGER.warning("Pertanyaan tidak dapat dirumuskan ulang untuk %s: %s", country, error)
            return question
        restated = keep_direction(self._clean(reply), country, trade_direction(question, country))
        if not restated:
            return question
        LOGGER.info("Pertanyaan untuk %s dirumuskan ulang: %r", country, restated)
        with self._cache_lock:
            self._cache[key] = restated
            while len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)
        return restated

    def _messages(self, question: str, country: str) -> list[dict[str, str]]:
        """Return the chat: instructions, the worked examples, then the question."""
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]
        for example_country, example_question, example_query in self.EXAMPLES:
            messages.append({"role": "user", "content": self._request(example_question, example_country)})
            messages.append({"role": "assistant", "content": example_query})
        messages.append({"role": "user", "content": self._request(question, country)})
        return messages

    @staticmethod
    def _request(question: str, country: str) -> str:
        """Return one request as the model sees it, with the direction when known."""
        lines = [f"Country: {country}", f"Language: {DOCUMENT_LANGUAGES[country]}"]
        direction = trade_direction(question, country)
        if direction == "import":
            lines.append(f"Direction: the goods go to {country}, so {country}'s rules for importing apply.")
        elif direction == "export":
            lines.append(f"Direction: the goods leave {country}, so {country}'s rules for exporting apply.")
        lines.append(f"Question: {question}")
        return "\n".join(lines)

    @classmethod
    def _clean(cls, reply: str) -> str:
        """Return the query line of a reply, without reasoning, a label, or quotes.

        An empty string — the question is then searched as typed — when the
        model answered instead: on 29 September 2026 it met a yes-or-no
        question with "Không, nhập khẩu sang Việt Nam không thể miễn phí…".
        """
        text = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.DOTALL | re.IGNORECASE)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        query = re.sub(r"^(query|search query|kueri|truy vấn)\s*:\s*", "", lines[0], flags=re.IGNORECASE)
        query = query.strip(" \"'“”«»")[:500]
        return "" if cls._ANSWER_OPENING.match(query) else query

    def _complete_with_ollama(self, messages: list[dict[str, str]]) -> str:
        """Ask the local Ollama model, with the answer model's own settings.

        The context window matches the answers' on purpose: Ollama reloads a
        model whose options change, and alternating would reload it twice
        for every question.
        """
        import requests

        response = requests.post(
            f"{self._settings.ollama_base_url}/api/chat",
            json={
                "model": self._settings.ollama_model_name,
                "messages": messages,
                "stream": False,
                "think": False,
                "keep_alive": self._settings.ollama_keep_alive,
                "options": {
                    "temperature": 0,
                    "num_ctx": self._settings.ollama_context_window,
                    "num_predict": 160,
                },
            },
            timeout=(10, self._settings.query_rewrite_timeout_seconds),
        )
        response.raise_for_status()
        return (response.json().get("message") or {}).get("content", "")


#: Shared rewriter used by the server and the command line.
QUERY_REWRITER = CountryQueryRewriter(SETTINGS)
