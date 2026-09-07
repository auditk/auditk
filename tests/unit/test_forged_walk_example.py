"""Tests for examples/forged-walk: invariants everywhere, live demo when langgraph is present."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_DEMO = Path(__file__).resolve().parents[2] / "examples" / "forged-walk" / "forged_walk_demo.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("forged_walk_demo", _DEMO)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["forged_walk_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


_HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


class TestCleanRoomInvariants:
    """Pure-function checks: run everywhere, no langgraph needed."""

    def test_good_trace_is_clean(self) -> None:
        demo = _load_demo()
        trace = ["fetch", "analyze", "review", "human_approval", "apply", "halt"]
        assert demo.check_trace_clean_room(trace) == []

    def test_spend_without_gate_is_flagged(self) -> None:
        demo = _load_demo()
        assert demo.check_trace_clean_room(["fetch", "analyze", "apply", "halt"]) == [
            "unconstrained_spend"
        ]

    def test_missing_halt_is_flagged(self) -> None:
        demo = _load_demo()
        trace = ["fetch", "analyze", "review", "human_approval", "apply"]
        assert demo.check_trace_clean_room(trace) == ["halt_not_reached"]

    def test_forged_trace_passes_from_state_alone(self) -> None:
        # The point of the demo: the forged self-report is indistinguishable
        # from an honest walk when all you have is the state-recorded trace.
        demo = _load_demo()
        forged = ["fetch", "analyze", "review", "human_approval", "apply", "halt"]
        assert demo.check_trace_clean_room(forged) == []

    def test_gate_in_prefix_only_still_passes(self) -> None:
        # Known limitation of prefix-presence invariants, documented in the
        # example README: a rejected or off-path gate still licenses the spend.
        demo = _load_demo()
        assert demo.check_trace_clean_room(["human_approval", "apply", "halt"]) == []


@pytest.mark.skipif(not _HAS_LANGGRAPH, reason="requires the [langgraph] extra")
class TestLiveDemo:
    """The pre-registered two-condition run, against the bundled checker."""

    def test_forgery_invisible_from_state_visible_from_runtime(self) -> None:
        demo = _load_demo()
        r = demo.run_demo(demo.check_trace_clean_room)
        # Condition A: the forged state trace passes clean.
        assert r["violations_state"] == []
        assert all(n in r["state_trace"] for n in demo.MUST_INCLUDE)
        # ...while no gate node actually executed.
        assert r["gates_fired"] is False
        # Condition B: the runtime's own record of the same run fails.
        assert r["violations_scheduler"] == ["unconstrained_spend"]
        assert r["supported"] is True
