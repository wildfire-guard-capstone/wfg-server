from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_URL = "postgresql+psycopg://wfg:wfg@localhost:5432/wfg"  # docker-compose.yml db


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # DATABASE_URL wins. Without it the URL is built from the POSTGRES_* values the DB
    # container itself uses (EC2 db.env), so the password lives in one file only.
    database_url: str = ""
    postgres_user: str | None = None
    postgres_password: str | None = None
    postgres_db: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432

    aihub_root: Path | None = None
    data_root: Path = Path("./data")
    # capston-data 「data/지도자원/적재」 — 바탕 데이터 적재 파일(app.db.seed.base)
    base_data_dir: Path | None = None
    elmfire_bin: str = "elmfire_1.1"
    kma_api_key: str | None = None

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        if not self.database_url:
            parts = (self.postgres_user, self.postgres_password, self.postgres_db)
            if all(parts):
                user, password, db = (quote(p, safe="") for p in parts)
                self.database_url = (
                    f"postgresql+psycopg://{user}:{password}@{self.db_host}:{self.db_port}/{db}"
                )
            else:
                self.database_url = LOCAL_DATABASE_URL
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
