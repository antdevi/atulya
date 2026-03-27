from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from .models import FactEvent, Scenario, TraceEvent


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def estimate_tokens(*parts: str) -> int:
    text = " ".join(part for part in parts if part).strip()
    if not text:
        return 0
    return max(1, sum(len(chunk.split()) for chunk in text.splitlines()))


def procedure_to_answer(steps: tuple[str, ...]) -> str:
    return " -> ".join(steps)


@dataclass(slots=True)
class SkillArtifact:
    skill_id: str
    task: str
    procedure: tuple[str, ...]
    source_memory_ids: tuple[str, ...]
    confidence: float
    last_validated_timestamp: str
    failure_conditions: tuple[str, ...]
    rollback_link: str | None
    prompt_template: str
    version: int = 1

    def to_payload(self) -> dict[str, object]:
        return {
            "artifact_type": "compiled_skill",
            "skill_id": self.skill_id,
            "task": self.task,
            "procedure": list(self.procedure),
            "source_memory_ids": list(self.source_memory_ids),
            "confidence": self.confidence,
            "last_validated_timestamp": self.last_validated_timestamp,
            "failure_conditions": list(self.failure_conditions),
            "rollback_link": self.rollback_link,
            "prompt_template": self.prompt_template,
            "version": self.version,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "SkillArtifact":
        return cls(
            skill_id=str(payload["skill_id"]),
            task=str(payload["task"]),
            procedure=tuple(payload["procedure"]),
            source_memory_ids=tuple(payload["source_memory_ids"]),
            confidence=float(payload["confidence"]),
            last_validated_timestamp=str(payload["last_validated_timestamp"]),
            failure_conditions=tuple(payload["failure_conditions"]),
            rollback_link=str(payload["rollback_link"]) if payload.get("rollback_link") else None,
            prompt_template=str(payload["prompt_template"]),
            version=int(payload.get("version", 1)),
        )


@dataclass(slots=True)
class EvaluationResult:
    scenario_id: str
    bucket: str
    strategy: str
    answer: str
    contradiction_value: str | None
    skill_created: bool
    skill_procedure: tuple[str, ...]
    reuse_answer: str | None
    recall_correct: bool
    contradiction_correct: bool | None
    skill_creation_true_positive: bool
    skill_creation_false_positive: bool
    skill_reuse_success: bool | None
    latency_ms: float
    token_cost: int
    exported_brain: bool

    def to_payload(self) -> dict[str, object]:
        data = asdict(self)
        data["skill_procedure"] = list(self.skill_procedure)
        return data


def _choose_latest_fact(facts: tuple[FactEvent, ...]) -> FactEvent:
    superseded_ids = {superseded for fact in facts for superseded in fact.supersedes}
    candidates = [fact for fact in facts if fact.id not in superseded_ids]
    return max(candidates or list(facts), key=lambda fact: parse_timestamp(fact.timestamp))


def _choose_majority_fact(facts: tuple[FactEvent, ...]) -> FactEvent:
    counts = Counter(fact.value for fact in facts)
    ranking = sorted(
        facts,
        key=lambda fact: (
            counts[fact.value],
            -parse_timestamp(fact.timestamp).timestamp(),
        ),
        reverse=True,
    )
    return ranking[0]


def _latest_success_for_exact_signature(traces: tuple[TraceEvent, ...], reuse_task: dict[str, str] | None) -> TraceEvent | None:
    if reuse_task is None:
        successful = [trace for trace in traces if trace.outcome == "success"]
        return max(successful, key=lambda trace: parse_timestamp(trace.timestamp), default=None)
    expected_signature = {key: value for key, value in reuse_task.items() if key != "task"}
    successful = [
        trace
        for trace in traces
        if trace.outcome == "success" and trace.task == reuse_task["task"] and trace.signature == expected_signature
    ]
    return max(successful, key=lambda trace: parse_timestamp(trace.timestamp), default=None)


def _compile_skill(scenario: Scenario) -> SkillArtifact | None:
    successful = [trace for trace in scenario.traces if trace.outcome == "success"]
    if len(successful) < 2:
        return None

    grouped: dict[str, list[TraceEvent]] = defaultdict(list)
    for trace in successful:
        grouped[trace.task].append(trace)

    task, traces = max(grouped.items(), key=lambda item: len(item[1]))
    procedure_counter = Counter(trace.steps for trace in traces)
    procedure, count = procedure_counter.most_common(1)[0]
    if count < 2:
        return None

    source_memory_ids = tuple(trace.id for trace in traces if trace.steps == procedure)
    latest_validation = max((trace.timestamp for trace in traces if trace.steps == procedure), key=parse_timestamp)
    prompt_template = f"When task is '{task}', follow: {procedure_to_answer(procedure)}."
    return SkillArtifact(
        skill_id=f"{scenario.id}-skill-v1",
        task=task,
        procedure=procedure,
        source_memory_ids=source_memory_ids,
        confidence=round(min(0.99, 0.55 + (0.15 * count)), 2),
        last_validated_timestamp=latest_validation,
        failure_conditions=scenario.failure_conditions or ("unseen_variant", "stale_evidence"),
        rollback_link=None,
        prompt_template=prompt_template,
    )


def _export_and_import_skill(artifact: SkillArtifact) -> SkillArtifact:
    with TemporaryDirectory(prefix="atulya-brain-") as tmpdir:
        path = Path(tmpdir) / f"{artifact.skill_id}.brain"
        path.write_text(json.dumps(artifact.to_payload(), indent=2, sort_keys=True), encoding="utf-8")
        imported = json.loads(path.read_text(encoding="utf-8"))
        return SkillArtifact.from_payload(imported)


def evaluate_plain_recall(scenario: Scenario) -> EvaluationResult:
    start = perf_counter()
    answer = ""
    contradiction_value = None
    reuse_answer = None

    if scenario.facts:
        selected = _choose_majority_fact(scenario.facts)
        answer = selected.value
        contradiction_value = selected.value if scenario.expected.contradiction_value else None
    elif scenario.traces:
        latest_trace = _latest_success_for_exact_signature(scenario.traces, scenario.reuse_task)
        if latest_trace is None and scenario.reuse_task is None:
            successful = [trace for trace in scenario.traces if trace.outcome == "success"]
            latest_trace = max(successful, key=lambda trace: parse_timestamp(trace.timestamp), default=None)
        if latest_trace is not None:
            answer = procedure_to_answer(latest_trace.steps)
            if scenario.reuse_task:
                expected_signature = {key: value for key, value in scenario.reuse_task.items() if key != "task"}
                if latest_trace.signature == expected_signature:
                    reuse_answer = answer
    latency_ms = (perf_counter() - start) * 1000
    token_cost = estimate_tokens(
        scenario.query,
        answer,
        *(fact.value for fact in scenario.facts),
        *(step for trace in scenario.traces for step in trace.steps),
    )
    return EvaluationResult(
        scenario_id=scenario.id,
        bucket=scenario.bucket,
        strategy="plain_recall",
        answer=answer,
        contradiction_value=contradiction_value,
        skill_created=False,
        skill_procedure=(),
        reuse_answer=reuse_answer,
        recall_correct=answer == scenario.expected.answer,
        contradiction_correct=(
            contradiction_value == scenario.expected.contradiction_value
            if scenario.expected.contradiction_value is not None
            else None
        ),
        skill_creation_true_positive=False,
        skill_creation_false_positive=False,
        skill_reuse_success=(reuse_answer == scenario.expected.reuse_answer) if scenario.expected.reuse_answer else None,
        latency_ms=latency_ms,
        token_cost=token_cost,
        exported_brain=False,
    )


def evaluate_memory_to_skill(scenario: Scenario) -> EvaluationResult:
    start = perf_counter()
    answer = ""
    contradiction_value = None
    skill = None
    reuse_answer = None
    exported_brain = False

    if scenario.facts:
        selected = _choose_latest_fact(scenario.facts)
        answer = selected.value
        contradiction_value = selected.value if scenario.expected.contradiction_value else None
    elif scenario.traces:
        skill = _compile_skill(scenario)
        if skill is not None:
            if scenario.portable:
                skill = _export_and_import_skill(skill)
                exported_brain = True
            answer = procedure_to_answer(skill.procedure)
            if scenario.reuse_task and skill.task == scenario.reuse_task["task"]:
                reuse_answer = answer
        else:
            latest_trace = max(
                (trace for trace in scenario.traces if trace.outcome == "success"),
                key=lambda trace: parse_timestamp(trace.timestamp),
                default=None,
            )
            if latest_trace is not None:
                answer = procedure_to_answer(latest_trace.steps)
    latency_ms = (perf_counter() - start) * 1000
    token_cost = estimate_tokens(
        scenario.query,
        answer,
        *(fact.value for fact in scenario.facts),
        *(step for trace in scenario.traces for step in trace.steps),
    )

    actual_procedure = skill.procedure if skill else ()
    true_positive = skill is not None and scenario.expected.should_create_skill and actual_procedure == scenario.expected.skill_procedure
    false_positive = skill is not None and not true_positive
    return EvaluationResult(
        scenario_id=scenario.id,
        bucket=scenario.bucket,
        strategy="memory_to_skill",
        answer=answer,
        contradiction_value=contradiction_value,
        skill_created=skill is not None,
        skill_procedure=actual_procedure,
        reuse_answer=reuse_answer,
        recall_correct=answer == scenario.expected.answer,
        contradiction_correct=(
            contradiction_value == scenario.expected.contradiction_value
            if scenario.expected.contradiction_value is not None
            else None
        ),
        skill_creation_true_positive=true_positive,
        skill_creation_false_positive=false_positive,
        skill_reuse_success=(reuse_answer == scenario.expected.reuse_answer) if scenario.expected.reuse_answer else None,
        latency_ms=latency_ms,
        token_cost=token_cost,
        exported_brain=exported_brain,
    )
