from xyz_agent.config import Settings
from xyz_agent.models import Evidence, Publication, Update
from xyz_agent.render import Renderer
from xyz_agent.validate import Validator


def test_renderer_escapes_model_text_and_keeps_evidence_private(
    publication: Publication, evidence: Evidence
) -> None:
    settings = Settings()
    malicious = publication.model_copy(
        update={"introduction": '<script>alert("x")</script> useful links'}
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
                    evidence_ids=[evidence.id],
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
