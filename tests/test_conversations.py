"""Tests for saving, resuming and deleting chat sessions."""

from __future__ import annotations

import pytest

from app.conversations import ChatTurn, ConversationStore, SessionTitleBuilder


@pytest.fixture
def store(isolated_settings, guard) -> ConversationStore:
    """A conversation store backed by a throwaway database file."""
    instance = ConversationStore(isolated_settings, guard)
    yield instance
    instance.close()


def add_exchange(store: ConversationStore, session_id: str, question: str, answer: str):
    """Append one question-and-answer pair to a session."""
    store.append_turn(session_id, ChatTurn(role="user", content=question))
    store.ensure_titled(session_id, question)
    store.append_turn(
        session_id,
        ChatTurn(
            role="assistant",
            content=answer,
            citations=[{"citation_number": 1, "document_name": "a.pdf"}],
            backend_name="ollama",
            model_name="qwen3:8b",
            elapsed_seconds=1.5,
        ),
    )


class TestSessionTitleBuilder:
    """Deriving a readable session title from the opening question."""

    def test_short_question_becomes_the_title(self):
        assert SessionTitleBuilder().build("Berapa EWMP?") == "Berapa EWMP?"

    def test_long_question_is_truncated_at_a_word_boundary(self):
        title = SessionTitleBuilder().build("kata " * 40)
        assert len(title) <= SessionTitleBuilder.MAXIMUM_LENGTH + 1
        assert title.endswith("…")

    def test_whitespace_is_collapsed(self):
        assert SessionTitleBuilder().build("Berapa\n\n  EWMP?") == "Berapa EWMP?"

    def test_empty_question_falls_back_to_the_default(self):
        assert SessionTitleBuilder().build("   ") == SessionTitleBuilder.DEFAULT_TITLE


class TestSessionLifecycle:
    """Creating, listing and loading sessions."""

    def test_new_store_has_no_sessions(self, store):
        assert store.list_sessions() == []

    def test_created_session_is_listed(self, store):
        session = store.create_session()
        listed = store.list_sessions()
        assert len(listed) == 1
        assert listed[0].session_id == session.session_id

    def test_new_session_starts_with_the_placeholder_title(self, store):
        assert store.create_session().title == SessionTitleBuilder.DEFAULT_TITLE

    def test_identifiers_are_unique(self, store):
        identifiers = {store.create_session().session_id for _ in range(20)}
        assert len(identifiers) == 20

    def test_loading_an_unknown_session_returns_none(self, store):
        assert store.load_session("tidak-ada") is None

    def test_session_exists_reports_correctly(self, store):
        session = store.create_session()
        assert store.session_exists(session.session_id) is True
        assert store.session_exists("tidak-ada") is False


class TestSavingAndResuming:
    """The save-and-continue behaviour."""

    def test_turns_are_stored_in_order(self, store):
        session = store.create_session()
        add_exchange(store, session.session_id, "Berapa EWMP?", "12,5 SKS [1].")

        loaded = store.load_session(session.session_id)
        assert [turn.role for turn in loaded.turns] == ["user", "assistant"]
        assert loaded.turns[0].content == "Berapa EWMP?"

    def test_answer_keeps_its_citations_and_backend(self, store):
        session = store.create_session()
        add_exchange(store, session.session_id, "Berapa EWMP?", "12,5 SKS [1].")

        answer = store.load_session(session.session_id).turns[1]
        assert answer.citations[0]["document_name"] == "a.pdf"
        assert answer.backend_name == "ollama"
        assert answer.elapsed_seconds == pytest.approx(1.5)

    def test_title_is_taken_from_the_first_question(self, store):
        session = store.create_session()
        add_exchange(store, session.session_id, "Berapa rata-rata EWMP DTPR?", "12,5 [1].")
        assert store.load_session(session.session_id).title == "Berapa rata-rata EWMP DTPR?"

    def test_title_is_not_overwritten_by_later_questions(self, store):
        """The session should stay findable under the topic it started with."""
        session = store.create_session()
        add_exchange(store, session.session_id, "Pertanyaan pertama", "Jawaban.")
        add_exchange(store, session.session_id, "Pertanyaan kedua", "Jawaban.")
        assert store.load_session(session.session_id).title == "Pertanyaan pertama"

    def test_turn_count_reflects_stored_turns(self, store):
        session = store.create_session()
        add_exchange(store, session.session_id, "Satu", "Jawab satu.")
        add_exchange(store, session.session_id, "Dua", "Jawab dua.")
        assert store.list_sessions()[0].turn_count == 4

    def test_sessions_are_listed_most_recent_first(self, store):
        older = store.create_session("Lama")
        newer = store.create_session("Baru")
        add_exchange(store, older.session_id, "Pertanyaan", "Jawaban.")

        listed = store.list_sessions()
        assert listed[0].session_id == older.session_id
        assert listed[1].session_id == newer.session_id

    def test_turns_for_an_unknown_session_are_rejected(self, store):
        """A stale browser tab must not create orphaned turns."""
        assert store.append_turn("tidak-ada", ChatTurn(role="user", content="halo")) == 0

    def test_unreadable_citations_do_not_break_loading(self, store):
        session = store.create_session()
        store.append_turn(session.session_id, ChatTurn(role="assistant", content="Jawaban"))
        store._connection.execute(
            "UPDATE turns SET citations_json = ? WHERE session_id = ?",
            ("{bukan json", session.session_id),
        )
        store._connection.commit()
        assert store.load_session(session.session_id).turns[0].citations == []


class TestHistoryForPrompting:
    """Fetching recent turns to replay to the model."""

    def test_returns_turns_oldest_first(self, store):
        session = store.create_session()
        add_exchange(store, session.session_id, "Satu", "Jawab satu.")
        add_exchange(store, session.session_id, "Dua", "Jawab dua.")

        history = store.recent_turns(session.session_id, 4)
        assert [turn.content for turn in history] == [
            "Satu", "Jawab satu.", "Dua", "Jawab dua.",
        ]

    def test_limit_keeps_the_newest_turns(self, store):
        session = store.create_session()
        add_exchange(store, session.session_id, "Satu", "Jawab satu.")
        add_exchange(store, session.session_id, "Dua", "Jawab dua.")

        history = store.recent_turns(session.session_id, 2)
        assert [turn.content for turn in history] == ["Dua", "Jawab dua."]

    def test_zero_limit_returns_nothing(self, store):
        session = store.create_session()
        add_exchange(store, session.session_id, "Satu", "Jawab.")
        assert store.recent_turns(session.session_id, 0) == []


class TestRenaming:
    """Retitling a conversation."""

    def test_rename_changes_the_title(self, store):
        session = store.create_session()
        assert store.rename_session(session.session_id, "Judul baru") is True
        assert store.load_session(session.session_id).title == "Judul baru"

    def test_renaming_an_unknown_session_fails(self, store):
        assert store.rename_session("tidak-ada", "Judul") is False

    def test_blank_title_is_rejected(self, store):
        session = store.create_session()
        assert store.rename_session(session.session_id, "   ") is False


class TestDeletion:
    """Deleting conversations."""

    def test_delete_removes_the_session(self, store):
        session = store.create_session()
        assert store.delete_session(session.session_id) is True
        assert store.load_session(session.session_id) is None

    def test_delete_removes_its_turns_too(self, store):
        session = store.create_session()
        add_exchange(store, session.session_id, "Pertanyaan", "Jawaban.")
        store.delete_session(session.session_id)
        assert store.collect_statistics()["turn_count"] == 0

    def test_deleting_an_unknown_session_reports_failure(self, store):
        assert store.delete_session("tidak-ada") is False

    def test_deleting_one_session_leaves_the_others(self, store):
        keep = store.create_session("Simpan")
        remove = store.create_session("Hapus")
        add_exchange(store, keep.session_id, "Pertanyaan", "Jawaban.")

        store.delete_session(remove.session_id)
        remaining = store.list_sessions()
        assert len(remaining) == 1
        assert remaining[0].session_id == keep.session_id
        assert store.load_session(keep.session_id).turn_count == 2

    def test_delete_all_reports_how_many_went(self, store):
        for _ in range(3):
            store.create_session()
        assert store.delete_all_sessions() == 3
        assert store.list_sessions() == []


class TestIsolationFromTheIndex:
    """Conversations must survive index maintenance."""

    def test_conversations_use_a_separate_database_file(self, isolated_settings, store):
        """`ingest --rebuild` clears the index; chat history must not be collateral."""
        conversation_file = isolated_settings.data_directory / "conversations.db"
        assert conversation_file.exists()
        assert conversation_file != isolated_settings.database_path

    def test_store_refuses_a_path_inside_the_corpus(self, isolated_settings, guard):
        from app.safety import SourceWriteAttempt

        isolated_settings.data_directory = isolated_settings.source_directory / "chat"
        with pytest.raises(SourceWriteAttempt):
            ConversationStore(isolated_settings, guard)
