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


def test_whatsapp_is_unset_by_default_so_the_webhook_fails_closed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = Settings(_env_file=None)
    assert settings.whatsapp_verify_token is None
    assert settings.whatsapp_app_secret is None
    assert settings.whatsapp_access_token is None
    assert settings.whatsapp_phone_number_id is None
    assert settings.whatsapp_graph_version  # non-empty default


def test_secret_not_leaked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-supersecret")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "meta-supersecret")
    settings = Settings(_env_file=None)
    assert "supersecret" not in repr(settings)
    assert "supersecret" not in str(settings)


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()
    assert get_settings() is get_settings()
