from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Ensure deterministic settings before app import.
os.environ["API_KEYS"] = "test-key"
os.environ["REDIS_URL"] = "redis://localhost:9/0"
os.environ["LLM_BASE_URL"] = "https://openrouter.ai/api"
os.environ["LLM_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["SERPER_API_KEY"] = ""
os.environ["SERPER_DEV_API_KEY"] = ""
os.environ["SERPAPI_API_KEY"] = ""
os.environ["FIRECRAWL_API_KEY"] = ""


@pytest.fixture
def client(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("API_KEYS", "test-key")
    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
