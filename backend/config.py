"""Settings read from the .env file at the project root."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Paper keys from https://app.alpaca.markets/paper/dashboard/overview
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    # Keep this true. Setting it false trades real money.
    alpaca_paper: bool = True
    # iex = free. sip = full market volume, needs a paid Alpaca plan. The
    # volume rules compare a day against its own average, so iex works fine.
    alpaca_data_feed: str = "iex"

    backend_port: int = 8020
    frontend_port: int = 5180
    cors_origins: str = "http://localhost:5180"
    log_level: str = "INFO"

    database_path: str = str(ROOT / "data" / "swing.db")

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
