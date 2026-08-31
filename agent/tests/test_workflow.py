from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from xyz_agent.config import Settings
from xyz_agent.models import Evidence, Finding, FindingCategory, Publication, Review
from xyz_agent.publish import Publisher
from xyz_agent.runtime import FakeRuntime, SessionResult
from xyz_agent.storage import LocalStorage
from xyz_agent.workflow import DigestWorkflow


@pytest.mark.asyncio
async def test_complete_graph_reuses_sessions_for_revision(
    tmp_path: Path, publication: Publication, evidence: Evidence
) -> None:
    revised = publication.model_copy(
        update={"candidate_id": "candidate-two", "introduction": "A revised set of useful links."}
    )
    rejection = Review(
        candidate_id=publication.candidate_id,
        approved=False,
        findings=[
            Finding(
                category=FindingCategory.BREVITY,
                affected_content="introduction",
                correction="Make the introduction shorter.",
            )
        ],
        rationale="One material correction is needed.",
    )
    approval = Review(
        candidate_id=revised.candidate_id,
        approved=True,
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
async def test_validation_failure_at_limit_does_not_publish(
    tmp_path: Path, publication: Publication, evidence: Evidence
) -> None:
    copied = publication.model_copy(
        update={
            "introduction": (
                "The source discusses a technical release and its implementation "
                "details for readers."
            )
        }
    )
    approval = Review(
        candidate_id=copied.candidate_id,
        approved=True,
        rationale="Approved by scripted reviewer.",
    )
    runtime = FakeRuntime(
        producer=[SessionResult(terminal=copied, evidence=[evidence])],
        reviewer=[SessionResult(terminal=approval)],
    )
    settings = Settings(runtime="fake", storage_root=tmp_path, max_iterations=1)
    result = await DigestWorkflow(settings, runtime, Publisher(LocalStorage(tmp_path))).run(
        copied.publication_date
    )
    assert result.status == "failed"
    assert not (tmp_path / "current.json").exists()
