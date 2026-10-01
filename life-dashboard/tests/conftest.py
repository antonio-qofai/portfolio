import pytest

from dashboard import actions


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    """Tests never reach the Claude API; pass a fake client to exercise the LLM path."""
    def refuse():
        raise actions.ActionsError("no Claude API in tests")

    monkeypatch.setattr(actions, "_client", refuse)


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    """The pipeline waits before retrying a failed connector; tests don't."""
    from dashboard import pipeline

    monkeypatch.setattr(pipeline, "RETRY_DELAY_SECONDS", 0)
