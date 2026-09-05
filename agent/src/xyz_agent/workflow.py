from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter
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
    editorial_anchors: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    diagnostics: list[str]
    publication: dict[str, Any]
    reviews: list[dict[str, Any]]
    feedback: list[dict[str, Any]]
    iteration: int
    max_iterations: int
    validation_attempts: int
    review_status: Literal["approved", "iteration_limit"]
    rendered: Any
    retained_dates: list[str]
    provenance: dict[str, str]
    terminal_status: Literal["running", "published", "failed"]
    failure: str
    run_prefix: str
    warnings: list[str]
    transcript: list[dict[str, Any]]


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _public_trace(value: Any) -> Any:
    """Preserve orchestration while removing private fetched evidence bodies."""
    if isinstance(value, dict):
        return {
            key: ("[private evidence omitted]" if key == "text" else _public_trace(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_public_trace(item) for item in value]
    if isinstance(value, str):
        try:
            return _public_trace(json.loads(value))
        except json.JSONDecodeError:
            return value
    return value


def _merge_evidence(
    current: list[dict[str, Any]], additions: list[Evidence]
) -> list[dict[str, Any]]:
    merged = {item["id"]: item for item in current}
    for item in additions:
        merged[item.id] = item.model_dump(mode="json")
    return list(merged.values())


def _unresolved_evidence_ids(publication: Publication, evidence: list[dict[str, Any]]) -> list[str]:
    known = {item["id"] for item in evidence}
    return sorted(
        {
            reference
            for update in publication.updates
            for reference in [
                *update.evidence_ids,
                *(
                    evidence_id
                    for check in update.verifications
                    for evidence_id in check.evidence_ids
                ),
            ]
            if reference not in known
        }
    )


def _review_check_errors(review: Review, publication: Publication) -> list[str]:
    expected = set(range(len(publication.updates)))
    actual = {check.update_index for check in review.checks}
    errors: list[str] = []
    if actual != expected or len(review.checks) != len(expected):
        errors.append("review must contain exactly one item check for every update")
    if review.approved and any(
        not (
            check.evidence_supported
            and check.attribution_correct
            and check.selection_rationale_credible
        )
        for check in review.checks
    ):
        errors.append("approved review contains a failed item check")
    return errors


def _tool_counts(interactions: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        Counter(
            item["name"]
            for item in interactions
            if item.get("kind") == "tool" and isinstance(item.get("name"), str)
        )
    )


def _diagnostic_counts(diagnostics: list[str]) -> dict[str, int]:
    return dict(Counter(diagnostic.split(":", 1)[0] for diagnostic in diagnostics))


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
        log.info(
            "produce_started",
            run_id=state["run_id"],
            iteration=state.get("iteration", 0),
            validation_attempts=state.get("validation_attempts", 0),
            correction=bool(state.get("feedback")),
            evidence_count=len(state.get("evidence", [])),
        )
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
                    "editorial_anchors": state["editorial_anchors"],
                },
                separators=(",", ":"),
            )
        diagnostics = list(state.get("diagnostics", []))
        evidence = list(state.get("evidence", []))
        transcript = list(state.get("transcript", []))
        for malformed_attempt in range(self.settings.malformed_retries + 1):
            started = perf_counter()
            result = await self.producer.prompt(prompt)
            evidence = _merge_evidence(evidence, result.evidence)
            log.info(
                "producer_prompt_settled",
                run_id=state["run_id"],
                iteration=state.get("iteration", 0),
                malformed_attempt=malformed_attempt,
                duration_ms=round((perf_counter() - started) * 1000),
                terminal_type=type(result.terminal).__name__ if result.terminal else None,
                evidence_received=len(result.evidence),
                evidence_count=len(evidence),
                tool_counts=_tool_counts(result.interactions),
                diagnostics_count=len(result.diagnostics),
                diagnostic_counts=_diagnostic_counts(result.diagnostics),
            )
            diagnostics.extend(result.diagnostics)
            transcript.extend(
                {"role": "producer", **_public_trace(item)} for item in result.interactions
            )
            unknown_evidence_ids: list[str] = []
            if isinstance(result.terminal, Publication):
                candidate = result.terminal
                unknown_evidence_ids = _unresolved_evidence_ids(candidate, evidence)
                if candidate.publication_date.isoformat() != state["publication_date"]:
                    diagnostics.append("producer returned the wrong publication date")
                elif unknown_evidence_ids:
                    diagnostics.append(
                        "producer returned unknown evidence IDs: "
                        + ", ".join(unknown_evidence_ids[:12])
                    )
                elif evidence:
                    log.info(
                        "producer_submitted",
                        run_id=state["run_id"],
                        iteration=state.get("iteration", 0) + 1,
                        update_count=len(candidate.updates),
                        evidence_count=len(evidence),
                    )
                    return {
                        "publication": candidate.model_dump(mode="json"),
                        "evidence": evidence,
                        "diagnostics": diagnostics,
                        "iteration": state.get("iteration", 0) + 1,
                        "feedback": [],
                        "transcript": transcript,
                    }
            prompt = json.dumps(
                {
                    "task": "retry_malformed_output",
                    "attempt": malformed_attempt + 1,
                    "errors": diagnostics[-3:],
                    "unknown_evidence_ids": unknown_evidence_ids,
                    "known_evidence_ids": [item["id"] for item in evidence],
                    "instruction": (
                        "Call submit_publication once with a valid artifact. Every evidence_ids "
                        "value must be one of known_evidence_ids."
                    ),
                },
                separators=(",", ":"),
            )
        log.warning(
            "producer_failed",
            run_id=state["run_id"],
            iteration=state.get("iteration", 0),
            evidence_count=len(evidence),
            diagnostics_count=len(diagnostics),
        )
        return {
            "terminal_status": "failed",
            "failure": "producer did not return a valid artifact with usable evidence",
            "diagnostics": diagnostics,
            "evidence": evidence,
            "transcript": transcript,
        }

    async def _review(self, state: GraphState) -> dict[str, Any]:
        assert self.reviewer is not None
        candidate = Publication.model_validate(state["publication"])
        log.info(
            "review_started",
            run_id=state["run_id"],
            iteration=state["iteration"],
            update_count=len(candidate.updates),
            evidence_count=len(state["evidence"]),
        )
        prompt = json.dumps(
            {
                "task": "review_candidate",
                "source_registry": state["registry"],
                "editorial_anchors": state["editorial_anchors"],
                "evidence": state["evidence"],
                "candidate": state["publication"],
            },
            separators=(",", ":"),
        )
        evidence = list(state["evidence"])
        diagnostics = list(state.get("diagnostics", []))
        transcript = list(state.get("transcript", []))
        for malformed_attempt in range(self.settings.malformed_retries + 1):
            started = perf_counter()
            result = await self.reviewer.prompt(prompt)
            evidence = _merge_evidence(evidence, result.evidence)
            log.info(
                "reviewer_prompt_settled",
                run_id=state["run_id"],
                iteration=state["iteration"],
                malformed_attempt=malformed_attempt,
                duration_ms=round((perf_counter() - started) * 1000),
                terminal_type=type(result.terminal).__name__ if result.terminal else None,
                evidence_received=len(result.evidence),
                evidence_count=len(evidence),
                tool_counts=_tool_counts(result.interactions),
                diagnostics_count=len(result.diagnostics),
                diagnostic_counts=_diagnostic_counts(result.diagnostics),
            )
            diagnostics.extend(result.diagnostics)
            transcript.extend(
                {"role": "reviewer", **_public_trace(item)} for item in result.interactions
            )
            if isinstance(result.terminal, Review):
                review = result.terminal
                if review.candidate_id == candidate.candidate_id:
                    check_errors = _review_check_errors(review, candidate)
                    if check_errors:
                        diagnostics.extend(check_errors)
                        prompt = json.dumps(
                            {
                                "task": "retry_malformed_review",
                                "attempt": malformed_attempt + 1,
                                "candidate_id": candidate.candidate_id,
                                "errors": check_errors,
                            },
                            separators=(",", ":"),
                        )
                        continue
                    reviews = [*state.get("reviews", []), review.model_dump(mode="json")]
                    log.info(
                        "review_submitted",
                        run_id=state["run_id"],
                        iteration=state["iteration"],
                        approved=review.approved,
                        finding_count=len(review.findings),
                    )
                    if review.approved:
                        return {
                            "reviews": reviews,
                            "review_status": "approved",
                            "evidence": evidence,
                            "diagnostics": diagnostics,
                            "transcript": transcript,
                        }
                    if state["iteration"] >= state["max_iterations"]:
                        return {
                            "reviews": reviews,
                            "review_status": "iteration_limit",
                            "evidence": evidence,
                            "diagnostics": diagnostics,
                            "transcript": transcript,
                        }
                    return {
                        "reviews": reviews,
                        "feedback": [item.model_dump(mode="json") for item in review.findings],
                        "evidence": evidence,
                        "diagnostics": diagnostics,
                        "transcript": transcript,
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
        log.warning(
            "reviewer_failed",
            run_id=state["run_id"],
            iteration=state["iteration"],
            evidence_count=len(evidence),
            diagnostics_count=len(diagnostics),
        )
        return {
            "terminal_status": "failed",
            "failure": "reviewer did not return a valid verdict",
            "diagnostics": diagnostics,
            "transcript": transcript,
        }

    async def _render(self, state: GraphState) -> dict[str, Any]:
        publication = Publication.model_validate(state["publication"])
        evidence = [Evidence.model_validate(item) for item in state["evidence"]]
        log.info(
            "render_started",
            run_id=state["run_id"],
            iteration=state["iteration"],
            update_count=len(publication.updates),
            evidence_count=len(evidence),
        )
        missing = _unresolved_evidence_ids(publication, state["evidence"])
        if missing:
            finding = Finding(
                category=FindingCategory.INTEGRITY,
                affected_content="evidence references",
                correction=f"Replace unknown evidence IDs: {', '.join(missing)}.",
                rule="evidence-resolves",
            )
            validation_attempts = state.get("validation_attempts", 0)
            log.warning(
                "render_evidence_unresolved",
                run_id=state["run_id"],
                iteration=state["iteration"],
                missing_count=len(missing),
                validation_attempts=validation_attempts,
            )
            if validation_attempts < self.settings.max_validation_retries:
                return {
                    "feedback": [finding.model_dump(mode="json")],
                    "rendered": None,
                    "iteration": max(0, state["iteration"] - 1),
                    "validation_attempts": validation_attempts + 1,
                }
            return {
                "terminal_status": "failed",
                "failure": "unresolved evidence after validation retries: "
                + ", ".join(missing[:12]),
                "rendered": None,
            }
        rendered = self.renderer.render(
            publication,
            evidence,
            self.settings.registry(),
            review=Review.model_validate(state["reviews"][-1]),
            editorial_anchors=state["editorial_anchors"],
            run_id=state["run_id"],
            review_status=state["review_status"],
            retained_dates=[date.fromisoformat(value) for value in state["retained_dates"]],
            provenance=state["provenance"],
            transcript=state["transcript"],
            scheduled_run_cron=self.settings.scheduled_run_cron,
        )
        log.info(
            "render_completed",
            run_id=state["run_id"],
            iteration=state["iteration"],
            artifact_count=len(rendered.files),
        )
        return {"rendered": rendered}

    async def _validate(self, state: GraphState) -> dict[str, Any]:
        findings = self.validator.validate(
            Publication.model_validate(state["publication"]),
            [Evidence.model_validate(item) for item in state["evidence"]],
            state["rendered"],
            retained_dates=state["retained_dates"],
        )
        validation_attempts = state.get("validation_attempts", 0)
        rules = sorted({item.rule or item.category for item in findings})
        if not findings:
            log.info(
                "validation_passed",
                run_id=state["run_id"],
                iteration=state["iteration"],
                validation_attempts=validation_attempts,
            )
            return {}
        log.warning(
            "validation_failed",
            run_id=state["run_id"],
            iteration=state["iteration"],
            validation_attempts=validation_attempts,
            finding_count=len(findings),
            rules=rules,
        )
        if validation_attempts < self.settings.max_validation_retries:
            return {
                "feedback": [item.model_dump(mode="json") for item in findings],
                "rendered": None,
                "iteration": max(0, state["iteration"] - 1),
                "validation_attempts": validation_attempts + 1,
            }
        return {
            "terminal_status": "failed",
            "failure": (
                "deterministic validation failed after validation retries: "
                f"{len(findings)} finding(s)"
            ),
        }

    async def _publish(self, state: GraphState) -> dict[str, Any]:
        log.info("publish_started", run_id=state["run_id"], iteration=state["iteration"])
        receipt = self.publisher.publish(state["rendered"])
        log.info(
            "publish_completed",
            run_id=state["run_id"],
            run_prefix=receipt.run_prefix,
            warning_count=len(receipt.warnings),
        )
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
        log.info(
            "run_started",
            run_id=run_id,
            publication_date=target.isoformat(),
            max_iterations=self.settings.max_iterations,
            max_validation_retries=self.settings.max_validation_retries,
            tool_budget=self.settings.tool_budget,
            session_timeout_seconds=self.settings.session_timeout_seconds,
        )
        try:
            lock_key = self.publisher.acquire_lock(
                target,
                run_id,
                lease_seconds=int(
                    self.settings.session_timeout_seconds * (self.settings.max_iterations * 2 + 2)
                ),
            )
        except LockContended:
            log.info("run_contended", run_id=run_id, publication_date=target.isoformat())
            return RunResult(run_id=run_id, publication_date=target, status="contended")
        try:
            self.producer, self.reviewer = await asyncio.gather(
                self.runtime.start("producer"), self.runtime.start("reviewer")
            )
            log.info("sessions_started", run_id=run_id, roles=["producer", "reviewer"])
            registry = self.settings.registry()
            editorial_anchors = self.settings.editorial_anchors()
            provenance = {
                "source_revision": self.settings.source_revision,
                "source_registry_sha256": _hash_file(self.settings.source_registry_path),
                "producer_prompt_sha256": _hash_file(self.settings.prompt_dir / "producer.md"),
                "reviewer_prompt_sha256": _hash_file(self.settings.prompt_dir / "reviewer.md"),
                "guidelines_sha256": _hash_file(self.settings.prompt_dir / "shared-guidelines.md"),
                "template_version": TEMPLATE_VERSION,
                "editorial_anchors_sha256": _hash_file(self.settings.loved_ones_path),
                "producer_provider": self.settings.producer_provider,
                "producer_model": self.settings.producer_model,
                "reviewer_provider": self.settings.reviewer_provider,
                "reviewer_model": self.settings.reviewer_model,
            }
            initial: GraphState = {
                "run_id": run_id,
                "publication_date": target.isoformat(),
                "registry": registry.model_dump(mode="json"),
                "editorial_anchors": [item.model_dump() for item in editorial_anchors],
                "evidence": [],
                "diagnostics": [],
                "reviews": [],
                "feedback": [],
                "iteration": 0,
                "max_iterations": self.settings.max_iterations,
                "validation_attempts": 0,
                "retained_dates": [item.isoformat() for item in self.publisher.retained_dates()],
                "provenance": provenance,
                "terminal_status": "running",
                "warnings": [],
                "transcript": [
                    {
                        "role": "producer",
                        "kind": "system_prompt",
                        "message": (self.settings.prompt_dir / "producer.md").read_text()
                        + "\n\n"
                        + (self.settings.prompt_dir / "shared-guidelines.md").read_text(),
                    },
                    {
                        "role": "reviewer",
                        "kind": "system_prompt",
                        "message": (self.settings.prompt_dir / "reviewer.md").read_text()
                        + "\n\n"
                        + (self.settings.prompt_dir / "shared-guidelines.md").read_text(),
                    },
                ],
            }
            final = await self.graph().ainvoke(initial)
            status = final.get("terminal_status", "failed")
            if status != "published":
                log.error("run_failed", run_id=run_id, reason=final.get("failure", "unknown"))
                return RunResult(run_id=run_id, publication_date=target, status="failed")
            log.info(
                "run_published",
                run_id=run_id,
                publication_date=target.isoformat(),
                iteration=final.get("iteration"),
                validation_attempts=final.get("validation_attempts", 0),
            )
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
