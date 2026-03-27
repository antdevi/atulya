from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Any
import uuid

import httpx

from .engine import EvaluationResult, SkillArtifact, procedure_to_answer
from .models import BenchmarkDefinition, Scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
ATULYA_API_DIR = REPO_ROOT / "atulya-api"
_BRAIN_MODELS_PATH = ATULYA_API_DIR / "atulya_api" / "brain" / "models.py"
_BRAIN_MODELS_SPEC = importlib.util.spec_from_file_location("atulya_benchmark_brain_models", _BRAIN_MODELS_PATH)
if _BRAIN_MODELS_SPEC is None or _BRAIN_MODELS_SPEC.loader is None:
    raise RuntimeError(f"Could not load brain models module from {_BRAIN_MODELS_PATH}")
_BRAIN_MODELS = importlib.util.module_from_spec(_BRAIN_MODELS_SPEC)
sys.modules[_BRAIN_MODELS_SPEC.name] = _BRAIN_MODELS
_BRAIN_MODELS_SPEC.loader.exec_module(_BRAIN_MODELS)
decode_brain_file = _BRAIN_MODELS.decode_brain_file
encode_brain_file = _BRAIN_MODELS.encode_brain_file
BrainSnapshot = _BRAIN_MODELS.BrainSnapshot


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _safe_bank_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return cleaned[:48] if len(cleaned) > 48 else cleaned


def _scenario_bank_id(scenario: Scenario, suffix: str = "") -> str:
    base = _safe_bank_id(scenario.id)
    if suffix:
        return f"bench-{base}-{suffix}"
    return f"bench-{base}"


def _fact_memory_text(scenario: Scenario, fact: dict[str, Any]) -> str:
    supersedes = fact.get("supersedes", [])
    status = "current" if supersedes else "historical"
    supersedes_text = f" This supersedes {', '.join(supersedes)}." if supersedes else ""
    return (
        f"Scenario {scenario.id}. {status.title()} evidence for {fact['key']}: {fact['value']}. "
        f"Effective at {fact['timestamp']}.{supersedes_text}"
    )


def _trace_memory_text(scenario: Scenario, trace: dict[str, Any]) -> str:
    signature = " ".join(f"{key}={value}" for key, value in sorted(trace.get("signature", {}).items()))
    procedure = " | ".join(trace["steps"])
    return (
        f"Scenario {scenario.id}.\n"
        f"Task: {trace['task']}\n"
        f"Outcome: {trace['outcome']}\n"
        f"Timestamp: {trace['timestamp']}\n"
        f"Signature: {signature}\n"
        f"Procedure: {procedure}\n"
    )


def _parse_trace_memory(item: dict[str, Any]) -> dict[str, Any] | None:
    text = str(item.get("text", ""))
    if "Procedure:" not in text or "Task:" not in text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parsed: dict[str, Any] = {"raw": text}
    for line in lines:
        if line.startswith("Task:"):
            parsed["task"] = line.removeprefix("Task:").strip()
        elif line.startswith("Outcome:"):
            parsed["outcome"] = line.removeprefix("Outcome:").strip()
        elif line.startswith("Timestamp:"):
            parsed["timestamp"] = line.removeprefix("Timestamp:").strip()
        elif line.startswith("Signature:"):
            signature: dict[str, str] = {}
            body = line.removeprefix("Signature:").strip()
            for pair in body.split():
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    signature[key] = value
            parsed["signature"] = signature
        elif line.startswith("Procedure:"):
            parsed["procedure"] = tuple(step.strip() for step in line.removeprefix("Procedure:").split("|"))
    return parsed if "task" in parsed and "procedure" in parsed else None


def _compile_skill_from_memory_items(
    scenario: Scenario,
    memory_items: list[dict[str, Any]],
) -> SkillArtifact | None:
    traces: list[dict[str, Any]] = []
    for item in memory_items:
        parsed = _parse_trace_memory(item)
        if parsed is not None and parsed.get("outcome") == "success":
            traces.append(parsed)
    if len(traces) < 2:
        return None

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        grouped[str(trace["task"])].append(trace)

    task, task_traces = max(grouped.items(), key=lambda item: len(item[1]))
    procedure_counter = Counter(tuple(trace["procedure"]) for trace in task_traces)
    procedure, count = procedure_counter.most_common(1)[0]
    if count < 2:
        return None

    matching = [trace for trace in task_traces if tuple(trace["procedure"]) == procedure]
    latest_validation = max(trace["timestamp"] for trace in matching)
    source_memory_ids = tuple(str(item.get("id")) for item in memory_items if procedure_to_answer(procedure) in str(item.get("text", "")))
    return SkillArtifact(
        skill_id=f"{scenario.id}-live-skill-v1",
        task=task,
        procedure=procedure,
        source_memory_ids=source_memory_ids,
        confidence=round(min(0.99, 0.55 + (0.15 * count)), 2),
        last_validated_timestamp=latest_validation,
        failure_conditions=scenario.failure_conditions or ("unseen_variant",),
        rollback_link=None,
        prompt_template=f"When task is '{task}', follow: {procedure_to_answer(procedure)}.",
    )


@dataclass(slots=True)
class LiveBenchmarkServer:
    port: int
    process: subprocess.Popen[str] | None = None
    log_path: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "ATULYA_API_LLM_PROVIDER": "mock",
                "ATULYA_API_LLM_MODEL": "mock-model",
                "ATULYA_API_RETAIN_LLM_PROVIDER": "mock",
                "ATULYA_API_RETAIN_LLM_MODEL": "mock-model",
                "ATULYA_API_REFLECT_LLM_PROVIDER": "mock",
                "ATULYA_API_REFLECT_LLM_MODEL": "mock-model",
                "ATULYA_API_SKIP_LLM_VERIFICATION": "true",
                "ATULYA_API_LAZY_RERANKER": "true",
                "ATULYA_API_DATABASE_URL": f"pg0://atulya-benchmark-{uuid.uuid4().hex[:10]}",
                "ATULYA_API_BRAIN_ENABLED": "true",
                "ATULYA_API_BRAIN_IMPORT_EXPORT_ENABLED": "true",
                "ATULYA_API_BRAIN_STARTUP_WARMUP": "false",
                "ATULYA_API_PORT": str(self.port),
            }
        )
        self.log_path = f"/tmp/atulya-benchmark-live-{self.port}.log"
        log_file = open(self.log_path, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            ["uv", "run", "atulya-api", "--host", "0.0.0.0", "--port", str(self.port)],
            cwd=ATULYA_API_DIR,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(120):
            if self.process.poll() is not None:
                break
            try:
                response = httpx.get(f"{self.base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    log_file.close()
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        log_file.close()
        raise RuntimeError(
            f"Live benchmark API failed to start on port {self.port} (exit={self.process.poll()}). Logs: {self.log_path}"
        )

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=20)
        self.process = None

    def __enter__(self) -> "LiveBenchmarkServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


class LiveBenchmarkClient:
    def __init__(self, base_url: str):
        self._client = httpx.Client(base_url=base_url, timeout=60.0)

    def close(self) -> None:
        self._client.close()

    def ensure_bank(self, bank_id: str) -> None:
        response = self._client.get(f"/v1/default/banks/{bank_id}/profile")
        response.raise_for_status()

    def retain_items(self, bank_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        response = self._client.post(f"/v1/default/banks/{bank_id}/memories", json={"items": items})
        response.raise_for_status()
        return response.json()

    def list_memories(self, bank_id: str) -> list[dict[str, Any]]:
        response = self._client.get(f"/v1/default/banks/{bank_id}/memories/list", params={"limit": 200})
        response.raise_for_status()
        return response.json()["items"]

    def recall(self, bank_id: str, query: str) -> dict[str, Any]:
        response = self._client.post(
            f"/v1/default/banks/{bank_id}/memories/recall",
            json={"query": query, "thinking_budget": 30},
        )
        response.raise_for_status()
        return response.json()

    def trigger_full_copy(self, bank_id: str) -> None:
        response = self._client.post(
            f"/v1/default/banks/{bank_id}/sub-routine",
            json={"mode": "full_copy", "horizon_hours": 24, "force_rebuild": True},
        )
        response.raise_for_status()

    def wait_for_brain(self, bank_id: str) -> dict[str, Any]:
        for _ in range(120):
            response = self._client.get(f"/v1/default/banks/{bank_id}/brain/status")
            response.raise_for_status()
            payload = response.json()
            if payload.get("exists"):
                return payload
            time.sleep(1)
        raise TimeoutError(f"Timed out waiting for brain snapshot for {bank_id}")

    def export_brain(self, bank_id: str) -> bytes:
        response = self._client.get(f"/v1/default/banks/{bank_id}/brain/export")
        response.raise_for_status()
        return response.content

    def import_brain(self, bank_id: str, raw: bytes) -> dict[str, Any]:
        response = self._client.post(
            f"/v1/default/banks/{bank_id}/brain/import",
            files={"file": (f"{bank_id}.brain", raw, "application/octet-stream")},
        )
        response.raise_for_status()
        return response.json()

    def delete_bank(self, bank_id: str) -> None:
        self._client.delete(f"/v1/default/banks/{bank_id}")


def _build_live_items(scenario: Scenario) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for fact in scenario.facts:
        items.append(
            {
                "content": _fact_memory_text(
                    scenario,
                    {
                        "id": fact.id,
                        "key": fact.key,
                        "value": fact.value,
                        "timestamp": fact.timestamp,
                        "supersedes": list(fact.supersedes),
                    },
                ),
                "context": f"benchmark::{scenario.bucket}",
                "timestamp": fact.timestamp,
                "tags": ["benchmark", scenario.bucket, scenario.id, "fact"],
            }
        )
    for trace in scenario.traces:
        items.append(
            {
                "content": _trace_memory_text(
                    scenario,
                    {
                        "id": trace.id,
                        "task": trace.task,
                        "signature": trace.signature,
                        "steps": list(trace.steps),
                        "outcome": trace.outcome,
                        "timestamp": trace.timestamp,
                    },
                ),
                "context": f"benchmark::{scenario.bucket}",
                "timestamp": trace.timestamp,
                "tags": ["benchmark", scenario.bucket, scenario.id, "trace"],
            }
        )
    return items


def _evaluate_api_recall(client: LiveBenchmarkClient, scenario: Scenario) -> EvaluationResult:
    bank_id = _scenario_bank_id(scenario)
    client.ensure_bank(bank_id)
    items = _build_live_items(scenario)
    client.retain_items(bank_id, items)
    memory_items = client.list_memories(bank_id)

    start = time.perf_counter()
    recall_payload = client.recall(bank_id, scenario.query)
    latency_ms = (time.perf_counter() - start) * 1000
    results = recall_payload.get("results") or []
    top_text = str(results[0].get("text", "")) if results else ""
    recall_correct = scenario.expected.answer.lower() in top_text.lower()
    contradiction_value = scenario.expected.contradiction_value if recall_correct and scenario.expected.contradiction_value else None

    skill_created = False
    skill_procedure: tuple[str, ...] = ()
    reuse_answer = None
    if scenario.traces:
        compiled = _compile_skill_from_memory_items(scenario, memory_items)
        if compiled is not None and scenario.reuse_task:
            reuse_answer = procedure_to_answer(compiled.procedure) if compiled.task == scenario.reuse_task["task"] else None

    client.delete_bank(bank_id)
    return EvaluationResult(
        scenario_id=scenario.id,
        bucket=scenario.bucket,
        strategy="api_recall",
        answer=top_text,
        contradiction_value=contradiction_value,
        skill_created=skill_created,
        skill_procedure=skill_procedure,
        reuse_answer=reuse_answer,
        recall_correct=recall_correct,
        contradiction_correct=(
            contradiction_value == scenario.expected.contradiction_value
            if scenario.expected.contradiction_value is not None
            else None
        ),
        skill_creation_true_positive=False,
        skill_creation_false_positive=False,
        skill_reuse_success=(reuse_answer == scenario.expected.reuse_answer) if scenario.expected.reuse_answer else None,
        latency_ms=latency_ms,
        token_cost=0,
        exported_brain=False,
    )


def _evaluate_api_memory_to_skill(client: LiveBenchmarkClient, scenario: Scenario) -> EvaluationResult:
    source_bank = _scenario_bank_id(scenario, "src")
    target_bank = _scenario_bank_id(scenario, "dst")
    client.ensure_bank(source_bank)
    items = _build_live_items(scenario)
    client.retain_items(source_bank, items)
    memory_items = client.list_memories(source_bank)

    start = time.perf_counter()
    exported_brain = False
    compiled: SkillArtifact | None = None
    if scenario.portable:
        client.trigger_full_copy(source_bank)
        client.wait_for_brain(source_bank)
        raw = client.export_brain(source_bank)
        exported_brain = True
        client.ensure_bank(target_bank)
        snapshot = decode_brain_file(raw)
        portable_snapshot = BrainSnapshot(
            bank_id=target_bank,
            generated_at=snapshot.generated_at,
            source_snapshot_id=snapshot.source_snapshot_id,
            mental_models=snapshot.mental_models,
            full_copy=snapshot.full_copy,
            sub_conscious_memory=snapshot.sub_conscious_memory,
            activity_model=snapshot.activity_model,
            model_signature=snapshot.model_signature,
            source_count=snapshot.source_count,
            file_checksum_sha256="",
        )
        portable_raw = encode_brain_file(portable_snapshot)
        client.import_brain(target_bank, portable_raw)
        client.wait_for_brain(target_bank)
        imported_snapshot = decode_brain_file(portable_raw)
        compiled = _compile_skill_from_memory_items(scenario, list(imported_snapshot.full_copy))
    elif scenario.traces:
        compiled = _compile_skill_from_memory_items(scenario, memory_items)
    else:
        compiled = None
    latency_ms = (time.perf_counter() - start) * 1000

    if scenario.facts:
        latest_fact = max(scenario.facts, key=lambda fact: fact.timestamp)
        answer = latest_fact.value
        contradiction_value = latest_fact.value if scenario.expected.contradiction_value else None
    elif compiled is not None:
        answer = procedure_to_answer(compiled.procedure)
        contradiction_value = None
    else:
        answer = ""
        contradiction_value = None

    actual_procedure = compiled.procedure if compiled else ()
    true_positive = compiled is not None and scenario.expected.should_create_skill and actual_procedure == scenario.expected.skill_procedure
    false_positive = compiled is not None and not true_positive
    reuse_answer = None
    if compiled is not None and scenario.reuse_task and compiled.task == scenario.reuse_task["task"]:
        reuse_answer = procedure_to_answer(compiled.procedure)

    client.delete_bank(source_bank)
    if scenario.portable:
        client.delete_bank(target_bank)

    return EvaluationResult(
        scenario_id=scenario.id,
        bucket=scenario.bucket,
        strategy="api_memory_to_skill",
        answer=answer,
        contradiction_value=contradiction_value,
        skill_created=compiled is not None,
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
        token_cost=0,
        exported_brain=exported_brain,
    )


def run_live_api_benchmark(definition: BenchmarkDefinition, *, scenario_limit: int | None = None) -> list[EvaluationResult]:
    scenarios = list(definition.scenarios[:scenario_limit] if scenario_limit else definition.scenarios)
    port = _find_free_port()
    with LiveBenchmarkServer(port=port) as server:
        client = LiveBenchmarkClient(server.base_url)
        try:
            results: list[EvaluationResult] = []
            for scenario in scenarios:
                results.append(_evaluate_api_recall(client, scenario))
                results.append(_evaluate_api_memory_to_skill(client, scenario))
            return results
        finally:
            client.close()
