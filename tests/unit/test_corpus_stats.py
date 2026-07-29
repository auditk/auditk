"""Unit tests for scripts/corpus_stats.py's pure counting logic.

Uses only a synthetic fixture built in-test under `tmp_path` — never the real
`~/.claude/projects` corpus. `scripts/` is not a package, so the module is
loaded directly from its file path (same approach as importing any other
free-standing script for testing).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "corpus_stats.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("corpus_stats", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


corpus_stats = _load_module()


def _assistant_record(
    tool_calls: list[tuple[str, dict[str, object]]] | None = None,
) -> dict[str, object]:
    content: list[dict[str, object]] = []
    for name, tool_input in tool_calls or []:
        content.append({"type": "tool_use", "name": name, "input": tool_input})
    return {"type": "assistant", "message": {"content": content}}


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


class TestIterJsonl:
    def test_skips_blank_and_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "session.jsonl"
        path.write_text('{"type": "user"}\n\nnot json\n["not a dict"]\n{"type": "assistant"}\n')
        records = corpus_stats.iter_jsonl(path)
        assert records == [{"type": "user"}, {"type": "assistant"}]


class TestCountRecordTypes:
    def test_counts_by_type_field(self) -> None:
        records = [{"type": "user"}, {"type": "assistant"}, {"type": "user"}, {"type": "system"}]
        counts = corpus_stats.count_record_types(records)
        assert counts == {"user": 2, "assistant": 1, "system": 1}

    def test_ignores_records_without_type(self) -> None:
        counts = corpus_stats.count_record_types([{"foo": "bar"}, {"type": "user"}])
        assert counts == {"user": 1}


class TestCountToolCalls:
    def test_counts_tool_use_blocks_in_assistant_messages(self) -> None:
        records = [
            _assistant_record([("Bash", {}), ("Read", {})]),
            _assistant_record([("Bash", {})]),
            {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
        ]
        counts = corpus_stats.count_tool_calls(records)
        assert counts == {"Bash": 2, "Read": 1}

    def test_filters_to_requested_names(self) -> None:
        records = [_assistant_record([("TodoWrite", {}), ("TaskCreate", {}), ("Bash", {})])]
        counts = corpus_stats.count_tool_calls(records, names=("TodoWrite", "TaskCreate"))
        assert counts == {"TodoWrite": 1, "TaskCreate": 1}
        assert "Bash" not in counts

    def test_ignores_malformed_message_shapes(self) -> None:
        records = [
            {"type": "assistant", "message": None},
            {"type": "assistant", "message": {"content": "not a list"}},
            {"type": "assistant"},
        ]
        counts = corpus_stats.count_tool_calls(records)
        assert counts == {}


class TestHasPlanStore:
    def test_true_when_task_files_present(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "abc-123"
        session_dir.mkdir()
        (session_dir / "1.json").write_text("{}")
        assert corpus_stats.has_plan_store(tmp_path, "abc-123") is True

    def test_false_when_directory_absent(self, tmp_path: Path) -> None:
        assert corpus_stats.has_plan_store(tmp_path, "missing") is False

    def test_false_when_directory_empty(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "empty-session"
        session_dir.mkdir()
        assert corpus_stats.has_plan_store(tmp_path, "empty-session") is False


class TestDiscoverSessions:
    def test_pairs_transcript_with_sibling_dir(self, tmp_path: Path) -> None:
        # The layout trap: <uuid>.jsonl is a SIBLING of <uuid>/, not inside it.
        project_dir = tmp_path / "-home-matt-Projects-demo"
        project_dir.mkdir()
        sid = "11111111-1111-1111-1111-111111111111"
        _write_jsonl(project_dir / f"{sid}.jsonl", [{"type": "user"}])
        sibling_dir = project_dir / sid
        (sibling_dir / "subagents").mkdir(parents=True)

        sessions = corpus_stats.discover_sessions(tmp_path)

        assert len(sessions) == 1
        assert sessions[0].session_id == sid
        assert sessions[0].session_dir == sibling_dir

    def test_session_dir_none_when_no_sibling(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        _write_jsonl(project_dir / "22222222-2222-2222-2222-222222222222.jsonl", [{"type": "user"}])

        sessions = corpus_stats.discover_sessions(tmp_path)

        assert sessions[0].session_dir is None

    def test_empty_when_root_missing(self, tmp_path: Path) -> None:
        assert corpus_stats.discover_sessions(tmp_path / "nonexistent") == []


class TestDiscoverSubagentTranscripts:
    def test_finds_agent_jsonl_files(self, tmp_path: Path) -> None:
        subagents_dir = tmp_path / "session-dir" / "subagents"
        subagents_dir.mkdir(parents=True)
        (subagents_dir / "agent-aaa.jsonl").write_text("{}\n")
        (subagents_dir / "agent-aaa.meta.json").write_text("{}")  # must not be picked up
        (subagents_dir / "agent-bbb.jsonl").write_text("{}\n")

        found = corpus_stats.discover_subagent_transcripts(tmp_path / "session-dir")

        assert [p.name for p in found] == ["agent-aaa.jsonl", "agent-bbb.jsonl"]

    def test_empty_when_no_subagents_dir(self, tmp_path: Path) -> None:
        assert corpus_stats.discover_subagent_transcripts(tmp_path / "session-dir") == []


class TestComputeCorpusStats:
    @pytest.fixture
    def synthetic_root(self, tmp_path: Path) -> Path:
        """A small synthetic corpus with the on-disk layout trap:

        - one session with a persisted plan store and no delegation
        - one session with a delegating parent (TaskCreate/TaskUpdate) and
          two subagent transcripts making tool calls
        """
        root = tmp_path / "projects"
        tasks_root = tmp_path / "tasks"

        proj = root / "-home-matt-Projects-demo"
        proj.mkdir(parents=True)

        sid_plain = "aaaaaaaa-0000-0000-0000-000000000001"
        _write_jsonl(
            proj / f"{sid_plain}.jsonl",
            [{"type": "user"}, _assistant_record([("Read", {})])],
        )
        plan_dir = tasks_root / sid_plain
        plan_dir.mkdir(parents=True)
        (plan_dir / "1.json").write_text(
            json.dumps({"subject": "do the thing", "status": "pending"})
        )

        sid_delegating = "bbbbbbbb-0000-0000-0000-000000000002"
        _write_jsonl(
            proj / f"{sid_delegating}.jsonl",
            [
                _assistant_record([("TaskCreate", {"subject": "spawn helper"})]),
                _assistant_record([("Task", {})]),
                _assistant_record([("TaskUpdate", {"taskId": "1", "status": "completed"})]),
            ],
        )
        subagents_dir = proj / sid_delegating / "subagents"
        subagents_dir.mkdir(parents=True)
        _write_jsonl(
            subagents_dir / "agent-one.jsonl",
            [_assistant_record([("Bash", {}), ("Edit", {}), ("Bash", {})])],
        )
        _write_jsonl(
            subagents_dir / "agent-two.jsonl",
            [_assistant_record([("Write", {}), ("Read", {})])],
        )

        self.root = root
        self.tasks_root = tasks_root
        return root

    def test_session_count(self, synthetic_root: Path) -> None:
        stats = corpus_stats.compute_corpus_stats(self.root, self.tasks_root)
        assert stats.session_count == 2

    def test_plan_anchor_counts_from_parent_transcripts_only(self, synthetic_root: Path) -> None:
        stats = corpus_stats.compute_corpus_stats(self.root, self.tasks_root)
        assert stats.plan_anchor_counts == {"TaskCreate": 1, "TaskUpdate": 1}

    def test_sessions_with_plan_store(self, synthetic_root: Path) -> None:
        stats = corpus_stats.compute_corpus_stats(self.root, self.tasks_root)
        assert stats.sessions_with_plan_store == 1

    def test_subagent_discovery_and_session_attribution(self, synthetic_root: Path) -> None:
        stats = corpus_stats.compute_corpus_stats(self.root, self.tasks_root)
        assert stats.subagent_transcript_count == 2
        assert stats.sessions_with_subagents == 1

    def test_delegate_tool_counts_by_tool(self, synthetic_root: Path) -> None:
        stats = corpus_stats.compute_corpus_stats(self.root, self.tasks_root)
        assert stats.delegate_tool_counts == {"Bash": 2, "Edit": 1, "Write": 1, "Read": 1}

    def test_delegate_counts_exclude_parent_task_tool_call(self, synthetic_root: Path) -> None:
        # The parent's own `Task` tool_use (the delegation marker) must not
        # be double-counted into the delegate tool histogram.
        stats = corpus_stats.compute_corpus_stats(self.root, self.tasks_root)
        assert "Task" not in stats.delegate_tool_counts

    def test_record_type_counts_cover_both_sessions(self, synthetic_root: Path) -> None:
        stats = corpus_stats.compute_corpus_stats(self.root, self.tasks_root)
        assert stats.record_type_counts["assistant"] == 4  # 1 + 3 across the two parents
        assert stats.record_type_counts["user"] == 1

    def test_empty_root_returns_zeroed_stats(self, tmp_path: Path) -> None:
        stats = corpus_stats.compute_corpus_stats(tmp_path / "missing", tmp_path / "missing-tasks")
        assert stats.session_count == 0
        assert stats.subagent_transcript_count == 0
        assert stats.delegate_tool_counts == {}


class TestFormatAndSerialise:
    def test_format_report_contains_key_sections(self) -> None:
        stats = corpus_stats.CorpusStats(session_count=1)
        stats.delegate_tool_counts["Bash"] = 5
        report = corpus_stats.format_report(stats)
        assert "session count: 1" in report
        assert "Bash" in report

    def test_stats_to_dict_round_trips_counters_as_plain_dicts(self) -> None:
        stats = corpus_stats.CorpusStats(session_count=2)
        stats.record_type_counts["user"] = 3
        as_dict = corpus_stats.stats_to_dict(stats)
        assert as_dict["session_count"] == 2
        assert as_dict["record_type_counts"] == {"user": 3}
        json.dumps(as_dict)  # must be JSON-serialisable
