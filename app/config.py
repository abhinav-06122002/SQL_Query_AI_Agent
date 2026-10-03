from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None
    database_path: str = str(ROOT / "data" / "sample.db")
    max_rows: int = 100
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
