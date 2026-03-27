from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import json

from .engine import EvaluationResult


@dataclass(frozen=True, slots=True)
class MetricSummary:
    scenario_count: int
    recall_accuracy: float
    contradiction_resolution_accuracy: float | None
    skill_creation_precision: float
    skill_reuse_success_rate: float | None
    time_to_useful_answer_ms: float | None
    token_cost_per_successful_action: float | None

    def to_payload(self) -> dict[str, object]:
        return {
            "scenario_count": self.scenario_count,
            "recall_accuracy": round(self.recall_accuracy, 4),
            "contradiction_resolution_accuracy": (
                round(self.contradiction_resolution_accuracy, 4)
                if self.contradiction_resolution_accuracy is not None
                else None
            ),
            "skill_creation_precision": round(self.skill_creation_precision, 4),
            "skill_reuse_success_rate": (
                round(self.skill_reuse_success_rate, 4) if self.skill_reuse_success_rate is not None else None
            ),
            "time_to_useful_answer_ms": (
                round(self.time_to_useful_answer_ms, 4) if self.time_to_useful_answer_ms is not None else None
            ),
            "token_cost_per_successful_action": (
                round(self.token_cost_per_successful_action, 4)
                if self.token_cost_per_successful_action is not None
                else None
            ),
        }


def summarize(results: list[EvaluationResult]) -> MetricSummary:
    scenario_count = len(results)
    recall_hits = sum(1 for result in results if result.recall_correct)
    contradiction_values = [result for result in results if result.contradiction_correct is not None]
    contradiction_hits = sum(1 for result in contradiction_values if result.contradiction_correct)
    true_positives = sum(1 for result in results if result.skill_creation_true_positive)
    false_positives = sum(1 for result in results if result.skill_creation_false_positive)
    reuse_values = [result for result in results if result.skill_reuse_success is not None]
    reuse_hits = sum(1 for result in reuse_values if result.skill_reuse_success)
    successful = [result for result in results if result.recall_correct]
    recall_accuracy = recall_hits / scenario_count if scenario_count else 0.0
    contradiction_accuracy = contradiction_hits / len(contradiction_values) if contradiction_values else None
    skill_precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) else 0.0
    reuse_success = reuse_hits / len(reuse_values) if reuse_values else None
    avg_time = sum(result.latency_ms for result in successful) / len(successful) if successful else None
    avg_token_cost = sum(result.token_cost for result in successful) / len(successful) if successful else None
    return MetricSummary(
        scenario_count=scenario_count,
        recall_accuracy=recall_accuracy,
        contradiction_resolution_accuracy=contradiction_accuracy,
        skill_creation_precision=skill_precision,
        skill_reuse_success_rate=reuse_success,
        time_to_useful_answer_ms=avg_time,
        token_cost_per_successful_action=avg_token_cost,
    )


def build_leaderboard(results: list[EvaluationResult]) -> dict[str, object]:
    grouped: dict[str, list[EvaluationResult]] = defaultdict(list)
    bucketed: dict[str, dict[str, list[EvaluationResult]]] = defaultdict(lambda: defaultdict(list))

    for result in results:
        grouped[result.strategy].append(result)
        bucketed[result.strategy][result.bucket].append(result)

    strategies: dict[str, dict[str, object]] = {}
    for strategy, strategy_results in grouped.items():
        strategies[strategy] = {
            "overall": summarize(strategy_results).to_payload(),
            "buckets": {
                bucket: summarize(bucket_results).to_payload()
                for bucket, bucket_results in sorted(bucketed[strategy].items())
            },
            "scenarios": [result.to_payload() for result in strategy_results],
        }
    return {"strategies": strategies}


def assert_expected_metrics(leaderboard: dict[str, object], expectations: dict[str, dict]) -> None:
    strategies = leaderboard["strategies"]
    failures: list[str] = []
    for strategy, strategy_expectation in expectations.items():
        if strategy not in strategies:
            failures.append(f"Missing strategy '{strategy}' in leaderboard output")
            continue
        actual_strategy = strategies[strategy]
        overall_expected = strategy_expectation.get("overall", {})
        overall_actual = actual_strategy["overall"]
        for metric_name, expected_value in overall_expected.items():
            actual_value = overall_actual.get(metric_name)
            if actual_value is None or actual_value < expected_value:
                failures.append(
                    f"{strategy}.overall.{metric_name} expected >= {expected_value}, got {actual_value}"
                )
        for bucket_name, bucket_expectation in strategy_expectation.get("buckets", {}).items():
            actual_bucket = actual_strategy["buckets"].get(bucket_name)
            if actual_bucket is None:
                failures.append(f"Missing bucket '{bucket_name}' for strategy '{strategy}'")
                continue
            for metric_name, expected_value in bucket_expectation.items():
                actual_value = actual_bucket.get(metric_name)
                if actual_value is None or actual_value < expected_value:
                    failures.append(
                        f"{strategy}.buckets.{bucket_name}.{metric_name} expected >= {expected_value}, got {actual_value}"
                    )
    if failures:
        raise SystemExit("Benchmark regression detected:\n- " + "\n- ".join(failures))


def write_outputs(
    leaderboard: dict[str, object],
    output_dir: Path,
    *,
    json_name: str = "leaderboard.json",
    markdown_name: str = "leaderboard.md",
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / json_name
    markdown_path = output_dir / markdown_name
    json_path.write_text(json.dumps(leaderboard, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(render_markdown(leaderboard), encoding="utf-8")
    return json_path, markdown_path


def render_markdown(leaderboard: dict[str, object]) -> str:
    lines = [
        "# Memory-to-Skill Benchmark Leaderboard",
        "",
        "## Overall",
        "",
        "| Strategy | Recall | Contradiction | Skill Precision | Skill Reuse | Avg Time (ms) | Avg Tokens / Success |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    strategies = leaderboard["strategies"]
    for strategy, payload in sorted(strategies.items()):
        overall = payload["overall"]
        lines.append(
            f"| `{strategy}` | {overall['recall_accuracy']:.2f} | "
            f"{_fmt(overall['contradiction_resolution_accuracy'])} | "
            f"{overall['skill_creation_precision']:.2f} | "
            f"{_fmt(overall['skill_reuse_success_rate'])} | "
            f"{_fmt(overall['time_to_useful_answer_ms'])} | "
            f"{_fmt(overall['token_cost_per_successful_action'])} |"
        )

    lines.extend(["", "## Bucket Breakdown", ""])
    for strategy, payload in sorted(strategies.items()):
        lines.extend(
            [
                f"### `{strategy}`",
                "",
                "| Bucket | Recall | Contradiction | Skill Precision | Skill Reuse | Avg Time (ms) | Avg Tokens / Success |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for bucket, summary in sorted(payload["buckets"].items()):
            lines.append(
                f"| `{bucket}` | {summary['recall_accuracy']:.2f} | "
                f"{_fmt(summary['contradiction_resolution_accuracy'])} | "
                f"{summary['skill_creation_precision']:.2f} | "
                f"{_fmt(summary['skill_reuse_success_rate'])} | "
                f"{_fmt(summary['time_to_useful_answer_ms'])} | "
                f"{_fmt(summary['token_cost_per_successful_action'])} |"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"
