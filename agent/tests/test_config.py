from pathlib import Path

import pytest
from pydantic import ValidationError
from xyz_agent.config import Settings, _repository_root


def test_repository_root_can_be_explicitly_configured(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "config/sources.json").write_text("{}")
    (tmp_path / "prompts/producer.md").write_text("producer")
    monkeypatch.setenv("XYZ_REPOSITORY_ROOT", str(tmp_path))
    assert _repository_root() == tmp_path


def test_loved_ones_are_loaded_as_editorial_anchors() -> None:
    anchors = Settings().editorial_anchors()
    assert len(anchors) == 5
    assert anchors[0].url == "https://www.seangoedecke.com/how-to-ship"
    assert anchors[0].tags == ["delivery", "engineering-leadership"]


def test_session_timeout_defaults_to_ten_minutes(monkeypatch) -> None:
    monkeypatch.delenv("XYZ_SESSION_TIMEOUT_SECONDS", raising=False)
    assert Settings().session_timeout_seconds == 600


def test_session_timeout_can_be_configured_up_to_three_thousand_seconds(monkeypatch) -> None:
    monkeypatch.setenv("XYZ_SESSION_TIMEOUT_SECONDS", "3000")
    assert Settings().session_timeout_seconds == 3000
    monkeypatch.setenv("XYZ_SESSION_TIMEOUT_SECONDS", "3001")
    with pytest.raises(ValidationError):
        Settings()


def test_max_iterations_allows_fifty_and_rejects_more() -> None:
    assert Settings(max_iterations=50).max_iterations == 50
    with pytest.raises(ValidationError):
        Settings(max_iterations=51)


def test_daily_schedule_matches_the_railway_cron() -> None:
    assert Settings().scheduled_run_cron == "0 6 * * *"
    with pytest.raises(ValidationError):
        Settings(scheduled_run_cron="0 */12 * * *")


def test_validation_retries_are_separate_and_bounded() -> None:
    assert Settings(max_validation_retries=10).max_validation_retries == 10
    with pytest.raises(ValidationError):
        Settings(max_validation_retries=11)
