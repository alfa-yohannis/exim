"""Tests for reasoning-tag filtering and language-model backend selection."""

from __future__ import annotations

import pytest

from app.language_models import (
    AgentEventTextExtractor,
    AntigravityBackend,
    BackendStatus,
    ClaudeCodeBackend,
    CodexBackend,
    LanguageModelFactory,
    OllamaBackend,
    ReasoningTagStripper,
)


class TestReasoningTagStripperCompleteText:
    """Cleaning a fully received response."""

    def test_removes_a_complete_reasoning_block(self):
        stripper = ReasoningTagStripper()
        assert stripper.strip_complete_text("<think>menimbang</think>Jawaban.") == "Jawaban."

    def test_removes_several_blocks(self):
        stripper = ReasoningTagStripper()
        cleaned = stripper.strip_complete_text("<think>a</think>X<think>b</think>Y")
        assert cleaned == "XY"

    def test_drops_an_unterminated_block_from_a_truncated_response(self):
        stripper = ReasoningTagStripper()
        assert stripper.strip_complete_text("Jawaban.<think>terpotong") == "Jawaban."

    def test_leaves_ordinary_text_untouched(self):
        stripper = ReasoningTagStripper()
        assert stripper.strip_complete_text("Rata-rata EWMP 12,5 SKS") == "Rata-rata EWMP 12,5 SKS"


class TestReasoningTagStripperStreaming:
    """Filtering the same markup as it arrives chunk by chunk."""

    def collect(self, stripper: ReasoningTagStripper, chunks: list[str]) -> str:
        """Feed chunks through the stripper and return the visible output."""
        visible = "".join(stripper.filter_stream_chunk(chunk) for chunk in chunks)
        return visible + stripper.flush()

    def test_passes_plain_text_through(self):
        assert self.collect(ReasoningTagStripper(), ["Rata", "-rata ", "EWMP"]) == "Rata-rata EWMP"

    def test_hides_a_block_contained_in_one_chunk(self):
        assert self.collect(ReasoningTagStripper(), ["<think>abc</think>Hasil"]) == "Hasil"

    def test_hides_a_block_split_across_chunks(self):
        chunks = ["<think>meni", "mbang jawaban", "</think>Hasil akhir"]
        assert self.collect(ReasoningTagStripper(), chunks) == "Hasil akhir"

    def test_holds_back_a_tag_split_mid_marker(self):
        """A chunk ending in "<thi" must not leak into the browser."""
        stripper = ReasoningTagStripper()
        first = stripper.filter_stream_chunk("Jawaban <thi")
        assert "<thi" not in first
        rest = stripper.filter_stream_chunk("nk>rahasia</think> selesai")
        assert (first + rest + stripper.flush()).strip() == "Jawaban  selesai".strip()

    def test_emits_text_that_merely_resembles_a_tag(self):
        result = self.collect(ReasoningTagStripper(), ["nilai < think ", "lebih kecil"])
        assert "lebih kecil" in result

    def test_flush_drops_an_unterminated_block(self):
        stripper = ReasoningTagStripper()
        stripper.filter_stream_chunk("Hasil<think>belum selesai")
        assert stripper.flush() == ""

    def test_handles_uppercase_tags(self):
        assert self.collect(ReasoningTagStripper(), ["<THINK>x</THINK>Hasil"]) == "Hasil"


class TestBackendStatus:
    """The availability record shown in the interface."""

    def test_serialises_every_field(self):
        payload = BackendStatus(
            True, "ollama", "qwen3:8b", "siap", runs_locally=True, effort="max"
        ).as_dictionary()
        assert payload == {
            "is_available": True,
            "backend_name": "ollama",
            "model_name": "qwen3:8b",
            "detail": "siap",
            "runs_locally": True,
            "effort": "max",
        }

    def test_effort_defaults_to_empty_for_backends_without_one(self):
        """Ollama has no reasoning setting; the badge must simply not appear."""
        assert BackendStatus(True, "ollama", "qwen3:8b").effort == ""

    def test_defaults_to_reporting_a_remote_backend(self):
        """Defaulting to "not local" fails safe for the privacy warning."""
        assert BackendStatus(True, "x", "y").runs_locally is False


class TestAgentEventTextExtractor:
    """Decoding the NDJSON events the three agent CLIs emit.

    The shapes asserted here were captured from the actual CLIs installed on
    this machine, not inferred from documentation.
    """

    def test_reads_a_claude_assistant_event(self):
        """Verified shape from `claude --print --output-format stream-json`."""
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Rata-rata EWMP 12,5 SKS"}]},
        }
        assert AgentEventTextExtractor().extract(event) == ["Rata-rata EWMP 12,5 SKS"]

    def test_joins_several_claude_text_blocks(self):
        event = {
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "bagian satu "},
                {"type": "text", "text": "bagian dua"},
            ]},
        }
        assert AgentEventTextExtractor().extract(event) == ["bagian satu ", "bagian dua"]

    def test_ignores_non_text_content_blocks(self):
        """Tool-use blocks are not answer text and must not be shown."""
        event = {
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file": "rahasia.pdf"}},
                {"type": "text", "text": "Jawaban"},
            ]},
        }
        assert AgentEventTextExtractor().extract(event) == ["Jawaban"]

    def test_reads_a_codex_completed_item(self):
        """Verified shape from `codex exec --json`."""
        event = {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": "OK"},
        }
        assert AgentEventTextExtractor().extract(event) == ["OK"]

    def test_ignores_codex_reasoning_and_command_items(self):
        event = {
            "type": "item.completed",
            "item": {"id": "item_1", "type": "reasoning", "text": "menimbang jawaban"},
        }
        assert AgentEventTextExtractor().extract(event) == []

    def test_reads_an_incremental_delta(self):
        event = {"type": "content_block_delta", "delta": {"text": "sepotong"}}
        assert AgentEventTextExtractor().extract(event) == ["sepotong"]

    @pytest.mark.parametrize(
        "event",
        [
            {"type": "system", "subtype": "init", "tools": ["Read", "Bash"]},
            {"type": "thread.started", "thread_id": "abc"},
            {"type": "turn.started"},
            {"type": "turn.completed", "usage": {"input_tokens": 15469}},
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
        ],
    )
    def test_ignores_lifecycle_and_metadata_events(self, event):
        """Session banners and usage records must never enter the answer."""
        assert AgentEventTextExtractor().extract(event) == []

    def test_ignores_malformed_events(self):
        assert AgentEventTextExtractor().extract("bukan dict") == []
        assert AgentEventTextExtractor().extract({"type": "assistant"}) == []

    def test_reads_a_terminal_result_field(self):
        extractor = AgentEventTextExtractor()
        assert extractor.extract_final_result({"is_error": False, "result": "Jawaban"}) == "Jawaban"

    def test_ignores_a_non_string_result(self):
        assert AgentEventTextExtractor().extract_final_result({"result": {"a": 1}}) == ""

    def test_detects_a_failed_turn(self):
        """The verified Claude terminal event carries is_error, not type=result."""
        message = AgentEventTextExtractor().describe_error(
            {"is_error": True, "result": "kuota habis"}
        )
        assert message == "kuota habis"

    def test_reports_no_error_for_a_successful_turn(self):
        assert AgentEventTextExtractor().describe_error({"is_error": False}) is None


class TestOllamaBackend:
    """The local model backend."""

    def test_reports_its_identity(self, isolated_settings):
        backend = OllamaBackend(isolated_settings)
        assert backend.name == "ollama"
        assert backend.model_name == isolated_settings.ollama_model_name

    def test_matches_an_exactly_installed_model(self, isolated_settings):
        isolated_settings.ollama_model_name = "qwen3:8b"
        backend = OllamaBackend(isolated_settings)
        assert backend._is_model_installed(["qwen3:8b", "llama3:8b"]) is True

    def test_matches_on_the_bare_model_name(self, isolated_settings):
        """A configured "qwen3" should match an installed "qwen3:8b"."""
        isolated_settings.ollama_model_name = "qwen3"
        backend = OllamaBackend(isolated_settings)
        assert backend._is_model_installed(["qwen3:8b"]) is True

    def test_reports_a_missing_model(self, isolated_settings):
        isolated_settings.ollama_model_name = "qwen3:8b"
        backend = OllamaBackend(isolated_settings)
        assert backend._is_model_installed(["mistral:7b"]) is False

    def test_unreachable_service_reports_unavailable(self, isolated_settings):
        isolated_settings.ollama_base_url = "http://127.0.0.1:1"
        status = OllamaBackend(isolated_settings).check_status()
        assert status.is_available is False
        assert "Ollama" in status.detail


class TestClaudeCodeBackend:
    """The Claude Code CLI backend."""

    def test_reports_its_identity(self, isolated_settings):
        assert ClaudeCodeBackend(isolated_settings).name == "claude_cli"

    def test_is_not_a_local_backend(self, isolated_settings):
        assert ClaudeCodeBackend(isolated_settings).runs_locally is False

    def test_blank_model_setting_reports_the_cli_default(self, isolated_settings):
        isolated_settings.claude_cli_model = ""
        assert "default" in ClaudeCodeBackend(isolated_settings).model_name

    def test_command_requests_non_interactive_streaming(self, isolated_settings):
        command = ClaudeCodeBackend(isolated_settings).build_command()
        assert "--print" in command
        assert "stream-json" in command

    def test_command_disables_filesystem_tools(self, isolated_settings):
        """The CLI must answer from the prompt, never read the corpus itself."""
        command = ClaudeCodeBackend(isolated_settings).build_command()
        assert "--disallowed-tools" in command
        assert "Read" in command and "Bash" in command and "Write" in command

    def test_command_includes_the_model_when_configured(self, isolated_settings):
        isolated_settings.claude_cli_model = "claude-sonnet-5"
        command = ClaudeCodeBackend(isolated_settings).build_command()
        assert "--model" in command
        assert "claude-sonnet-5" in command

    def test_missing_executable_reports_unavailable(self, isolated_settings):
        isolated_settings.claude_cli_executable = "claude-yang-tidak-ada"
        status = ClaudeCodeBackend(isolated_settings).check_status()
        assert status.is_available is False
        assert "tidak ditemukan" in status.detail


class TestCodexBackend:
    """The OpenAI Codex CLI backend."""

    def test_reports_its_identity(self, isolated_settings):
        assert CodexBackend(isolated_settings).name == "codex_cli"

    def test_uses_the_non_interactive_exec_subcommand(self, isolated_settings):
        command = CodexBackend(isolated_settings).build_command()
        assert command[1] == "exec"
        assert "--json" in command

    def test_runs_in_a_read_only_sandbox(self, isolated_settings):
        """The sandbox is what physically prevents Codex writing to the corpus."""
        command = CodexBackend(isolated_settings).build_command()
        assert "--sandbox" in command
        assert command[command.index("--sandbox") + 1] == "read-only"

    def test_skips_the_git_repository_check(self, isolated_settings):
        """This project is not a git repository, and Codex refuses to run without this."""
        assert "--skip-git-repo-check" in CodexBackend(isolated_settings).build_command()

    def test_reads_the_prompt_from_standard_input(self, isolated_settings):
        """A trailing "-" is how Codex is told to take the prompt from stdin."""
        assert CodexBackend(isolated_settings).build_command()[-1] == "-"

    def test_missing_executable_reports_unavailable(self, isolated_settings):
        isolated_settings.codex_cli_executable = "codex-yang-tidak-ada"
        assert CodexBackend(isolated_settings).check_status().is_available is False


class TestAntigravityBackend:
    """The Antigravity (``agy``) CLI backend."""

    def test_reports_its_identity(self, isolated_settings):
        assert AntigravityBackend(isolated_settings).name == "antigravity_cli"

    def test_defaults_to_the_agy_command(self, isolated_settings):
        assert AntigravityBackend(isolated_settings).executable == "agy"

    def test_command_requests_non_interactive_streaming(self, isolated_settings):
        command = AntigravityBackend(isolated_settings).build_command()
        assert "--print" in command
        assert "stream-json" in command

    def test_display_name_drops_the_embedded_effort_tier(self, isolated_settings):
        """The tier is already shown as a badge; repeating it reads as noise."""
        assert AntigravityBackend(isolated_settings).model_name == "gemini-3.1-pro"

    def test_command_still_sends_the_full_catalogue_name(self, isolated_settings):
        """Shortening is cosmetic — the CLI only accepts the full tiered name."""
        command = AntigravityBackend(isolated_settings).build_command()
        assert command[command.index("--model") + 1] == "gemini-3.1-pro-high"

    @pytest.mark.parametrize(
        "configured,displayed",
        [
            ("gemini-3.1-pro-high", "gemini-3.1-pro"),
            ("gemini-3.7-flash-medium", "gemini-3.7-flash"),
            ("gemini-3.5-flash-low", "gemini-3.5-flash"),
            ("claude-opus-4-6-thinking", "claude-opus-4-6-thinking"),
            ("gpt-oss-120b-medium", "gpt-oss-120b"),
        ],
    )
    def test_only_a_trailing_tier_is_stripped(
        self, isolated_settings, configured, displayed
    ):
        """A name that merely ends in a word must survive intact."""
        isolated_settings.antigravity_cli_model = configured
        assert AntigravityBackend(isolated_settings).model_name == displayed

    def test_disables_slash_command_expansion(self, isolated_settings):
        """A retrieved passage beginning with "/" must stay document text."""
        assert "--disable-slash-commands" in AntigravityBackend(isolated_settings).build_command()

    def test_missing_executable_reports_unavailable(self, isolated_settings):
        isolated_settings.antigravity_cli_executable = "agy-yang-tidak-ada"
        assert AntigravityBackend(isolated_settings).check_status().is_available is False


class TestCommandLineAgentContract:
    """Guarantees every CLI backend must uphold."""

    BACKEND_CLASSES = (ClaudeCodeBackend, CodexBackend, AntigravityBackend)

    @pytest.mark.parametrize("backend_class", BACKEND_CLASSES)
    def test_no_backend_passes_the_prompt_as_an_argument(self, isolated_settings, backend_class):
        """Prompts exceed the Windows argument limit; they must go via stdin."""
        command = backend_class(isolated_settings).build_command()
        assert all(len(argument) < 200 for argument in command)

    @pytest.mark.parametrize("backend_class", BACKEND_CLASSES)
    def test_every_backend_reports_itself_as_remote(self, isolated_settings, backend_class):
        assert backend_class(isolated_settings).runs_locally is False

    @pytest.mark.parametrize("backend_class", BACKEND_CLASSES)
    def test_command_starts_with_the_configured_executable(
        self, isolated_settings, backend_class
    ):
        backend = backend_class(isolated_settings)
        assert backend.build_command()[0] == backend.executable

    @pytest.mark.parametrize("backend_class", BACKEND_CLASSES)
    def test_every_backend_bounds_its_runtime(self, isolated_settings, backend_class):
        """Reading a CLI's stdout blocks; without a timeout a hang is unbounded."""
        assert backend_class(isolated_settings).timeout_seconds > 0

    @pytest.mark.parametrize("backend_class", BACKEND_CLASSES)
    def test_every_backend_names_a_model_by_default(self, isolated_settings, backend_class):
        """Each CLI is pinned to a specific model rather than left to drift."""
        command = backend_class(isolated_settings).build_command()
        assert "--model" in command

    @pytest.mark.parametrize("backend_class", BACKEND_CLASSES)
    def test_model_name_carries_only_the_model(self, isolated_settings, backend_class):
        """Effort has its own field, so repeating it here would show it twice."""
        assert "effort" not in backend_class(isolated_settings).model_name

    @pytest.mark.parametrize("backend_class", BACKEND_CLASSES)
    def test_every_backend_reports_a_reasoning_depth(self, isolated_settings, backend_class):
        """The interface renders this as its own badge beside the model."""
        assert backend_class(isolated_settings).effort


class TestReasoningEffort:
    """Each CLI is driven at its deepest reasoning setting.

    The model names and effort levels asserted here were read from each CLI's
    own catalogue on this machine, not assumed from documentation.
    """

    def test_claude_runs_opus_5_at_max_effort(self, isolated_settings):
        command = ClaudeCodeBackend(isolated_settings).build_command()
        assert command[command.index("--model") + 1] == "claude-opus-5"
        assert command[command.index("--effort") + 1] == "max"

    def test_codex_runs_its_flagship_at_max_effort(self, isolated_settings):
        command = CodexBackend(isolated_settings).build_command()
        assert command[command.index("--model") + 1] == "gpt-5.6-sol"
        assert '-c' in command
        assert 'model_reasoning_effort="max"' in command

    def test_codex_effort_is_quoted_for_toml_parsing(self, isolated_settings):
        """Codex parses the override value as TOML; an unquoted word is not a string."""
        override = [a for a in CodexBackend(isolated_settings).build_command()
                    if a.startswith("model_reasoning_effort")][0]
        assert override.endswith('"max"')

    def test_antigravity_runs_gemini_pro_at_high_effort(self, isolated_settings):
        """That CLI accepts only low/medium/high, so "high" is its maximum."""
        command = AntigravityBackend(isolated_settings).build_command()
        assert command[command.index("--model") + 1] == "gemini-3.1-pro-high"
        assert command[command.index("--effort") + 1] == "high"

    @pytest.mark.parametrize(
        "backend_class,expected",
        [
            (ClaudeCodeBackend, "max"),
            (CodexBackend, "max"),
            (AntigravityBackend, "high"),
        ],
    )
    def test_effort_is_reported_separately_from_the_model_name(
        self, isolated_settings, backend_class, expected
    ):
        """The interface shows effort as its own badge, so it needs its own field."""
        assert backend_class(isolated_settings).effort == expected

    def test_effort_can_be_overridden_by_configuration(self, isolated_settings):
        isolated_settings.claude_cli_effort = "low"
        command = ClaudeCodeBackend(isolated_settings).build_command()
        assert command[command.index("--effort") + 1] == "low"

    def test_blank_effort_omits_the_flag(self, isolated_settings):
        isolated_settings.claude_cli_effort = ""
        assert "--effort" not in ClaudeCodeBackend(isolated_settings).build_command()

    @pytest.mark.parametrize(
        "backend_class,attribute",
        [
            (ClaudeCodeBackend, "claude_cli_timeout_seconds"),
            (CodexBackend, "codex_cli_timeout_seconds"),
            (AntigravityBackend, "antigravity_cli_timeout_seconds"),
        ],
    )
    def test_timeouts_allow_for_deep_reasoning(
        self, isolated_settings, backend_class, attribute
    ):
        """Max-effort answers take minutes; a 4-minute cap would truncate them."""
        assert getattr(isolated_settings, attribute) >= 600

    def test_each_backend_reads_its_own_timeout_setting(self, isolated_settings):
        isolated_settings.claude_cli_timeout_seconds = 111
        isolated_settings.codex_cli_timeout_seconds = 222
        isolated_settings.antigravity_cli_timeout_seconds = 333
        assert ClaudeCodeBackend(isolated_settings).timeout_seconds == 111
        assert CodexBackend(isolated_settings).timeout_seconds == 222
        assert AntigravityBackend(isolated_settings).timeout_seconds == 333

    def test_a_missing_executable_raises_a_readable_error(self, isolated_settings):
        """The message must name the command so the fix is obvious."""
        isolated_settings.codex_cli_executable = "codex-yang-tidak-ada"
        backend = CodexBackend(isolated_settings)
        with pytest.raises(RuntimeError) as error:
            list(backend.generate_stream("sistem", "pengguna"))
        assert "codex-yang-tidak-ada" in str(error.value)


class TestLanguageModelFactory:
    """Resolving a backend name to an instance."""

    def test_lists_every_backend_local_first(self, isolated_settings):
        assert LanguageModelFactory(isolated_settings).available_backend_names == [
            "ollama",
            "claude_cli",
            "codex_cli",
            "antigravity_cli",
        ]

    @pytest.mark.parametrize(
        "backend_name,expected_class",
        [
            ("ollama", OllamaBackend),
            ("claude_cli", ClaudeCodeBackend),
            ("codex_cli", CodexBackend),
            ("antigravity_cli", AntigravityBackend),
        ],
    )
    def test_builds_each_registered_backend(self, isolated_settings, backend_name, expected_class):
        factory = LanguageModelFactory(isolated_settings)
        assert isinstance(factory.create(backend_name), expected_class)

    def test_creates_the_configured_backend_by_default(self, isolated_settings):
        isolated_settings.language_model_backend = "ollama"
        assert LanguageModelFactory(isolated_settings).create().name == "ollama"

    def test_explicit_name_overrides_configuration(self, isolated_settings):
        isolated_settings.language_model_backend = "ollama"
        factory = LanguageModelFactory(isolated_settings)
        assert factory.create("claude_cli").name == "claude_cli"

    def test_name_matching_ignores_case_and_padding(self, isolated_settings):
        factory = LanguageModelFactory(isolated_settings)
        assert factory.create("  OLLAMA  ").name == "ollama"

    def test_instances_are_reused(self, isolated_settings):
        factory = LanguageModelFactory(isolated_settings)
        assert factory.create("ollama") is factory.create("ollama")

    def test_unknown_backend_raises_with_the_valid_options(self, isolated_settings):
        factory = LanguageModelFactory(isolated_settings)
        with pytest.raises(ValueError) as error:
            factory.create("gpt-lokal")
        assert "ollama" in str(error.value)
