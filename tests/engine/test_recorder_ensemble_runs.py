"""Tests for ensemble run boundary recording in SimulationRecorder."""

import json

from ripple.engine.recorder import SimulationRecorder
from ripple.primitives.models import OmniscientVerdict


def test_recorder_ensemble_runs_are_isolated(tmp_path):
    out = tmp_path / "ensemble.json"
    recorder = SimulationRecorder(output_path=out, run_id="r0")

    # Run 1
    recorder.begin_ensemble_run(run_index=0, run_id="r0r1", random_seed=1)
    recorder.record_wave_start(0, {"pre": 1})
    verdict = OmniscientVerdict(
        wave_number=0,
        simulated_time_elapsed="0h",
        simulated_time_remaining="0h",
        continue_propagation=False,
        activated_agents=[],
        skipped_agents=[],
        global_observation="done",
        termination_reason="test",
    )
    recorder.record_wave_end(
        0,
        verdict=verdict,
        agent_responses={},
        post_snapshot={"post": 1},
        terminated=True,
    )
    recorder.end_ensemble_run()

    # Run 2
    recorder.begin_ensemble_run(run_index=1, run_id="r0r2", random_seed=2)
    recorder.record_wave_start(0, {"pre": 2})
    recorder.record_wave_end(
        0,
        verdict=verdict,
        agent_responses={},
        post_snapshot={"post": 2},
        terminated=True,
    )
    recorder.end_ensemble_run()

    data = json.loads(out.read_text(encoding="utf-8"))

    # Top-level process.waves should remain empty (runs are stored under process.ensemble_runs)
    assert data["process"]["waves"] == []

    runs = data["process"]["ensemble_runs"]
    assert len(runs) == 2
    assert runs[0]["run_index"] == 0
    assert runs[0]["random_seed"] == 1
    assert len(runs[0]["process"]["waves"]) == 1
    assert runs[0]["process"]["waves"][0]["wave_number"] == 0

    assert runs[1]["run_index"] == 1
    assert runs[1]["random_seed"] == 2
    assert len(runs[1]["process"]["waves"]) == 1
    assert runs[1]["process"]["waves"][0]["pre_snapshot"]["pre"] == 2

