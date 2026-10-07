"""Tests for platform/config.py (tb-agent doc 23).

Hermetic: every test passes ``_env_file=None`` so a developer's local ``.env``
never changes the outcome, and controls the environment via ``monkeypatch``.
"""

import pytest
from pydantic import ValidationError

from agent.platform.config import Settings, get_settings


def test_fails_fast_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_reads_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"


def test_defaults_applied(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = Settings(_env_file=None)
    assert settings.anthropic_model  # non-empty default
    assert settings.agent_max_steps == 8
    assert settings.agent_max_tokens == 16000
    assert settings.log_level == "INFO"


def test_secret_not_leaked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-supersecret")
    settings = Settings(_env_file=None)
    assert "supersecret" not in repr(settings)
    assert "supersecret" not in str(settings)


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()
    assert get_settings() is get_settings()
