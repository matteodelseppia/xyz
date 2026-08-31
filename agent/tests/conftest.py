from __future__ import annotations

from datetime import date

import pytest
from xyz_agent.models import Evidence, Publication, Review, Update


@pytest.fixture
def evidence() -> Evidence:
    return Evidence(
        id="ev_0123456789abcdef",
        source_id="simon-willison",
        url="https://example.com/update",
        title="Example update",
        text="The source discusses a technical release and its implementation details for readers.",
        kind="entry",
    )


@pytest.fixture
def publication(evidence: Evidence) -> Publication:
    return Publication(
        candidate_id="candidate-one",
        publication_date=date(2026, 1, 2),
        title="Today in software",
        introduction="A short collection of worthwhile engineering links.",
        updates=[
            Update(
                heading="A release to consider",
                description="A project has published a new technical release.",
                why_read="It may be useful to engineers following this area.",
                evidence_ids=[evidence.id],
            )
        ],
    )


@pytest.fixture
def approved(publication: Publication) -> Review:
    return Review(
        candidate_id=publication.candidate_id,
        approved=True,
        rationale="The annotation is supported, brief, and original.",
    )
