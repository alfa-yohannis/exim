"""Tests for the embedder's sharing of one model between indexing and questions.

The model's fast tokenizer fails with "Already borrowed" when two threads use
it at once, which happens whenever a question arrives while the server indexes
new files. A stand-in model records any overlap instead of failing.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from app.embeddings import TextEmbedder


class _OverlapRecordingModel:
    """Stands in for SentenceTransformer and its tokenizer; notes concurrent use."""

    def __init__(self) -> None:
        self.tokenizer = self
        self.overlapped = False
        self._active = 0
        self._counter_lock = threading.Lock()

    def encode(self, texts, **options):
        with self._counter_lock:
            self._active += 1
            self.overlapped = self.overlapped or self._active > 1
        time.sleep(0.005)
        with self._counter_lock:
            self._active -= 1
        if options.get("add_special_tokens") is False:  # used as the tokenizer
            return [1, 2, 3]
        return np.zeros((len(texts), 4), dtype=np.float32)

    def get_sentence_embedding_dimension(self) -> int:
        return 4


def test_indexing_questions_and_token_counting_take_turns(isolated_settings):
    embedder = TextEmbedder(isolated_settings)
    model = _OverlapRecordingModel()
    embedder._model = model
    counter = embedder.build_token_counter()

    workers = [
        threading.Thread(target=lambda: [embedder.encode_passages(["teks"] * 4) for _ in range(10)]),
        threading.Thread(target=lambda: [embedder.encode_question("pertanyaan") for _ in range(10)]),
        threading.Thread(target=lambda: [counter.count("potongan") for _ in range(10)]),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert not model.overlapped
