from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent / ".env"

class Settings(BaseSettings):
    secret_key: str
    database_url: str = "sqlite+aiosqlite:///./notdienstplaner.db"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "notdienstplaner@example.com"
    doctors_per_day: int = 2
    app_base_url: str = "http://localhost:8000"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

settings = Settings()
