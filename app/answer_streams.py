"""Answer generation that outlives the HTTP connection which requested it.

A phone loses connections far more readily than a desktop: the screen locks,
the browser is sent to the background, or Wi-Fi power saving drops a link that
has been quiet for a while. Quiet stretches are normal here — retrieval takes
several seconds, and a command-line agent at maximum effort can think for
minutes before its first word. When the answer was generated *inside* the HTTP
response, a dropped connection abandoned the generation with it: the reader saw
"network error" after the sources had already arrived, and the answer was never
saved to the conversation.

Each answer now runs as an :class:`AnswerJob` on its own thread and keeps a log
of every event it produces. HTTP responses are only readers of that log. A
reader that loses its connection reconnects and asks for the events after the
last one it saw, and the answer is completed and saved whether or not anyone is
still listening.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Iterator

from .rag import StreamEvent

LOGGER = logging.getLogger(__name__)


class ServerSentEventEncoder:
    """Formats stream events as Server-Sent Event frames."""

    def encode(self, event: StreamEvent, event_number: int | None = None) -> str:
        """Return one SSE frame for ``event``, numbered when ``event_number`` is given.

        The number goes in the standard ``id:`` field, which is what a reader
        hands back to resume from exactly this point.
        """
        payload_json = json.dumps(event.payload, ensure_ascii=False)
        identifier_line = f"id: {event_number}\n" if event_number is not None else ""
        return f"{identifier_line}event: {event.name}\ndata: {payload_json}\n\n"

    def encode_heartbeat(self) -> str:
        """Return a comment frame, which SSE readers ignore by definition.

        Sent during long silences so the connection never looks idle to a phone's
        power management or to anything else between browser and server.
        """
        return ": keep-alive\n\n"


class AnswerJob:
    """One answer being generated in the background, with its full event log.

    Events are numbered from 1 in the order they were produced. A reader passes
    the last number it received to :meth:`follow` and gets everything after it,
    which is what lets a reconnecting browser carry on without repeating text.
    """

    #: Shown when the reader asked for generation to stop.
    CANCELLED_MESSAGE = "Dihentikan oleh pengguna."

    def __init__(self, answer_id: str, source_events: Iterator[StreamEvent]) -> None:
        """Prepare a job that will relay ``source_events`` once started.

        ``source_events`` is normally the engine's answer generator, which does
        no work until iterated — so nothing runs on the caller's thread.
        """
        self.answer_id = answer_id
        self._source_events = source_events
        self._event_log: list[StreamEvent] = []
        self._condition = threading.Condition()
        self._cancel_requested = threading.Event()
        self._started_at = time.time()
        self._finished_at: float | None = None
        self._thread = threading.Thread(
            target=self._run, name=f"jawaban-{answer_id[:8]}", daemon=True
        )

    @property
    def is_finished(self) -> bool:
        """True once the last event has been logged."""
        with self._condition:
            return self._finished_at is not None

    @property
    def finished_at(self) -> float | None:
        """When the job finished, as a Unix timestamp, or None while running."""
        with self._condition:
            return self._finished_at

    @property
    def event_count(self) -> int:
        """How many events have been logged so far."""
        with self._condition:
            return len(self._event_log)

    def start(self) -> None:
        """Begin generating on the job's own thread."""
        self._thread.start()

    def wait_until_finished(self, timeout_seconds: float | None = None) -> bool:
        """Block until the job finishes; returns False if the timeout expired."""
        self._thread.join(timeout_seconds)
        return not self._thread.is_alive()

    def request_cancel(self) -> None:
        """Ask the job to stop generating.

        Honoured when the next event arrives, because a backend blocked waiting
        for its model cannot be interrupted mid-read. Closing the generator at
        that point is what ends a command-line agent's subprocess.
        """
        self._cancel_requested.set()

    def follow(
        self, after_event_number: int, heartbeat_seconds: float
    ) -> Iterator[tuple[int, StreamEvent] | None]:
        """Yield ``(event_number, event)`` for every event after the one given.

        Waits for new events while the job runs. ``None`` is yielded whenever
        ``heartbeat_seconds`` pass with nothing new, so the caller can send
        something down an otherwise silent connection. Ends once the job has
        finished and every logged event has been delivered.
        """
        delivered_count = max(0, after_event_number)
        while True:
            with self._condition:
                if delivered_count >= len(self._event_log) and self._finished_at is None:
                    self._condition.wait(timeout=heartbeat_seconds)
                pending_events = self._event_log[delivered_count:]
                has_finished = self._finished_at is not None

            # Pending events are delivered before the finished check, so the
            # final events of a job that has just ended are never skipped.
            if pending_events:
                for offset, event in enumerate(pending_events, start=1):
                    yield delivered_count + offset, event
                delivered_count += len(pending_events)
            elif has_finished:
                return
            else:
                yield None

    def _run(self) -> None:
        """Relay source events into the log until they end or a cancel arrives."""
        try:
            for event in self._source_events:
                self._append(event)
                if self._cancel_requested.is_set():
                    break
        except Exception as error:
            # The engine turns its own failures into events; this guards
            # against anything it did not foresee, so readers still get an end.
            LOGGER.exception("Pembuatan jawaban %s gagal", self.answer_id)
            self._append(StreamEvent("error", {"message": f"Terjadi kesalahan: {error}"}))
        finally:
            self._close_source()
            self._ensure_terminated()
            with self._condition:
                self._finished_at = time.time()
                self._condition.notify_all()

    def _close_source(self) -> None:
        """Close the source generator, releasing whatever it holds open."""
        close_source = getattr(self._source_events, "close", None)
        if close_source is None:
            return
        try:
            close_source()
        except Exception:
            LOGGER.exception("Gagal menutup aliran jawaban %s", self.answer_id)

    def _ensure_terminated(self) -> None:
        """Guarantee the log ends with ``done``, which is how readers know to stop."""
        with self._condition:
            last_event = self._event_log[-1] if self._event_log else None
        if last_event is not None and last_event.name == "done":
            return
        if self._cancel_requested.is_set():
            self._append(StreamEvent("error", {"message": self.CANCELLED_MESSAGE}))
        self._append(
            StreamEvent("done", {"elapsed_seconds": round(time.time() - self._started_at, 2)})
        )

    def _append(self, event: StreamEvent) -> None:
        """Log one event and wake every reader waiting for it."""
        with self._condition:
            self._event_log.append(event)
            self._condition.notify_all()


class AnswerJobRegistry:
    """Keeps recent answer jobs so a reader that lost its connection can find one.

    Answer identifiers are long random tokens handed only to the reader that
    asked the question, so knowing one is what grants access to its events.
    """

    def __init__(self, retention_seconds: float = 1800.0, maximum_retained: int = 50) -> None:
        """Set how long finished answers are kept, and how many at most."""
        self._retention_seconds = retention_seconds
        self._maximum_retained = maximum_retained
        self._jobs: dict[str, AnswerJob] = {}
        self._lock = threading.Lock()

    def start(self, source_events: Iterator[StreamEvent]) -> AnswerJob:
        """Register a job for ``source_events`` and start generating."""
        job = AnswerJob(secrets.token_urlsafe(18), source_events)
        with self._lock:
            self._discard_stale_jobs()
            self._jobs[job.answer_id] = job
        job.start()
        return job

    def find(self, answer_id: str) -> AnswerJob | None:
        """Return the job with ``answer_id``, or None if unknown or discarded."""
        with self._lock:
            return self._jobs.get(answer_id)

    def _discard_stale_jobs(self) -> None:
        """Forget finished jobs past retention, then the oldest beyond the cap.

        Running jobs are never discarded: their readers may still reconnect,
        and dropping one would not stop its thread anyway.
        """
        now = time.time()
        for answer_id, job in list(self._jobs.items()):
            finished_at = job.finished_at
            if finished_at is not None and now - finished_at > self._retention_seconds:
                del self._jobs[answer_id]

        # Dictionaries keep insertion order, so the first finished jobs are the oldest.
        excess_count = len(self._jobs) - self._maximum_retained + 1
        if excess_count <= 0:
            return
        oldest_finished = [answer_id for answer_id, job in self._jobs.items() if job.is_finished]
        for answer_id in oldest_finished[:excess_count]:
            del self._jobs[answer_id]


class AnswerFrameStreamer:
    """Turns one answer job into the SSE frames a single reader receives."""

    #: Name of the frame that tells a new reader which answer it is following.
    ACCEPTED_EVENT_NAME = "accepted"

    def __init__(self, encoder: ServerSentEventEncoder, heartbeat_seconds: float) -> None:
        """Bind the streamer to a frame encoder and a keep-alive interval."""
        self._encoder = encoder
        self._heartbeat_seconds = heartbeat_seconds

    def iterate_frames(
        self, job: AnswerJob, after_event_number: int = 0, announce: bool = False
    ) -> Iterator[str]:
        """Yield frames for the job's events after ``after_event_number``.

        A synchronous generator on purpose: Starlette runs it in a worker
        thread, where waiting for the next event blocks nothing else.

        ``announce`` prefixes an unnumbered ``accepted`` frame carrying the
        answer identifier; it is sent once, to the reader that asked the
        question, and never replayed to a reader that is resuming.
        """
        if announce:
            yield self._encoder.encode(
                StreamEvent(self.ACCEPTED_EVENT_NAME, {"answer_id": job.answer_id})
            )
        for delivery in job.follow(after_event_number, self._heartbeat_seconds):
            if delivery is None:
                yield self._encoder.encode_heartbeat()
                continue
            event_number, event = delivery
            yield self._encoder.encode(event, event_number)
