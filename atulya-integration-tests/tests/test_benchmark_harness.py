from __future__ import annotations

from pathlib import Path

from atulya_benchmarks.engine import evaluate_memory_to_skill, evaluate_plain_recall
from atulya_benchmarks.models import load_benchmark_definition
from atulya_benchmarks.reporting import assert_expected_metrics, build_leaderboard, write_outputs
from atulya_benchmarks.run import default_scenarios_path


def test_benchmark_definition_has_expected_coverage():
    definition = load_benchmark_definition(default_scenarios_path())

    assert len(definition.scenarios) == 24

    buckets = {}
    for scenario in definition.scenarios:
        buckets[scenario.bucket] = buckets.get(scenario.bucket, 0) + 1

    assert buckets == {
        "temporal_correction": 6,
        "contradiction_handling": 6,
        "skill_emergence": 6,
        "portability": 6,
    }


def test_benchmark_runner_writes_artifacts_and_meets_contract(tmp_path: Path):
    definition = load_benchmark_definition(default_scenarios_path())

    results = []
    for scenario in definition.scenarios:
        results.append(evaluate_plain_recall(scenario))
        results.append(evaluate_memory_to_skill(scenario))

    leaderboard = build_leaderboard(results)
    leaderboard["benchmark_name"] = definition.benchmark_name
    leaderboard["scenario_count"] = len(definition.scenarios)
    json_path, markdown_path = write_outputs(leaderboard, tmp_path)

    assert json_path.exists()
    assert markdown_path.exists()
    assert "memory_to_skill" in json_path.read_text(encoding="utf-8")
    assert "Memory-to-Skill Benchmark Leaderboard" in markdown_path.read_text(encoding="utf-8")

    assert_expected_metrics(leaderboard, definition.expected_metrics)
