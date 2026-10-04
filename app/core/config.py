from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://wfg:wfg@localhost:5432/wfg"
    aihub_root: Path | None = None
    data_root: Path = Path("./data")
    elmfire_bin: str = "elmfire_1.1"
    kma_api_key: str | None = None
    data_go_kr_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
