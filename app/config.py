from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "ClaimsNexus"
    app_env: str = "development"
    app_debug: bool = True
    secret_key: str = "change-me"
    api_v1_prefix: str = "/api/v1"

    # Anthropic
    anthropic_api_key: str = ""
    llm_default_model: str = "claude-sonnet-4-6"
    llm_arbiter_model: str = "claude-opus-4-7"
    llm_max_tokens: int = 4096
    llm_max_output_tokens: int = 200
    llm_temperature: float = 0.1

    # OpenAI-compatible (Chat Completions API)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # Gemini / Google Generative AI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com"

    # Ollama (local)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # Explicit provider: gemini | openai | ollama | anthropic | together (empty = auto-detect)
    # MODEL_PROVIDER env var maps to this field.
    model_provider: str = "gemini"
    llm_provider: str = ""

    # Together.ai
    use_together: bool = False
    together_api_key: str = ""
    # Use a Together-hosted model id (see https://docs.together.ai/docs/models); not OpenAI model names.
    together_model: str = "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
    together_base_url: str = "https://api.together.ai"

    # Database — default to SQLite so the system works without Postgres
    database_url: str = "sqlite+aiosqlite:///./claimsnexus.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Weaviate
    weaviate_url: str = "http://localhost:8080"
    weaviate_api_key: str = ""

    # Risk Weights
    risk_weight_fraud: float = Field(default=0.45, ge=0.0, le=1.0)
    risk_weight_medical: float = Field(default=0.30, ge=0.0, le=1.0)
    risk_weight_policy: float = Field(default=0.25, ge=0.0, le=1.0)

    # Routing Thresholds
    risk_fast_track_max: float = 0.25
    risk_standard_max: float = 0.50
    risk_full_max: float = 0.70
    risk_escalate_min: float = 0.86
    fraud_auto_reject_threshold: float = 0.85
    claim_fast_track_value_max: float = 1000.0

    # Debate
    debate_max_rounds: int = 3
    debate_similarity_threshold: float = 0.90

    # Human Review
    human_review_enabled: bool = True
    human_review_timeout_hours: int = 48


settings = Settings()
