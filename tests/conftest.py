import pytest


@pytest.fixture(autouse=True)
def _ignore_local_llm_overrides(monkeypatch):
    """Keep a developer's .env from changing request shapes under test."""
    monkeypatch.delenv("LLM_STRICT_JSON", raising=False)
