from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent

class TestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    gemini_api_key: str = ""
    openai_api_key: str = ""


if __name__ == "__main__":
    settings = TestSettings()
    print(f"GEMINI_API_KEY loaded: {bool(settings.gemini_api_key)}")
    print(f"GEMINI first 20: {settings.gemini_api_key[:20] if settings.gemini_api_key else 'EMPTY'}")
    print(f"OPENAI_API_KEY loaded: {bool(settings.openai_api_key)}")
    print(f"OPENAI first 20: {settings.openai_api_key[:20] if settings.openai_api_key else 'EMPTY'}")
