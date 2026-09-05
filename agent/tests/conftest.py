from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from xyz_agent.models import Evidence, Publication, Review, ReviewCheck, Update, Verification


@pytest.fixture
def evidence() -> Evidence:
    return Evidence(
        id="ev_0123456789abcdef",
        source_id="simon-willison",
        url="https://example.com/update",
        title="Example update",
        published_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        text="The source discusses a technical release and its implementation details for readers.",
        kind="entry",
    )


@pytest.fixture
def publication(evidence: Evidence) -> Publication:
    return Publication(
        candidate_id="candidate-one",
        publication_date=date(2026, 1, 2),
        title="Today in software",
        updates=[
            Update(
                heading="A release to consider",
                description="A project has published a new technical release.",
                why_read="It may be useful to engineers following this area.",
                keywords=["release", "engineering"],
                evidence_ids=[evidence.id],
                verifications=[
                    Verification(
                        assertion="heading",
                        question="Does the source concern this release?",
                        evidence_ids=[evidence.id],
                    ),
                    Verification(
                        assertion="description",
                        question="Does the source support this high-level description?",
                        evidence_ids=[evidence.id],
                    ),
                    Verification(
                        assertion="why_read",
                        question="Does the source offer useful implementation detail?",
                        evidence_ids=[evidence.id],
                    ),
                ],
            )
        ],
    )


@pytest.fixture
def approved(publication: Publication) -> Review:
    return Review(
        candidate_id=publication.candidate_id,
        approved=True,
        checks=[
            ReviewCheck(
                update_index=0,
                evidence_supported=True,
                attribution_correct=True,
                selection_rationale_credible=True,
                opinion="The article offers useful implementation detail.",
            )
        ],
        rationale="The annotation is supported, brief, and original.",
    )
