from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_URL = "postgresql+psycopg://wfg:wfg@localhost:5432/wfg"  # docker-compose.yml db


class Settings(BaseSettings):
    # env_ignore_empty: `RESULTS_DIR=`처럼 비워 둔 값은 기본값(None)으로 본다.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

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

    # 결과 조회 API(app.routers.incidents) — demo.uiseong.export_bundle이 만든 폴더.
    # 제안 근거에 표준매뉴얼 원문이 들어 있으므로 저장소 밖에 둔다. 비우면 503.
    results_dir: Path | None = None
    # 로그인(app.routers.auth) — 계정 파일(python -m app.services.accounts)과 토큰 서명 키.
    # 둘 중 하나라도 비우면 로그인이 503을 돌려준다.
    accounts_file: Path | None = None
    auth_secret: str | None = None
    token_ttl_minutes: int = 480
    # 개발 중 다른 출처(예: Vite http://127.0.0.1:5173)에서 부를 때만. 같은 출처 배포면 비워 둔다.
    cors_origins: list[str] = []

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
