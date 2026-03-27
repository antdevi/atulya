from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json


@dataclass(frozen=True, slots=True)
class FactEvent:
    id: str
    key: str
    value: str
    timestamp: str
    supersedes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TraceEvent:
    id: str
    task: str
    signature: dict[str, str]
    steps: tuple[str, ...]
    outcome: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class ScenarioExpected:
    answer: str
    contradiction_value: str | None = None
    should_create_skill: bool = False
    skill_procedure: tuple[str, ...] = ()
    reuse_answer: str | None = None


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    bucket: str
    title: str
    query: str
    expected: ScenarioExpected
    facts: tuple[FactEvent, ...] = ()
    traces: tuple[TraceEvent, ...] = ()
    reuse_task: dict[str, str] | None = None
    failure_conditions: tuple[str, ...] = ()
    portable: bool = False


@dataclass(frozen=True, slots=True)
class BenchmarkDefinition:
    benchmark_name: str
    expected_metrics: dict[str, dict]
    scenarios: tuple[Scenario, ...]


def load_benchmark_definition(path: Path) -> BenchmarkDefinition:
    raw = json.loads(path.read_text(encoding="utf-8"))
    scenarios = []
    for item in raw["scenarios"]:
        facts = tuple(
            FactEvent(
                id=fact["id"],
                key=fact["key"],
                value=fact["value"],
                timestamp=fact["timestamp"],
                supersedes=tuple(fact.get("supersedes", [])),
            )
            for fact in item.get("facts", [])
        )
        traces = tuple(
            TraceEvent(
                id=trace["id"],
                task=trace["task"],
                signature=dict(trace.get("signature", {})),
                steps=tuple(trace["steps"]),
                outcome=trace["outcome"],
                timestamp=trace["timestamp"],
            )
            for trace in item.get("traces", [])
        )
        expected_raw = item["expected"]
        expected = ScenarioExpected(
            answer=expected_raw["answer"],
            contradiction_value=expected_raw.get("contradiction_value"),
            should_create_skill=expected_raw.get("should_create_skill", False),
            skill_procedure=tuple(expected_raw.get("skill_procedure", [])),
            reuse_answer=expected_raw.get("reuse_answer"),
        )
        scenarios.append(
            Scenario(
                id=item["id"],
                bucket=item["bucket"],
                title=item["title"],
                query=item["query"],
                facts=facts,
                traces=traces,
                reuse_task=dict(item["reuse_task"]) if item.get("reuse_task") else None,
                failure_conditions=tuple(item.get("failure_conditions", [])),
                portable=item.get("portable", False),
                expected=expected,
            )
        )
    return BenchmarkDefinition(
        benchmark_name=raw["benchmark_name"],
        expected_metrics=raw.get("expected_metrics", {}),
        scenarios=tuple(scenarios),
    )
