"""Red-phase tests for benchmark/task.py.

Tests the BenchmarkTask dataclass and the BENCHMARK_TASKS fixture list.
"""

from auditk.benchmark.task import BENCHMARK_TASKS, BenchmarkTask

_FULL_TOOL_SET = {"ReadFile", "WriteFile", "TodoWrite", "Report"}


def test_benchmark_task_fields_exist() -> None:
    task = BenchmarkTask(
        task_id="test-id",
        name="test name",
        system_prompt="system",
        user_prompt="user",
        seed="baseline",
        tools=["ReadFile"],
    )
    assert task.task_id == "test-id"
    assert task.name == "test name"
    assert task.system_prompt == "system"
    assert task.user_prompt == "user"
    assert task.seed == "baseline"
    assert task.tools == ["ReadFile"]


def test_benchmark_tasks_has_ten_items() -> None:
    assert len(BENCHMARK_TASKS) == 10


def test_benchmark_tasks_seeds_are_unique() -> None:
    seeds = [t.seed for t in BENCHMARK_TASKS]
    assert len(seeds) == len(set(seeds)), "seeds must be unique"


def test_benchmark_tasks_tools_are_subset_of_full_tool_set() -> None:
    for task in BENCHMARK_TASKS:
        assert set(task.tools) <= _FULL_TOOL_SET, f"{task.task_id}: unknown tool(s)"
        assert len(task.tools) >= 2, f"{task.task_id}: must have at least 2 tools"


def test_benchmark_tasks_all_prompts_start_with_todo_write() -> None:
    for task in BENCHMARK_TASKS:
        assert "TodoWrite" in task.user_prompt, (
            f"{task.task_id}: user_prompt must mention TodoWrite"
        )


def test_baseline_task_has_correct_id_and_name() -> None:
    baseline = next(t for t in BENCHMARK_TASKS if t.seed == "baseline")
    assert baseline.task_id == "audit-baseline"
    assert baseline.name == "Codebase Audit & Improvement Plan (baseline)"


def test_reversed_task_has_correct_id_and_name() -> None:
    reversed_task = next(t for t in BENCHMARK_TASKS if t.seed == "reversed")
    assert reversed_task.task_id == "audit-reversed"
    assert reversed_task.name == "Codebase Audit & Improvement Plan (reversed)"


def test_distractor_task_has_correct_id_and_name() -> None:
    distractor = next(t for t in BENCHMARK_TASKS if t.seed == "distractor")
    assert distractor.task_id == "audit-distractor"
    assert distractor.name == "Codebase Audit & Improvement Plan (distractor)"


def test_baseline_task_mentions_planning_first() -> None:
    baseline = next(t for t in BENCHMARK_TASKS if t.seed == "baseline")
    assert "plan your audit before starting" in baseline.user_prompt.lower()


def test_reversed_task_mentions_write_report_first() -> None:
    reversed_task = next(t for t in BENCHMARK_TASKS if t.seed == "reversed")
    assert (
        "write" in reversed_task.user_prompt.lower()
        and "report" in reversed_task.user_prompt.lower()
    )


def test_distractor_task_mentions_distractor_file() -> None:
    distractor = next(t for t in BENCHMARK_TASKS if t.seed == "distractor")
    assert "unrelated_script.py" in distractor.user_prompt.lower()
