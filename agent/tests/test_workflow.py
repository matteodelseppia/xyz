from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from xyz_agent.config import Settings
from xyz_agent.models import Evidence, Finding, FindingCategory, Publication, Review, ReviewCheck
from xyz_agent.publish import Publisher
from xyz_agent.runtime import FakeRuntime, SessionResult
from xyz_agent.storage import LocalStorage
from xyz_agent.workflow import DigestWorkflow


@pytest.mark.asyncio
async def test_complete_graph_reuses_sessions_for_revision(
    tmp_path: Path, publication: Publication, evidence: Evidence
) -> None:
    revised = publication.model_copy(update={"candidate_id": "candidate-two"})
    rejection = Review(
        candidate_id=publication.candidate_id,
        approved=False,
        findings=[
            Finding(
                category=FindingCategory.BREVITY,
                affected_content="description",
                correction="Make the description shorter.",
            )
        ],
        checks=[
            ReviewCheck(
                update_index=0,
                evidence_supported=True,
                attribution_correct=True,
                selection_rationale_credible=False,
                opinion="The description needs a more concise explanation.",
            )
        ],
        rationale="One material correction is needed.",
    )
    approval = Review(
        candidate_id=revised.candidate_id,
        approved=True,
        checks=[
            ReviewCheck(
                update_index=0,
                evidence_supported=True,
                attribution_correct=True,
                selection_rationale_credible=True,
                opinion="The revised item clearly states its reading value.",
            )
        ],
        rationale="The correction resolves the finding.",
    )
    runtime = FakeRuntime(
        producer=[
            SessionResult(terminal=publication, evidence=[evidence]),
            SessionResult(terminal=revised),
        ],
        reviewer=[SessionResult(terminal=rejection), SessionResult(terminal=approval)],
    )
    settings = Settings(runtime="fake", storage_root=tmp_path, max_iterations=3)
    result = await DigestWorkflow(settings, runtime, Publisher(LocalStorage(tmp_path))).run(
        date(2026, 1, 2)
    )
    assert result.status == "published"
    assert result.review_status == "approved"
    assert len(runtime.producer.prompts) == 2
    assert "latest_findings" in runtime.producer.prompts[1]
    assert LocalStorage(tmp_path).get("current.json")


@pytest.mark.asyncio
async def test_unknown_evidence_output_is_retried_without_consuming_an_iteration(
    tmp_path: Path, publication: Publication, evidence: Evidence
) -> None:
    invalid = publication.model_copy(
        update={
            "candidate_id": "invalid-candidate",
            "updates": [
                publication.updates[0].model_copy(update={"evidence_ids": ["ev_ffffffffffffffff"]})
            ],
        }
    )
    valid = publication.model_copy(update={"candidate_id": "valid-candidate"})
    approval = Review(
        candidate_id=valid.candidate_id,
        approved=True,
        checks=[
            ReviewCheck(
                update_index=0,
                evidence_supported=True,
                attribution_correct=True,
                selection_rationale_credible=True,
                opinion="The corrected source reference supports this item.",
            )
        ],
        rationale="The corrected evidence reference is valid.",
    )
    runtime = FakeRuntime(
        producer=[
            SessionResult(terminal=invalid, evidence=[evidence]),
            SessionResult(terminal=valid),
        ],
        reviewer=[SessionResult(terminal=approval)],
    )
    settings = Settings(runtime="fake", storage_root=tmp_path, max_iterations=1)

    result = await DigestWorkflow(settings, runtime, Publisher(LocalStorage(tmp_path))).run(
        publication.publication_date
    )

    assert result.status == "published"
    assert len(runtime.producer.prompts) == 2
    assert "unknown_evidence_ids" in runtime.producer.prompts[1]
    assert evidence.id in runtime.producer.prompts[1]


@pytest.mark.asyncio
async def test_validation_correction_does_not_consume_a_generation_iteration(
    tmp_path: Path, publication: Publication, evidence: Evidence
) -> None:
    copied = publication.model_copy(
        update={
            "updates": [
                publication.updates[0].model_copy(
                    update={
                        "description": (
                            "The source discusses a technical release and its implementation "
                            "details for readers."
                        )
                    }
                )
            ]
        }
    )
    approval = Review(
        candidate_id=publication.candidate_id,
        approved=True,
        checks=[
            ReviewCheck(
                update_index=0,
                evidence_supported=True,
                attribution_correct=True,
                selection_rationale_credible=True,
                opinion="The item is concise and useful to its intended reader.",
            )
        ],
        rationale="Approved by scripted reviewer.",
    )
    runtime = FakeRuntime(
        producer=[
            SessionResult(terminal=copied, evidence=[evidence]),
            SessionResult(terminal=publication),
        ],
        reviewer=[SessionResult(terminal=approval), SessionResult(terminal=approval)],
    )
    settings = Settings(
        runtime="fake",
        storage_root=tmp_path,
        max_iterations=1,
        max_validation_retries=1,
    )

    result = await DigestWorkflow(settings, runtime, Publisher(LocalStorage(tmp_path))).run(
        copied.publication_date
    )

    assert result.status == "published"
    assert len(runtime.producer.prompts) == 2


@pytest.mark.asyncio
async def test_validation_failure_at_limit_does_not_publish(
    tmp_path: Path, publication: Publication, evidence: Evidence
) -> None:
    copied = publication.model_copy(
        update={
            "updates": [
                publication.updates[0].model_copy(
                    update={
                        "description": (
                            "The source discusses a technical release and its implementation "
                            "details for readers."
                        )
                    }
                )
            ]
        }
    )
    approval = Review(
        candidate_id=copied.candidate_id,
        approved=True,
        checks=[
            ReviewCheck(
                update_index=0,
                evidence_supported=True,
                attribution_correct=True,
                selection_rationale_credible=True,
                opinion="The item is concise and useful to its intended reader.",
            )
        ],
        rationale="Approved by scripted reviewer.",
    )
    runtime = FakeRuntime(
        producer=[SessionResult(terminal=copied, evidence=[evidence])],
        reviewer=[SessionResult(terminal=approval)],
    )
    settings = Settings(
        runtime="fake", storage_root=tmp_path, max_iterations=1, max_validation_retries=0
    )
    result = await DigestWorkflow(settings, runtime, Publisher(LocalStorage(tmp_path))).run(
        copied.publication_date
    )
    assert result.status == "failed"
    assert not (tmp_path / "current.json").exists()
