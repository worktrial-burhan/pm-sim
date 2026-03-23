from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch):
    from app.services.config import get_settings

    monkeypatch.setenv("PM_SIM_ENABLE_TEMPORAL", "false")
    monkeypatch.setenv("PM_SIM_CLAUDE_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
