"""Tests for restating a question for the country whose rules it asks about.

The local model is replaced by a recorder, so nothing here needs Ollama.
"""

from __future__ import annotations

import pytest

from app.query_rewriting import CountryQueryRewriter, keep_direction, trade_direction


class _RecordingModel:
    """Stands in for the local model: records each request, returns a set reply."""

    def __init__(self, reply: str = "Quy định nhập khẩu cà phê vào Việt Nam", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.requests: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]]) -> str:
        self.requests.append(messages)
        if self.error is not None:
            raise self.error
        return self.reply


def test_restates_the_question_for_the_country(isolated_settings):
    model = _RecordingModel()
    restated = CountryQueryRewriter(isolated_settings, complete=model).rewrite(
        "Syarat ekspor kopi ke Vietnam?", "Vietnam"
    )
    assert restated == "Quy định nhập khẩu cà phê vào Việt Nam"
    request = model.requests[0][-1]["content"]
    assert "Country: Vietnam" in request and "Vietnamese" in request
    assert "Syarat ekspor kopi ke Vietnam?" in request


def test_shows_the_model_worked_examples_before_the_question(isolated_settings):
    model = _RecordingModel()
    CountryQueryRewriter(isolated_settings, complete=model).rewrite("Pertanyaan?", "Indonesia")
    roles = [message["role"] for message in model.requests[0]]
    assert roles == ["system"] + ["user", "assistant"] * len(CountryQueryRewriter.EXAMPLES) + ["user"]


def test_keeps_only_the_query_line(isolated_settings):
    model = _RecordingModel(reply='<think>…</think>\nQuery: "Ketentuan ekspor kopi dari Indonesia"\nKarena …')
    restated = CountryQueryRewriter(isolated_settings, complete=model).rewrite("Pertanyaan?", "Indonesia")
    assert restated == "Ketentuan ekspor kopi dari Indonesia"


def test_a_failure_searches_the_question_as_typed(isolated_settings):
    model = _RecordingModel(error=TimeoutError("model busy"))
    rewriter = CountryQueryRewriter(isolated_settings, complete=model)
    assert rewriter.rewrite("Syarat ekspor kopi", "Vietnam") == "Syarat ekspor kopi"


def test_an_empty_reply_searches_the_question_as_typed(isolated_settings):
    rewriter = CountryQueryRewriter(isolated_settings, complete=_RecordingModel(reply="  \n"))
    assert rewriter.rewrite("Syarat ekspor kopi", "Vietnam") == "Syarat ekspor kopi"


def test_asks_the_model_once_for_a_repeated_question(isolated_settings):
    model = _RecordingModel()
    rewriter = CountryQueryRewriter(isolated_settings, complete=model)
    rewriter.rewrite("Syarat ekspor kopi", "Vietnam")
    rewriter.rewrite("Syarat ekspor kopi", "Vietnam")
    rewriter.rewrite("Syarat ekspor kopi", "Indonesia")
    assert len(model.requests) == 2


def test_restates_only_for_countries_whose_document_language_is_known(isolated_settings):
    rewriter = CountryQueryRewriter(isolated_settings, complete=_RecordingModel())
    assert rewriter.supports("Indonesia") and rewriter.supports("Vietnam")
    assert not rewriter.supports(None) and not rewriter.supports("Filipina")


def test_can_be_switched_off(isolated_settings):
    isolated_settings.query_rewrite_enabled = False
    assert not CountryQueryRewriter(isolated_settings, complete=_RecordingModel()).supports("Vietnam")


@pytest.mark.parametrize(
    ("question", "country", "direction"),
    [
        ("Apa syarat ekspor kopi ke Vietnam?", "Vietnam", "import"),
        ("Apa syarat ekspor kopi ke Vietnam?", "Indonesia", None),
        ("Saya orang Vietnam, ingin impor kopi dari Indonesia", "Indonesia", "export"),
        ("Export coffee from Indonesia to Vietnam", "Vietnam", "import"),
        ("Export coffee from Indonesia to Vietnam", "Indonesia", "export"),
        ("Xuất khẩu cà phê sang Việt Nam cần gì?", "Vietnam", "import"),
        ("Nhập khẩu văn phòng phẩm từ Indonesia", "Indonesia", "export"),
        ("What does Vietnam require to import coffee?", "Vietnam", None),
        ("Apa itu PIB?", "Indonesia", None),
    ],
)
def test_reads_which_side_of_the_trade_a_country_is_on(question, country, direction):
    assert trade_direction(question, country) == direction


@pytest.mark.parametrize(
    ("query", "country", "direction", "expected"),
    [
        ("Điều kiện xuất khẩu cà phê sang Việt Nam", "Vietnam", "import", "Điều kiện nhập khẩu cà phê vào Việt Nam"),
        ("Xuất khẩu cà phê", "Vietnam", "import", "Nhập khẩu cà phê"),
        ("Ketentuan impor kopi dari Indonesia oleh importir", "Indonesia", "export",
         "Ketentuan ekspor kopi dari Indonesia oleh eksportir"),
        ("Ketentuan ekspor kopi dari Indonesia", "Indonesia", "export", "Ketentuan ekspor kopi dari Indonesia"),
        ("Quy định nhập khẩu cà phê", "Vietnam", None, "Quy định nhập khẩu cà phê"),
    ],
)
def test_the_known_direction_overrides_the_models_verb(query, country, direction, expected):
    assert keep_direction(query, country, direction) == expected


def test_imports_into_vietnam_read_as_vietnamese_writes_them():
    restated = keep_direction("Điều kiện xuất khẩu cà phê sang Việt Nam", "Vietnam", "import")
    assert restated == "Điều kiện nhập khẩu cà phê vào Việt Nam"


@pytest.mark.parametrize(
    "reply",
    ["Không, nhập khẩu sang Việt Nam không thể miễn phí.", "Ya, bisa dengan Form D.", "No. It depends."],
)
def test_an_answer_instead_of_a_query_searches_the_question_as_typed(isolated_settings, reply):
    rewriter = CountryQueryRewriter(isolated_settings, complete=_RecordingModel(reply=reply))
    assert rewriter.rewrite("Apakah impor ke Vietnam bisa bebas bea masuk?", "Vietnam") == (
        "Apakah impor ke Vietnam bisa bebas bea masuk?"
    )


def test_a_query_may_start_with_a_negative_word(isolated_settings):
    reply = "Không chịu thuế nhập khẩu đối với hàng hóa tạm nhập tái xuất"
    rewriter = CountryQueryRewriter(isolated_settings, complete=_RecordingModel(reply=reply))
    assert rewriter.rewrite("Barang apa yang tidak kena bea masuk?", "Vietnam") == reply


def test_a_restatement_keeps_the_side_of_the_trade(isolated_settings):
    model = _RecordingModel(reply="Điều kiện xuất khẩu cà phê sang Việt Nam: thủ tục hải quan")
    restated = CountryQueryRewriter(isolated_settings, complete=model).rewrite(
        "Apa syarat ekspor kopi ke Vietnam?", "Vietnam"
    )
    assert restated.startswith("Điều kiện nhập khẩu cà phê vào Việt Nam")


def test_tells_the_model_the_direction_it_must_follow(isolated_settings):
    model = _RecordingModel()
    CountryQueryRewriter(isolated_settings, complete=model).rewrite("Apa syarat ekspor kopi ke Vietnam?", "Vietnam")
    assert "Vietnam's rules for importing apply" in model.requests[0][-1]["content"]
