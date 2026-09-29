"""Tests for the reranker's first load, when two questions arrive together.

Until 29 September 2026 a question arriving while another was still loading
the model skipped reranking and was answered from raw fusion order. A slow
stand-in model shows whether the second question now waits.
"""

from __future__ import annotations

import threading
import time

from app.documents import RetrievedPassage
from app.reranking import CrossEncoderReranker


class _SlowModel:
    """Stands in for the cross-encoder: favours the passage mentioning coffee."""

    def predict(self, pairs, **options):
        return [0.9 if "kopi" in passage else 0.1 for _question, passage in pairs]


class _SlowLoadingReranker(CrossEncoderReranker):
    """Takes a while to load, as the real model does on first use."""

    def _build_model(self, device: str):
        time.sleep(0.3)
        return _SlowModel()


def _passages() -> list[RetrievedPassage]:
    return [
        RetrievedPassage(
            chunk_id=index, document_id=f"d{index}", document_name=f"d{index}.txt",
            relative_path=f"Indonesia/d{index}.txt", absolute_path=f"C:/corpus/d{index}.txt",
            section_label="Halaman 1", text=text,
        )
        for index, text in enumerate(["bea masuk umum", "ekspor kopi"], start=1)
    ]


def test_a_question_during_the_first_load_waits_and_is_reranked(isolated_settings):
    isolated_settings.reranking_enabled = True
    reranker = _SlowLoadingReranker(isolated_settings)
    first = threading.Thread(target=reranker.load)
    first.start()
    time.sleep(0.05)  # the first load is under way

    ranked = reranker.rerank("syarat ekspor kopi", _passages(), keep_count=2)
    first.join()

    assert ranked[0].text == "ekspor kopi"
    assert ranked[0].reranked_score == 0.9
