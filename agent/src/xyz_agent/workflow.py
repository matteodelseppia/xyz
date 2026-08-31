from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

import structlog
from langgraph.graph import END, START, StateGraph

from .config import Settings
from .models import Evidence, Finding, FindingCategory, Publication, Review, RunResult
from .publish import LockContended, Publisher
from .render import TEMPLATE_VERSION, Renderer
from .runtime import AgentRuntime, PiRuntime, RoleSession, RuntimeFailure
from .validate import Validator

log = structlog.get_logger()


class GraphState(TypedDict, total=False):
    run_id: str
    publication_date: str
    registry: dict[str, Any]
    evidence: list[dict[str, Any]]
    diagnostics: list[str]
    publication: dict[str, Any]
    reviews: list[dict[str, Any]]
    feedback: list[dict[str, Any]]
    iteration: int
    max_iterations: int
    review_status: Literal["approved", "iteration_limit"]
    rendered: Any
    retained_dates: list[str]
    provenance: dict[str, str]
    terminal_status: Literal["running", "published", "failed"]
    failure: str
    run_prefix: str
    warnings: list[str]


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _merge_evidence(
    current: list[dict[str, Any]], additions: list[Evidence]
) -> list[dict[str, Any]]:
    merged = {item["id"]: item for item in current}
    for item in additions:
        merged[item.id] = item.model_dump(mode="json")
    return list(merged.values())


class DigestWorkflow:
    def __init__(
        self,
        settings: Settings,
        runtime: AgentRuntime | None,
        publisher: Publisher,
        renderer: Renderer | None = None,
        validator: Validator | None = None,
    ) -> None:
        self.settings = settings
        self.runtime = runtime or PiRuntime(settings)
        self.publisher = publisher
        self.renderer = renderer or Renderer(settings.template_dir)
        self.validator = validator or Validator()
        self.producer: RoleSession | None = None
        self.reviewer: RoleSession | None = None

    async def _produce(self, state: GraphState) -> dict[str, Any]:
        assert self.producer is not None
        if state.get("feedback"):
            prompt = json.dumps(
                {
                    "task": "revise_candidate",
                    "publication_date": state["publication_date"],
                    "candidate": state.get("publication"),
                    "latest_findings": state["feedback"],
                    "evidence": state["evidence"],
                },
                separators=(",", ":"),
            )
        else:
            prompt = json.dumps(
                {
                    "task": "produce_daily_digest",
                    "publication_date": state["publication_date"],
                    "source_registry": state["registry"],
                },
                separators=(",", ":"),
            )
        diagnostics = list(state.get("diagnostics", []))
        evidence = list(state.get("evidence", []))
        for malformed_attempt in range(self.settings.malformed_retries + 1):
            result = await self.producer.prompt(prompt)
            evidence = _merge_evidence(evidence, result.evidence)
            diagnostics.extend(result.diagnostics)
            if isinstance(result.terminal, Publication):
                candidate = result.terminal
                if candidate.publication_date.isoformat() != state["publication_date"]:
                    diagnostics.append("producer returned the wrong publication date")
                elif evidence:
                    return {
                        "publication": candidate.model_dump(mode="json"),
                        "evidence": evidence,
                        "diagnostics": diagnostics,
                        "iteration": state.get("iteration", 0) + 1,
                        "feedback": [],
                    }
            prompt = json.dumps(
                {
                    "task": "retry_malformed_output",
                    "attempt": malformed_attempt + 1,
                    "errors": diagnostics[-3:],
                    "instruction": "Call submit_publication once with a valid artifact.",
                },
                separators=(",", ":"),
            )
        return {
            "terminal_status": "failed",
            "failure": "producer did not return a valid artifact with usable evidence",
            "diagnostics": diagnostics,
            "evidence": evidence,
        }

    async def _review(self, state: GraphState) -> dict[str, Any]:
        assert self.reviewer is not None
        candidate = Publication.model_validate(state["publication"])
        prompt = json.dumps(
            {
                "task": "review_candidate",
                "source_registry": state["registry"],
                "evidence": state["evidence"],
                "candidate": state["publication"],
            },
            separators=(",", ":"),
        )
        evidence = list(state["evidence"])
        diagnostics = list(state.get("diagnostics", []))
        for malformed_attempt in range(self.settings.malformed_retries + 1):
            result = await self.reviewer.prompt(prompt)
            evidence = _merge_evidence(evidence, result.evidence)
            diagnostics.extend(result.diagnostics)
            if isinstance(result.terminal, Review):
                review = result.terminal
                if review.candidate_id == candidate.candidate_id:
                    reviews = [*state.get("reviews", []), review.model_dump(mode="json")]
                    if review.approved:
                        return {
                            "reviews": reviews,
                            "review_status": "approved",
                            "evidence": evidence,
                            "diagnostics": diagnostics,
                        }
                    if state["iteration"] >= state["max_iterations"]:
                        return {
                            "reviews": reviews,
                            "review_status": "iteration_limit",
                            "evidence": evidence,
                            "diagnostics": diagnostics,
                        }
                    return {
                        "reviews": reviews,
                        "feedback": [item.model_dump(mode="json") for item in review.findings],
                        "evidence": evidence,
                        "diagnostics": diagnostics,
                    }
                diagnostics.append("review candidate_id mismatch")
            prompt = json.dumps(
                {
                    "task": "retry_malformed_review",
                    "attempt": malformed_attempt + 1,
                    "candidate_id": candidate.candidate_id,
                    "errors": diagnostics[-3:],
                },
                separators=(",", ":"),
            )
        return {
            "terminal_status": "failed",
            "failure": "reviewer did not return a valid verdict",
            "diagnostics": diagnostics,
        }

    async def _render(self, state: GraphState) -> dict[str, Any]:
        publication = Publication.model_validate(state["publication"])
        evidence = [Evidence.model_validate(item) for item in state["evidence"]]
        known = {item.id for item in evidence}
        missing = sorted(
            {reference for update in publication.updates for reference in update.evidence_ids}
            - known
        )
        if missing:
            finding = Finding(
                category=FindingCategory.INTEGRITY,
                affected_content="evidence references",
                correction=f"Replace unknown evidence IDs: {', '.join(missing)}.",
                rule="evidence-resolves",
            )
            if state["iteration"] < state["max_iterations"]:
                return {"feedback": [finding.model_dump(mode="json")], "rendered": None}
            return {
                "terminal_status": "failed",
                "failure": "unresolved evidence at iteration limit",
                "rendered": None,
            }
        rendered = self.renderer.render(
            publication,
            evidence,
            self.settings.registry(),
            run_id=state["run_id"],
            review_status=state["review_status"],
            retained_dates=[date.fromisoformat(value) for value in state["retained_dates"]],
            provenance=state["provenance"],
        )
        return {"rendered": rendered}

    async def _validate(self, state: GraphState) -> dict[str, Any]:
        findings = self.validator.validate(
            Publication.model_validate(state["publication"]),
            [Evidence.model_validate(item) for item in state["evidence"]],
            state["rendered"],
            retained_dates=state["retained_dates"],
        )
        if not findings:
            return {}
        if state["iteration"] < state["max_iterations"]:
            return {
                "feedback": [item.model_dump(mode="json") for item in findings],
                "rendered": None,
            }
        return {
            "terminal_status": "failed",
            "failure": f"deterministic validation failed: {len(findings)} finding(s)",
        }

    async def _publish(self, state: GraphState) -> dict[str, Any]:
        receipt = self.publisher.publish(state["rendered"])
        return {
            "terminal_status": "published",
            "run_prefix": receipt.run_prefix,
            "warnings": receipt.warnings,
        }

    @staticmethod
    def _after_produce(state: GraphState) -> str:
        return "failed" if state.get("terminal_status") == "failed" else "review"

    @staticmethod
    def _after_review(state: GraphState) -> str:
        if state.get("terminal_status") == "failed":
            return "failed"
        return "produce" if state.get("feedback") else "render"

    @staticmethod
    def _after_render(state: GraphState) -> str:
        if state.get("terminal_status") == "failed":
            return "failed"
        return "produce" if state.get("feedback") else "validate"

    @staticmethod
    def _after_validate(state: GraphState) -> str:
        if state.get("terminal_status") == "failed":
            return "failed"
        return "produce" if state.get("feedback") else "publish"

    def graph(self) -> Any:
        graph = StateGraph(GraphState)
        graph.add_node("produce", self._produce)
        graph.add_node("review", self._review)
        graph.add_node("render", self._render)
        graph.add_node("validate", self._validate)
        graph.add_node("publish", self._publish)
        graph.add_node("failed", lambda state: {})
        graph.add_edge(START, "produce")
        graph.add_conditional_edges("produce", self._after_produce)
        graph.add_conditional_edges("review", self._after_review)
        graph.add_conditional_edges("render", self._after_render)
        graph.add_conditional_edges("validate", self._after_validate)
        graph.add_edge("publish", END)
        graph.add_edge("failed", END)
        return graph.compile()

    async def run(self, publication_date: date | None = None) -> RunResult:
        target = publication_date or datetime.now(UTC).date()
        run_id = uuid.uuid4().hex
        try:
            lock_key = self.publisher.acquire_lock(
                target,
                run_id,
                lease_seconds=int(
                    self.settings.session_timeout_seconds * (self.settings.max_iterations * 2 + 2)
                ),
            )
        except LockContended:
            return RunResult(run_id=run_id, publication_date=target, status="contended")
        try:
            self.producer, self.reviewer = await asyncio.gather(
                self.runtime.start("producer"), self.runtime.start("reviewer")
            )
            registry = self.settings.registry()
            provenance = {
                "source_revision": self.settings.source_revision,
                "source_registry_sha256": _hash_file(self.settings.source_registry_path),
                "producer_prompt_sha256": _hash_file(self.settings.prompt_dir / "producer.md"),
                "reviewer_prompt_sha256": _hash_file(self.settings.prompt_dir / "reviewer.md"),
                "guidelines_sha256": _hash_file(self.settings.prompt_dir / "shared-guidelines.md"),
                "template_version": TEMPLATE_VERSION,
                "producer_provider": self.settings.producer_provider,
                "producer_model": self.settings.producer_model,
                "reviewer_provider": self.settings.reviewer_provider,
                "reviewer_model": self.settings.reviewer_model,
            }
            initial: GraphState = {
                "run_id": run_id,
                "publication_date": target.isoformat(),
                "registry": registry.model_dump(mode="json"),
                "evidence": [],
                "diagnostics": [],
                "reviews": [],
                "feedback": [],
                "iteration": 0,
                "max_iterations": self.settings.max_iterations,
                "retained_dates": [item.isoformat() for item in self.publisher.retained_dates()],
                "provenance": provenance,
                "terminal_status": "running",
                "warnings": [],
            }
            final = await self.graph().ainvoke(initial)
            status = final.get("terminal_status", "failed")
            if status != "published":
                log.error("run_failed", run_id=run_id, reason=final.get("failure", "unknown"))
                return RunResult(run_id=run_id, publication_date=target, status="failed")
            return RunResult(
                run_id=run_id,
                publication_date=target,
                status="published",
                run_prefix=final["run_prefix"],
                review_status=final.get("review_status"),
                warnings=final.get("warnings", []),
            )
        except RuntimeFailure as exc:
            log.error("runtime_failure", run_id=run_id, error=str(exc))
            return RunResult(run_id=run_id, publication_date=target, status="failed")
        finally:
            await self.runtime.close()
            self.publisher.release_lock(lock_key, run_id)
