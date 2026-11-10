"""Unit tests for EchoScope configuration loader (Phase 1).

Covers defaults, dummy placeholder sanitization, and strict precedence:
Constructor Init > OS Environment > .env file > .streamlit/secrets.toml > Defaults.
"""

from echoscope.config import Settings, get_settings


def test_default_settings(monkeypatch):
    """Checks that default settings are loaded with fallback model and safe empty keys."""
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)

    settings = Settings()
    assert settings.gemini_model == "gemini-3.8-flash"
    assert settings.db_path == "youtube_tracker.db"
    assert settings.youtube_api_key == ""
    assert settings.gemini_api_key == ""
    assert not settings.is_youtube_configured
    assert not settings.is_gemini_configured


def test_placeholder_keys_are_stripped(monkeypatch):
    """Ensures dummy placeholder strings from template files are treated as empty."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "REPLACE_WITH_YOUR_TOKEN")
    monkeypatch.setenv("GEMINI_API_KEY", "YOUR_TEST_TOKEN")

    settings = Settings()
    assert settings.youtube_api_key == ""
    assert settings.gemini_api_key == ""
    assert not settings.is_youtube_configured
    assert not settings.is_gemini_configured


def test_secrets_toml_loads_when_env_absent(tmp_path, monkeypatch):
    """Verifies that .streamlit/secrets.toml populates settings when env is empty."""
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    streamlit_dir = tmp_path / ".streamlit"
    streamlit_dir.mkdir()
    secrets_file = streamlit_dir / "secrets.toml"
    lines = [
        "youtube_api_key" + ' = "val_from_toml_yt"',
        "gemini_api_key" + ' = "val_from_toml_gemini"',
        'gemini_model = "gemini-3.8-flash"',
    ]
    secrets_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    settings = get_settings()

    assert settings.youtube_api_key == "val_from_toml_yt"
    assert settings.gemini_api_key == "val_from_toml_gemini"
    assert settings.is_youtube_configured
    assert settings.is_gemini_configured


def test_strict_precedence_order(tmp_path, monkeypatch):
    """Validates precedence: OS Environment > .env file > .streamlit/secrets.toml."""
    # 1. Setup .streamlit/secrets.toml
    streamlit_dir = tmp_path / ".streamlit"
    streamlit_dir.mkdir()
    secrets_file = streamlit_dir / "secrets.toml"
    secrets_file.write_text('youtube_api_key = "from_secrets"\n', encoding="utf-8")

    # 2. Setup .env file
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("YOUTUBE_API_KEY=from_dotenv\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    # With .env and secrets: .env wins
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    settings = Settings()
    assert settings.youtube_api_key == "from_dotenv"

    # With OS env set: OS env wins over .env and secrets
    monkeypatch.setenv("YOUTUBE_API_KEY", "from_os_env")
    settings_os = Settings()
    assert settings_os.youtube_api_key == "from_os_env"

    # Explicit constructor argument wins over everything
    settings_init = Settings(YOUTUBE_API_KEY="from_init_arg")
    assert settings_init.youtube_api_key == "from_init_arg"
