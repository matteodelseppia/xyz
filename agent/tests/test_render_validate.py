from datetime import UTC, datetime

from xyz_agent.config import Settings
from xyz_agent.models import Evidence, Publication, Update, Verification
from xyz_agent.render import Renderer, next_cron_run
from xyz_agent.validate import Validator


def test_renderer_escapes_model_text_and_keeps_evidence_private(
    publication: Publication, evidence: Evidence
) -> None:
    settings = Settings()
    malicious = publication.model_copy(
        update={
            "updates": [
                publication.updates[0].model_copy(
                    update={"description": '<script>alert("x")</script> useful links'}
                )
            ]
        }
    )
    rendered = Renderer(settings.template_dir).render(
        malicious,
        [evidence],
        settings.registry(),
        run_id="a" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={"revision": "test"},
    )
    html = rendered.files["index.html"][0].decode()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert evidence.text not in b"".join(data for data, _ in rendered.files.values()).decode()
    assert not Validator().validate(malicious, [evidence], rendered, retained_dates=[])


def test_renderer_publishes_checkable_item_audit_and_editorial_calibration(
    publication: Publication, evidence: Evidence, approved
) -> None:
    settings = Settings()
    rendered = Renderer(settings.template_dir).render(
        publication,
        [evidence],
        settings.registry(),
        review=approved,
        editorial_anchors=[item.model_dump() for item in settings.editorial_anchors()],
        run_id="f" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )
    html = rendered.files["index.html"][0].decode()
    publication_json = rendered.files["publication.json"][0].decode()
    assert "Why was this selected?" in html
    assert "Reviewer:" in html
    assert "The article offers useful implementation detail." in html
    assert evidence.text not in publication_json
    assert '"editorial_calibration"' in publication_json
    assert '"url":"/loved-ones/"' in publication_json
    assert '"verifications"' in publication_json


def test_renderer_uses_dated_entry_evidence_for_a_cited_feed_item(
    publication: Publication, evidence: Evidence
) -> None:
    feed = evidence.model_copy(update={"published_at": None, "kind": "feed"})
    entry = evidence.model_copy(
        update={
            "id": "ev_1111111111111111",
            "published_at": datetime(2026, 1, 2, 12, tzinfo=UTC),
            "kind": "entry",
        }
    )
    cited_feed = publication.model_copy(
        update={"updates": [publication.updates[0].model_copy(update={"evidence_ids": [feed.id]})]}
    )
    rendered = Renderer(Settings().template_dir).render(
        cited_feed,
        [feed, entry],
        Settings().registry(),
        run_id="e" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )

    html = rendered.files["index.html"][0].decode()
    publication_json = rendered.files["publication.json"][0].decode()
    assert "date unavailable" not in html
    assert 'datetime="2026-01-02">2026-01-02</time>' in html
    assert '"published_at":"2026-01-02T12:00:00+00:00"' in publication_json


def test_renderer_uses_the_primary_evidence_title_for_an_article_heading(
    publication: Publication, evidence: Evidence
) -> None:
    rendered = Renderer(Settings().template_dir).render(
        publication,
        [evidence],
        Settings().registry(),
        run_id="9" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )

    html = rendered.files["index.html"][0].decode()
    publication_json = rendered.files["publication.json"][0].decode()
    assert ">Example update</a></h1>" in html
    assert "A release to consider</a></h1>" not in html
    assert '"heading":"Example update"' in publication_json


def test_next_cron_run_matches_daily_schedule() -> None:
    assert next_cron_run(datetime(2026, 9, 1, 5, 59, 59, tzinfo=UTC), "0 6 * * *") == datetime(
        2026, 9, 1, 6, tzinfo=UTC
    )
    assert next_cron_run(datetime(2026, 9, 1, 6, tzinfo=UTC), "0 6 * * *") == datetime(
        2026, 9, 2, 6, tzinfo=UTC
    )


def test_digest_layout_has_requested_links(publication: Publication, evidence: Evidence) -> None:
    settings = Settings()
    rendered = Renderer(settings.template_dir).render(
        publication,
        [evidence],
        settings.registry(),
        run_id="c" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )
    html = rendered.files["index.html"][0].decode()
    assert html.index("next run") < html.index("last run") < html.index("run trace")
    assert 'href="/days/' in html and '/prompt/"' in html
    assert '<footer class="digest-footer"><a href="/sources">sources</a>' in html
    assert '<a href="/loved-ones/">loved ones</a>' in html
    assert 'href="https://github.com/matteodelseppia/xyz"' in html
    assert "data-local-time>" in html
    assert '<time class="published" datetime="2026-01-01">2026-01-01</time>' in html
    assert "✦" not in html
    prompt_html = rendered.files["prompt/index.html"][0].decode()
    assert "data-copy-prompt" in prompt_html
    assert "data-prompt-transcript" in prompt_html
    assert "data-trace-search" in prompt_html
    assert 'data-trace-filter="output"' in prompt_html
    assert "data-trace-events" in prompt_html
    assert "raw redacted trace" in prompt_html


def test_validator_rejects_concentrated_sources_for_ten_updates(
    publication: Publication, evidence: Evidence
) -> None:
    settings = Settings()
    concentrated = publication.model_copy(update={"updates": [publication.updates[0]] * 10})
    rendered = Renderer(settings.template_dir).render(
        concentrated,
        [evidence],
        settings.registry(),
        run_id="d" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )
    findings = Validator().validate(concentrated, [evidence], rendered, retained_dates=[])
    assert any(finding.rule == "source-diversity" for finding in findings)


def test_validator_allows_required_evidence_title_as_article_heading(
    publication: Publication, evidence: Evidence
) -> None:
    heading = "Eight token evidence heading that must remain exact"
    titled_evidence = evidence.model_copy(
        update={"title": heading, "text": f"{heading}. This source provides additional detail."}
    )
    titled_publication = publication.model_copy(
        update={"updates": [publication.updates[0].model_copy(update={"heading": heading})]}
    )
    rendered = Renderer(Settings().template_dir).render(
        titled_publication,
        [titled_evidence],
        Settings().registry(),
        run_id="e" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )

    findings = Validator().validate(
        titled_publication, [titled_evidence], rendered, retained_dates=[]
    )

    assert not any(finding.rule == "originality" for finding in findings)


def test_validator_reports_every_overlapping_update_in_one_pass(
    publication: Publication, evidence: Evidence
) -> None:
    first = publication.updates[0].model_copy(update={"description": evidence.text})
    second = publication.updates[0].model_copy(
        update={"heading": "Another update", "why_read": evidence.text}
    )
    copied = publication.model_copy(update={"updates": [first, second]})
    rendered = Renderer(Settings().template_dir).render(
        copied,
        [evidence],
        Settings().registry(),
        run_id="a" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )

    findings = Validator().validate(copied, [evidence], rendered, retained_dates=[])
    originality_targets = {
        finding.affected_content for finding in findings if finding.rule == "originality"
    }

    assert originality_targets == {"update 1: description", "update 2: why_read"}


def test_validator_does_not_join_unrelated_fields_for_originality_matching(
    publication: Publication, evidence: Evidence
) -> None:
    boundary_evidence = evidence.model_copy(
        update={"text": "Boundary words cannot form matches across separate digest updates."}
    )
    first = publication.updates[0].model_copy(
        update={
            "verifications": [
                *publication.updates[0].verifications[:2],
                publication.updates[0]
                .verifications[2]
                .model_copy(update={"question": "Boundary words cannot form"}),
            ]
        }
    )
    second = publication.updates[0].model_copy(
        update={
            "heading": "Another update",
            "description": "Matches across separate digest updates.",
        }
    )
    candidate = publication.model_copy(update={"updates": [first, second]})
    rendered = Renderer(Settings().template_dir).render(
        candidate,
        [boundary_evidence],
        Settings().registry(),
        run_id="f" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )

    findings = Validator().validate(candidate, [boundary_evidence], rendered, retained_dates=[])

    assert not any(finding.rule == "originality" for finding in findings)


def test_validator_rejects_substantial_source_overlap(
    publication: Publication, evidence: Evidence
) -> None:
    settings = Settings()
    copied = publication.model_copy(
        update={
            "updates": [
                Update(
                    heading="Copied wording",
                    description=(
                        "The source discusses a technical release and its implementation "
                        "details for readers."
                    ),
                    why_read="This may be useful.",
                    keywords=["originality"],
                    evidence_ids=[evidence.id],
                    verifications=[
                        Verification(
                            assertion="heading",
                            question="Does the source support this heading?",
                            evidence_ids=[evidence.id],
                        ),
                        Verification(
                            assertion="description",
                            question="Does the source support this description?",
                            evidence_ids=[evidence.id],
                        ),
                        Verification(
                            assertion="why_read",
                            question="Does the source offer this value?",
                            evidence_ids=[evidence.id],
                        ),
                    ],
                )
            ]
        }
    )
    rendered = Renderer(settings.template_dir).render(
        copied,
        [evidence],
        settings.registry(),
        run_id="b" * 32,
        review_status="approved",
        retained_dates=[],
        provenance={},
    )
    findings = Validator().validate(copied, [evidence], rendered, retained_dates=[])
    assert {finding.rule for finding in findings} == {"originality"}
