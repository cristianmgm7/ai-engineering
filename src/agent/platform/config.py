"""L0 · Config — typed settings, validated at boot (tb-agent doc 23).

Fail-fast: instantiating ``Settings`` with a missing required var raises
``ValidationError`` at startup instead of misbehaving mid-request. Feature code
consumes ``get_settings()``, never ``os.environ`` directly.

Only the variables the code uses *today* (slice 1) live here. More arrive with
their components (webhook/L4, storage/L0, ...), grouped into nested sub-models
once there are enough to warrant it.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Anthropic (L0 · ModelProvider) ---
    anthropic_api_key: SecretStr  # required → fail-fast if missing
    anthropic_model: str = "claude-sonnet-5"

    # --- Agent core (L2 · reasoning loop) ---
    agent_max_steps: int = 8  # default RunLimits.max_steps
    agent_max_tokens: int = 16000  # default RunLimits.max_tokens

    # --- WhatsApp channel (L4 · edges/whatsapp). Optional: unset = fail closed ---
    whatsapp_verify_token: SecretStr | None = None  # Meta's GET subscription handshake
    whatsapp_app_secret: SecretStr | None = None  # signs webhooks (X-Hub-Signature-256)
    whatsapp_access_token: SecretStr | None = None  # Bearer for the Graph API
    whatsapp_phone_number_id: str | None = None  # the bot's sender id (= webhook :hook)
    whatsapp_graph_version: str = "v21.0"  # Graph API version in the send URL

    # --- Storage (L3 · adapters/stores) ---
    database_path: str | None = None  # SQLite file; unset = in-memory, state dies on restart

    # --- Langfuse (L3 · adapters/tracing). Optional: unset = tracing off ---
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # --- App ---
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return a cached, validated ``Settings`` singleton.

    Cached so config is parsed and validated once per process. Tests that mutate
    the environment call ``get_settings.cache_clear()`` first.
    """
    return Settings()
