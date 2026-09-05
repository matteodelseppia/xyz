from datetime import timedelta
from pathlib import Path

from xyz_agent.config import Settings
from xyz_agent.models import Evidence, Publication
from xyz_agent.publish import Publisher
from xyz_agent.render import Renderer
from xyz_agent.storage import LocalStorage


def test_publisher_retains_seven_distinct_days(
    tmp_path: Path, publication: Publication, evidence: Evidence
) -> None:
    settings = Settings()
    storage = LocalStorage(tmp_path)
    publisher = Publisher(storage, retention_days=7)
    renderer = Renderer(settings.template_dir)
    for offset in range(8):
        day = publication.publication_date + timedelta(days=offset)
        candidate = publication.model_copy(
            update={"candidate_id": f"candidate-{offset}", "publication_date": day}
        )
        rendered = renderer.render(
            candidate,
            [evidence],
            settings.registry(),
            run_id=f"{offset:032x}",
            review_status="approved",
            retained_dates=publisher.retained_dates(),
            provenance={},
        )
        publisher.publish(rendered)
    assert len(publisher.retained_dates()) == 7
    assert not storage.list(f"runs/{publication.publication_date}")
    current = storage.get("current.json").data.decode()
    assert str(publication.publication_date + timedelta(days=7)) in current
