from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_URL = "postgresql+psycopg://wfg:wfg@localhost:5432/wfg"  # docker-compose.yml db


class Settings(BaseSettings):
    # env_ignore_empty: `KMA_API_KEY=`처럼 비워 둔 값은 기본값(None)으로 본다.
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

    # 개발 중 다른 출처(예: Vite http://127.0.0.1:5173)에서 부를 때만. 같은 출처 배포면 비워 둔다.
    cors_origins: list[str] = []

    # 예측 API(PRED-01)가 쓰는 예측 제공자(app.predict). file이면 PREDICTOR_PATH(P1~P8 GeoJSON)
    predictor: str = "synthetic"
    predictor_path: str | None = None
    predictor_crs: int = 4326
    predictor_weather: str | None = None
    # 예측 API(PRED-01)가 쓰는 제안 작성기(app.propose). 지금은 fake(테스트 문장)만 있다
    proposal_writer: str = "fake"

    # 로그인 토큰(JWT HS256). 비워 두면 서버를 켤 때마다 임시 키를 만든다(재시작하면 다시 로그인)
    jwt_secret: str = ""
    jwt_expires_s: int = 1800

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
