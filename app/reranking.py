"""Cross-encoder reranking of retrieved candidates.

Hybrid retrieval is tuned for recall: it deliberately returns more candidates
than the answer needs. :class:`CrossEncoderReranker` supplies the precision
half, scoring each ``(question, passage)`` pair jointly instead of comparing
two independently produced vectors. That catches passages that merely share
vocabulary with the question, which is the dominant failure mode when many
documents in a corpus use near-identical boilerplate.

The reranker runs on CPU by default. With the language model occupying roughly
5 GB of an 8 GB card, loading a second 1.1 GB model onto the GPU risks an
out-of-memory failure mid-answer, and 20-odd pairs score fast enough on a
20-core CPU to stay well inside the time a user waits for a response.
"""

from __future__ import annotations

import logging
import threading

from .config import Settings
from .documents import RetrievedPassage
from .embeddings import ComputeDeviceResolver

LOGGER = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Re-scores candidate passages against the question, most relevant first.

    Disabled or failed loading is not fatal: :meth:`rerank` then returns the
    candidates in their original fused order, so retrieval still works without
    the reranking model present.
    """

    def __init__(
        self, settings: Settings, device_resolver: ComputeDeviceResolver | None = None
    ) -> None:
        """Record configuration without loading the cross-encoder."""
        self._settings = settings
        self._device_resolver = device_resolver or ComputeDeviceResolver()
        self._model = None
        self._load_attempted = False
        self._load_lock = threading.Lock()
        #: Two visitors asking at once would share the model's fast tokenizer,
        #: which fails with "Already borrowed" when used from two threads.
        self._use_lock = threading.Lock()
        self._unavailable_reason: str | None = None

    @property
    def is_enabled(self) -> bool:
        """True when reranking is switched on in configuration."""
        return self._settings.reranking_enabled

    @property
    def is_loaded(self) -> bool:
        """True once the cross-encoder weights are resident in memory."""
        return self._model is not None

    @property
    def unavailable_reason(self) -> str | None:
        """Why reranking is not running, or None when it is."""
        if not self._settings.reranking_enabled:
            return "Reranking dinonaktifkan (RERANK_ENABLED=0)"
        return self._unavailable_reason

    def load(self) -> None:
        """Load the cross-encoder once, recording failure rather than raising.

        A question arriving while another is still loading the model waits
        for it. Returning at once, as this did until 29 September 2026, sent
        that question on unranked: two visitors asking just after a restart
        meant one of them got passages in raw fusion order.
        """
        if self._model is not None or self._unavailable_reason is not None:
            return
        with self._load_lock:
            if self._model is not None or self._load_attempted:
                return
            self._load_attempted = True
            try:
                device = self._device_resolver.resolve(self._settings.reranking_device)
                LOGGER.info(
                    "Memuat model reranking %s pada %s",
                    self._settings.reranking_model_name,
                    device,
                )
                self._model = self._build_model(device)
            except Exception as error:
                self._unavailable_reason = f"Gagal memuat model reranking: {error}"
                LOGGER.warning(self._unavailable_reason)

    def _build_model(self, device: str):
        """Create the cross-encoder on ``device``."""
        from sentence_transformers import CrossEncoder

        return CrossEncoder(
            self._settings.reranking_model_name,
            device=device,
            max_length=512,
            cache_folder=str(self._settings.model_cache_directory),
        )

    def rerank(
        self, question: str, candidates: list[RetrievedPassage], keep_count: int
    ) -> list[RetrievedPassage]:
        """Return the ``keep_count`` candidates most relevant to ``question``.

        Each returned passage carries its cross-encoder score in
        :attr:`RetrievedPassage.reranked_score`. When reranking is unavailable,
        the original fused ordering is preserved and scores stay unset.
        """
        if not candidates:
            return []
        if not self._settings.reranking_enabled:
            return candidates[:keep_count]

        self.load()
        if self._model is None:
            return candidates[:keep_count]

        try:
            question_passage_pairs = [
                (question, candidate.text) for candidate in candidates
            ]
            with self._use_lock:
                scores = self._model.predict(
                    question_passage_pairs, show_progress_bar=False, batch_size=8
                )
        except Exception as error:
            LOGGER.warning("Reranking gagal, memakai urutan awal: %s", error)
            return candidates[:keep_count]

        for candidate, score in zip(candidates, scores):
            candidate.reranked_score = float(score)
        candidates.sort(key=self._sort_key, reverse=True)
        return candidates[:keep_count]

    def _sort_key(self, passage: RetrievedPassage) -> float:
        """Ordering key for reranked results: highest cross-encoder score first."""
        return passage.reranked_score if passage.reranked_score is not None else 0.0
