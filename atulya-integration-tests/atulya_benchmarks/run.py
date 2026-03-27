from __future__ import annotations

import argparse
from pathlib import Path

from .engine import evaluate_memory_to_skill, evaluate_plain_recall
from .models import load_benchmark_definition
from .reporting import assert_expected_metrics, build_leaderboard, write_outputs


def default_scenarios_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scenarios" / "memory_to_skill_scenarios.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deterministic Atulya memory-to-skill benchmark harness.")
    parser.add_argument(
        "--scenario-file",
        type=Path,
        default=default_scenarios_path(),
        help="Path to the benchmark scenario JSON file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmark-results",
        help="Directory where leaderboard.json and leaderboard.md should be written.",
    )
    parser.add_argument(
        "--mode",
        choices=["deterministic", "live-api"],
        default="deterministic",
        help="Benchmark execution mode. 'live-api' boots a real local API and exercises HTTP plus .brain endpoints.",
    )
    parser.add_argument(
        "--scenario-limit",
        type=int,
        default=None,
        help="Optional cap for benchmark scenarios. Useful for local smoke runs of live-api mode.",
    )
    parser.add_argument(
        "--scenario-ids",
        type=str,
        default="",
        help="Comma-separated scenario IDs to run. Useful for targeted live-api smoke tests.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    definition = load_benchmark_definition(args.scenario_file)
    selected_ids = [item.strip() for item in args.scenario_ids.split(",") if item.strip()]
    scenarios = definition.scenarios
    if selected_ids:
        wanted = set(selected_ids)
        scenarios = tuple(scenario for scenario in definition.scenarios if scenario.id in wanted)
        if len(scenarios) != len(wanted):
            found = {scenario.id for scenario in scenarios}
            missing = sorted(wanted - found)
            raise SystemExit(f"Unknown scenario ids: {', '.join(missing)}")
    if args.scenario_limit:
        scenarios = scenarios[: args.scenario_limit]
    if not args.scenario_limit and len(definition.scenarios) != 24:
        raise SystemExit(f"Expected exactly 24 scenarios, found {len(definition.scenarios)}")

    if args.mode == "live-api":
        from .live_api import run_live_api_benchmark

        live_definition = definition.__class__(
            benchmark_name=definition.benchmark_name,
            expected_metrics=definition.expected_metrics,
            scenarios=scenarios,
        )
        results = run_live_api_benchmark(live_definition)
        output_dir = args.output_dir
        json_name = "leaderboard.live.json"
        markdown_name = "leaderboard.live.md"
    else:
        results = []
        for scenario in scenarios:
            results.append(evaluate_plain_recall(scenario))
            results.append(evaluate_memory_to_skill(scenario))
        output_dir = args.output_dir
        json_name = "leaderboard.json"
        markdown_name = "leaderboard.md"

    leaderboard = build_leaderboard(results)
    leaderboard["benchmark_name"] = definition.benchmark_name
    leaderboard["scenario_count"] = len(scenarios)
    leaderboard["mode"] = args.mode
    json_path, markdown_path = write_outputs(leaderboard, output_dir, json_name=json_name, markdown_name=markdown_name)
    if args.mode == "deterministic":
        assert_expected_metrics(leaderboard, definition.expected_metrics)

    print(f"Benchmark: {definition.benchmark_name}")
    print(f"Mode: {args.mode}")
    print(f"Scenario count: {len(scenarios)}")
    print(f"JSON leaderboard: {json_path}")
    print(f"Markdown leaderboard: {markdown_path}")


if __name__ == "__main__":
    main()
