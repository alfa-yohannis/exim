"""Tests for answer jobs: generation that survives the reader's connection.

The source events are scripted, and a gate stands in for a slow model, so each
test controls exactly when the "model" produces its next event.
"""

from __future__ import annotations

import threading
from typing import Iterator

from app.answer_streams import (
    AnswerFrameStreamer,
    AnswerJob,
    AnswerJobRegistry,
    ServerSentEventEncoder,
)
from app.rag import StreamEvent

#: Long enough for a loaded test machine, short enough that a hang fails fast.
WAIT_SECONDS = 5.0

#: Heartbeat interval for tests, so waiting for silence takes milliseconds.
QUICK_HEARTBEAT_SECONDS = 0.02


class GatedEventSource:
    """Emits some events, pauses at a gate as a slow model would, then the rest."""

    def __init__(
        self, events_before_gate: list[StreamEvent], events_after_gate: list[StreamEvent]
    ) -> None:
        """Hold the events to emit on either side of the gate."""
        self._events_before_gate = events_before_gate
        self._events_after_gate = events_after_gate
        self.gate = threading.Event()
        self.reached_gate = threading.Event()
        self.ran_to_completion = False
        self.was_released = False

    def stream(self) -> Iterator[StreamEvent]:
        """Yield the scripted events, recording whether the stream was cut short."""
        try:
            yield from self._events_before_gate
            self.reached_gate.set()
            self.gate.wait(WAIT_SECONDS)
            yield from self._events_after_gate
            self.ran_to_completion = True
        finally:
            self.was_released = True


class FailingEventSource:
    """Emits one event, then raises as an unforeseen bug would."""

    def stream(self) -> Iterator[StreamEvent]:
        """Yield the sources, then fail."""
        yield StreamEvent("sources", {"citations": []})
        raise RuntimeError("kesalahan tak terduga")


def scripted_answer() -> list[StreamEvent]:
    """A complete answer as the engine would stream it."""
    return [
        StreamEvent("sources", {"citations": [{"citation_number": 1}]}),
        StreamEvent("status", {"message": "Menyusun jawaban..."}),
        StreamEvent("token", {"text": "Rata-rata EWMP "}),
        StreamEvent("token", {"text": "adalah 12,5 SKS [1]."}),
        StreamEvent("done", {"elapsed_seconds": 1.5}),
    ]


def run_to_completion(source_events) -> AnswerJob:
    """Start a job over ``source_events`` and wait for it to finish."""
    job = AnswerJob("uji", iter(source_events))
    job.start()
    assert job.wait_until_finished(WAIT_SECONDS)
    return job


def logged_events(job: AnswerJob, after_event_number: int = 0) -> list[tuple[int, StreamEvent]]:
    """Every delivery a reader of a finished job receives."""
    return [
        delivery
        for delivery in job.follow(after_event_number, QUICK_HEARTBEAT_SECONDS)
        if delivery is not None
    ]


def logged_event_names(job: AnswerJob) -> list[str]:
    """The names of every event a finished job logged, in order."""
    return [event.name for _, event in logged_events(job)]


def next_event(reader: Iterator) -> tuple[int, StreamEvent]:
    """The next real event from a reader, skipping heartbeats."""
    for delivery in reader:
        if delivery is not None:
            return delivery
    raise AssertionError("Pembaca berakhir sebelum ada peristiwa")


class TestServerSentEventEncoder:
    def test_a_numbered_frame_carries_its_number_as_the_id(self):
        frame = ServerSentEventEncoder().encode(StreamEvent("token", {"text": "a"}), 7)
        assert frame == 'id: 7\nevent: token\ndata: {"text": "a"}\n\n'

    def test_an_unnumbered_frame_has_no_id_line(self):
        frame = ServerSentEventEncoder().encode(StreamEvent("accepted", {"answer_id": "x"}))
        assert not frame.startswith("id:")
        assert frame.startswith("event: accepted\n")

    def test_indonesian_text_is_not_escaped(self):
        frame = ServerSentEventEncoder().encode(StreamEvent("token", {"text": "Sitasi — ✓"}), 1)
        assert "Sitasi — ✓" in frame

    def test_a_heartbeat_is_a_comment_without_data(self):
        heartbeat = ServerSentEventEncoder().encode_heartbeat()
        assert heartbeat.startswith(":")
        assert heartbeat.endswith("\n\n")
        # A frame without a data line is ignored by every SSE reader.
        assert "data:" not in heartbeat


class TestAnswerJobRelaying:
    def test_events_are_numbered_from_one_in_order(self):
        job = run_to_completion(scripted_answer())
        deliveries = logged_events(job)
        assert [number for number, _ in deliveries] == [1, 2, 3, 4, 5]
        assert [event.name for _, event in deliveries] == [
            "sources", "status", "token", "token", "done",
        ]

    def test_a_resuming_reader_receives_only_what_it_missed(self):
        job = run_to_completion(scripted_answer())
        resumed = logged_events(job, after_event_number=3)
        assert [number for number, _ in resumed] == [4, 5]
        assert resumed[0][1].payload["text"] == "adalah 12,5 SKS [1]."

    def test_a_reader_that_saw_everything_receives_nothing_more(self):
        job = run_to_completion(scripted_answer())
        assert logged_events(job, after_event_number=5) == []

    def test_a_negative_position_is_treated_as_the_start(self):
        job = run_to_completion(scripted_answer())
        assert len(logged_events(job, after_event_number=-4)) == 5

    def test_several_readers_each_receive_every_event(self):
        job = run_to_completion(scripted_answer())
        assert logged_events(job) == logged_events(job)

    def test_silence_produces_heartbeats_rather_than_blocking(self):
        source = GatedEventSource([StreamEvent("sources", {"citations": []})], [])
        job = AnswerJob("uji", source.stream())
        job.start()
        reader = job.follow(0, QUICK_HEARTBEAT_SECONDS)
        assert next_event(reader)[1].name == "sources"
        assert source.reached_gate.wait(WAIT_SECONDS)
        # Nothing new while the "model" is thinking: the reader gets a heartbeat.
        assert next(reader) is None
        source.gate.set()
        assert job.wait_until_finished(WAIT_SECONDS)


class TestAnswerOutlivesItsReader:
    def test_the_answer_completes_with_no_reader_at_all(self):
        source = GatedEventSource(scripted_answer()[:2], scripted_answer()[2:])
        job = AnswerJob("uji", source.stream())
        job.start()
        source.gate.set()
        assert job.wait_until_finished(WAIT_SECONDS)
        assert source.ran_to_completion
        assert job.event_count == 5

    def test_a_reader_leaving_mid_answer_does_not_stop_generation(self):
        source = GatedEventSource(scripted_answer()[:2], scripted_answer()[2:])
        job = AnswerJob("uji", source.stream())
        job.start()

        reader = job.follow(0, QUICK_HEARTBEAT_SECONDS)
        assert next_event(reader)[1].name == "sources"
        # The phone locks: the reader goes away while the model is still thinking.
        reader.close()

        source.gate.set()
        assert job.wait_until_finished(WAIT_SECONDS)
        assert source.ran_to_completion
        assert logged_event_names(job)[-1] == "done"


class TestAnswerJobTermination:
    def test_a_cancelled_job_stops_and_releases_its_source(self):
        source = GatedEventSource(
            [StreamEvent("sources", {"citations": []})],
            [StreamEvent("token", {"text": "a"}), StreamEvent("token", {"text": "b"})],
        )
        job = AnswerJob("uji", source.stream())
        job.start()
        assert source.reached_gate.wait(WAIT_SECONDS)

        job.request_cancel()
        source.gate.set()
        assert job.wait_until_finished(WAIT_SECONDS)

        # Stopped after the first event that followed the request, not at the end.
        assert not source.ran_to_completion
        # Closing the generator is what kills a command-line agent's process.
        assert source.was_released
        assert logged_event_names(job) == ["sources", "token", "error", "done"]
        assert logged_events(job)[2][1].payload["message"] == AnswerJob.CANCELLED_MESSAGE

    def test_cancelling_a_finished_job_changes_nothing(self):
        job = run_to_completion(scripted_answer())
        job.request_cancel()
        assert logged_event_names(job) == ["sources", "status", "token", "token", "done"]

    def test_an_unexpected_failure_still_ends_with_error_and_done(self):
        job = AnswerJob("uji", FailingEventSource().stream())
        job.start()
        assert job.wait_until_finished(WAIT_SECONDS)
        names = logged_event_names(job)
        assert names == ["sources", "error", "done"]
        assert "kesalahan tak terduga" in logged_events(job)[1][1].payload["message"]

    def test_a_source_that_ends_without_done_gets_one(self):
        job = run_to_completion([StreamEvent("token", {"text": "a"})])
        assert logged_event_names(job) == ["token", "done"]

    def test_done_is_never_duplicated(self):
        job = run_to_completion(scripted_answer())
        assert logged_event_names(job).count("done") == 1

    def test_finished_state_is_reported(self):
        source = GatedEventSource([], [])
        job = AnswerJob("uji", source.stream())
        job.start()
        assert source.reached_gate.wait(WAIT_SECONDS)
        assert not job.is_finished
        assert job.finished_at is None
        source.gate.set()
        assert job.wait_until_finished(WAIT_SECONDS)
        assert job.is_finished
        assert job.finished_at is not None


class TestAnswerJobRegistry:
    def test_a_started_job_can_be_found_by_its_identifier(self):
        registry = AnswerJobRegistry()
        job = registry.start(iter(scripted_answer()))
        assert registry.find(job.answer_id) is job
        assert job.wait_until_finished(WAIT_SECONDS)

    def test_identifiers_are_unique_and_unguessably_long(self):
        registry = AnswerJobRegistry()
        identifiers = {registry.start(iter([])).answer_id for _ in range(20)}
        assert len(identifiers) == 20
        assert all(len(identifier) >= 20 for identifier in identifiers)

    def test_an_unknown_identifier_is_not_found(self):
        assert AnswerJobRegistry().find("tidak-ada") is None

    def test_finished_jobs_are_forgotten_after_the_retention_period(self, monkeypatch):
        registry = AnswerJobRegistry(retention_seconds=60)
        old_job = registry.start(iter(scripted_answer()))
        assert old_job.wait_until_finished(WAIT_SECONDS)

        later = old_job.finished_at + 61
        monkeypatch.setattr("app.answer_streams.time.time", lambda: later)
        registry.start(iter([]))
        assert registry.find(old_job.answer_id) is None

    def test_the_oldest_finished_jobs_are_dropped_beyond_the_cap(self):
        registry = AnswerJobRegistry(maximum_retained=2)
        jobs = []
        for _ in range(3):
            job = registry.start(iter(scripted_answer()))
            assert job.wait_until_finished(WAIT_SECONDS)
            jobs.append(job)
        assert registry.find(jobs[0].answer_id) is None
        assert registry.find(jobs[1].answer_id) is jobs[1]
        assert registry.find(jobs[2].answer_id) is jobs[2]

    def test_a_running_job_is_never_dropped(self):
        registry = AnswerJobRegistry(maximum_retained=1)
        source = GatedEventSource([], [])
        running_job = registry.start(source.stream())
        assert source.reached_gate.wait(WAIT_SECONDS)
        for _ in range(3):
            registry.start(iter([]))
        assert registry.find(running_job.answer_id) is running_job
        source.gate.set()
        assert running_job.wait_until_finished(WAIT_SECONDS)


class TestAnswerFrameStreamer:
    def test_a_new_reader_is_told_the_answer_identifier_first(self):
        job = run_to_completion(scripted_answer())
        streamer = AnswerFrameStreamer(ServerSentEventEncoder(), QUICK_HEARTBEAT_SECONDS)
        frames = list(streamer.iterate_frames(job, announce=True))
        assert frames[0] == 'event: accepted\ndata: {"answer_id": "uji"}\n\n'
        assert frames[1].startswith("id: 1\nevent: sources\n")
        assert len(frames) == 6

    def test_a_resuming_reader_gets_no_announcement(self):
        job = run_to_completion(scripted_answer())
        streamer = AnswerFrameStreamer(ServerSentEventEncoder(), QUICK_HEARTBEAT_SECONDS)
        frames = list(streamer.iterate_frames(job, after_event_number=3))
        assert [frame.split("\n")[0] for frame in frames] == ["id: 4", "id: 5"]

    def test_silence_is_filled_with_heartbeat_frames(self):
        source = GatedEventSource([StreamEvent("sources", {"citations": []})], [])
        job = AnswerJob("uji", source.stream())
        job.start()
        encoder = ServerSentEventEncoder()
        frames = AnswerFrameStreamer(encoder, QUICK_HEARTBEAT_SECONDS).iterate_frames(job)

        assert next(frames).startswith("id: 1\nevent: sources")
        assert source.reached_gate.wait(WAIT_SECONDS)
        assert next(frames) == encoder.encode_heartbeat()
        source.gate.set()
        assert list(frames)[-1].startswith("id: 2\nevent: done")
