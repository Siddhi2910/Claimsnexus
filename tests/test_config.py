from pathlib import Path

from app.config import Settings, settings


def test_settings_env_file_is_absolute() -> None:
    env_file = Settings.model_config.get("env_file")
    assert env_file is not None
    assert Path(env_file).is_absolute()
    assert Path(env_file).name == ".env"
    assert Path(env_file).exists()


def test_settings_loads_gemini_key_from_env_file() -> None:
    assert settings.gemini_api_key != ""
